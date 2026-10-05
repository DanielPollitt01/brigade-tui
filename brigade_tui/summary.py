"""The factory summary line and the per-project progress signal.

The summary is the whole factory in one line: projects, running, idle,
blocked, done and spend today. It answers "is anything on fire" before the
eye moves on. The per-project progress signal is the same idea for one
project, shown in the tab bar, so a blocked project is visible from the tab
bar itself.

The counts come from the real session stores, through the model's
``session_state``. Nothing here invents a state and nothing here writes.

Blocked is never inferred from time. A project shows the blocked signal only
when a source reports a blocked session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from rich.text import Text
from textual.content import Content
from textual.markup import escape

from .model import (
    IDLE_WINDOW_SECONDS,
    RUNNING_WINDOW_SECONDS,
    ProjectRef,
    Session,
    SessionState,
    last_activity_at,
    session_state,
)
from .store import MAX_ROWS_PER_TAB, MAX_TABS

#: The states the summary and the progress signals count, in display order.
STATE_ORDER: tuple[SessionState, ...] = (
    SessionState.RUNNING,
    SessionState.IDLE,
    SessionState.BLOCKED,
    SessionState.COMPLETED,
)


@dataclass(frozen=True)
class FactorySummary:
    """The whole factory in one line."""

    projects: int
    running: int
    idle: int
    blocked: int
    done: int
    spend_today: Decimal

    def line(self) -> str:
        """The plain-text summary line, with no styling."""
        return (
            f"{self.projects} projects, "
            f"{self.running} running, "
            f"{self.idle} idle, "
            f"{self.blocked} blocked, "
            f"{self.done} done, "
            f"${self.spend_today:,.2f} today"
        )


@dataclass(frozen=True)
class ViewCaps:
    """What the live view shows, against what the stores hold.

    The tab bar caps the projects it shows and each tab caps its rows. This
    record carries both, so the view can state the caps out loud and a hidden
    project or row is never silently hidden.
    """

    projects_shown: int
    projects_total: int
    rows_shown: int
    rows_total: int
    tab_cap: int
    row_cap: int

    def line(self) -> str:
        """The plain-text caps line, with no styling."""
        return (
            f"{self.projects_shown} of {self.projects_total} projects"
            f" (tab cap {self.tab_cap})  \u00b7  "
            f"{self.rows_shown} of {self.rows_total} rows"
            f" (row cap {self.row_cap})"
        )


def build_view_caps(
    pairs: Sequence[tuple[ProjectRef, list[Session]]],
    visible_keys: Sequence[str],
) -> ViewCaps:
    """Count what the live view shows against what the stores hold.

    ``projects_shown`` is the number of project tabs in the bar, so it is
    capped at ``MAX_TABS``. ``rows_shown`` is the rows the tab tables render,
    each project capped at ``MAX_ROWS_PER_TAB``. ``projects_total`` and
    ``rows_total`` count every project and row in the snapshot, so a hidden
    project or row is counted and the difference is visible.
    """
    visible = set(visible_keys)
    projects_shown = 0
    rows_shown = 0
    rows_total = 0
    for project, group in pairs:
        rows_total += len(group)
        if project.key in visible:
            projects_shown += 1
            rows_shown += min(len(group), MAX_ROWS_PER_TAB)
    return ViewCaps(
        projects_shown=projects_shown,
        projects_total=len(pairs),
        rows_shown=rows_shown,
        rows_total=rows_total,
        tab_cap=MAX_TABS,
        row_cap=MAX_ROWS_PER_TAB,
    )


def render_view_caps(caps: ViewCaps) -> Text:
    """Render the caps line. The limits are dim, so the counts stay lead.

    Both numbers are stated out loud: projects shown against projects that
    exist, and rows shown against rows that exist. A hidden project or row
    is therefore never a silent loss.
    """
    text = Text()
    text.append(f"{caps.projects_shown} of {caps.projects_total} projects")
    text.append(f" (tab cap {caps.tab_cap})", style="dim")
    text.append("  ·  ")
    text.append(f"{caps.rows_shown} of {caps.rows_total} rows")
    text.append(f" (row cap {caps.row_cap})", style="dim")
    return text


@dataclass(frozen=True)
class ProjectProgress:
    """The progress of one project: how many sessions are in each state."""

    total: int
    running: int
    idle: int
    blocked: int
    done: int
    age_seconds: float

    @property
    def signal(self) -> str:
        """The compact progress signal, for example ``3/5``."""
        return f"{self.done}/{self.total}"


def _count_states(
    sessions: Sequence[Session],
    now: datetime,
    window: float,
    idle_window: float,
) -> dict[SessionState, int]:
    counts = {state: 0 for state in STATE_ORDER}
    for session in sessions:
        counts[session_state(session, now, window, idle_window)] += 1
    return counts


def is_today(when: datetime, now: datetime) -> bool:
    """True when ``when`` falls on ``now``'s UTC day."""
    return when.astimezone(timezone.utc).date() == now.astimezone(timezone.utc).date()


