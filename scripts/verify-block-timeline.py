#!/usr/bin/env python3
"""Prove the tab body is a real-time block timeline, not a cascade.

Reads the real pi, Claude Code and opencode stores. It finds a real project
where a role ran more than once, then drives the real ``BrigadeTUI``
over those real sessions, at 140x45, and checks:

  1. the block timeline is the default tab body; ``t`` shows the flat table and
     ``t`` returns to the timeline
  2. the x-axis is real elapsed seconds from the earliest session start in the
     tab, and the right edge names the real span
  3. one lane per agent or role; the label is the role when known, else a short
     session id, never the raw title
  4. every block's start and end match the source session's ``started_at`` and
     ``last_activity``, and its offset is the real elapsed seconds
  5. a role that ran more than once owns two blocks at different real times,
     the later block to the right of the earlier
  6. blocks in different lanes overlap in columns only when the real intervals
     overlapped
  7. blocks are coloured by model, with the model legend; no state word

It writes ``docs/block-timeline-item38.svg`` (and a PNG when ``rsvg-convert`` is
present) from the real render.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-block-timeline.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from textual.widgets import DataTable, TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.model import Session, is_bare_hash, session_label
from brigade_tui.sources import default_sources
from brigade_tui.timeline import (
    TimelineView,
    display_model,
    format_axis_time,
    time_span,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
SVG_PATH = DOCS / "block-timeline-item38.svg"
PNG_PATH = DOCS / "block-timeline-item38.png"

ROLES = ("planner", "builder", "reviewer")
STATE_WORDS = ("running", "queued", "success", "failed", "idle", "done")

#: Column overlap allowance for two real-time-disjoint blocks. Rounding two
#: endpoints to grid cells can bring them within one cell.
OVERLAP_TOLERANCE = 1


class FilteredSource:
    """A real source that reports an explicit, already-read session list."""

    harness = None  # type: ignore[assignment]

    def __init__(self, sessions: list[Session]) -> None:
        self.sessions = list(sessions)

    def exists(self) -> bool:
        return True

    def snapshot(self) -> list[Session]:
        return list(self.sessions)


def read_real_sessions() -> list[Session]:
    sessions: list[Session] = []
    for source in default_sources():
        if source.exists():
            sessions.extend(source.snapshot())
    return sessions


def pick_multi_run_project(
    sessions: list[Session],
) -> tuple[str, list[Session]]:
    """The real repeated-role project with the tightest real-time span.

    A short span keeps the repeated blocks readable: a project whose sessions
    span days would squeeze every block into one grid cell.
    """
    by_project: dict[str, list[Session]] = defaultdict(list)
    for session in sessions:
        by_project[session.project.key].append(session)
    best_key = ""
    best_span = float("inf")
    for key, group in by_project.items():
        counts: dict[str, int] = defaultdict(int)
        for session in group:
            if session.role:
                counts[session.role] += 1
        if max(counts.values(), default=0) < 2:
            continue
        _origin, span = time_span(group)
        if span < best_span:
            best_span = span
            best_key = key
    return best_key, by_project.get(best_key, [])


async def drive(sessions: list[Session]) -> dict:
    app = BrigadeTUI(sources=(FilteredSource(sessions),))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)
        index = 0
        content.active = panes[index].id
        await pilot.pause()
        timeline = app.query_one("#timeline-0", TimelineView)
        # This historical verifier checks block geometry, not the live window,
        # so anchor the window at the project's own latest activity.
        timeline._now = max(session.last_activity for session in sessions)
        await pilot.pause()
        table = app.query_one("#sessions-0", DataTable)
        default_body = (bool(timeline.display), bool(table.display))
        lanes = timeline.lanes
        colours = timeline.model_colours
        rendered = timeline.render()
        plain = rendered.plain
        window = timeline.window
        saved = app.save_screenshot(filename=SVG_PATH.name, path=str(DOCS))
        await pilot.press("t")
        await pilot.pause()
        toggled = (bool(timeline.display), bool(table.display))
        await pilot.press("t")
        await pilot.pause()
        restored = (bool(timeline.display), bool(table.display))
    return {
        "lanes": lanes,
        "colours": colours,
        "plain": plain,
        "window": window,
        "default_body": default_body,
        "toggled": toggled,
        "restored": restored,
        "screenshot": saved,
    }


async def drive_default() -> int:
    """Render the real default view and return the tab count."""
    app = BrigadeTUI(sources=default_sources())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        return len(content.query(TabPane))


def block_pairs(lanes) -> list[tuple]:
    pairs = []
    for lane in lanes:
        for block in lane.blocks:
            pairs.append((lane, block))
    return pairs


def main() -> int:
    real = read_real_sessions()
    project_key, group = pick_multi_run_project(real)
    if not group:
        print("FAIL: no real project holds a repeated role")
        return 1

    result = asyncio.run(drive(group))
    lanes = result["lanes"]
    plain = result["plain"]
    failures: list[str] = []

    origin, span = time_span(group)
    origin = result["window"].start
    by_key = {(session.session_id, session.started_at): session for session in group}

    # (1) Default body is the timeline; t toggles the flat table.
    if not result["default_body"][0] or result["default_body"][1]:
        failures.append("the block timeline is not the default tab body")
    if not result["toggled"][1] or result["toggled"][0]:
        failures.append("t did not show the flat table")
    if not result["restored"][0] or result["restored"][1]:
        failures.append("t did not return to the timeline")

    # (2) Axis is human elapsed time and the right edge names the window span.
    lines = plain.splitlines()
    axis_line = next(
        (line for line in lines if line.strip().startswith("0:00")),
        lines[1] if len(lines) > 1 else "",
    )
    expected_axis = format_axis_time(result["window"].span)
    if "0:00" not in axis_line:
        failures.append(f"axis has no 0:00 label: {axis_line!r}")
    if expected_axis not in axis_line:
        failures.append(f"axis does not name the window span {expected_axis}: {axis_line!r}")

    # (3) One lane per agent or role, label is meaningful, never a bare hash.
    for lane in lanes:
        if lane.key in ROLES or lane.key == lane.blocks[0].session_id:
            pass
        else:
            failures.append(f"lane key {lane.key!r} is neither a role nor a session id")
        if lane.label in ROLES:
            continue
        session = by_key.get((lane.blocks[0].session_id, lane.blocks[0].start))
        if session is None:
            failures.append(f"lane {lane.key!r}: no source session for label")
            continue
        if is_bare_hash(lane.label) or lane.label != session_label(session):
            failures.append(
                f"lane label {lane.label!r} is not a meaningful project label"
            )

    # (4) Every block start and end matches the source timestamps.
    checked = 0
    for lane, block in block_pairs(lanes):
        session = by_key.get((block.session_id, block.start))
        if session is None or session.started_at is None:
            failures.append(f"{block.session_id[:8]}: no source session for block")
            continue
        if block.start != session.started_at:
            failures.append(
                f"{block.session_id[:8]}: block start {block.start} != started_at"
                f" {session.started_at}"
            )
        if block.end != session.last_activity:
            failures.append(
                f"{block.session_id[:8]}: block end {block.end} != last_activity"
                f" {session.last_activity}"
            )
        want_start = (session.started_at - origin).total_seconds()
        want_end = (session.last_activity - origin).total_seconds()
        if abs(block.offset_start - want_start) > 0.001:
            failures.append(
                f"{block.session_id[:8]}: offset_start {block.offset_start}"
                f" != {want_start}"
            )
        if abs(block.offset_end - want_end) > 0.001:
            failures.append(
                f"{block.session_id[:8]}: offset_end {block.offset_end}"
                f" != {want_end}"
            )
        checked += 1
    if checked < 3:
        failures.append(f"only {checked} blocks checked against source timestamps")

    # (5) A role that ran twice owns two blocks at different, ordered times.
    repeated = [lane for lane in lanes if len(lane.blocks) > 1]
    if not repeated:
        failures.append("no lane has two blocks for a repeated role")
    for lane in repeated:
        starts = [block.offset_start for block in lane.blocks]
        if starts != sorted(starts):
            failures.append(f"lane {lane.label!r}: blocks are not in start order")
        if len(set(starts)) != len(starts):
            failures.append(f"lane {lane.label!r}: two blocks share one start")

    # (6) Blocks in different lanes overlap only when the sessions overlapped.
    for index, (lane_a, block_a) in enumerate(block_pairs(lanes)):
        for lane_b, block_b in block_pairs(lanes)[index + 1 :]:
            if lane_a.key == lane_b.key:
                continue
            real_overlap = (
                block_a.offset_start < block_b.offset_end
                and block_b.offset_start < block_a.offset_end
            )
            col_overlap = max(
                0,
                min(block_a.end_col, block_b.end_col)
                - max(block_a.start_col, block_b.start_col),
            )
            if not real_overlap and col_overlap > OVERLAP_TOLERANCE:
                failures.append(
                    f"{lane_a.label!r} and {lane_b.label!r} overlap in columns"
                    f" ({col_overlap} cells) without a real overlap"
                )

    # (7) Colour by model, legend names the models, no state word.
    colours = result["colours"]
    model_colour: dict[str, str] = {}
    for _lane, block in block_pairs(lanes):
        model = display_model(block.model)
        prior = model_colour.get(model)
        if prior is not None and prior != colours.get(model):
            failures.append(f"{model}: two colours for one model")
        model_colour[model] = colours.get(model)
    if len(set(model_colour.values())) != len(model_colour):
        failures.append("two different models share a colour")
    for model in model_colour:
        if model not in plain:
            failures.append(f"legend does not name model {model!r}")
    lowered = plain.lower()
    for word in STATE_WORDS:
        if word in lowered:
            failures.append(f"state word {word!r} appears in the timeline")

    # Default real view still caps at 10 tabs.
    default_tabs = asyncio.run(drive_default())
    if default_tabs > 10:
        failures.append(f"default view shows {default_tabs} tabs, over the cap")

    print(f"project: {project_key}")
    print(f"real sessions in project: {len(group)}")
    print(f"axis origin: {origin.isoformat()}")
    print(f"window span: {result['window'].span:.1f}s   axis label: {expected_axis}")
    print(f"axis: {axis_line.strip()}")
    print(f"default real view tabs: {default_tabs}")
    print("")
    print(f"{'lane':10s} {'label':10s} {'blocks':>6s}  block start->end (s)  cols")
    for lane in lanes:
        rendered_blocks = ", ".join(
            f"[{block.offset_start:.1f},{block.offset_end:.1f}]"
            f"c[{block.start_col},{block.end_col}]"
            for block in lane.blocks
        )
        print(
            f"{lane.key[:10]:10s} {lane.label[:10]:10s} {len(lane.blocks):6d}"
            f"  {rendered_blocks}"
        )
    print("")
    print(f"models in use: {sorted(model_colour)}")
    print(f"blocks checked against source timestamps: {checked}")
    print(f"screenshot: {Path(result['screenshot']).relative_to(REPO_ROOT)}")

    if failures:
        print("")
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    if code == 0 and SVG_PATH.exists():
        try:
            subprocess.run(
                ["rsvg-convert", "-o", str(PNG_PATH), str(SVG_PATH)],
                check=True,
            )
            print(f"saved {SVG_PATH}")
            print(f"saved {PNG_PATH}")
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"note: PNG conversion skipped: {error}", file=sys.stderr)
    sys.exit(code)
