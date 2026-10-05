"""The block timeline that is a project tab's default body.

One horizontal lane per agent or role. The lane label sits on the left: the
role when the session names one (planner, builder, reviewer), else the project
name plus a short title fragment or a readable id. Never the whole raw prompt
title and never a bare truncated hash.

The x-axis is real elapsed seconds from the earliest session start in the tab.
Every active interval is its own block, placed at its real start and sized by
its real duration. A role that runs more than once, such as a builder that runs
again after reviewer feedback, gets a second block later in the same lane.
Blocks in different lanes overlap only when the sessions really overlapped.

A block's colour is chosen by the session's model string, not the harness. Two
sessions on the same model share a colour; different models differ. The legend
names the models in use. No colour or word marks a state. The flat table is
still there, behind the ``t`` key.
"""

from __future__ import annotations

import colorsys
import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from .model import Harness, Session, session_label

#: The colour used when a session reports no model.
UNKNOWN_MODEL_COLOUR = "grey62"

#: The model name shown for a session that reports no model.
UNKNOWN_MODEL = "(no model)"

#: Preferred width of the lane label column.
LABEL_WIDTH = 22

#: A block is at least one cell wide, so a zero-length session still shows.
MIN_BAR_CELLS = 1

#: The waterfall shows at most this many seconds, ending at now. The view is
#: bounded: a project with a longer history is clipped to this window.
WINDOW_SECONDS = 600.0

#: Columns held at the right of the plot for the now marker, so the marker is
#: a visible line at the current elapsed position, not glued to the last tick.
NOW_MARGIN_CELLS = 1

#: The glyph drawn at the current elapsed position on every row.
NOW_MARKER = "┃"


@dataclass(frozen=True)
class Block:
    """One active interval, in real time and in grid columns.

    ``offset_start`` and ``offset_end`` are real seconds from the tab's
    earliest session start, so a block sits at its real position on the shared
    axis. A block belongs to exactly one session.
    """

    session_id: str
    harness: Harness
    model: str | None
    start: datetime
    end: datetime
    duration: float
    offset_start: float
    offset_end: float
    start_col: int
    end_col: int


@dataclass(frozen=True)
class Lane:
    """One agent or role. Its blocks are the runs of that agent or role."""

    key: str
    label: str
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class TimeWindow:
    """The slice of real time the waterfall shows.

    ``start`` and ``end`` are the window bounds, ``span`` is its length in
    seconds, and ``now_offset`` is the current time as real seconds from
    ``start``. A block is placed by its offset from ``start``, so the now
    marker and the blocks share one origin.
    """

    start: datetime
    end: datetime
    span: float
    now_offset: float


def display_model(model: str | None) -> str:
    """The legend name for a model, with a name for the no-model case."""
    return model if model else UNKNOWN_MODEL


def _model_index(name: str) -> int:
    """A stable integer for a model name. Python's own hash() is salted."""
    return int.from_bytes(hashlib.sha1(name.encode("utf-8")).digest()[:4], "big")


