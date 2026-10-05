#!/usr/bin/env python3
"""Prove ISC-2: the session count the TUI renders equals the sum of the
sessions each enabled source reports.

Read-only. It never writes to a source store.

Method:
  1. Read each real source (pi, Claude Code, opencode) once and count the
     sessions it reports. Also count the raw store rows independently:
     depth-2 ``*.jsonl`` files for pi and Claude, the opencode ``session``
     table.
  2. Hand the same frozen snapshot to the real ``BrigadeTUI`` and
     count the DataTable rows it renders.

The frozen wrapper exists only to remove drift while counting. A session
written by another agent mid-run would otherwise move both numbers between
reads. The wrapper reads the real source; it does not change the app.

Usage:
    uv run python scripts/verify-counts.py

Exit status 0 when rendered == sum of reported. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from textual.widgets import DataTable

from brigade_tui.app import BrigadeTUI
from brigade_tui.sources import default_sources
from brigade_tui.sources.claude import ClaudeSessions
from brigade_tui.sources.opencode import OpencodeSessions
from brigade_tui.sources.pi import PiSessions


class FrozenSource:
    """A real source whose snapshot is read once, then held."""

    def __init__(self, real) -> None:
        self.real = real
        self.harness = real.harness
        self._snapshot: list | None = None

    def exists(self) -> bool:
        return self.real.exists()

    def snapshot(self) -> list:
        if self._snapshot is None:
            self._snapshot = list(self.real.snapshot())
        return list(self._snapshot)

    @property
    def reported(self) -> int:
        return len(self._snapshot or [])


def raw_store_counts() -> dict[str, int]:
    """Count the raw store rows the adapters read, independent of the adapter."""
    counts: dict[str, int] = {}

    pi_root = PiSessions().root
    counts["pi_files_depth2"] = sum(
        1 for p in pi_root.glob("*/*.jsonl")
    )

    claude_root = ClaudeSessions().root
    counts["claude_files_depth2"] = sum(
        1 for p in claude_root.glob("*/*.jsonl")
    )

    db = OpencodeSessions().db
    if db.is_file():
        uri = f"file:{db}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            counts["opencode_session_rows"] = conn.execute(
                "SELECT count(*) FROM session"
            ).fetchone()[0]
    else:
        counts["opencode_session_rows"] = 0
    return counts


async def rendered_row_count(sources: list[FrozenSource]) -> tuple[int, dict[str, int]]:
    """Drive the real app and count one rendered DataTable row per session."""
    app = BrigadeTUI(sources=sources)
    async with app.run_test() as pilot:
        await pilot.pause()
        per_pane: dict[str, int] = {}
        total = 0
        for table in app.query(DataTable):
            key = str(table.id)
            per_pane[key] = table.row_count
            total += table.row_count
        return total, per_pane


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    for source in frozen:
        source.snapshot()  # read once, freeze for the render count
    per_source = {
        source.harness.value: {
            "exists": source.exists(),
            "reported": source.reported,
        }
        for source in frozen
    }
    direct_total = sum(item["reported"] for item in per_source.values())

    rendered, per_pane = asyncio.run(rendered_row_count(frozen))

    report = {
        "sources": per_source,
        "sum_of_reported": direct_total,
        "rendered_rows": rendered,
        "panes": per_pane,
        "raw_store_counts": raw_store_counts(),
        "matches": rendered == direct_total,
    }
    print(json.dumps(report, indent=2))

    if rendered != direct_total:
        print(
            f"FAIL: rendered {rendered} != reported {direct_total}",
            file=__import__("sys").stderr,
        )
        return 1
    print(f"PASS: rendered {rendered} == sum of reported {direct_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
