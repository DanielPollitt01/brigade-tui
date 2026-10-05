"""Read Claude Code session transcripts. Read-only.

Claude Code writes one JSONL file per session under
``~/.claude/projects/<cwd-slug>/``. Records carry ``sessionId`` and ``cwd``.
Assistant records carry the ``model``. The file's mtime is the last activity.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..model import Harness, Session, Usage, current_run_start
from .base import (
    TranscriptCache,
    TranscriptMeta,
    file_mtime,
    first_text,
    parse_iso,
    project_from_directory,
)

_SCAN_LINES = 300


def default_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects")).expanduser()


class ClaudeSessions:
    """Read Claude Code sessions from a directory tree of JSONL files."""

    harness = Harness.CLAUDE

    #: How the store is opened. Claude transcripts are read as plain files.
    open_mode = "read"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root is not None else default_root()
        self._usage = TranscriptCache(_read_usage)

    @property
    def store_path(self) -> str:
        return str(self.root)

    def exists(self) -> bool:
        return self.root.is_dir()

    def snapshot(self) -> list[Session]:
        if not self.exists():
            return []
        sessions: list[Session] = []
        for path in self.root.glob("*/*.jsonl"):
            session = self._read(path)
            if session is not None:
                sessions.append(session)
        sessions.sort(key=lambda item: item.last_activity, reverse=True)
        return sessions

    def _read(self, path: Path) -> Session | None:
        session_id = ""
        cwd = ""
        started = None
        model: str | None = None
        title: str | None = None
        try:
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= _SCAN_LINES:
                        break
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not session_id and isinstance(record.get("sessionId"), str):
                        session_id = record["sessionId"]
                    if not cwd and isinstance(record.get("cwd"), str):
                        cwd = record["cwd"]
                    if started is None and record.get("timestamp"):
                        started = parse_iso(record.get("timestamp"))
                    kind = record.get("type")
                    if kind == "assistant" and model is None:
                        message = record.get("message")
                        if isinstance(message, dict) and message.get("model"):
                            model = str(message["model"])
                    elif kind == "user" and title is None:
                        message = record.get("message")
                        if isinstance(message, dict):
                            title = first_text(message.get("content"))
        except OSError:
            return None
        if not session_id:
            session_id = path.stem
        meta = self._usage.read(path)
        return Session(
            harness=self.harness,
            session_id=session_id,
            project=project_from_directory(cwd),
            directory=cwd,
            last_activity=file_mtime(path),
            title=title,
            model=model,
            usage=meta.usage,
            started_at=started,
            run_started_at=meta.run_started_at,
            run_ended_at=meta.run_ended_at,
            transcript_path=str(path),
        )


def _read_usage(path: Path) -> TranscriptMeta:
    """Read token totals and the start of the most recent run.

    Claude Code writes several snapshots of one assistant reply to the
    transcript as content blocks finalize, and every snapshot repeats the
    same ``message.usage``. Summing every line would overcount by roughly
    three times, so keep the first record per ``message.id`` and sum each id
    once. The transcript carries tokens, and no cost, so ``cost`` stays None.
    Every record also carries a timestamp, so the same pass finds the run
    start.
    """
    input_tokens = output_tokens = cache_read = cache_write = 0
    seen: set[str] = set()
    times: list[datetime] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = parse_iso(record.get("timestamp"))
                if timestamp is not None:
                    times.append(timestamp)
                if record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if isinstance(message_id, str):
                    if message_id in seen:
                        continue
                    seen.add(message_id)
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                input_tokens += _int(usage.get("input_tokens"))
                output_tokens += _int(usage.get("output_tokens"))
                cache_read += _int(usage.get("cache_read_input_tokens"))
                cache_write += _int(usage.get("cache_creation_input_tokens"))
    except OSError:
        return TranscriptMeta()
    return TranscriptMeta(
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
        run_started_at=current_run_start(times),
        run_ended_at=max(times) if times else None,
    )


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)
