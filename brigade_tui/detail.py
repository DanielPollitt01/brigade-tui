"""One session detail pane, opened from a card, a block or a search row.

The pane is read-only. It shows the session's title, harness, model, working
directory, start, last activity, state, token totals and cost, then a tail of
the transcript the session was read from. Nothing here writes to a store and
nothing launches an agent.

The transcript tail comes from ``transcript.read_transcript_tail``. Escape
closes the pane.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static

from .grid import format_elapsed
from .model import Usage, elapsed_seconds, session_label, session_state
from .transcript import (
    TAIL_LINES,
    TranscriptTail,
    read_transcript_tail,
)


def format_timestamp(when: datetime | None) -> str:
    """A UTC timestamp for the detail pane, or ``-`` when there is none."""
    if when is None:
        return "-"
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_tokens(usage: Usage) -> str:
    """The token line: the total, then the input and output split."""
    total = usage.total_tokens
    if total <= 0:
        return "-"
    return f"{total:,} (in {usage.input_tokens:,} / out {usage.output_tokens:,})"


def format_cost(cost: Decimal | None) -> str:
    """The cost line, or ``-`` when the source reports no cost."""
    if cost is None:
        return "-"
    return f"${cost:.4f}"


def metadata_lines(session, now: datetime) -> list[tuple[str, str]]:
    """The labelled fields the detail pane shows, in order."""
    state = session_state(session, now)
    return [
        ("Session", session.session_id),
        ("Title", session.title or "(untitled)"),
        ("Harness", session.harness.value),
        ("Model", session.model or "-"),
        ("Directory", session.directory or "-"),
        ("Start", format_timestamp(session.started_at)),
        ("Last activity", format_timestamp(session.last_activity)),
        ("Elapsed", format_elapsed(elapsed_seconds(session, now))),
        ("State", state.value),
        ("Tokens", format_tokens(session.usage)),
        ("Cost", format_cost(session.usage.cost)),
        ("Agent", session_label(session)),
    ]


def render_metadata(session, now: datetime) -> Text:
    """The metadata block as Rich text, one labelled field per line."""
    text = Text()
    width = max(len(label) for label, _ in metadata_lines(session, now))
    for index, (label, value) in enumerate(metadata_lines(session, now)):
        text.append(f"{label.ljust(width)}  ", style="bold")
        text.append(value)
        if index < len(metadata_lines(session, now)) - 1:
            text.append("\n")
    return text


def tail_lines(tail: TranscriptTail) -> list[str]:
    """One rendered line per transcript entry, oldest first."""
    lines: list[str] = []
    for entry in tail.entries:
        stamp = (
            entry.when.astimezone(timezone.utc).strftime("%H:%M:%S")
            if entry.when is not None
            else "--:--:--"
        )
        lines.append(f"{stamp}  {entry.role}: {entry.text}")
    return lines


def tail_heading(tail: TranscriptTail, limit: int = TAIL_LINES) -> str:
    """The heading above the tail: what it is and where it came from."""
    if tail.error:
        return f"Read-only transcript tail: unavailable ({tail.error})"
    if tail.total == 0:
        return f"Read-only transcript tail: no messages in {tail.source}"
    shown = len(tail.entries)
    prefix = "last " if tail.truncated else ""
    return (
        f"Read-only transcript tail: {prefix}{shown} of {tail.total} messages"
        f" from {tail.source}"
    )


class SessionDetailScreen(ModalScreen):
    """One session, read-only: metadata plus a transcript tail."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(
        self,
        session,
        now: datetime | None = None,
        tail: TranscriptTail | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.now = now if now is not None else datetime.now(timezone.utc)
        self.tail = (
            tail if tail is not None else read_transcript_tail(session)
        )

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Session detail (read-only)", id="detail-title"),
            Static(render_metadata(self.session, self.now), id="detail-meta"),
            Static(tail_heading(self.tail), id="detail-tail-heading"),
            RichLog(
                id="detail-tail",
                wrap=True,
                markup=False,
                highlight=False,
            ),
            id="detail-box",
        )

    def on_mount(self) -> None:
        self.sub_title = self.session.title or self.session.session_id
        log = self.query_one("#detail-tail", RichLog)
        lines = tail_lines(self.tail)
        if lines:
            log.write("\n".join(lines))
        else:
            log.write("(no transcript messages)")
        log.scroll_end(animate=False)
