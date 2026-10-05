"""Read-only source tests against real fixture stores in a temp root.

These are not mocks. They copy a real pi transcript, a real Claude Code
transcript and a real opencode SQLite store into a temp root, point the source
env vars at the copy, and prove Brigade reads them without changing them. No
pi, Claude Code or opencode install is required on the machine.

Rebuild the fixtures with ``python3 scripts/make-source-fixtures.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from textual.widgets import DataTable, Static

from brigade_tui.app import BrigadeTUI, session_cells
from brigade_tui.health import render_health_strip
from brigade_tui.model import Harness
from brigade_tui.sources import (
    ClaudeSessions,
    OpencodeSessions,
    PiSessions,
    default_sources,
)
from brigade_tui.sources.base import project_from_directory
from brigade_tui.store import SessionStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PI_ID = "pi-fixture-session"
CLAUDE_ID = "claude-fixture-session"
OPENCODE_ID = "ses_fixture_opencode"


def seed_fixture_root(tmp_path: Path) -> Path:
    """Copy every fixture store into a temp root. Return the root."""
    shutil.copytree(FIXTURES / "pi", tmp_path / "pi")
    shutil.copytree(FIXTURES / "claude", tmp_path / "claude")
    shutil.copytree(FIXTURES / "opencode", tmp_path / "opencode")
    return tmp_path


def point_env_at_fixtures(monkeypatch, root: Path) -> None:
    """Point the three source env vars at the temp root."""
    monkeypatch.setenv("PI_SESSION_DIR", str(root / "pi"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(root / "claude"))
    monkeypatch.setenv("OPENCODE_DB", str(root / "opencode" / "opencode.db"))


def test_pi_sessions_read_from_env_root(tmp_path, monkeypatch) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    source = PiSessions()
    sessions = source.snapshot()
    assert [session.session_id for session in sessions] == [PI_ID]
    first = sessions[0]
    assert first.harness is Harness.PI
    assert first.last_activity.tzinfo is not None
    assert isinstance(first.last_activity, datetime)
    assert first.model == "fixture-provider/fixture-model"


def test_pi_usage_totals_sum_every_assistant_turn(tmp_path, monkeypatch) -> None:
    """pi usage is the sum over every assistant record, cost included."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    first = PiSessions().snapshot()[0]
    usage = first.usage
    assert usage.input_tokens == 140
    assert usage.output_tokens == 30
    assert usage.cache_read_tokens == 30
    assert usage.cache_write_tokens == 7
    assert usage.total_tokens == 207
    assert usage.cost is not None
    assert str(usage.cost) == "0.00212"


def test_claude_usage_dedupes_repeated_assistant_snapshots(
    tmp_path, monkeypatch
) -> None:
    """Claude repeats one reply across snapshots; count each message id once."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    first = ClaudeSessions().snapshot()[0]
    usage = first.usage
    assert usage.input_tokens == 14
    assert usage.output_tokens == 60
    assert usage.cache_read_tokens == 220
    assert usage.cache_write_tokens == 106
    assert usage.total_tokens == 400
    assert usage.cost is None, "the Claude transcript carries no cost"


def test_usage_cache_returns_the_same_totals_for_an_unchanged_file(
    tmp_path, monkeypatch
) -> None:
    """A second poll over an unchanged file reads the same totals."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    source = PiSessions()
    before = source.snapshot()[0].usage
    after = source.snapshot()[0].usage
    assert before == after


def test_claude_sessions_read_from_env_root(tmp_path, monkeypatch) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    source = ClaudeSessions()
    sessions = source.snapshot()
    assert [session.session_id for session in sessions] == [CLAUDE_ID]
    assert sessions[0].harness is Harness.CLAUDE
    assert sessions[0].model == "claude-fixture-model"


def test_opencode_sessions_read_from_env_root(tmp_path, monkeypatch) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    source = OpencodeSessions()
    sessions = source.snapshot()
    assert [session.session_id for session in sessions] == [OPENCODE_ID]
    first = sessions[0]
    assert first.harness is Harness.OPENCODE
    assert first.last_activity.tzinfo is not None
    assert first.usage.total_tokens == 165


def test_opencode_db_is_byte_for_byte_unchanged_after_a_read(
    tmp_path, monkeypatch
) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    source = OpencodeSessions()

    def digest() -> str:
        return hashlib.sha256(source.db.read_bytes()).hexdigest()

    before = digest()
    source.snapshot()
    source.snapshot()
    assert digest() == before


def test_default_sources_read_every_temp_root(tmp_path, monkeypatch) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    sessions = SessionStore(default_sources()).refresh()
    ids = {session.session_id for session in sessions}
    assert ids == {PI_ID, CLAUDE_ID, OPENCODE_ID}


def test_source_health_reports_path_mode_and_count(tmp_path, monkeypatch) -> None:
    """every enabled source shows its path, open mode and count."""
    root = seed_fixture_root(tmp_path)
    point_env_at_fixtures(monkeypatch, root)
    _sessions, health = SessionStore(default_sources()).poll()
    by_harness = {item.harness: item for item in health}
    assert set(by_harness) == {"pi", "claude", "opencode"}
    assert by_harness["pi"].path == str(root / "pi")
    assert by_harness["claude"].path == str(root / "claude")
    assert by_harness["opencode"].path == str(root / "opencode" / "opencode.db")
    assert by_harness["pi"].open_mode == "read"
    assert by_harness["claude"].open_mode == "read"
    assert by_harness["opencode"].open_mode == "ro"
    assert by_harness["pi"].count == 1
    assert by_harness["claude"].count == 1
    assert by_harness["opencode"].count == 1
    assert all(item.available for item in health)


