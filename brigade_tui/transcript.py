"""Read the tail of a session transcript. Read-only.

The session detail pane shows a read-only tail of the transcript a session
came from. A transcript is a JSONL file for pi and Claude Code, and the
opencode SQLite store for opencode. This module owns the one reader both the
pane and the verifier call, so the pane text and the source file cannot drift.

The tail is the last ``limit`` message records, oldest first. Tool records,
usage records and other non-message records are skipped. A record's text is
whitespace-normalized, so one record is one logical line in the pane. Nothing
here writes to a store and nothing launches a process.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .model import Harness, Session

#: How many message records the tail holds.
TAIL_LINES = 40

#: Roles shown in a transcript tail. Tool and system records are skipped.
TAIL_ROLES: frozenset[str] = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class TranscriptEntry:
    """One message in the tail."""

    role: str
    text: str
    when: datetime | None = None


@dataclass(frozen=True)
class TranscriptTail:
    """The tail of one session's transcript.

    ``source`` is the file or database the tail was read from. ``total`` is
    every message record the reader saw, so a pane can say it is showing the
    last of many. ``truncated`` is True when the transcript held more records
    than the limit. An unreadable source yields an empty entries tuple and a
    total of 0, never an exception.
    """

    session_id: str
    source: str
    entries: tuple[TranscriptEntry, ...] = ()
    total: int = 0
    truncated: bool = False
    error: str | None = None


def read_transcript_tail(session: Session, limit: int = TAIL_LINES) -> TranscriptTail:
    """Read the tail of ``session``'s transcript. Never raises."""
    path = session.transcript_path
    if not path:
        return TranscriptTail(
            session_id=session.session_id,
            source="",
            error="no transcript path",
        )
    if session.harness is Harness.OPENCODE:
        return _read_opencode_tail(path, session.session_id, limit)
    return _read_jsonl_tail(path, session.harness, session.session_id, limit)


def _read_jsonl_tail(
    path: str,
    harness: Harness,
    session_id: str,
    limit: int,
) -> TranscriptTail:
    """Read the tail of a pi or Claude Code JSONL transcript."""
    entries: list[TranscriptEntry] = []
    total = 0
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                record = _parse_json(line)
                if record is None:
                    continue
                entry = _entry_from_record(harness, record)
                if entry is None:
                    continue
                total += 1
                entries.append(entry)
    except OSError as error:
        return TranscriptTail(
            session_id=session_id, source=path, error=type(error).__name__
        )
    tail = entries[-limit:] if limit > 0 else []
    return TranscriptTail(
        session_id=session_id,
        source=path,
        entries=tuple(tail),
        total=total,
        truncated=total > len(tail),
    )


def _entry_from_record(harness: Harness, record: dict) -> TranscriptEntry | None:
    """One transcript entry from a pi or Claude JSONL record, or None."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or role not in TAIL_ROLES:
        return None
    text = message_text(message.get("content"))
    if not text:
        return None
    return TranscriptEntry(
        role=role,
        text=text,
        when=_record_time(record),
    )


def message_text(content: object) -> str:
    """The text of a message content value, whitespace-normalized.

    A string is the text. A list is every text block joined, so a reply split
    across blocks reads as one line. A tool result or an image yields the
    empty string.
    """
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        raw = "\n".join(parts)
    else:
        return ""
    return " ".join(raw.split())


def _record_time(record: dict) -> datetime | None:
    """The timestamp a record carries, as an aware datetime, or None."""
    value = record.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_opencode_tail(
    db: str,
    session_id: str,
    limit: int,
) -> TranscriptTail:
    """Read the tail of an opencode session from its SQLite store.

    Messages come from the ``message`` table and their text from ``part``.
    The store has changed shape across opencode versions, so a missing table
    yields an empty tail rather than an error. The URI forces read-only mode.
    """
    uri = f"file:{db}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as error:
        return TranscriptTail(
            session_id=session_id, source=db, error=type(error).__name__
        )
    try:
        connection.row_factory = sqlite3.Row
        texts = _opencode_parts(connection, session_id)
        entries: list[TranscriptEntry] = []
        for row in connection.execute(
            "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        ):
            data = _parse_json(str(row["data"] or ""))
            if data is None:
                continue
            role = data.get("role")
            if not isinstance(role, str) or role not in TAIL_ROLES:
                continue
            text = " ".join(texts.get(str(row["id"]), "").split())
            if not text:
                continue
            entries.append(
                TranscriptEntry(
                    role=role,
                    text=text,
                    when=_opencode_time(data.get("time")),
                )
            )
    except sqlite3.Error as error:
        return TranscriptTail(
            session_id=session_id, source=db, error=type(error).__name__
        )
    finally:
        connection.close()
    tail = entries[-limit:] if limit > 0 else []
    return TranscriptTail(
        session_id=session_id,
        source=db,
        entries=tuple(tail),
        total=len(entries),
        truncated=len(entries) > len(tail),
    )


def _opencode_parts(
    connection: sqlite3.Connection, session_id: str
) -> dict[str, str]:
    """Group each message's text parts, in part order."""
    grouped: dict[str, list[str]] = {}
    try:
        rows = connection.execute(
            "SELECT message_id, data FROM part WHERE session_id = ?"
            " ORDER BY time_created, id",
            (session_id,),
        )
    except sqlite3.Error:
        return {}
    for row in rows:
        data = _parse_json(str(row["data"] or ""))
        if data is None or data.get("type") != "text":
            continue
        text = data.get("text")
        if not isinstance(text, str) or not text:
            continue
        grouped.setdefault(str(row["message_id"]), []).append(text)
    return {key: "\n".join(value) for key, value in grouped.items()}


def _opencode_time(value: object) -> datetime | None:
    """The created time of an opencode message, as an aware datetime."""
    if not isinstance(value, dict):
        return None
    created = value.get("created")
    if not isinstance(created, (int, float)):
        return None
    return datetime.fromtimestamp(created / 1000, tz=timezone.utc)
