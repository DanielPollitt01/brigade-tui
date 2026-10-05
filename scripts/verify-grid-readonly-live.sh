#!/usr/bin/env bash
# verify-grid-readonly-live.sh — prove the grid is read-only and that a list
# key never moves the hidden table, in a real Herdr pane.
#
# Runs Brigade in a real pane against a real pi root with two projects, each
# holding three sessions. Then:
#
#   1. the grid shows its read-only banner, so the user knows it has no cursor
#   2. pressing j several times while the grid is shown leaves the view on the
#      grid: no table header and no waterfall block appears
#   3. entering the active project tab and showing the table proves the hidden
#      table cursor is still on the first row: j did not move it
#   4. j in the project view does move the table cursor, so the key still works
#      where a list is on screen
#
# Usage:
#   bash scripts/verify-grid-readonly-live.sh <pane_id>
#
# The pane must be an idle shell. Exit status 0 on PASS.

set -euo pipefail

PANE="${1:?usage: verify-grid-readonly-live.sh <pane_id>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${GRID_ROOT:-$(mktemp -d "${HOME}/.cache/brigade-grid-r7.XXXXXX")}"
EMPTY_CLAUDE="$ROOT/empty-claude"
NO_OPENCODE="$ROOT/no-opencode.db"
mkdir -p "$EMPTY_CLAUDE"
rm -f "$NO_OPENCODE"

#: Stop Brigade and leave the pane at a shell, on any exit.
cleanup() {
  herdr pane send-keys "$PANE" ctrl+c 2>/dev/null || true
  sleep 0.4
  herdr pane send-keys "$PANE" ctrl+q 2>/dev/null || true
}
trap cleanup EXIT

#: The DataTable cursor row accent background. Rows that are not the cursor
#: use 39;39;39. This is how the live pane shows the selected row.
CURSOR_BG='48;2;1;120;212'

write_session() {
  local project="$1" role="$2" id="$3" age="${4:-5}"
  local dir="$ROOT/$project"
  mkdir -p "$dir"
  python3 - "$dir" "$project" "$role" "$id" "$age" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
directory, project, role, session_id, age = sys.argv[1:6]
started = datetime.now(timezone.utc) - timedelta(seconds=int(age))
stamp = started.isoformat().replace("+00:00", "Z")
cwd = f"/home/user/{project}"
records = [
    {"type": "session", "id": session_id, "timestamp": stamp, "cwd": cwd},
    {"type": "model_change", "id": f"mc-{session_id}", "timestamp": stamp,
     "provider": "opencode-go", "modelId": "deepseek-v4.1-flash"},
    {"type": "message", "id": f"msg-{session_id}", "timestamp": stamp,
     "message": {"role": "user", "content": f"grid r7 {role}"}},
    {"type": "session_info", "id": f"info-{session_id}", "timestamp": stamp,
     "name": role},
]
with open(f"{directory}/{session_id}.jsonl", "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps(record) + "\n")
PY
  touch "$dir/$id.jsonl"
}

read_pane() {
  herdr pane read "$PANE" --source visible --format text 2>/dev/null || true
}

read_ansi() {
  herdr pane read "$PANE" --source visible --format ansi 2>/dev/null || true
}

wait_for() {
  local needle="$1" seconds="$2"
  local deadline=$((SECONDS + seconds))
  until read_pane | grep -qF "$needle"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: '$needle' did not appear within ${seconds}s"
      read_pane
      exit 1
    fi
    sleep 0.3
  done
}

# The 0-based index of the table cursor row among the data rows. The cursor
# row carries the accent background; every other data row does not.
cursor_row() {
  read_ansi \
    | grep 'opencode-go' \
    | awk -v bg="$CURSOR_BG" 'BEGIN { n = 0 } index($0, bg) { print n; exit } { n++ }'
}

# Three sessions in each project, so the hidden table has rows to move.
write_session proj-alpha builder alpha-1 5
write_session proj-alpha builder alpha-2 9
write_session proj-alpha builder alpha-3 14
write_session proj-beta reviewer beta-1 6
write_session proj-beta reviewer beta-2 10
write_session proj-beta reviewer beta-3 15

echo "root=$ROOT"
echo "pane=$PANE"

# Make sure the pane is at a shell prompt, not a leftover run.
herdr pane send-keys "$PANE" ctrl+c 2>/dev/null || true
sleep 0.4
herdr pane send-keys "$PANE" ctrl+q 2>/dev/null || true
sleep 0.4

herdr pane run "$PANE" env \
  PI_SESSION_DIR="$ROOT" \
  CLAUDE_PROJECTS_DIR="$EMPTY_CLAUDE" \
  OPENCODE_DB="$NO_OPENCODE" \
  "$REPO/.venv/bin/python" -m brigade_tui

wait_for "No cursor here" 30
banner="$(read_pane | grep -F "No cursor here" | head -1)"
echo "grid banner: $banner"
if ! read_pane | grep -qF "j / k do nothing"; then
  echo "FAIL: the grid banner does not say j / k do nothing"
  read_pane
  exit 1
fi

# Press j several times while the grid is shown. The view must stay on the
# grid: no table header and no waterfall lane block may appear.
for _ in 1 2 3 4; do
  herdr pane send-keys "$PANE" j
  sleep 0.2
done
sleep 1
if read_pane | grep -q "Harness"; then
  echo "FAIL: a table appeared after j in grid mode"
  read_pane
  exit 1
fi
if read_pane | grep -q "█"; then
  echo "FAIL: a waterfall appeared after j in grid mode"
  read_pane
  exit 1
fi
if ! read_pane | grep -qF "No cursor here"; then
  echo "FAIL: the grid left the screen after j in grid mode"
  read_pane
  exit 1
fi
echo "after j x4 in grid mode: still the read-only grid, no hidden table shown"

# Drop into the active project tab and show its flat table.
herdr pane send-keys "$PANE" ctrl+1
sleep 1
herdr pane send-keys "$PANE" t
wait_for "Harness" 10
sleep 1
first_cursor="$(cursor_row)"
echo "table cursor row after j in grid mode: $first_cursor"
if [ "$first_cursor" != "0" ]; then
  echo "FAIL: the hidden table cursor moved to row $first_cursor; j acted on it"
  read_ansi | grep 'opencode-go' | cat -v
  exit 1
fi

# j in the project view must move the cursor, so the key still works.
herdr pane send-keys "$PANE" j
sleep 1
second_cursor="$(cursor_row)"
echo "table cursor row after j in project view: $second_cursor"
if [ "$second_cursor" != "1" ]; then
  echo "FAIL: j did not move the table cursor in the project view"
  read_ansi | grep 'opencode-go' | cat -v
  exit 1
fi

# Leave the pane at a clean shell. The EXIT trap runs cleanup.
echo "PASS: grid says read-only, j in grid mode left the hidden table cursor on row 0, j in the project view moved it"
