"""Tests for the real-time block timeline.

The tab body is a block timeline: the x-axis is a human ``m:ss`` scale over
the last time window ending at now, one lane per agent or role, and every
active interval is a block at its real start and size. A role that runs twice
shows two blocks. The now marker is drawn at the current elapsed position and
the window bounds what the view can show.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from brigade_tui.model import Harness, ProjectRef, Session
from brigade_tui.timeline import (
    NOW_MARKER,
    WINDOW_SECONDS,
    axis_ticks,
    build_lanes,
    build_window,
    format_axis_time,
    render_timeline,
    time_span,
)

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def sequential(durations: list[float]) -> list[Session]:
    """Sessions laid end to end. The span is ``sum(durations)`` seconds."""
    sessions: list[Session] = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        sessions.append(
            Session(
                harness=Harness.PI,
                session_id=f"session-{index:02d}",
                project=ProjectRef(key="alpha", label="alpha"),
                directory="alpha",
                last_activity=NOW + timedelta(seconds=cursor + duration),
                started_at=NOW + timedelta(seconds=cursor),
            )
        )
        cursor += duration
    return sessions


def test_axis_labels_are_human_minutes_and_seconds() -> None:
    """A tick is ``m:ss`` elapsed time, not a raw count of seconds."""
    ticks = axis_ticks(600.0, 117)
    labels = [label for _column, label in ticks]
    assert labels[0] == "0:00"
    assert labels[-1] == "10:00"
    assert all((":" in label) for label in labels)
    assert format_axis_time(90.0) == "1:30"
    assert format_axis_time(3661.0) == "1:01:01"


def test_axis_max_equals_the_real_elapsed_span() -> None:
    """A span that is not a round multiple is still the axis max."""
    ticks = axis_ticks(649.2, 118)
    assert ticks[-1] == (117, "10:49")
    assert ticks[-1][0] == 118 - 1


def test_origin_is_the_earliest_start_and_span_is_latest_end() -> None:
    """The full-data span still starts at the earliest session start."""
    sessions = [
        Session(
            harness=Harness.PI,
            session_id="late",
            project=ProjectRef(key="a", label="a"),
            directory="a",
            last_activity=NOW + timedelta(seconds=50),
            started_at=NOW + timedelta(seconds=20),
        ),
        Session(
            harness=Harness.PI,
            session_id="early",
            project=ProjectRef(key="a", label="a"),
            directory="a",
            last_activity=NOW + timedelta(seconds=30),
            started_at=NOW,
        ),
    ]
    origin, span = time_span(sessions)
    assert origin == NOW
    assert span == 50.0


def test_window_bounds_a_1200s_timeline() -> None:
    """A 1200s history is clipped to the last window; no block sits past it."""
    sessions = sequential([400.0, 500.0, 300.0])
    window = build_window(sessions)
    assert window.span == WINDOW_SECONDS == 600.0
    assert window.now_offset == 600.0

    lanes = build_lanes(sessions, plot_width=100)
    axis_width = 100 - 1  # one column held for the now marker
    visible = {block.session_id for lane in lanes for block in lane.blocks}
    # The first session ended 200s before the window opens, so it is dropped.
    assert "session-00" not in visible
    assert visible == {"session-01", "session-02"}
    for lane in lanes:
        for block in lane.blocks:
            assert 0 <= block.start_col < block.end_col <= axis_width
    assert max(block.end_col for lane in lanes for block in lane.blocks) == axis_width


def test_no_block_is_pushed_off_the_right_edge_by_minimum_bar_width() -> None:
    """Many tiny blocks with a one-cell minimum stay inside the plot."""
    sessions = sequential([10.0] * 60)  # 600s exactly, the window length
    lanes = build_lanes(sessions, plot_width=118)
    axis_width = 118 - 1
    blocks = [lane.blocks[0] for lane in lanes]
    assert len(blocks) == 60
    for block in blocks:
        assert 0 <= block.start_col < axis_width
        assert block.start_col < block.end_col <= axis_width
    assert max(block.end_col for block in blocks) == axis_width


def test_a_running_block_grows_between_two_live_polls() -> None:
    """A session whose last_activity advances draws a wider block next poll."""
    first = sequential([100.0, 130.0])
    first_lanes = build_lanes(first, plot_width=118)
    running_first = first_lanes[-1].blocks[0]

    second = sequential([100.0, 160.0])
    second_lanes = build_lanes(second, plot_width=118)
    running_second = second_lanes[-1].blocks[0]

    assert running_second.duration > running_first.duration
    width_first = running_first.end_col - running_first.start_col
    width_second = running_second.end_col - running_second.start_col
    assert width_second > width_first
    assert second_lanes[-1].blocks[0].end_col == 118 - 1

    # The axis max grows with the running session too, in human units.
    assert axis_ticks(160.0, 118)[-1][1] == "2:40"


def test_now_marker_is_drawn_at_the_current_elapsed_position() -> None:
    """The now marker sits at the current elapsed column on every row."""
    sessions = sequential([40.0, 55.0])
    now = NOW + timedelta(seconds=95)
    text = render_timeline(sessions, width=100, now=now)
    lines = text.plain.splitlines()
    # Caption, then axis, then the tick guide, then one row per lane.
    assert lines[0].strip() == "last 1:35  now 1:35"
    axis = lines[1]
    marker_column = len(axis) - 1
    assert axis[marker_column] == NOW_MARKER
    # The guide row and the two lane rows are full width; the legend is not.
    for lane_row in lines[2:5]:
        assert lane_row[marker_column] == NOW_MARKER


def test_no_activity_in_the_window_renders_an_empty_axis_with_now() -> None:
    """A project idle for over the window shows no lanes but keeps the axis."""
    sessions = sequential([40.0, 55.0])
    now = NOW + timedelta(hours=2)
    text = render_timeline(sessions, width=100, now=now)
    assert "no session activity in this window" in text.plain
    assert NOW_MARKER in text.plain.splitlines()[1]
