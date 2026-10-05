#!/usr/bin/env python3
"""Prove STW-24: the live view over the real stores.

Reads the real pi, Claude Code and opencode stores once, then drives the real
``BrigadeTUI`` and checks the rendered tab bar and tables:

  1. at most 10 tabs, ranked by the newest session in each project
  2. every visible tab label is unique, short and readable
  3. every tab lists at most 50 rows, newest first

It also writes ``docs/sessions.svg`` from that same live render.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-tabs.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from pathlib import Path

from textual.widgets import DataTable, TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI, session_label
from brigade_tui.sources import default_sources
from brigade_tui.store import (
    MAX_ROWS_PER_TAB,
    MAX_TABS,
    SessionStore,
    unique_labels,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
MAX_LABEL_LENGTH = 48

#: A rendered tab label: ``name done/total [blocked] age``. The name is the
#: unique project label. The rest is the per-project progress signal.
PROGRESS_LABEL_RE = re.compile(
    r"^(?P<name>.+?) (?P<done>\d+)/(?P<total>\d+)"
    r"(?: (?P<blocked>\d+) blocked)? (?P<age>\d+[smhd])$"
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


def session_cell(session) -> str:
    """The Session cell the app renders for one session."""
    return session_label(session)


def frozen_sessions(frozen: list[FrozenSource]) -> list:
    sessions: list = []
    for source in frozen:
        sessions.extend(source.snapshot())
    sessions.sort(key=lambda item: item.last_activity, reverse=True)
    return sessions


def expected_view(sessions: list) -> dict:
    """The ranked, capped, labelled view the app must render."""
    pairs = SessionStore([]).by_project(sessions)
    top = pairs[:MAX_TABS]
    labels = unique_labels(top)
    tabs = []
    for index, (project, group) in enumerate(top):
        capped = group[:MAX_ROWS_PER_TAB]
        tabs.append(
            {
                "index": index,
                "key": project.key,
                "label": labels[index],
                "total": len(group),
                "rows": len(capped),
                "first": session_cell(capped[0]) if capped else None,
                "last": session_cell(capped[-1]) if capped else None,
                "newest_first": all(
                    capped[i].last_activity >= capped[i + 1].last_activity
                    for i in range(len(capped) - 1)
                ),
            }
        )
    return {"pairs": pairs, "tabs": tabs}


async def rendered_view(frozen: list[FrozenSource]) -> dict:
    app = BrigadeTUI(sources=frozen)
    async with app.run_test(size=(170, 55)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)
        labels = [str(content.get_tab(pane).label) for pane in panes]
        rows: list[int] = []
        first: list[str | None] = []
        last: list[str | None] = []
        for index, _pane in enumerate(panes):
            table = app.query_one(f"#sessions-{index}", DataTable)
            rows.append(table.row_count)
            if table.row_count:
                first.append(table.get_row_at(0)[5])
                last.append(table.get_row_at(table.row_count - 1)[5])
            else:
                first.append(None)
                last.append(None)
        saved = app.save_screenshot(filename="sessions.svg", path=str(DOCS))
    return {
        "labels": labels,
        "rows": rows,
        "first": first,
        "last": last,
        "screenshot": str(saved),
    }


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    sessions = frozen_sessions(frozen)
    expected = expected_view(sessions)
    rendered = asyncio.run(rendered_view(frozen))

    pairs = expected["pairs"]
    tabs = expected["tabs"]
    failures: list[str] = []

    # (1) At most 10 tabs, ranked by the newest session in each project.
    if len(rendered["labels"]) != len(tabs):
        failures.append(
            f"rendered {len(rendered['labels'])} tabs but expected {len(tabs)}"
        )
    if len(tabs) > MAX_TABS:
        failures.append(f"tab count {len(tabs)} exceeds {MAX_TABS}")
    if len(pairs) > MAX_TABS:
        hidden = {project.key for project, _ in pairs[MAX_TABS:]}
        visible = {tab["key"] for tab in tabs}
        overlap = hidden & visible
        if overlap:
            failures.append(f"older projects own a tab: {sorted(overlap)}")
    sorted_ok = all(
        pairs[i][1][0].last_activity >= pairs[i + 1][1][0].last_activity
        for i in range(len(pairs) - 1)
    )
    if not sorted_ok:
        failures.append("projects are not ranked by newest session")

    # (2) Unique, short, readable labels. Each label leads with the unique
    # project name and then carries the per-project progress signal.
    labels = rendered["labels"]
    if len(set(labels)) != len(labels):
        failures.append(f"duplicate tab labels: {labels}")
    expected_labels = [tab["label"] for tab in tabs]
    rendered_names: list[str] = []
    for index, label in enumerate(labels):
        match = PROGRESS_LABEL_RE.match(label)
        if match is None:
            failures.append(f"label has no progress signal: {label!r}")
            rendered_names.append(label)
            continue
        name = match.group("name")
        rendered_names.append(name)
        if len(name) > MAX_LABEL_LENGTH:
            failures.append(
                f"label name over {MAX_LABEL_LENGTH} chars: {name!r}"
            )
        total = int(match.group("total"))
        done = int(match.group("done"))
        if index < len(tabs) and total != tabs[index]["total"]:
            failures.append(
                f"label {label!r}: signal total {total}"
                f" != store project size {tabs[index]['total']}"
            )
        if not 0 <= done <= total:
            failures.append(f"label {label!r}: done {done} outside 0..{total}")
    if rendered_names != expected_labels:
        failures.append(
            f"rendered names {rendered_names} != expected labels"
            f" {expected_labels}"
        )

    # (2b) Every project, not only the visible ten, gets a unique label. The
    # real stores hold same-basename projects, so this shows the path context.
    all_labels = unique_labels(pairs)
    if len(set(all_labels)) != len(all_labels):
        failures.append("labels collide across all projects")
    base_counts = Counter(project.label for project, _ in pairs)
    disambiguated: dict[str, list[str]] = {}
    for (project, _group), label in zip(pairs, all_labels):
        if base_counts[project.label] > 1:
            disambiguated.setdefault(project.label, []).append(label)

    # (3) At most 50 rows per tab, newest first.
    for index, tab in enumerate(tabs):
        if rendered["rows"][index] != tab["rows"]:
            failures.append(
                f"tab {tab['label']}: rendered {rendered['rows'][index]} rows"
                f" != expected {tab['rows']}"
            )
        if rendered["rows"][index] > MAX_ROWS_PER_TAB:
            failures.append(
                f"tab {tab['label']}: {rendered['rows'][index]} rows >"
                f" {MAX_ROWS_PER_TAB}"
            )
        if not tab["newest_first"]:
            failures.append(f"tab {tab['label']}: rows are not newest first")
        if rendered["first"][index] != tab["first"]:
            failures.append(
                f"tab {tab['label']}: first row {rendered['first'][index]!r}"
                f" != newest session {tab['first']!r}"
            )
        if rendered["last"][index] != tab["last"]:
            failures.append(
                f"tab {tab['label']}: last row {rendered['last'][index]!r}"
                f" != oldest rendered session {tab['last']!r}"
            )

    report = {
        "store": "pi + claude + opencode (real)",
        "sessions_total": len(sessions),
        "projects_total": len(pairs),
        "tab_count": len(rendered["labels"]),
        "tab_limit": MAX_TABS,
        "rows_per_tab_limit": MAX_ROWS_PER_TAB,
        "labels": labels,
        "labels_unique": len(set(labels)) == len(labels),
        "all_project_labels_unique": len(set(all_labels)) == len(all_labels),
        "disambiguated_labels": disambiguated,
        "max_label_length": max((len(label) for label in labels), default=0),
        "rows_per_tab": {
            label: rendered["rows"][index]
            for index, label in enumerate(labels)
        },
        "hidden_projects": max(0, len(pairs) - MAX_TABS),
        "screenshot": rendered["screenshot"],
        "failures": failures,
    }
    print(json.dumps(report, indent=2))

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"PASS: {report['tab_count']} tabs <= {MAX_TABS}, labels unique,"
        f" rows per tab <= {MAX_ROWS_PER_TAB}, newest first"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
