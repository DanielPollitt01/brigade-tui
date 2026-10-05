"""The harness-neutral session source interface and shared helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..model import Harness, ProjectRef, Session, Usage


@runtime_checkable
class SessionSource(Protocol):
    """A read-only source of sessions.

    ``snapshot`` returns every session the source can see right now. It must
    never write to the source store.
    """

    harness: Harness

    def exists(self) -> bool:
        """True when the source's store is present on this machine."""

    def snapshot(self) -> list[Session]:
        """Read every session, newest activity first."""


@lru_cache(maxsize=4096)
def resolve_repo_root(directory: str) -> str:
    """Return the git checkout a worktree belongs to, else ``directory``.

    A git worktree's root holds a ``.git`` file whose ``gitdir`` points at
    ``<owner>/.git/worktrees/<name>``. Such a session folds onto the owner, so
    one project owns one tab. A normal checkout (a ``.git`` directory) and a
    directory with no git marker are returned unchanged, so distinct projects
    stay distinct.
    """
    if not directory:
        return directory
    path = Path(directory)
    if not path.is_absolute():
        return directory
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.is_file():
            owner = _repo_root_from_gitfile(marker)
            return owner if owner else directory
        if marker.is_dir():
            return directory
    return directory


def _repo_root_from_gitfile(marker: Path) -> str | None:
    """Resolve the owning working tree from a worktree ``.git`` file.

    A git worktree holds a ``.git`` file that reads
    ``gitdir: <owner>/.git/worktrees/<name>``. The owner is everything before
    the ``.git`` segment. Any other shape returns None, so the caller keeps
    the session directory.
    """
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    raw = text.split(":", 1)[1].strip()
    gitdir = Path(raw)
    if not gitdir.is_absolute():
        gitdir = marker.parent / gitdir
    parts = gitdir.parts
    if ".git" not in parts:
        return None
    index = parts.index(".git")
    if index == 0 or index + 1 >= len(parts) or parts[index + 1] != "worktrees":
        return None
    return str(Path(*parts[:index]))


def project_from_directory(directory: str) -> ProjectRef:
    """Turn a working directory into a project key and short label.

    The key is the owning git checkout when the directory is a git worktree,
    so every worktree folds into the project it belongs to. The label stays
    the directory's base name.
    """
    if not directory or directory == "/":
        return ProjectRef(key=directory or "/", label="/")
    root = resolve_repo_root(directory)
    path = Path(root)
    try:
        if path == Path.home():
            return ProjectRef(key=str(path), label="~")
    except RuntimeError:
        pass
    if root == "/":
        return ProjectRef(key="/", label="/")
    return ProjectRef(key=str(path), label=path.name or str(path))


@dataclass(frozen=True)
class TranscriptMeta:
    """The metadata a full transcript read yields: usage and the run start.

    ``run_started_at`` is the start of the session's most recent run, or None
    when the transcript holds no timestamps.
    """

    usage: Usage = Usage()
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None


class TranscriptCache:
    """Read per-file usage and run start, and keep them while unchanged.

    A real store holds hundreds of megabytes of transcripts, and the TUI
    polls every few seconds. Recomputing every file on every poll is too
    slow, so a file whose size and mtime did not change keeps the metadata
    from its last read. Only a changed file is read again. The cache never
    writes to the file.
    """

    def __init__(self, reader: Callable[[Path], TranscriptMeta]) -> None:
        self._reader = reader
        self._entries: dict[Path, tuple[int, int, TranscriptMeta]] = {}

    def read(self, path: Path) -> TranscriptMeta:
        try:
            stat = path.stat()
        except OSError:
            return TranscriptMeta()
        cached = self._entries.get(path)
        if (
            cached is not None
            and cached[0] == stat.st_size
            and cached[1] == stat.st_mtime_ns
        ):
            return cached[2]
        meta = self._reader(path)
        self._entries[path] = (stat.st_size, stat.st_mtime_ns, meta)
        return meta


def file_mtime(path: Path) -> datetime:
    """Last modification time, as an aware UTC datetime."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def parse_iso(value: object) -> datetime | None:
    """Parse an ISO 8601 string into an aware datetime, or return None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def first_text(content: object) -> str | None:
    """Pull the first text block out of a message content value."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = block["text"]
                break
            if isinstance(block, str):
                text = block
                break
    else:
        return None
    text = " ".join(text.split())
    if not text:
        return None
    if text.startswith("<"):
        return None
    return text[:100]
