#!/usr/bin/env bash
# verify-focus-live.sh — prove ISC-28 in a real Herdr pane.
#
# Runs the worktree Brigade in a real pane against a real pi root. It drops
# into a project waterfall, then writes a brand-new project session and
# checks three things:
#
#   1. the view did not move: the same project's waterfall is still shown,
#      the new project did not steal focus
#   2. the new tab appears at the left of the tab bar
#   3. h and l move the active tab by user action
#
# Usage:
#   bash scripts/verify-focus-live.sh <pane_id>
#
# The pane must be an idle shell. Exit status 0 on PASS.

set -euo pipefail

PANE="${1:?usage: verify-focus-live.sh <pane_id>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${FOCUS_ROOT:-$(mktemp -d "${HOME}/.cache/brigade-focus.XXXXXX")}"
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
     "message": {"role": "user", "content": f"focus {role}"}},
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

# Two projects. alpha is older, beta is newer, so beta starts as tab 1.
write_session proj-alpha builder alpha-1 60
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

echo "grid baseline:"
read_pane | grep -E 'proj-(alpha|beta)' | head -1

# Drop into a project: l enters the view and moves to the next tab (alpha).
herdr pane send-keys "$PANE" l
wait_for "█" 10
baseline_lane="$(active_lane)"
echo "after l, active lane=$baseline_lane"
if [ "$baseline_lane" != "builder" ]; then
  echo "FAIL: expected the alpha builder waterfall, saw '$baseline_lane'"
  read_pane
  exit 1
fi

# A brand-new newest project arrives. The view must not move.
write_session proj-gamma planner gamma-1 1
wait_for "proj-gamma" 15
sleep 4

after_lane="$(active_lane)"
echo "after new project, active lane=$after_lane"
tab_line="$(read_pane | grep -E 'proj-(alpha|beta|gamma)' | head -1)"
echo "tab bar: $tab_line"

if read_pane | grep -q '┌'; then
  echo "FAIL: the grid view appeared after a new tab, focus was stolen"
  exit 1
fi
if [ "$after_lane" != "builder" ]; then
  echo "FAIL: active view moved from builder to '$after_lane'"
  exit 1
fi

gamma_pos=$(awk -v s="proj-gamma" '{print index($0, s)}' <<<"$tab_line")
beta_pos=$(awk -v s="proj-beta" '{print index($0, s)}' <<<"$tab_line")
if [ "$gamma_pos" -eq 0 ] || [ "$beta_pos" -eq 0 ] || [ "$gamma_pos" -ge "$beta_pos" ]; then
  echo "FAIL: the new tab proj-gamma is not at the left of proj-beta"
  exit 1
fi
echo "new tab at left: proj-gamma at $gamma_pos < proj-beta at $beta_pos"

# h and l move the active tab, by user action.
herdr pane send-keys "$PANE" h
sleep 2
moved_lane="$(active_lane)"
echo "after h, active lane=$moved_lane"
if [ "$moved_lane" != "reviewer" ]; then
  echo "FAIL: h did not move the active tab to beta's reviewer waterfall"
  read_pane
  exit 1
fi

herdr pane send-keys "$PANE" l
sleep 2
back_lane="$(active_lane)"
echo "after l, active lane=$back_lane"
if [ "$back_lane" != "builder" ]; then
  echo "FAIL: l did not move the active tab back to alpha"
  read_pane
  exit 1
fi

# Leave the pane at a clean shell.
herdr pane send-keys "$PANE" ctrl+c
sleep 0.5
herdr pane send-keys "$PANE" ctrl+q
sleep 0.5

echo "PASS: new tab did not steal focus, new tab at left, h and l move the active tab"