def build_summary(
    pairs: Sequence[tuple[ProjectRef, list[Session]]],
    now: datetime,
    window: float = RUNNING_WINDOW_SECONDS,
    idle_window: float = IDLE_WINDOW_SECONDS,
) -> FactorySummary:
    """Count every project and session in the snapshot for the summary line.

    ``projects`` is the number of project groups in the snapshot. ``running``,
    ``idle``, ``blocked`` and ``done`` count every session by its state.
    ``spend_today`` sums the reported cost of sessions whose last activity is
    today. A session that reports no cost adds nothing; no cost is invented.
    """
    sessions = [session for _project, group in pairs for session in group]
    counts = _count_states(sessions, now, window, idle_window)
    spend = Decimal(0)
    for session in sessions:
        cost = session.usage.cost
        if cost is not None and is_today(last_activity_at(session), now):
            spend += cost
    return FactorySummary(
        projects=len(pairs),
        running=counts[SessionState.RUNNING],
        idle=counts[SessionState.IDLE],
        blocked=counts[SessionState.BLOCKED],
        done=counts[SessionState.COMPLETED],
        spend_today=spend,
    )


def render_summary(summary: FactorySummary) -> Text:
    """Render the summary line. A non-zero blocked count is red.

    Red is the grid's blocked colour, so the one signal that needs the user
    carries the same colour here.
    """
    text = Text()
    text.append(f"{summary.projects} projects, ")
    text.append(f"{summary.running} running, ")
    text.append(f"{summary.idle} idle, ")
    blocked_style = "bold red" if summary.blocked else ""
    text.append(f"{summary.blocked} blocked", style=blocked_style)
    text.append(
        f", {summary.done} done, ${summary.spend_today:,.2f} today"
    )
    return text


def build_progress(
    sessions: Sequence[Session],
    now: datetime,
    window: float = RUNNING_WINDOW_SECONDS,
    idle_window: float = IDLE_WINDOW_SECONDS,
) -> ProjectProgress:
    """Count one project's sessions by state, and its newest activity age."""
    counts = _count_states(sessions, now, window, idle_window)
    newest = max((last_activity_at(session) for session in sessions), default=now)
    age = max(0.0, (now - newest).total_seconds())
    return ProjectProgress(
        total=len(sessions),
        running=counts[SessionState.RUNNING],
        idle=counts[SessionState.IDLE],
        blocked=counts[SessionState.BLOCKED],
        done=counts[SessionState.COMPLETED],
        age_seconds=age,
    )


def format_age(seconds: float) -> str:
    """A short freshness age: ``45s``, ``3m``, ``2h``, ``4d``."""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def progress_label(name: str, progress: ProjectProgress) -> Content:
    """One tab label: the project name, its progress, its freshness.

    The blocked count is shown only when it is non-zero, and it is red, so a
    project that needs the user is visible from the tab bar. The grid's
    reserved blocked colour is the same red. The name is escaped, so a path
    with markup brackets renders literally.
    """
    parts = [f"{escape(name)} {progress.signal} "]
    if progress.blocked:
        parts.append(f"[bold red]{progress.blocked} blocked[/bold red] ")
    parts.append(format_age(progress.age_seconds))
    return Content.from_markup("".join(parts))


def progress_labels(
    pairs: Sequence[tuple[ProjectRef, list[Session]]],
    labels: Sequence[str],
    now: datetime,
    window: float = RUNNING_WINDOW_SECONDS,
    idle_window: float = IDLE_WINDOW_SECONDS,
) -> list[Content]:
    """Build one progress tab label per project, in the same order.

    ``labels`` are the unique project names from ``store.unique_labels``. The
    progress signal is appended to each, so the name stays the leading part.
    """
    return [
        progress_label(name, build_progress(group, now, window, idle_window))
        for (_project, group), name in zip(pairs, labels)
    ]
