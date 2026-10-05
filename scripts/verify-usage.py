#!/usr/bin/env python3
"""Prove STW-53: pi and Claude transcripts carry real token and cost totals.

Read-only. It never writes to a source store.

Method, once per harness:

  1. Ask the real Brigade source for every session, and take the first one
     that reports tokens. pi and Claude read the real stores under
     ``PI_SESSION_DIR`` and ``CLAUDE_PROJECTS_DIR``.
  2. Find the real transcript file for that session id, independently of the
     source, by scanning the store tree.
  3. Scan that exact file with a second, independent parser. This is the
     transcript's own truth, not a restatement of the source code.
  4. Assert the source totals equal the independent totals, and that the row
     the TUI renders is not blank.

Usage:
    uv run python scripts/verify-usage.py

Exit status 0 when both harnesses match. Non-zero otherwise.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from brigade_tui.app import _cost, _tokens
from brigade_tui.sources.claude import ClaudeSessions
from brigade_tui.sources.opencode import OpencodeSessions
from brigade_tui.sources.pi import PiSessions


def iter_records(path: Path):
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def find_pi_file(root: Path, session_id: str) -> Path | None:
    for path in root.glob("*/*.jsonl"):
        for record in iter_records(path):
            if record.get("type") == "session" and record.get("id") == session_id:
                return path
            if record.get("type") == "session":
                break
    return None


def find_claude_file(root: Path, session_id: str) -> Path | None:
    for path in root.glob("*/*.jsonl"):
        for record in iter_records(path):
            if record.get("sessionId") == session_id:
                return path
            if record.get("sessionId") is not None:
                break
    return None


def scan_pi(path: Path) -> dict:
    input_tokens = output_tokens = cache_read = cache_write = 0
    cost = Decimal(0)
    has_cost = False
    for record in iter_records(path):
        if record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens += int(usage.get("input") or 0)
        output_tokens += int(usage.get("output") or 0)
        cache_read += int(usage.get("cacheRead") or 0)
        cache_write += int(usage.get("cacheWrite") or 0)
        cost_block = usage.get("cost")
        if isinstance(cost_block, dict) and cost_block.get("total") is not None:
            cost += Decimal(str(cost_block["total"]))
            has_cost = True
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
        "cost": str(cost) if has_cost else None,
    }


def scan_claude(path: Path) -> dict:
    input_tokens = output_tokens = cache_read = cache_write = 0
    seen: set[str] = set()
    for record in iter_records(path):
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if isinstance(message_id, str):
            if message_id in seen:
                continue
            seen.add(message_id)
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        cache_read += int(usage.get("cache_read_input_tokens") or 0)
        cache_write += int(usage.get("cache_creation_input_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
        "cost": None,
    }


def source_usage(session) -> dict:
    usage = session.usage
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "total_tokens": usage.total_tokens,
        "cost": str(usage.cost) if usage.cost is not None else None,
    }


def check(name: str, source, find_file, scanner, require_cost: bool) -> dict:
    sessions = source.snapshot()
    chosen = next((s for s in sessions if s.usage.total_tokens > 0), None)
    if chosen is None:
        raise SystemExit(f"{name}: no session with tokens in {source.root}")
    path = find_file(source.root, chosen.session_id)
    if path is None:
        raise SystemExit(f"{name}: no transcript file for {chosen.session_id}")
    expected = scanner(path)
    actual = source_usage(chosen)
    if actual != expected:
        raise SystemExit(
            f"{name}: totals differ\n  source:      {actual}\n  independent: {expected}"
        )
    rendered_tokens = _tokens(chosen.usage.total_tokens)
    rendered_cost = _cost(chosen.usage.cost)
    if rendered_tokens in ("", "-"):
        raise SystemExit(f"{name}: token cell is blank: {rendered_tokens!r}")
    if require_cost and rendered_cost in ("", "-"):
        raise SystemExit(f"{name}: cost cell is blank: {rendered_cost!r}")
    return {
        "harness": name,
        "session_id": chosen.session_id,
        "transcript": str(path),
        "source_totals": actual,
        "independent_totals": expected,
        "rendered_tokens": rendered_tokens,
        "rendered_cost": rendered_cost,
        "tokens_non_blank": rendered_tokens not in ("", "-"),
        "cost_non_blank": rendered_cost not in ("", "-"),
        "matches": actual == expected,
    }


def harness_matrix(opencode: OpencodeSessions) -> list[dict]:
    """One real session per harness, so the non-blank claim is countable."""
    row = []
    for name, source in (
        ("pi", PiSessions()),
        ("claude", ClaudeSessions()),
        ("opencode", opencode),
    ):
        chosen = next(
            (s for s in source.snapshot() if s.usage.total_tokens > 0), None
        )
        if chosen is None:
            row.append({"harness": name, "present": False})
            continue
        row.append(
            {
                "harness": name,
                "present": True,
                "session_id": chosen.session_id,
                "tokens_non_blank": _tokens(chosen.usage.total_tokens) not in ("", "-"),
                "cost_non_blank": _cost(chosen.usage.cost) not in ("", "-"),
            }
        )
    return row


def main() -> int:
    pi = PiSessions()
    claude = ClaudeSessions()
    opencode = OpencodeSessions()
    if not pi.exists() or not claude.exists():
        print(
            f"missing store: pi={pi.root} claude={claude.root}",
            file=sys.stderr,
        )
        return 2
    results = [
        check("pi", pi, find_pi_file, scan_pi, require_cost=True),
        check("claude", claude, find_claude_file, scan_claude, require_cost=False),
    ]
    matrix = harness_matrix(opencode)
    tokens_non_blank = sum(1 for row in matrix if row.get("tokens_non_blank"))
    cost_non_blank = sum(1 for row in matrix if row.get("cost_non_blank"))
    document = {
        "criterion": "STW-53 usage totals from pi and Claude transcripts",
        "stores": {
            "pi": str(pi.root),
            "claude": str(claude.root),
            "opencode": str(opencode.db),
        },
        "results": results,
        "harness_matrix": matrix,
        "tokens_non_blank_harnesses": tokens_non_blank,
        "cost_non_blank_harnesses": cost_non_blank,
    }
    out = Path(__file__).resolve().parents[1] / "evidence" / "STW-53-verify-usage.json"
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(
            f"{result['harness']}: {result['session_id']} "
            f"tokens={result['source_totals']['total_tokens']} "
            f"({result['rendered_tokens']}) "
            f"cost={result['rendered_cost']} "
            f"matches={result['matches']}"
        )
    for row in matrix:
        print(
            f"{row['harness']}: tokens_non_blank={row.get('tokens_non_blank')} "
            f"cost_non_blank={row.get('cost_non_blank')}"
        )
    print(f"tokens non-blank: {tokens_non_blank}/3, cost non-blank: {cost_non_blank}/3")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
