"""Config file, CLI flags and env var tests.

These read real fixture transcripts and real temp TOML files. No mocks.
They prove the three config paths resolve in the documented order:

    env var > CLI flag > config file > built-in default

and that the app reads the root the config names.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from brigade_tui.config import (
    DEFAULT_REFRESH_SECONDS,
    ConfigError,
    load_config,
    parse_args,
    read_config_file,
)
from brigade_tui.sources import sources_from_roots
from brigade_tui.store import SessionStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_pi_root(tmp_path: Path, name: str, session_id: str) -> Path:
    """Copy the real pi fixture to ``tmp_path/name`` under a new session id."""
    root = tmp_path / name
    shutil.copytree(FIXTURES / "pi", root)
    for path in root.glob("*/*.jsonl"):
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
    return root


def write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def clear_env() -> dict[str, str]:
    """An env with no Brigade source var set."""
    return {"PATH": "/usr/bin"}


def test_config_file_sets_source_root_and_brigade_reads_it(tmp_path) -> None:
    """A config file names the pi root, and the source reads that root."""
    config_root = make_pi_root(tmp_path, "config-pi", "config-file-session")
    config_file = write_config(
        tmp_path / "config.toml",
        f'[sources]\npi = "{config_root}"\n',
    )

    config = load_config([], env=clear_env(), config_path=config_file)
    assert config.sources.pi == str(config_root)

    sessions = SessionStore(
        sources_from_roots(
            config.sources.pi,
            tmp_path / "no-claude",
            tmp_path / "no-opencode.db",
        )
    ).refresh()
    assert [session.session_id for session in sessions] == ["config-file-session"]


def test_cli_flag_overrides_config_file(tmp_path) -> None:
    """``--pi-dir`` wins over the pi root in the config file."""
    config_root = make_pi_root(tmp_path, "config-pi", "from-config")
    cli_root = make_pi_root(tmp_path, "cli-pi", "from-cli")
    config_file = write_config(
        tmp_path / "config.toml",
        f'[sources]\npi = "{config_root}"\n',
    )

    config = load_config(
        ["--config", str(config_file), "--pi-dir", str(cli_root)],
        env=clear_env(),
    )
    assert config.sources.pi == str(cli_root)

    sessions = SessionStore(
        sources_from_roots(
            config.sources.pi,
            tmp_path / "no-claude",
            tmp_path / "no-opencode.db",
        )
    ).refresh()
    assert [session.session_id for session in sessions] == ["from-cli"]


def test_env_var_outranks_cli_flag_and_config_file(tmp_path) -> None:
    """The env var keeps the highest priority, as documented."""
    config_root = make_pi_root(tmp_path, "config-pi", "from-config")
    cli_root = make_pi_root(tmp_path, "cli-pi", "from-cli")
    env_root = make_pi_root(tmp_path, "env-pi", "from-env")
    config_file = write_config(
        tmp_path / "config.toml",
        f'[sources]\npi = "{config_root}"\n',
    )

    config = load_config(
        ["--config", str(config_file), "--pi-dir", str(cli_root)],
        env={"PI_SESSION_DIR": str(env_root)},
    )
    assert config.sources.pi == str(env_root)


def test_view_options_come_from_config_file_then_cli(tmp_path) -> None:
    """View options resolve from the file, and a CLI flag overrides one."""
    config_file = write_config(
        tmp_path / "config.toml",
        "[view]\nrefresh = 7\nrunning_window = 300\nwaterfall_window = 900\n",
    )

    from_file = load_config(["--config", str(config_file)], env=clear_env())
    assert from_file.view.refresh == 7
    assert from_file.view.running_window == 300
    assert from_file.view.waterfall_window == 900

    overridden = load_config(
        ["--config", str(config_file), "--refresh", "11"],
        env=clear_env(),
    )
    assert overridden.view.refresh == 11
    # The other file values stay.
    assert overridden.view.running_window == 300
    assert overridden.view.waterfall_window == 900


def test_missing_config_file_uses_defaults(tmp_path) -> None:
    """No config file is not an error: the built-in defaults apply."""
    config = load_config([], env=clear_env(), config_path=tmp_path / "nope.toml")
    assert config.view.refresh == DEFAULT_REFRESH_SECONDS
    assert config.sources.pi.endswith(".pi/agent/sessions")


def test_bad_config_file_names_the_path(tmp_path) -> None:
    """A malformed TOML file fails loudly and names the path."""
    bad = write_config(tmp_path / "bad.toml", "not = [valid\n")

    with pytest.raises(ConfigError) as caught:
        read_config_file(bad)
    assert str(bad) in str(caught.value)


def test_rejects_a_non_positive_view_number() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--refresh", "0"])


def test_detect_sources_is_gone() -> None:
    """The dead source path is removed, not left beside the live one."""
    import brigade_tui.sources as sources

    assert not hasattr(sources, "detect_sources")
    text = (Path(sources.__file__)).read_text(encoding="utf-8")
    assert "detect_sources" not in text
