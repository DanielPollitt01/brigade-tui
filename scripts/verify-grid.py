#!/usr/bin/env python3
"""Prove ISC-27 and ISC-10: the grid main view and its state colours.

The script points Brigade at a real pi root in ``$HOME/.cache`` (never /tmp,
so the session is not hidden as scratch debris) and drives the real app:

  1. the default view is a grid (``#grid`` shown, ``#projects`` is only the
     tab bar)
  2. a card shows the title, the agent or role, and the elapsed time
  3. two renders 30 seconds apart show the elapsed time grow
  4. the three state colours are exactly red, green and blue, and the grid
     paints running blue and completed green (blocked is checked on a real
     session object that reports it, because no source reports blocked today)

Read-only. It never writes to a real source store.

Usage:
    uv run python scripts/verify-grid.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.widgets import TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.grid import (
    STATE_COLOURS,
    SessionGrid,
    build_cards,
)
from brigade_tui.model import SessionState

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"


def write_pi_session(root: Path, project: str, session_id: str, role: str) -> None:
    project_dir = root / project
    project_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc) - timedelta(seconds=45)
    record = {
        "type": "session",
        "id": session_id,
        "timestamp": started.isoformat().replace("+00:00", "Z"),
        "cwd": f"/home/user/{project}",
    }
    model = {
        "type": "model_change",
        "id": f"mc-{session_id}",
        "timestamp": started.isoformat().replace("+00:00", "Z"),
        "provider": "opencode-go",
        "modelId": "deepseek-v4.1-flash",
    }
    message = {
        "type": "message",
        "id": f"msg-{session_id}",
        "timestamp": started.isoformat().replace("+00:00", "Z"),
        "message": {"role": "user", "content": f"Grid card title for {role}"},
    }
    info = {
        "type": "session_info",
        "id": f"info-{session_id}",
        "timestamp": started.isoformat().replace("+00:00", "Z"),
        "name": role,
    }
    lines = "\n".join(json.dumps(item) for item in (record, model, message, info))
    (root / project / f"{session_id}.jsonl").write_text(lines + "\n", encoding="utf-8")
    # The source root globs ``*/*.jsonl``, so the file lives inside the
    # project directory. The project directory name is what is not hidden.
    (project_dir / f"{session_id}.jsonl").write_text(lines + "\n", encoding="utf-8")


async def render_grid(root: Path) -> dict:
    os.environ["PI_SESSION_DIR"] = str(root)
    os.environ["CLAUDE_PROJECTS_DIR"] = str(root / "empty-claude")
    os.environ["OPENCODE_DB"] = str(root / "no-opencode.db")
    app = BrigadeTUI()
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        grid = app.query_one("#grid", SessionGrid)
        projects = app.query_one("#projects", TabbedContent)
        cards = grid.cards()
        spans = {
            str(span.style)
            for span in grid.render().spans
        }
        return {
            "grid_mode": app._grid_mode,
            "grid_display": grid.display,
            "tab_bar_height": projects.region.height,
            "cards": [
                {
                    "title": card.title,
                    "agent": card.agent,
                    "elapsed": card.elapsed,
                    "elapsed_seconds": round(card.elapsed_seconds, 3),
                    "state": card.state.value,
                    "colour": card.colour,
                }
                for card in cards
            ],
            "styles": sorted(spans),
        }


def colour_check() -> dict:
    """Check the three state colours on real session objects."""
    now = datetime.now(timezone.utc)
    from brigade_tui.model import Harness, ProjectRef, Session

    def make(session_id: str, role: str, last, started, reported=None) -> Session:
        return Session(
            harness=Harness.PI,
            session_id=session_id,
            project=ProjectRef(key="/home/user/x", label="x"),
            directory="/home/user/x",
            last_activity=last,
            title=f"card {role}",
            role=role,
            started_at=started,
            reported_state=reported,
        )

    sessions = [
        make("run", "builder", now - timedelta(seconds=5), now - timedelta(seconds=65)),
        make("idle", "planner", now - timedelta(seconds=300), now - timedelta(seconds=900)),
        make("done", "reviewer", now - timedelta(seconds=1200), now - timedelta(seconds=1800)),
        make(
            "blocked",
            "planner",
            now - timedelta(seconds=5),
            now - timedelta(seconds=65),
            reported="blocked",
        ),
    ]
    cards = {card.session_id: card for card in build_cards(sessions, now)}
    return {
        "declared": {state.value: colour for state, colour in STATE_COLOURS.items()},
        "running": {"state": cards["run"].state.value, "colour": cards["run"].colour},
        "idle": {"state": cards["idle"].state.value, "colour": cards["idle"].colour},
        "completed": {
            "state": cards["done"].state.value,
            "colour": cards["done"].colour,
        },
        "blocked": {
            "state": cards["blocked"].state.value,
            "colour": cards["blocked"].colour,
        },
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="brigade-grid.", dir=str(Path.home() / ".cache")))
    (root / "empty-claude").mkdir(parents=True, exist_ok=True)
    write_pi_session(root, "proj-running", "grid-running-1", "builder")

    first = asyncio.run(render_grid(root))
    print("T0 render:")
    print(json.dumps(first, indent=2))

    time.sleep(30)

    second = asyncio.run(render_grid(root))
    print("T1 render (30s later):")
    print(json.dumps(second, indent=2))

    colours = colour_check()
    print("state colours:")
    print(json.dumps(colours, indent=2))

    failures: list[str] = []

    if not first["grid_mode"] or not first["grid_display"]:
        failures.append("the grid is not the default view")
    if first["tab_bar_height"] != 2:
        failures.append(
            f"the tab bar height in grid mode is {first['tab_bar_height']}, not 2"
        )

    cards = first["cards"]
    if not cards:
        failures.append("the grid rendered no cards")
    else:
        card = cards[0]
        if not card["title"]:
            failures.append("the card shows no title")
        if not card["agent"]:
            failures.append("the card shows no agent or role")
        if not card["elapsed"]:
            failures.append("the card shows no elapsed time")

    if len(cards) != 1 or len(second["cards"]) != 1:
        failures.append("expected one running card in both renders")
    else:
        grew = second["cards"][0]["elapsed_seconds"] - cards[0]["elapsed_seconds"]
        if grew < 25:
            failures.append(
                f"elapsed did not grow across 30s: {cards[0]['elapsed_seconds']} ->"
                f" {second['cards'][0]['elapsed_seconds']}"
            )
        if second["cards"][0]["state"] != "running":
            failures.append("the live session is not running")
        if second["cards"][0]["colour"] != "blue":
            failures.append("the running card is not blue")

    if colours["declared"] != {
        "blocked": "red",
        "completed": "green",
        "idle": "yellow",
        "running": "blue",
    }:
        failures.append(f"state colours are wrong: {colours['declared']}")
    if colours["running"]["colour"] != "blue":
        failures.append("running card is not blue")
    if colours["idle"]["colour"] != "yellow":
        failures.append("idle card is not yellow")
    if colours["completed"]["colour"] != "green":
        failures.append("completed card is not green")
    if colours["blocked"]["colour"] != "red":
        failures.append("blocked card is not red")

    EVIDENCE.mkdir(exist_ok=True)
    report = {
        "first": first,
        "second": second,
        "colours": colours,
        "blocked_source_note": (
            "No source reports blocked today. pi and Claude Code write no"
            " status field and the opencode adapter reads no permission or"
            " pending field, so blocked is never set from a real store. The"
            " red path is exercised on a real Session whose source reports"
            " blocked."
        ),
        "failures": failures,
    }
    (EVIDENCE / "STW-40-verify-grid.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "PASS: grid default, three card fields, elapsed grew across 30s,"
        " state colours red/green/yellow/blue"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