def _palette_colour(index: int) -> str:
    """A well-spaced colour for a palette index.

    Consecutive indexes land about 137 degrees apart in hue and step through
    three value bands, so a collision nudge still gives a visibly different
    colour. The hue is confined to three bands that avoid red, green and
    blue, because those three colours are reserved for session state and no
    other view may carry state.
    """
    raw = (index * 0.618033988749895) % 1.0
    # Three allowed hue bands, each away from a state hue: amber, cyan and
    # violet. The band keeps the model colour clear of a state colour.
    band = int(raw * 3) % 3
    within = (raw * 3) % 1.0
    hue = (0.10, 0.44, 0.76)[band] + within * 0.12
    value = (0.95, 0.78, 0.62)[(index // 4) % 3]
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.68, value)
    return f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"


def assign_model_colours(models: Iterable[str | None]) -> dict[str, str]:
    """Map each distinct model name to a colour, deterministically.

    A colour depends only on the model name, so the same model keeps the same
    colour across every tab and every poll. Within one set the colours are
    forced distinct, so two different models never share a colour.
    """
    ordered = sorted({display_model(model) for model in models})
    colours: dict[str, str] = {}
    used: set[str] = set()
    for name in ordered:
        base = _model_index(name)
        attempt = 0
        colour = _palette_colour(base + attempt)
        while colour in used:
            attempt += 1
            colour = _palette_colour(base + attempt)
        colours[name] = colour
        used.add(colour)
    return colours


def nice_step(span: float, plot_width: int) -> int:
    """A round tick step in seconds that fits roughly one label per 10 cells."""
    max_ticks = max(1, plot_width // 10)
    raw = span / max_ticks
    magnitude = 10 ** int(math.floor(math.log10(raw))) if raw > 0 else 1
    for multiple in (1, 2, 5, 10):
        candidate = magnitude * multiple
        if candidate >= raw:
            return max(1, int(candidate))
    return max(1, int(magnitude * 10))


def axis_ticks(span: float, plot_width: int) -> list[tuple[int, str]]:
    """Tick column and label pairs across the plot area.

    The last tick always sits at the right edge and names the real elapsed
    span, so the axis max equals the real span even when it is not a multiple
    of the round step. Round ticks stop before the span so the two labels never
    collide.
    """
    if span <= 0:
        span = 1.0
    step = nice_step(span, plot_width)
    ticks: list[tuple[int, str]] = []
    value = 0.0
    while value < span:
        column = int(round((value / span) * plot_width))
        column = max(0, min(plot_width - 1, column))
        if not ticks or column > ticks[-1][0]:
            ticks.append((column, format_axis_time(value)))
        value += step

    # The axis max is the real elapsed span, not the last round step below it.
    # Drop any round tick that would collide with the total label, then place
    # the span at the right edge. The renderer right-aligns an overflowing
    # label, so the span needs room for its full width here.
    final_col = plot_width - 1
    final_label = format_axis_time(span)
    while ticks and ticks[-1][0] + len(ticks[-1][1]) > final_col - len(final_label):
        ticks.pop()
    ticks.append((final_col, final_label))
    return ticks


def ordered_sessions(sessions: list[Session]) -> list[Session]:
    """Sessions in real start order, then by session id."""
    return sorted(
        sessions,
        key=lambda session: (
            session.started_at or session.last_activity,
            session.session_id,
        ),
    )


def time_span(sessions: list[Session]) -> tuple[datetime, float]:
    """The tab's origin and real elapsed span, in seconds.

    The origin is the earliest session start in the tab. The span is the
    latest end minus that origin. Both the axis and every block offset are
    measured from the same origin, so positions are real elapsed seconds.
    """
    starts = [session.started_at or session.last_activity for session in sessions]
    origin = min(starts)
    end = max(session.last_activity for session in sessions)
    span = (end - origin).total_seconds()
    return origin, (span if span > 0 else 1.0)


def _axis_width(plot_width: int) -> int:
    """The columns the time axis may use, leaving room for the now marker."""
    return max(1, plot_width - NOW_MARGIN_CELLS)


def build_window(
    sessions: list[Session],
    now: datetime | None = None,
    window_seconds: float = WINDOW_SECONDS,
) -> TimeWindow:
    """The time window the waterfall shows, ending at ``now``.

    ``now`` defaults to the latest activity in the set, so a caller without a
    wall clock sees the tail of the data. The window never spans more than
    ``window_seconds``: a longer project is clipped to the last window. The
    window end is ``now``, so the now marker sits at the current position and
    the view bounds the activity it can show.
    """
    if now is None:
        now = (
            max(session.last_activity for session in sessions)
            if sessions
            else datetime.now(timezone.utc)
        )
    starts = [session.started_at or session.last_activity for session in sessions]
    earliest = min(starts) if starts else now
    span = (now - earliest).total_seconds()
    if span > window_seconds:
        start = now - timedelta(seconds=window_seconds)
        span = window_seconds
    else:
        start = earliest
        span = max(span, 1.0)
    now_offset = max(0.0, min((now - start).total_seconds(), span))
    return TimeWindow(start=start, end=now, span=span, now_offset=now_offset)


def _scale_column(seconds: float, span: float, plot_width: int) -> int:
    """Place a real elapsed second on the grid, clamped to the plot."""
    column = int(round((seconds / span) * plot_width))
    return max(0, min(plot_width, column))


def _lane_key(session: Session) -> str:
    """A role groups its runs into one lane; a session with no role is alone."""
    return session.role if session.role else session.session_id


def _build_block(
    session: Session, window: TimeWindow, axis_width: int
) -> Block | None:
    """One session's block inside the window, or None when fully outside.

    A block that starts before the window is clipped to the left edge; a
    block that ends after the window is clipped to the right edge. A block
    wholly before or after the window is dropped, so the window bounds the
    view. The real start and end are kept, so the offset still maps back to
    the source timestamps.
    """
    start = session.started_at or session.last_activity
    end = session.last_activity
    duration = (end - start).total_seconds()
    if duration < 0:
        duration = 0.0
    offset_start = (start - window.start).total_seconds()
    offset_end = max((end - window.start).total_seconds(), offset_start)
    if offset_end <= 0 or offset_start >= window.span:
        return None
    start_col = _scale_column(max(0.0, offset_start), window.span, axis_width)
    end_col = _scale_column(min(window.span, offset_end), window.span, axis_width)
    if end_col - start_col < MIN_BAR_CELLS:
        end_col = min(axis_width, start_col + MIN_BAR_CELLS)
        start_col = max(0, end_col - MIN_BAR_CELLS)
    return Block(
        session_id=session.session_id,
        harness=session.harness,
        model=session.model,
        start=start,
        end=end,
        duration=duration,
        offset_start=offset_start,
        offset_end=offset_end,
        start_col=start_col,
        end_col=end_col,
    )


def build_lanes(
    sessions: list[Session],
    plot_width: int,
    now: datetime | None = None,
    window_seconds: float = WINDOW_SECONDS,
) -> list[Lane]:
    """Group real sessions into lanes of real-time blocks.

    One lane per role, or per session when it names no role. Every session is a
    block at its real offset from the window start, so a role that runs twice
    shows two blocks at different x positions. Blocks outside the window are
    dropped; the window bounds what is shown. Lanes are ordered by the earliest
    visible block start, so the first lane starts near the left edge.
    """
    if not sessions:
        return []
    window = build_window(sessions, now, window_seconds)
    axis_width = _axis_width(plot_width)
    ordered = ordered_sessions(sessions)

    groups: dict[str, list[Session]] = {}
    for session in ordered:
        groups.setdefault(_lane_key(session), []).append(session)

    lanes: list[Lane] = []
    for key, group in groups.items():
        blocks = tuple(
            block
            for session in group
            if (block := _build_block(session, window, axis_width)) is not None
        )
        if not blocks:
            continue
        lanes.append(Lane(key=key, label=session_label(group[0]), blocks=blocks))
    lanes.sort(key=lambda lane: (lane.blocks[0].offset_start, lane.key))
    return lanes


def format_seconds(seconds: float) -> str:
    """A short duration in seconds. One decimal below 1000, none above."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 1000:
        return f"{seconds:.1f}s"
    return f"{seconds:.0f}s"


def format_axis_time(seconds: float) -> str:
    """A human tick label: ``m:ss`` below an hour, ``h:mm:ss`` above.

    The axis carries elapsed time, not a date. A worker reads ``2:00`` as two
    minutes in, not as two o'clock, and the caption names the window in the
    same units.
    """
    total = int(round(max(0.0, seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fit_label(label: str, width: int) -> str:
    if len(label) <= width:
        return label.ljust(width)
    if width <= 1:
        return label[:width]
    return label[: width - 1] + "…"


def _models_in_use(sessions: list[Session]) -> list[str]:
    return sorted({display_model(session.model) for session in sessions})


def _block_colour(colours: dict[str, str], model: str | None) -> str:
    return colours.get(
        display_model(model),
        UNKNOWN_MODEL_COLOUR if not model else "white",
    )


def _block_style(colours: dict[str, str], block: Block, click_action) -> Style:
    """A block's model colour, plus a click action when one is wired in."""
    style = Style.parse(_block_colour(colours, block.model))
    if click_action is None:
        return style
    action = click_action(block.session_id)
    if not action:
        return style
    return style + Style.from_meta({"@click": action})


def _fill_block_styles(
    styles: list[Style | str],
    block: Block,
    colours: dict[str, str],
    click_action,
) -> list[Style | str]:
    """Mark every visible block column with the block's own style."""
    style = _block_style(colours, block, click_action)
    last = min(block.end_col, len(styles))
    for column in range(block.start_col, last):
        styles[column] = style
    return styles


def render_timeline(
    sessions: list[Session],
    width: int,
    model_colours: dict[str, str] | None = None,
    now: datetime | None = None,
    window_seconds: float = WINDOW_SECONDS,
    click_action=None,
) -> Text:
    """Render the whole tab body as a Rich ``Text`` block.

    The axis is human ``m:ss`` labels over the last ``WINDOW_SECONDS`` ending
    at ``now``. A now marker is drawn at the current elapsed position. A block
    outside the window is clipped or dropped, so the window bounds the view.

    ``click_action`` is an optional callable from a session id to a Textual
    action string. When it is set, a click on a block runs that action, so a
    block is one entry point into the session detail pane.
    """
    width = max(width, 32)
    label_width = min(LABEL_WIDTH, max(10, width // 4))
    plot_width = max(8, width - label_width)
    axis_width = _axis_width(plot_width)
    text = Text()

    if not sessions:
        return Text("no sessions", style="dim")

    colours = model_colours if model_colours is not None else assign_model_colours(
        session.model for session in sessions
    )
    window = build_window(sessions, now, window_seconds)
    ticks = axis_ticks(window.span, axis_width)
    lanes = build_lanes(sessions, plot_width, now, window_seconds)
    now_col = _scale_column(window.now_offset, window.span, axis_width)

    def emit(cells: list[str], styles: list[str], label: str = "") -> None:
        text.append(_fit_label(label, label_width), style="dim")
        for column, char in enumerate(cells):
            if column == now_col:
                text.append(NOW_MARKER, style="bold white")
            else:
                text.append(char, style=styles[column])
        text.append("\n")

    # Caption: the window in force and where now sits inside it.
    text.append(_fit_label("", label_width), style="dim")
    text.append(
        f"last {format_axis_time(window.span)}"
        f"  now {format_axis_time(window.now_offset)}",
        style="dim",
    )
    text.append("\n")

    # Time axis across the top. Human labels, the last naming the window span.
    axis = [" "] * plot_width
    axis_styles = ["dim"] * plot_width
    for column, label in ticks:
        start = column
        if start + len(label) > axis_width:
            start = max(0, axis_width - len(label))
        for offset, char in enumerate(label):
            position = start + offset
            if position < axis_width and axis[position] == " ":
                axis[position] = char
    emit(axis, axis_styles)

    # Tick guide row.
    marks = [" "] * plot_width
    mark_styles = ["dim"] * plot_width
    for column, _label in ticks:
        marks[column] = "┆"
    emit(marks, mark_styles)

    # One lane per agent or role. Every block sits at its real time.
    for lane in lanes:
        cells = [" "] * plot_width
        styles: list[Style | str] = ["dim"] * plot_width
        for column, _label in ticks:
            cells[column] = "┆"
        for block in lane.blocks:
            styles = _fill_block_styles(styles, block, colours, click_action)
            last = min(block.end_col, axis_width)
            for column in range(block.start_col, last):
                cells[column] = "█"
        for block in lane.blocks:
            duration = format_seconds(block.duration)
            last = min(block.end_col, axis_width)
            bar_width = last - block.start_col
            if bar_width >= len(duration) + 2:
                centre = block.start_col + (bar_width - len(duration)) // 2
                for offset, char in enumerate(duration):
                    position = centre + offset
                    if block.start_col <= position < last:
                        cells[position] = char
        emit(cells, styles, lane.label)

    if not lanes:
        text.append(_fit_label("", label_width), style="dim")
        text.append("no session activity in this window", style="dim")
        text.append("\n")

    # Legend: each model in use and its colour. Never a state word.
    text.append("\n")
    for model in _models_in_use(sessions):
        colour = colours.get(model, UNKNOWN_MODEL_COLOUR)
        text.append("  ")
        text.append("■", style=colour)
        text.append(f" {model}", style="dim")
    return text


class TimelineView(Static):
    """A project tab body that draws the real-time block timeline."""

    def __init__(
        self,
        sessions: list[Session] | None = None,
        model_colours: dict[str, str] | None = None,
        now: datetime | None = None,
        window_seconds: float = WINDOW_SECONDS,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sessions: list[Session] = list(sessions or [])
        self._model_colours: dict[str, str] = dict(model_colours or {})
        self._now: datetime | None = now
        self._window_seconds = window_seconds
        #: Optional callable from a session id to a Textual action string.
        #: The app sets it so a click on a block opens the detail pane.
        self.click_action = None

    def update_sessions(
        self,
        sessions: list[Session],
        model_colours: dict[str, str] | None = None,
        now: datetime | None = None,
        window_seconds: float | None = None,
    ) -> None:
        self._sessions = list(sessions)
        if window_seconds is not None:
            self._window_seconds = window_seconds
        if model_colours is not None:
            self._model_colours = dict(model_colours)
        else:
            self._model_colours = assign_model_colours(
                session.model for session in self._sessions
            )
        if now is not None:
            self._now = now
        self.refresh()

    @property
    def model_colours(self) -> dict[str, str]:
        if self._model_colours:
            return dict(self._model_colours)
        return assign_model_colours(session.model for session in self._sessions)

    @property
    def window(self) -> TimeWindow:
        """The time window the waterfall shows at this poll."""
        return build_window(self._sessions, self._now, self._window_seconds)

    @property
    def lanes(self) -> list[Lane]:
        """The rendered lane geometry, recomputed at the current width."""
        if not self._sessions:
            return []
        width = max(self.size.width or 80, 32)
        label_width = min(LABEL_WIDTH, max(10, width // 4))
        plot_width = max(8, width - label_width)
        return build_lanes(
            self._sessions, plot_width, self._now, self._window_seconds
        )

    def render(self) -> Text:
        width = self.size.width or 80
        return render_timeline(
            self._sessions,
            width,
            self.model_colours,
            self._now,
            self._window_seconds,
            click_action=self.click_action,
        )