def test_missing_store_shows_unavailable_not_zero(tmp_path, monkeypatch) -> None:
    """a missing store is unavailable, never a silent zero count."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "missing" / "opencode.db"))
    _sessions, health = SessionStore(default_sources()).poll()
    opencode = next(item for item in health if item.harness == "opencode")
    assert opencode.available is False
    assert opencode.status == "unavailable"
    assert opencode.error is None


def test_health_strip_names_every_source_with_a_count(tmp_path, monkeypatch) -> None:
    """the rendered strip names all three sources and their counts."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    _sessions, health = SessionStore(default_sources()).poll()
    rendered = render_health_strip(health).plain
    for name in ("pi", "claude", "opencode"):
        assert name in rendered
    assert "1 session" in rendered
    assert "unavailable" not in rendered


def test_health_strip_marks_a_missing_store(tmp_path, monkeypatch) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "missing" / "opencode.db"))
    _sessions, health = SessionStore(default_sources()).poll()
    rendered = render_health_strip(health).plain
    assert "unavailable" in rendered
    assert "0 session" not in rendered


async def test_app_shows_the_source_health_strip(tmp_path, monkeypatch) -> None:
    """the real app renders the strip from the configured sources."""
    root = seed_fixture_root(tmp_path)
    point_env_at_fixtures(monkeypatch, root)
    app = BrigadeTUI()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        strip = app.query_one("#source-health", Static)
        rendered = strip.render()
        text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        await pilot.pause()
    for name in ("pi", "claude", "opencode"):
        assert name in text
    assert "1 session" in text
    assert str(root / "pi") in text


async def test_app_reads_sources_from_env_configured_temp_roots(
    tmp_path, monkeypatch
) -> None:
    """The app itself reads the env-configured temp roots, with no real store."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    app = BrigadeTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_sessions()
        await pilot.pause()
        rows = [
            table.get_row(row_key)
            for table in app.query(DataTable)
            for row_key in table.rows
        ]
    harnesses = {str(row[0]) for row in rows}
    assert harnesses == {"pi", "claude", "opencode"}


def test_store_groups_by_project_from_temp_roots(tmp_path, monkeypatch) -> None:
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    store = SessionStore(default_sources())
    sessions = store.refresh()
    pairs = store.by_project(sessions)
    assert len(pairs) == 1
    total = sum(len(group) for _project, group in pairs)
    assert total == len(sessions) == 3
    for _project, group in pairs:
        activities = [session.last_activity for session in group]
        assert activities == sorted(activities, reverse=True)


def test_app_row_shows_real_tokens_for_pi_and_claude(
    tmp_path, monkeypatch
) -> None:
    """The table row the TUI renders is not blank for pi and Claude."""
    point_env_at_fixtures(monkeypatch, seed_fixture_root(tmp_path))
    sessions = SessionStore(default_sources()).refresh()
    by_harness = {session.harness: session for session in sessions}
    for harness in (Harness.PI, Harness.CLAUDE, Harness.OPENCODE):
        cells = session_cells(by_harness[harness])
        assert cells[4] not in ("", "-"), f"{harness} token cell is blank"


def test_project_from_directory_labels_the_home_dir() -> None:
    assert project_from_directory(str(Path.home())).label == "~"
    ref = project_from_directory("/opt/work/brigade-tui")
    assert ref.label == "brigade-tui"


def make_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a real git repo and a real worktree of it. Return both."""
    repo = tmp_path / "owner"
    repo.mkdir()
    run = lambda *args: subprocess.run(args, check=True, capture_output=True)
    run("git", "init", "-q", str(repo))
    run("git", "-C", str(repo), "config", "user.email", "t@example.com")
    run("git", "-C", str(repo), "config", "user.name", "Test")
    (repo / "readme.txt").write_text("hello\n", encoding="utf-8")
    run("git", "-C", str(repo), "add", "readme.txt")
    run("git", "-C", str(repo), "commit", "-qm", "init")
    worktree = tmp_path / "worktrees" / "ITEM-42"
    run("git", "-C", str(repo), "worktree", "add", "-q", str(worktree))
    return repo, worktree


def test_worktree_directory_folds_into_the_owning_project(tmp_path) -> None:
    """A real git worktree resolves to the repo it belongs to."""
    repo, worktree = make_worktree(tmp_path)
    ref = project_from_directory(str(worktree))
    assert ref.key == str(repo)
    assert ref.label == "owner"
    # A subdirectory of the worktree folds to the same owner.
    (worktree / "src").mkdir()
    assert project_from_directory(str(worktree / "src")).key == str(repo)
    # The worktree path never owns a project of its own.
    assert "/worktrees/ITEM-42" not in ref.key


def test_pi_worktree_session_reads_under_the_owning_project(
    tmp_path, monkeypatch
) -> None:
    """A real pi transcript with a worktree cwd folds into the owner."""
    repo, worktree = make_worktree(tmp_path)
    root = tmp_path / "pi"
    project_dir = root / "slug"
    project_dir.mkdir(parents=True)
    record = {
        "type": "session",
        "id": "worktree-session",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "cwd": str(worktree),
    }
    (project_dir / "worktree-session.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("PI_SESSION_DIR", str(root))

    sessions = PiSessions().snapshot()
    assert len(sessions) == 1
    assert sessions[0].project.key == str(repo)
    # The session still records where it really ran.
    assert sessions[0].directory == str(worktree)
