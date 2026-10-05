"""The grid main view: one state-coloured card per session.

The grid is the default view. Cards are grouped under a project heading, so
a card always names the project it belongs to. Each card shows three fields:

- the session title
- the current agent or role
- the elapsed running time

A card is coloured by state and by nothing else. The state colours are
reserved:

- red for blocked
- green for completed
- yellow for idle
- blue for running

The waterfall never uses these three colours and never shows a state word, so
state lives only in the grid.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from .model import (
    IDLE_WINDOW_SECONDS,
    RUNNING_WINDOW_SECONDS,
    Session,
    SessionState,
    elapsed_seconds,
    session_label,
    session_state,
)

#: The one colour per state. No other colour carries a state.
STATE_COLOURS: dict[SessionState, str] = {
    SessionState.BLOCKED: "red",
    SessionState.COMPLETED: "green",
    SessionState.IDLE: "yellow",
    SessionState.RUNNING: "blue",
}

#: Card geometry. One card is this many cells wide and lines tall.
CARD_WIDTH = 34
CARD_HEIGHT = 4

#: Text shown when a session reports no title.
UNTITLED = "(untitled)"

#: The grid has no list cursor. Keys that move a list must do nothing here,
#: and the banner says so, so no key ever acts on a hidden table.
READ_ONLY_BANNER = (
    "Grid: read-only. No cursor here. "
    "j / k do nothing. Press l, click a tab, or / to search."
)


@dataclass(frozen=True)
class Card:
    """One rendered session card and the fields a test can read back."""

    session_id: str
    title: str
    agent: str
    elapsed_seconds: float
    elapsed: str
    state: SessionState
    colour: str
    #: The owning project's short label. Cards are grouped under this heading.
    project: str = ""


def card_agent(session: Session) -> str:
    """The current agent or role shown on a card.

    A named role wins, then a source-reported agent, then a meaningful
    label: the project name plus a short title fragment or a readable id.
    Never the whole raw prompt title and never a bare truncated hash.
    """
    return session.role or session.agent or session_label(session)


def format_elapsed(seconds: float) -> str:
    """A short elapsed time: ``45s``, ``3m 20s``, ``2h 05m``."""
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours}h {minutes:02d}m"


def build_cards(
    sessions: list[Session],
    now: datetime,
    window: float | None = None,
    idle_window: float | None = None,
) -> list[Card]:
    """Build one card per session, newest activity first."""
    span = RUNNING_WINDOW_SECONDS if window is None else window
    idle_span = IDLE_WINDOW_SECONDS if idle_window is None else idle_window
    ordered = sorted(sessions, key=lambda item: item.last_activity, reverse=True)
    cards: list[Card] = []
    for session in ordered:
        state = session_state(session, now, span, idle_span)
        seconds = elapsed_seconds(session, now, span, idle_span)
        cards.append(
            Card(
                session_id=session.session_id,
                title=session.title.strip() if session.title else UNTITLED,
                agent=card_agent(session),
                elapsed_seconds=seconds,
                elapsed=format_elapsed(seconds),
                state=state,
                colour=STATE_COLOURS[state],
                project=session.project.label,
            )
        )
    return cards


def group_cards(cards: list[Card]) -> list[tuple[str, list[Card]]]:
    """Group cards by project heading, first project first.

    ``build_cards`` orders sessions newest first, so the first card a project
    contributes is its newest. Projects therefore stay ordered by their newest
    session, the same order the tab bar uses.
    """
    groups: OrderedDict[str, list[Card]] = OrderedDict()
    for card in cards:
        groups.setdefault(card.project, []).append(card)
    return list(groups.items())


def heading_text(project: str, count: int) -> str:
    """The heading line above one project's cards."""
    return f"▌ {project} ({count})"


def _fit(text: str, width: int) -> str:
    text = " ".join(text.split())
    if len(text) <= width:
        return text.ljust(width)
    if width <= 1:
        return text[:width].ljust(width)
    return (text[: width - 1] + "…").ljust(width)


def _card_lines(card: Card) -> list[str]:
    inner = CARD_WIDTH - 2
    return [
        "┌" + "─" * inner + "┐",
        "│" + _fit(card.title, inner) + "│",
        "│" + _fit(f"{card.agent}  {card.elapsed}", inner) + "│",
        "└" + "─" * inner + "┘",
    ]


def render_grid(
    cards: list[Card],
    width: int,
    click_action=None,
) -> Text:
    """Render cards grouped by project heading, wrapped to the width.

    ``click_action`` is an optional callable from a session id to a Textual
    action string. When it is set, a click on a card runs that action, so a
    card is one entry point into the session detail pane. When it is None,
    the grid renders exactly as before.
    """
    if not cards:
        return Text("no sessions", style="dim")
    width = max(width, CARD_WIDTH)
    columns = max(1, width // CARD_WIDTH)
    text = Text()
    for project, group in group_cards(cards):
        text.append(heading_text(project, len(group)), style="bold")
        text.append("\n")
        for row_start in range(0, len(group), columns):
            row = group[row_start : row_start + columns]
            blocks = [_card_lines(card) for card in row]
            for line in range(CARD_HEIGHT):
                for card, block in zip(row, blocks):
                    text.append(block[line], style=_card_style(card, click_action))
                    text.append(" ")
                text.append("\n")
    return text


def _card_style(card: Card, click_action) -> Style:
    """A card's colour, plus a click action when one is wired in."""
    style = Style.parse(card.colour)
    if click_action is None:
        return style
    action = click_action(card.session_id)
    if not action:
        return style
    return style + Style.from_meta({"@click": action})


class SessionGrid(Static):
    """The default main view: a grid of state-coloured session cards."""

    can_focus = True

    def __init__(
        self,
        sessions: list[Session] | None = None,
        now: datetime | None = None,
        window: float | None = None,
        idle_window: float | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sessions: list[Session] = list(sessions or [])
        self._now: datetime | None = now
        self._window = RUNNING_WINDOW_SECONDS if window is None else window
        self._idle_window = IDLE_WINDOW_SECONDS if idle_window is None else idle_window
        #: Optional callable from a session id to a Textual action string.
        #: The app sets it so a click on a card opens the detail pane.
        self.click_action = None

    def update_sessions(
        self,
        sessions: list[Session],
        now: datetime | None = None,
        window: float | None = None,
        idle_window: float | None = None,
    ) -> None:
        self._sessions = list(sessions)
        self._now = now
        if window is not None:
            self._window = window
        if idle_window is not None:
            self._idle_window = idle_window
        self.refresh()

    @property
    def now(self) -> datetime:
        if self._now is not None:
            return self._now
        from datetime import timezone

        return datetime.now(timezone.utc)

    def cards(self) -> list[Card]:
        """The cards this grid renders, with the fields a test reads back."""
        return build_cards(
            self._sessions, self.now, self._window, self._idle_window
        )

    def render(self) -> Text:
        text = Text()
        text.append(READ_ONLY_BANNER + "\n", style="dim")
        text.append_text(
            render_grid(
                self.cards(),
                self.size.width or 80,
                click_action=self.click_action,
            )
        )
        return text
