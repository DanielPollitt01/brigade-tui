#!/usr/bin/env bash
# make-sandbox.sh — build a real-schema ssf.db fixture for the TUI tests.
#
# The schema is read from upstream tracer.py (SSSF), never hand-written here.
# It also applies upstream's additive MIGRATIONS list, so the db matches a real
# run on the same commit.
#
# Upstream pin: de31374882e7a4e3e5b7bb9bd09e69dc2f779356 (SCOPE.md section 1).
# uv used to stamp the reference factory: 0.12.21 (pinned).
#
# Usage:
#   bash scripts/make-sandbox.sh [output.db]
#
# Default output: $SANDBOX_ROOT/fixtures/sssf.db
#   = /home/user/.local/share/brigade-tui/fixtures/sssf.db
# Overrides:
#   SANDBOX_ROOT   factory sandbox root (default /home/user/.local/share/brigade-tui)
#   FIXTURE_OUT    explicit output path
#   UPSTREAM_DIR   upstream clone path (default $SANDBOX_ROOT/upstream)
#   PYTHON         python3 binary (default: python3)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX_ROOT="${SANDBOX_ROOT:-/home/user/.local/share/brigade-tui}"
OUT="${1:-${FIXTURE_OUT:-$SANDBOX_ROOT/fixtures/sssf.db}}"
UPSTREAM_DIR="${UPSTREAM_DIR:-$SANDBOX_ROOT/upstream}"
PIN="de31374882e7a4e3e5b7bb9bd09e69dc2f779356"
TRACER="$UPSTREAM_DIR/.claude/skills/sssf/templates/adws/adw_modules/tracer.py"
PYTHON="${PYTHON:-python3}"

if [ ! -f "$TRACER" ]; then
  echo "upstream tracer.py missing; cloning $PIN into $UPSTREAM_DIR" >&2
  mkdir -p "$(dirname "$UPSTREAM_DIR")"
  if [ ! -d "$UPSTREAM_DIR/.git" ]; then
    git clone --quiet https://github.com/disler/super-simple-software-factory "$UPSTREAM_DIR"
  fi
  git -C "$UPSTREAM_DIR" fetch --quiet origin "$PIN"
  git -C "$UPSTREAM_DIR" checkout --quiet "$PIN"
fi

