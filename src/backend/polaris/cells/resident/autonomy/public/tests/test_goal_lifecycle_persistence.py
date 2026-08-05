"""GR2A tests for strict durable lifecycle transitions in existing Goal SSoT."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
from pathlib import Path
from queue import Empty
from typing import Any

import pytest
from polaris.cells.resident.autonomy.internal.goal_governor import GoalGovernor
from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage
from polaris.cells.resident.autonomy.public import (
    ApproveResidentGoalCommandV1,
    ArchiveResidentGoalCommandV1,
    CreateResidentGoalCommandV1,
    MaterializeResidentGoalCommandV1,
    ResidentGoalLifecycleErrorV1,
    StageResidentGoalCommandV1,
    approve_resident_goal,
    archive_resident_goal,
    create_resident_goal,
    materialize_resident_goal,
    reset_resident_services,
    stage_resident_goal,
)
from polaris.domain.models.resident import GoalStatus


def _hold_goal_lock(
    workspace: str,
    acquired: Any,
    release: Any,
) -> None:
    storage = ResidentStorage(workspace)
    lock_path = Path(f"{storage.paths.goals_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        acquired.set()
        assert release.wait(timeout=10)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _competing_goal_transition(
    workspace: str,
    goal_id: str,
    transition: str,
    ready: Any,
    results: Any,
) -> None:
    storage = ResidentStorage(workspace)
    governor = GoalGovernor(storage)
    ready.put(transition)
    try:
        if transition == "approve":
            goal = governor.approve_goal(goal_id, expected_revision=None)
        else:
            goal = governor.reject_goal(goal_id, expected_revision=None)
        results.put(("ok", transition, goal.status.value if goal is not None else "missing"))
    except ResidentGoalLifecycleErrorV1 as exc:
        results.put(("error", transition, exc.error_code))


def _created_governor(tmp_path: Path) -> tuple[GoalGovernor, str]:
    governor = GoalGovernor(ResidentStorage(str(tmp_path)))
    goal = governor.create_manual_proposal({"title": "Strict durable goal"})
    return governor, goal.goal_id


def test_goal_lifecycle_revision_persists_across_restart(tmp_path: Path) -> None:
    governor, goal_id = _created_governor(tmp_path)
    approved = governor.approve_goal(goal_id, expected_revision=0)
    assert approved is not None
    assert approved.status is GoalStatus.APPROVED
    assert approved.materialization_artifacts["goal_lifecycle_revision"] == 1

    restarted = GoalGovernor(ResidentStorage(str(tmp_path)))
    materialized = restarted.materialize_goal(goal_id, expected_revision=1)
    assert materialized is not None
    persisted = restarted.list_goals()[0]
    assert persisted.status is GoalStatus.MATERIALIZED
    assert persisted.materialization_artifacts["goal_lifecycle_revision"] == 2


def test_goal_same_state_is_idempotent_without_revision_increment(tmp_path: Path) -> None:
    governor, goal_id = _created_governor(tmp_path)
    first = governor.approve_goal(goal_id, note="first", expected_revision=0)
    replay = governor.approve_goal(goal_id, note="different ignored", expected_revision=1)
    assert first is not None and replay is not None
    assert replay.materialization_artifacts["goal_lifecycle_revision"] == 1
    assert replay.approval_note == "first"


def test_goal_lifecycle_cas_and_invalid_transition_fail_closed(tmp_path: Path) -> None:
    governor, goal_id = _created_governor(tmp_path)
    governor.approve_goal(goal_id, expected_revision=0)
    with pytest.raises(ResidentGoalLifecycleErrorV1) as cas_exc:
        governor.materialize_goal(goal_id, expected_revision=0)
    assert cas_exc.value.error_code == "goal_revision_conflict"
    with pytest.raises(ResidentGoalLifecycleErrorV1) as transition_exc:
        governor.reject_goal(goal_id, expected_revision=1)
    assert transition_exc.value.error_code == "invalid_goal_transition"


def test_archived_goal_terminal(tmp_path: Path) -> None:
    governor, goal_id = _created_governor(tmp_path)
    governor.reject_goal(goal_id, expected_revision=0)
    archived = governor.archive_goal(goal_id, expected_revision=1)
    assert archived is not None and archived.status is GoalStatus.ARCHIVED
    with pytest.raises(ResidentGoalLifecycleErrorV1) as exc_info:
        governor.approve_goal(goal_id, expected_revision=2)
    assert exc_info.value.error_code == "invalid_goal_transition"


def test_unknown_persisted_goal_status_and_invalid_json_fail_closed(tmp_path: Path) -> None:
    storage = ResidentStorage(str(tmp_path))
    goals_path = Path(storage.paths.goals_path)
    goals_path.write_text(
        json.dumps({"items": [{"goal_id": "goal-corrupt", "status": "mystery"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ResidentGoalLifecycleErrorV1) as status_exc:
        storage.load_goals()
    assert status_exc.value.error_code == "unknown_persisted_goal_state"

    goals_path.write_text("{bad-json", encoding="utf-8")
    with pytest.raises(ResidentGoalLifecycleErrorV1) as json_exc:
        storage.load_goals()
    assert json_exc.value.error_code == "goal_lifecycle_state_corrupt"


def test_public_goal_commands_expose_cas_and_archive(tmp_path: Path) -> None:
    reset_resident_services()
    created = create_resident_goal(
        CreateResidentGoalCommandV1(workspace=str(tmp_path), payload={"title": "Public CAS goal"})
    )
    goal_id = created["goal_id"]
    approved = approve_resident_goal(
        ApproveResidentGoalCommandV1(
            workspace=str(tmp_path),
            goal_id=goal_id,
            expected_revision=0,
        )
    )
    assert approved is not None
    materialized = materialize_resident_goal(
        MaterializeResidentGoalCommandV1(
            workspace=str(tmp_path),
            goal_id=goal_id,
            expected_revision=1,
        )
    )
    assert materialized is not None
    archived = archive_resident_goal(
        ArchiveResidentGoalCommandV1(
            workspace=str(tmp_path),
            goal_id=goal_id,
            expected_revision=2,
        )
    )
    assert archived is not None
    assert archived["status"] == "archived"


def test_goal_cas_is_atomic_across_processes(tmp_path: Path) -> None:
    governor, goal_id = _created_governor(tmp_path)
    del governor
    context = multiprocessing.get_context("fork")
    ready = context.Queue()
    results = context.Queue()
    lock_acquired = context.Event()
    release_lock = context.Event()
    lock_holder = context.Process(
        target=_hold_goal_lock,
        args=(str(tmp_path), lock_acquired, release_lock),
    )
    processes = [
        context.Process(
            target=_competing_goal_transition,
            args=(str(tmp_path), goal_id, transition, ready, results),
        )
        for transition in ("approve", "reject")
    ]
    lock_holder.start()
    assert lock_acquired.wait(timeout=5), "lock-holder process did not acquire goals lock"
    try:
        for process in processes:
            process.start()
        observed_ready = {ready.get(timeout=5) for _ in processes}
        assert observed_ready == {"approve", "reject"}
        with pytest.raises(Empty):
            results.get(timeout=0.5)
    finally:
        release_lock.set()

    lock_holder.join(timeout=10)
    assert lock_holder.exitcode == 0
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=2) for _ in processes]
    successes = [item for item in outcomes if item[0] == "ok"]
    failures = [item for item in outcomes if item[0] == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0][2] == "invalid_goal_transition"

    persisted = ResidentStorage(str(tmp_path)).load_goals()
    assert len(persisted) == 1
    assert persisted[0].status.value == successes[0][2]
    assert persisted[0].materialization_artifacts["goal_lifecycle_revision"] == 1


def test_stage_preserves_lifecycle_revision_across_repeat_and_reload(tmp_path: Path) -> None:
    reset_resident_services()
    created = create_resident_goal(
        CreateResidentGoalCommandV1(
            workspace=str(tmp_path),
            payload={"title": "Preserve lifecycle revision"},
        )
    )
    goal_id = created["goal_id"]
    approved = approve_resident_goal(
        ApproveResidentGoalCommandV1(
            workspace=str(tmp_path),
            goal_id=goal_id,
            expected_revision=0,
        )
    )
    assert approved is not None
    assert approved["materialization_artifacts"]["goal_lifecycle_revision"] == 1

    first = stage_resident_goal(StageResidentGoalCommandV1(workspace=str(tmp_path), goal_id=goal_id))
    assert first is not None
    assert first["goal"]["materialization_artifacts"]["goal_lifecycle_revision"] == 2

    repeated = stage_resident_goal(StageResidentGoalCommandV1(workspace=str(tmp_path), goal_id=goal_id))
    assert repeated is not None
    assert repeated["goal"]["materialization_artifacts"]["goal_lifecycle_revision"] == 2

    persisted = ResidentStorage(str(tmp_path)).load_goals()
    reloaded = next(goal for goal in persisted if goal.goal_id == goal_id)
    assert reloaded.status is GoalStatus.MATERIALIZED
    assert reloaded.materialization_artifacts["goal_lifecycle_revision"] == 2
