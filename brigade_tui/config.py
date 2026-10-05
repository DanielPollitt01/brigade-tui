"""Brigade configuration: defaults, a config file, CLI flags and env vars.

One resolved config feeds the app, so the roots and view options the app
really used are known in one place. Precedence, highest first:

1. environment variables: ``PI_SESSION_DIR``, ``CLAUDE_PROJECTS_DIR``,
   ``OPENCODE_DB``
2. CLI flags: ``--pi-dir``, ``--claude-dir``, ``--opencode-db``,
   ``--refresh``, ``--running-window``, ``--waterfall-window``
3. the config file: ``~/.config/brigade/config.toml``, or ``--config``
4. built-in defaults

The config file is TOML::

    [sources]
    pi = "/tmp/pi"
    claude = "/tmp/claude"
    opencode = "/tmp/opencode.db"

    [view]
    refresh = 5
    running_window = 300
    waterfall_window = 900
"""

from __future__ import annotations

import argparse
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .sources.claude import default_root as _claude_default
from .sources.opencode import default_db as _opencode_default
from .sources.pi import default_root as _pi_default

#: Where Brigade reads its config unless ``--config`` names another file.
DEFAULT_CONFIG_PATH = "~/.config/brigade/config.toml"

#: Seconds between session polls.
DEFAULT_REFRESH_SECONDS = 3.0

#: The env var names, lowest effort to override, highest priority.
PI_ENV = "PI_SESSION_DIR"
CLAUDE_ENV = "CLAUDE_PROJECTS_DIR"
OPENCODE_ENV = "OPENCODE_DB"


class ConfigError(ValueError):
    """A config file or flag value Brigade cannot use."""


@dataclass(frozen=True)
class SourceRoots:
    """The resolved source root for each harness."""

    pi: str
    claude: str
    opencode: str


@dataclass(frozen=True)
class ViewOptions:
    """The resolved view options. Times are seconds."""

    refresh: float
    running_window: float
    waterfall_window: float


@dataclass(frozen=True)
class BrigadeConfig:
    """One resolved config: where to read, and how to show it."""

    sources: SourceRoots
    view: ViewOptions
    #: The config file that was read, or the path that was looked for.
    config_path: str = ""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the ``brigade`` command line."""
    parser = argparse.ArgumentParser(
        prog="brigade",
        description="Read-only terminal view over a running software factory.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"config file to read (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--pi-dir",
        default=None,
        help=f"pi session root (default: {PI_ENV} or ~/.pi/agent/sessions)",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help=(
            "Claude Code projects root "
            f"(default: {CLAUDE_ENV} or ~/.claude/projects)"
        ),
    )
    parser.add_argument(
        "--opencode-db",
        default=None,
        help=(
            "opencode SQLite store "
            f"(default: {OPENCODE_ENV} or ~/.local/share/opencode/opencode.db)"
        ),
    )
    parser.add_argument(
        "--refresh",
        type=_positive,
        default=None,
        help="seconds between session polls (default: 3)",
    )
    parser.add_argument(
        "--running-window",
        type=_positive,
        default=None,
        help="seconds a session counts as running (default: 120)",
    )
    parser.add_argument(
        "--waterfall-window",
        type=_positive,
        default=None,
        help="seconds the waterfall shows (default: 600)",
    )
    return parser.parse_args(argv)


def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def load_config(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> BrigadeConfig:
    """Resolve the config from the file, the CLI flags and the env vars.

    ``env`` defaults to ``os.environ``. ``config_path`` overrides both the
    file named by ``--config`` and the default path, for a caller that
    already knows where to look.
    """
    args = parse_args(argv)
    environ = os.environ if env is None else env
    path = _config_path(args.config, config_path)
    data = read_config_file(path)

    roots = SourceRoots(
        pi=_resolve_root(
            environ, PI_ENV, args.pi_dir, _file_value(data, "sources", "pi"), _pi_default()
        ),
        claude=_resolve_root(
            environ,
            CLAUDE_ENV,
            args.claude_dir,
            _file_value(data, "sources", "claude"),
            _claude_default(),
        ),
        opencode=_resolve_root(
            environ,
            OPENCODE_ENV,
            args.opencode_db,
            _file_value(data, "sources", "opencode"),
            _opencode_default(),
        ),
    )
    view = ViewOptions(
        refresh=_resolve_number(
            args.refresh,
            _file_value(data, "view", "refresh"),
            DEFAULT_REFRESH_SECONDS,
        ),
        running_window=_resolve_number(
            args.running_window,
            _file_value(data, "view", "running_window"),
            _running_default(),
        ),
        waterfall_window=_resolve_number(
            args.waterfall_window,
            _file_value(data, "view", "waterfall_window"),
            _waterfall_default(),
        ),
    )
    return BrigadeConfig(sources=roots, view=view, config_path=str(path))


def read_config_file(path: Path) -> dict:
    """Read a TOML config file. A missing file is an empty config."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"cannot read config {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"config {path} is not a TOML table")
    return data


def _config_path(cli_value: str | None, override: str | Path | None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    if cli_value:
        return Path(cli_value).expanduser()
    return Path(DEFAULT_CONFIG_PATH).expanduser()


def _file_value(data: dict, section: str, key: str) -> object:
    """Read ``[section] key``, dropping to a top-level ``key`` when absent."""
    block = data.get(section)
    if isinstance(block, dict) and key in block:
        return block[key]
    return data.get(key)


def _resolve_root(
    environ: Mapping[str, str],
    env_name: str,
    cli_value: str | None,
    file_value: object,
    default: Path,
) -> str:
    """One root by precedence: env, then CLI, then file, then default."""
    for value in (environ.get(env_name), cli_value, file_value):
        if value:
            return str(Path(str(value)).expanduser())
    return str(Path(default).expanduser())


def _resolve_number(
    cli_value: float | None, file_value: object, default: float
) -> float:
    """One view number by precedence: CLI, then file, then default."""
    if cli_value is not None:
        return float(cli_value)
    if file_value is not None:
        try:
            return float(file_value)
        except (TypeError, ValueError) as error:
            raise ConfigError(f"view option is not a number: {file_value!r}") from error
    return float(default)


def _running_default() -> float:
    from .model import RUNNING_WINDOW_SECONDS

    return RUNNING_WINDOW_SECONDS


def _waterfall_default() -> float:
    from .timeline import WINDOW_SECONDS

    return WINDOW_SECONDS
