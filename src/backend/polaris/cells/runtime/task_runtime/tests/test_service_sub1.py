from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from queue import Empty
from typing import Any, Callable, NamedTuple, NoReturn, cast

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.internal import service as service_module
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    TaskExecutionSession,
    build_task_runtime_execution_event_payload,
    terminal_session_timestamp,
)
from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    TaskBoard,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
    SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1,
    BindRuntimeTaskToFactoryRunCommandV1,
    FenceExpiredFactoryRunSessionsCommandV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    OwnerReworkExecutionAuthorizationV1,
    PrepareOwnerReworkExecutionCommandV1,
    PrepareSameTaskLocalReworkCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionFactV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
    heartbeat_task_runtime_execution_attempt,
    query_observable_task_rows,
    reset_runtime_task_records,
    validate_task_runtime_execution_attempt,
)
from polaris.kernelone.storage import resolve_runtime_path


def _task_file_path(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.json"))


def _bootstrap_task_runtime_fact_stream(workspace: Path) -> None:
    """Establish the explicit FactStream authority needed by TaskRuntime tests."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="task-runtime-service-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _create_bootstrapped_task_runtime_service(workspace: str | Path) -> TaskRuntimeService:
    """Construct TaskRuntime only after its public FactStream precondition exists."""

    workspace_path = Path(workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    _bootstrap_task_runtime_fact_stream(workspace_path)
    return TaskRuntimeService(str(workspace_path))


def _multiprocess_claim_execution(
    workspace: str,
    task_id: int,
    worker_id: str,
    start_event: Any,
    result_queue: Any,
) -> None:
    """Claim one shared persisted task from an independent Python process."""

    service = _create_bootstrapped_task_runtime_service(workspace)
    if not start_event.wait(timeout=15):
        result_queue.put({"success": False, "reason": "start_timeout"})
        return
    result_queue.put(
        service.claim_execution(
            task_id,
            worker_id=worker_id,
            role_id="director",
            run_id="cross-process-run",
            selection_source="multiprocessing-regression",
        )
    )


def _multiprocess_hold_session_lock(
    workspace: str,
    task_id: int,
    ready_marker_path: str,
    hold_seconds: float,
) -> None:
    """Hold the real cooperative session lock from an isolated spawned process."""

    board = TaskBoard(workspace)
    lock_path = Path(resolve_runtime_path(workspace, f"runtime/tasks/.task_{task_id}.session.json.lock"))
    with board._file_lock(lock_path):
        Path(ready_marker_path).write_text("locked\n", encoding="utf-8")
        time.sleep(hold_seconds)


def _sha256_utf8_file(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


_ROW_WRITE_RECEIPT_FIELDS = frozenset({"task_id", "task_path", "before_hash", "after_hash", "operation", "written_at"})
_SESSION_WRITE_RECEIPT_FIELDS = frozenset(
    {
        "task_id",
        "session_id",
        "session_path",
        "before_hash",
        "after_hash",
        "operation",
        "written_at",
        "preserved_terminal_session",
    }
)


def _owner_rework_prepare_command(
    workspace: Path,
    *,
    task_role: str,
    handoff_id: str = "owner-rework-handoff-1",
) -> PrepareOwnerReworkExecutionCommandV1:
    """Build public TaskMarket evidence for one owner/requester prepare call."""

    handoff = {
        "schema_version": "task-market.owner-rework-route/1",
        "handoff_id": handoff_id,
        "owner_task_id": "owner-task",
        "requester_task_id": "requester-task",
        "owner_previous_status": "resolved",
        "requester_previous_status": "in_execution",
        "owner_reopened": True,
        "dependency_mode": "resolved_only",
        "failure_metadata": {"error_code": "SCOPE_CONFLICT"},
        "evidence_metadata": {"source": "task-runtime-service-test"},
        "metadata": {"test": True},
        "routed_at": "2026-07-11T00:00:00+00:00",
    }
    task_id = "owner-task" if task_role == "owner" else "requester-task"
    counterparty_task_id = "requester-task" if task_role == "owner" else "owner-task"
    handoff_records = {"owner_rework_handoffs": {handoff_id: handoff}}
    claimed_item = {
        "task_id": task_id,
        "status": "in_execution",
        "lease_token": "task-market-lease",
        "claimed_by": "director-owner-rework",
        "metadata": handoff_records,
    }
    counterparty_item = {
        "task_id": counterparty_task_id,
        "status": "pending_exec",
        "metadata": handoff_records,
    }
    return PrepareOwnerReworkExecutionCommandV1(
        authorization=OwnerReworkExecutionAuthorizationV1(
            schema_version=OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
            workspace=str(workspace),
            task_id=task_id,
            lease_token="task-market-lease",
            worker_id="director-owner-rework",
            worker_role="director",
            task_role=task_role,
            counterparty_task_id=counterparty_task_id,
            handoff=handoff,
            claimed_item=claimed_item,
            counterparty_item=counterparty_item,
        )
    )


def _execution_event_payload_for_result(
    workspace: Path,
    execution_event: dict[str, Any],
    *,
    event_type: str,
) -> dict[str, Any]:
    event_id = str(execution_event.get("fact_event_id") or "").strip()
    assert event_id
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
        )
    ).events
    matches = [event for event in events if str(event.get("event_id") or "").strip() == event_id]
    assert len(matches) == 1
    payload = matches[0].get("payload")
    assert isinstance(payload, dict)
    return payload


def _execution_event_payloads_by_task_id(
    workspace: Path,
    *,
    event_type: str,
) -> dict[int, dict[str, Any]]:
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
        )
    ).events
    payloads: dict[int, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload")
        assert isinstance(payload, dict)
        task_id = int(str(payload.get("task_id") or "0"))
        payloads[task_id] = payload
    return payloads


def _assert_task_row_write_receipt(
    receipt: object,
    *,
    task_id: int,
    task_path: Path,
) -> dict[str, Any]:
    assert receipt is not None
    if isinstance(receipt, dict):
        values = {field: receipt[field] for field in _ROW_WRITE_RECEIPT_FIELDS}
    else:
        values = {field: getattr(receipt, field) for field in _ROW_WRITE_RECEIPT_FIELDS if hasattr(receipt, field)}
    missing = _ROW_WRITE_RECEIPT_FIELDS - set(values)
    assert not missing
    assert values["task_id"] == task_id
    assert str(values["task_path"]) in {str(task_path), f"runtime/tasks/task_{task_id}.json"}
    assert isinstance(values["operation"], str)
    assert values["operation"].strip()
    assert isinstance(values["written_at"], str)
    assert values["written_at"].strip()
    return values


def _assert_task_execution_session_write_receipt(
    receipt: object,
    *,
    task_id: int,
    session_id: str,
    session_path: Path,
    preserved_terminal_session: bool,
) -> dict[str, Any]:
    assert receipt is not None
    if isinstance(receipt, dict):
        values = {field: receipt[field] for field in _SESSION_WRITE_RECEIPT_FIELDS}
    else:
        values = {field: getattr(receipt, field) for field in _SESSION_WRITE_RECEIPT_FIELDS if hasattr(receipt, field)}
    missing = _SESSION_WRITE_RECEIPT_FIELDS - set(values)
    assert not missing
    assert values["task_id"] == task_id
    assert values["session_id"] == session_id
    assert str(values["session_path"]) in {str(session_path), f"runtime/tasks/task_{task_id}.session.json"}
    assert isinstance(values["before_hash"], str)
    assert isinstance(values["after_hash"], str)
    assert values["after_hash"].strip()
    assert values["operation"] == "replace"
    assert isinstance(values["written_at"], str)
    assert values["written_at"].strip()
    assert values["preserved_terminal_session"] is preserved_terminal_session
    return values


def _assert_execution_event_row_write_receipt(
    payload: dict[str, Any],
    *,
    task_id: int,
    task_path: Path,
) -> dict[str, Any]:
    details = payload.get("details")
    assert isinstance(details, dict)
    return _assert_task_row_write_receipt(
        details.get("row_write_receipt"),
        task_id=task_id,
        task_path=task_path,
    )


def _assert_execution_event_session_write_receipt(
    payload: dict[str, Any],
    *,
    task_id: int,
    session_id: str,
    session_path: Path,
    preserved_terminal_session: bool,
) -> dict[str, Any]:
    details = payload.get("details")
    assert isinstance(details, dict)
    return _assert_task_execution_session_write_receipt(
        details.get("session_write_receipt"),
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=preserved_terminal_session,
    )


def _raise_fact_stream_unavailable(
    *,
    event_type_str: str,
    payload: dict[str, Any],
) -> NoReturn:
    assert event_type_str
    assert payload
    raise RuntimeError("fact stream unavailable")


def _append_execution_fact_probe(
    workspace: Path,
    *,
    task_id: object,
    event_type: str,
    status: str,
    run_id: str,
    subject: str = "execution fact gateway probe",
) -> None:
    task_token = str(task_id)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
            source="runtime.task_runtime",
            task_id=task_token,
            run_id=run_id,
            payload={
                "task_id": task_token,
                "event_type": event_type,
                "status": status,
                "execution_state": status,
                "run_id": run_id,
                "task_row_snapshot": {
                    "id": task_id,
                    "task_id": task_token,
                    "subject": subject,
                },
            },
        )
    )


def _spy_execution_fact_gateway(
    service: TaskRuntimeService,
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, int]]:
    gateway_calls: list[tuple[int, int]] = []
    original_gateway = service._query_execution_fact_events

    def query_execution_fact_events_spy(
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> Any:
        gateway_calls.append((int(limit), int(offset)))
        return original_gateway(limit=limit, offset=offset)

    monkeypatch.setattr(service, "_query_execution_fact_events", query_execution_fact_events_spy)
    return gateway_calls


def _assert_execution_event_append_failure_with_row_write_receipt(
    execution_event: dict[str, Any],
    *,
    event_type: str,
    task_id: int,
    task_path: Path,
) -> dict[str, Any]:
    assert execution_event["ok"] is False
    assert execution_event["event_type"] == event_type
    assert execution_event["published"] is False
    assert execution_event["error"] == "fact stream unavailable"
    return _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=task_path,
    )


def _assert_execution_event_append_failure_with_session_write_receipt(
    execution_event: dict[str, Any],
    *,
    event_type: str,
    task_id: int,
    session_id: str,
    session_path: Path,
) -> dict[str, Any]:
    assert execution_event["ok"] is False
    assert execution_event["event_type"] == event_type
    assert execution_event["published"] is False
    assert execution_event["error"] == "fact stream unavailable"
    return _assert_execution_event_session_write_receipt(
        execution_event,
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )


class _SessionFileLockProbe(NamedTuple):
    entered_lock_paths: list[Path]
    read_observed_under_file_lock: list[bool]
    write_observed_under_file_lock: list[bool]


def _observe_session_file_lock(
    service: TaskRuntimeService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    task_id: int,
) -> _SessionFileLockProbe:
    entered_lock_paths: list[Path] = []
    active_lock_paths: list[Path] = []
    read_observed_under_file_lock: list[bool] = []
    write_observed_under_file_lock: list[bool] = []
    session_logical_path = service._session_logical_path(task_id)
    expected_lock_path = service._session_file_lock_path(task_id)
    original_file_lock = service._board._file_lock
    original_read_json = service._kernel_fs.read_json
    original_write_json_atomic = service._kernel_fs.write_json_atomic

    def session_lock_is_active() -> bool:
        return bool(active_lock_paths and active_lock_paths[-1] == expected_lock_path)

    @contextmanager
    def tracking_file_lock(lock_file_path: Path) -> Iterator[object]:
        lock_path = Path(lock_file_path)
        entered_lock_paths.append(lock_path)
        with original_file_lock(lock_path) as lock_handle:
            active_lock_paths.append(lock_path)
            try:
                yield lock_handle
            finally:
                released_lock_path = active_lock_paths.pop()
                assert released_lock_path == lock_path

    def wrapped_read_json(logical_path: str) -> Any:
        if logical_path == session_logical_path:
            read_observed_under_file_lock.append(session_lock_is_active())
        return original_read_json(logical_path)

    def wrapped_write_json_atomic(
        logical_path: str,
        payload: Any,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> object:
        if logical_path == session_logical_path:
            write_observed_under_file_lock.append(session_lock_is_active())
        return original_write_json_atomic(
            logical_path,
            payload,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )

    monkeypatch.setattr(service._board, "_file_lock", tracking_file_lock)
    monkeypatch.setattr(service._kernel_fs, "read_json", wrapped_read_json)
    monkeypatch.setattr(service._kernel_fs, "write_json_atomic", wrapped_write_json_atomic)
    return _SessionFileLockProbe(
        entered_lock_paths=entered_lock_paths,
        read_observed_under_file_lock=read_observed_under_file_lock,
        write_observed_under_file_lock=write_observed_under_file_lock,
    )


def _observe_session_write_file_lock(
    service: TaskRuntimeService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    task_id: int,
) -> _SessionFileLockProbe:
    return _observe_session_file_lock(
        service,
        monkeypatch,
        task_id=task_id,
    )


def _session_file_path(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.session.json"))


def _settle_claimed_execution_attempt(
    service: TaskRuntimeService,
    claim: dict[str, Any],
    *,
    outcome: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Settle only the canonical attempt identity returned by a real claim."""

    execution_attempt = claim.get("execution_attempt")
    assert isinstance(execution_attempt, dict)
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(execution_attempt)
    return service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome=outcome,  # type: ignore[arg-type]
            summary=summary,
            metadata=metadata or {},
        )
    )


