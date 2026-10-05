#!/usr/bin/env python3
"""Prove STW-49: a human waterfall axis, a time window and a now marker.

Reads the real pi, Claude Code and opencode stores, then drives the real
``BrigadeTUI`` at 140x45. For every visible project pane it checks:

  1. the axis is labelled for humans: ``m:ss`` elapsed time, never a raw
     seconds count like ``600s``
  2. the window bounds the view: the window never spans more than
     ``WINDOW_SECONDS``, and no block sits past the window edge; a project
     whose full history is longer is clipped to the last window
  3. the now marker is drawn at the current elapsed position: the marker
     glyph sits at the column the window places ``now`` on, on the axis row
     and on every lane row

It writes ``docs/waterfall-window-stw49.svg`` (and a PNG when
``rsvg-convert`` is present) from the real render, and prints a table.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-waterfall-window.py
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

from textual.widgets import TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.sources import default_sources
from brigade_tui.timeline import (
    LABEL_WIDTH,
    NOW_MARGIN_CELLS,
    NOW_MARKER,
    WINDOW_SECONDS,
    TimelineView,
    time_span,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
SVG_PATH = DOCS / "waterfall-window-stw49.svg"
PNG_PATH = DOCS / "waterfall-window-stw49.png"


def axis_width_for(timeline: TimelineView) -> tuple[int, int]:
    """The lane-label width and the plot columns the axis may use."""
    width = max(timeline.size.width or 80, 32)
    label_width = min(LABEL_WIDTH, max(10, width // 4))
    plot_width = max(8, width - label_width)
    return label_width, max(1, plot_width - NOW_MARGIN_CELLS)


def now_column(window, axis_width: int) -> int:
    """The column the current elapsed position maps to."""
    return max(0, min(axis_width, int(round((window.now_offset / window.span) * axis_width))))


async def drive() -> list[dict]:
    """Render the real TUI and report every visible project pane."""
    app = BrigadeTUI(sources=default_sources())
    report: list[dict] = []
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)
        for index, key in enumerate(app._pane_keys):
            content.active = panes[index].id
            await pilot.pause()
            timeline = app.query_one(f"#timeline-{index}", TimelineView)
            await pilot.pause()
            window = timeline.window
            label_width, axis_width = axis_width_for(timeline)
            marker_column = now_column(window, axis_width)
            lanes = timeline.lanes
            plain = timeline.render().plain
            lines = plain.splitlines()
            axis_line = lines[1] if len(lines) > 1 else ""
            guide_line = lines[2] if len(lines) > 2 else ""
            lane_lines = lines[3 : 3 + len(lanes)]
            full_origin, full_span = time_span(timeline._sessions)
            report.append(
                {
                    "key": key,
                    "index": index,
                    "sessions": len(timeline._sessions),
                    "full_span": full_span,
                    "window_span": window.span,
                    "now_offset": window.now_offset,
                    "axis_width": axis_width,
                    "label_width": label_width,
                    "marker_column": marker_column,
                    "axis_line": axis_line,
                    "guide_line": guide_line,
                    "lane_lines": lane_lines,
                    "lanes": lanes,
                    "plain": plain,
                    "width": timeline.size.width or 80,
                }
            )
        saved = app.save_screenshot(filename=SVG_PATH.name, path=str(DOCS))
        for row in report:
            row["screenshot"] = saved
    return report


def check_pane(row: dict, failures: list[str]) -> int:
    """Check one pane's axis, window and now marker. Return blocks checked."""
    key = row["key"]
    short = key.split("/")[-1] or key
    axis = row["axis_line"]
    marker_column = row["marker_column"]
    axis_width = row["axis_width"]
    # The marker glyph sits after the lane-label column.
    marker_at = row["label_width"] + marker_column

    # (1) Human axis labels.
    if ":" not in axis:
        failures.append(f"{short}: axis has no human m:ss label: {axis!r}")
    if "0:00" not in axis:
        failures.append(f"{short}: axis has no 0:00 start label: {axis!r}")
    if re.search(r"\b\d+s\b", axis):
        failures.append(f"{short}: axis still uses a raw seconds label: {axis!r}")

    # (2) The window bounds the view.
    if row["window_span"] > WINDOW_SECONDS + 0.001:
        failures.append(
            f"{short}: window span {row['window_span']} > {WINDOW_SECONDS}"
        )
    if abs(row["now_offset"] - row["window_span"]) > 0.001:
        failures.append(
            f"{short}: now offset {row['now_offset']} is not the window end"
        )
    blocks = 0
    for lane in row["lanes"]:
        for block in lane.blocks:
            blocks += 1
            if block.start_col < 0 or block.end_col > axis_width:
                failures.append(
                    f"{short}: block {block.session_id[:8]} cols"
                    f" [{block.start_col},{block.end_col}] past the window"
                    f" edge {axis_width}"
                )
            if block.offset_end <= 0 or block.offset_start >= row["window_span"]:
                failures.append(
                    f"{short}: block {block.session_id[:8]} sits outside the"
                    f" window offsets [{block.offset_start},{block.offset_end}]"
                )

    # (3) The now marker is drawn at the current elapsed position.
    if marker_at >= len(axis):
        failures.append(f"{short}: marker column {marker_at} is off the row")
    elif axis[marker_at] != NOW_MARKER:
        failures.append(
            f"{short}: axis has no now marker at column {marker_at}"
        )
    if row["guide_line"][marker_at : marker_at + 1] != NOW_MARKER:
        failures.append(f"{short}: guide row has no now marker")
    for line in row["lane_lines"]:
        if line[marker_at : marker_at + 1] != NOW_MARKER:
            failures.append(f"{short}: a lane row has no now marker")
    return blocks


