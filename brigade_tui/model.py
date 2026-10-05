"""Domain types for the Brigade read-only session view.

The model is harness-neutral. Each source parses its own storage into these
types, so nothing downstream sees a JSONL row or a SQLite column.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

#: Roles a session may name in its own name or title. A lane label is one of
#: these when the session names one, else a project plus a readable detail.
ROLES: tuple[str, ...] = ("planner", "builder", "reviewer")

#: Longest title fragment carried in a lane or card label. The fragment is a
#: readable phrase, never the whole raw prompt title.
LABEL_TITLE_CHARS = 40


def detect_role(*values: object) -> str | None:
    """Return a known role named in any value, else None.

    A role matches only as a whole word, so ``builder`` in a path does not
    fake a role but ``item-31-builder`` does.
    """
    for value in values:
        if not value:
            continue
        tokens = re.findall(r"[a-z]+", str(value).lower())
        for role in ROLES:
            if role in tokens:
                return role
    return None


#: Roots whose sessions are seed or scratch debris, not real projects. A
#: seed or scratch run sets the working directory to a throwaway temp dir,
#: so the session's cwd is the marker. Brigade hides by that marker, never by
#: guessing from a title or a name.
SCRATCH_ROOTS: tuple[str, ...] = ("/tmp", "/var/tmp", "/private/tmp")

#: Session id, project key or directory prefixes that mark a seed or scratch
#: run even outside a temp root. A harness can use these to mark one.
SCRATCH_PREFIXES: tuple[str, ...] = ("seed-", "scratch-")

#: A path under a git worktrees directory. A real git worktree folds to its
#: owning project (see ``sources.base.resolve_repo_root``), so its project key
#: is the owner. One that does not fold has no owner: it is debris, not a
#: project, and owns no tab.
WORKTREE_KEY_RE = re.compile(r"/worktrees/[^/]+(/|$)")


def _is_under(location: str, root: str) -> bool:
    """True when ``location`` is ``root`` or a path below it."""
    if not location:
        return False
    location = os.path.normpath(location)
    root = os.path.normpath(root)
    return location == root or location.startswith(root + os.sep)


def is_scratch(session: "Session") -> bool:
    """True when a session is seed or scratch debris, not a real project.

    A seed or scratch run sets the working directory to a throwaway temp dir.
    A session whose cwd, directory or project key sits under a temp root is
    that debris. A session id or project key that starts with a seed prefix is
    hidden too, so a run can mark one even outside a temp dir.

    A worktree that did not fold onto an owner keeps the worktree path as its
    project key. It is debris too, so it owns no tab.
    """
    location = session.directory or session.project.key
    roots = (*SCRATCH_ROOTS, tempfile.gettempdir())
    if any(_is_under(location, root) for root in roots):
        return True
    if session.project.key and WORKTREE_KEY_RE.search(session.project.key):
        return True
    markers = (session.session_id, session.project.key, session.directory)
    return any(marker.startswith(SCRATCH_PREFIXES) for marker in markers if marker)


class Harness(str, Enum):
    """A coding agent whose sessions Brigade can read."""

    PI = "pi"
    CLAUDE = "claude"
    OPENCODE = "opencode"


class SessionState(str, Enum):
    """How a session card is coloured. Colour is the only state signal.

    ``RUNNING`` means the last activity sits inside a short window.
    ``IDLE`` means it sits outside that window but inside the idle band: the
    run paused, or waits on the user, and is not done. ``COMPLETED`` means it
    sits outside the idle band. ``BLOCKED`` is never inferred from time: it is
    set only when a source reports it.
    """

    RUNNING = "running"
    IDLE = "idle"
    COMPLETED = "completed"
    BLOCKED = "blocked"


#: A session whose last activity is older than this many seconds is idle.
RUNNING_WINDOW_SECONDS = 120

#: A session whose last activity is older than this many seconds is completed.
#: Between the running window and this band the session is idle, so a run that
#: pauses for a few minutes is not shown as done.
IDLE_WINDOW_SECONDS = 600

#: A gap longer than this ends a run. The run span counts from the first
#: activity after the last long gap, so a resumed transcript shows the resumed
#: run, not the age of the session.
RUN_GAP_SECONDS = 600


#: Source-reported state values that map to ``SessionState.BLOCKED``.
BLOCKED_REPORTS: frozenset[str] = frozenset({"blocked", "blocked_on_permission"})


@dataclass(frozen=True)
class Usage:
    """Token and spend totals for one session.

    Cost is a Decimal or None. None means the source did not report a cost.
    Never invent one.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: Decimal | None = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass(frozen=True)
class ProjectRef:
    """A project, identified by directory. The key is unique, the label is not."""

    key: str
    label: str