ExecutionTransitionInvoker = Callable[[TaskRuntimeService, dict[str, Any]], dict[str, Any]]
OwnerTerminalTransitionInvoker = Callable[[TaskRuntimeService, object], dict[str, Any] | None]


def _complete_execution_transition(
    service: TaskRuntimeService,
    claim: dict[str, Any],
) -> dict[str, Any]:
    return _settle_claimed_execution_attempt(service, claim, outcome="completed", summary="done")


def _fail_execution_transition(
    service: TaskRuntimeService,
    claim: dict[str, Any],
) -> dict[str, Any]:
    return _settle_claimed_execution_attempt(
        service,
        claim,
        outcome="failed",
        summary="director execution failed",
    )


def _suspend_execution_transition(
    service: TaskRuntimeService,
    claim: dict[str, Any],
) -> dict[str, Any]:
    return _settle_claimed_execution_attempt(
        service,
        claim,
        outcome="suspended",
        summary="factory_stage_timeout",
    )


_EXECUTION_TRANSITION_HELPER_CASES: tuple[tuple[str, str, str, ExecutionTransitionInvoker], ...] = (
    ("complete_execution", "completed", "completed", _complete_execution_transition),
    ("fail_execution", "failed", "failed", _fail_execution_transition),
    ("suspend_execution", "suspended", "pending", _suspend_execution_transition),
)


