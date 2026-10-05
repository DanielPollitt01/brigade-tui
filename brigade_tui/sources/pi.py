"""Read pi session transcripts. Read-only.

pi writes one JSONL file per session under
``~/.pi/agent/sessions/<cwd-slug>/``. The first line is a ``session`` record
carrying the id, ``cwd`` and timestamp. A later ``model_change`` record carries
the model. The file's mtime is the last activity.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from decimal import Decimal

from ..model import (
    Harness,
    ProjectRef,
    Session,
    Usage,
    current_run_start,
    detect_role,
)
from .base import (
    TranscriptCache,
    TranscriptMeta,
    file_mtime,
    first_text,
    parse_iso,
    project_from_directory,
)

_SCAN_LINES = 200


def default_root() -> Path:
    return Path(os.environ.get("PI_SESSION_DIR", "~/.pi/agent/sessions")).expanduser()


class PiSessions:
    """Read pi sessions from a directory tree of JSONL files."""

    harness = Harness.PI

    #: How the store is opened. pi transcripts are read as plain text files.
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
        role: str | None = None
        try:
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= _SCAN_LINES:
                        break
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = record.get("type")
                    if kind == "session":
                        session_id = str(record.get("id") or path.stem)
                        cwd = str(record.get("cwd") or "")
                        started = parse_iso(record.get("timestamp"))
                    elif kind == "model_change" and model is None:
                        model = _format_model(record)
                    elif kind == "session_info" and role is None:
                        role = detect_role(record.get("name"))
                    elif kind == "message" and title is None:
                        message = record.get("message")
                        if isinstance(message, dict) and message.get("role") == "user":
                            title = first_text(message.get("content"))
        except OSError:
            return None
        if not session_id:
            return None
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
            role=role,
            transcript_path=str(path),
        )


def _read_usage(path: Path) -> TranscriptMeta:
    """Read token totals and the start of the most recent run.

    pi writes one ``message`` record per assistant turn. Each carries a
    ``usage`` block: ``input``, ``output``, ``cacheRead``, ``cacheWrite`` and
    a nested ``cost`` whose ``total`` is the turn's spend. The totals are the
    sum over the whole transcript, so read every line, not the metadata head.
    Every record also carries a timestamp, so the same pass finds the run
    start.
    """
    input_tokens = output_tokens = cache_read = cache_write = 0
    cost = Decimal(0)
    has_cost = False
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
                if record.get("type") != "message":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") != "assistant":
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                input_tokens += _int(usage.get("input"))
                output_tokens += _int(usage.get("output"))
                cache_read += _int(usage.get("cacheRead"))
                cache_write += _int(usage.get("cacheWrite"))
                total = _cost_total(usage.get("cost"))
                if total is not None:
                    cost += total
                    has_cost = True
    except OSError:
        return TranscriptMeta()
    return TranscriptMeta(
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost=cost if has_cost else None,
        ),
        run_started_at=current_run_start(times),
        run_ended_at=max(times) if times else None,
    )


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _cost_total(value: object) -> Decimal | None:
    if not isinstance(value, dict):
        return None
    total = value.get("total")
    if total is None:
        return None
    try:
        return Decimal(str(total))
    except Exception:
        return None


def _format_model(record: dict) -> str | None:
    model_id = record.get("modelId")
    provider = record.get("provider")
    if model_id and provider:
        return f"{provider}/{model_id}"
    return str(model_id) if model_id else None
