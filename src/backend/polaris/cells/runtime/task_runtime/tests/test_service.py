from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public.service import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
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
        status="ready",
        metadata={"owner_role": "director"},
    )
    assert updated is not None
    assert str(updated["status"]) == "ready"

    row = service.get_task(f"task-{created['id']}")
    assert isinstance(row, dict)
    assert row["subject"] == "wire runtime.v2 taskboard"
    assert row["status"] == "ready"
    assert row["metadata"]["owner_role"] == "director"

    rows = service.list_task_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]


def test_task_runtime_service_projects_rows_from_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-FACT",
            run_id="run-fact",
            payload={
                "task_id": "TASK-FACT",
                "run_id": "run-fact",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact",
                "task_row_snapshot": {
                    "id": "TASK-FACT",
                    "task_id": "TASK-FACT",
                    "subject": "Fact backed task",
                    "description": "Projected by task runtime owner",
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    rows = service.list_task_rows_from_execution_facts()

    assert len(rows) == 1
    assert rows[0]["task_id"] == "TASK-FACT"
    assert rows[0]["subject"] == "Fact backed task"
    assert rows[0]["description"] == "Projected by task runtime owner"
    assert rows[0]["running"] is True
    assert rows[0]["metadata"]["source"] == "task_runtime.execution_fact"


def test_task_runtime_service_observable_rows_overlay_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Observable task",
        description="File row should receive fact overlay",
        priority=2,
    )
    task_id = str(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-observable",
            payload={
                "task_id": task_id,
                "run_id": "run-observable",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-observable",
                "task_row_snapshot": created,
            },
        )
    )

    rows = service.list_observable_task_rows()

    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert rows[0]["subject"] == "Observable task"
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["running"] is True
    assert rows[0]["metadata"]["previous_status"] == "pending"
    assert rows[0]["metadata"]["source"] == "task_runtime.execution_fact"


def test_task_runtime_service_stats_use_observable_execution_fact_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Observable stats task",
        description="File row should not dominate execution facts",
    )
    task_id = str(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-observable-stats",
            payload={
                "task_id": task_id,
                "run_id": "run-observable-stats",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-observable-stats",
                "task_row_snapshot": created,
            },
        )
    )

    stats = service.get_task_row_stats()

    assert stats["total"] == 1
    assert stats["pending"] == 0
    assert stats["ready"] == 0
    assert stats["in_progress"] == 1


def test_task_runtime_service_raw_list_all_is_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    service.create_task_row(subject="row projection only")

    with pytest.raises(RuntimeError, match="use list_task_rows"):
        service.list_all()


def test_task_runtime_service_entity_apis_are_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="row projection only")

    with pytest.raises(RuntimeError, match="use create_task_row"):
        service.create(subject="legacy entity create")
    with pytest.raises(RuntimeError, match="use get_task"):
        service.get(created["id"])
    with pytest.raises(RuntimeError, match="use update_task_row"):
        service.update(created["id"], status="in_progress")
    with pytest.raises(RuntimeError, match="use update_task_row"):
        service.update_task(created["id"], status="in_progress")
    with pytest.raises(RuntimeError, match="use reopen_task_row"):
        service.reopen(created["id"])


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


def test_task_runtime_service_create_links_reverse_dependency_with_execution_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="parent task")
    parent_id = int(parent["id"])
    child = service.create_task_row(subject="child task", blocked_by=[parent_id])
    child_id = int(child["id"])

    parent_after = service.get_task(parent_id)
    assert parent_after is not None
    assert parent_after["blocks"] == [child_id]

    reverse_events = [
        event for event in child.get("execution_events", []) if event.get("event_type") == "reverse_dependency_linked"
    ]
    assert len(reverse_events) == 1
    reverse_event = reverse_events[0]
    assert reverse_event["ok"] is True
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    linked_payloads = [event["payload"] for event in events if event["event_type"] == "reverse_dependency_linked"]
    assert len(linked_payloads) == 1
    linked_payload = linked_payloads[0]
    assert linked_payload["details"]["dependent_task_id"] == child_id
    assert linked_payload["details"]["blocks"] == [child_id]
    assert linked_payload["task_row_snapshot"]["id"] == parent_id


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
        status="ready",
        metadata={"owner_role": "director"},
    )

    assert row is not None
    assert row["status"] == "ready"
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


def test_update_task_row_rejects_terminal_status_owner_bypass(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="terminal bypass guard")

    with pytest.raises(RuntimeError, match="terminal_task_status_requires_task_runtime_owner_transition:completed"):
        service.update_task_row(created["id"], status="completed")
    with pytest.raises(RuntimeError, match="execution_task_status_requires_task_runtime_owner_transition:in_progress"):
        service.update_task_row(created["id"], status="in_progress")
    with pytest.raises(RuntimeError, match="execution_task_status_requires_task_runtime_owner_transition:claimed"):
        service.update_task_row(created["id"], status="claimed")

    row = service.get_task(created["id"])
    assert row is not None
    assert row["status"] == "pending"


