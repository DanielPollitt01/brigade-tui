#!/usr/bin/env bash
# verify-search-live.sh — prove STW-51 R10 in a real Herdr pane.
#
# Runs Brigade in a real pane against a temp pi root with two sessions whose
# ids are full UUIDs. Then, keyboard only:
#
#   1. press `/`. The search browser opens.
#   2. read the result rows. The raw session id is visible on each row, not
#      only the short label.
#   3. press `down` to hand focus to the result table, then `j` to move the
#      cursor to the second row, then `enter`.
#   4. the session detail pane opens for the row the cursor moved to, and the
#      raw session id is visible in the pane too.
#
# No mouse is used. Every key goes through `herdr pane send-keys`, so the
# running Textual app routes it, not a test double.
#
# Usage:
#   bash scripts/verify-search-live.sh <pane_id>
#
# The pane must be an idle shell at least 80 columns wide. Exit status 0 on
# PASS.

set -euo pipefail

PANE="${1:?usage: verify-search-live.sh <pane_id>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${SEARCH_ROOT:-$(mktemp -d "${HOME}/.cache/brigade-search-r10.XXXXXX")}"
EMPTY_CLAUDE="$ROOT/empty-claude"
NO_OPENCODE="$ROOT/no-opencode.db"
NEWER_ID="01a10446-a3f2-73df-8891-452b15f67f93"
OLDER_ID="01a10445-1111-72df-8891-452b15f60000"
NEWER_TITLE="NEWER-SEARCH-TITLE"
OLDER_TITLE="OLDER-SEARCH-TITLE"
mkdir -p "$EMPTY_CLAUDE"
rm -f "$NO_OPENCODE"

#: Stop Brigade and leave the pane at a shell, on any exit.
cleanup() {
  herdr pane send-keys "$PANE" ctrl+c 2>/dev/null || true
  sleep 0.4
  herdr pane send-keys "$PANE" ctrl+q 2>/dev/null || true
}
trap cleanup EXIT

read_pane() {
  herdr pane read "$PANE" --source visible --format text 2>/dev/null || true
}

wait_for() {
  local needle="$1" seconds="$2"
  local deadline=$((SECONDS + seconds))
  until read_pane | grep -qF "$needle"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "FAIL: '$needle' did not appear within ${seconds}s"
      read_pane | head -40
      exit 1
    fi
    sleep 0.3
  done
}

#: Build the temp pi root with two real transcripts. The newer session is
#: listed first, so moving to row 2 selects the older one.
"$REPO/.venv/bin/python" - "$ROOT" <<'PY'
import json, os, sys
from datetime import datetime, timedelta, timezone
root = sys.argv[1]
project = f"{root}/proj-alpha"
os.makedirs(project, exist_ok=True)
now = datetime.now(timezone.utc)
iso = lambda d: d.isoformat().replace("+00:00", "Z")
for session_id, title, seconds_ago in (
    ("01a10445-1111-72df-8891-452b15f60000", "OLDER-SEARCH-TITLE", 300),
    ("01a10446-a3f2-73df-8891-452b15f67f93", "NEWER-SEARCH-TITLE", 10),
):
    start = now - timedelta(seconds=seconds_ago)
    records = [
        {"type": "session", "id": session_id, "timestamp": iso(start),
         "cwd": "/home/user/proj-alpha"},
        {"type": "model_change", "id": f"mc-{session_id}",
         "timestamp": iso(start), "provider": "opencode-go",
         "modelId": "deepseek-v4.1-flash"},
        {"type": "session_info", "id": f"info-{session_id}",
         "timestamp": iso(start), "name": "builder"},
    ]
    for index in range(3):
        records.append({
            "type": "message", "id": f"msg-{session_id}-{index}",
            "timestamp": iso(start + timedelta(seconds=index * 5)),
            "message": {"role": "user" if index % 2 == 0 else "assistant",
                        "content": title if index == 0 else f"message {index}"},
        })
    with open(f"{project}/{session_id}.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
PY

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
wait_for "$NEWER_TITLE" 30

# (1) Open search from the keyboard.
herdr pane send-keys "$PANE" slash
wait_for "Search every session" 10

# (2) The raw session id is on the result rows.
ROW_TEXT="$(read_pane)"
for raw in "$NEWER_ID" "$OLDER_ID"; do
  if ! printf '%s' "$ROW_TEXT" | grep -qF "$raw"; then
    echo "FAIL: the search row does not show the raw session id '$raw'"
    printf '%s\n' "$ROW_TEXT" | head -40
    exit 1
  fi
done
echo "search rows show the raw session ids: $NEWER_ID and $OLDER_ID"

# (3) Keyboard only: down hands off to the table, j moves to row 2, enter opens.
herdr pane send-keys "$PANE" down
sleep 0.5
herdr pane send-keys "$PANE" j
sleep 0.5
herdr pane send-keys "$PANE" enter
wait_for "Session detail (read-only)" 10

# (4) The detail pane is the row the cursor moved to, and shows the raw id.
DETAIL_TEXT="$(read_pane)"
if ! printf '%s' "$DETAIL_TEXT" | grep -qF "$OLDER_TITLE"; then
  echo "FAIL: the cursor did not move to the second row (older session)"
  printf '%s\n' "$DETAIL_TEXT" | head -40
  exit 1
fi
if ! printf '%s' "$DETAIL_TEXT" | grep -qF "$OLDER_ID"; then
  echo "FAIL: the detail pane does not show the raw session id '$OLDER_ID'"
  printf '%s\n' "$DETAIL_TEXT" | head -40
  exit 1
fi
echo "detail pane for the second row shows the raw id: $OLDER_ID"

echo "PASS: search opened by keyboard, the cursor moved, the second row opened, and the raw session id is visible on the row and in the detail pane"
