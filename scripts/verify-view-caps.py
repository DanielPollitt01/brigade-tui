#!/usr/bin/env python3
"""Prove STW-50: the view states the tab cap and the row cap.

Reads the real pi, Claude Code and opencode stores once, then drives the real
``BrigadeTUI`` and reads the rendered ``#view-caps`` line. The line
must state how many projects and rows the live view shows out of how many
exist, and name both caps:

  1. projects shown == the number of rendered project tabs, capped at 10
  2. projects exist == every project in the snapshot, scratch hidden
  3. rows shown == the rows the tab tables render, each project capped at 50
  4. rows exist == every row in every project in the snapshot

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-view-caps.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from textual.widgets import Static

from brigade_tui.app import BrigadeTUI
from brigade_tui.sources import default_sources
from brigade_tui.store import (
    MAX_ROWS_PER_TAB,
    MAX_TABS,
    SessionStore,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"

#: The rendered caps line, parsed into named counts.
CAPS_RE = re.compile(
    r"^(?P<ps>\d+) of (?P<pt>\d+) projects "
    r"\(tab cap (?P<tc>\d+)\)\s+·\s+"
    r"(?P<rs>\d+) of (?P<rt>\d+) rows "
    r"\(row cap (?P<rc>\d+)\)$"
)


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


def expected_caps(pairs) -> dict:
    """The counts the view must state, computed by hand from the snapshot."""
    projects_total = len(pairs)
    projects_shown = min(projects_total, MAX_TABS)
    rows_total = sum(len(group) for _project, group in pairs)
    rows_shown = sum(
        min(len(group), MAX_ROWS_PER_TAB)
        for _project, group in pairs[:MAX_TABS]
    )
    return {
        "projects_shown": projects_shown,
        "projects_total": projects_total,
        "rows_shown": rows_shown,
        "rows_total": rows_total,
        "tab_cap": MAX_TABS,
        "row_cap": MAX_ROWS_PER_TAB,
        "projects_hidden": max(0, projects_total - MAX_TABS),
        "rows_hidden": rows_total - rows_shown,
    }


async def rendered_caps(frozen: list[FrozenSource]) -> dict:
    """Drive the real app and read the rendered caps line."""
    app = BrigadeTUI(sources=frozen)
    async with app.run_test(size=(170, 55)) as pilot:
        await pilot.pause()
        line = app.query_one("#view-caps", Static).render().plain
        return {"line": line, "visible_keys": list(app._tab_order)}


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    sessions = [session for source in frozen for session in source.snapshot()]
    pairs = SessionStore([]).by_project(sessions)
    expected = expected_caps(pairs)
    rendered = asyncio.run(rendered_caps(frozen))

    failures: list[str] = []
    match = CAPS_RE.match(rendered["line"])
    if match is None:
        failures.append(f"caps line did not parse: {rendered['line']!r}")
        rendered_counts = {}
    else:
        rendered_counts = {
            "projects_shown": int(match.group("ps")),
            "projects_total": int(match.group("pt")),
            "tab_cap": int(match.group("tc")),
            "rows_shown": int(match.group("rs")),
            "rows_total": int(match.group("rt")),
            "row_cap": int(match.group("rc")),
        }
        for field in (
            "projects_shown",
            "projects_total",
            "rows_shown",
            "rows_total",
            "tab_cap",
            "row_cap",
        ):
            if rendered_counts[field] != expected[field]:
                failures.append(
                    f"caps {field} {rendered_counts[field]}"
                    f" != expected {expected[field]}"
                )
        if rendered_counts["tab_cap"] != MAX_TABS:
            failures.append("the tab cap is not named in the line")
        if rendered_counts["row_cap"] != MAX_ROWS_PER_TAB:
            failures.append("the row cap is not named in the line")

    # The stated shown projects must equal the rendered tab bar.
    if rendered_counts.get("projects_shown") != len(rendered["visible_keys"]):
        failures.append(
            f"caps projects_shown {rendered_counts.get('projects_shown')}"
            f" != rendered tabs {len(rendered['visible_keys'])}"
        )

    report = {
        "store": "pi + claude + opencode (real)",
        "sessions_total": len(sessions),
        "caps_line": rendered["line"],
        "rendered_counts": rendered_counts,
        "expected_counts": expected,
        "visible_tabs": len(rendered["visible_keys"]),
        "hidden_projects": expected["projects_hidden"],
        "hidden_rows": expected["rows_hidden"],
        "matches": not failures,
        "failures": failures,
    }
    EVIDENCE.mkdir(exist_ok=True)
    out = EVIDENCE / "STW-50-view-caps.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"PASS: view states {rendered['line']};"
        f" {expected['projects_hidden']} projects and"
        f" {expected['rows_hidden']} rows hidden by the caps are counted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
