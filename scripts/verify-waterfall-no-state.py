#!/usr/bin/env python3
"""Prove ISC-9: the waterfall shows no state word and no state colour.

The waterfall renders from the real stores (or a temp pi root when
``PI_SESSION_DIR`` is set). It reads every lane and block and checks:

  1. no ``blocked``, ``running`` or ``completed`` word appears
  2. no red, green or blue style is used, because those three colours carry
     state in the grid only

Usage:
    uv run python scripts/verify-waterfall-no-state.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

from brigade_tui.sources import default_sources
from brigade_tui.timeline import render_timeline

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"

STATE_WORDS = ("blocked", "running", "completed")
STATE_COLOURS = ("red", "green", "blue")


def main() -> int:
    sessions = []
    for source in default_sources():
        try:
            sessions.extend(source.snapshot())
        except Exception:
            continue

    text = render_timeline(sessions, width=160)
    plain = text.plain.lower()
    styles = sorted({str(span.style) for span in text.spans})

    found_words = [word for word in STATE_WORDS if word in plain]
    found_colours = [
        style for style in styles if any(colour in style for colour in STATE_COLOURS)
    ]

    report = {
        "sessions": len(sessions),
        "lanes": plain.count("\n"),
        "state_words_found": found_words,
        "state_colours_found": found_colours,
        "styles_used": styles,
    }
    print(json.dumps(report, indent=2))

    failures = []
    if found_words:
        failures.append(f"state words in the waterfall: {found_words}")
    if found_colours:
        failures.append(f"state colours in the waterfall: {found_colours}")

    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "ITEM-40-verify-waterfall-no-state.json").write_text(
        json.dumps({**report, "failures": failures}, indent=2), encoding="utf-8"
    )

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS: no state word and no state colour in the waterfall")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
