"""Tests for the session detail pane and the transcript tail reader.

The reader is tested against real files written to ``tmp_path``: a pi JSONL
transcript, a Claude Code JSONL transcript and an opencode SQLite store. The
pane is tested through Textual's ``run_test()`` harness, including the three
entry points: a grid card, a waterfall block and a search row.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.widgets import DataTable, Input

from brigade_tui.app import SearchScreen, BrigadeTUI
from brigade_tui.detail import (
    SessionDetailScreen,
    metadata_lines,
    render_metadata,
    tail_heading,
    tail_lines,
)
from brigade_tui.model import Harness, ProjectRef, Session
from brigade_tui.transcript import read_transcript_tail

NOW = datetime.now(timezone.utc)


def iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def write_pi_transcript(path: Path, session_id: str, count: int = 60) -> Path:
    """A real pi transcript: session, model, then user/assistant messages."""
    path.parent.mkdir(parents=True, exist_ok=True)
    start = NOW - timedelta(minutes=10)
    records = [
        {
            "type": "session",
            "id": session_id,
            "timestamp": iso(start),
            "cwd": "/home/user/proj-alpha",
        },
        {
            "type": "model_change",
            "id": f"mc-{session_id}",
            "timestamp": iso(start),
            "provider": "opencode-go",
            "modelId": "deepseek-v4.1-flash",
        },
    ]
    for index in range(count):
        text = f"message {index}"
        if index == 0:
            text = "TITLE-TEXT"
        elif index == 10:
            text = "HEADMARKER-1010"
        elif index == count - 1:
            text = "TAILMARKER-9999"
        records.append(
            {
                "type": "message",
                "id": f"msg-{index}",
                "timestamp": iso(start + timedelta(seconds=index * 5)),
                "message": {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": text,
                },
            }
        )
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def pi_session(path: Path, session_id: str) -> Session:
    return Session(
        harness=Harness.PI,
        session_id=session_id,
        project=ProjectRef(key="/home/user/proj-alpha", label="proj-alpha"),
        directory="/home/user/proj-alpha",
        last_activity=NOW - timedelta(seconds=5),
        title="TITLE-TEXT",
        model="opencode-go/deepseek-v4.1-flash",
        started_at=NOW - timedelta(minutes=10),
        transcript_path=str(path),
    )


def test_tail_is_the_last_records_and_excludes_the_head(tmp_path) -> None:
    path = write_pi_transcript(tmp_path / "proj" / "s1.jsonl", "s1", count=60)
    tail = read_transcript_tail(pi_session(path, "s1"))
    assert tail.total == 60
    assert len(tail.entries) == 40
    assert tail.truncated is True
    assert tail.entries[0].text == "message 20"
    assert tail.entries[-1].text == "TAILMARKER-9999"
    assert tail.entries[-1].role == "assistant"
    assert "HEADMARKER-1010" not in {entry.text for entry in tail.entries}


def test_tail_is_empty_for_a_missing_file(tmp_path) -> None:
    missing = tmp_path / "nope.jsonl"
    tail = read_transcript_tail(pi_session(missing, "gone"))
    assert tail.entries == ()
    assert tail.total == 0
    assert tail.error is not None


def test_tail_reads_a_claude_transcript(tmp_path) -> None:
    path = tmp_path / "claude" / "c1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "user",
            "sessionId": "c1",
            "cwd": "/home/user/proj-alpha",
            "timestamp": iso(NOW),
            "message": {"role": "user", "content": "hello there"},
        },
        {
            "type": "assistant",
            "sessionId": "c1",
            "timestamp": iso(NOW + timedelta(seconds=1)),
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hi back"}]},
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    session = Session(
        harness=Harness.CLAUDE,
        session_id="c1",
        project=ProjectRef(key="/home/user/proj-alpha", label="proj-alpha"),
        directory="/home/user/proj-alpha",
        last_activity=NOW,
        transcript_path=str(path),
    )
    tail = read_transcript_tail(session)
    assert [entry.text for entry in tail.entries] == ["hello there", "hi back"]
    assert [entry.role for entry in tail.entries] == ["user", "assistant"]


def test_tail_reads_an_opencode_store(tmp_path) -> None:
    db = tmp_path / "opencode.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    connection.execute(
        "CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,"
        " time_created INTEGER, data TEXT)"
    )
    for index, (role, text) in enumerate(
        [("user", "first question"), ("assistant", "the answer")]
    ):
        message_id = f"m{index}"
        connection.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            (
                message_id,
                "ses-1",
                index,
                json.dumps({"role": role, "time": {"created": index}}),
            ),
        )
        connection.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            (
                f"p{index}",
                message_id,
                "ses-1",
                index,
                json.dumps({"type": "text", "text": text}),
            ),
        )
    connection.commit()
    connection.close()
    session = Session(
        harness=Harness.OPENCODE,
        session_id="ses-1",
        project=ProjectRef(key="/home/user/proj-alpha", label="proj-alpha"),
        directory="/home/user/proj-alpha",
        last_activity=NOW,
        transcript_path=str(db),
    )
    tail = read_transcript_tail(session)
    assert [entry.text for entry in tail.entries] == ["first question", "the answer"]
    assert tail.total == 2


def test_metadata_lines_show_every_required_field(tmp_path) -> None:
    path = write_pi_transcript(tmp_path / "proj" / "s1.jsonl", "s1")
    session = pi_session(path, "s1")
    labels = [label for label, _ in metadata_lines(session, NOW)]
    assert labels == [
        "Session",
        "Title",
        "Harness",
        "Model",
        "Directory",
        "Start",
        "Last activity",
        "Elapsed",
        "State",
        "Tokens",
        "Cost",
        "Agent",
    ]
    rendered = render_metadata(session, NOW).plain
    assert "TITLE-TEXT" in rendered
    assert "/home/user/proj-alpha" in rendered
    assert "opencode-go/deepseek-v4.1-flash" in rendered


class FixedSource:
    """A real in-test source that returns a fixed session list."""

    harness = Harness.PI

    def __init__(self, sessions: list[Session]) -> None:
        self._sessions = list(sessions)

    def exists(self) -> bool:
        return True

    def snapshot(self) -> list[Session]:
        return list(self._sessions)


def two_sessions(tmp_path) -> list[Session]:
    alpha = pi_session(
        write_pi_transcript(tmp_path / "proj" / "alpha.jsonl", "session-alpha"),
        "session-alpha",
    )
    beta = pi_session(
        write_pi_transcript(tmp_path / "proj" / "beta.jsonl", "session-beta"),
        "session-beta",
    )
    beta = Session(
        **{
            **beta.__dict__,
            "title": "BETA-TITLE",
            "last_activity": NOW - timedelta(seconds=1),
        }
    )
    return [alpha, beta]


async def test_detail_pane_shows_fields_and_the_transcript_tail(tmp_path) -> None:
    session = two_sessions(tmp_path)[1]
    app = BrigadeTUI(sources=(FixedSource([session]),))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        app.open_session_detail(session.session_id)
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen)
        detail = app.screen
        text = detail.query_one("#detail-meta").render().plain
        assert "BETA-TITLE" in text
        assert "session-beta" in text
        assert "pi" in text
        heading = detail.query_one("#detail-tail-heading").render().plain
        assert "Read-only transcript tail" in heading
        assert str(detail.tail.entries[-1].text) == "TAILMARKER-9999"


async def test_grid_card_click_opens_the_detail_pane(tmp_path) -> None:
    sessions = two_sessions(tmp_path)
    app = BrigadeTUI(sources=(FixedSource(sessions),))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        # The grid draws cards under the banner and heading, so the title line
        # of the first card sits at grid offset (1, 3). The first card is the
        # newest session, session-beta.
        await pilot.click("#grid", offset=(3, 3))
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen)
        assert app.screen.session.session_id == "session-beta"


async def test_waterfall_block_click_opens_the_detail_pane(tmp_path) -> None:
    sessions = two_sessions(tmp_path)
    app = BrigadeTUI(sources=(FixedSource(sessions),))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        app.enter_project_view()
        await pilot.pause()
        # Lanes are ordered builder then reviewer. The reviewer lane is the
        # second lane row (offset y=4) with its block starting past the label.
        await pilot.click("#timeline-0", offset=(40, 4))
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen)
        assert app.screen.session.session_id == "session-beta"


async def test_search_row_enter_opens_the_same_detail_pane(tmp_path) -> None:
    sessions = two_sessions(tmp_path)
    app = BrigadeTUI(sources=(FixedSource(sessions),))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, SearchScreen)
        app.screen.query_one("#search-input", Input).value = "session-beta"
        await pilot.pause()
        table = app.screen.query_one("#search-results", DataTable)
        assert table.row_count == 1
        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen)
        assert app.screen.session.session_id == "session-beta"


def test_tail_lines_and_heading_name_the_source(tmp_path) -> None:
    path = write_pi_transcript(tmp_path / "proj" / "s1.jsonl", "s1")
    tail = read_transcript_tail(pi_session(path, "s1"))
    lines = tail_lines(tail)
    assert len(lines) == 40
    assert lines[-1].endswith("TAILMARKER-9999")
    assert str(path) in tail_heading(tail)
