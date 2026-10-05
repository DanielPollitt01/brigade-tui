#!/usr/bin/env python3
"""Prove config file, CLI flags, and the dead source path removal.

It proves three things, each against a real temp store and the real app:

  1. a config file names a source root and Brigade reads that root
  2. a CLI flag overrides the config file
  3. no dead source path remains in the code

It also checks the env var keeps the highest priority, and that a view
option from the config and a CLI flag reach the app.

Read-only. It never writes to a real source store.

Usage:
    python scripts/verify-config.py

Exit status 0 when every check passes, non-zero otherwise.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from textual.widgets import DataTable

from brigade_tui.app import BrigadeTUI
from brigade_tui.config import load_config
from brigade_tui.sources import sources_from_roots

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "evidence"
EVIDENCE_FILE = EVIDENCE / "ITEM-54-config.txt"
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def make_pi_root(root: Path, name: str, session_id: str) -> Path:
    """Copy the real pi fixture under ``name`` with a distinct session id."""
    destination = root / name
    shutil.copytree(FIXTURES / "pi", destination)
    for path in destination.glob("*/*.jsonl"):
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for record in records:
            if record.get("type") == "session":
                record["id"] = session_id
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
    return destination


def build_sources(config, sandbox: Path):
    """Build the three sources, pointing the unused two at empty paths."""
    return sources_from_roots(
        config.sources.pi,
        sandbox / "no-claude",
        sandbox / "no-opencode.db",
    )


async def read_tui_session_ids(config, sandbox: Path) -> list[str]:
    """Drive the real app headless and read the session ids it shows."""
    app = BrigadeTUI(
        sources=build_sources(config, sandbox),
        refresh_seconds=config.view.refresh,
        running_window=config.view.running_window,
        waterfall_window=config.view.waterfall_window,
    )
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        await app.refresh_sessions()
        await pilot.pause()
        rows = [
            table.get_row(row_key)
            for table in app.query(DataTable)
            for row_key in table.rows
        ]
    # The session label is the last column of every rendered row.
    return [str(row[-1]) for row in rows]


def find_dead_path() -> list[str]:
    """Every Python file under the package that names ``detect_sources``."""
    hits: list[str] = []
    for path in (REPO_ROOT / "brigade_tui").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "detect_sources" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    return hits


def run() -> int:
    failures: list[str] = []
    lines: list[str] = ["== ITEM-54 config and CLI proof ==", ""]

    with tempfile.TemporaryDirectory() as raw:
        sandbox = Path(raw)
        config_root = make_pi_root(sandbox, "config-pi", "cfg-root")
        cli_root = make_pi_root(sandbox, "cli-pi", "cli-root")
        env_root = make_pi_root(sandbox, "env-pi", "env-root")
        config_file = sandbox / "config.toml"
        config_file.write_text(
            "[sources]\n"
            f'pi = "{config_root}"\n'
            "\n"
            "[view]\n"
            "refresh = 7\n"
            "running_window = 300\n"
            "waterfall_window = 900\n",
            encoding="utf-8",
        )
        clean_env = {"PATH": "/usr/bin"}

        # 1. The config file sets the root, and Brigade reads it.
        from_file = load_config(
            ["--config", str(config_file)], env=clean_env
        )
        file_ids = asyncio.run(read_tui_session_ids(from_file, sandbox))
        lines.append("== 1. config file sets a source root ==")
        lines.append(f"config file: {config_file}")
        lines.append(f"pi root from config: {from_file.sources.pi}")
        lines.append(f"expected root:       {config_root}")
        lines.append(f"TUI session ids:     {file_ids}")
        if from_file.sources.pi != str(config_root):
            failures.append("config file did not set the pi root")
        if "cfg-root" not in file_ids:
            failures.append("Brigade did not read the config file pi root")

        # 2. A CLI flag overrides the config file.
        from_cli = load_config(
            ["--config", str(config_file), "--pi-dir", str(cli_root)],
            env=clean_env,
        )
        cli_ids = asyncio.run(read_tui_session_ids(from_cli, sandbox))
        lines.append("")
        lines.append("== 2. CLI flag overrides the config file ==")
        lines.append(f"pi root from config: {from_file.sources.pi}")
        lines.append(f"pi root from --pi-dir: {from_cli.sources.pi}")
        lines.append(f"expected root:         {cli_root}")
        lines.append(f"TUI session ids:       {cli_ids}")
        if from_cli.sources.pi != str(cli_root):
            failures.append("--pi-dir did not override the config file")
        if "cli-root" not in cli_ids:
            failures.append("Brigade did not read the --pi-dir root")

        # 3. The env var keeps the highest priority.
        from_env = load_config(
            ["--config", str(config_file), "--pi-dir", str(cli_root)],
            env={"PI_SESSION_DIR": str(env_root)},
        )
        lines.append("")
        lines.append("== 3. env var outranks the CLI flag ==")
        lines.append(f"env PI_SESSION_DIR: {env_root}")
        lines.append(f"resolved pi root:   {from_env.sources.pi}")
        if from_env.sources.pi != str(env_root):
            failures.append("env var did not outrank the CLI flag")

        # View options from the file, then overridden by a flag.
        override = load_config(
            ["--config", str(config_file), "--refresh", "11"], env=clean_env
        )
        lines.append("")
        lines.append("== 4. view options ==")
        lines.append(
            f"from file: refresh={from_file.view.refresh}"
            f" running_window={from_file.view.running_window}"
            f" waterfall_window={from_file.view.waterfall_window}"
        )
        lines.append(f"refresh after --refresh 11: {override.view.refresh}")
        if from_file.view.waterfall_window != 900:
            failures.append("waterfall_window from the config file did not apply")
        if override.view.refresh != 11:
            failures.append("--refresh did not override the config file")

    # 5. No dead source path.
    dead = find_dead_path()
    lines.append("")
    lines.append("== 5. dead source path ==")
    lines.append(f"files naming detect_sources: {dead or 'none'}")
    if dead:
        failures.append(f"dead detect_sources remains in {dead}")

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
    print(report, end="")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