def _cancel_owner_terminal_transition(
    service: TaskRuntimeService,
    task_id: object,
) -> dict[str, Any] | None:
    return service.cancel_task_row_for_deduplication(
        task_id,
        primary_task_id=101,
        reason="unit_owner_cancel",
        metadata={"owner_transition": "cancel"},
        source="unit_owner_transition",
    )


def _fail_owner_terminal_transition(
    service: TaskRuntimeService,
    task_id: object,
) -> dict[str, Any] | None:
    return service.fail_task_row_from_role_adapter(
        task_id,
        reason="unit_owner_failure",
        metadata={"owner_transition": "fail"},
        role_id="pm",
        source="unit_owner_transition",
        failure_class="unit_failure",
    )


_OWNER_TERMINAL_TRANSITION_CASES: tuple[tuple[str, OwnerTerminalTransitionInvoker], ...] = (
    ("cancel_task_row_for_deduplication", _cancel_owner_terminal_transition),
    ("fail_task_row_from_role_adapter", _fail_owner_terminal_transition),
)


def _claimed_execution_for_transition(
    service: TaskRuntimeService,
    *,
    subject: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = service.create_task_row(subject=subject)
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id=run_id,
        selection_source="unit",
    )
    assert claimed["success"] is True
    return created, claimed


def _session_for_terminal_reconcile(task_id: int, *, status: str) -> TaskExecutionSession:
    session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id=f"run-terminal-reconcile-{status}",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    if status == "completed":
        session.mark_completed(result_summary="terminal reconcile completed")
    elif status == "failed":
        session.mark_failed(error="terminal reconcile failed")
    elif status != "active":
        raise AssertionError(f"unsupported reconcile session status: {status}")
    return session


