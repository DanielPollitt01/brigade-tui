#!/usr/bin/env python3
"""Prove STW-25: previous sessions stay reachable through the live view.

The live dashboard caps the tab bar to the 10 newest projects and each tab to
the newest 50 rows. This script drives the real ``BrigadeTUI`` over the
real pi, Claude Code and opencode stores and checks the session browser opened
with ``/``:

  1. every project, not only the 10 newest, is reachable
  2. every session in a project, not only the newest 50, is reachable
  3. search finds a known old session in a project the live view hides

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-history.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.widgets import DataTable, Input

from brigade_tui.app import SearchScreen, BrigadeTUI, session_cells
from brigade_tui.sources import default_sources
from brigade_tui.store import MAX_ROWS_PER_TAB, MAX_TABS, SessionStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"


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


def frozen_sessions(frozen: list[FrozenSource]) -> list:
    sessions: list = []
    for source in frozen:
        sessions.extend(source.snapshot())
    sessions.sort(key=lambda item: item.last_activity, reverse=True)
    return sessions


def table_rows(table: DataTable) -> list[tuple[str, ...]]:
    return [tuple(table.get_row_at(index)) for index in range(table.row_count)]


async def drive(frozen: list[FrozenSource], checks: dict) -> dict:
    """Open the browser in a real app and run the three reachability checks."""
    app = BrigadeTUI(sources=frozen)
    results: dict[str, object] = {}
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        screen = app.screen
        if not isinstance(screen, SearchScreen):
            raise AssertionError(f"/ did not open the browser, got {screen!r}")
        table = screen.query_one("#search-results", DataTable)
        search = screen.query_one("#search-input", Input)

        async def filter_by(needle: str) -> list[tuple[str, ...]]:
            search.value = needle
            await pilot.pause()
            return table_rows(table)

        # (1) Every project, not only the 10 newest, is reachable.
        hidden = checks["hidden_project"]
        hidden_rows = await filter_by(hidden.key)
        hidden_wanted = {session_cells(session) for session in hidden.sessions}
        hidden_found = hidden_wanted & set(hidden_rows)
        results["check1"] = {
            "hidden_project": hidden.key,
            "sessions": len(hidden.sessions),
            "rows_matched": len(hidden_rows),
            "all_sessions_reachable": hidden_found == hidden_wanted,
        }

        # (2) Every session in a project, not only the newest 50, is reachable.
        big = checks["big_project"]
        big_rows = await filter_by(big.key)
        big_wanted = {session_cells(session) for session in big.sessions}
        big_found = big_wanted & set(big_rows)
        past_50 = session_cells(big.sessions[MAX_ROWS_PER_TAB])
        results["check2"] = {
            "project": big.key,
            "sessions": len(big.sessions),
            "rows_matched": len(big_rows),
            "all_sessions_reachable": big_found == big_wanted,
            "row_51_reachable": past_50 in big_rows,
            "row_51_session": past_50[-1],
        }

        # (3) Search finds a known old session in a hidden project.
        old = checks["old_session"]
        old_rows = await filter_by(old.session_id)
        results["check3"] = {
            "session_id": old.session_id,
            "project": old.project.key,
            "rows_matched": len(old_rows),
            "found": session_cells(old) in old_rows,
        }

        saved = app.save_screenshot(filename="history.svg", path=str(DOCS))
        results["screenshot"] = str(saved)
    return results


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    sessions = frozen_sessions(frozen)
    pairs = SessionStore([]).by_project(sessions)

    # A project the live view hides: ranked 11th or lower.
    if len(pairs) <= MAX_TABS:
        print("SKIP: fewer than 11 projects in the real stores")
        return 1
    hidden_key, hidden_sessions = pairs[MAX_TABS]
    hidden = type("Project", (), {"key": hidden_key.key, "sessions": hidden_sessions})

    # The real project with the most sessions, if it has more than 50 rows.
    big_key, big_sessions = max(pairs, key=lambda pair: len(pair[1]))
    if len(big_sessions) <= MAX_ROWS_PER_TAB:
        print("SKIP: no project in the real stores has more than 50 sessions")
        return 1
    big = type("Project", (), {"key": big_key.key, "sessions": big_sessions})

    # A known old session: in a hidden project, and ranked 51st or lower there
    # when the project is big enough to hide one.
    old_source = hidden_sessions[-1]
    if len(hidden_sessions) > MAX_ROWS_PER_TAB:
        old_source = hidden_sessions[MAX_ROWS_PER_TAB]
    old = old_source

    checks = {"hidden_project": hidden, "big_project": big, "old_session": old}
    results = asyncio.run(drive(frozen, checks))

    failures: list[str] = []
    if not results["check1"]["all_sessions_reachable"]:
        failures.append("a hidden project's sessions are not all reachable")
    if not results["check2"]["all_sessions_reachable"]:
        failures.append("a project's sessions past row 50 are not all reachable")
    if not results["check2"]["row_51_reachable"]:
        failures.append("the 51st session in the big project is not reachable")
    if not results["check3"]["found"]:
        failures.append("search did not find the known old session")

    report = {
        "store": "pi + claude + opencode (real)",
        "sessions_total": len(sessions),
        "projects_total": len(pairs),
        "tab_limit": MAX_TABS,
        "rows_per_tab_limit": MAX_ROWS_PER_TAB,
        **results,
        "failures": failures,
    }
    print(json.dumps(report, indent=2))

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "PASS: hidden project reachable, row past 50 reachable,"
        " old session found by search"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
