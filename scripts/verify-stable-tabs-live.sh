#!/usr/bin/env bash
# verify-stable-tabs-live.sh — prove ITEM-47 in a real Herdr pane.
#
# Runs the worktree Brigade in a real pane against a real pi root. It records
# the tab bar and the active project, then makes an existing project the newest
# by writing a new session for that project. A stable bar must not reorder, and
# the active project must stay put.
#
# Acceptance: tab positions do not change when a session updates, and the
# active tab stays put.
#
# Usage:
#   bash scripts/verify-stable-tabs-live.sh <pane_id>
#
# The pane must be an idle shell. Exit status 0 on PASS.

set -euo pipefail

PANE="${1:?usage: verify-stable-tabs-live.sh <pane_id>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${STABLE_ROOT:-$(mktemp -d "${HOME}/.cache/brigade-stable.XXXXXX")}"
EMPTY_CLAUDE="$ROOT/empty-claude"
NO_OPENCODE="$ROOT/no-opencode.db"
mkdir -p "$EMPTY_CLAUDE"
rm -f "$NO_OPENCODE"

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
     "message": {"role": "user", "content": f"stable {role}"}},
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

active_lane() {
  read_pane | grep -oE '^(builder|reviewer|planner)' | head -1
}

tab_line() {
  # The tab strip is the first visible line that holds both project labels.
  read_pane | grep -E 'proj-(alpha|beta)' | head -1
}

# proj-alpha is older, proj-beta newer, so beta owns the left tab.
write_session proj-alpha builder alpha-1 90
sleep 1
write_session proj-beta reviewer beta-1 5

echo "root=$ROOT"
echo "pane=$PANE"

herdr pane run "$PANE" env \
  PI_SESSION_DIR="$ROOT" \
  CLAUDE_PROJECTS_DIR="$EMPTY_CLAUDE" \
  OPENCODE_DB="$NO_OPENCODE" \
  "$REPO/.venv/bin/python" -m brigade_tui

wait_for "proj-alpha" 30
wait_for "proj-beta" 30
sleep 1

before_tabs="$(tab_line)"
echo "tab bar before: $before_tabs"

# Enter alpha's waterfall by user action.
herdr pane send-keys "$PANE" l
wait_for "█" 10
sleep 1
before_lane="$(active_lane)"
echo "active lane before: $before_lane"
if [ "$before_lane" != "builder" ]; then
  echo "FAIL: expected alpha's builder waterfall, saw '$before_lane'"
  read_pane
  exit 1
fi

# An existing project updates: a brand-new session lands in proj-alpha, so
# alpha becomes the newest project. A ranked bar would swap the two tabs.
write_session proj-alpha builder alpha-2 0
sleep 5

after_tabs="$(tab_line)"
after_lane="$(active_lane)"
echo "tab bar after:  $after_tabs"
echo "active lane after: $after_lane"

if [ "$after_tabs" != "$before_tabs" ]; then
  echo "FAIL: tab order changed on a session update"
  echo "  before: $before_tabs"
  echo "  after:  $after_tabs"
  exit 1
fi
if [ "$after_lane" != "$before_lane" ]; then
  echo "FAIL: active tab moved from '$before_lane' to '$after_lane'"
  exit 1
fi

# Leave the pane at a clean shell.
herdr pane send-keys "$PANE" ctrl+c
sleep 0.5
herdr pane send-keys "$PANE" ctrl+q
sleep 0.5

echo "PASS: a session update did not reorder the tabs, and the active tab stayed put"