def _assert_terminal_reconcile_result_shape(
    result: tuple[dict[str, Any] | None, str, dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    assert isinstance(result, tuple)
    assert len(result) == 3
    row, error, event = result
    assert row is None or isinstance(row, dict)
    assert isinstance(error, str)
    assert event is None or isinstance(event, dict)
    return row, error, event






def test_task_runtime_execution_event_without_factory_run_is_not_published(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(subject="non factory execution event")
    task_id = int(created["id"])

    execution_event = created["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    assert execution_event["fact_event_id"]
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert execution_event["published"] is False
    assert "publish_error" not in execution_event
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_task_runtime_factory_event_publish_false_is_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            return False

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )

    created = service.create_task_row(
        subject="factory publisher returned false",
        metadata={"factory_run_id": "factory_123456789abc"},
    )
    task_id = int(created["id"])

    execution_event = created["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    assert execution_event["fact_event_id"]
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert execution_event["published"] is False
    assert execution_event["publish_error"] == "factory_execution_event_publish_returned_false"
    assert execution_event["projection_evidence"] == {
        "schema_version": "task-runtime.execution-event-projection/1",
        "source": "task_runtime",
        "stage": "event_publish",
        "code": "factory_execution_event_publish_returned_false",
        "status": "not_published",
        "details": {
            "factory_run_id": "factory_123456789abc",
            "durable_fact": {
                "event_id": execution_event["fact_event_id"],
                "stream": "task_runtime.execution",
                "event_seq": execution_event["fact_event_seq"],
            },
        },
    }
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_task_execution_result_keeps_success_when_factory_publish_returns_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missed realtime projection never reverses a durable execution transition."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            del subject, payload
            return False

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )
    created = service.create_task_row(
        subject="high-level result preserves durable success",
        metadata={"factory_run_id": "factory_123456789abc"},
    )

    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="director-123456789abc",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    assert claimed["execution_event"]["ok"] is True
    assert claimed["execution_event"]["published"] is False
    assert "failure_evidence" not in claimed["execution_event"]
    projection_evidence = claimed["projection_evidence"]
    assert projection_evidence == claimed["execution_event"]["projection_evidence"]
    assert projection_evidence["code"] == "factory_execution_event_publish_returned_false"
    assert projection_evidence["status"] == "not_published"
    projection_evidence["details"]["durable_fact"]["event_id"] = "mutated"
    assert claimed["execution_event"]["projection_evidence"]["details"]["durable_fact"]["event_id"] != "mutated"


def test_task_runtime_factory_event_preserves_payload_director_run_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
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
            "fact_event_id": "evt-committed-terminal-fact",
            "fact_event_seq": 17,
        }
    )

    assert ok is True
    envelope = published["payload"]
    assert isinstance(envelope, dict)
    assert envelope["run_id"] == "factory_123456789abc"
    assert envelope["channel"] == "event.factory:factory_123456789abc"
    assert envelope["event_id"] == "evt-committed-terminal-fact"
    assert envelope["cursor"] == 17
    assert envelope["meta"]["fact_event_seq"] == 17
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["run_id"] == "director-123456789abc"
    assert payload["factory_run_id"] == "factory_123456789abc"
    assert payload["director_run_id"] == "director-123456789abc"


def test_task_runtime_factory_event_keeps_durable_snapshot_out_of_realtime_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A durable Task row snapshot must not overflow the NATS realtime frame."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
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
    huge_snapshot = {
        "id": 1,
        "metadata": {
            "adapter_result": {
                "quality_repair_attempts": [{"provider_result": "x" * 2_000_000}],
            }
        },
    }
    huge_details = {
        "error_code": "director_materialization_quality_failed",
        "error_message": "界" * 2_000_000,
        "adapter_result": {"output": "x" * 2_000_000},
        "primary_llm": {"output": "x" * 2_000_000},
    }

    ok = service._publish_factory_execution_event(
        {
            "run_id": "director-123456789abc",
            "factory_run_id": "factory_123456789abc",
            "task_id": "1",
            "event_type": "failed",
            "status": "failed",
            "fact_event_id": "evt-durable-task-row-snapshot",
            "fact_event_seq": 63,
            "fact_stream": "task_runtime.execution",
            "task_row_snapshot": huge_snapshot,
            "details": huge_details,
        }
    )

    assert ok is True
    envelope = published["payload"]
    assert isinstance(envelope, dict)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert "task_row_snapshot" not in payload
    assert payload["task_row_snapshot_projection"] == {
        "schema_version": "task-runtime.realtime-row-snapshot-projection/1",
        "status": "durable_fact_only",
        "fact_event_id": "evt-durable-task-row-snapshot",
        "fact_event_seq": 63,
        "fact_stream": "task_runtime.execution",
    }
    assert payload["details"]["error_code"] == "director_materialization_quality_failed"
    assert len(payload["details"]["error_message"]) == 512
    assert "adapter_result" not in payload["details"]
    assert "primary_llm" not in payload["details"]
    assert len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) < 64 * 1024


