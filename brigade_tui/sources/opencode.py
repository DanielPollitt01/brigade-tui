"""Read opencode sessions from its SQLite store. Read-only.

opencode keeps sessions in ``~/.local/share/opencode/opencode.db``. The
database is opened in read-only URI mode, so Brigade can never write to it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from ..model import Harness, Session, current_run_start, detect_role, Usage
from .base import project_from_directory


def default_db() -> Path:
    return Path(
        os.environ.get("OPENCODE_DB", "~/.local/share/opencode/opencode.db")
    ).expanduser()


class OpencodeSessions:
    """Read opencode sessions from the ``session`` table."""

    harness = Harness.OPENCODE

    #: How the store is opened. The SQLite URI forces read-only mode.
    open_mode = "ro"

    #: Columns always selected. ``agent`` is added when the store has it, so
    #: an older store with no agent column still reads.
    BASE_COLUMNS = (
        "id, directory, title, model, cost, tokens_input, tokens_output,"
        " tokens_cache_read, tokens_cache_write, time_created, time_updated"
    )

    def __init__(self, db: str | Path | None = None) -> None:
        self.db = Path(db).expanduser() if db is not None else default_db()

    @property
    def store_path(self) -> str:
        return str(self.db)

    def exists(self) -> bool:
        return self.db.is_file()

    def snapshot(self) -> list[Session]:
        if not self.exists():
            return []
        uri = f"file:{self.db}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.Error:
            return []
        sessions: list[Session] = []
        try:
            connection.row_factory = sqlite3.Row
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(session)")
            }
            agent_column = "agent" if "agent" in columns else "NULL AS agent"
            query = f"SELECT {self.BASE_COLUMNS}, {agent_column} FROM session"
            times = _message_times(connection)
            for row in connection.execute(query):
                sessions.append(self._row_to_session(row, times))
        except sqlite3.Error:
            return []
        finally:
            connection.close()
        sessions.sort(key=lambda item: item.last_activity, reverse=True)
        return sessions

    def _row_to_session(
        self,
        row: sqlite3.Row,
        times: dict[str, list[datetime]] | None = None,
    ) -> Session:
        directory = str(row["directory"] or "")
        session_id = str(row["id"])
        return Session(
            harness=self.harness,
            session_id=session_id,
            project=project_from_directory(directory),
            directory=directory,
            last_activity=_from_ms(row["time_updated"]),
            title=str(row["title"]) if row["title"] else None,
            model=format_model(row["model"]),
            role=detect_role(row["title"]),
            agent=str(row["agent"]) if row["agent"] else None,
            usage=Usage(
                input_tokens=_int(row["tokens_input"]),
                output_tokens=_int(row["tokens_output"]),
                cache_read_tokens=_int(row["tokens_cache_read"]),
                cache_write_tokens=_int(row["tokens_cache_write"]),
                cost=_decimal(row["cost"]),
            ),
            started_at=_from_ms(row["time_created"]),
            run_started_at=current_run_start((times or {}).get(session_id, [])),
            transcript_path=str(self.db),
        )


def _message_times(connection: sqlite3.Connection) -> dict[str, list[datetime]]:
    """Group each session's message times, oldest first.

    The start of the current run comes from the same full read, so an
    opencode session resumed after a pause shows the resumed run, not its
    age. A store with no message table yields no times, and the run span
    falls back to the session's own start.
    """
    times: dict[str, list[datetime]] = {}
    try:
        rows = connection.execute(
            "SELECT session_id, time_created FROM message"
            " WHERE time_created IS NOT NULL ORDER BY session_id, time_created"
        )
    except sqlite3.Error:
        return times
    for row in rows:
        times.setdefault(str(row[0]), []).append(_from_ms(row[1]))
    return times


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _from_ms(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def format_model(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(parsed, dict):
        return value
    model_id = parsed.get("id")
    provider = parsed.get("providerID") or parsed.get("provider")
    if model_id and provider:
        return f"{provider}/{model_id}"
    return str(model_id) if model_id else value
