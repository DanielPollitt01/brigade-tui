#!/usr/bin/env python3
"""Prove git worktrees fold into the owning project.

Runs against the real session stores on this machine (the ones Brigade reads
by default). It proves three things:

  1. no ``.../worktrees/<name>`` path owns a tab
  2. the owning project shows the worktree sessions
  3. the grid renders a project heading

Read-only. It never writes to a source store.

Usage:
    python scripts/verify-worktree-fold.py

Exit status 0 when every check passes, non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from brigade_tui.app import BrigadeTUI
from brigade_tui.grid import SessionGrid, heading_text
from brigade_tui.model import is_scratch
from brigade_tui.sources import default_sources
from brigade_tui.sources.base import resolve_repo_root
from brigade_tui.store import MAX_TABS, SessionStore, unique_labels

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"
EVIDENCE_FILE = EVIDENCE / "ITEM-42-worktree-fold.txt"

#: The git worktree layout: ``.../worktrees/<name>``.
WORKTREE_RE = re.compile(r"/worktrees/[^/]+")

#: The project this worktree belongs to. Its sessions fold here.
OWNER_KEY = "/home/user/Workspace/work/software-factory-tui"


def is_worktree_session(session) -> bool:
    """True when the session's cwd is a git worktree, not a normal checkout."""
    directory = session.directory or ""
    if not directory:
        return False
    return resolve_repo_root(directory) != directory


def collect() -> dict:
    store = SessionStore(default_sources())
    sessions = store.refresh()
    pairs = store.by_project(sessions)
    labels = unique_labels(pairs)
    tabs = list(zip(pairs, labels))[:MAX_TABS]

    worktree_sessions = [s for s in sessions if is_worktree_session(s)]
    folded = [
        s for s in worktree_sessions if s.project.key != s.directory
    ]
    # Any session whose project key is a worktree path. A folded worktree has
    # the owner as its key, so only unattributed debris is left here.
    worktree_key = [
        s for s in sessions if WORKTREE_RE.search(s.project.key or "")
    ]
    visible_worktree_key = [s for s in worktree_key if not is_scratch(s)]
    owner = next((pair for pair in pairs if pair[0].key == OWNER_KEY), None)
    owner_worktree_sessions = (
        [s for s in owner[1] if is_worktree_session(s)] if owner else []
    )
    return {
        "sessions": len(sessions),
        "projects": len(pairs),
        "tabs": [
            {
                "label": label,
                "key": project.key,
                "sessions": len(group),
                "owns_tab": True,
            }
            for (project, group), label in tabs
        ],
        "worktree_sessions": len(worktree_sessions),
        "worktree_folded": len(folded),
        "worktree_debris_hidden": len(worktree_key),
        "visible_worktree_keys": len(visible_worktree_key),
        "owner_key": OWNER_KEY,
        "owner_sessions": len(owner[1]) if owner else 0,
        "owner_worktree_sessions": len(owner_worktree_sessions),
    }


async def render_grid_probe() -> str:
    """Drive the real app headless and read the rendered grid text."""
    app = BrigadeTUI()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        await app.refresh_sessions()
        await pilot.pause()
        grid = app.query_one("#grid", SessionGrid)
        return grid.render().plain


def main() -> int:
    data = collect()
    grid_text = asyncio.run(render_grid_probe())

    owner_heading = heading_text("software-factory-tui", data["owner_sessions"])
    owner_heading_present = owner_heading in grid_text

    failures: list[str] = []
    if data["visible_worktree_keys"]:
        failures.append(
            f"{data['visible_worktree_keys']} sessions still key to a visible "
            "worktree path"
        )
    for tab in data["tabs"]:
        if WORKTREE_RE.search(tab["key"]):
            failures.append(f"tab '{tab['label']}' owns worktree path {tab['key']}")
    if not data["owner_worktree_sessions"]:
        failures.append(
            f"owner {OWNER_KEY} shows no worktree sessions"
        )
    if not owner_heading_present:
        failures.append(
            f"grid has no project heading '{owner_heading}'"
        )

    lines: list[str] = []
    lines.append("== ITEM-42 worktree fold proof ==")
    lines.append(f"sessions: {data['sessions']}")
    lines.append(f"projects: {data['projects']}")
    lines.append("")
    lines.append("== tabs (top %d) ==" % MAX_TABS)
    for tab in data["tabs"]:
        check = "FAIL" if WORKTREE_RE.search(tab["key"]) else "ok"
        lines.append(
            f"{check} {tab['label']}  key={tab['key']}  sessions={tab['sessions']}"
        )
    lines.append("")
    lines.append("== fold ==")
    lines.append(f"git worktree sessions in store: {data['worktree_sessions']}")
    lines.append(f"folded onto an owner: {data['worktree_folded']}")
    lines.append(f"unattributed worktree keys hidden: {data['worktree_debris_hidden']}")
    lines.append(f"visible worktree keys: {data['visible_worktree_keys']}")
    lines.append(f"owner: {data['owner_key']}")
    lines.append(f"owner sessions: {data['owner_sessions']}")
    lines.append(f"owner worktree sessions: {data['owner_worktree_sessions']}")
    lines.append("")
    lines.append("== grid heading ==")
    lines.append(f"expected: {owner_heading}")
    lines.append("present: " + ("yes" if owner_heading_present else "no"))
    lines.append("")
    lines.append("== grid headings rendered ==")
    for line in grid_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("▌ "):
            lines.append(stripped)
    lines.append("")
    if failures:
        lines.append("== RESULT: FAIL ==")
        for failure in failures:
            lines.append(f"- {failure}")
    else:
        lines.append("== RESULT: PASS ==")

    report = "\n".join(lines) + "\n"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(report, encoding="utf-8")
    print(report)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