def test_claim_execution_fails_closed_on_execution_event_append_failure(
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

    assert claimed["success"] is False
    assert claimed["reason"] == "execution_event_append_failed"
    assert claimed["requested_reason"] == "claimed"
    assert claimed["failure_class"] == "ledger_append_failed"
    assert claimed["state_mutation_applied"] is True
    assert claimed["execution_event"] == {
        "ok": False,
        "event_type": "claimed",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert claimed["task"]["status"] == "in_progress"


def test_complete_execution_fails_closed_on_execution_event_append_failure(
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

    assert completed["success"] is False
    assert completed["reason"] == "execution_event_append_failed"
    assert completed["requested_reason"] == "completed"
    assert completed["failure_class"] == "ledger_append_failed"
    assert completed["state_mutation_applied"] is True
    assert completed["execution_event"] == {
        "ok": False,
        "event_type": "completed",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert completed["task"]["status"] == "completed"


def test_fail_execution_fails_closed_on_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="fail with append evidence")
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-fail-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    failed = service.fail_execution(
        created["id"],
        session_id=str(claimed["session"]["session_id"]),
        error="director execution failed",
    )

    assert failed["success"] is False
    assert failed["reason"] == "execution_event_append_failed"
    assert failed["requested_reason"] == "failed"
    assert failed["failure_class"] == "ledger_append_failed"
    assert failed["state_mutation_applied"] is True
    assert failed["execution_event"] == {
        "ok": False,
        "event_type": "failed",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert failed["task"]["status"] == "failed"


def test_suspend_execution_fails_closed_on_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="suspend with append evidence")
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-suspend-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    suspended = service.suspend_execution(
        created["id"],
        session_id=str(claimed["session"]["session_id"]),
        reason="factory_stage_timeout",
    )

    assert suspended["success"] is False
    assert suspended["reason"] == "execution_event_append_failed"
    assert suspended["requested_reason"] == "suspended"
    assert suspended["failure_class"] == "ledger_append_failed"
    assert suspended["state_mutation_applied"] is True
    assert suspended["execution_event"] == {
        "ok": False,
        "event_type": "suspended",
        "published": False,
        "error": "fact stream unavailable",
    }
    assert suspended["task"]["status"] == "pending"


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

    service.create_task_row(subject="wake ready waiters")

    waiter.join(timeout=2.0)
    assert not waiter.is_alive()
    assert wait_results == [True]
    assert listener_event.wait(timeout=0.5)
    assert ready_events == ["ready"]

    unsubscribe()
    service.create_task_row(subject="listener already removed")
    assert ready_events == ["ready"]


def test_task_runtime_service_wakes_ready_waiters_when_dependency_unblocks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="parent task")
    parent_id = int(parent["id"])
    child = service.create_task_row(subject="child task", blocked_by=[parent_id])
    claimed = service.claim_execution(
        parent_id,
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
        parent_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="parent done",
    )

    waiter.join(timeout=2.0)
    assert completed["success"] is True
    assert not waiter.is_alive()
    assert wait_results == [True]
    assert listener_event.wait(timeout=0.5)
    assert ready_events == ["ready"]
    child_row = service.get_task(child["id"])
    assert child_row is not None
    assert child_row["status"] == "pending"


def test_task_runtime_service_reconciles_terminal_session_before_reclaim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="completed task with stale row")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-terminal-session",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    task_path = _task_file_path(workspace, created_id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "pending"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert reclaimed["execution_event"]["ok"] is True
    assert reclaimed["execution_event"]["event_type"] == "terminal_session_reconciled"
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

    created = service.create_task_row(subject="retry after failure via ready reset")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-ready",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    old_session_id = str(claimed["session"]["session_id"])

    failed = service.fail_execution(created_id, session_id=old_session_id, error="transient failure")
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="ready")
    assert reset is not None
    assert reset["status"] == "ready"

    reclaimed = service.claim_execution(
        created_id,
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
    persisted = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "in_progress"


def test_task_runtime_deliberate_pending_retry_is_claimable_and_not_flipped_to_failed(tmp_path: Path) -> None:
    """A deliberate FAILED->PENDING retry must not be reconciled back to failed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="retry after failure via pending reset")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-pending",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    failed = service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="transient failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="pending")
    assert reset is not None
    assert reset["status"] == "pending"

    reclaimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-pending-2",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is True
    assert reclaimed["task"]["status"] == "in_progress"
    persisted = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "in_progress"

    completed = service.complete_execution(
        created_id,
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

    created = service.create_task_row(subject="retry should be visible to queue")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-queue-retry",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    failed = service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="temporary platform failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="pending")
    assert reset is not None
    assert reset["status"] == "pending"

    queued_rows = service.list_task_rows(include_terminal=False)
    assert [row["id"] for row in queued_rows] == [created_id]
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
    assert reclaimed["task"]["id"] == created_id
    assert reclaimed["task"]["status"] == "in_progress"


def test_task_runtime_stale_pending_row_with_newer_terminal_session_still_rejects_reclaim(tmp_path: Path) -> None:
    """A stale row carrying an OLD reset marker must not beat a NEWER terminal session."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="stale row after second failure")
    created_id = created["id"]
    first_claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-first",
        selection_source="task_id_lookup",
    )
    assert first_claim["success"] is True
    assert service.fail_execution(
        created_id,
        session_id=str(first_claim["session"]["session_id"]),
        error="first failure",
    )["success"]

    time.sleep(0.02)
    # Sanctioned retry: stamps the terminal-reset marker.
    assert service.update_task_row(created_id, status="pending") is not None
    second_claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-second",
        selection_source="task_id_lookup",
    )
    assert second_claim["success"] is True
    time.sleep(0.02)
    assert service.fail_execution(
        created_id,
        session_id=str(second_claim["session"]["session_id"]),
        error="second failure",
    )["success"]

    # Stale byte-level rewrite: pending row with the OLD reset marker, while
    # the terminal session on disk is NEWER than that marker.
    task_path = _task_file_path(workspace, created_id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "pending"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert reclaimed["execution_event"]["ok"] is True
    assert reclaimed["execution_event"]["event_type"] == "terminal_session_reconciled"
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

    created = service.create_task_row(subject="stale ready row over failed session")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-stale-ready",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    assert service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="genuine failure",
    )["success"]

    # Stale writer clobbers the row to READY without the sanctioned reset
    # marker (bypassing the state machine entirely).
    task_path = _task_file_path(workspace, created_id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "ready"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert "reconcile_error" not in reclaimed
    assert reclaimed["execution_event"]["ok"] is True
    assert reclaimed["execution_event"]["event_type"] == "terminal_session_reconciled"
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"


def test_task_runtime_service_preserves_terminal_session_during_run_cancellation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="completed task with stale active session")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-terminal-race",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    session_path = _session_file_path(workspace, created_id)
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
    persisted_task = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted_task["status"] == "completed"


