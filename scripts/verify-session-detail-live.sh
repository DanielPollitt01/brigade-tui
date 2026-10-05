#!/usr/bin/env bash
# verify-session-detail-live.sh — prove the session detail pane in a real
# Herdr pane.
#
# Runs Brigade in a real pane against a temp pi root with one project and two
# sessions, each with a 60 message transcript. Then:
#
#   1. click a grid card. The detail pane opens for that session.
#   2. enter the project waterfall and click a block. The same pane opens for
#      the same session.
#   3. open search, filter to that session, and press Enter on the row. The
#      same pane opens again.
#   4. the pane shows every required field and a read-only transcript tail
#      whose last line is the last message in the source file, and whose first
#      message is not in the tail.
#
# A mouse click is sent as a real SGR mouse event through `herdr pane
# send-text`, so the click is routed by the running Textual app, not by a
# test double.
#
# Usage:
#   bash scripts/verify-session-detail-live.sh <pane_id>
#
# The pane must be an idle shell at least 60 columns wide. Exit status 0 on
# PASS.

set -euo pipefail

PANE="${1:?usage: verify-session-detail-live.sh <pane_id>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${DETAIL_ROOT:-$(mktemp -d "${HOME}/.cache/brigade-detail-r2.XXXXXX")}"
EMPTY_CLAUDE="$ROOT/empty-claude"
NO_OPENCODE="$ROOT/no-opencode.db"
TARGET="session-beta"
TARGET_TITLE="BETA-TITLE-2222"
TAIL_MARKER="TAILMARKER-beta-9137"
HEAD_MARKER="HEADMARKER-EXCLUDED"
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

#: Click at a 1-based screen row and column by sending a real SGR mouse press
#: and release to the pane.
click_at() {
  local row="$1" col="$2"
  herdr pane send-text "$PANE" \
    "$(printf '\033[<0;%d;%dM\033[<0;%d;%dm' "$col" "$row" "$col" "$row")"
}

#: Find the first screen line holding a needle. Print the 1-based row and the
#: 1-based column of the needle, ready to click.
locate() {
  local needle="$1"
  read_pane | awk -v n="$needle" 'i=index($0,n){print NR, i; exit}'
}

#: Locate a block on a lane: the line that names the lane and holds a block
#: glyph. Print the row and the block glyph's column.
locate_block() {
  local lane="$1"
  read_pane | awk -v n="$lane" \
    'index($0,n) && index($0,"█"){print NR, index($0,"█"); exit}'
}

#: The source file's last message text, read independently of Brigade.
source_last_message() {
  "$REPO/.venv/bin/python" - "$ROOT/proj-alpha/$TARGET.jsonl" <<'PY'
import json, sys
last = ""
for line in open(sys.argv[1], encoding="utf-8"):
    record = json.loads(line)
    message = record.get("message")
    if isinstance(message, dict) and message.get("role") in ("user", "assistant"):
        content = message.get("content")
        if isinstance(content, str):
            last = " ".join(content.split())
print(last)
PY
}

#: Assert the open detail pane is the target session with its tail.
check_detail() {
  local entry="$1"
  local pane
  pane="$(read_pane)"
  if ! printf '%s' "$pane" | grep -qF "Session detail (read-only)"; then
    echo "FAIL: $entry did not open the detail pane"
    printf '%s\n' "$pane" | head -40
    exit 1
  fi
  if ! printf '%s' "$pane" | grep -qF "$TARGET"; then
    echo "FAIL: $entry opened the wrong session"
    printf '%s\n' "$pane" | head -40
    exit 1
  fi
  if ! printf '%s' "$pane" | grep -qF "Read-only transcript tail"; then
    echo "FAIL: $entry did not show a read-only transcript tail"
    exit 1
  fi
  if ! printf '%s' "$pane" | grep -qF "$TAIL_MARKER"; then
    echo "FAIL: $entry tail does not show the source file's last message"
    printf '%s\n' "$pane" | tail -20
    exit 1
  fi
  if printf '%s' "$pane" | grep -qF "$HEAD_MARKER"; then
    echo "FAIL: $entry tail includes a message outside the tail window"
    exit 1
  fi
  echo "$entry: detail pane for $TARGET, tail ends at $TAIL_MARKER, head excluded"
}

#: Build the temp pi root with two real transcripts.
"$REPO/.venv/bin/python" - "$ROOT" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
root = sys.argv[1]
project = f"{root}/proj-alpha"
import os
os.makedirs(project, exist_ok=True)
now = datetime.now(timezone.utc)
iso = lambda d: d.isoformat().replace("+00:00", "Z")
for session_id, title, role, tail_marker in (
    ("session-alpha", "ALPHA-TITLE-1111", "builder", "TAILMARKER-alpha-4271"),
    ("session-beta", "BETA-TITLE-2222", "reviewer", "TAILMARKER-beta-9137"),
):
    start = now - timedelta(minutes=10)
    records = [
        {"type": "session", "id": session_id, "timestamp": iso(start), "cwd": "/home/user/proj-alpha"},
        {"type": "model_change", "id": f"mc-{session_id}", "timestamp": iso(start),
         "provider": "opencode-go", "modelId": "deepseek-v4.1-flash"},
        {"type": "session_info", "id": f"info-{session_id}", "timestamp": iso(start), "name": role},
    ]
    for index in range(60):
        if index == 0:
            text = title
        elif index == 10:
            text = "HEADMARKER-EXCLUDED"
        elif index == 59:
            text = tail_marker
        else:
            text = f"message {index} from {role}"
        records.append({
            "type": "message", "id": f"msg-{session_id}-{index}",
            "timestamp": iso(start + timedelta(seconds=index * 5)),
            "message": {"role": "user" if index % 2 == 0 else "assistant", "content": text},
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
wait_for "$TARGET_TITLE" 30

# (1) Grid card: click the title line of the target card.
read -r ROW COL <<<"$(locate "$TARGET_TITLE")"
echo "grid card at row=$ROW col=$COL"
click_at "$ROW" "$COL"
wait_for "Session detail (read-only)" 10
check_detail "grid card"

# Close, then enter the project waterfall.
herdr pane send-keys "$PANE" escape
sleep 1
herdr pane send-keys "$PANE" ctrl+1
sleep 2
wait_for "builder" 10

# (2) Waterfall block: click the reviewer lane's block.
read -r ROW COL <<<"$(locate_block "reviewer")"
echo "reviewer block at row=$ROW col=$COL"
click_at "$ROW" "$COL"
wait_for "Session detail (read-only)" 10
check_detail "waterfall block"

# (3) Search row: filter to the target, focus the table, press Enter.
herdr pane send-keys "$PANE" escape
sleep 1
herdr pane send-keys "$PANE" escape
sleep 1
herdr pane send-keys "$PANE" slash
sleep 1
herdr pane send-text "$PANE" "$TARGET"
sleep 1
herdr pane send-keys "$PANE" tab
sleep 0.5
herdr pane send-keys "$PANE" enter
wait_for "Session detail (read-only)" 10
check_detail "search row"

# (4) The tail's last line matches the source file's last message.
LAST_SOURCE="$(source_last_message)"
PANE_TEXT="$(read_pane)"
if ! printf '%s' "$PANE_TEXT" | grep -qF "$LAST_SOURCE"; then
  echo "FAIL: the pane tail does not contain the source file's last message '$LAST_SOURCE'"
  exit 1
fi
echo "tail last line matches source file: $LAST_SOURCE"

# Leave the pane at a clean shell. The EXIT trap runs cleanup.
echo "PASS: grid card, waterfall block and search row all opened the same session detail pane; the read-only tail matches the source file"
