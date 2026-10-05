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
        project=ProjectRef(key="/tmp/item32/wt/ITEM-1", label="ITEM-1"),
        directory="/tmp/item32/wt/ITEM-1",
        last_activity=now,
    )
    root = Session(
        harness=Harness.PI,
        session_id="seed-defect-one-01",
        project=ProjectRef(key="/srv/worktrees/ITEM-1", label="ITEM-1"),
        directory="/srv/worktrees/ITEM-1",
        last_activity=now,
    )
    real = Session(
        harness=Harness.PI,
        session_id="real-session-id",
        project=ProjectRef(key="/home/user/Workspace/work/brigade", label="brigade"),
        directory="/home/user/Workspace/work/brigade",
        last_activity=now,
    )
    assert is_scratch(temp) is True
    assert is_scratch(root) is True
    assert is_scratch(real) is False


def test_scratch_sessions_stay_out_of_the_tab_view() -> None:
    sessions = [
        # A scratch run is newer than the real project. It must not own a tab
        # and must not push the real project out.
        session("/tmp/item32/wt/ITEM-1", "ITEM-1", 20),
        session("/home/user/Workspace/work/brigade", "brigade", 5),
    ]
    pairs = SessionStore([]).by_project(sessions)
    assert [project.key for project, _ in pairs] == ["/home/user/Workspace/work/brigade"]

    all_pairs = SessionStore([]).by_project(sessions, include_scratch=True)
    assert [project.key for project, _ in all_pairs] == [
        "/tmp/item32/wt/ITEM-1",
        "/home/user/Workspace/work/brigade",
    ]


def test_unattributed_worktree_path_is_hidden_but_a_folded_one_is_not() -> None:
    now = BASE
    folded = Session(
        harness=Harness.PI,
        session_id="folded-worktree",
        project=ProjectRef(
            key="/home/user/Workspace/work/software-factory-tui",
            label="software-factory-tui",
        ),
        directory="/home/user/.local/share/workspace/orchestrator/worktrees/ITEM-42",
        last_activity=now,
    )
    debris = Session(
        harness=Harness.PI,
        session_id="orphan-worktree",
        project=ProjectRef(
            key="/home/user/.local/share/workspace/orchestrator/worktrees/ITEM-1",
            label="ITEM-1",
        ),
        directory="/home/user/.local/share/workspace/orchestrator/worktrees/ITEM-1",
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
        (ProjectRef("/srv/worktrees/ITEM-1", "ITEM-1"), []),
        (ProjectRef("/var/tmp/st-hb/worktrees/ITEM-1", "ITEM-1"), []),
        (ProjectRef("/srv/other", "other"), []),
    ]
    labels = unique_labels(pairs)

    assert len(set(labels)) == len(labels)
    assert labels[2] == "other"
    assert all("ITEM-1" in label for label in labels[:2])
    assert labels[0] != labels[1]