def test_task_runtime_stale_metadata_update_does_not_downgrade_completed_row(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    writer = TaskRuntimeService(str(workspace))

    created = writer.create_task_row(subject="completed task")
    created_id = created["id"]
    stale_reader = TaskRuntimeService(str(workspace))
    stale_reader.get_task(created_id)

    claimed = writer.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-complete",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    completed = writer.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    updated = stale_reader.update_task_row(created_id, metadata={"late_projection": "workspace_quality_gate_failed"})

    assert updated is not None
    assert updated["status"] == "completed"
    task_path = _task_file_path(workspace, created_id)
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
    assert persisted["metadata"]["late_projection"] == "workspace_quality_gate_failed"


def test_task_runtime_service_refreshes_stale_blocked_row_with_completed_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="completed prerequisite")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="stale blocked child",
        blocked_by=[parent_id],
        metadata={"resolved_depends_on_task_ids": [parent_id]},
    )
    child_id = child["id"]
    claim_parent = service.claim_execution(
        parent_id,
        worker_id="director",
        role_id="director",
        run_id="run-stale-unblock",
        selection_source="unit",
    )
    assert claim_parent["success"] is True
    completed = service.complete_execution(
        parent_id,
        session_id=str(claim_parent["session"]["session_id"]),
        result_summary="parent done",
    )
    assert completed["success"] is True
    assert completed["dependency_execution_events"][0]["ok"] is True
    assert completed["dependency_execution_events"][0]["event_type"] == "dependencies_unblocked"

    stale = service.update_task_row(child_id, status="blocked")
    assert stale is not None
    assert str(stale["status"]) == "blocked"
    assert stale["blocked_by"] == []

    refresh = service.refresh_dependency_unblocks()
    assert refresh["unblocked_task_ids"] == [child_id]
    assert refresh["execution_events"][0]["ok"] is True
    assert refresh["execution_events"][0]["event_type"] == "dependencies_unblocked"
    fact_rows = service.list_task_rows_from_execution_facts()
    fact_child = next(row for row in fact_rows if row["id"] == child_id)
    assert fact_child["status"] == "pending"
    claim_child = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-stale-unblock",
        selection_source="unit",
    )

    assert claim_child["success"] is True
    assert claim_child["task"]["id"] == child_id


def test_task_runtime_service_records_dependency_blocker_refresh_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    completed_parent = service.create_task_row(subject="completed dependency")
    active_parent = service.create_task_row(subject="still active dependency")
    completed_parent_id = int(completed_parent["id"])
    active_parent_id = int(active_parent["id"])
    child = service.create_task_row(
        subject="partially blocked child",
        blocked_by=[completed_parent_id, active_parent_id],
    )
    child_id = int(child["id"])
    stale_child = service.update_task_row(child_id, status="blocked")
    assert stale_child is not None

    claimed = service.claim_execution(
        completed_parent_id,
        worker_id="director",
        role_id="director",
        run_id="run-partial-unblock",
        selection_source="unit",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        completed_parent_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="dependency done",
    )
    assert completed["success"] is True
    assert completed["dependency_execution_events"][0]["ok"] is True
    assert completed["dependency_execution_events"][0]["event_type"] == "dependency_blockers_refreshed"

    stale_child = service.update_task_row(
        child_id,
        status="blocked",
        blocked_by=[completed_parent_id, active_parent_id],
    )
    assert stale_child is not None
    refresh = service.refresh_dependency_unblocks()

    assert refresh["unblocked_task_ids"] == []
    assert refresh["refreshed_task_ids"] == [child_id]
    assert refresh["execution_events"][0]["ok"] is True
    assert refresh["execution_events"][0]["event_type"] == "dependency_blockers_refreshed"
    child_row = service.get_task(child_id)
    assert child_row is not None
    assert child_row["status"] == "blocked"
    assert child_row["blocked_by"] == [active_parent_id]


def test_task_runtime_service_blocks_missing_dependency_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    child = service.create_task_row(subject="child with missing dependency", blocked_by=[999])
    child_id = child["id"]

    claim = service.claim_execution(
        child_id,
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

    created = service.create_task_row(subject="reset stale taskboard rows", metadata={"scope": "src/App.tsx"})
    created_id = created["id"]
    claim = service.claim_execution(
        created_id,
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


def test_task_runtime_reset_rows_for_reexecution_emits_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="dirty task", metadata={"scope": "src/App.tsx"})
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-reset-row",
        selection_source="unit",
    )
    assert claimed["success"] is True
    assert _session_file_path(workspace, created_id).is_file()

    result = service.reset_task_rows_for_reexecution(source="unit.reset")

    assert result["success"] is True
    assert result["reset_files"] == [f"task_{created_id}.json"]
    assert result["deleted_session_files"] == [f"task_{created_id}.session.json"]
    assert not _session_file_path(workspace, created_id).exists()
    task_payload = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert task_payload["status"] == "pending"
    assert task_payload["assignee"] == ""
    assert "runtime_execution" not in task_payload["metadata"]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    reset_event = next(event for event in events if event.get("event_type") == "reexecution_reset")
    assert reset_event["payload"]["task_id"] == str(created_id)
    assert reset_event["payload"]["details"]["source"] == "unit.reset"


def test_task_runtime_import_rows_for_reexecution_preserves_ids_and_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    _session_file_path(workspace, 7).write_text('{"status":"active"}', encoding="utf-8")

    result = service.import_task_rows_for_reexecution(
        [
            {
                "id": 7,
                "subject": "legacy director task",
                "description": "resume existing PM/CE task",
                "status": "in_progress",
                "blocked_by": [],
                "metadata": {
                    "runtime_execution": {"status": "active"},
                    "workflow_run_id": "old-run",
                    "pm_task_id": "TASK-7",
                },
            }
        ],
        source="unit.import",
        source_task_dir="/tmp/source/runtime/tasks",
    )

    assert result["success"] is True
    assert result["imported_files"] == ["task_7.json"]
    assert result["deleted_session_files"] == ["task_7.session.json"]
    assert not _session_file_path(workspace, 7).exists()
    task_payload = json.loads(_task_file_path(workspace, 7).read_text(encoding="utf-8"))
    assert task_payload["id"] == 7
    assert task_payload["status"] == "pending"
    assert task_payload["metadata"] == {"pm_task_id": "TASK-7"}

    next_row = service.create_task_row(subject="next task")
    assert next_row["id"] == 8

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    imported_event = next(event for event in events if event.get("event_type") == "reexecution_imported")
    assert imported_event["payload"]["task_id"] == "7"
    assert imported_event["payload"]["details"]["source"] == "unit.import"
    assert imported_event["payload"]["details"]["source_task_dir"] == "/tmp/source/runtime/tasks"


