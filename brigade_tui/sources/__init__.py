"""Session sources and the default read-only set."""

from __future__ import annotations

from pathlib import Path

from .base import SessionSource, project_from_directory
from .claude import ClaudeSessions
from .opencode import OpencodeSessions
from .pi import PiSessions

__all__ = [
    "SessionSource",
    "project_from_directory",
    "PiSessions",
    "ClaudeSessions",
    "OpencodeSessions",
    "default_sources",
    "sources_from_roots",
]


def default_sources() -> list[SessionSource]:
    """Every supported source, whether or not its store exists."""
    return [PiSessions(), ClaudeSessions(), OpencodeSessions()]


def sources_from_roots(
    pi_root: str | Path | None = None,
    claude_root: str | Path | None = None,
    opencode_db: str | Path | None = None,
) -> list[SessionSource]:
    """Build the three sources from resolved roots.

    A root of None falls back to that source's own env var or default, so a
    caller that resolved nothing still gets the documented behavior. This is
    the one default source path the app uses; there is no second one.
    """
    return [
        PiSessions(pi_root),
        ClaudeSessions(claude_root),
        OpencodeSessions(opencode_db),
    ]
