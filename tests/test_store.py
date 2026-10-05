"""Tests for the ranked, capped, labelled project view.

These build real domain objects. No store is mocked and nothing is written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from brigade_tui.model import Harness, ProjectRef, Session, is_scratch
from brigade_tui.store import SessionStore, unique_labels

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def session(key: str, label: str, minutes: int, index: int = 0) -> Session:
    return Session(
        harness=Harness.PI,
        session_id=f"{label}-{index}",
        project=ProjectRef(key=key, label=label),
        directory=key,
        last_activity=BASE + timedelta(minutes=minutes),
    )


def test_is_scratch_marks_a_temp_cwd_or_a_seed_prefix() -> None:
    now = BASE
    temp = Session(
        harness=Harness.PI,
        session_id="01a10446-a3f2-73df-8891-452b15f67f93",
        project=ProjectRef(key="/tmp/stw32/wt/STW-1", label="STW-1"),
        directory="/tmp/stw32/wt/STW-1",
        last_activity=now,
    )
    root = Session(
        harness=Harness.PI,
        session_id="seed-defect-one-01",
        project=ProjectRef(key="/srv/worktrees/STW-1", label="STW-1"),
        directory="/srv/worktrees/STW-1",
        last_activity=now,
    )
    real = Session(
        harness=Harness.PI,
        session_id="real-session-id",
        project=ProjectRef(key="/home/user/work/brigade-tui", label="brigade"),
        directory="/home/user/work/brigade-tui",
        last_activity=now,
    )
    assert is_scratch(temp) is True
    assert is_scratch(root) is True
    assert is_scratch(real) is False


def test_scratch_sessions_stay_out_of_the_tab_view() -> None:
    sessions = [
        # A scratch run is newer than the real project. It must not own a tab
        # and must not push the real project out.
        session("/tmp/stw32/wt/STW-1", "STW-1", 20),
        session("/home/user/work/brigade-tui", "brigade", 5),
    ]
    pairs = SessionStore([]).by_project(sessions)
    assert [project.key for project, _ in pairs] == ["/home/user/work/brigade-tui"]

    all_pairs = SessionStore([]).by_project(sessions, include_scratch=True)
    assert [project.key for project, _ in all_pairs] == [
        "/tmp/stw32/wt/STW-1",
        "/home/user/work/brigade-tui",
    ]


def test_unattributed_worktree_path_is_hidden_but_a_folded_one_is_not() -> None:
    now = BASE
    folded = Session(
        harness=Harness.PI,
        session_id="folded-worktree",
        project=ProjectRef(
            key="/home/user/work/brigade-tui",
            label="software-factory-tui",
        ),
        directory="/home/user/work/worktrees/STW-42",
        last_activity=now,
    )
    debris = Session(
        harness=Harness.PI,
        session_id="orphan-worktree",
        project=ProjectRef(
            key="/home/user/work/worktrees/STW-1",
            label="STW-1",
        ),
        directory="/home/user/work/worktrees/STW-1",
        last_activity=now,
    )
    assert is_scratch(folded) is False
    assert is_scratch(debris) is True


def test_by_project_ranks_projects_by_newest_session() -> None:
    sessions = [
        session("/a/one", "one", 1),
        session("/b/two", "two", 5),
        session("/a/one", "one", 10),
    ]
    pairs = SessionStore([]).by_project(sessions)

    assert [project.key for project, _ in pairs] == ["/a/one", "/b/two"]
    assert [len(group) for _, group in pairs] == [2, 1]
    # Inside a group the newest session is first.
    assert pairs[0][1][0].last_activity > pairs[0][1][1].last_activity


def test_unique_labels_disambiguate_a_shared_basename() -> None:
    pairs = [
        (ProjectRef("/srv/worktrees/STW-1", "STW-1"), []),
        (ProjectRef("/var/tmp/st-hb/worktrees/STW-1", "STW-1"), []),
        (ProjectRef("/srv/other", "other"), []),
    ]
    labels = unique_labels(pairs)

    assert len(set(labels)) == len(labels)
    assert labels[2] == "other"
    assert all("STW-1" in label for label in labels[:2])
    assert labels[0] != labels[1]
