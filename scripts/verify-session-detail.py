#!/usr/bin/env python3
"""Prove STW-43: one session detail pane with a read-only transcript tail.

Read-only. It never writes to a source store. It builds a temp pi root, reads
it with the real ``PiSessions`` source, then:

  1. checks the pane metadata carries every required field
  2. checks the transcript tail is the last records and matches an independent
     parse of the same source file, so the pane text cannot drift from the file
  3. drives the real app headless and opens the detail pane from each of the
     three entry points: a grid card, a waterfall block and a search row
  4. checks each entry point opens the same session and the same pane

Usage:
    uv run python scripts/verify-session-detail.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brigade_tui.app import BrigadeTUI
from brigade_tui.detail import (
    SessionDetailScreen,
    metadata_lines,
    render_metadata,
)
from brigade_tui.model import Session
from brigade_tui.sources.pi import PiSessions
from brigade_tui.transcript import TAIL_LINES, read_transcript_tail

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"
TARGET_ID = "session-beta"
REQUIRED_FIELDS = (
    "Title",
    "Harness",
    "Model",
    "Directory",
    "Start",
    "Last activity",
    "State",
    "Tokens",
    "Cost",
)


def iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def build_root(root: Path) -> Path:
    """Write two real pi transcripts into a temp root. Return the root."""
    project = root / "proj-alpha"
    project.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    for session_id, title, role, tail_marker in (
        ("session-alpha", "ALPHA-TITLE-1111", "builder", "TAILMARKER-alpha-4271"),
        ("session-beta", "BETA-TITLE-2222", "reviewer", "TAILMARKER-beta-9137"),
    ):
        start = now - timedelta(minutes=10)
        records = [
            {"type": "session", "id": session_id, "timestamp": iso(start),
             "cwd": "/home/user/proj-alpha"},
            {"type": "model_change", "id": f"mc-{session_id}", "timestamp": iso(start),
             "provider": "opencode-go", "modelId": "deepseek-v4.1-flash"},
            {"type": "session_info", "id": f"info-{session_id}", "timestamp": iso(start),
             "name": role},
        ]
        for index in range(60):
            if index == 0:
                text = title
            elif index == 10:
                text = "HEADMARKER-EXCLUDED"
            elif index == 59:
                text = tail_marker
            else:
                text = f"message {index} from {role}"
            records.append({
                "type": "message",
                "id": f"msg-{session_id}-{index}",
                "timestamp": iso(start + timedelta(seconds=index * 5)),
                "message": {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": text,
                },
            })
        (project / f"{session_id}.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
    return root


def independent_tail(path: Path, limit: int = TAIL_LINES) -> list[tuple[str, str]]:
    """Parse the same file with a second, independent parser."""
    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        entries.append((role, " ".join(content.split())))
    return entries[-limit:]


def find_session(sessions: list[Session], session_id: str) -> Session:
    return next(item for item in sessions if item.session_id == session_id)


async def check_entry_points(sessions: list[Session]) -> dict:
    """Open the detail pane from all three entry points. Return what opened."""

    class FrozenSource:
        harness = sessions[0].harness

        def exists(self) -> bool:
            return True

        def snapshot(self) -> list[Session]:
            return list(sessions)

    FrozenSource.harness = sessions[0].harness
    result: dict[str, str] = {}
    app = BrigadeTUI(sources=(FrozenSource(),))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        # Grid card: the newest session is session-beta, drawn first. Its
        # title line is at grid offset (1, 3), after the banner and heading.
        await pilot.click("#grid", offset=(3, 3))
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen), "grid card did not open a pane"
        result["grid_card"] = app.screen.session.session_id
        await pilot.press("escape")
        await pilot.pause()

        # Waterfall block: the reviewer lane is the second lane row.
        app.enter_project_view()
        await pilot.pause()
        await pilot.click("#timeline-0", offset=(40, 4))
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen), "block did not open a pane"
        result["waterfall_block"] = app.screen.session.session_id
        await pilot.press("escape")
        await pilot.pause()

        # Search row: filter to the target, focus the table, press enter.
        await pilot.press("escape")
        await pilot.press("slash")
        await pilot.pause()
        app.screen.query_one("#search-input").value = TARGET_ID
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen), "search row did not open a pane"
        result["search_row"] = app.screen.session.session_id
    return result


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="brigade-detail-"))
    failures: list[str] = []
    try:
        build_root(root)
        saved = os.environ.get("PI_SESSION_DIR")
        os.environ["PI_SESSION_DIR"] = str(root)
        try:
            sessions = PiSessions().snapshot()
        finally:
            if saved is None:
                os.environ.pop("PI_SESSION_DIR", None)
            else:
                os.environ["PI_SESSION_DIR"] = saved

        target = find_session(sessions, TARGET_ID)
        tail = read_transcript_tail(target)
        expected = independent_tail(Path(target.transcript_path or ""))

        # (1) Metadata carries every required field.
        labels = [label for label, _ in metadata_lines(target, datetime.now(timezone.utc))]
        for field in REQUIRED_FIELDS:
            if field not in labels:
                failures.append(f"metadata field missing: {field}")
        rendered_meta = render_metadata(target, datetime.now(timezone.utc)).plain
        for value in ("BETA-TITLE-2222", "proj-alpha", "deepseek-v4.1-flash"):
            if value not in rendered_meta:
                failures.append(f"metadata value missing: {value}")

        # (2) The tail is the last records and matches the file.
        actual = [(entry.role, entry.text) for entry in tail.entries]
        if actual != expected:
            failures.append("transcript tail does not match the source file")
        if len(actual) != TAIL_LINES:
            failures.append(f"tail length is {len(actual)}, expected {TAIL_LINES}")
        if "HEADMARKER-EXCLUDED" in {text for _role, text in actual}:
            failures.append("tail included a record outside the tail window")
        if actual and actual[-1][1] != "TAILMARKER-beta-9137":
            failures.append("tail does not end at the source file's last message")

        # (3) Each entry point opens the same pane.
        entry_points = asyncio.run(check_entry_points(sessions))
        for name, session_id in entry_points.items():
            if session_id != TARGET_ID:
                failures.append(f"{name} opened {session_id}, expected {TARGET_ID}")

        document = {
            "item": "STW-43",
            "target_session": TARGET_ID,
            "source_file": target.transcript_path,
            "tail_total": tail.total,
            "tail_shown": len(tail.entries),
            "tail_truncated": tail.truncated,
            "tail_matches_source_file": actual == expected,
            "tail_first": actual[0] if actual else None,
            "tail_last": actual[-1] if actual else None,
            "metadata_fields": labels,
            "entry_points": entry_points,
            "failures": failures,
        }
        EVIDENCE.mkdir(exist_ok=True)
        out = EVIDENCE / "STW-43-verify-session-detail.json"
        out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

        print(f"source file: {target.transcript_path}")
        print(f"tail: {len(tail.entries)} of {tail.total} records, truncated={tail.truncated}")
        print(f"tail first: {actual[0] if actual else None}")
        print(f"tail last:  {actual[-1] if actual else None}")
        print(f"tail matches independent parse: {actual == expected}")
        print(f"metadata fields: {', '.join(labels)}")
        for name, session_id in entry_points.items():
            print(f"entry point {name:16} opened {session_id}")
        if failures:
            print("FAIL")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("PASS: one pane from all three entry points, tail matches the source file")
        print(f"wrote {out}")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