def test_task_runtime_inspects_reexecution_source_task_rows(tmp_path: Path) -> None:
    task_dir = tmp_path / "runtime" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "task_2.json").write_text(
        json.dumps({"id": 2, "status": "failed", "metadata": {"pm_task_id": "TASK-2"}}),
        encoding="utf-8",
    )
    (task_dir / "task_2.session.json").write_text('{"status":"active"}', encoding="utf-8")
    (task_dir / "task_bad.json").write_text("{not-json", encoding="utf-8")
    (task_dir / "notes.json").write_text("{}", encoding="utf-8")

    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)

    assert inspection["task_rows"] == [{"id": 2, "status": "failed", "metadata": {"pm_task_id": "TASK-2"}}]
    assert inspection["task_files"] == ["task_2.json"]
    assert inspection["task_count"] == 1
    assert isinstance(inspection["latest_mtime"], float)


def test_task_runtime_reexecution_source_reader_rejects_non_task_dir(tmp_path: Path) -> None:
    non_task_dir = tmp_path / "runtime" / "not_tasks"
    non_task_dir.mkdir(parents=True)
    (non_task_dir / "task_1.json").write_text('{"id":1}', encoding="utf-8")

    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(non_task_dir)

    assert inspection == {
        "task_rows": [],
        "task_files": [],
        "task_count": 0,
        "latest_mtime": 0.0,
    }


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

    stale = service.create_task_row(subject="stale", description="old row")
    stale_id = stale["id"]
    claimed_stale = service.claim_execution(
        stale_id,
        worker_id="test",
        role_id="director",
        selection_source="unit",
    )
    assert claimed_stale["success"] is True
    service.complete_execution(
        stale_id,
        session_id=str(claimed_stale["session"]["session_id"]),
        result_summary="old row",
        metadata={"previous_run": "old"},
    )

    row = service.ensure_task_row(
        external_task_id="TASK-1",
        subject="current PM task",
        metadata={"pm_task_id": "TASK-1"},
    )

    assert row["id"] != stale_id
    external_lookup = service.get_task("TASK-1")
    assert external_lookup is not None
    assert external_lookup["id"] == row["id"]
    stale_lookup = service.get_task(f"task-{stale_id}")
    assert stale_lookup is not None
    assert stale_lookup["id"] == stale_id


def test_task_runtime_service_surfaces_resumable_task_and_reclaims_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="实现账单模型",
        description="补齐数据模型和测试",
        metadata={"scope": "src/billing, tests/"},
    )
    created_id = created["id"]

    first_claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-1",
        selection_source="task_id_lookup",
    )
    assert first_claim["success"] is True

    suspended = service.suspend_execution(
        created_id,
        session_id=str(first_claim["session"]["session_id"]),
        reason="director_execution_cancelled",
    )
    assert suspended["success"] is True
    assert suspended["task"]["status"] == "pending"
    assert suspended["task"]["resume_state"] == "resumable"

    selected = service.select_next_task(prefer_resumable=True)
    assert isinstance(selected, dict)
    assert int(selected["id"]) == int(created_id)
    assert selected["resume_state"] == "resumable"

    resumed = service.claim_execution(
        created_id,
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
        created_id,
        session_id=str(resumed["session"]["session_id"]),
        result_summary="implemented billing model",
    )
    assert completed["success"] is True
    assert completed["task"]["status"] == "completed"


def test_task_runtime_service_suspends_active_sessions_for_cancelled_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    cancelled_task = service.create_task_row(subject="cancelled run task")
    cancelled_task_id = cancelled_task["id"]
    other_task = service.create_task_row(subject="other run task")
    other_task_id = other_task["id"]
    cancelled_claim = service.claim_execution(
        cancelled_task_id,
        worker_id="director",
        role_id="director",
        run_id="director-cancelled",
        selection_source="task_id_lookup",
    )
    other_claim = service.claim_execution(
        other_task_id,
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
    assert suspended["task_ids"] == [str(cancelled_task_id)]
    assert len(suspended["execution_events"]) == 1
    assert suspended["execution_events"][0]["ok"] is True
    assert suspended["execution_events"][0]["event_type"] == "suspended"

    cancelled_heartbeat = service.heartbeat_execution(
        cancelled_task_id,
        session_id=str(cancelled_claim["session"]["session_id"]),
    )
    assert cancelled_heartbeat["success"] is False
    assert cancelled_heartbeat["reason"] == "session_not_active"
    cancelled_row = service.get_task(cancelled_task_id)
    assert cancelled_row is not None
    assert cancelled_row["status"] == "pending"
    assert cancelled_row["resume_state"] == "resumable"
    assert cancelled_row["metadata"]["cancellation_reason"] == "factory_stage_timeout"

    other_heartbeat = service.heartbeat_execution(
        other_task_id,
        session_id=str(other_claim["session"]["session_id"]),
    )
    assert other_heartbeat["success"] is True
    assert other_heartbeat["execution_event"]["ok"] is True
    assert other_heartbeat["execution_event"]["event_type"] == "heartbeat_renewed"


def test_heartbeat_execution_fails_closed_on_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="heartbeat append evidence")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
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
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        context_summary="renew lease after tool dispatch",
    )

    assert heartbeat["success"] is False
    assert heartbeat["reason"] == "execution_event_append_failed"
    assert heartbeat["requested_reason"] == "heartbeat_renewed"
    assert heartbeat["failure_class"] == "ledger_append_failed"
    assert heartbeat["state_mutation_applied"] is True
    assert heartbeat["execution_event"] == {
        "ok": False,
        "event_type": "heartbeat_renewed",
        "published": False,
        "error": "fact stream unavailable",
    }


