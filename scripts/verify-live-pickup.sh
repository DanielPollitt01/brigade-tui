#!/usr/bin/env bash
# verify-live-pickup.sh — prove ISC-3 in a real Herdr pane.
#
# Writes a real pi-format session file into a watched pi root while brigade is
# running in a Herdr pane, then polls the pane until the new row shows and
# reports the elapsed time. The bar is one poll interval, 3 seconds.
#
# Usage:
#   bash scripts/verify-live-pickup.sh <pane_id>
#
# The pane must be an idle shell. The script points brigade only at a temp
# pi root (CLAUDE_PROJECTS_DIR empty, OPENCODE_DB missing), so the row it
# watches is the only one in play. The root sits under $HOME, never /tmp:
# Brigade hides seed and scratch sessions whose cwd is a temp dir, so a /tmp
# root would make this pickup proof a hidden project and the row would not show.

set -euo pipefail

PANE="${1:?usage: verify-live-pickup.sh <pane_id>}"
mkdir -p "${HOME}/.cache"
ROOT="${LIVE_ROOT:-$(mktemp -d "${HOME}/.cache/brigade-live.XXXXXX")}"
PROJ="$ROOT/verify-live"
EMPTY_CLAUDE="$ROOT/empty-claude"
NO_OPENCODE="$ROOT/no-opencode.db"
mkdir -p "$PROJ" "$EMPTY_CLAUDE"
rm -f "$NO_OPENCODE"

write_session() {
  local file="$1" id="$2" title="$3" model="$4"
  cat > "$file" <<EOF
{"type":"session","version":3,"id":"$id","timestamp":"2026-10-04T00:00:00.000Z","cwd":"$PROJ"}
{"type":"model_change","id":"mc-$id","timestamp":"2026-10-04T00:00:01.000Z","provider":"verify","modelId":"$model"}
{"type":"message","id":"msg-$id","timestamp":"2026-10-04T00:00:02.000Z","message":{"role":"user","content":"$title"}}
EOF
}

write_session "$PROJ/seed.jsonl" "verify-seed-0001" "seed-session-row" "seed-model"

echo "root=$ROOT"
echo "pane=$PANE"

# Start brigade in the real Herdr pane, pointed only at the temp pi root.
herdr pane run "$PANE" env \
  PI_SESSION_DIR="$ROOT" \
  CLAUDE_PROJECTS_DIR="$EMPTY_CLAUDE" \
  OPENCODE_DB="$NO_OPENCODE" \
  brigade

# Wait for the baseline row to render.
deadline=$((SECONDS + 30))
until herdr pane read "$PANE" --source visible --format text 2>/dev/null | grep -q "seed-session-row"; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "FAIL: baseline seed row did not show within 30s"
    herdr pane read "$PANE" --source visible --format text || true
    exit 1
  fi
  sleep 0.2
done
echo "baseline visible: seed-session-row"

# Write one new real session and time how long the pane takes to show it.
start="$(date +%s.%N)"
write_session "$PROJ/live.jsonl" "verify-live-0002" "live-pickup-row" "live-model"

polls=0
deadline=$((SECONDS + 10))
until herdr pane read "$PANE" --source visible --format text 2>/dev/null | grep -q "live-pickup-row"; do
  polls=$((polls + 1))
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "FAIL: live row did not show within 10s"
    herdr pane read "$PANE" --source visible --format text || true
    exit 1
  fi
  sleep 0.1
done
end="$(date +%s.%N)"

elapsed="$(python3 -c "print(f'{float($end) - float($start):.3f}')")"
echo "poll_interval=3.000s"
echo "polls=$polls"
echo "elapsed=${elapsed}s"

python3 - "$elapsed" <<'PY'
import sys

elapsed = float(sys.argv[1])
if elapsed <= 3.0:
    print(f"PASS: live row appeared in {elapsed:.3f}s <= 3.000s")
    sys.exit(0)
print(f"FAIL: live row appeared in {elapsed:.3f}s > 3.000s")
sys.exit(1)
PY
