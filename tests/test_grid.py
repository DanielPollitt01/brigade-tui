"""Tests for the grid main view and the focus rule.

The grid is the default view, one state-coloured card per session. A new tab
or a session change never moves the active view.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from textual.widgets import DataTable, TabbedContent, TabPane

from brigade_tui.app import BrigadeTUI
from brigade_tui.grid import (
    READ_ONLY_BANNER,
    STATE_COLOURS,
    SessionGrid,
    build_cards,
    format_elapsed,
    group_cards,
    heading_text,
    render_grid,
)
from brigade_tui.model import (
    Harness,
    ProjectRef,
    Session,
    SessionState,
    elapsed_seconds,
    session_state,
)
from brigade_tui.timeline import (
    TimelineView,
    assign_model_colours,
    render_timeline,
)

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def session(
    project: str,
    session_id: str = "sid",
    *,
    title: str | None = "Fix the grid",
    role: str | None = "builder",
    last_activity: datetime | None = None,
    started_at: datetime | None = None,
    run_started_at: datetime | None = None,
    reported_state: str | None = None,
    model: str | None = "opencode-go/deepseek-v4.1-flash",
) -> Session:
    return Session(
        harness=Harness.PI,
        session_id=session_id,
        project=ProjectRef(key=project, label=project),
        directory=project,
        last_activity=last_activity or NOW - timedelta(seconds=10),
        title=title,
        model=model,
        started_at=started_at,
        run_started_at=run_started_at,
        role=role,
        reported_state=reported_state,
    )


class MutableSource:
    """A real source whose snapshot the test can change between polls."""

    harness = Harness.PI

    def __init__(self, sessions: list[Session]) -> None:
        self._sessions = list(sessions)

    def exists(self) -> bool:
        return True

    def snapshot(self) -> list[Session]:
        return list(self._sessions)


def test_state_is_running_idle_and_completed_over_two_windows() -> None:
    fresh = session("/home/user/a", last_activity=NOW - timedelta(seconds=30))
    paused = session("/home/user/a", last_activity=NOW - timedelta(seconds=300))
    stale = session("/home/user/a", last_activity=NOW - timedelta(seconds=1200))
    assert session_state(fresh, NOW) is SessionState.RUNNING
    assert session_state(paused, NOW) is SessionState.IDLE
    assert session_state(stale, NOW) is SessionState.COMPLETED


def test_blocked_only_when_a_source_reports_it() -> None:
    blocked = session(
        "/home/user/a",
        last_activity=NOW - timedelta(seconds=30),
        reported_state="blocked",
    )
    assert session_state(blocked, NOW) is SessionState.BLOCKED
    # A fresh session with no report is running, not blocked.
    assert (
        session_state(session("/home/user/a", last_activity=NOW), NOW)
        is SessionState.RUNNING
    )


def test_card_shows_title_agent_and_elapsed_and_colours_by_state() -> None:
    sessions = [
        session(
            "/home/user/a",
            "running-one",
            title="Build the grid",
            role="builder",
            last_activity=NOW - timedelta(seconds=10),
            started_at=NOW - timedelta(seconds=90),
        ),
        session(
            "/home/user/b",
            "completed-one",
            title="Review the diff",
            role="reviewer",
            last_activity=NOW - timedelta(seconds=1200),
            started_at=NOW - timedelta(seconds=1800),
        ),
        session(
            "/home/user/d",
            "idle-one",
            title="Waiting on a build",
            role="planner",
            last_activity=NOW - timedelta(seconds=300),
            started_at=NOW - timedelta(seconds=600),
        ),
        session(
            "/home/user/c",
            "blocked-one",
            title="Waiting on a key",
            role="planner",
            last_activity=NOW - timedelta(seconds=10),
            started_at=NOW - timedelta(seconds=30),
            reported_state="blocked",
        ),
    ]
    cards = {card.session_id: card for card in build_cards(sessions, NOW)}
    running = cards["running-one"]
    assert running.title == "Build the grid"
    assert running.agent == "builder"
    assert running.elapsed == "1m 30s"
    assert running.state is SessionState.RUNNING
    assert running.colour == "blue"
    assert cards["completed-one"].colour == "green"
    assert cards["idle-one"].state is SessionState.IDLE
    assert cards["idle-one"].colour == "yellow"
    assert cards["blocked-one"].colour == "red"


def test_state_colours_are_exactly_red_green_yellow_blue() -> None:
    assert STATE_COLOURS == {
        SessionState.BLOCKED: "red",
        SessionState.COMPLETED: "green",
        SessionState.IDLE: "yellow",
        SessionState.RUNNING: "blue",
    }


def test_running_elapsed_grows_but_idle_and_completed_elapsed_hold() -> None:
    running = session(
        "/home/user/a",
        last_activity=NOW - timedelta(seconds=5),
        started_at=NOW - timedelta(seconds=20),
    )
    later = NOW + timedelta(seconds=30)
    assert elapsed_seconds(running, NOW) == 20
    assert elapsed_seconds(running, later) == 50

    idle = session(
        "/home/user/a",
        last_activity=NOW - timedelta(seconds=300),
        started_at=NOW - timedelta(seconds=600),
    )
    assert elapsed_seconds(idle, NOW) == 300
    assert elapsed_seconds(idle, later) == 300

    completed = session(
        "/home/user/a",
        last_activity=NOW - timedelta(seconds=1200),
        started_at=NOW - timedelta(seconds=1500),
    )
    assert elapsed_seconds(completed, now=later) == 300


def test_elapsed_is_the_run_span_not_the_session_age() -> None:
    """A resumed transcript shows the resumed run, not the session's age."""
    long_lived = session(
        "/home/user/a",
        last_activity=NOW - timedelta(seconds=5),
        started_at=NOW - timedelta(hours=6650),
        run_started_at=NOW - timedelta(minutes=3),
    )
    assert elapsed_seconds(long_lived, NOW) == 180
    assert format_elapsed(elapsed_seconds(long_lived, NOW)) == "3m 00s"