def test_suspend_active_executions_for_run_fails_closed_on_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="cancel with append evidence")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
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

    assert suspended["success"] is False
    assert suspended["reason"] == "execution_event_append_failed"
    assert suspended["failure_class"] == "ledger_append_failed"
    assert suspended["suspended_count"] == 1
    assert suspended["failed"] == [
        {
            "reason": "execution_event_append_failed",
            "failure_class": "ledger_append_failed",
            "event_type": "suspended",
            "error": "fact stream unavailable",
        }
    ]
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

    created = service.create_task_row(
        subject="persist task session canonically",
        description="ensure runtime/tasks owns both rows and sessions",
    )
    created_id = created["id"]

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-canonical-session",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    canonical_path = f"runtime/tasks/task_{created_id}.session.json"
    legacy_path = f"runtime/tasks/sessions/task_{created_id}.session.json"

    assert service._kernel_fs.exists(canonical_path)
    assert not service._kernel_fs.exists(legacy_path)


def test_task_runtime_service_ignores_corrupt_session_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="recover corrupt session")
    created_id = created["id"]
    service._kernel_fs.write_json_atomic(
        f"runtime/tasks/task_{created_id}.session.json",
        {
            "session_id": "tx-corrupt",
            "task_id": created_id,
            "role_id": "director",
            "status": "active",
            "lease_expires_at": "2026-01-01T00:02:00+00:00",
        },
        indent=2,
        ensure_ascii=False,
    )

    with caplog.at_level("WARNING"):
        claimed = service.claim_execution(
            created_id,
            worker_id="director",
            role_id="director",
            run_id="run-recovered",
            selection_source="task_id_lookup",
        )

    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    assert claimed["session"]["task_id"] == created_id
    assert claimed["session"]["session_id"] != "tx-corrupt"
    assert "Failed to parse task runtime session" in caplog.text
    assert "worker_id" in caplog.text


def test_task_runtime_service_writes_sessions_atomically(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="persist task session atomically",
        description="session readers must never observe partial JSON writes",
    )
    created_id = created["id"]

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
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-atomic-session",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True
    assert atomic_calls
    logical_path, payload, indent, ensure_ascii = atomic_calls[-1]
    assert logical_path == f"runtime/tasks/task_{created_id}.session.json"
    assert isinstance(payload, dict)
    assert indent == 2
    assert ensure_ascii is False


def test_task_runtime_service_emits_execution_events_via_fact_stream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="emit execution event",
        description="verify task_runtime.execution stream",
    )
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-fact-stream",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created_id,
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

    created = service.create_task_row(subject="emit row update events")
    created_id = created["id"]
    child = service.create_task_row(subject="dependent row", blocked_by=[int(created_id)])
    child_id = int(child["id"])
    updated = service.update_task_row(created_id, metadata={"qa": "failed"})
    assert updated is not None
    claimed = service.claim_execution(
        created_id,
        worker_id="test",
        role_id="director",
        selection_source="unit",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True
    unblocked_child = service.get_task(child_id)
    assert unblocked_child is not None
    assert unblocked_child["status"] == "pending"
    assert unblocked_child["blocked_by"] == []
    reopened = service.reopen_task_row(created_id, reason="qa_rework")
    assert reopened is not None

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert "updated" in event_types
    assert "reopened" in event_types
    assert "downstream_dependency_reblocked" in event_types

    updated_event = next(event for event in events if event.get("event_type") == "updated")
    assert updated_event["payload"]["status"] == "pending"
    assert updated_event["payload"]["details"]["metadata_updated"] is True

    reopened_event = next(event for event in events if event.get("event_type") == "reopened")
    assert reopened_event["payload"]["details"]["reason"] == "qa_rework"
    assert reopened_event["payload"]["execution_state"] == "pending"

    reblocked_event = next(event for event in events if event.get("event_type") == "downstream_dependency_reblocked")
    assert reblocked_event["payload"]["task_row_snapshot"]["id"] == child_id
    assert reblocked_event["payload"]["details"]["reopened_task_id"] == int(created_id)
    assert reblocked_event["payload"]["details"]["active_blockers"] == [int(created_id)]
    child_after = service.get_task(child_id)
    assert child_after is not None
    assert child_after["status"] == "blocked"
    assert child_after["blocked_by"] == [int(created_id)]


def test_task_runtime_rework_exhaustion_failure_is_owner_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="qa-owned rework exhausted")
    created_id = int(created["id"])
    child = service.create_task_row(subject="dependent row", blocked_by=[created_id])
    child_id = int(child["id"])
    claimed = service.claim_execution(
        created_id,
        worker_id="director-worker",
        role_id="director",
        run_id="run-qa",
        selection_source="test",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="director completed",
    )
    assert completed["success"] is True

    failed = service.fail_task_row_after_rework_exhausted(
        created_id,
        reason="qa_rework_retry_exhausted",
        metadata={"qa_last_verdict": "FAIL"},
        source="qa_verdict",
    )

    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["metadata"]["qa_last_verdict"] == "FAIL"
    assert failed["metadata"]["runtime_execution"]["status"] == "failed"
    assert failed["metadata"]["runtime_execution"]["last_error"] == "qa_rework_retry_exhausted"
    execution_event_types = [str(event.get("event_type") or "") for event in failed.get("execution_events", [])]
    assert execution_event_types[-2:] == ["downstream_dependency_reblocked", "failed"]

    child_after = service.get_task(child_id)
    assert child_after is not None
    assert child_after["status"] == "blocked"
    assert child_after["blocked_by"] == [created_id]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert "reopened" in event_types
    assert "failed" in event_types
    failed_event = events[-1]
    assert failed_event["event_type"] == "failed"
    assert failed_event["payload"]["execution_state"] == "failed"
    assert failed_event["payload"]["details"] == {
        "reason": "qa_rework_retry_exhausted",
        "source": "qa_verdict",
        "rework_exhausted": True,
    }


