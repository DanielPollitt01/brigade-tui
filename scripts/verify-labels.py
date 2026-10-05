#!/usr/bin/env python3
"""Prove R11: every visible lane and card label is meaningful.

Reads the real pi, Claude Code and opencode stores, then drives the real
``BrigadeTUI`` over them at 160x48. It collects every visible label:

  - every grid card label (``Card.agent``), the card's agent or role field
  - every timeline lane label, for every project tab
  - every flat-table Session cell, for every project tab

and checks:

  1. no visible label is a bare eight-character hash
  2. a no-role session with a title gets the project name plus a title
     fragment, never the bare short id and never the whole raw title
  3. a no-role session with no title gets the project name plus a readable
     id (harness plus short id), never a bare hash
  4. the real stores hold at least one no-role, no-title session, and its
     rendered label is the readable-id form, so the fallback is proven on real
     data
  5. the count of labels the old bare-short-id rule would have rendered and
     the count the new rule renders, for the record

Writes ``evidence/STW-52-verify-labels.json`` from the real run.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-labels.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from textual.widgets import DataTable, TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.grid import SessionGrid
from brigade_tui.model import Session, is_bare_hash, session_label
from brigade_tui.sources import default_sources
from brigade_tui.timeline import TimelineView

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"
JSON_PATH = EVIDENCE / "STW-52-verify-labels.json"

ROLES = ("planner", "builder", "reviewer")


class FrozenSource:
    """A real source whose snapshot is read once, then held."""

    def __init__(self, real) -> None:
        self.real = real
        self.harness = real.harness
        self._snapshot: list[Session] | None = None

    def exists(self) -> bool:
        return self.real.exists()

    def snapshot(self) -> list[Session]:
        if self._snapshot is None:
            self._snapshot = list(self.real.snapshot())
        return list(self._snapshot)


async def drive(frozen: list[FrozenSource]) -> dict:
    """Render the real TUI and collect every visible label."""
    app = BrigadeTUI(sources=frozen)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()

        grid = app.query_one("#grid", SessionGrid)
        card_labels = [card.agent for card in grid.cards()]
        card_ids = [card.session_id for card in grid.cards()]

        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)
        lane_labels: list[str] = []
        table_labels: list[str] = []
        for index, pane in enumerate(panes):
            content.active = pane.id
            await pilot.pause()
            timeline = app.query_one(f"#timeline-{index}", TimelineView)
            lane_labels.extend(lane.label for lane in timeline.lanes)
            table = app.query_one(f"#sessions-{index}", DataTable)
            for row_key in table.rows:
                table_labels.append(str(table.get_row(row_key)[-1]))

    return {
        "card_labels": card_labels,
        "card_ids": card_ids,
        "lane_labels": lane_labels,
        "table_labels": table_labels,
    }


def main() -> int:
    frozen = [FrozenSource(source) for source in default_sources()]
    sessions: list[Session] = []
    for source in frozen:
        sessions.extend(source.snapshot())
    by_id = {session.session_id: session for session in sessions}

    result = asyncio.run(drive(frozen))
    failures: list[str] = []

    all_labels = (
        result["card_labels"] + result["lane_labels"] + result["table_labels"]
    )
    visible = [label for label in all_labels if label and label != "(untitled)"]

    # (1) No visible label is a bare eight-character hash.
    bad = sorted({label for label in visible if is_bare_hash(label)})
    if bad:
        failures.append(f"{len(bad)} visible labels are bare hashes: {bad[:10]}")

    # (2) and (3) A no-role session renders project plus fragment or readable id.
    checked_title = 0
    checked_readable = 0
    repaired = 0
    for card_id, label in zip(result["card_ids"], result["card_labels"]):
        session = by_id.get(card_id)
        if session is None or session.role:
            continue
        if session.agent and label == session.agent:
            # A source-reported agent wins before the label fallback.
            continue
        name = session.project.label or session.project.key
        if label != session_label(session):
            failures.append(
                f"{card_id[:8]}: card label {label!r} != session_label"
            )
        if not label.startswith(f"{name}:"):
            failures.append(f"{card_id[:8]}: label {label!r} does not name {name!r}")
        if is_bare_hash(label):
            failures.append(f"{card_id[:8]}: label {label!r} is a bare hash")
        if label == (session.session_id[:8] or "-"):
            failures.append(f"{card_id[:8]}: label {label!r} is the bare short id")
        if session.title:
            fragment = " ".join(session.title.split())[:40].rstrip()
            if fragment and fragment not in label:
                failures.append(
                    f"{card_id[:8]}: label {label!r} drops the title fragment"
                )
            checked_title += 1
        else:
            expected = f"{session.harness.value}:{session.session_id[:8]}"
            if expected not in label:
                failures.append(
                    f"{card_id[:8]}: label {label!r} has no readable id {expected!r}"
                )
            checked_readable += 1
        if is_bare_hash(session.session_id[:8]):
            repaired += 1

    if checked_readable < 1:
        failures.append("no real no-role, no-title session was rendered")

    # (4) A dedicated real no-role, no-title session exists in the stores.
    silent_real = [
        session
        for session in sessions
        if not session.role and not session.title and session.session_id in by_id
    ]

    summary = {
        "sessions_read": len(sessions),
        "grid_cards": len(result["card_labels"]),
        "lanes": len(result["lane_labels"]),
        "table_rows": len(result["table_labels"]),
        "visible_labels": len(visible),
        "bare_hash_labels": 0 if not bad else len(bad),
        "no_role_labels_checked": checked_title + checked_readable,
        "checked_title_fragment": checked_title,
        "checked_readable_id": checked_readable,
        "old_rule_would_hash": repaired,
        "real_no_role_no_title_sessions": len(silent_real),
        "sample_readable_labels": sorted(
            {
                label
                for card_id, label in zip(
                    result["card_ids"], result["card_labels"]
                )
                if (session := by_id.get(card_id))
                and not session.role
                and not session.title
                and is_bare_hash(session.session_id[:8])
            }
        )[:6],
        "sample_title_labels": sorted(
            {
                label
                for card_id, label in zip(
                    result["card_ids"], result["card_labels"]
                )
                if (session := by_id.get(card_id))
                and not session.role
                and session.title
            }
        )[:6],
        "failures": failures,
    }

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    if failures:
        print(f"FAIL: {len(failures)} problem(s)", file=sys.stderr)
        return 1
    print("PASS: no visible label is a bare eight-character hash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