def test_current_run_start_cuts_at_the_last_long_gap() -> None:
    from brigade_tui.model import current_run_start

    times = [
        NOW - timedelta(hours=20),
        NOW - timedelta(hours=19, minutes=58),
        NOW - timedelta(minutes=30),
        NOW - timedelta(minutes=25),
    ]
    assert current_run_start(times) == NOW - timedelta(minutes=30)
    assert current_run_start([]) is None


def test_render_grid_contains_the_three_card_fields() -> None:
    cards = build_cards(
        [
            session(
                "/home/user/a",
                title="Build the grid",
                role="builder",
                last_activity=NOW - timedelta(seconds=10),
                started_at=NOW - timedelta(seconds=90),
            )
        ],
        NOW,
    )
    rendered = render_grid(cards, width=80).plain
    assert "Build the grid" in rendered
    assert "builder" in rendered
    assert "1m 30s" in rendered


def test_format_elapsed_shapes() -> None:
    assert format_elapsed(45) == "45s"
    assert format_elapsed(200) == "3m 20s"
    assert format_elapsed(7500) == "2h 05m"


def test_grid_groups_cards_under_a_project_heading() -> None:
    """Cards are grouped by project, newest project first."""
    sessions = [
        session("/home/user/beta", "beta-new", last_activity=NOW - timedelta(minutes=1)),
        session("/home/user/alpha", "alpha-new", last_activity=NOW - timedelta(minutes=5)),
        session("/home/user/alpha", "alpha-old", last_activity=NOW - timedelta(minutes=10)),
    ]
    cards = build_cards(sessions, NOW)
    groups = group_cards(cards)
    assert [project for project, _ in groups] == ["/home/user/beta", "/home/user/alpha"]
    assert [len(group) for _, group in groups] == [1, 2]

    rendered = render_grid(cards, width=80).plain
    assert heading_text("/home/user/beta", 1) in rendered
    assert heading_text("/home/user/alpha", 2) in rendered