def test_task_runtime_dedup_cancel_is_owner_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    primary = service.create_task_row(subject="primary task")
    duplicate = service.create_task_row(subject="duplicate task")

    cancelled = service.cancel_task_row_for_deduplication(
        duplicate["id"],
        primary_task_id=primary["id"],
        reason="pm_duplicate_subject",
        metadata={"dedup_source": "pm_adapter"},
        source="pm_adapter",
    )

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["metadata"]["dedup_merged_into"] == primary["id"]
    assert cancelled["metadata"]["dedup_reason"] == "pm_duplicate_subject"
    assert cancelled["metadata"]["dedup_source"] == "pm_adapter"
    assert cancelled["execution_event"]["event_type"] == "cancelled"

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    cancelled_event = events[-1]
    assert cancelled_event["event_type"] == "cancelled"
    assert cancelled_event["payload"]["execution_state"] == "cancelled"
    assert cancelled_event["payload"]["details"] == {
        "reason": "pm_duplicate_subject",
        "source": "pm_adapter",
        "dedup_merged_into": str(primary["id"]),
    }


def test_task_runtime_role_adapter_failure_is_owner_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="pm planning task")
    failed = service.fail_task_row_from_role_adapter(
        created["id"],
        reason="pm_runtime_exception",
        metadata={"pm_error": "llm kernel offline"},
        role_id="pm",
        source="pm_adapter",
        failure_class="pm_runtime_exception",
    )

    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["metadata"]["pm_error"] == "llm kernel offline"
    assert failed["metadata"]["role_adapter_failure_reason"] == "pm_runtime_exception"
    assert failed["metadata"]["role_adapter_failure_role"] == "pm"
    assert failed["metadata"]["role_adapter_failure_class"] == "pm_runtime_exception"
    assert failed["execution_event"]["event_type"] == "failed"

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    failed_event = events[-1]
    assert failed_event["event_type"] == "failed"
    assert failed_event["payload"]["execution_state"] == "failed"
    assert failed_event["payload"]["details"] == {
        "reason": "pm_runtime_exception",
        "source": "pm_adapter",
        "role": "pm",
        "failure_class": "pm_runtime_exception",
    }


def test_reopen_task_row_reports_event_append_failure_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="reopen with append evidence")
    created_id = created["id"]
    updated = service.update_task_row(created_id, metadata={"qa": "failed"})
    assert updated is not None
    claimed = service.claim_execution(
        created_id,
        worker_id="test",
        role_id="director",
        selection_source="unit",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True
    original_append_event = service_module.append_fact_event

    def fail_reopened_append(command: object) -> object:
        event_type = str(getattr(command, "event_type", "") or "")
        if event_type == "reopened":
            raise RuntimeError("fact stream unavailable")
        return original_append_event(command)

    monkeypatch.setattr(service_module, "append_fact_event", fail_reopened_append)

    row = service.reopen_task_row(created_id, reason="qa_rework")

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

    created = service.create_task_row(
        subject="project fact receipt",
        description="factory execution event should point at the fact stream event",
        metadata={"factory_run_id": "factory_123456789abc"},
    )
    created_id = created["id"]
    published.clear()

    claimed = service.claim_execution(
        created_id,
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


def test_create_task_row_projects_fact_event_seq_matching_fact_stream(tmp_path: Path) -> None:
    """``create_task_row`` must project a positive ``fact_event_seq`` that matches the
    seq stored in the fact stream entry. The seq must NOT be fabricated on
    the failure path.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    row = service.create_task_row(subject="project fact_event_seq")

    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    assert isinstance(execution_event.get("fact_event_seq"), int)
    assert execution_event["fact_event_seq"] >= 1

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    created_event = next(event for event in events if event.get("event_type") == "created")
    assert int(created_event["seq"]) == execution_event["fact_event_seq"]


def test_claim_and_complete_execution_projects_fact_event_seq_consistently(tmp_path: Path) -> None:
    """Claim + complete must publish execution_event.fact_event_seq that is consistent
    with the ``query_fact_events`` seq for the same stream event.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="seq claim+complete")
    created_id = int(created["id"])

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-seq-claim",
        selection_source="unit",
    )
    assert claimed["success"] is True
    claim_event = claimed["execution_event"]
    assert claim_event["ok"] is True
    assert claim_event["event_type"] == "claimed"
    assert isinstance(claim_event.get("fact_event_seq"), int)
    assert claim_event["fact_event_seq"] >= 1

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True
    completed_event = completed["execution_event"]
    assert completed_event["ok"] is True
    assert completed_event["event_type"] == "completed"
    assert isinstance(completed_event.get("fact_event_seq"), int)
    assert completed_event["fact_event_seq"] > claim_event["fact_event_seq"]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    seq_by_type: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        seq_by_type[event_type] = int(event["seq"])

    assert seq_by_type["created"] == int(created["execution_event"]["fact_event_seq"])
    assert seq_by_type["claimed"] == int(claim_event["fact_event_seq"])
    assert seq_by_type["completed"] == int(completed_event["fact_event_seq"])


def test_execution_event_does_not_fabricate_fact_event_seq_on_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``append_fact_event`` must omit ``fact_event_seq`` from the public
    ``execution_event`` projection so consumers cannot latch onto a phantom seq.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    def fail_append_event(_command: object) -> object:
        raise RuntimeError("fact stream unavailable")

    monkeypatch.setattr(service_module, "append_fact_event", fail_append_event)

    row = service.create_task_row(subject="fail append seq projection")
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is False
    assert execution_event["event_type"] == "created"
    assert "fact_event_seq" not in execution_event


def test_execution_event_does_not_fabricate_fact_event_seq_on_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``_publish_factory_execution_event`` raises after a successful append,
    the public ``execution_event`` still exposes ``fact_event_seq`` because the
    seq was already allocated by the fact stream; the helper projects it as
    positive evidence regardless of publish path so the failure shape remains
    transparent.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    def fail_publish(_payload: dict[str, object]) -> bool:
        raise RuntimeError("publish down")

    monkeypatch.setattr(service, "_publish_factory_execution_event", fail_publish)

    row = service.create_task_row(subject="publish-failure seq")
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["published"] is False
    assert execution_event["publish_error"] == "publish down"
    # The fact stream accepted the event even if publish failed, so fact_event_seq
    # is projected. The publish_error/published fields carry the honest verdict.
    assert isinstance(execution_event.get("fact_event_seq"), int)
    assert execution_event["fact_event_seq"] >= 1


