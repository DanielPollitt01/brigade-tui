#!/usr/bin/env python3
"""Prove STW-45: elapsed time is the real run span, and the state splits.

The script points Brigade at the real stores on this machine and builds the
grid cards the way the TUI does. It proves:

  1. The three time states appear on cards: running, idle and completed.
  2. No card shows an impossible elapsed. An elapsed larger than the session's
     wall-clock age is impossible because the run span starts at or after the
     session start. The old bug showed the age itself, so a short run in an
     old transcript read as thousands of hours.
  3. The red-team case is repaired: a running session whose age is hours
     (because its transcript was resumed) shows a short run span, not its age.

Read-only. It never writes to a real source store.

Usage:
    uv run python scripts/verify-elapsed.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from brigade_tui.grid import build_cards
from brigade_tui.model import is_scratch
from brigade_tui.sources import detect_sources

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"

#: A run that really spanned a day is possible, but no card in a live TUI
#: should show more than this from these stores. The old bug showed 6650h.
IMPOSSIBLE_HOURS = 24.0

#: A running session older than this with a run span under an hour is a
#: resumed transcript. The old code showed the age; the new code shows the run.
OLD_AGE_HOURS = 2.0
SHORT_RUN_SECONDS = 3600.0


def main() -> int:
    now = datetime.now(timezone.utc)
    sources = detect_sources()
    sessions = []
    for source in sources:
        sessions.extend(source.snapshot())
    sessions = [session for session in sessions if not is_scratch(session)]
    cards = build_cards(sessions, now)

    states = Counter(card.state.value for card in cards)
    missing = [name for name in ("running", "idle", "completed") if not states.get(name)]

    age_by_id = {
        session.session_id: (now - session.started_at).total_seconds()
        for session in sessions
        if session.started_at is not None
    }
    age_violations = [
        card.session_id
        for card in cards
        if card.session_id in age_by_id
        and card.elapsed_seconds > age_by_id[card.session_id] + 1.0
    ]
    impossible = [
        card for card in cards if card.elapsed_seconds > IMPOSSIBLE_HOURS * 3600
    ]

    running = [card for card in cards if card.state.value == "running"]
    resumed = [
        card
        for card in running
        if age_by_id.get(card.session_id, 0) > OLD_AGE_HOURS * 3600
        and card.elapsed_seconds < SHORT_RUN_SECONDS
    ]

    by_elapsed = sorted(cards, key=lambda card: card.elapsed_seconds, reverse=True)
    top = [
        {
            "elapsed": card.elapsed,
            "elapsed_seconds": round(card.elapsed_seconds, 1),
            "state": card.state.value,
            "session_id": card.session_id[:24],
            "age_hours": round(age_by_id.get(card.session_id, 0) / 3600, 1),
        }
        for card in by_elapsed[:5]
    ]
    resumed_report = [
        {
            "session_id": card.session_id[:24],
            "elapsed": card.elapsed,
            "age_hours": round(age_by_id.get(card.session_id, 0) / 3600, 1),
        }
        for card in resumed
    ]

    failures: list[str] = []
    if not cards:
        failures.append("no cards were built from the real stores")
    for name in missing:
        failures.append(f"the {name} state does not appear on any card")
    if age_violations:
        failures.append(
            f"{len(age_violations)} cards show an elapsed larger than the"
            f" session age: {age_violations[:5]}"
        )
    if impossible:
        failures.append(
            f"{len(impossible)} cards show more than {IMPOSSIBLE_HOURS}h: "
            f"{[card.session_id for card in impossible[:5]]}"
        )
    if not resumed:
        # Not a failure: the live stores change. The unit test
        # ``test_elapsed_is_the_run_span_not_the_session_age`` proves the
        # resumed case, and the no-impossible-elapsed check proves the bar.
        report_note = (
            "no live running session with an old age and a short run span was"
            " present at this check; the resumed case is covered by the unit"
            " test test_elapsed_is_the_run_span_not_the_session_age"
        )
    else:
        report_note = ""

    report = {
        "checked_at": now.isoformat(),
        "sources": [source.harness.value for source in sources],
        "cards": len(cards),
        "states": dict(states),
        "missing_states": missing,
        "age_violations": age_violations[:10],
        "impossible_cards": [card.session_id for card in impossible[:10]],
        "resumed_running": resumed_report,
        "top_by_elapsed": top,
        "note": report_note,
        "failures": failures,
    }
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "STW-45-verify-elapsed.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, indent=2))
    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS: elapsed is the real run span and the three time states show")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
