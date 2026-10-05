#!/usr/bin/env python3
"""Prove the source health strip with path, open mode and count.

Read-only. It never writes to a source store.

Method:

  1. Point Brigade at the real stores (the three env vars default to the
     machine's pi, Claude Code and opencode stores) and poll.
  2. Assert every enabled source appears with its real store path, its open
     mode and a count, and that the count equals the sessions that source
     contributed to the same poll.
  3. Drive the real app headless and read the rendered strip from the widget.
  4. Point one source at a missing store. Assert it shows ``unavailable``,
     not a bare zero. This is the silent-failure case.

Usage:
    uv run python scripts/verify-source-health.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from textual.widgets import Static

from brigade_tui.app import BrigadeTUI
from brigade_tui.health import render_health_strip
from brigade_tui.sources import default_sources
from brigade_tui.store import SessionStore

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"


def poll_health() -> tuple[list, list, dict[str, int]]:
    """Poll the configured stores once. Return sessions, health and counts."""
    store = SessionStore(default_sources())
    sessions, health = store.poll()
    counts = {item.harness: item.count for item in health}
    return sessions, health, counts


async def render_app_strip() -> str:
    """Drive the real app headless and read the strip the user would see."""
    app = BrigadeTUI()
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause()
        strip = app.query_one("#source-health", Static)
        rendered = strip.render()
        text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        await pilot.pause()
    return text


def main() -> int:
    failures: list[str] = []
    saved = os.environ.get("OPENCODE_DB")
    try:
        sessions, health, counts = poll_health()
        by_name = {item.harness: item for item in health}
        expected_names = {"pi", "claude", "opencode"}
        if set(by_name) != expected_names:
            failures.append(f"strip does not list all three sources: {sorted(by_name)}")

        # The count must equal what the same poll contributed for that source.
        for name in expected_names:
            contributed = sum(1 for s in sessions if s.harness.value == name)
            item = by_name.get(name)
            if item is None:
                continue
            if item.count != contributed:
                failures.append(
                    f"{name}: health count {item.count} != contributed {contributed}"
                )
            if not item.path:
                failures.append(f"{name}: no store path")
            if not item.open_mode:
                failures.append(f"{name}: no open mode")
            if not item.available:
                failures.append(f"{name}: real store reported unavailable")

        strip_text = asyncio.run(render_app_strip())
        for name in expected_names:
            if name not in strip_text:
                failures.append(f"rendered app strip omits {name}")

        # A missing store must read as unavailable, never as zero silence.
        os.environ["OPENCODE_DB"] = str(
            Path(REPO_ROOT) / "evidence" / "no-such-opencode.db"
        )
        _sessions, missing_health, _counts = poll_health()
        missing = {item.harness: item for item in missing_health}
        opencode = missing["opencode"]
        if opencode.available:
            failures.append("a missing opencode store reported available")
        if opencode.status != "unavailable":
            failures.append(
                f"a missing store shows {opencode.status!r}, not 'unavailable'"
            )
        missing_text = render_health_strip(missing_health).plain
        if "unavailable" not in missing_text:
            failures.append("the strip does not render the missing store as unavailable")
        if "0 session" in missing_text:
            failures.append("the strip renders a missing store as a zero count")

        document = {
            "criterion": "ITEM-46 source health strip with path, open mode and count",
            "real_stores": [
                {
                    "harness": item.harness,
                    "path": item.path,
                    "open_mode": item.open_mode,
                    "available": item.available,
                    "count": item.count,
                    "status": item.status,
                }
                for item in health
            ],
            "poll_session_total": len(sessions),
            "rendered_strip": strip_text,
            "missing_store": {
                "harness": opencode.harness,
                "path": opencode.path,
                "available": opencode.available,
                "status": opencode.status,
                "rendered_strip": missing_text,
            },
            "failures": failures,
        }
        EVIDENCE.mkdir(exist_ok=True)
        out = EVIDENCE / "ITEM-46-source-health.json"
        out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    finally:
        if saved is None:
            os.environ.pop("OPENCODE_DB", None)
        else:
            os.environ["OPENCODE_DB"] = saved

    print("real source health:")
    for item in health:
        print(
            f"  {item.harness:8} {item.path}  [{item.open_mode}]  "
            f"{item.status}  available={item.available}"
        )
    print("rendered strip:")
    print(f"  {strip_text}")
    print("missing store:")
    print(f"  {missing_text}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASS: three sources listed, counts match, missing store unavailable")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
