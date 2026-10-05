"""The Brigade read-only session view.

This app reads session stores that other programs already wrote. It opens
nothing for writing and it launches nothing. It shows what is going on.

Key bindings:

- ``h`` and ``l`` previous and next tab
- ``ctrl+1`` and up to switch straight to a tab
- ``j`` and ``k`` move the session-list cursor, ``gg`` first row, ``G`` last row
- ``/`` search, ``?`` help
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane

from .config import DEFAULT_REFRESH_SECONDS, load_config
from .detail import SessionDetailScreen
from .grid import SessionGrid
from .health import render_health_strip
from .model import (
    RUNNING_WINDOW_SECONDS,
    ProjectRef,
    Session,
    is_scratch,
    session_label,
)
from .sources import SessionSource, default_sources, sources_from_roots
from .store import MAX_ROWS_PER_TAB, MAX_TABS, SessionStore, unique_labels
from .summary import (
    build_summary,
    build_view_caps,
    progress_labels,
    render_summary,
    render_view_caps,
)
from .timeline import WINDOW_SECONDS, TimelineView, assign_model_colours

#: Default project tabs before any session source is wired in.
DEFAULT_PROJECTS: tuple[str, ...] = ("brigade",)

#: The highest direct tab jump key. ``ctrl+0`` is the tenth tab.
TAB_LIMIT = MAX_TABS

#: Seconds between session polls when no config or flag sets one.
REFRESH_SECONDS = DEFAULT_REFRESH_SECONDS

#: Seconds that a lone ``g`` waits for a second ``g`` before it is dropped.
G_PREFIX_SECONDS = 0.6


def tab_switch_bindings() -> list[Binding]:
    """Build the fixed key bindings for the tab bar.

    Kept as a function so the test can assert the exact set the app registers.
    """
    bindings = [
        Binding("h", "previous_tab", "Prev tab"),
        Binding("l", "next_tab", "Next tab"),
        Binding("j", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up"),
        Binding("g", "first_row", "First row"),
        Binding("G", "last_row", "Last row"),
    ]
    for index in range(TAB_LIMIT):
        key = "ctrl+0" if index == TAB_LIMIT - 1 else f"ctrl+{index + 1}"
        bindings.append(
            Binding(key, f"switch_tab({index})", f"Tab {index + 1}", show=False)
        )
    bindings.append(Binding("t", "toggle_table", "Table"))
    bindings.append(Binding("escape", "back_to_grid", "Grid", show=False))
    bindings.append(Binding("slash", "search", "Search"))
    bindings.append(Binding("question_mark", "help", "Help"))
    return bindings


class BrigadeTabbedContent(TabbedContent):
    """Project tabs plus the per-project waterfall body.

    A tab clicked by the user drops into the project waterfall. A pane added
    or rebuilt by the poll does not, so a new tab never steals the view.
    """

    def _on_tabs_tab_activated(self, event) -> None:  # type: ignore[override]
        super()._on_tabs_tab_activated(event)
        if not getattr(self.app, "_rendering", False):
            self.app.enter_project_view()


class SearchScreen(ModalScreen):
    """Session browser. Opens on ``/``.

    The live dashboard caps the tab bar to the 10 newest projects and each
    tab to the newest 50 rows. This screen is how everything the live view
    hides stays reachable: it lists every session from every project, newest
    first, and filters as text is typed. Filtering matches project, harness,
    model, title and session id.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        # Down hands the filter box off to the result table. Once the table
        # has focus, the same key moves the cursor (the table's own binding).
        Binding("down", "focus_results", "Results", show=False),
        # Vim motions over the result rows. ``check_action`` disables these
        # while the filter box has focus, so ``j`` and ``k`` still type.
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "first_row", "First row", show=False),
        Binding("G", "last_row", "Last row", show=False),
        Binding("enter", "open_row", "Open", show=False),
    ]

    #: Actions that only make sense while the result table has focus.
    TABLE_ACTIONS = frozenset(
        {"cursor_down", "cursor_up", "first_row", "last_row"}
    )

    def __init__(self, sessions: Sequence[Session] | None = None) -> None:
        super().__init__()
        self.all_sessions: list[Session] = list(sessions or [])
        #: The sessions the table currently shows, in row order, so a
        #: selected row maps back to its session for the detail pane.
        self._rows: list[Session] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Search every session", id="search-title"),
            Input(
                placeholder="Filter by project, session, harness or model",
                id="search-input",
            ),
            DataTable(id="search-results", cursor_type="row"),
            id="search-box",
        )

    def on_mount(self) -> None:
        table = self.query_one("#search-results", DataTable)
        table.add_columns(*SESSION_COLUMNS)
        self._populate("")
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter at the filter box moves focus to the result table."""
        self._results_table().focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Let ``j`` and ``k`` type in the filter box but move in the table.

        The vim motions are disabled while the filter has focus, so the
        filter stays free text. They turn on the moment the table owns
        focus, which is how a keyboard-only user reaches and opens a row.
        """
        if action in self.TABLE_ACTIONS and not self._results_table().has_focus:
            return False
        return True

    def action_focus_results(self) -> None:
        """Move focus from the filter box down into the result table."""
        self._results_table().focus()

    def action_cursor_down(self) -> None:
        self._results_table().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._results_table().action_cursor_up()

    def action_first_row(self) -> None:
        table = self._results_table()
        if table.row_count:
            table.move_cursor(row=0)

    def action_last_row(self) -> None:
        table = self._results_table()
        if table.row_count:
            table.move_cursor(row=table.row_count - 1)

    def action_open_row(self) -> None:
        """Open the selected row, or focus the table when the filter has it."""
        table = self._results_table()
        if table.has_focus:
            if table.row_count:
                table.action_select_cursor()
        else:
            table.focus()

    def _results_table(self) -> DataTable:
        return self.query_one("#search-results", DataTable)

    def on_data_table_row_selected(
        self, event: DataTable.RowSelected
    ) -> None:
        """Enter on a search row opens the same session detail pane."""
        if 0 <= event.cursor_row < len(self._rows):
            self.app.open_session_detail(self._rows[event.cursor_row].session_id)

    def _populate(self, query: str) -> None:
        needle = query.strip().lower()
        matches = [
            session for session in self.all_sessions if _matches(session, needle)
        ]
        self._rows = matches
        table = self.query_one("#search-results", DataTable)
        table.clear()
        for session in matches:
            table.add_row(*session_cells(session))
        self.query_one("#search-title", Static).update(
            "Search every session "
            f"({len(matches)} shown, Down/Tab to the rows, Enter opens detail)"
        )


