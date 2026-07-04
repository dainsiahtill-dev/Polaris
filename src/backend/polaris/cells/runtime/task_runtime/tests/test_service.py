from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public.service import QueryFactEventsV1, query_fact_events
from polaris.cells.runtime.task_runtime.internal import service as service_module
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService, reset_runtime_task_records
from polaris.kernelone.storage import resolve_runtime_path


def _task_file_path(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.json"))


def _session_file_path(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.session.json"))


def test_task_runtime_service_normalizes_task_ids() -> None:
    assert TaskRuntimeService.normalize_task_id("task-12") == 12
    assert TaskRuntimeService.normalize_task_id("12") == 12
    assert TaskRuntimeService.normalize_task_id("task-12-extra") == 12
    assert TaskRuntimeService.normalize_task_id("bad-id") is None


def test_task_runtime_service_manages_task_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="wire runtime.v2 taskboard",
        description="use snapshot.tasks as primary source",
        metadata={"phase": "projection"},
    )
    assert created["id"] > 0

    updated = service.update_task_row(
        f"task-{created['id']}",
        status="in_progress",
        metadata={"owner_role": "director"},
    )
    assert updated is not None
    assert str(updated["status"]) == "in_progress"

    row = service.get_task(f"task-{created['id']}")
    assert isinstance(row, dict)
    assert row["subject"] == "wire runtime.v2 taskboard"
    assert row["status"] == "in_progress"
    assert row["metadata"]["owner_role"] == "director"

    rows = service.list_task_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]


def test_task_runtime_service_raw_list_all_is_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    service.create_task_row(subject="row projection only")

    with pytest.raises(RuntimeError, match="use list_task_rows"):
        service.list_all()


def test_task_runtime_service_ready_and_stats_entity_apis_are_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="ready row projection")

    ready_rows = service.list_ready_task_rows()
    stats = service.get_task_row_stats()

    assert [row["id"] for row in ready_rows] == [created["id"]]
    assert stats["total"] == 1
    assert stats["pending"] == 1
    assert stats["ready"] == 1
    with pytest.raises(RuntimeError, match="use list_ready_task_rows"):
        service.list_ready()
    with pytest.raises(RuntimeError, match="use list_ready_task_rows"):
        service.get_ready_tasks()
    with pytest.raises(RuntimeError, match="use get_task_row_stats"):
        service.get_stats()


def test_task_runtime_service_does_not_proxy_legacy_board_methods(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    for method_name in ("list_my_tasks", "get_dependency_graph", "get_critical_path"):
        with pytest.raises(AttributeError):
            getattr(service, method_name)


def test_create_task_row_reports_event_append_failure_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    row = service.create_task_row(
        subject="create with append evidence",
        description="return projection evidence without mutating task metadata",
        metadata={"phase": "projection"},
    )

    assert row["status"] == "pending"
    assert row["execution_event"] == {
        "ok": False,
        "event_type": "created",
        "published": False,
        "error": "fact stream unavailable",
    }
    persisted = service.get_task(row["id"])
    assert persisted is not None
    assert "execution_event" not in persisted
    assert "execution_event" not in persisted["metadata"]


def test_update_task_row_reports_event_append_failure_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="update with append evidence")

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    row = service.update_task_row(
        created["id"],
        status="in_progress",
        metadata={"owner_role": "director"},
    )

    assert row is not None
    assert row["status"] == "in_progress"
    assert row["execution_event"] == {
        "ok": False,
        "event_type": "updated",
        "published": False,
        "error": "fact stream unavailable",
    }
    persisted = service.get_task(row["id"])
    assert persisted is not None
    assert "execution_event" not in persisted
    assert "execution_event" not in persisted["metadata"]


