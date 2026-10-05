"""Tests for the factory summary line and the per-project progress signal.

The summary counts every session by state and sums today's spend. The progress
signal is the same idea for one project, in a tab label. Blocked is shown only
when a source reports it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from brigade_tui.model import (
    Harness,
    ProjectRef,
    Session,
    Usage,
)
from brigade_tui.store import MAX_ROWS_PER_TAB, MAX_TABS
from brigade_tui.summary import (
    build_progress,
    build_summary,
    build_view_caps,
    format_age,
    progress_label,
    progress_labels,
    render_summary,
    render_view_caps,
)

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def session(
    project: str,
    session_id: str,
    *,
    age: float,
    cost: Decimal | None = None,
    reported_state: str | None = None,
) -> Session:
    return Session(
        harness=Harness.PI,
        session_id=session_id,
        project=ProjectRef(key=project, label=project),
        directory=project,
        last_activity=NOW - timedelta(seconds=age),
        usage=Usage(cost=cost),
        reported_state=reported_state,
    )


def pairs(*groups):
    return [
        (ProjectRef(key=name, label=name), sessions)
        for name, sessions in groups
    ]


def test_summary_counts_each_state_from_the_sessions() -> None:
    grouped = pairs(
        ("alpha", [session("alpha", "r", age=10)]),
        (
            "beta",
            [
                session("beta", "i", age=300),
                session("beta", "b", age=10, reported_state="blocked"),
                session("beta", "c", age=5000),
            ],
        ),
    )
    summary = build_summary(grouped, NOW)
    assert summary.projects == 2
    assert summary.running == 1
    assert summary.idle == 1
    assert summary.blocked == 1
    assert summary.done == 1
    # The line names every field the criterion asks for.
    line = render_summary(summary).plain
    assert line == "2 projects, 1 running, 1 idle, 1 blocked, 1 done, $0.00 today"


def test_view_caps_state_projects_and_rows_shown_out_of_totals() -> None:
    """The caps line states what is shown against what exists."""
    grouped = pairs(
        ("alpha", [session("alpha", f"a{i}", age=i) for i in range(60)]),
        ("beta", [session("beta", f"b{i}", age=i) for i in range(20)]),
        ("gamma", [session("gamma", "g", age=1)]),
    )
    caps = build_view_caps(grouped, ["alpha", "beta"])
    assert caps.projects_shown == 2
    assert caps.projects_total == 3
    # alpha is capped at 50, beta keeps its 20, gamma is hidden by the tab cap.
    assert caps.rows_shown == 50 + 20
    assert caps.rows_total == 60 + 20 + 1
    assert caps.tab_cap == MAX_TABS
    assert caps.row_cap == MAX_ROWS_PER_TAB
    line = caps.line()
    assert "2 of 3 projects (tab cap 10)" in line
    assert "70 of 81 rows (row cap 50)" in line
    assert render_view_caps(caps).plain == line


def test_view_caps_shows_everything_when_nothing_is_capped() -> None:
    """With few projects and rows, shown equals total and no loss is implied."""
    grouped = pairs(
        ("alpha", [session("alpha", "a", age=1)]),
        ("beta", [session("beta", "b", age=2)]),
    )
    caps = build_view_caps(grouped, ["alpha", "beta"])
    assert caps.projects_shown == caps.projects_total == 2
    assert caps.rows_shown == caps.rows_total == 2
    assert "2 of 2 projects" in caps.line()
    assert "2 of 2 rows" in caps.line()


def test_summary_spend_today_only_counts_today() -> None:
    today = session("alpha", "today", age=10, cost=Decimal("1.25"))
    old = session("alpha", "old", age=10, cost=Decimal("9.00"))
    yesterday = replace(old, last_activity=NOW - timedelta(days=2))
    summary = build_summary(pairs(("alpha", [today, yesterday])), NOW)
    assert summary.spend_today == Decimal("1.25")
    assert "$1.25 today" in render_summary(summary).plain


def test_summary_sums_reported_costs_and_invents_none() -> None:
    grouped = pairs(
        (
            "alpha",
            [
                session("alpha", "a", age=10, cost=Decimal("0.50")),
                session("alpha", "b", age=20, cost=None),
                session("alpha", "c", age=30, cost=Decimal("0.25")),
            ],
        )
    )
    assert build_summary(grouped, NOW).spend_today == Decimal("0.75")


def test_progress_signal_is_done_over_total_with_freshness() -> None:
    sessions = [
        session("alpha", "a", age=10),
        session("alpha", "b", age=10),
        session("alpha", "c", age=5000),
    ]
    progress = build_progress(sessions, NOW)
    assert progress.total == 3
    assert progress.done == 1
    assert progress.signal == "1/3"
    assert format_age(progress.age_seconds) == "10s"
    assert str(progress_label("alpha", progress)) == "alpha 1/3 10s"


def test_blocked_project_is_marked_and_red_in_the_tab_label() -> None:
    sessions = [
        session("beta", "a", age=10),
        session("beta", "b", age=10, reported_state="blocked"),
    ]
    progress = build_progress(sessions, NOW)
    label = progress_label("beta", progress)
    assert str(label) == "beta 0/2 1 blocked 10s"
    # The blocked count carries the grid's blocked colour.
    blocked_spans = [span for span in label.spans if "red" in str(span.style)]
    assert blocked_spans, "blocked count is not red"
    # A project with no block report carries no blocked word.
    clear = progress_label("beta", build_progress([session("beta", "a", age=10)], NOW))
    assert "blocked" not in str(clear)
    assert not [span for span in clear.spans if "red" in str(span.style)]


def test_progress_labels_keep_the_unique_name_leading() -> None:
    grouped = pairs(
        ("a", [session("a", "x", age=10)]),
        ("b", [session("b", "y", age=5000)]),
    )
    labels = progress_labels(grouped, ["a", "b"], NOW)
    assert [str(label) for label in labels] == ["a 0/1 10s", "b 1/1 1h"]
    assert all(str(label).split(" ")[0] in {"a", "b"} for label in labels)