def test_execution_event_omits_fact_event_seq_when_appended_seq_is_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``FactEventAppendedV1.appended_seq`` is ``None`` (e.g. future idempotent
    hits that opt out of CAS), the public projection must still omit
    ``fact_event_seq`` rather than emit a fabricated ``0`` or ``-1``.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    workspace_path = str(workspace)

    def _make_appended() -> Any:
        class _Appended:
            event_id = "evt-no-seq"
            workspace = workspace_path
            stream = "task_runtime.execution"
            storage_path = "runtime/events/task_runtime.execution.jsonl"
            appended_at = "2026-01-01T00:00:00+00:00"
            appended_seq = None

        return _Appended()

    def append_no_seq(_command: object) -> Any:
        return _make_appended()

    monkeypatch.setattr(service_module, "append_fact_event", append_no_seq)

    row = service.create_task_row(subject="no-seq append")
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is True
    assert "fact_event_seq" not in execution_event


def test_list_task_rows_from_execution_facts_projects_fact_event_seq_matching_event_seq(
    tmp_path: Path,
) -> None:
    """``list_task_rows_from_execution_facts`` must copy the queried Fact Stream
    event wrapper ``seq`` onto the projected row as ``fact_event_seq`` when the
    payload lacks a valid positive seq, and the value must match the queried
    event's seq exactly. The seq must never be fabricated.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-SEQ",
            run_id="run-seq-read",
            payload={
                "task_id": "TASK-SEQ",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-seq",
                "task_row_snapshot": {
                    "id": "TASK-SEQ",
                    "task_id": "TASK-SEQ",
                    "subject": "fact-derived row",
                },
            },
        )
    )

    rows = service.list_task_rows_from_execution_facts()
    assert len(rows) == 1
    row = rows[0]

    # Top-level fact_event_seq must be projected and must match the wrapper
    # seq returned by the FactStream query — proving the read-side copy is
    # sourced from the event envelope, not fabricated.
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    seq_by_task: dict[str, int] = {}
    for event in events:
        task_id = str(event.get("task_id") or "").strip()
        if task_id:
            seq_by_task[task_id] = int(event["seq"])

    assert row["fact_event_seq"] == seq_by_task["TASK-SEQ"]
    assert isinstance(row["fact_event_seq"], int)
    assert row["fact_event_seq"] >= 1


def test_list_task_rows_from_execution_facts_preserves_payload_fact_event_seq(tmp_path: Path) -> None:
    """When the persisted fact payload already carries a valid positive
    ``fact_event_seq``, the read model must keep that value rather than
    overwrite it with the wrapper seq.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-PRESET",
            run_id="run-preset",
            payload={
                "task_id": "TASK-PRESET",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-preset",
                "fact_event_seq": 999,
                "task_row_snapshot": {
                    "id": "TASK-PRESET",
                    "task_id": "TASK-PRESET",
                    "subject": "preset seq row",
                },
            },
        )
    )

    rows = service.list_task_rows_from_execution_facts()
    assert len(rows) == 1
    assert rows[0]["fact_event_seq"] == 999


def test_list_task_rows_from_execution_facts_uses_latest_fact_window(tmp_path: Path) -> None:
    """When the fact stream has more events than the requested window, the read
    model must project the latest window, not the earliest one.

    Otherwise a long-running task can keep showing a stale status even though
    later ``task_runtime.execution`` facts are the authoritative state source.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    for event_type, status in (
        ("created", "pending"),
        ("claimed", "in_progress"),
        ("completed", "completed"),
    ):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="task_runtime.execution",
                event_type=event_type,
                source="runtime.task_runtime",
                task_id="TASK-WINDOW",
                run_id="run-window",
                payload={
                    "task_id": "TASK-WINDOW",
                    "event_type": event_type,
                    "status": status,
                    "execution_state": status,
                    "task_row_snapshot": {
                        "id": "TASK-WINDOW",
                        "task_id": "TASK-WINDOW",
                        "subject": "latest window row",
                    },
                },
            )
        )

    rows = service.list_task_rows_from_execution_facts(limit=2)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["execution_state"] == "completed"
    assert rows[0]["fact_event_seq"] == 3


def test_list_task_rows_from_execution_facts_omits_fact_event_seq_when_wrapper_seq_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both the payload ``fact_event_seq`` and the wrapper ``seq`` are
    missing/invalid, the projected row must NOT fabricate a seq field.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-INVALID",
            run_id="run-invalid",
            payload={
                "task_id": "TASK-INVALID",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "task_row_snapshot": {
                    "id": "TASK-INVALID",
                    "task_id": "TASK-INVALID",
                    "subject": "invalid seq row",
                },
            },
        )
    )

    events = list(
        query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    )
    assert events, "fact stream must contain the appended event"

    # Strip seq/fact_event_seq from the queried event to simulate an event
    # record that has no seq evidence to copy.
    def fake_query_fact_events(query: QueryFactEventsV1) -> Any:
        result = original_query_fact_events(query)
        scrubbed: list[dict[str, object]] = []
        for event in result.events:
            stripped = dict(event)
            stripped.pop("seq", None)
            payload = dict(stripped.get("payload") or {})
            payload.pop("fact_event_seq", None)
            stripped["payload"] = payload
            scrubbed.append(stripped)
        return type(result)(
            workspace=result.workspace,
            stream=result.stream,
            events=tuple(scrubbed),
            total=result.total,
            next_offset=result.next_offset,
        )

    original_query_fact_events = service_module.query_fact_events
    monkeypatch.setattr(service_module, "query_fact_events", fake_query_fact_events)

    rows = service.list_task_rows_from_execution_facts()
    assert len(rows) == 1
    assert "fact_event_seq" not in rows[0]


def test_list_observable_task_rows_preserves_fact_event_seq_overlay(tmp_path: Path) -> None:
    """The observable overlay must keep the fact-derived ``fact_event_seq``
    field visible on the merged row, matching the queried event wrapper seq
    for the latest event and never dropping it during the file-row overlay
    merge.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Overlay preserves fact_event_seq",
        description="file row overlaid by fact row",
    )
    task_id = str(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-overlay-seq",
            payload={
                "task_id": task_id,
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-overlay-seq",
                "task_row_snapshot": created,
            },
        )
    )

    rows = service.list_observable_task_rows()
    assert len(rows) == 1
    row = rows[0]

    # The overlay must carry the LATEST fact_event_seq from the fact stream —
    # the ``claimed`` event (seq=2) is later than the original ``created``
    # event (seq=1) emitted by create_task_row.
    latest_seq = max(
        int(event["seq"])
        for event in query_fact_events(
            QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")
        ).events
        if str(event.get("task_id") or "").strip() == task_id
    )
    assert row["fact_event_seq"] == latest_seq
    assert isinstance(row["fact_event_seq"], int)
    # Overlay must not have dropped other projection fields.
    assert row["status"] == "in_progress"
    assert row["running"] is True
    assert row["metadata"]["previous_status"] == "pending"
    assert row["metadata"]["source"] == "task_runtime.execution_fact"