def test_create_task_row_projects_fact_event_seq_matching_fact_stream(tmp_path: Path) -> None:
    """``create_task_row`` must project a positive ``fact_event_seq`` that matches the
    seq stored in the fact stream entry. The seq must NOT be fabricated on
    the failure path.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

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
    service = _create_bootstrapped_task_runtime_service(workspace)

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

    completed = _settle_claimed_execution_attempt(service, claimed, outcome="completed", summary="done")
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
    """A failed execution fact append must omit ``fact_event_seq`` from the
    public ``execution_event`` projection so consumers cannot latch onto a
    phantom seq.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(service, "_append_execution_fact", _raise_fact_stream_unavailable)

    row = service.create_task_row(subject="fail append seq projection")
    task_id = int(row["id"])
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is False
    assert execution_event["event_type"] == "created"
    assert "fact_event_seq" not in execution_event
    _assert_execution_event_append_failure_with_row_write_receipt(
        execution_event,
        event_type="created",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


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
    service = _create_bootstrapped_task_runtime_service(workspace)

    def fail_publish(_payload: dict[str, object]) -> bool:
        raise RuntimeError("publish down")

    monkeypatch.setattr(service, "_publish_factory_execution_event", fail_publish)

    row = service.create_task_row(subject="publish-failure seq")
    task_id = int(row["id"])
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["published"] is False
    assert execution_event["publish_error"] == "publish down"
    # The fact stream accepted the event even if publish failed, so fact_event_seq
    # is projected. The publish_error/published fields carry the honest verdict.
    assert isinstance(execution_event.get("fact_event_seq"), int)
    assert execution_event["fact_event_seq"] >= 1
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


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
    service = _create_bootstrapped_task_runtime_service(workspace)
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
    service = _create_bootstrapped_task_runtime_service(workspace)

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
    service = _create_bootstrapped_task_runtime_service(workspace)

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
    service = _create_bootstrapped_task_runtime_service(workspace)

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


def test_list_task_rows_from_execution_facts_queries_gateway_and_keeps_latest_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list projection must query execution facts through the service gateway.

    This pins the replacement/monitoring boundary for the Execution Ledger read
    model while preserving the existing latest-window behavior.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    for event_type, status in (
        ("created", "pending"),
        ("claimed", "in_progress"),
        ("completed", "completed"),
    ):
        _append_execution_fact_probe(
            workspace,
            task_id="TASK-GATEWAY-WINDOW",
            event_type=event_type,
            status=status,
            run_id="run-gateway-window",
            subject="gateway latest window row",
        )

    gateway_calls = _spy_execution_fact_gateway(service, monkeypatch)

    rows = service.list_task_rows_from_execution_facts(limit=2)

    assert gateway_calls == [(2, 0), (2, 1)]
    assert len(rows) == 1
    assert rows[0]["task_id"] == "TASK-GATEWAY-WINDOW"
    assert rows[0]["status"] == "completed"
    assert rows[0]["execution_state"] == "completed"
    assert rows[0]["fact_event_seq"] == 3


def test_find_latest_execution_fact_row_for_task_pages_backward_through_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-task lookup must page backward through the service gateway."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    target_task_id = 232

    _append_execution_fact_probe(
        workspace,
        task_id=target_task_id,
        event_type="claimed",
        status="in_progress",
        run_id="run-gateway-target-claimed",
        subject="target task first fact",
    )
    _append_execution_fact_probe(
        workspace,
        task_id=9001,
        event_type="claimed",
        status="in_progress",
        run_id="run-gateway-other-1",
        subject="other task first fact",
    )
    _append_execution_fact_probe(
        workspace,
        task_id=target_task_id,
        event_type="completed",
        status="completed",
        run_id="run-gateway-target-completed",
        subject="target task latest fact",
    )
    _append_execution_fact_probe(
        workspace,
        task_id=9002,
        event_type="claimed",
        status="in_progress",
        run_id="run-gateway-other-2",
        subject="other task second fact",
    )
    _append_execution_fact_probe(
        workspace,
        task_id=9003,
        event_type="completed",
        status="completed",
        run_id="run-gateway-other-3",
        subject="other task terminal fact",
    )

    gateway_calls = _spy_execution_fact_gateway(service, monkeypatch)

    row = service._find_latest_execution_fact_row_for_task(target_task_id, page_size=2)

    assert gateway_calls == [(1, 0), (2, 3), (2, 1)]
    assert row is not None
    assert row["id"] == target_task_id
    assert row["task_id"] == str(target_task_id)
    assert row["status"] == "completed"
    assert row["execution_state"] == "completed"
    assert row["fact_event_seq"] == 3


def test_list_task_rows_from_execution_facts_omits_fact_event_seq_when_wrapper_seq_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both the payload ``fact_event_seq`` and the wrapper ``seq`` are
    missing/invalid, the projected row must NOT fabricate a seq field.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

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
    service = _create_bootstrapped_task_runtime_service(workspace)
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


def test_dependent_rows_blocked_by_reads_fact_overlaid_observable_rows(tmp_path: Path) -> None:
    """Dependency fan-out evidence must read fact-overlaid observable rows.

    The raw dependent rows intentionally stay stale with no persisted
    ``blocked_by`` relation. Only the latest ``task_runtime.execution`` fact
    snapshot declares the dependency, so a file-only implementation of
    ``_dependent_rows_blocked_by`` cannot see it.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    parent = service.create_task_row(subject="observable dependency parent")
    parent_id = int(parent["id"])
    dependent = service.create_task_row(
        subject="stale dependent file row",
        description="raw row has no blocked_by; fact snapshot owns dependency",
    )
    dependent_id = int(dependent["id"])
    malformed = service.create_task_row(subject="malformed blocker snapshot")
    malformed_id = int(malformed["id"])

    raw_dependent = json.loads(_task_file_path(workspace, dependent_id).read_text(encoding="utf-8"))
    raw_malformed = json.loads(_task_file_path(workspace, malformed_id).read_text(encoding="utf-8"))
    assert raw_dependent["status"] == "pending"
    assert raw_dependent["blocked_by"] == []
    assert raw_malformed["blocked_by"] == []

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="dependency_blocked",
            source="runtime.task_runtime",
            task_id=str(dependent_id),
            run_id="run-fact-overlaid-dependent",
            payload={
                "task_id": str(dependent_id),
                "run_id": "run-fact-overlaid-dependent",
                "event_type": "dependency_blocked",
                "status": "blocked",
                "execution_state": "blocked",
                "task_row_snapshot": {
                    "id": dependent_id,
                    "task_id": str(dependent_id),
                    "subject": "fact-overlaid dependent row",
                    "description": "observable snapshot owns blocked_by",
                    "blocked_by": [parent_id],
                    "metadata": {"source": "task_runtime.row_snapshot", "projection": "execution_fact"},
                },
            },
        )
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="dependency_blocked",
            source="runtime.task_runtime",
            task_id=str(malformed_id),
            run_id="run-fact-overlaid-malformed-blocker",
            payload={
                "task_id": str(malformed_id),
                "run_id": "run-fact-overlaid-malformed-blocker",
                "event_type": "dependency_blocked",
                "status": "blocked",
                "execution_state": "blocked",
                "task_row_snapshot": {
                    "id": malformed_id,
                    "task_id": str(malformed_id),
                    "subject": "malformed blocker row",
                    "blocked_by": {"not-a-task": parent_id},
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    dependent_rows = service._dependent_rows_blocked_by(parent_id)

    assert [int(row["id"]) for row in dependent_rows] == [dependent_id]
    row = dependent_rows[0]
    assert row["status"] == "blocked"
    assert row["blocked_by"] == [parent_id]
    assert row["subject"] == "fact-overlaid dependent row"
    assert row["metadata"]["source"] == "task_runtime.execution_fact"
    assert "previous_status" not in row["metadata"]
    assert row["metadata"]["projection"] == "execution_fact"

    persisted_dependent = json.loads(_task_file_path(workspace, dependent_id).read_text(encoding="utf-8"))
    assert persisted_dependent["status"] == "pending"
    assert persisted_dependent["blocked_by"] == []


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


def test_file_task_rows_project_to_observable_rows_across_refresh_suspend_and_reset(
    tmp_path: Path,
) -> None:
    """Raw file-backed rows must remain the common source for public projections.

    This pins the helper extraction boundary without depending on the helper
    name: existing file rows are still visible, observable reads still overlay
    execution facts without mutating stale blockers, list_task_rows still owns
    dependency refresh, suspend still projects resumable execution state, and
    reexecution reset still walks every persisted task row.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    parent = service.create_task_row(subject="file-backed parent")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="file-backed child",
        blocked_by=[parent_id],
    )
    child_id = int(child["id"])

    file_rows = {int(row["id"]): row for row in service.list_task_rows()}
    assert set(file_rows) == {parent_id, child_id}
    assert file_rows[parent_id]["status"] == "pending"
    assert file_rows[child_id]["status"] == "blocked"
    assert file_rows[child_id]["blocked_by"] == [parent_id]

    _append_terminal_fact_event(
        workspace,
        task_id=str(parent_id),
        event_type="completed",
        status="completed",
        run_id="run-file-row-helper-regression",
    )

    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[parent_id]["status"] == "completed"
    assert observable[parent_id]["metadata"]["source"] == "task_runtime.execution_fact"
    assert observable[child_id]["status"] == "blocked"
    assert observable[child_id]["blocked_by"] == [parent_id]

    persisted_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert persisted_child["status"] == "blocked"
    assert persisted_child["blocked_by"] == [parent_id]

    refreshed_rows = {int(row["id"]): row for row in service.list_task_rows()}
    assert refreshed_rows[child_id]["status"] == "pending"
    assert refreshed_rows[child_id]["blocked_by"] == []

    refreshed_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert refreshed_child["status"] == "pending"
    assert refreshed_child["blocked_by"] == []

    claimed = service.claim_execution(
        child_id,
        worker_id="director",
        role_id="director",
        run_id="run-file-row-helper-claim",
        selection_source="unit",
    )
    assert claimed["success"] is True

    suspended = _settle_claimed_execution_attempt(service, claimed, outcome="suspended", summary="unit_regression")
    assert suspended["success"] is True
    assert suspended["task"]["status"] == "pending"
    assert suspended["task"]["resume_state"] == "resumable"
    assert _session_file_path(workspace, child_id).is_file()

    suspended_observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert suspended_observable[child_id]["status"] == "pending"
    assert suspended_observable[child_id]["resume_state"] == "resumable"
    assert suspended_observable[child_id]["metadata"]["source"] == "task_runtime.execution_fact"

    reset = service.reset_task_rows_for_reexecution(source="unit.file-row-helper")

    assert reset["success"] is True
    assert set(reset["reset_files"]) == {f"task_{parent_id}.json", f"task_{child_id}.json"}
    assert reset["deleted_session_files"] == [f"task_{child_id}.session.json"]
    assert not _session_file_path(workspace, child_id).exists()

    reset_rows = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert set(reset_rows) == {parent_id, child_id}
    assert reset_rows[parent_id]["status"] == "pending"
    assert reset_rows[child_id]["status"] == "pending"
    assert reset_rows[child_id]["blocked_by"] == []


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
    service = _create_bootstrapped_task_runtime_service(workspace)

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


