"""Headless tests for the TUI scaffold.

These use Textual's ``run_test()`` harness. No database and no subprocess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.widgets import DataTable, Input, Static, TabPane, TabbedContent

from brigade_tui.app import (
    TAB_LIMIT,
    HelpScreen,
    SearchScreen,
    BrigadeTUI,
    tab_switch_bindings,
)
from brigade_tui.detail import SessionDetailScreen
from brigade_tui.model import Harness, ProjectRef, Session, session_label
from brigade_tui.timeline import (
    TimelineView,
    assign_model_colours,
    build_lanes,
    display_model,
)

PROJECTS = ("alpha", "beta", "gamma")
REPO_ROOT = Path(__file__).resolve().parents[1]


def expected_actions() -> dict[str, str]:
    """The key to action-name map the app must register."""
    mapping = {
        "h": "previous_tab",
        "l": "next_tab",
        "j": "cursor_down",
        "k": "cursor_up",
        "g": "first_row",
        "G": "last_row",
        "t": "toggle_table",
        "escape": "back_to_grid",
        "slash": "search",
        "question_mark": "help",
    }
    for index in range(TAB_LIMIT):
        key = "ctrl+0" if index == TAB_LIMIT - 1 else f"ctrl+{index + 1}"
        mapping[key] = "switch_tab"
    return mapping


async def test_tab_bar_is_present_with_one_tab_per_project() -> None:
    app = BrigadeTUI(projects=PROJECTS, sources=())
    async with app.run_test() as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        panes = app.query(TabPane)
        assert content is not None
        # Pane ids come from the project key, not the position, so they are
        # stable when the bar changes. One id per project, all distinct.
        assert [pane.id for pane in panes] == [
            app._pane_id(key) for key in PROJECTS
        ]
        assert len(set(pane.id for pane in panes)) == len(PROJECTS)
        labels = [str(content.get_tab(pane).label) for pane in panes]
        # Each label leads with the project name and adds a progress signal.
        assert [label.split(" ")[0] for label in labels] == list(PROJECTS)
        assert all(label == name or label.startswith(name + " ")
                   for label, name in zip(labels, PROJECTS))

        tab_bar = app.query_one("ContentTabs")
        assert tab_bar.region.width == app.size.width
        assert tab_bar.region.y < app.size.height // 2


async def test_every_binding_is_registered_to_a_real_action() -> None:
    app = BrigadeTUI(projects=PROJECTS, sources=())
    async with app.run_test():
        registered = {key: binding.action for key, binding in app._bindings}
        for key, action in expected_actions().items():
            assert key in registered, f"{key} is not registered"
            assert registered[key].split("(")[0] == action
            assert callable(getattr(app, f"action_{action}")), (
                f"action_{action} does not exist"
            )


async def test_bindings_actually_act() -> None:
    app = BrigadeTUI(projects=PROJECTS, sources=())
    async with app.run_test() as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)

        await pilot.press("ctrl+2")
        await pilot.pause()
        assert content.active == app._pane_id("beta")

        await pilot.press("l")
        await pilot.pause()
        assert content.active == app._pane_id("gamma")

        await pilot.press("l")
        await pilot.pause()
        assert content.active == app._pane_id(
            "alpha"
        ), "l wraps from the last tab to the first"

        await pilot.press("h")
        await pilot.pause()
        assert content.active == app._pane_id(
            "gamma"
        ), "h wraps from the first tab to the last"

        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, SearchScreen)

        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


async def test_j_k_gg_and_G_move_the_session_cursor() -> None:
    app = BrigadeTUI(projects=PROJECTS, sources=())
    async with app.run_test() as pilot:
        await pilot.pause()
        # List keys act on a project view, never on the hidden table behind
        # the read-only grid.
        app.enter_project_view()
        await pilot.pause()
        await pilot.press("t")
        table = app.query_one("#sessions-0", DataTable)
        for index in range(5):
            table.add_row("pi", "model", "1s", "-", "-", f"s{index}")
        table.focus()
        await pilot.pause()
        assert table.cursor_row == 0

        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("j")
        assert table.cursor_row == 2
        await pilot.press("k")
        assert table.cursor_row == 1

        await pilot.press("G")
        assert table.cursor_row == 4

        await pilot.press("g")
        await pilot.press("g")
        assert table.cursor_row == 0


async def test_timeline_is_the_default_body_and_t_switches_to_the_table() -> None:
    """The tab body is the swimlane timeline; ``t`` reaches the flat table."""
    app = BrigadeTUI(projects=PROJECTS, sources=())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        timeline = app.query_one("#timeline-0", TimelineView)
        table = app.query_one("#sessions-0", DataTable)
        assert timeline.display is True
        assert table.display is False

        await pilot.press("t")
        await pilot.pause()
        assert timeline.display is False
        assert table.display is True

        await pilot.press("t")
        await pilot.pause()
        assert timeline.display is True
        assert table.display is False


class OneSessionSource:
    """A tiny in-test source that reports exactly one session."""

    harness = Harness.PI

    def __init__(self, session: Session) -> None:
        self.session = session

    def exists(self) -> bool:
        return True

    def snapshot(self) -> list[Session]:
        return [self.session]


async def test_timeline_blocks_span_started_at_to_last_activity() -> None:
    """A block starts at ``started_at`` and ends at ``last_activity``."""
    now = datetime.now(timezone.utc)
    session = Session(
        harness=Harness.PI,
        session_id="timeline-one",
        project=ProjectRef(key="alpha", label="alpha"),
        directory="alpha",
        last_activity=now,
        started_at=now - timedelta(seconds=20),
    )
    app = BrigadeTUI(sources=(OneSessionSource(session),))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        timeline = app.query_one("#timeline-0", TimelineView)
        lanes = timeline.lanes
        assert len(lanes) == 1
        block = lanes[0].blocks[0]
        assert block.start == session.started_at
        assert block.end == session.last_activity
        assert block.duration == 20.0
        assert block.end_col > block.start_col


def test_label_is_role_else_project_and_title_or_readable_id() -> None:
    """R11: a lane or card label is meaningful when no role is known."""
    now = datetime.now(timezone.utc)
    named = Session(
        harness=Harness.PI,
        session_id="01a10431bcd",
        project=ProjectRef(key="a", label="a"),
        directory="a",
        last_activity=now,
        title="Fix the waterfall colours please",
        role="reviewer",
    )
    assert session_label(named) == "reviewer"

    unnamed = Session(
        harness=Harness.PI,
        session_id="01a10431bcd",
        project=ProjectRef(key="a", label="a"),
        directory="a",
        last_activity=now,
        title="Fix the waterfall colours please",
    )
    label = session_label(unnamed)
    # The project name plus a title fragment, never the bare short id.
    assert label == "a: Fix the waterfall colours please"
    assert label != unnamed.title
    assert label != unnamed.session_id[:8]

    silent = Session(
        harness=Harness.CLAUDE,
        session_id="01a10431bcd",
        project=ProjectRef(key="a", label="a"),
        directory="a",
        last_activity=now,
        title=None,
    )
    readable = session_label(silent)
    # No title: the project name plus a readable id, never a bare hash.
    assert readable == "a: claude:01a10431"
    assert readable != silent.session_id[:8]


def test_blocks_sit_at_real_offsets_and_a_role_gets_two_blocks() -> None:
    """ISC-15/ISC-26: real offsets, and a repeated role gets two blocks."""
    now = datetime.now(timezone.utc)
    sessions = [
        Session(
            harness=Harness.PI,
            session_id="builder-1",
            project=ProjectRef(key="a", label="a"),
            directory="a",
            last_activity=now + timedelta(seconds=10),
            started_at=now,
            role="builder",
        ),
        Session(
            harness=Harness.PI,
            session_id="reviewer-1",
            project=ProjectRef(key="a", label="a"),
            directory="a",
            last_activity=now + timedelta(seconds=20),
            started_at=now + timedelta(seconds=5),
            role="reviewer",
        ),
        Session(
            harness=Harness.PI,
            session_id="builder-2",
            project=ProjectRef(key="a", label="a"),
            directory="a",
            last_activity=now + timedelta(seconds=40),
            started_at=now + timedelta(seconds=30),
            role="builder",
        ),
    ]
    lanes = build_lanes(sessions, plot_width=120)
    by_key = {lane.key: lane for lane in lanes}
    builder = by_key["builder"]
    assert len(builder.blocks) == 2
    assert [block.offset_start for block in builder.blocks] == [0.0, 30.0]
    assert [block.offset_end for block in builder.blocks] == [10.0, 40.0]
    # The later builder block sits to the right of the first.
    assert builder.blocks[1].start_col > builder.blocks[0].start_col
    # The reviewer block overlaps the first builder block in real time.
    reviewer = by_key["reviewer"]
    assert reviewer.blocks[0].offset_start == 5.0
    assert reviewer.blocks[0].start_col < builder.blocks[0].end_col


def test_model_colours_are_one_per_model_deterministically() -> None:
    """ISC-16: same model shares a colour, different models differ."""
    colours = assign_model_colours(
        ["opencode-go/deepseek-v4.1-flash", "anthropic/claude-opus-5",
         "opencode-go/deepseek-v4.1-flash", None]
    )
    assert colours["opencode-go/deepseek-v4.1-flash"] != colours["anthropic/claude-opus-5"]
    assert len(set(colours.values())) == len(colours)
    assert display_model(None) in colours
    assert assign_model_colours(
        ["anthropic/claude-opus-5", "opencode-go/deepseek-v4.1-flash"]
    ) == {
        "anthropic/claude-opus-5": colours["anthropic/claude-opus-5"],
        "opencode-go/deepseek-v4.1-flash": colours[
            "opencode-go/deepseek-v4.1-flash"
        ],
    }


async def test_renders_an_svg_screenshot_to_docs() -> None:
    docs = REPO_ROOT / "docs"
    docs.mkdir(exist_ok=True)
    app = BrigadeTUI(projects=PROJECTS, sources=())
    async with app.run_test() as pilot:
        await pilot.pause()
        saved = app.save_screenshot(filename="scaffold.svg", path=str(docs))
    svg_path = Path(saved)
    assert svg_path == docs / "scaffold.svg"
    assert svg_path.exists()
    assert svg_path.stat().st_size > 0
    assert "<svg" in svg_path.read_text(encoding="utf-8")


def test_bindings_match_the_documented_set() -> None:
    """The static BINDINGS list is the one the test asserts against."""
    assert {b.key for b in tab_switch_bindings()} == set(expected_actions())


def make_session(project: str, session_id: str, age: int) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        harness=Harness.PI,
        session_id=session_id,
        project=ProjectRef(key=project, label=project),
        directory=project,
        last_activity=now - timedelta(minutes=age),
    )


class MultiSessionSource:
    """An in-test source that reports a fixed list of sessions."""

    harness = Harness.PI

    def __init__(self, sessions: list[Session]) -> None:
        self._sessions = list(sessions)

    def exists(self) -> bool:
        return True

    def snapshot(self) -> list[Session]:
        return list(self._sessions)


async def test_seed_session_owns_no_tab_but_search_finds_it_by_id() -> None:
    """A temp-cwd seed session is hidden from tabs, yet reachable by id."""
    now = datetime.now(timezone.utc)
    seed = Session(
        harness=Harness.PI,
        session_id="01a10446-a3f2-73df-8891-452b15f67f93",
        project=ProjectRef(key="/tmp/stw32/wt/STW-1", label="STW-1"),
        directory="/tmp/stw32/wt/STW-1",
        last_activity=now,
    )
    real = Session(
        harness=Harness.PI,
        session_id="real-project-session",
        project=ProjectRef(key="/home/user/work/brigade-tui", label="brigade"),
        directory="/home/user/work/brigade-tui",
        last_activity=now - timedelta(minutes=5),
    )
    app = BrigadeTUI(sources=(MultiSessionSource([seed, real]),))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        # The seed project owns no tab; the real /home project still shows.
        assert app._pane_keys == ["/home/user/work/brigade-tui"]
        # The hidden session is still polled, so search can reach it.
        assert any(s.session_id == seed.session_id for s in app._sessions)

        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, SearchScreen)
        search = app.screen.query_one("#search-input", Input)
        search.value = seed.session_id
        await pilot.pause()
        table = app.screen.query_one("#search-results", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[-1] == seed.session_id
        assert table.get_row_at(0)[-2] == f"{seed.project.label}: pi:{seed.session_id[:8]}"


async def test_view_caps_state_projects_and_rows_shown_out_of_totals() -> None:
    """The default view states the tab cap and the row cap out loud."""
    sessions = [
        make_session(f"/work/p{index}", f"s{index}", index)
        for index in range(3)
    ]
    # One project holds more rows than the 50 row cap, so the loss is real.
    sessions += [
        make_session("/work/big", f"big-{index}", 100 + index)
        for index in range(60)
    ]
    app = BrigadeTUI(sources=(MultiSessionSource(sessions),))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        line = app.query_one("#view-caps", Static).render().plain
        assert "4 of 4 projects (tab cap 10)" in line
        # 3 single-row projects plus the 50 shown rows of the 60-row project.
        assert "53 of 63 rows (row cap 50)" in line


async def test_search_browser_lists_sessions_beyond_the_live_caps() -> None:
    """The browser lists every project and session, and filters as text is typed."""
    sessions = [
        make_session(f"/work/p{index}", f"session-{index:03d}", index)
        for index in range(120)
    ]
    app = BrigadeTUI(projects=("only",), sources=())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._sessions = sessions
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, SearchScreen)
        table = app.screen.query_one("#search-results", DataTable)
        assert table.row_count == len(sessions)

        search = app.screen.query_one("#search-input", Input)
        search.value = "session-119"
        await pilot.pause()
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "/work/p119"
        assert table.get_row_at(0)[-1] == "session-119"
        assert table.get_row_at(0)[-2] == "/work/p119: pi:session-"


async def test_search_is_keyboard_reachable_and_shows_the_raw_id() -> None:
    """R10: search is operable from the keyboard with no dead end.

    The filter box hands off to the result table on Down. ``j`` and ``k``
    still type in the filter, then move the cursor once the table has focus.
    The raw session id sits on the row, and Enter opens the same detail pane.
    """
    sessions = [
        make_session("/work/a", "session-aaa", 1),
        make_session("/work/b", "session-bbb", 2),
    ]
    app = BrigadeTUI(projects=("only",), sources=())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app._sessions = sessions
        await pilot.press("slash")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SearchScreen)
        table = screen.query_one("#search-results", DataTable)
        search = screen.query_one("#search-input", Input)

        # The raw session id is visible on the row, not only a short label.
        assert table.get_row_at(0)[-1] == "session-aaa"

        # j and k are filter text while the filter box has focus.
        await pilot.press("j")
        await pilot.press("k")
        await pilot.pause()
        assert search.value == "jk"
        assert not table.has_focus

        search.value = "session"
        await pilot.pause()
        assert table.row_count == 2

        # Down hands off to the table; the cursor is then keyboard driven.
        await pilot.press("down")
        await pilot.pause()
        assert table.has_focus
        assert table.cursor_row == 0
        await pilot.press("j")
        assert table.cursor_row == 1
        await pilot.press("k")
        assert table.cursor_row == 0
        await pilot.press("G")
        assert table.cursor_row == 1
        await pilot.press("g")
        assert table.cursor_row == 0

        # Enter opens the detail pane, and the raw id is in the pane too.
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SessionDetailScreen)
        assert app.screen.session.session_id == "session-aaa"
        meta = app.screen.query_one("#detail-meta").render().plain
        assert "session-aaa" in meta


async def test_tab_order_and_pane_identity_hold_when_a_session_updates() -> None:
    """A session update must not reorder the bar or change a pane id."""
    source = MultiSessionSource(
        [
            make_session("/work/a", "a1", 10),
            make_session("/work/b", "b1", 5),
        ]
    )
    app = BrigadeTUI(sources=(source,))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        before_keys = list(app._pane_keys)
        before_labels = [str(content.get_tab(pane).label) for pane in app.query(TabPane)]
        before_ids = [pane.id for pane in app.query(TabPane)]

        # Project a gets a new session and becomes the newest project. Before
        # this item that reordered the whole tab bar.
        source._sessions.append(make_session("/work/a", "a2", 0))
        await app.refresh_sessions()
        await pilot.pause()

        content = app.query_one("#projects", TabbedContent)
        assert app._pane_keys == before_keys
        assert (
            [str(content.get_tab(pane).label) for pane in app.query(TabPane)]
            == before_labels
        )
        assert [pane.id for pane in app.query(TabPane)] == before_ids
        # The pane id is a function of the project key, not its position.
        assert app._pane_id("/work/a") in before_ids


async def test_active_tab_is_pinned_when_it_would_fall_past_the_cap() -> None:
    """A newcomer cannot push the active project off the bar or steal it."""
    sessions = [
        make_session(f"/work/p{index}", f"s{index}", index)
        for index in range(TAB_LIMIT)
    ]
    source = MultiSessionSource(sessions)
    app = BrigadeTUI(sources=(source,))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert len(app._pane_keys) == TAB_LIMIT
        content = app.query_one("#projects", TabbedContent)
        # Drop into the oldest visible project, the one a newcomer evicts.
        pinned = app._pane_keys[-1]
        await pilot.press("ctrl+0")
        await pilot.pause()
        assert app._pane_keys[app._active_index()] == pinned

        source._sessions.append(make_session("/work/new", "new", 0))
        await app.refresh_sessions()
        await pilot.pause()

        assert len(app._pane_keys) == TAB_LIMIT
        assert pinned in app._pane_keys
        assert app._pane_keys[app._active_index()] == pinned
        assert content.active == app._pane_id(pinned)

        # Another poll must not re-add the project the pin evicted, or the
        # bar would rotate on every poll.
        settled = list(app._pane_keys)
        await app.refresh_sessions()
        await pilot.pause()
        assert app._pane_keys == settled
        assert app._pane_keys[app._active_index()] == pinned