# ---------------------------------------------------------------------------
# WS2 Execution Ledger SSoT convergence — selection must respect terminal facts
# ---------------------------------------------------------------------------
#
# These tests pin the contract that ``list_ready_task_rows``,
# ``select_next_task``, and ``claim_next_execution`` consult the observable
# task-runtime read model (file row overlaid with the latest
# ``task_runtime.execution`` fact) instead of relying on a stale pending file
# row alone. They fail if the selection APIs ever fall back to file-only
# state when a newer execution fact projects the task as terminal.


def _append_terminal_fact_event(
    workspace: Path,
    *,
    task_id: str,
    event_type: str,
    status: str,
    run_id: str,
) -> None:
    """Append a fact-only terminal event without mutating the file row.

    The payload deliberately omits ``task_row_snapshot`` for the row-id fact so
    the read projection still carries the file-row's id while picking up the
    terminal ``execution_state`` from the event payload itself.
    """

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
            source="runtime.task_runtime",
            task_id=task_id,
            run_id=run_id,
            payload={
                "task_id": task_id,
                "run_id": run_id,
                "event_type": event_type,
                "status": status,
                "execution_state": status,
            },
        )
    )


def test_list_ready_task_rows_skips_file_pending_row_with_terminal_fact(tmp_path: Path) -> None:
    """``list_ready_task_rows`` must drop a pending file row whose latest fact
    is terminal — without rewriting the underlying file row.

    Test setup:
      * file row stays at status="pending" (ready candidate).
      * newer ``task_runtime.execution`` fact projects the same task as
        ``completed``.
    Expected: the stale pending file row is NOT returned by
    ``list_ready_task_rows`` because the observable model is terminal.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="stale pending row over terminal fact")
    created_id = str(created["id"])

    # Sanity check: a fresh pending row is ready.
    initial_ready = service.list_ready_task_rows()
    assert [row["id"] for row in initial_ready] == [int(created_id)]
    assert initial_ready[0]["status"] == "pending"

    # Append a newer terminal fact WITHOUT mutating the file row.
    _append_terminal_fact_event(
        workspace,
        task_id=created_id,
        event_type="completed",
        status="completed",
        run_id="run-fact-completed",
    )

    # File row on disk is still pending.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"

    ready_rows = service.list_ready_task_rows()
    assert all(int(row["id"]) != int(created_id) for row in ready_rows), (
        "stale pending file row must not be returned once a terminal fact exists; "
        f"got {[row['id'] for row in ready_rows]}"
    )

    # Observable model confirms the terminal verdict.
    observable = service.list_observable_task_rows()
    matching = [row for row in observable if int(row["id"]) == int(created_id)]
    assert matching, "observable rows must still surface the task id"
    assert matching[0]["status"] == "completed"


def test_select_next_task_with_requested_id_rejects_stale_pending_file_row(tmp_path: Path) -> None:
    """``select_next_task(requested_task_id=...)`` must not return a stale
    pending file row when the latest observable fact for that task is
    terminal.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="select_next_task terminal fact rejection")
    created_id = str(created["id"])

    _append_terminal_fact_event(
        workspace,
        task_id=created_id,
        event_type="failed",
        status="failed",
        run_id="run-fact-failed",
    )

    # File row stays pending; observable model is failed.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    observable = service.list_observable_task_rows()
    assert observable[0]["status"] == "failed"

    selected = service.select_next_task(requested_task_id=created_id)

    assert selected is None, (
        "select_next_task must NOT return a stale pending file row when the "
        f"latest observable fact is terminal; got {selected!r}"
    )


def test_claim_next_execution_skips_stale_pending_row_with_terminal_fact(tmp_path: Path) -> None:
    """``claim_next_execution`` must skip a stale pending file row whose
    latest observable fact is terminal and claim another available task
    instead.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    stale = service.create_task_row(subject="stale pending file row over terminal fact")
    stale_id = str(stale["id"])
    fresh = service.create_task_row(subject="fresh available task")
    fresh_id = str(fresh["id"])

    _append_terminal_fact_event(
        workspace,
        task_id=stale_id,
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-skip",
    )

    on_disk = json.loads(_task_file_path(workspace, stale_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[int(stale_id)]["status"] == "completed"
    assert observable[int(fresh_id)]["status"] == "pending"

    claimed = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-claim-skip-stale",
        selection_source="queue",
    )

    assert claimed["success"] is True
    assert claimed["task"]["id"] == int(fresh_id)
    assert claimed["task"]["status"] == "in_progress"
    # The stale task must NOT have been claimed or had its file row mutated.
    persisted_stale = json.loads(_task_file_path(workspace, stale_id).read_text(encoding="utf-8"))
    assert persisted_stale["status"] == "pending"
    attempted_ids = [
        int(attempt["task_id"]) for attempt in (claimed.get("attempts") or []) if attempt.get("task_id") is not None
    ]
    assert int(stale_id) not in attempted_ids, (
        f"stale pending row with terminal fact must not appear in claim attempts; got attempts={attempted_ids!r}"
    )
