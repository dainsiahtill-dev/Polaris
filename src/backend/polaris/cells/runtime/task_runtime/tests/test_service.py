from __future__ import annotations

import threading
from pathlib import Path

from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService, reset_runtime_task_records
from polaris.kernelone.storage import resolve_runtime_path


def test_task_runtime_service_normalizes_task_ids() -> None:
    assert TaskRuntimeService.normalize_task_id("task-12") == 12
    assert TaskRuntimeService.normalize_task_id("12") == 12
    assert TaskRuntimeService.normalize_task_id("task-12-extra") == 12
    assert TaskRuntimeService.normalize_task_id("bad-id") is None


def test_task_runtime_service_manages_task_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create(
        subject="wire runtime.v2 taskboard",
        description="use snapshot.tasks as primary source",
        metadata={"phase": "projection"},
    )
    assert created.id > 0

    updated = service.update_task(
        f"task-{created.id}",
        status="in_progress",
        metadata={"owner_role": "director"},
    )
    assert updated is not None
    assert str(updated.status.value) == "in_progress"

    row = service.get_task(f"task-{created.id}")
    assert isinstance(row, dict)
    assert row["subject"] == "wire runtime.v2 taskboard"
    assert row["status"] == "in_progress"
    assert row["metadata"]["owner_role"] == "director"

    rows = service.list_task_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == created.id


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
    assert service.get_task("TASK-1")["id"] == row["id"]
    assert service.get_task(f"task-{stale.id}")["id"] == stale.id


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
    assert '"event_type":"completed"' in content


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
