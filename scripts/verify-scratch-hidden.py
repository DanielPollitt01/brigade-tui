#!/usr/bin/env python3
"""Prove seed and scratch sessions stay out of the live tab view.

Reads the real pi, Claude Code and opencode stores once, then drives the real
``BrigadeTUI``. It checks:

  1. a real seed or scratch session, whose cwd is a temp dir, owns no project
     tab in the default view
  2. a real project under /home or the repo tree still owns a tab
  3. the hidden seed session is still polled, and search finds it by id
  4. the flat table for the real project still shows its rows

The marker is the one a seed run sets: the session cwd is a temp dir, or
the session id carries a ``seed-`` or ``scratch-`` prefix. Brigade hides by
that marker, never by guessing from a title.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-scratch-hidden.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.widgets import DataTable, Input, TabPane

from brigade_tui.app import SearchScreen, BrigadeTUI
from brigade_tui.model import SCRATCH_ROOTS, is_scratch
from brigade_tui.sources import default_sources
from brigade_tui.store import MAX_ROWS_PER_TAB, SessionStore

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


def _under(path: str, root: str) -> bool:
    if not path:
        return False
    return path == root or path.startswith(root.rstrip("/") + "/")


def is_temp(path: str) -> bool:
    return any(_under(path, root) for root in SCRATCH_ROOTS)


async def drive(frozen: list[FrozenSource], seed, real) -> dict:
    """Run the real app and take the tab bar, tables and search readings."""
    app = BrigadeTUI(sources=frozen)
    results: dict[str, object] = {}
    async with app.run_test(size=(170, 55)) as pilot:
        await pilot.pause()
        results["pane_keys"] = list(app._pane_keys)
        results["pane_has_temp"] = [key for key in app._pane_keys if is_temp(key)]
        results["pane_has_real"] = [
            key
            for key in app._pane_keys
            if key.startswith("/home") or str(REPO_ROOT).startswith(key)
        ]

        # The flat table for the real project still shows its rows.
        real_tab_rows = None
        if real.project.key in app._pane_keys:
            index = app._pane_keys.index(real.project.key)
            table = app.query_one(f"#sessions-{index}", DataTable)
            real_tab_rows = table.row_count
        results["real_tab_rows"] = real_tab_rows
        results["hidden_in_a_tab"] = any(
            seed.project.key == key for key in app._pane_keys
        )

        # The hidden seed session is still polled.
        results["seed_polled"] = any(
            session.session_id == seed.session_id for session in app._sessions
        )

        # Search finds the seed session by id.
        await pilot.press("slash")
        await pilot.pause()
        screen = app.screen
        if not isinstance(screen, SearchScreen):
            raise AssertionError(f"/ did not open the browser, got {screen!r}")
        search = screen.query_one("#search-input", Input)
        search.value = seed.session_id
        await pilot.pause()
        table = screen.query_one("#search-results", DataTable)
        rows = [tuple(table.get_row_at(i)) for i in range(table.row_count)]
        results["search_rows"] = rows
        results["search_found"] = any(
            row[-1] == seed.session_id for row in rows
        )
        await pilot.press("escape")
        await pilot.pause()
        saved = app.save_screenshot(filename="sessions-scratch-hidden.svg", path=str(DOCS))
        results["screenshot"] = str(saved)
    return results


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    sessions = frozen_sessions(frozen)
    results: dict[str, object] = {"failures": []}

    # A real seed or scratch session, marked by a temp cwd or a seed prefix.
    scratch = [session for session in sessions if is_scratch(session)]
    temp_scratch = [session for session in scratch if is_temp(session.directory)]
    if not temp_scratch:
        print("SKIP: no real seed session with a temp cwd in the stores")
        return 1
    seed = temp_scratch[0]
    results["seed"] = {
        "session_id": seed.session_id,
        "harness": seed.harness.value,
        "project": seed.project.key,
        "directory": seed.directory,
    }

    # A real project under /home or the repo tree, using the store's own view.
    pairs = SessionStore([]).by_project(sessions)
    real_pairs = [
        (project, group)
        for project, group in pairs
        if project.key.startswith("/home")
        and project.key != seed.project.key
    ]
    if not real_pairs:
        print("SKIP: no real /home project in the stores")
        return 1
    real_project, real_group = real_pairs[0]
    real = real_group[0]

    results.update(asyncio.run(drive(frozen, seed, real)))
    results["real"] = {"project": real_project.key, "rows": len(real_group)}
    results["scratch_total"] = len(scratch)
    results["sessions_total"] = len(sessions)

    failures: list[str] = []
    if results["pane_has_temp"]:
        failures.append(
            f"a temp project owns a tab: {results['pane_has_temp']}"
        )
    if not results["pane_has_real"]:
        failures.append("no real /home project owns a tab")
    if results["hidden_in_a_tab"]:
        failures.append("the seed project owns a tab")
    if not results["seed_polled"]:
        failures.append("the seed session is not polled, so it cannot be searched")
    if not results["search_found"]:
        failures.append("search did not find the seed session by id")
    if not results["real_tab_rows"]:
        failures.append("the real project tab shows no rows")

    results["failures"] = failures
    print(json.dumps(results, indent=2))

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "PASS: no temp project owns a tab, the /home project shows,"
        " search finds the seed session by id"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