def test_waterfall_has_no_state_word_and_no_state_colour() -> None:
    sessions = [
        session(
            "/home/user/a",
            "one",
            role="builder",
            last_activity=NOW - timedelta(seconds=10),
            started_at=NOW - timedelta(seconds=60),
        ),
        session(
            "/home/user/b",
            "two",
            role="reviewer",
            model="anthropic/claude-opus-5",
            last_activity=NOW - timedelta(seconds=30),
            started_at=NOW - timedelta(seconds=90),
        ),
    ]
    text = render_timeline(sessions, width=100, model_colours=assign_model_colours(
        s.model for s in sessions
    ))
    lowered = text.plain.lower()
    for word in ("blocked", "running", "completed"):
        assert word not in lowered
    colours = assign_model_colours(s.model for s in sessions)
    assert set(colours.values()).isdisjoint({"red", "green", "blue"})


async def test_grid_is_the_default_view() -> None:
    app = BrigadeTUI(
        sources=(MutableSource([session("/home/user/a")]),)
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        grid = app.query_one("#grid", SessionGrid)
        assert app._grid_mode is True
        assert grid.display is True
        projects = app.query_one("#projects", TabbedContent)
        assert projects.region.height == 2


async def test_new_tab_does_not_steal_focus_and_appears_left() -> None:
    source = MutableSource(
        [
            session("/home/user/a", "a1", last_activity=NOW - timedelta(minutes=10)),
            session("/home/user/b", "b1", last_activity=NOW - timedelta(minutes=5)),
        ]
    )
    app = BrigadeTUI(sources=(source,))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        content = app.query_one("#projects", TabbedContent)
        # Drop into the second tab and stay there.
        await pilot.press("l")
        await pilot.pause()
        active_key = app._pane_keys[app._active_index()]

        # A brand-new newest project arrives.
        source._sessions.append(
            session("/home/user/c", "c1", last_activity=NOW - timedelta(minutes=1))
        )
        await app.refresh_sessions()
        await pilot.pause()

        # The same project is still active; the view did not move.
        assert app._pane_keys[app._active_index()] == active_key
        # The new tab is first, at the left of the tab bar.
        panes = app.query(TabPane)
        labels = [str(content.get_tab(pane).label) for pane in panes]
        assert labels[0].startswith("/home/user/c ")

        # h and l move the active tab, by user action only.
        before = content.active
        await pilot.press("h")
        await pilot.pause()
        assert content.active != before


async def test_escape_returns_to_the_grid() -> None:
    app = BrigadeTUI(
        sources=(MutableSource([session("/home/user/a")]),)
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert app._grid_mode is False
        await pilot.press("escape")
        await pilot.pause()
        assert app._grid_mode is True
        assert app.query_one("#grid", SessionGrid).display is True


async def test_grid_updates_on_a_session_change_without_mode_switch() -> None:
    source = MutableSource([session("/home/user/a", "a1")])
    app = BrigadeTUI(sources=(source,))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        grid = app.query_one("#grid", SessionGrid)
        assert [card.session_id for card in grid.cards()] == ["a1"]
        source._sessions.append(session("/home/user/a", "a2"))
        await app.refresh_sessions()
        await pilot.pause()
        assert {card.session_id for card in grid.cards()} == {"a1", "a2"}
        assert app._grid_mode is True


def test_grid_render_says_read_only() -> None:
    """The grid tells the user it is read-only, so no cursor is expected."""
    plain = SessionGrid().render().plain
    assert "read-only" in plain
    assert "j / k do nothing" in plain
    assert READ_ONLY_BANNER in plain


async def test_grid_keys_do_not_move_the_hidden_table() -> None:
    """While the grid is shown, j, k, gg and G must not touch the table.

    The table is a project-tab body. It is hidden in grid mode. A list key
    must never move its cursor behind the grid.
    """
    app = BrigadeTUI(
        projects=("alpha", "beta"), sources=()
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        assert app._grid_mode is True
        table = app.query_one("#sessions-0", DataTable)
        for index in range(5):
            table.add_row("pi", "model", "1s", "-", "-", f"s{index}")
        table.move_cursor(row=2)
        await pilot.pause()
        assert table.cursor_row == 2

        for key in ("j", "j", "k", "G"):
            await pilot.press(key)
            await pilot.pause()
            assert table.cursor_row == 2, f"{key} moved the hidden table"

        await pilot.press("g")
        await pilot.press("g")
        await pilot.pause()
        assert table.cursor_row == 2, "gg moved the hidden table"