def test_observable_task_row_stats_count_terminal_fact_overlay(tmp_path: Path) -> None:
    """Observable stats must count terminal facts over stale file rows."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(subject="observable stats terminal fact")
    created_id = str(created["id"])

    raw_before = service.list_task_rows()
    assert raw_before[0]["status"] == "pending"

    _append_terminal_fact_event(
        workspace,
        task_id=created_id,
        event_type="completed",
        status="completed",
        run_id="run-fact-stats-completed",
    )

    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"

    observable_stats = service.get_observable_task_row_stats()
    compatibility_stats = service.get_task_row_stats()

    assert observable_stats == compatibility_stats
    assert observable_stats["total"] == 1
    assert observable_stats["pending"] == 0
    assert observable_stats["ready"] == 0
    assert observable_stats["completed"] == 1

    raw_after = service.list_task_rows()
    assert raw_after[0]["status"] == "pending"


def test_select_next_task_with_requested_id_rejects_stale_pending_file_row(tmp_path: Path) -> None:
    """``select_next_task(requested_task_id=...)`` must not return a stale
    pending file row when the latest observable fact for that task is
    terminal.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

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
    service = _create_bootstrapped_task_runtime_service(workspace)

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


def test_refresh_dependency_unblocks_overlays_execution_fact_status(tmp_path: Path) -> None:
    """``refresh_dependency_unblocks`` must treat a parent as completed when
    its latest ``task_runtime.execution`` fact says so — even when the file
    row is left stale/pending and ``complete_execution`` was never called.

    Test setup:
      * Parent file row stays at ``status=pending`` (never claimed, never
        completed through the service).
      * Child is created with ``blocked_by=[parent_id]`` so the on-disk row
        is ``status=blocked`` with ``blocked_by=[parent_id]``.
      * A ``task_runtime.execution`` fact is appended for the parent whose
        payload carries ``event_type="completed"`` /
        ``status="completed"`` / ``execution_state="completed"`` plus a
        full ``task_row_snapshot`` so the projection is consistent.
    Expected:
      * ``refresh_dependency_unblocks()`` unblocks the child:
        ``unblocked_task_ids`` contains the child id, the persisted file
        row moves to ``pending`` with ``blocked_by=[]``, and a
        ``dependencies_unblocked`` execution event is recorded.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    parent = service.create_task_row(subject="fact-only completed parent")
    parent_id = str(parent["id"])
    child = service.create_task_row(
        subject="child blocked on fact-completed parent",
        blocked_by=[parent["id"]],
    )
    child_id = int(child["id"])

    # Sanity: the on-disk child row is blocked against the pending parent.
    raw_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    raw_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert raw_parent["status"] == "pending"
    assert raw_child["status"] == "blocked"
    assert raw_child["blocked_by"] == [parent["id"]]

    # Append a terminal execution fact for the parent WITHOUT going through
    # the service APIs — the file row stays stale/pending on disk.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
            source="runtime.task_runtime",
            task_id=parent_id,
            run_id="run-fact-completed-parent",
            payload={
                "task_id": parent_id,
                "run_id": "run-fact-completed-parent",
                "event_type": "completed",
                "status": "completed",
                "execution_state": "completed",
                "session_id": "session-fact-completed",
                "task_row_snapshot": {
                    "id": parent_id,
                    "task_id": parent_id,
                    "subject": parent["subject"],
                    "description": parent.get("description", ""),
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    refresh = service.refresh_dependency_unblocks()

    assert child_id in refresh["unblocked_task_ids"], (
        f"child must be unblocked once parent fact projects as completed; got "
        f"unblocked_task_ids={refresh['unblocked_task_ids']!r}"
    )
    assert refresh["unblocked_count"] == 1
    matching_events = [
        event for event in refresh["execution_events"] if event.get("event_type") == "dependencies_unblocked"
    ]
    assert matching_events, "refresh must record a dependencies_unblocked execution event"
    assert matching_events[0]["ok"] is True

    persisted_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert persisted_child["status"] == "pending"
    assert persisted_child["blocked_by"] == []


def test_claim_execution_rejects_stale_pending_row_with_terminal_execution_fact(
    tmp_path: Path,
) -> None:
    """``claim_execution(task_id)`` must reject a stale pending file row when
    the latest ``task_runtime.execution`` fact for the same task is terminal,
    without mutating the file row.

    Regression: a direct ``claim_execution(task_id)`` call used to consult only
    the file-backed ``TaskBoard`` row. If an external orchestrator appended a
    terminal fact (e.g. ``completed``) without going through
    ``complete_execution``, the raw row stayed ``pending`` and the claim would
    silently re-acquire a task whose authoritative state is already terminal.
    The direct claim path must treat the latest execution fact as
    authoritative and reject the claim with ``task_terminal`` while leaving the
    stale file row untouched (read-model veto, not a hidden mutation).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(subject="claim direct rejects stale pending over terminal fact")
    created_id = int(created["id"])

    # Sanity: the file row is initially ready/pending and no terminal fact exists.
    initial_ready = service.list_ready_task_rows()
    assert [int(row["id"]) for row in initial_ready] == [created_id]
    assert initial_ready[0]["status"] == "pending"

    # Append a newer terminal fact WITHOUT going through the service APIs.
    # ``complete_execution`` is intentionally NOT called and no session file
    # exists on disk; the file row stays stale/pending.
    _append_terminal_fact_event(
        workspace,
        task_id=str(created_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-claim-direct",
    )

    # File row on disk is still pending; observable model is terminal.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    observable = service.list_observable_task_rows()
    matching = [row for row in observable if int(row["id"]) == created_id]
    assert matching, "observable rows must still surface the task id"
    assert matching[0]["status"] == "completed"

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-claim-direct-over-terminal-fact",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is False
    assert claimed["reason"] == "task_terminal"
    # The returned task row is the projected fact row — its status reflects the
    # terminal verdict from the execution fact stream.
    assert isinstance(claimed.get("task"), dict)
    assert claimed["task"]["status"] == "completed"
    # The rejection must surface that the execution fact is authoritative so
    # callers can distinguish read-model vetoes from raw-row terminal states.
    assert claimed.get("execution_fact_authoritative") is True
    assert claimed.get("source") == "task_runtime.execution_fact"
    assert claimed.get("fact_status") == "completed"

    # The raw file row must remain pending: this is a read-model veto, not a
    # hidden mutation. The next ``complete_execution`` / ``reopen`` flow is
    # still free to act on the file row.
    persisted = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "pending", (
        "claim_execution must not mutate the file row when the rejection is "
        "anchored on the execution fact stream; the raw row stays pending so "
        "the eventual owner path can run the sanctioned state transition"
    )


def test_claim_execution_refreshes_dependency_unblocks_from_execution_fact(
    tmp_path: Path,
) -> None:
    """``claim_execution(child_id)`` must refresh dependency unblocks before
    reading the child row, so a child whose parent is only complete in the
    latest ``task_runtime.execution`` fact becomes directly claimable.

    Regression: the direct ``claim_execution(task_id)`` path used to skip the
    ``refresh_dependency_unblocks`` projection, so a child blocked against a
    parent that completed only via the fact stream would be rejected with
    ``task_blocked`` even though the parent was already complete. The direct
    path now refreshes first, then claims, so the child row reaches
    ``in_progress`` in a single call.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    # Parent file row stays at status="pending" — never claimed, never
    # completed through the service APIs.
    parent = service.create_task_row(subject="fact-only completed parent for direct claim")
    parent_id = int(parent["id"])
    # Child is created with blocked_by=[parent_id], so the on-disk child row
    # is status="blocked" with blocked_by=[parent_id].
    child = service.create_task_row(
        subject="child blocked on fact-completed parent",
        blocked_by=[parent_id],
    )
    child_id = int(child["id"])

    # Sanity: the on-disk child row is blocked against the pending parent and
    # no terminal fact exists yet.
    raw_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    raw_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert raw_parent["status"] == "pending"
    assert raw_child["status"] == "blocked"
    assert raw_child["blocked_by"] == [parent_id]

    # Append a terminal execution fact for the parent WITHOUT going through the
    # service APIs. The file row stays stale/pending on disk.
    _append_terminal_fact_event(
        workspace,
        task_id=str(parent_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-parent-direct-claim",
    )

    # File row on disk still pending; observable model says the parent is
    # completed, but it remains a read-only projection. The child stays blocked
    # until the claim path performs the sanctioned refresh.
    on_disk_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    assert on_disk_parent["status"] == "pending"
    on_disk_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert on_disk_child["status"] == "blocked", (
        "claim_execution must be the path that triggers the unblock refresh on "
        "the file row; until then the persisted child row stays blocked"
    )
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[parent_id]["status"] == "completed"
    assert observable[child_id]["status"] == "blocked"
    assert observable[child_id]["blocked_by"] == [parent_id]

    # Direct ``claim_execution(child_id)`` must refresh dependency unblocks
    # first, see the parent as completed via the fact overlay, unblock the
    # child, and then claim it in one call.
    claimed = service.claim_execution(
        child_id,
        worker_id="director",
        role_id="director",
        run_id="run-claim-child-direct",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True, (
        "claim_execution must refresh dependency unblocks before checking "
        "dependencies so a fact-only-completed parent unblocks the child; got "
        f"{claimed!r}"
    )
    assert claimed["reason"] == "claimed"
    assert isinstance(claimed.get("task"), dict)
    assert int(claimed["task"]["id"]) == child_id
    assert claimed["task"]["status"] == "in_progress"

    # Persisted child row must reach ``in_progress`` and have its blockers
    # cleared: refresh_dependency_unblocks cleared ``blocked_by`` to ``[]``
    # and the subsequent claim step moved the row to ``in_progress``.
    persisted_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert persisted_child["status"] == "in_progress"
    assert persisted_child["blocked_by"] == []


# ---------------------------------------------------------------------------
# get_task observable fact-overlay regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.get_task`` is the canonical public read projection for
# a single task row. It MUST surface the latest ``task_runtime.execution`` fact
# overlay (the same converged view that ``list_observable_task_rows`` exposes)
# rather than the raw ``TaskBoard`` row that lives on disk. Without the
# overlay, downstream consumers reading a single task directly would observe a
# stale ``pending`` row while the authoritative ``completed`` fact exists in
# the execution ledger, defeating the read-model convergence the rest of the
# selection/claim paths now rely on.


def test_get_task_returns_fact_overlaid_status_for_numeric_task_id(tmp_path: Path) -> None:
    """``get_task(task_id)`` must surface the latest ``task_runtime.execution``
    fact overlay for a numeric task id.

    Regression: a stale ``pending`` file row must NOT be returned as the
    authoritative status when a newer ``completed`` fact exists in the
    execution ledger. The raw file row remains untouched — the overlay is a
    read-model convergence, not a hidden mutation.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(
        subject="get_task numeric overlay",
        description="file row stays pending while fact overlays to completed",
    )
    created_id = int(created["id"])

    # Sanity: file row is initially pending and get_task agrees.
    on_disk_before = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk_before["status"] == "pending"
    initial_row = service.get_task(created_id)
    assert isinstance(initial_row, dict)
    assert initial_row["status"] == "pending"

    # Append a newer terminal fact WITHOUT going through the service APIs.
    # ``complete_execution`` is intentionally NOT called; no session file is
    # written; the file row stays stale/pending on disk.
    _append_terminal_fact_event(
        workspace,
        task_id=str(created_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-get-task",
    )

    # Raw file row on disk must remain pending: the overlay is read-only.
    on_disk_after = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk_after["status"] == "pending", (
        "get_task must not mutate the file row when overlaying the execution "
        "fact stream; the raw row stays pending so the eventual owner path "
        "can run the sanctioned state transition"
    )

    # ``get_task`` MUST now surface the fact-overlaid status, not the stale
    # pending file row.
    overlaid = service.get_task(created_id)
    assert isinstance(overlaid, dict)
    assert overlaid["status"] == "completed", (
        f"get_task must surface the latest task_runtime.execution fact status; got status={overlaid.get('status')!r}"
    )
    assert overlaid["id"] == created_id
    # The fact-overlay marker must be present so consumers can distinguish a
    # file-row status from a fact-overlaid status.
    assert overlaid.get("metadata", {}).get("source") == "task_runtime.execution_fact"
    assert "previous_status" not in overlaid.get("metadata", {})