def test_claim_execution_reports_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="claim with append evidence")

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-append-failure",
        selection_source="unit",
    )

    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    assert claimed["execution_event"] == {
        "ok": False,
        "event_type": "claimed",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert claimed["task"]["status"] == "in_progress"


def test_complete_execution_reports_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="complete with append evidence")
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-complete-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    completed = service.complete_execution(
        created["id"],
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )

    assert completed["success"] is True
    assert completed["reason"] == "completed"
    assert completed["execution_event"] == {
        "ok": False,
        "event_type": "completed",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert completed["task"]["status"] == "completed"


def test_task_runtime_service_wakes_ready_waiters_on_create(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    ready_events: list[str] = []
    listener_event = threading.Event()

    def on_ready() -> None:
        ready_events.append("ready")
        listener_event.set()

    unsubscribe = service.add_ready_listener(on_ready)
    waiter_started = threading.Event()
    wait_results: list[bool] = []

    def wait_for_ready() -> None:
        waiter_started.set()
        wait_results.append(service.wait_ready(timeout=1.0))

    waiter = threading.Thread(target=wait_for_ready)
    waiter.start()
    assert waiter_started.wait(timeout=0.5)

    service.create(subject="wake ready waiters")

    waiter.join(timeout=2.0)
    assert not waiter.is_alive()
    assert wait_results == [True]
    assert listener_event.wait(timeout=0.5)
    assert ready_events == ["ready"]

    unsubscribe()
    service.create(subject="listener already removed")
    assert ready_events == ["ready"]


def test_task_runtime_service_wakes_ready_waiters_when_dependency_unblocks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create(subject="parent task")
    child = service.create(subject="child task", blocked_by=[parent.id])
    claimed = service.claim_execution(
        parent.id,
        worker_id="director",
        role_id="director",
        run_id="run-unblock",
        selection_source="unit",
    )
    assert claimed["success"] is True
    assert service.wait_ready(timeout=0.0) is False

    ready_events: list[str] = []
    listener_event = threading.Event()

    def on_ready() -> None:
        ready_events.append("ready")
        listener_event.set()

    service.add_ready_listener(on_ready)
    waiter_started = threading.Event()
    wait_results: list[bool] = []

    def wait_for_ready() -> None:
        waiter_started.set()
        wait_results.append(service.wait_ready(timeout=1.0))

    waiter = threading.Thread(target=wait_for_ready)
    waiter.start()
    assert waiter_started.wait(timeout=0.5)

    completed = service.complete_execution(
        parent.id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="parent done",
    )

    waiter.join(timeout=2.0)
    assert completed["success"] is True
    assert not waiter.is_alive()
    assert wait_results == [True]
    assert listener_event.wait(timeout=0.5)
    assert ready_events == ["ready"]
    child_row = service.get_task(child.id)
    assert child_row is not None
    assert child_row["status"] == "pending"


def test_task_runtime_service_reconciles_terminal_session_before_reclaim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="completed task with stale row")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-terminal-session",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    task_path = _task_file_path(workspace, created.id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "pending"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert reclaimed["task"]["status"] == "completed"
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"


def test_task_runtime_ready_reset_row_with_older_terminal_session_is_claimable(tmp_path: Path) -> None:
    """A deliberate FAILED->READY reset must win over the stale terminal session.

    Regression: claim_execution used to crash with
    InvalidTaskStateTransitionError (ready -> failed) when reconciling the
    stale terminal session against a deliberately reset row.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="retry after failure via ready reset")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-ready",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    old_session_id = str(claimed["session"]["session_id"])

    failed = service.fail_execution(created.id, session_id=old_session_id, error="transient failure")
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task(created.id, status="ready")
    assert reset is not None
    assert reset.status.value == "ready"

    reclaimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-ready-2",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is True
    assert reclaimed["reason"] == "claimed"
    assert reclaimed["resumed"] is False
    assert str(reclaimed["session"]["session_id"]) != old_session_id
    assert reclaimed["task"]["status"] == "in_progress"
    persisted = json.loads(_task_file_path(workspace, created.id).read_text(encoding="utf-8"))
    assert persisted["status"] == "in_progress"


def test_task_runtime_deliberate_pending_retry_is_claimable_and_not_flipped_to_failed(tmp_path: Path) -> None:
    """A deliberate FAILED->PENDING retry must not be reconciled back to failed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="retry after failure via pending reset")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-pending",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    failed = service.fail_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        error="transient failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task(created.id, status="pending")
    assert reset is not None
    assert reset.status.value == "pending"

    reclaimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-pending-2",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is True
    assert reclaimed["task"]["status"] == "in_progress"
    persisted = json.loads(_task_file_path(workspace, created.id).read_text(encoding="utf-8"))
    assert persisted["status"] == "in_progress"

    completed = service.complete_execution(
        created.id,
        session_id=str(reclaimed["session"]["session_id"]),
        result_summary="second attempt worked",
    )
    assert completed["success"] is True
    assert completed["task"]["status"] == "completed"


