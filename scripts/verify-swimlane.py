#!/usr/bin/env python3
"""Prove the block timeline labels and colours against the real stores.

Reads the real pi, Claude Code and opencode stores once, then drives the real
``BrigadeTUI``. In the project tab for this worktree it checks:

  1. the timeline is the default body and the flat table is behind one key
  2. a time axis across the top is labelled in real elapsed seconds
  3. one lane per agent or role; each lane label is a role (planner, builder,
     reviewer), or the project name plus a short title fragment or a readable
     id, never a bare truncated hash and never the raw prompt title
  4. every block sits at its real start and is sized by its real duration,
     ``last_activity`` minus ``started_at``
  5. bar colour is chosen by model: same model shares a colour, different
     models differ, and the legend names every model in use
  6. no state word appears anywhere in the render

It writes ``docs/swimlane-stw31.svg`` and ``docs/swimlane-stw31.png`` from that
real render.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-swimlane.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from textual.widgets import DataTable, TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.model import is_bare_hash, session_label
from brigade_tui.sources import default_sources
from brigade_tui.store import MAX_ROWS_PER_TAB, SessionStore
from brigade_tui.timeline import (
    TimelineView,
    display_model,
    format_axis_time,
    time_span,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
SVG_PATH = DOCS / "swimlane-stw31.svg"
PNG_PATH = DOCS / "swimlane-stw31.png"

ROLES = ("planner", "builder", "reviewer")
STATE_WORDS = ("running", "queued", "success", "failed", "idle", "done")


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


async def drive(frozen: list[FrozenSource]) -> dict:
    """Render the real TUI, check the timeline, and save the screenshots."""
    app = BrigadeTUI(sources=frozen)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)

        # Find the tab for this worktree. Fall back to the first tab.
        index = next(
            (i for i, key in enumerate(app._pane_keys) if key == str(REPO_ROOT)),
            0,
        )
        content.active = panes[index].id
        await pilot.pause()

        timeline = app.query_one(f"#timeline-{index}", TimelineView)
        table = app.query_one(f"#sessions-{index}", DataTable)
        default_body = {
            "timeline_visible": bool(timeline.display),
            "table_visible": bool(table.display),
        }

        lanes = timeline.lanes
        colours = timeline.model_colours
        rendered = timeline.render()
        plain = rendered.plain
        _origin, span = time_span(timeline._sessions)
        window = timeline.window

        saved = app.save_screenshot(filename=SVG_PATH.name, path=str(DOCS))
        screenshot = str(saved)

        # Toggle to the flat table, then back to the timeline.
        await pilot.press("t")
        await pilot.pause()
        toggled = {
            "timeline_visible": bool(timeline.display),
            "table_visible": bool(table.display),
        }
        await pilot.press("t")
        await pilot.pause()
        restored = {
            "timeline_visible": bool(timeline.display),
            "table_visible": bool(table.display),
        }

    return {
        "index": index,
        "pane": app._pane_keys[index],
        "lanes": lanes,
        "colours": colours,
        "plain": plain,
        "span": span,
        "window": window,
        "default_body": default_body,
        "toggled": toggled,
        "restored": restored,
        "screenshot": screenshot,
    }


def label_ok(lane, by_id) -> bool:
    """True when a lane label is meaningful, not a bare truncated hash."""
    if lane.label in ROLES:
        return True
    if not lane.label or is_bare_hash(lane.label):
        return False
    session = by_id.get(lane.blocks[0].session_id)
    if session is None:
        return False
    return lane.label == session_label(session)


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    sessions = frozen_sessions(frozen)
    pairs = SessionStore([]).by_project(sessions)
    group = next(
        (rows for project, rows in pairs if project.key == str(REPO_ROOT)),
        sessions,
    )
    group = group[:MAX_ROWS_PER_TAB]
    by_id = {session.session_id: session for session in group}

    result = asyncio.run(drive(frozen))
    lanes = result["lanes"]
    failures: list[str] = []

    # (1) Timeline is the default body, the table is behind one key.
    if not result["default_body"]["timeline_visible"]:
        failures.append("timeline is not the default tab body")
    if result["default_body"]["table_visible"]:
        failures.append("flat table is visible by default")
    if not result["toggled"]["table_visible"]:
        failures.append("t did not show the flat table")
    if not result["restored"]["timeline_visible"]:
        failures.append("t did not return to the timeline")

    # (2) Time axis across the top, in human elapsed time.
    lines = result["plain"].splitlines()
    axis_line = next(
        (line for line in lines if line.strip().startswith("0:00")),
        lines[0] if lines else "",
    )
    if "0:00" not in axis_line:
        failures.append(f"time axis has no 0:00 label: {axis_line!r}")
    span_label = format_axis_time(result["window"].span)
    if span_label not in axis_line:
        failures.append(f"axis does not name the window span {span_label}: {axis_line!r}")

    # (3) One lane per agent or role; label is meaningful, never a bare hash.
    for lane in lanes:
        if not label_ok(lane, by_id):
            failures.append(
                f"lane {lane.key!r}: label {lane.label!r} is not a meaningful label"
            )

    # (4) Every block start, end and duration against the real fields.
    checks = 0
    for lane in lanes:
        for block in lane.blocks:
            session = by_id.get(block.session_id)
            if session is None or session.started_at is None:
                continue
            expected = (session.last_activity - session.started_at).total_seconds()
            if abs(block.duration - expected) > 0.001:
                failures.append(
                    f"{block.session_id[:8]}: duration {block.duration} != {expected}"
                )
            if block.start != session.started_at:
                failures.append(f"{block.session_id[:8]}: block start != started_at")
            if block.end != session.last_activity:
                failures.append(f"{block.session_id[:8]}: block end != last_activity")
            checks += 1
    if checks < 2:
        failures.append(f"only {checks} blocks with a started_at to check")

    # (5) Colour by model: same model same colour, different models differ.
    colours = result["colours"]
    model_colour: dict[str, str] = {}
    for lane in lanes:
        for block in lane.blocks:
            model = display_model(block.model)
            if model in model_colour and model_colour[model] != colours.get(model):
                failures.append(f"{model}: two colours for one model")
            model_colour[model] = colours.get(model)
    if len(set(model_colour.values())) != len(model_colour):
        failures.append("two different models share a colour")
    for model in model_colour:
        if model not in result["plain"]:
            failures.append(f"legend does not name model {model!r}")

    # (6) No state word.
    lowered = result["plain"].lower()
    for word in STATE_WORDS:
        if word in lowered:
            failures.append(f"state word {word!r} appears in the timeline")
    if result["plain"].count("■") != len(model_colour) or not model_colour:
        failures.append("legend swatch count does not match the models in use")

    print(f"tab: {result['pane']}")
    print(f"lanes: {len(lanes)}  sessions in tab: {len(group)}")
    print(f"axis: {axis_line.strip()}")
    print("")
    print("blocks per lane")
    for lane in lanes:
        for block in lane.blocks:
            model = display_model(block.model)
            print(
                f"  {lane.label:10s} {block.session_id[:8]}"
                f"  model={model!r}  colour={colours.get(model)!r}"
                f"  duration={block.duration:.1f}s"
                f"  offset=[{block.offset_start:.1f}, {block.offset_end:.1f}]"
                f"  cols=[{block.start_col}, {block.end_col}]"
            )
    print("")
    print(f"models in use: {sorted(model_colour)}")
    print(f"blocks checked: {checks}")
    if failures:
        print("")
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    # The SVG is the raw render. Convert to PNG for the saved screenshot.
    if code == 0 and SVG_PATH.exists():
        try:
            subprocess.run(
                ["rsvg-convert", "-o", str(PNG_PATH), str(SVG_PATH)],
                check=True,
            )
            print(f"saved {SVG_PATH}")
            print(f"saved {PNG_PATH}")
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"FAIL: could not convert screenshot: {error}", file=sys.stderr)
            code = 1
    sys.exit(code)