def main() -> int:
    report = asyncio.run(drive())
    if not report:
        print("FAIL: no project panes rendered")
        return 1

    failures: list[str] = []
    total_blocks = 0
    for row in report:
        total_blocks += check_pane(row, failures)

    # The window must actually clip the longest real project on screen.
    longest = max(report, key=lambda row: row["full_span"])
    clipped = longest["full_span"] > WINDOW_SECONDS
    if clipped and longest["window_span"] > WINDOW_SECONDS + 0.001:
        failures.append(
            f"{longest['key']}: a {longest['full_span']:.0f}s project was not"
            " clipped to the window"
        )

    print("STW-49 waterfall window: real stores, 140x45")
    print(f"panes: {len(report)}  blocks checked: {total_blocks}")
    print(
        f"longest visible history: {longest['full_span']:.1f}s"
        f" -> window {longest['window_span']:.1f}s"
        f" ({'clipped' if clipped else 'within window'})"
    )
    print("")
    for row in report:
        short = (row["key"].split("/")[-1] or row["key"])[:34]
        print(
            f"{short:34s}"
            f" sessions={row['sessions']:3d}"
            f" full={row['full_span']:9.1f}s"
            f" window={row['window_span']:7.1f}s"
            f" now_col={row['marker_column']:3d}"
            f" lanes={len(row['lanes']):3d}"
        )
        print(f"{'':34s} axis: {row['axis_line'].strip()}")

    # A real project that is older than the window is the bound proof.
    sample = longest
    print("")
    print(f"clipped example: {sample['key']}")
    print(f"  full history span : {sample['full_span']:.1f}s")
    print(f"  window span       : {sample['window_span']:.1f}s")
    print(f"  window now offset : {sample['now_offset']:.1f}s")
    print(f"  axis              : {sample['axis_line'].strip()}")
    print(f"  marker column     : {sample['marker_column']} of {sample['axis_width']}")
    print(f"  screenshot        : {SVG_PATH.relative_to(REPO_ROOT)}")

    if failures:
        print("")
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("")
    print("PASS: human axis, bounded window, now marker at the current position")
    return 0


if __name__ == "__main__":
    code = main()
    if code == 0 and SVG_PATH.exists():
        try:
            subprocess.run(
                ["rsvg-convert", "-o", str(PNG_PATH), str(SVG_PATH)],
                check=True,
            )
            print(f"saved {SVG_PATH.relative_to(REPO_ROOT)}")
            print(f"saved {PNG_PATH.relative_to(REPO_ROOT)}")
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"FAIL: could not convert screenshot: {error}", file=sys.stderr)
            code = 1
    sys.exit(code)