def test_task_runtime_claim_next_honors_deliberate_retry_projection(tmp_path: Path) -> None:
    """Queue projection must not hide a deliberate retry behind stale session evidence."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="retry should be visible to queue")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-queue-retry",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    failed = service.fail_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        error="temporary platform failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task(created.id, status="pending")
    assert reset is not None
    assert reset.status.value == "pending"

    queued_rows = service.list_task_rows(include_terminal=False)
    assert [row["id"] for row in queued_rows] == [created.id]
    assert queued_rows[0]["status"] == "pending"
    assert (
        queued_rows[0]["metadata"]["runtime_execution"]["session_projection_authority"]
        == "row_reset_after_terminal_session"
    )

    reclaimed = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-queue-retry-2",
        selection_source="queue",
    )

    assert reclaimed["success"] is True
    assert reclaimed["task"]["id"] == created.id
    assert reclaimed["task"]["status"] == "in_progress"


def test_task_runtime_stale_pending_row_with_newer_terminal_session_still_rejects_reclaim(tmp_path: Path) -> None:
    """A stale row carrying an OLD reset marker must not beat a NEWER terminal session."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="stale row after second failure")
    first_claim = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-first",
        selection_source="task_id_lookup",
    )
    assert first_claim["success"] is True
    assert service.fail_execution(
        created.id,
        session_id=str(first_claim["session"]["session_id"]),
        error="first failure",
    )["success"]

    time.sleep(0.02)
    # Sanctioned retry: stamps the terminal-reset marker.
    assert service.update_task(created.id, status="pending") is not None
    second_claim = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-second",
        selection_source="task_id_lookup",
    )
    assert second_claim["success"] is True
    time.sleep(0.02)
    assert service.fail_execution(
        created.id,
        session_id=str(second_claim["session"]["session_id"]),
        error="second failure",
    )["success"]

    # Stale byte-level rewrite: pending row with the OLD reset marker, while
    # the terminal session on disk is NEWER than that marker.
    task_path = _task_file_path(workspace, created.id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "pending"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"


def test_task_runtime_stale_ready_row_reconcile_does_not_crash_claim(tmp_path: Path) -> None:
    """A stale byte-level READY rewrite must reconcile to terminal, not crash.

    READY -> failed has no valid state-machine transition; reconciliation must
    fall back to the evidence-based bridge instead of raising
    InvalidTaskStateTransitionError through the claim path.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="stale ready row over failed session")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-stale-ready",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    assert service.fail_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        error="genuine failure",
    )["success"]

    # Stale writer clobbers the row to READY without the sanctioned reset
    # marker (bypassing the state machine entirely).
    task_path = _task_file_path(workspace, created.id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "ready"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert "reconcile_error" not in reclaimed
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"


def test_task_runtime_service_preserves_terminal_session_during_run_cancellation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="completed task with stale active session")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-terminal-race",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    session_path = _session_file_path(workspace, created.id)
    stale_session = json.loads(session_path.read_text(encoding="utf-8"))
    stale_session["status"] = "active"
    stale_session["resumable"] = True
    stale_session["last_error"] = ""
    session_path.write_text(json.dumps(stale_session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    suspended = reloaded.suspend_active_executions_for_run(
        "run-terminal-race",
        reason="factory_stage_timeout",
    )

    assert suspended["success"] is True
    assert suspended["suspended_count"] == 0
    persisted_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted_session["status"] == "completed"
    persisted_task = json.loads(_task_file_path(workspace, created.id).read_text(encoding="utf-8"))
    assert persisted_task["status"] == "completed"


def test_task_runtime_stale_metadata_update_does_not_downgrade_completed_row(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    writer = TaskRuntimeService(str(workspace))

    created = writer.create(subject="completed task")
    stale_reader = TaskRuntimeService(str(workspace))
    stale_reader.get_task(created.id)

    claimed = writer.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-complete",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    completed = writer.complete_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    updated = stale_reader.update_task(created.id, metadata={"late_projection": "workspace_quality_gate_failed"})

    assert updated is not None
    assert updated.status.value == "completed"
    task_path = _task_file_path(workspace, created.id)
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
    assert persisted["metadata"]["late_projection"] == "workspace_quality_gate_failed"


def test_task_runtime_service_refreshes_stale_blocked_row_with_completed_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create(subject="completed prerequisite")
    child = service.create(
        subject="stale blocked child",
        blocked_by=[parent.id],
        metadata={"resolved_depends_on_task_ids": [parent.id]},
    )
    claim_parent = service.claim_execution(
        parent.id,
        worker_id="director",
        role_id="director",
        run_id="run-stale-unblock",
        selection_source="unit",
    )
    assert claim_parent["success"] is True
    completed = service.complete_execution(
        parent.id,
        session_id=str(claim_parent["session"]["session_id"]),
        result_summary="parent done",
    )
    assert completed["success"] is True

    stale = service.update_task(child.id, status="blocked")
    assert stale is not None
    assert str(stale.status.value) == "blocked"
    assert stale.blocked_by == []

    refresh = service.refresh_dependency_unblocks()
    assert refresh["unblocked_task_ids"] == [child.id]
    claim_child = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-stale-unblock",
        selection_source="unit",
    )

    assert claim_child["success"] is True
    assert claim_child["task"]["id"] == child.id


def test_task_runtime_service_blocks_missing_dependency_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    child = service.create(subject="child with missing dependency", blocked_by=[999])

    claim = service.claim_execution(
        child.id,
        worker_id="director",
        role_id="director",
        run_id="run-missing-dependency",
        selection_source="unit",
    )

    assert claim["success"] is False
    assert claim["reason"] == "task_blocked"


def test_task_runtime_reset_records_clears_rows_sessions_and_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="reset stale taskboard rows", metadata={"scope": "src/App.tsx"})
    claim = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-reset",
        selection_source="unit",
        external_task_id="task-reset",
    )
    assert claim["success"] is True

    taskboard_event_path = Path(resolve_runtime_path(str(workspace), "runtime/events/taskboard.terminal.events.jsonl"))
    taskboard_event_path.parent.mkdir(parents=True, exist_ok=True)
    taskboard_event_path.write_text('{"event_type":"completed"}\n', encoding="utf-8")

    result = reset_runtime_task_records(str(workspace))

    assert result["failed_count"] == 0
    assert TaskRuntimeService(str(workspace)).list_task_rows() == []
    tasks_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    assert not list(tasks_dir.glob("task_*.json"))
    assert (tasks_dir / ".max_id").is_file()
    assert not taskboard_event_path.exists()


def test_task_runtime_service_materializes_legacy_task_and_claims_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    row = service.ensure_task_row(
        external_task_id="task-0-director",
        subject="实现账单导出接口",
        description="生成导出模块并补充测试",
        metadata={"scope": "src/billing, tests/"},
    )

    assert str(row["status"]) == "pending"
    assert str(row["metadata"]["external_task_id"]) == "task-0-director"

    claim = service.claim_execution(
        row["id"],
        worker_id="director",
        role_id="director",
        run_id="run-materialized",
        selection_source="materialized_orchestration_task",
        external_task_id="task-0-director",
    )

    assert claim["success"] is True
    claimed_task = claim["task"]
    assert claimed_task["status"] == "in_progress"
    assert claimed_task["claimed_by"] == "director"
    assert claimed_task["workflow_run_id"] == "run-materialized"
    assert str(claim["session"]["session_id"])


def test_ensure_task_row_reports_materialized_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    append_count = 0
    original_append_event = service_module.append_fact_event

    def fail_materialized_append(command: object) -> object:
        nonlocal append_count
        append_count += 1
        event_type = str(getattr(command, "event_type", "") or "")
        if event_type == "materialized":
            raise RuntimeError("fact stream unavailable")
        return original_append_event(command)

    monkeypatch.setattr(service_module, "append_fact_event", fail_materialized_append)

    row = service.ensure_task_row(
        external_task_id="task-0-director",
        subject="实现账单导出接口",
        description="生成导出模块并补充测试",
        metadata={"scope": "src/billing, tests/"},
    )

    assert append_count >= 2
    assert row["status"] == "pending"
    assert row["execution_event"] == {
        "ok": False,
        "event_type": "materialized",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert len(row["execution_events"]) == 2
    assert row["execution_events"][0]["ok"] is True
    assert row["execution_events"][0]["event_type"] == "created"
    assert row["execution_events"][1] == row["execution_event"]


def test_task_runtime_external_task_id_does_not_collide_with_numeric_row(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    stale = service.create(subject="stale", description="old row")
    service.update(stale.id, status="completed", metadata={"previous_run": "old"})

    row = service.ensure_task_row(
        external_task_id="TASK-1",
        subject="current PM task",
        metadata={"pm_task_id": "TASK-1"},
    )

    assert row["id"] != stale.id
    external_lookup = service.get_task("TASK-1")
    assert external_lookup is not None
    assert external_lookup["id"] == row["id"]
    stale_lookup = service.get_task(f"task-{stale.id}")
    assert stale_lookup is not None
    assert stale_lookup["id"] == stale.id


def test_task_runtime_service_surfaces_resumable_task_and_reclaims_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(
        subject="实现账单模型",
        description="补齐数据模型和测试",
        metadata={"scope": "src/billing, tests/"},
    )

    first_claim = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-1",
        selection_source="task_id_lookup",
    )
    assert first_claim["success"] is True

    suspended = service.suspend_execution(
        created.id,
        session_id=str(first_claim["session"]["session_id"]),
        reason="director_execution_cancelled",
    )
    assert suspended["success"] is True
    assert suspended["task"]["status"] == "pending"
    assert suspended["task"]["resume_state"] == "resumable"

    selected = service.select_next_task(prefer_resumable=True)
    assert isinstance(selected, dict)
    assert int(selected["id"]) == int(created.id)
    assert selected["resume_state"] == "resumable"

    resumed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-2",
        selection_source="resumable_queue_fallback",
    )
    assert resumed["success"] is True
    assert resumed["resumed"] is True
    assert resumed["task"]["status"] == "in_progress"
    assert resumed["task"]["resume_state"] == "resumed"

    completed = service.complete_execution(
        created.id,
        session_id=str(resumed["session"]["session_id"]),
        result_summary="implemented billing model",
    )
    assert completed["success"] is True
    assert completed["task"]["status"] == "completed"


def test_task_runtime_service_suspends_active_sessions_for_cancelled_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    cancelled_task = service.create(subject="cancelled run task")
    other_task = service.create(subject="other run task")
    cancelled_claim = service.claim_execution(
        cancelled_task.id,
        worker_id="director",
        role_id="director",
        run_id="director-cancelled",
        selection_source="task_id_lookup",
    )
    other_claim = service.claim_execution(
        other_task.id,
        worker_id="director",
        role_id="director",
        run_id="director-other",
        selection_source="task_id_lookup",
    )
    assert cancelled_claim["success"] is True
    assert other_claim["success"] is True

    suspended = service.suspend_active_executions_for_run(
        "director-cancelled",
        reason="factory_stage_timeout",
        metadata={"cancelled_by": "unit-test"},
    )

    assert suspended["success"] is True
    assert suspended["reason"] == "suspended"
    assert suspended["suspended_count"] == 1
    assert suspended["task_ids"] == [str(cancelled_task.id)]
    assert len(suspended["execution_events"]) == 1
    assert suspended["execution_events"][0]["ok"] is True
    assert suspended["execution_events"][0]["event_type"] == "suspended"

    cancelled_heartbeat = service.heartbeat_execution(
        cancelled_task.id,
        session_id=str(cancelled_claim["session"]["session_id"]),
    )
    assert cancelled_heartbeat["success"] is False
    assert cancelled_heartbeat["reason"] == "session_not_active"
    cancelled_row = service.get_task(cancelled_task.id)
    assert cancelled_row is not None
    assert cancelled_row["status"] == "pending"
    assert cancelled_row["resume_state"] == "resumable"
    assert cancelled_row["metadata"]["cancellation_reason"] == "factory_stage_timeout"

    other_heartbeat = service.heartbeat_execution(
        other_task.id,
        session_id=str(other_claim["session"]["session_id"]),
    )
    assert other_heartbeat["success"] is True
    assert other_heartbeat["execution_event"]["ok"] is True
    assert other_heartbeat["execution_event"]["event_type"] == "heartbeat_renewed"


def test_heartbeat_execution_reports_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create(subject="heartbeat append evidence")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-heartbeat-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    heartbeat = service.heartbeat_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        context_summary="renew lease after tool dispatch",
    )

    assert heartbeat["success"] is True
    assert heartbeat["reason"] == "heartbeat_renewed"
    assert heartbeat["execution_event"] == {
        "ok": False,
        "event_type": "heartbeat_renewed",
        "published": False,
        "error": "fact stream unavailable",
    }


def test_suspend_active_executions_for_run_reports_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create(subject="cancel with append evidence")
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-cancel-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    suspended = service.suspend_active_executions_for_run(
        "run-cancel-append-failure",
        reason="factory_stage_timeout",
    )

    assert suspended["success"] is True
    assert suspended["suspended_count"] == 1
    assert suspended["execution_events"] == [
        {
            "ok": False,
            "event_type": "suspended",
            "published": False,
            "error": "fact stream unavailable",
        }
    ]


def test_task_runtime_service_persists_sessions_under_canonical_task_namespace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(
        subject="persist task session canonically",
        description="ensure runtime/tasks owns both rows and sessions",
    )

    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-canonical-session",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    canonical_path = f"runtime/tasks/task_{created.id}.session.json"
    legacy_path = f"runtime/tasks/sessions/task_{created.id}.session.json"

    assert service._kernel_fs.exists(canonical_path)
    assert not service._kernel_fs.exists(legacy_path)


def test_task_runtime_service_ignores_corrupt_session_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="recover corrupt session")
    service._kernel_fs.write_json_atomic(
        f"runtime/tasks/task_{created.id}.session.json",
        {
            "session_id": "tx-corrupt",
            "task_id": created.id,
            "role_id": "director",
            "status": "active",
            "lease_expires_at": "2026-01-01T00:02:00+00:00",
        },
        indent=2,
        ensure_ascii=False,
    )

    with caplog.at_level("WARNING"):
        claimed = service.claim_execution(
            created.id,
            worker_id="director",
            role_id="director",
            run_id="run-recovered",
            selection_source="task_id_lookup",
        )

    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    assert claimed["session"]["task_id"] == created.id
    assert claimed["session"]["session_id"] != "tx-corrupt"
    assert "Failed to parse task runtime session" in caplog.text
    assert "worker_id" in caplog.text


def test_task_runtime_service_writes_sessions_atomically(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(
        subject="persist task session atomically",
        description="session readers must never observe partial JSON writes",
    )

    atomic_calls: list[tuple[str, object, int, bool]] = []
    original_atomic_write = service._kernel_fs.write_json_atomic

    def write_json_atomic_spy(
        logical_path: str,
        payload: object,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> object:
        atomic_calls.append((logical_path, payload, indent, ensure_ascii))
        return original_atomic_write(
            logical_path,
            payload,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )

    def reject_non_atomic_session_write(*args: object, **kwargs: object) -> object:
        raise AssertionError("task runtime sessions must use write_json_atomic")

    monkeypatch.setattr(service._kernel_fs, "write_json_atomic", write_json_atomic_spy)
    monkeypatch.setattr(service._kernel_fs, "write_json", reject_non_atomic_session_write)

    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-atomic-session",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True
    assert atomic_calls
    logical_path, payload, indent, ensure_ascii = atomic_calls[-1]
    assert logical_path == f"runtime/tasks/task_{created.id}.session.json"
    assert isinstance(payload, dict)
    assert indent == 2
    assert ensure_ascii is False


def test_task_runtime_service_emits_execution_events_via_fact_stream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(
        subject="emit execution event",
        description="verify task_runtime.execution stream",
    )
    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="run-fact-stream",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created.id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    event_path = Path(resolve_runtime_path(str(workspace), "runtime/events/task_runtime.execution.jsonl"))
    assert event_path.is_file()
    content = event_path.read_text(encoding="utf-8")
    assert '"stream":"task_runtime.execution"' in content
    assert '"event_type":"created"' in content
    assert '"event_type":"completed"' in content

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert event_types[:3] == ["created", "claimed", "completed"]
    created_event = next(event for event in events if event.get("event_type") == "created")
    assert created_event["payload"]["execution_state"] == "pending"
    assert created_event["payload"]["details"] == {"source": "runtime.task_runtime.create"}

    completed_event = next(event for event in events if event.get("event_type") == "completed")
    payload = completed_event["payload"]
    assert payload["execution_state"] == "completed"
    assert payload["attempt"] == 1
    assert payload["resume_count"] == 0
    assert payload["last_result_summary"] == "done"
    assert payload["lease_expires_at"]


def test_task_runtime_update_and_reopen_emit_execution_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(subject="emit row update events")
    updated = service.update(created.id, status="completed", metadata={"qa": "failed"})
    assert updated is not None
    reopened = service.reopen(created.id, reason="qa_rework")
    assert reopened is not None

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert "updated" in event_types
    assert "reopened" in event_types

    updated_event = next(event for event in events if event.get("event_type") == "updated")
    assert updated_event["payload"]["status"] == "completed"
    assert updated_event["payload"]["details"]["metadata_updated"] is True

    reopened_event = next(event for event in events if event.get("event_type") == "reopened")
    assert reopened_event["payload"]["details"]["reason"] == "qa_rework"
    assert reopened_event["payload"]["execution_state"] == "pending"


def test_reopen_task_row_reports_event_append_failure_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create(subject="reopen with append evidence")
    updated = service.update(created.id, status="completed", metadata={"qa": "failed"})
    assert updated is not None
    original_append_event = service_module.append_fact_event

    def fail_reopened_append(command: object) -> object:
        event_type = str(getattr(command, "event_type", "") or "")
        if event_type == "reopened":
            raise RuntimeError("fact stream unavailable")
        return original_append_event(command)

    monkeypatch.setattr(service_module, "append_fact_event", fail_reopened_append)

    row = service.reopen_task_row(created.id, reason="qa_rework")

    assert row is not None
    assert row["status"] == "pending"
    assert row["execution_event"] == {
        "ok": False,
        "event_type": "reopened",
        "published": False,
        "error": "fact stream unavailable",
    }
    persisted = service.get_task(row["id"])
    assert persisted is not None
    assert "execution_event" not in persisted
    assert "execution_event" not in persisted["metadata"]


def test_task_runtime_factory_event_projects_fact_stream_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    published: dict[str, object] = {}

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            published["subject"] = subject
            published["payload"] = payload
            return True

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )

    created = service.create(
        subject="project fact receipt",
        description="factory execution event should point at the fact stream event",
        metadata={"factory_run_id": "factory_123456789abc"},
    )
    published.clear()

    claimed = service.claim_execution(
        created.id,
        worker_id="director",
        role_id="director",
        run_id="director-123456789abc",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True
    envelope = published["payload"]
    assert isinstance(envelope, dict)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["event_type"] == "claimed"
    assert payload["execution_state"] == "in_progress"
    assert payload["attempt"] == 1
    assert payload["resume_count"] == 0
    assert payload["lease_expires_at"]
    assert payload["fact_event_id"]
    assert payload["fact_stream"] == "task_runtime.execution"
    assert payload["fact_storage_path"] == "runtime/events/task_runtime.execution.jsonl"


def test_task_runtime_factory_event_preserves_payload_director_run_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    published: dict[str, object] = {}

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            published["subject"] = subject
            published["payload"] = payload
            return True

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )

    ok = service._publish_factory_execution_event(
        {
            "run_id": "director-123456789abc",
            "factory_run_id": "factory_123456789abc",
            "task_id": "task-1",
            "event_type": "completed",
            "status": "completed",
        }
    )

    assert ok is True
    envelope = published["payload"]
    assert isinstance(envelope, dict)
    assert envelope["run_id"] == "factory_123456789abc"
    assert envelope["channel"] == "event.factory:factory_123456789abc"
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["run_id"] == "director-123456789abc"
    assert payload["factory_run_id"] == "factory_123456789abc"
    assert payload["director_run_id"] == "director-123456789abc"
