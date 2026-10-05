#!/usr/bin/env python3
"""Prove STW-44: the factory summary line and the per-project progress signal.

Two proofs, both driven through the real ``BrigadeTUI``:

  1. Real stores. Read the real pi, Claude Code and opencode stores once.
     Count every project and session independently, from the same model the
     app uses (``session_state``). Drive the app and read the rendered
     ``#factory-summary`` line. Every number must match the store counts:
     projects, running, idle, blocked, done and spend today.

  2. Blocked tab. A blocked state is never inferred from time. Only a source
     report sets it, and no shipped source reports one yet. So this probe
     takes the real newest session and adds one real-store clone that reports
     ``blocked``. The tab for that real project must carry the blocked word
     and the blocked colour in its label.

Read-only. It never writes to a source store.

Usage:
    uv run python scripts/verify-factory-summary.py

Exit status 0 when every check passes. Non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from textual.widgets import Static, TabPane, TabbedContent

from brigade_tui.app import BrigadeTUI
from brigade_tui.model import (
    SessionState,
    last_activity_at,
    session_state,
)
from brigade_tui.sources import default_sources
from brigade_tui.store import SessionStore
from brigade_tui.summary import is_today

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"

#: The rendered summary line, parsed into named counts.
SUMMARY_RE = re.compile(
    r"^(?P<projects>\d+) projects, "
    r"(?P<running>\d+) running, "
    r"(?P<idle>\d+) idle, "
    r"(?P<blocked>\d+) blocked, "
    r"(?P<done>\d+) done, "
    r"\$(?P<spend>[\d,]+\.\d{2}) today$"
)


class FrozenSource:
    """A real source whose snapshot is read once, then held."""

    def __init__(self, real, extra: list | None = None) -> None:
        self.real = real
        self.harness = real.harness
        self._extra = list(extra or [])
        self._snapshot: list | None = None

    def exists(self) -> bool:
        return self.real.exists()

    def snapshot(self) -> list:
        if self._snapshot is None:
            self._snapshot = list(self.real.snapshot()) + list(self._extra)
        return list(self._snapshot)


def independent_counts(pairs, now: datetime) -> dict:
    """Count every project and session from the snapshot, by hand."""
    sessions = [session for _project, group in pairs for session in group]
    counts = {state: 0 for state in SessionState}
    spend = Decimal(0)
    for session in sessions:
        counts[session_state(session, now)] += 1
        cost = session.usage.cost
        if cost is not None and is_today(last_activity_at(session), now):
            spend += cost
    return {
        "projects": len(pairs),
        "running": counts[SessionState.RUNNING],
        "idle": counts[SessionState.IDLE],
        "blocked": counts[SessionState.BLOCKED],
        "done": counts[SessionState.COMPLETED],
        "spend": spend,
        "sessions": len(sessions),
    }


async def rendered_summary(frozen: list[FrozenSource]) -> tuple[str, dict]:
    """Drive the real app and read the rendered summary line and tab labels."""
    app = BrigadeTUI(sources=frozen)
    async with app.run_test(size=(170, 55)) as pilot:
        await pilot.pause()
        line = app.query_one("#factory-summary", Static).render().plain
        content = app.query_one("#projects", TabbedContent)
        tabs = {
            app._pane_keys[index]: str(content.get_tab(pane).label)
            for index, pane in enumerate(app.query(TabPane))
        }
        return line, tabs


async def blocked_tab_probe(real_source, now: datetime) -> dict:
    """Add a blocked report to one real project and read the tab bar."""
    sessions = real_source.snapshot()
    if not sessions:
        return {"ok": False, "reason": "real store reported no sessions"}
    newest = max(sessions, key=lambda item: item.last_activity)
    blocked = replace(
        newest,
        session_id="stw-44-blocked-probe",
        title="blocked probe",
        reported_state="blocked",
        last_activity=now,
        run_started_at=None,
        run_ended_at=None,
        started_at=now,
    )
    frozen = FrozenSource(real_source, extra=[blocked])
    app = BrigadeTUI(sources=[frozen])
    async with app.run_test(size=(170, 55)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        target_key = newest.project.key
        index = app._pane_keys.index(target_key) if target_key in app._pane_keys else -1
        if index < 0:
            return {
                "ok": False,
                "reason": f"project {target_key!r} owns no tab",
            }
        label = content.get_tab(app.query(TabPane)[index]).label
        text = str(label)
        red = any("red" in str(span.style) for span in label.spans)
        return {
            "ok": True,
            "project_key": target_key,
            "label": text,
            "has_blocked_word": "blocked" in text,
            "blocked_colour": red,
        }


def main() -> int:
    now = datetime.now(timezone.utc)
    real = [FrozenSource(source) for source in default_sources()]
    sessions = [session for source in real for session in source.snapshot()]
    # The app groups by project and hides scratch sessions. Use the same store.
    pairs = SessionStore([]).by_project(sessions)
    expected = independent_counts(pairs, now)

    line, tabs = asyncio.run(rendered_summary(real))
    match = SUMMARY_RE.match(line)
    failures: list[str] = []

    if match is None:
        failures.append(f"summary line did not parse: {line!r}")
        rendered = {}
    else:
        rendered = {
            "projects": int(match.group("projects")),
            "running": int(match.group("running")),
            "idle": int(match.group("idle")),
            "blocked": int(match.group("blocked")),
            "done": int(match.group("done")),
            "spend": Decimal(match.group("spend").replace(",", "")),
        }
        for field in ("projects", "running", "idle", "blocked", "done"):
            if rendered[field] != expected[field]:
                failures.append(
                    f"summary {field} {rendered[field]} != store {expected[field]}"
                )
        if rendered["spend"] != expected["spend"].quantize(Decimal("0.01")):
            failures.append(
                f"summary spend {rendered['spend']} != store"
                f" {expected['spend'].quantize(Decimal('0.01'))}"
            )

    # Every rendered tab signal must name the real project size.
    size_by_key = {project.key: len(group) for project, group in pairs}
    for key, label in tabs.items():
        if key not in size_by_key:
            continue
        signal = re.search(r"(\d+)/(\d+)", label)
        if signal is None:
            failures.append(f"tab {label!r} has no progress signal")
        elif int(signal.group(2)) != size_by_key[key]:
            failures.append(
                f"tab {label!r} total {signal.group(2)} != store size"
                f" {size_by_key[key]}"
            )

    # The blocked tab probe.
    blocked = asyncio.run(blocked_tab_probe(real[0], now))
    if not blocked.get("ok"):
        failures.append(f"blocked probe failed: {blocked.get('reason')}")
    else:
        if not blocked["has_blocked_word"]:
            failures.append(
                f"blocked tab label lacks the word: {blocked['label']!r}"
            )
        if not blocked["blocked_colour"]:
            failures.append(
                f"blocked tab label lacks the blocked colour: {blocked['label']!r}"
            )

    report = {
        "store": "pi + claude + opencode (real)",
        "summary_line": line,
        "store_counts": {**expected, "spend": str(expected["spend"])},
        "rendered_counts": {
            **rendered,
            "spend": str(rendered.get("spend", "")),
        },
        "tabs_rendered": len(tabs),
        "tab_labels": tabs,
        "blocked_probe": blocked,
        "matches": not failures,
        "failures": failures,
    }
    EVIDENCE.mkdir(exist_ok=True)
    out = EVIDENCE / "STW-44-factory-summary.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"PASS: summary {line!r} matches the store counts;"
        f" blocked tab {blocked['label']!r} is visible from the tab bar"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