[ -f "$TRACER" ] || { echo "tracer.py not found at $TRACER" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT" "$OUT-wal" "$OUT-shm"

"$PYTHON" - "$TRACER" "$OUT" <<'PY'
"""Build ssf.db from upstream's own SCHEMA DDL plus deterministic seed rows.

The SCHEMA and MIGRATIONS constants are read straight out of tracer.py with
ast, so this fixture can never drift from the real factory schema. Seed rows
are fixed values, so reruns produce the same bytes.
"""
import ast
import json
import sqlite3
import sys

tracer_path, out_path = sys.argv[1], sys.argv[2]
tree = ast.parse(open(tracer_path).read(), filename=tracer_path)
constants = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
        if name in ("SCHEMA", "MIGRATIONS"):
            constants[name] = ast.literal_eval(node.value)

schema = constants["SCHEMA"]
migrations = constants.get("MIGRATIONS", [])
assert "CREATE TABLE IF NOT EXISTS sessions" in schema, "upstream SCHEMA not found"

conn = sqlite3.connect(out_path)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
conn.executescript(schema)
for table, column, decl in migrations:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

ADW = "sandbox01"
T0 = "2026-10-02T00:00:00+00:00"
T1 = "2026-10-02T00:00:05+00:00"
T2 = "2026-10-02T00:00:30+00:00"
T3 = "2026-10-02T00:00:45+00:00"
T4 = "2026-10-02T00:01:10+00:00"

conn.execute(
    "INSERT INTO sessions (adw_id, adw_name, request, status, engineer, started_at,"
    " ended_at, total_tokens, total_cost, archived) VALUES (?,?,?,?,?,?,?,?,?,?)",
    (ADW, "adw_plan_build_test", "Add a /health endpoint", "success", "sandbox",
     T0, T4, 12345, 0.0123, 0),
)

phases = [
    (ADW + "_01_plan", ADW, 1, "plan", "agent", "planner",
     "Turn the request into a plan", "success", 1, 0, None, T0, T1),
    (ADW + "_02_build", ADW, 2, "build", "agent", "builder",
     "Implement the plan", "success", 1, 0, None, T1, T2),
    (ADW + "_03_test", ADW, 3, "test", "code", "tests",
     "Run the suite", "success", 1, 0, None, T2, T3),
]
conn.executemany(
    "INSERT INTO phases (phase_id, adw_id, seq, name, kind, owner, description,"
    " status, attempt, retries, error, started_at, ended_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", phases)

events = [
    ("evt_000000000001", ADW, ADW + "_01_plan", None, "phase_start", "plan",
     json.dumps({"description": "Turn the request into a plan"}), None, T0, None),
    ("evt_000000000002", ADW, ADW + "_01_plan", None, "agent_start", "planner",
     json.dumps({"model": "google/gemini-3.6-flash"}), None, T0, None),
    ("evt_000000000003", ADW, ADW + "_01_plan", None, "tool_call", "read: spec.md",
     json.dumps({"tool": "read", "path": "spec.md"}), None, T0, T1),
    ("evt_000000000004", ADW, ADW + "_01_plan", None, "agent_end", "planner",
     json.dumps({"output_type": "PlanOutput"}), 900, T1, T1),
    ("evt_000000000005", ADW, ADW + "_02_build", None, "phase_start", "build",
     json.dumps({"description": "Implement the plan"}), None, T1, None),
    ("evt_000000000006", ADW, ADW + "_02_build", None, "tool_call", "edit: app.py",
     json.dumps({"tool": "edit", "path": "app.py"}), None, T1, T2),
    ("evt_000000000007", ADW, ADW + "_02_build", None, "agent_end", "builder",
     json.dumps({"output_type": "BuildOutput"}), 1100, T2, T2),
    ("evt_000000000008", ADW, ADW + "_03_test", None, "phase_start", "test",
     json.dumps({"command": "pytest -q"}), None, T2, None),
    ("evt_000000000009", ADW, ADW + "_03_test", None, "phase_end", "test",
     json.dumps({"passed": True}), None, T3, T3),
]
conn.executemany(
    "INSERT INTO events (event_id, adw_id, phase_id, parent_id, type, name,"
    " payload_json, tokens, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    events)

conn.executemany(
    "INSERT INTO envelopes (envelope_id, adw_id, phase_id, agent, output_type,"
    " payload_json, valid, attempt, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
    [
        ("env_000000000001", ADW, ADW + "_01_plan", "planner", "PlanOutput",
         json.dumps({"plan": "Add GET /health returning ok"}), 1, 1, T1),
        ("env_000000000002", ADW, ADW + "_02_build", "builder", "BuildOutput",
         json.dumps({"files": ["app.py"]}), 1, 1, T2),
    ])

conn.execute(
    "INSERT INTO gate_results (adw_id, phase_id, attempt, gate, passed,"
    " violations_json, checks_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
    (ADW, ADW + "_03_test", 1, "tests_pass", 1, "[]",
     json.dumps([{"item": "pytest -q", "ok": True, "note": "1 passed"}]), T3))

conn.executemany(
    "INSERT INTO processes (adw_id, kind, name, pid, command, started_at, ended_at)"
    " VALUES (?,?,?,?,?,?,?)",
    [
        (ADW, "adw", "", 4242, "python adws/adw_plan_build_test.py", T0, T4),
        (ADW, "agent", "builder", 4243, "pi builder google/gemini-3.6-flash", T1, T2),
    ])

conn.executemany(
    "INSERT INTO agent_sessions (adw_id, agent, coding_agent, model, color,"
    " session_id, context_tokens, context_window, created_at, last_used_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?)",
    [
        (ADW, "planner", "pi", "google/gemini-3.6-flash", "#a78bfa",
         "sssf-sandbox01-planner", 900, 1000000, T0, T1),
        (ADW, "builder", "pi", "google/gemini-3.6-flash", "#22d3ee",
         "sssf-sandbox01-builder", 1100, 1000000, T1, T2),
    ])

conn.commit()
conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
conn.close()

counts = {}
check = sqlite3.connect(out_path)
for table in ("sessions", "phases", "events", "envelopes", "gate_results",
              "processes", "agent_sessions"):
    counts[table] = check.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
check.close()
print(f"built {out_path}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
PY

echo "tables:"
sqlite3 "$OUT" ".tables"