@dataclass(frozen=True)
class Session:
    """One session, normalized across harnesses."""

    harness: Harness
    session_id: str
    project: ProjectRef
    directory: str
    last_activity: datetime
    title: str | None = None
    model: str | None = None
    usage: Usage = Usage()
    started_at: datetime | None = None
    #: The start of the session's most recent run, found from the transcript's
    #: activity timestamps. None means the source holds no timeline, so the
    #: run span falls back to ``started_at``.
    run_started_at: datetime | None = None
    #: The last activity a source reports, when it holds a real timeline. The
    #: file mtime can be bumped by a copy or a backup long after the last
    #: record, so the run span and the state use this when it is present.
    run_ended_at: datetime | None = None
    role: str | None = None
    agent: str | None = None
    #: A state a source reports directly, such as ``blocked``. None means the
    #: source reports no state, so the state is inferred from activity time.
    reported_state: str | None = None
    #: The transcript this session was read from. A JSONL file for pi and
    #: Claude Code, the opencode SQLite database for opencode. None means the
    #: source holds no transcript to tail.
    transcript_path: str | None = None


def last_activity_at(session: Session) -> datetime:
    """The session's real last activity.

    A transcript source reports the last record's timestamp, so a bumped file
    mtime never counts as activity. A source with no timeline falls back to
    its reported ``last_activity``.
    """
    return session.run_ended_at or session.last_activity


def session_state(
    session: Session,
    now: datetime,
    window: float = RUNNING_WINDOW_SECONDS,
    idle_window: float = IDLE_WINDOW_SECONDS,
) -> SessionState:
    """The state of one session: blocked, running, idle or completed.

    A source-reported block wins. Otherwise the state is running when the
    last activity sits inside ``window`` seconds of ``now``, idle when it sits
    inside ``idle_window`` seconds, and completed when it sits outside both.
    No other input sets a state.
    """
    if session.reported_state in BLOCKED_REPORTS:
        return SessionState.BLOCKED
    idle = (now - last_activity_at(session)).total_seconds()
    if idle <= window:
        return SessionState.RUNNING
    if idle <= idle_window:
        return SessionState.IDLE
    return SessionState.COMPLETED


def current_run_start(
    times: Iterable[datetime],
    gap: float = RUN_GAP_SECONDS,
) -> datetime | None:
    """Start of the most recent run, from a transcript's activity times.

    Walk the times in order and cut at the last gap longer than ``gap``. The
    run starts at the first time after that gap, so a session resumed today
    after a gap overnight counts from the resume, not from the day it was
    created. None means the source held no timeline.
    """
    clean = [time for time in times if time is not None]
    if not clean:
        return None
    start = clean[0]
    for previous, current in zip(clean, clean[1:]):
        if (current - previous).total_seconds() > gap:
            start = current
    return start


def elapsed_seconds(
    session: Session,
    now: datetime,
    window: float = RUNNING_WINDOW_SECONDS,
    idle_window: float = IDLE_WINDOW_SECONDS,
) -> float:
    """The real run span on the card, in seconds.

    The span runs from the start of the session's current run to its last
    activity, never from the session's birth. A long lived transcript that was
    resumed shows the resumed run, not its age. A running or blocked session
    counts up to ``now`` so the card keeps moving while it works. An idle or
    completed session holds the run span it reached, so a finished card stops.
    """
    start = session.run_started_at or session.started_at or session.last_activity
    state = session_state(session, now, window, idle_window)
    if state in (SessionState.RUNNING, SessionState.BLOCKED):
        end = max(now, last_activity_at(session))
    else:
        end = last_activity_at(session)
    return max(0.0, (end - start).total_seconds())


#: A label that is only a truncated hash tells the user nothing. The R11
#: acceptance bar is that no visible label matches this shape.
BARE_HASH_RE = re.compile(r"^[0-9a-f]{8}$")


def is_bare_hash(label: str) -> bool:
    """True when a label is only a bare eight-character hash."""
    return bool(BARE_HASH_RE.match(label.strip()))


def _title_fragment(title: str | None, limit: int = LABEL_TITLE_CHARS) -> str:
    """The first readable words of a title, or an empty string.

    Whitespace is collapsed, so a prompt that opens with a newline or runs
    across lines still gives one clean phrase. The fragment is cut to
    ``limit`` characters so a label stays short.
    """
    if not title:
        return ""
    fragment = " ".join(title.split())
    if len(fragment) > limit:
        fragment = fragment[:limit].rstrip()
    return fragment


def _readable_id(session: "Session") -> str:
    """A short session id that names its harness, not a bare hash.

    A bare eight-character hash tells the user nothing. The harness prefix
    reads as ``pi:01a0b3e8``, so even a session with no role and no title is
    identifiable and is never a bare truncated hash.
    """
    short = session.session_id[:8]
    return f"{session.harness.value}:{short}" if short else session.harness.value


def session_label(session: Session) -> str:
    """A lane or card label.

    The role wins when the session names one (planner, builder, reviewer).
    Otherwise the label is the project name plus a short title fragment, and
    when the session has no title, the project name plus a readable id. Never
    a bare truncated hash and never the whole raw prompt title.
    """
    if session.role:
        return session.role
    name = session.project.label or session.project.key or "-"
    detail = _title_fragment(session.title) or _readable_id(session)
    return f"{name}: {detail}"