class HelpScreen(ModalScreen):
    """Shortcut list. Opens on ``?``."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    HELP_LINES = (
        "Shortcuts",
        "",
        "h                previous tab",
        "l                next tab",
        "ctrl+1 .. ctrl+0 switch to tab 1 to 10",
        "j / k            move the session list down / up (project view)",
        "gg               jump to the first row (project view)",
        "G                jump to the last row (project view)",
        "t                toggle timeline / flat table",
        "escape           back to the grid view",
        "/                search every session",
        "down / tab       move from the search filter to the result rows",
        "j / k            move the search result cursor once it has focus",
        "enter            open the selected search row's detail pane",
        "?                this help",
        "escape           close",
        "",
        "Click a grid card or a waterfall block to open the session detail",
        "pane. It shows the title, harness, model, directory, start, last",
        "activity, state, tokens, cost and a read-only transcript tail.",
        "",
        "The grid is the default view: one card per session, coloured by",
        "state. Red is blocked, green completed, blue running. The grid is",
        "read-only and has no list cursor, so j, k, gg and G do nothing there.",
        "The factory summary line above the tabs shows the whole factory:",
        "projects, running, idle, blocked, done and spend today.",
        "Each tab label shows that project's progress: done/total, the age of",
        "its newest activity, and a red blocked count when a session reports",
        "one.",
        "The tab bar above the grid drops into a project waterfall, which",
        "shows timing only.",
        "The tab bar shows the 10 newest projects and 50 rows each.",
        "Seed and scratch sessions (a cwd under a temp dir) own no tab.",
        "Search lists every session from every project, newest first.",
        "",
        "Read-only. Brigade never writes and never launches.",
    )

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("\n".join(self.HELP_LINES), id="help-text"),
            id="help-box",
        )


class BrigadeTUI(App):
    """The one-pane, read-only session viewer."""

    CSS = """
    #projects {
        height: 1fr;
    }

    #grid {
        display: none;
        height: 1fr;
    }

    #source-health {
        height: 1;
        padding: 0 1;
    }

    #factory-summary {
        height: 1;
        padding: 0 1;
    }

    #view-caps {
        height: 1;
        padding: 0 1;
    }

    Screen.grid-mode #projects {
        height: 2;
    }

    Screen.grid-mode #projects ContentSwitcher {
        display: none;
    }

    Screen.grid-mode #grid {
        display: block;
    }

    DataTable {
        height: 1fr;
    }

    .timeline {
        height: 1fr;
        overflow-x: hidden;
        overflow-y: auto;
    }

    SearchScreen, HelpScreen {
        align: center middle;
    }

    SessionDetailScreen {
        align: center middle;
    }

    #detail-box {
        width: 90%;
        height: 90%;
        padding: 1 2;
        border: round $foreground;
    }

    #detail-title {
        text-style: bold;
        padding-bottom: 1;
    }

    #detail-meta {
        height: auto;
        padding-bottom: 1;
        border-bottom: solid $foreground 30%;
    }

    #detail-tail-heading {
        padding: 1 0;
    }

    #detail-tail {
        height: 1fr;
        background: $surface;
    }

    #search-box, #help-box {
        padding: 1 2;
        border: round $foreground;
    }

    #search-box {
        width: 90%;
        height: 80%;
    }

    #help-box {
        width: 60;
        height: auto;
    }

    #search-title {
        padding-bottom: 1;
    }

    #search-results {
        height: 1fr;
    }

    #help-text {
        width: auto;
    }

    ContentTabs Tab:hover {
        color: $foreground;
        background: $foreground 25%;
    }
    """

    TITLE = "Brigade"
    SUB_TITLE = "read-only session view"
    BINDINGS = tab_switch_bindings()

    def __init__(
        self,
        projects: Iterable[str] | None = None,
        sources: Sequence[SessionSource] | None = None,
        refresh_seconds: float | None = None,
        running_window: float | None = None,
        waterfall_window: float | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.fixed_projects: tuple[str, ...] | None = (
            tuple(projects) if projects is not None else None
        )
        self.projects: tuple[str, ...] = (
            self.fixed_projects if self.fixed_projects is not None else DEFAULT_PROJECTS
        )
        self.store = SessionStore(sources if sources is not None else default_sources())
        #: Resolved view options. A None argument keeps the built-in default,
        #: so a caller with no config gets the documented behavior.
        self.refresh_seconds = (
            REFRESH_SECONDS if refresh_seconds is None else refresh_seconds
        )
        self.running_window = (
            RUNNING_WINDOW_SECONDS if running_window is None else running_window
        )
        self.waterfall_window = (
            WINDOW_SECONDS if waterfall_window is None else waterfall_window
        )
        #: The frozen display order of the visible project keys. A project
        #: keeps its position across polls; a new project takes the left end.
        self._tab_order: list[str] = []
        #: One stable pane id per project key, so a tab keeps its identity
        #: even when the visible set or its position changes.
        self._pane_ids_by_key: dict[str, str] = {}
        self._pane_keys: list[str] = []
        self._rendering = False
        #: True after one ``g`` while the app waits for the second ``g``.
        self._g_pending = False
        self._g_timer = None
        #: Every session from the last poll. The search browser reads this,
        #: so it sees projects and rows the live dashboard caps away.
        self._sessions: list[Session] = []
        #: Detail-pane tokens, rebuilt each poll. A rendered card or block
        #: carries the token in its click action, so a click opens exactly the
        #: session it was drawn for, even after a later poll.
        self._detail_tokens: dict[str, Session] = {}
        self._detail_token_by_id: dict[str, str] = {}
        #: The model to colour map for the whole session set, so a model
        #: keeps one colour across every tab and every poll.
        self._model_colours: dict[str, str] = {}
        #: The flat table is hidden until ``t`` asks for it. The swimlane
        #: timeline is the project tab body.
        self._show_table = False
        #: True when the grid main view is shown instead of a project
        #: waterfall. The grid is the default view.
        self._grid_mode = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="source-health")
        yield Static(id="factory-summary")
        yield Static(id="view-caps")
        yield BrigadeTabbedContent(id="projects")
        yield SessionGrid(id="grid")
        yield Footer()

    async def on_mount(self) -> None:
        self.screen.set_class(self._grid_mode, "grid-mode")
        await self.refresh_sessions()
        self._show_grid()
        if self.fixed_projects is None:
            self.set_interval(self.refresh_seconds, self.refresh_sessions)

    async def refresh_sessions(self) -> None:
        """Poll every source and repaint the tabs. Read-only."""
        if self._rendering:
            return
        self._rendering = True
        try:
            sessions, health = self.store.poll()
            self._sessions = sessions
            self._register_detail_targets(sessions)
            self._model_colours = assign_model_colours(
                session.model for session in sessions
            )
            self.query_one("#source-health", Static).update(
                render_health_strip(health)
            )
            if self.fixed_projects is not None:
                pairs = [
                    (ProjectRef(key=name, label=name), [])
                    for name in self.fixed_projects
                ]
            else:
                pairs = self.store.by_project(sessions)
            now = datetime.now(timezone.utc)
            # The summary counts every project in the snapshot, including a
            # project past the 10 tab cap. The tab bar still caps at 10.
            self.query_one("#factory-summary", Static).update(
                render_summary(build_summary(pairs, now))
            )
            await self._render(pairs, now)
            # State the caps out loud: how many projects and rows the live
            # view shows against how many the stores hold, so a project or
            # row hidden by a cap is never a silent loss.
            self.query_one("#view-caps", Static).update(
                render_view_caps(build_view_caps(pairs, self._tab_order))
            )
            # The grid shows the same projects as the tab view: unattributed
            # worktrees and scratch runs own no heading.
            grid = self.query_one("#grid", SessionGrid)
            grid.click_action = self.detail_action
            grid.update_sessions(
                [session for session in sessions if not is_scratch(session)],
                window=self.running_window,
            )
        finally:
            self._rendering = False

    def _stable_order(
        self, ranked_keys: list[str], active_key: str | None
    ) -> list[str]:
        """Freeze the tab order across polls.

        A project keeps its tab position once it owns one, so a session
        update never moves a click target. A brand-new project takes the
        left end, which is the ISA rule for a new tab. The active project
        stays in the bar even when it falls outside the newest ten, so the
        poll cannot steal the view.
        """
        if not self._tab_order:
            return ranked_keys[:TAB_LIMIT]
        # The newest projects own the bar. The active project is pinned in,
        # and the lowest-ranked non-active project makes room for it.
        desired = list(ranked_keys[:TAB_LIMIT])
        if active_key in ranked_keys and active_key not in desired:
            desired.append(active_key)
            for index in range(len(desired) - 1, -1, -1):
                if desired[index] != active_key:
                    del desired[index]
                    break
        desired_set = set(desired)
        existing = [key for key in self._tab_order if key in desired_set]
        existing_set = set(existing)
        newcomers = [key for key in desired if key not in existing_set]
        return newcomers + existing

    async def _render(
        self,
        pairs: list[tuple[ProjectRef, list[Session]]],
        now: datetime,
    ) -> None:
        # The live view: only the newest projects own a tab, and each tab is
        # capped to the newest rows. Older projects stay off screen. The tab
        # order is frozen across polls, so a poll never reorders the bar.
        key_map = {project.key: (project, sessions) for project, sessions in pairs}
        ranked_keys = [project.key for project, _ in pairs]
        tabs = self.query_one("#projects", TabbedContent)
        active_key = (
            self._pane_keys[self._active_index()]
            if self._pane_keys and tabs.active
            else None
        )
        order = self._stable_order(ranked_keys, active_key)
        self._tab_order = order
        ordered = [key_map[key] for key in order]
        names = unique_labels(ordered)
        labels = progress_labels(ordered, names, now)
        if order != self._pane_keys:
            # Removal is async. Wait for it before reusing pane ids, or the
            # old ContentTab collides with the new pane and raises DuplicateIds.
            await tabs.clear_panes()
            self._pane_keys = []
            for index, ((project, sessions), label) in enumerate(zip(ordered, labels)):
                timeline = TimelineView(
                    sessions,
                    model_colours=self._model_colours,
                    now=datetime.now(timezone.utc),
                    window_seconds=self.waterfall_window,
                    id=f"timeline-{index}",
                    classes="timeline",
                )
                timeline.click_action = self.detail_action
                table = DataTable(
                    id=f"sessions-{index}", cursor_type="row", classes="session-table"
                )
                timeline.display = not self._show_table
                table.display = self._show_table
                pane = TabPane(label, timeline, table, id=self._pane_id(project.key))
                await tabs.add_pane(pane)
                self._pane_keys.append(project.key)
                self._fill_table(table, sessions)
            if active_key in self._pane_keys:
                tabs.active = self._pane_id(active_key)
            self._apply_view()
        else:
            for index, (_project, sessions) in enumerate(ordered):
                table = self.query_one(f"#sessions-{index}", DataTable)
                self._fill_table(table, sessions)
                timeline = self.query_one(f"#timeline-{index}", TimelineView)
                timeline.click_action = self.detail_action
                timeline.update_sessions(
                    sessions,
                    self._model_colours,
                    now=datetime.now(timezone.utc),
                    window_seconds=self.waterfall_window,
                )
        tabs.display = bool(order)

    def _fill_table(self, table: DataTable, sessions: list[Session]) -> None:
        # Keep the cursor on the same row across a live poll, or every refresh
        # would yank the list back to the top while Dan is moving through it.
        previous_row = table.cursor_row if table.row_count else 0
        table.clear(columns=True)
        table.add_columns("Harness", "Model", "Last", "Tokens", "Cost", "Session")
        for session in sessions[:MAX_ROWS_PER_TAB]:
            table.add_row(
                session.harness.value,
                _short(session.model),
                _ago(session.last_activity),
                _tokens(session.usage.total_tokens),
                _cost(session.usage.cost),
                session_label(session),
            )
        if table.row_count:
            table.move_cursor(row=min(previous_row, table.row_count - 1))

    def _pane_id(self, key: str) -> str:
        """Return the stable pane id for a project key, assigning one once."""
        pane_id = self._pane_ids_by_key.get(key)
        if pane_id is None:
            pane_id = f"pane-{len(self._pane_ids_by_key)}"
            self._pane_ids_by_key[key] = pane_id
        return pane_id

    def _pane_ids(self) -> list[str]:
        return [pane.id for pane in self.query(TabPane) if pane.id]

    def _tabbed_content(self) -> TabbedContent:
        return self.query_one("#projects", TabbedContent)

    def _active_index(self) -> int:
        ids = self._pane_ids()
        if not ids:
            return 0
        active = self._tabbed_content().active
        return ids.index(active) if active in ids else 0

    def _activate(self, index: int) -> None:
        ids = self._pane_ids()
        if 0 <= index < len(ids):
            self._tabbed_content().active = ids[index]
            self.enter_project_view()

    def _show_grid(self) -> None:
        """Show the grid main view and focus it."""
        grid = self.query_one("#grid", SessionGrid)
        if not grid.can_focus:
            grid.can_focus = True
        grid.focus()

    def enter_project_view(self) -> None:
        """Dropping into a project tab shows that project's waterfall.

        Only a user action calls this: ``h``, ``l``, ``ctrl+1``..``ctrl+0``
        or a tab click. A poll that adds a tab or changes a session never
        calls it, so the active view never moves on its own.
        """
        if self._grid_mode:
            self._grid_mode = False
            self.screen.set_class(False, "grid-mode")
        self._focus_active_view()

    def action_back_to_grid(self) -> None:
        """``escape``: leave the project waterfall and show the grid again."""
        if not self._grid_mode:
            self._grid_mode = True
            self.screen.set_class(True, "grid-mode")
        self._show_grid()

    def _active_table(self) -> DataTable | None:
        """The session table for the active tab, or None when there is none."""
        try:
            return self.query_one(f"#sessions-{self._active_index()}", DataTable)
        except NoMatches:
            return None

    def _active_timeline(self) -> TimelineView | None:
        """The timeline for the active tab, or None when there is none."""
        try:
            return self.query_one(f"#timeline-{self._active_index()}", TimelineView)
        except NoMatches:
            return None

    def _apply_view(self) -> None:
        """Show the timeline or the flat table, per the current toggle."""
        for timeline in self.query(TimelineView):
            timeline.display = not self._show_table
        for table in self.query("DataTable.session-table"):
            table.display = self._show_table

    def action_toggle_table(self) -> None:
        """``t``: swap the tab body between the timeline and the flat table."""
        self._show_table = not self._show_table
        self._apply_view()
        self._focus_active_view()

    def _focus_active_view(self) -> None:
        if self._grid_mode:
            self._show_grid()
            return
        if self._show_table:
            table = self._active_table()
            if table is not None:
                table.focus()
            return
        timeline = self._active_timeline()
        if timeline is not None:
            timeline.focus()

    def action_next_tab(self) -> None:
        ids = self._pane_ids()
        if ids:
            self._activate((self._active_index() + 1) % len(ids))

    def action_previous_tab(self) -> None:
        ids = self._pane_ids()
        if ids:
            self._activate((self._active_index() - 1) % len(ids))

    def action_switch_tab(self, index: int) -> None:
        self._activate(index)

    def action_cursor_down(self) -> None:
        # The grid is read-only and has no list cursor, so a list key must
        # never move the hidden project table while the grid is shown.
        if self._grid_mode:
            return
        table = self._active_table()
        if table is not None:
            table.action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._grid_mode:
            return
        table = self._active_table()
        if table is not None:
            table.action_cursor_up()

    def action_first_row(self) -> None:
        """``gg``: a lone ``g`` waits briefly for the second one."""
        if self._grid_mode:
            return
        if self._g_pending:
            self._clear_g_prefix()
            table = self._active_table()
            if table is not None and table.row_count:
                table.move_cursor(row=0)
            return
        self._g_pending = True
        self._g_timer = self.set_timer(G_PREFIX_SECONDS, self._clear_g_prefix)

    def action_last_row(self) -> None:
        if self._grid_mode:
            return
        table = self._active_table()
        if table is not None and table.row_count:
            table.move_cursor(row=table.row_count - 1)

    def _clear_g_prefix(self) -> None:
        if self._g_timer is not None:
            self._g_timer.stop()
        self._g_pending = False
        self._g_timer = None

    def action_search(self) -> None:
        self.push_screen(SearchScreen(self._sessions))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def _register_detail_targets(self, sessions: Sequence[Session]) -> None:
        """Rebuild the token to session map the click actions point at.

        The token is derived from the session id, so a click action drawn on
        an earlier poll still resolves to the same session, or to nothing.
        """
        self._detail_tokens = {}
        self._detail_token_by_id = {}
        for session in sessions:
            token = _detail_token(session.session_id)
            self._detail_tokens[token] = session
            self._detail_token_by_id[session.session_id] = token

    def detail_action(self, session_id: str) -> str | None:
        """The click action a card or block carries for one session."""
        token = self._detail_token_by_id.get(session_id)
        if token is None:
            return None
        return f"app.open_detail('{token}')"

    def action_open_detail(self, token: str) -> None:
        """A click on a card or block: open the session detail pane."""
        session = self._detail_tokens.get(token)
        if session is None:
            return
        self.push_screen(SessionDetailScreen(session))

    def open_session_detail(self, session_id: str) -> None:
        """A search row was chosen: open the session detail pane."""
        session = next(
            (item for item in self._sessions if item.session_id == session_id),
            None,
        )
        if session is not None:
            self.push_screen(SessionDetailScreen(session))


def _detail_token(session_id: str) -> str:
    """A short, safe action token for one session id."""
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:12]
    return "d" + digest


#: Columns shared by the dashboard tables and the search browser. The final
#: column is the raw session id, so a search row is verifiable against the id
#: the user pasted into the filter.
SESSION_COLUMNS = (
    "Project",
    "Harness",
    "Model",
    "Last",
    "Tokens",
    "Cost",
    "Session",
    "Session ID",
)


def session_cells(session: Session) -> tuple[str, ...]:
    """One rendered row for a session, in ``SESSION_COLUMNS`` order.

    The label column names the session for a reader. The last column is the
    raw session id, so the row proves what the filter actually matched.
    """
    return (
        session.project.label,
        session.harness.value,
        _short(session.model),
        _ago(session.last_activity),
        _tokens(session.usage.total_tokens),
        _cost(session.usage.cost),
        session_label(session),
        session.session_id,
    )


def _matches(session: Session, needle: str) -> bool:
    """True when ``needle`` (already lowercased) is in the session's fields."""
    if not needle:
        return True
    haystack = " ".join(
        value
        for value in (
            session.project.key,
            session.project.label,
            session.harness.value,
            session.model,
            session.title,
            session.session_id,
        )
        if value
    ).lower()
    return needle in haystack


def _short(value: str | None, limit: int = 40) -> str:
    if not value:
        return "-"
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tokens(count: int) -> str:
    if count <= 0:
        return "-"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _cost(cost) -> str:
    if cost is None:
        return "-"
    return f"${cost:.4f}"


def _ago(when: datetime) -> str:
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point. Resolve the config, then run the app."""
    config = load_config(argv)
    BrigadeTUI(
        sources=sources_from_roots(
            config.sources.pi, config.sources.claude, config.sources.opencode
        ),
        refresh_seconds=config.view.refresh,
        running_window=config.view.running_window,
        waterfall_window=config.view.waterfall_window,
    ).run()


if __name__ == "__main__":
    main()
