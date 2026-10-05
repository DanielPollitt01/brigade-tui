#!/usr/bin/env python3
"""Prove the ITEM-38 block timeline against the real stores.

Two parts:

1. A live read of the real ``BrigadeTUI`` over the real pi, Claude Code
   and opencode stores. It selects the tab for this worktree, reads the seconds
   axis and the lanes, and writes an SVG screenshot. The axis max must equal
   the real elapsed span, and no block may start past the right edge.

2. A unit check that renders a 1200s timeline. The axis must name 1200s and no
   block may be pushed off the right edge.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-waterfall.py --out evidence/waterfall-live-T0.txt
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.widgets import TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.model import Harness, ProjectRef, Session
from brigade_tui.sources import default_sources
from brigade_tui.timeline import (
    NOW_MARKER,
    TimelineView,
    build_lanes,
    build_window,
    format_axis_time,
    render_timeline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"


def sequential(durations: list[float]) -> list[Session]:
    """Sessions laid end to end, so the span is ``sum(durations)`` seconds."""
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    sessions: list[Session] = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        sessions.append(
            Session(
                harness=Harness.PI,
                session_id=f"unit-1200-{index}",
                project=ProjectRef(key="unit", label="unit"),
                directory="unit",
                last_activity=now + timedelta(seconds=cursor + duration),
                started_at=now + timedelta(seconds=cursor),
            )
        )
        cursor += duration
    return sessions


def check_1200s_timeline() -> tuple[bool, list[str]]:
    """Render a 1200s history and check the 600s window clips it."""
    lines: list[str] = []
    sessions = sequential([400.0, 500.0, 300.0])
    window = build_window(sessions)
    lanes = build_lanes(sessions, plot_width=100)
    axis_width = 100 - 1  # one column held for the now marker
    text = render_timeline(sessions, width=122)
    axis = next(
        (line for line in text.plain.splitlines() if line.strip().startswith("0:00")),
        "",
    )
    lines.append("unit check: 1200s history, 600s window")
    lines.append(f"  window: {window.span:.1f}s ending at now")
    lines.append(f"  axis: {axis}")
    for lane in lanes:
        for block in lane.blocks:
            lines.append(
                f"  {block.offset_start:7.1f} -> {block.offset_end:7.1f}s"
                f"  cols [{block.start_col},{block.end_col}]"
            )
    ok = True
    if window.span != 600.0:
        lines.append("  FAIL: the window does not span 600s")
        ok = False
    visible = {block.session_id for lane in lanes for block in lane.blocks}
    if "unit-1200-0" in visible:
        lines.append("  FAIL: a block older than the window is still drawn")
        ok = False
    if format_axis_time(600.0) not in axis:
        lines.append("  FAIL: axis does not name the 10 minute window")
        ok = False
    if NOW_MARKER not in axis:
        lines.append("  FAIL: axis has no now marker")
        ok = False
    if any(
        block.start_col < 0 or block.end_col > axis_width
        for lane in lanes
        for block in lane.blocks
    ):
        lines.append("  FAIL: a block sits past the window edge")
        ok = False
    if all(block.start_col < block.end_col for lane in lanes for block in lane.blocks) is False:
        lines.append("  FAIL: a block has zero width")
        ok = False
    if ok:
        lines.append("  PASS")
    return ok, lines


async def live_read(target_key: str, svg_name: str) -> tuple[bool, list[str]]:
    """Read the real TUI once and report the axis and block geometry."""
    lines: list[str] = []
    app = BrigadeTUI(sources=default_sources())
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)
        index = next(
            (i for i, key in enumerate(app._pane_keys) if key == target_key), 0
        )
        content.active = panes[index].id
        await pilot.pause()
        timeline = app.query_one(f"#timeline-{index}", TimelineView)
        await pilot.pause()
        width = timeline.size.width or 80
        label_width = min(22, max(10, width // 4))
        plot_width = max(8, width - label_width)
        axis_width = max(1, plot_width - 1)
        lanes = timeline.lanes
        rendered = timeline.render().plain
        axis = next(
            (line for line in rendered.splitlines() if line.strip().startswith("0:00")),
            "",
        )
        window = timeline.window
        axis_max = format_axis_time(window.span)
        marker_column = label_width + min(
            axis_width,
            int(round((window.now_offset / window.span) * axis_width)),
        )
        saved = app.save_screenshot(filename=svg_name, path=str(DOCS))
    lines.append(f"pane key: {target_key}")
    lines.append(f"pane index: {index}")
    lines.append(f"poll time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"render width: {width} cells, plot width: {plot_width} cells")
    lines.append(f"window span: {window.span:.1f}s, now offset: {window.now_offset:.1f}s")
    lines.append(f"axis max label: {axis_max}")
    lines.append(f"now marker column: {marker_column}")
    lines.append(f"axis: {axis}")
    lines.append("lanes (label, block offset start->end, cols):")
    for lane in lanes:
        for block in lane.blocks:
            lines.append(
                f"  {lane.label:10s}"
                f" [{block.offset_start:8.1f},{block.offset_end:8.1f}]"
                f" cols [{block.start_col},{block.end_col}]"
            )
    lines.append(f"screenshot: {Path(saved).relative_to(REPO_ROOT)}")

    ok = True
    if not lanes:
        lines.append("FAIL: no lanes")
        ok = False
    if window.span > 600.0 + 0.001:
        lines.append(f"FAIL: window span {window.span:.1f}s exceeds 600s")
        ok = False
    if axis_max not in axis:
        lines.append(f"FAIL: axis max {axis_max} is not on the axis")
        ok = False
    if marker_column >= len(axis) or axis[marker_column] != NOW_MARKER:
        lines.append("FAIL: no now marker at the current elapsed position")
        ok = False
    if any(
        block.start_col >= axis_width for lane in lanes for block in lane.blocks
    ):
        lines.append("FAIL: a block starts off the right edge")
        ok = False
    return ok, lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--svg", default="waterfall-live.svg")
    parser.add_argument("--target", default=str(REPO_ROOT))
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    lines: list[str] = [f"ITEM-49 waterfall window evidence: {args.out}", ""]
    unit_ok, unit_lines = check_1200s_timeline()
    lines.extend(unit_lines)
    lines.append("")
    live_ok = True
    if not args.skip_live:
        live_ok, live_lines = asyncio.run(live_read(args.target, args.svg))
        lines.extend(live_lines)

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if (unit_ok and live_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
