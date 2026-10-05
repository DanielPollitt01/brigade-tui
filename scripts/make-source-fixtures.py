#!/usr/bin/env python3
"""Regenerate the source fixtures for the Brigade tests.

Writes one real fixture session for each harness under ``tests/fixtures/``:

- pi: a JSONL transcript under ``pi/<cwd-slug>/``
- Claude Code: a JSONL transcript under ``claude/<cwd-slug>/``
- opencode: a SQLite store under ``opencode/opencode.db``

The tests copy these into a temp root and point ``PI_SESSION_DIR``,
``CLAUDE_PROJECTS_DIR`` and ``OPENCODE_DB`` at the copy. So the suite runs on a
machine with no pi, Claude Code or opencode install.

The output is deterministic. Rerun to rebuild:

    python3 scripts/make-source-fixtures.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"

#: The directory every fixture session claims as its cwd. Machine-independent.
#: It is under ``/home``, not ``/tmp``, so the fixtures are real projects and
#: stay visible: Brigade hides seed and scratch sessions that live in a temp
#: dir. The scratch-hiding tests build their own temp-cwd session on purpose.
PROJECT_DIR = "/home/brigade-fixture"
SLUG = "--home-brigade-fixture"

PI_ID = "pi-fixture-session"
CLAUDE_ID = "claude-fixture-session"
OPENCODE_ID = "ses_fixture_opencode"
OPENCODE_MODEL = json.dumps(
    {"id": "fixture-model", "providerID": "fixture-provider", "variant": "default"}
)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pi() -> None:
    records = [
        {
            "type": "session",
            "version": 3,
            "id": PI_ID,
            "timestamp": "2026-01-01T00:00:00.000Z",
            "cwd": PROJECT_DIR,
        },
        {
            "type": "model_change",
            "id": "mc1",
            "parentId": None,
            "timestamp": "2026-01-01T00:00:01.000Z",
            "provider": "fixture-provider",
            "modelId": "fixture-model",
        },
        {
            "type": "message",
            "id": "msg1",
            "parentId": "mc1",
            "timestamp": "2026-01-01T00:00:02.000Z",
            "message": {"role": "user", "content": "Fixture pi session"},
        },
        {
            "type": "message",
            "id": "msg2",
            "parentId": "msg1",
            "timestamp": "2026-01-01T00:00:03.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 30,
                    "cacheWrite": 5,
                    "reasoning": 4,
                    "totalTokens": 155,
                    "cost": {
                        "input": 0.001,
                        "output": 0.0002,
                        "cacheRead": 0.0003,
                        "cacheWrite": 0.0001,
                        "total": 0.0016,
                    },
                },
            },
        },
        {
            "type": "message",
            "id": "msg3",
            "parentId": "msg2",
            "timestamp": "2026-01-01T00:00:04.000Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "usage": {
                    "input": 40,
                    "output": 10,
                    "cacheRead": 0,
                    "cacheWrite": 2,
                    "reasoning": 1,
                    "totalTokens": 52,
                    "cost": {
                        "input": 0.0004,
                        "output": 0.0001,
                        "cacheRead": 0,
                        "cacheWrite": 0.00002,
                        "total": 0.00052,
                    },
                },
            },
        },
    ]
    write_jsonl(FIXTURES / "pi" / SLUG / "pi-fixture.jsonl", records)


def write_claude() -> None:
    records = [
        {
            "type": "user",
            "sessionId": CLAUDE_ID,
            "uuid": "u1",
            "cwd": PROJECT_DIR,
            "timestamp": "2026-01-01T00:00:00.000Z",
            "message": {"role": "user", "content": "Fixture Claude session"},
        },
        {
            "type": "assistant",
            "sessionId": CLAUDE_ID,
            "uuid": "a1",
            "cwd": PROJECT_DIR,
            "timestamp": "2026-01-01T00:00:01.000Z",
            "message": {
                "id": "msg_fixture_1",
                "role": "assistant",
                "model": "claude-fixture-model",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 100,
                },
            },
        },
        {
            "type": "assistant",
            "sessionId": CLAUDE_ID,
            "uuid": "a2",
            "cwd": PROJECT_DIR,
            "timestamp": "2026-01-01T00:00:02.000Z",
            "message": {
                "id": "msg_fixture_1",
                "role": "assistant",
                "model": "claude-fixture-model",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 100,
                },
            },
        },
        {
            "type": "assistant",
            "sessionId": CLAUDE_ID,
            "uuid": "a3",
            "cwd": PROJECT_DIR,
            "timestamp": "2026-01-01T00:00:03.000Z",
            "message": {
                "id": "msg_fixture_2",
                "role": "assistant",
                "model": "claude-fixture-model",
                "content": [{"type": "text", "text": "done"}],
                "usage": {
                    "input_tokens": 4,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 6,
                },
            },
        },
    ]
    write_jsonl(FIXTURES / "claude" / SLUG / "claude-fixture.jsonl", records)


def write_opencode() -> None:
    path = FIXTURES / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE session (
              id text PRIMARY KEY,
              project_id text NOT NULL,
              directory text NOT NULL,
              title text NOT NULL,
              version text NOT NULL,
              cost real DEFAULT 0 NOT NULL,
              tokens_input integer DEFAULT 0 NOT NULL,
              tokens_output integer DEFAULT 0 NOT NULL,
              tokens_cache_read integer DEFAULT 0 NOT NULL,
              tokens_cache_write integer DEFAULT 0 NOT NULL,
              model text,
              time_created integer NOT NULL,
              time_updated integer NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO session (id, project_id, directory, title, version, cost,"
            " tokens_input, tokens_output, tokens_cache_read, tokens_cache_write,"
            " model, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                OPENCODE_ID,
                "global",
                PROJECT_DIR,
                "Fixture opencode session",
                "1.0.0",
                0.0123,
                100,
                50,
                10,
                5,
                OPENCODE_MODEL,
                1767225600000,
                1767225601000,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    write_pi()
    write_claude()
    write_opencode()
    print(f"wrote source fixtures under {FIXTURES}")


if __name__ == "__main__":
    main()
