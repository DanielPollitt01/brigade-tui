"""Fold session snapshots from every source into one read-only view."""

from __future__ import annotations

from collections import Counter, OrderedDict
from collections.abc import Iterable, Sequence
from pathlib import Path

from .health import SourceHealth
from .model import ProjectRef, Session, is_scratch
from .sources.base import SessionSource

#: Most project tabs shown at once. Tab 10 is the last direct jump key.
MAX_TABS = 10

#: Most rows shown inside one project tab.
MAX_ROWS_PER_TAB = 50


def _error_name(error: Exception) -> str:
    """A short, safe name for a source read failure."""
    return type(error).__name__ or "error"


class SessionStore:
    """Poll every source and group sessions by project, newest first."""

    def __init__(self, sources: Sequence[SessionSource]) -> None:
        self.sources = list(sources)

    def poll(self) -> tuple[list[Session], list[SourceHealth]]:
        """Read every source once and report both sessions and source health.

        A source that is missing is marked unavailable rather than skipped,
        so a silent adapter failure is visible. A source that raises is
        marked with its error. One pass feeds both the session view and the
        health strip, so a large transcript is not read twice per poll.
        """
        sessions: list[Session] = []
        health: list[SourceHealth] = []
        for source in self.sources:
            path = str(getattr(source, "store_path", "") or "")
            open_mode = str(getattr(source, "open_mode", "read"))
            harness = getattr(source.harness, "value", str(source.harness))
            try:
                available = source.exists()
            except Exception as error:
                health.append(
                    SourceHealth(
                        harness, path, open_mode, False, 0, _error_name(error)
                    )
                )
                continue
            if not available:
                health.append(SourceHealth(harness, path, open_mode, False, 0))
                continue
            try:
                found = source.snapshot()
            except Exception as error:
                health.append(
                    SourceHealth(
                        harness, path, open_mode, False, 0, _error_name(error)
                    )
                )
                continue
            sessions.extend(found)
            health.append(SourceHealth(harness, path, open_mode, True, len(found)))
        sessions.sort(key=lambda item: item.last_activity, reverse=True)
        return sessions, health

    def refresh(self) -> list[Session]:
        """Read every source and return the sessions. Health is discarded."""
        return self.poll()[0]

    def by_project(
        self,
        sessions: Iterable[Session] | None = None,
        *,
        include_scratch: bool = False,
    ) -> list[tuple[ProjectRef, list[Session]]]:
        """Group sessions by project, newest session first in each group.

        Projects are ordered by the newest session in each one, so the first
        project owns the first tab. A project whose newest session is older
        sorts lower and can fall outside the tab bar.

        Seed and scratch sessions stay out of the live tab view by default:
        their cwd is a throwaway temp dir, so they are debris, not a project.
        Pass ``include_scratch=True`` to keep them, for example to reach a
        hidden session in the search browser.
        """
        grouped: OrderedDict[str, tuple[ProjectRef, list[Session]]] = OrderedDict()
        for session in sessions if sessions is not None else self.refresh():
            if not include_scratch and is_scratch(session):
                continue
            project = session.project
            if project.key not in grouped:
                grouped[project.key] = (project, [])
            grouped[project.key][1].append(session)
        pairs = list(grouped.values())
        for _project, group in pairs:
            group.sort(key=lambda item: item.last_activity, reverse=True)
        pairs.sort(key=lambda item: item[1][0].last_activity, reverse=True)
        return pairs


def unique_labels(pairs: Sequence[tuple[ProjectRef, list[Session]]]) -> list[str]:
    """Return one short, unique tab label per project, in the same order.

    A label is the project directory's base name. When two visible projects
    share a base name, the label gains parent path segments until it is
    distinct. A last-resort numeric suffix keeps the result unique even when
    two projects have the same key.
    """
    items = [(project.key, project.label) for project, _ in pairs]
    counts = Counter(label for _, label in items)
    used: set[str] = set()
    assigned: dict[int, str] = {}

    for index, (_key, label) in enumerate(items):
        if counts[label] == 1:
            assigned[index] = label
            used.add(label)

    for index, (key, label) in enumerate(items):
        if index in assigned:
            continue
        parts = Path(key).parts or (key,)
        chosen: str | None = None
        for depth in range(2, len(parts) + 1):
            candidate = "/".join(parts[-depth:])
            if candidate not in used:
                chosen = candidate
                break
        if chosen is None:
            chosen = key if key not in used else label
        base = chosen
        suffix = 2
        while chosen in used:
            chosen = f"{base} ({suffix})"
            suffix += 1
        assigned[index] = chosen
        used.add(chosen)

    return [assigned[index] for index in range(len(items))]
