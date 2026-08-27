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
from types import SimpleNamespace
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


def test_task_runtime_service_normalizes_task_ids() -> None:
    assert TaskRuntimeService.normalize_task_id("task-12") == 12
    assert TaskRuntimeService.normalize_task_id("12") == 12
    assert TaskRuntimeService.normalize_task_id("task-12-extra") == 12
    assert TaskRuntimeService.normalize_task_id("bad-id") is None


def test_task_runtime_service_manages_task_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

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


def test_task_runtime_service_records_taskboard_row_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(
        subject="receipt anchored task",
        description="create should record a file-write receipt",
        metadata={"phase": "row-write-receipt"},
    )
    task_id = int(created["id"])
    task_path = _task_file_path(workspace, task_id)
    create_after_hash = _sha256_utf8_file(task_path)

    create_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert create_receipt["before_hash"] in {
        "",
        "file_absent",
        hashlib.sha256(b"").hexdigest(),
    }
    assert create_receipt["after_hash"] == create_after_hash

    updated = service.update_task_row(
        f"task-{task_id}",
        metadata={"owner_role": "director", "receipt_probe": "update"},
    )
    assert updated is not None
    update_after_hash = _sha256_utf8_file(task_path)

    update_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert update_receipt["before_hash"] == create_after_hash
    assert update_receipt["after_hash"] == update_after_hash
    assert update_receipt["after_hash"] != update_receipt["before_hash"]


def test_row_write_receipt_details_use_task_identity_after_latest_anchor_moves(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    task_a = service.create_task_row(
        subject="keyed row receipt task a",
        description="row receipt must remain addressable by task identity",
        metadata={"phase": "row-receipt-keyed", "case": "a"},
    )
    task_a_id = int(task_a["id"])
    task_a_path = _task_file_path(workspace, task_a_id)
    task_a_after_hash = _sha256_utf8_file(task_a_path)
    task_a_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_a_id,
        task_path=task_a_path,
    )
    assert task_a_receipt["after_hash"] == task_a_after_hash

    task_b = service.create_task_row(
        subject="keyed row receipt task b",
        description="second row write moves the global latest anchor",
        metadata={"phase": "row-receipt-keyed", "case": "b"},
    )
    task_b_id = int(task_b["id"])
    assert task_b_id != task_a_id
    task_b_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_b_id,
        task_path=_task_file_path(workspace, task_b_id),
    )
    assert task_b_receipt["after_hash"] != task_a_receipt["after_hash"]

    task_a_details = service._row_write_receipt_details_for_task(task_a)
    projected_task_a_receipt = _assert_task_row_write_receipt(
        task_a_details.get("row_write_receipt"),
        task_id=task_a_id,
        task_path=task_a_path,
    )
    assert projected_task_a_receipt == task_a_receipt
    assert projected_task_a_receipt["after_hash"] == task_a_after_hash

    unknown_task_details = service._row_write_receipt_details_for_task({"id": task_b_id + 1000})
    assert "row_write_receipt" not in unknown_task_details


def test_claim_execution_records_session_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(
        subject="session receipt anchored task",
        description="claim should record a session write receipt",
        metadata={"phase": "session-write-receipt"},
    )
    task_id = int(created["id"])

    claimed = service.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="run-session-receipt",
        selection_source="unit",
    )

    assert claimed["success"] is True
    session_id = str(claimed["session"]["session_id"])
    session_path = _session_file_path(workspace, task_id)
    assert session_path.is_file()

    receipt = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert receipt["after_hash"] == _sha256_utf8_file(session_path)


def test_session_write_receipt_details_use_session_identity_after_latest_anchor_moves(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    task_a = service.create_task_row(subject="keyed session receipt task a")
    task_b = service.create_task_row(subject="keyed session receipt task b")
    task_a_id = int(task_a["id"])
    task_b_id = int(task_b["id"])
    assert task_b_id != task_a_id

    claim_a = service.claim_execution(
        task_a_id,
        worker_id="director",
        role_id="director",
        run_id="run-keyed-session-receipt-a",
        selection_source="unit",
    )
    assert claim_a["success"] is True
    session_a = TaskExecutionSession.from_dict(claim_a["session"])
    session_a_path = _session_file_path(workspace, task_a_id)
    session_a_after_hash = _sha256_utf8_file(session_a_path)
    session_a_receipt = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_a_id,
        session_id=session_a.session_id,
        session_path=session_a_path,
        preserved_terminal_session=False,
    )
    assert session_a_receipt["after_hash"] == session_a_after_hash

    claim_b = service.claim_execution(
        task_b_id,
        worker_id="director",
        role_id="director",
        run_id="run-keyed-session-receipt-b",
        selection_source="unit",
    )
    assert claim_b["success"] is True
    session_b_id = str(claim_b["session"]["session_id"])
    session_b_receipt = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_b_id,
        session_id=session_b_id,
        session_path=_session_file_path(workspace, task_b_id),
        preserved_terminal_session=False,
    )
    assert session_b_receipt["session_id"] != session_a_receipt["session_id"]

    session_a_details = service._session_write_receipt_details_for_session(session_a)
    projected_session_a_receipt = _assert_task_execution_session_write_receipt(
        session_a_details.get("session_write_receipt"),
        task_id=task_a_id,
        session_id=session_a.session_id,
        session_path=session_a_path,
        preserved_terminal_session=False,
    )
    assert projected_session_a_receipt == session_a_receipt
    assert projected_session_a_receipt["after_hash"] == session_a_after_hash

    wrong_session = TaskExecutionSession.from_dict(
        {
            **claim_a["session"],
            "session_id": f"{session_a.session_id}-wrong",
        }
    )
    wrong_session_details = service._session_write_receipt_details_for_session(wrong_session)
    assert "session_write_receipt" not in wrong_session_details


def test_read_session_normal_path_reads_while_holding_session_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="session read lock normal path")
    task_id = int(created["id"])
    session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id="run-session-read-lock-normal",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    session_path = _session_file_path(workspace, task_id)
    session_path.write_text(
        json.dumps(session.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    before_hash = _sha256_utf8_file(session_path)
    file_lock_probe = _observe_session_file_lock(
        service,
        monkeypatch,
        task_id=task_id,
    )
    expected_lock_path = service._session_file_lock_path(task_id)

    persisted_session = service._read_session(task_id)

    assert persisted_session is not None
    assert persisted_session.session_id == session.session_id
    assert persisted_session.task_id == task_id
    assert persisted_session.status == session.status
    assert file_lock_probe.entered_lock_paths == [expected_lock_path]
    assert file_lock_probe.read_observed_under_file_lock == [True]
    assert file_lock_probe.write_observed_under_file_lock == []
    assert expected_lock_path.is_file()
    assert _sha256_utf8_file(session_path) == before_hash


def test_write_session_normal_path_writes_while_holding_session_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="session write lock normal path")
    task_id = int(created["id"])
    session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id="run-session-write-lock-normal",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    session_path = _session_file_path(workspace, task_id)
    file_lock_probe = _observe_session_write_file_lock(
        service,
        monkeypatch,
        task_id=task_id,
    )
    expected_lock_path = service._session_file_lock_path(task_id)

    session_written = service._write_session(session)

    assert session_written is True
    assert file_lock_probe.entered_lock_paths == [expected_lock_path]
    assert file_lock_probe.write_observed_under_file_lock
    assert all(file_lock_probe.write_observed_under_file_lock)
    assert expected_lock_path.is_file()
    persisted_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted_session["session_id"] == session.session_id
    receipt = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_id,
        session_id=session.session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert receipt["after_hash"] == _sha256_utf8_file(session_path)


def test_claim_execution_event_details_include_session_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="session receipt event projection")
    task_id = int(created["id"])

    claimed = service.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="run-session-receipt-event",
        selection_source="unit",
    )

    assert claimed["success"] is True
    session_id = str(claimed["session"]["session_id"])
    session_path = _session_file_path(workspace, task_id)
    last_receipt = service.last_session_write_receipt()
    assert last_receipt is not None
    expected_receipt = last_receipt.to_dict()

    projected_result = _assert_execution_event_session_write_receipt(
        claimed["execution_event"],
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert projected_result == expected_receipt

    payload = _execution_event_payload_for_result(workspace, claimed["execution_event"], event_type="claimed")
    projected_payload = _assert_execution_event_session_write_receipt(
        payload,
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert projected_payload == expected_receipt


def test_heartbeat_execution_event_details_include_session_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="heartbeat session receipt event projection")
    task_id = int(created["id"])
    claimed = service.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="run-heartbeat-session-receipt-event",
        selection_source="unit",
    )
    assert claimed["success"] is True
    session_id = str(claimed["session"]["session_id"])

    heartbeat = service.heartbeat_execution(
        task_id,
        session_id=session_id,
        lease_ttl_seconds=180,
        context_summary="renew lease after tool dispatch",
    )

    assert heartbeat["success"] is True
    assert heartbeat["execution_event"]["ok"] is True
    assert heartbeat["execution_event"]["event_type"] == "heartbeat_renewed"
    session_path = _session_file_path(workspace, task_id)
    last_receipt = service.last_session_write_receipt()
    assert last_receipt is not None
    expected_receipt = last_receipt.to_dict()
    assert expected_receipt["after_hash"] == _sha256_utf8_file(session_path)

    projected_result = _assert_execution_event_session_write_receipt(
        heartbeat["execution_event"],
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert projected_result == expected_receipt

    payload = _execution_event_payload_for_result(
        workspace,
        heartbeat["execution_event"],
        event_type="heartbeat_renewed",
    )
    projected_payload = _assert_execution_event_session_write_receipt(
        payload,
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert projected_payload == expected_receipt


def test_append_execution_event_omits_session_write_receipt_for_wrong_session_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    first = service.create_task_row(subject="first session receipt task")
    second = service.create_task_row(subject="second session receipt task")
    first_claim = service.claim_execution(
        int(first["id"]),
        worker_id="director",
        role_id="director",
        run_id="run-first-session",
        selection_source="unit",
    )
    second_claim = service.claim_execution(
        int(second["id"]),
        worker_id="director",
        role_id="director",
        run_id="run-second-session",
        selection_source="unit",
    )
    assert first_claim["success"] is True
    assert second_claim["success"] is True
    last_receipt = service.last_session_write_receipt()
    assert last_receipt is not None
    assert last_receipt.session_id == second_claim["session"]["session_id"]

    wrong_session = TaskExecutionSession.from_dict(
        {
            **first_claim["session"],
            "session_id": f"{first_claim['session']['session_id']}-wrong",
        }
    )
    event = service._append_execution_event(
        "heartbeat",
        task_row=first_claim["task"],
        session=wrong_session,
        details={"source": "stale-session-receipt-test"},
    )

    assert event["ok"] is True
    event_details = event.get("details")
    assert isinstance(event_details, dict)
    assert "session_write_receipt" not in event_details
    payload = _execution_event_payload_for_result(workspace, event, event_type="heartbeat")
    details = payload.get("details")
    assert isinstance(details, dict)
    assert "session_write_receipt" not in details
    assert details["source"] == "stale-session-receipt-test"


def test_non_terminal_control_events_do_not_reuse_terminal_session_transition_id(
    tmp_path: Path,
) -> None:
    """Distinct recovery actions may cite one terminal session without colliding.

    Live L3-23 r19 emitted multiple ProjectCompletion owner-rework actions for
    one failed Director session. ``same_task_local_rework_prepared`` reused the
    session's terminal transition id, so the second action hit a FactStream
    idempotency conflict even though its action id and payload were different.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    task = service.create_task_row(subject="terminal-session control-event task")
    task_id = int(task["id"])
    session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id="factory-current",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="unit",
    )
    session.mark_failed(error="compile failed")

    first = service._append_execution_event(
        "same_task_local_rework_prepared",
        task_row=task,
        session=session,
        details={"action_id": "a" * 64},
    )
    second = service._append_execution_event(
        "same_task_local_rework_prepared",
        task_row=task,
        session=session,
        details={"action_id": "b" * 64},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["fact_event_id"] != second["fact_event_id"]
    first_payload = _execution_event_payload_for_result(
        workspace,
        first,
        event_type="same_task_local_rework_prepared",
    )
    second_payload = _execution_event_payload_for_result(
        workspace,
        second,
        event_type="same_task_local_rework_prepared",
    )
    assert first_payload["idempotency_key"] != second_payload["idempotency_key"]
    assert session.terminal_transition_id not in first_payload["idempotency_key"]
    assert session.terminal_transition_id not in second_payload["idempotency_key"]


def test_terminal_outcome_event_keeps_terminal_session_transition_id(tmp_path: Path) -> None:
    """Actual terminal outcome projection remains stable and idempotent."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    task = service.create_task_row(subject="terminal-outcome task")
    session = TaskExecutionSession.create(
        task_id=int(task["id"]),
        role_id="director",
        worker_id="director-worker",
        run_id="factory-current",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="unit",
    )
    session.mark_failed(error="compile failed")

    event = service._append_execution_event(
        "failed",
        task_row=task,
        session=session,
        details={"error": "compile failed"},
    )

    assert event["ok"] is True
    payload = _execution_event_payload_for_result(workspace, event, event_type="failed")
    assert payload["idempotency_key"].endswith(session.terminal_transition_id)


def test_write_session_terminal_preserved_path_writes_while_holding_session_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="preserve terminal session receipt")
    task_id = int(created["id"])

    terminal_session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id="run-terminal-preserved-receipt",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    terminal_session.mark_completed(result_summary="terminal session wins")
    session_path = _session_file_path(workspace, task_id)
    session_path.write_text(
        json.dumps(terminal_session.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    incoming = TaskExecutionSession.from_dict(
        {
            **terminal_session.to_dict(),
            "status": "active",
            "last_result_summary": "",
            "released_at": "",
            "resumable": True,
        }
    )
    file_lock_probe = _observe_session_write_file_lock(
        service,
        monkeypatch,
        task_id=task_id,
    )
    expected_lock_path = service._session_file_lock_path(task_id)

    session_written = service._write_session(incoming)

    assert session_written is False
    assert file_lock_probe.entered_lock_paths == [expected_lock_path]
    assert file_lock_probe.read_observed_under_file_lock == [True]
    assert file_lock_probe.write_observed_under_file_lock
    assert all(file_lock_probe.write_observed_under_file_lock)
    assert expected_lock_path.is_file()
    assert incoming.status == "completed"
    persisted_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted_session["status"] == "completed"
    receipt = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_id,
        session_id=terminal_session.session_id,
        session_path=session_path,
        preserved_terminal_session=True,
    )
    assert receipt["after_hash"] == _sha256_utf8_file(session_path)


def test_failed_session_write_does_not_update_last_session_write_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="failed session receipt write")
    task_id = int(created["id"])
    claimed = service.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="run-session-write-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True
    session_id = str(claimed["session"]["session_id"])
    session_path = _session_file_path(workspace, task_id)
    baseline = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )

    def raise_session_write_unavailable(*_args: object, **_kwargs: Any) -> NoReturn:
        raise RuntimeError("session write unavailable")

    monkeypatch.setattr(service._kernel_fs, "write_json_atomic", raise_session_write_unavailable)

    with pytest.raises(RuntimeError, match="session write unavailable"):
        service.heartbeat_execution(
            task_id,
            session_id=session_id,
            lease_ttl_seconds=180,
            context_summary="receipt should stay anchored to the last successful write",
        )

    last_receipt = service.last_session_write_receipt()
    assert last_receipt is not None
    assert last_receipt.to_dict() == baseline


def test_session_write_cas_conflict_raises_and_preserves_last_session_write_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="session cas guarded write")
    task_id = int(created["id"])
    claimed = service.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="run-session-cas-conflict",
        selection_source="unit",
    )
    assert claimed["success"] is True
    session_id = str(claimed["session"]["session_id"])
    session_path = _session_file_path(workspace, task_id)
    baseline = _assert_task_execution_session_write_receipt(
        service.last_session_write_receipt(),
        task_id=task_id,
        session_id=session_id,
        session_path=session_path,
        preserved_terminal_session=False,
    )
    assert baseline["after_hash"] == _sha256_utf8_file(session_path)

    external_payload = json.loads(session_path.read_text(encoding="utf-8"))
    external_payload["context_summary"] = "external session update wins CAS race"
    external_metadata = external_payload.setdefault("metadata", {})
    assert isinstance(external_metadata, dict)
    external_metadata["cas_probe"] = "external_update_after_before_hash"
    external_content = json.dumps(external_payload, indent=2, ensure_ascii=False) + "\n"
    external_hash = hashlib.sha256(external_content.encode("utf-8")).hexdigest()
    assert external_hash != baseline["after_hash"]

    session_logical_path = service._session_logical_path(task_id)
    original_read_current_session_payload_hash = service._read_current_session_payload_hash
    injected_external_write = False
    observed_hash_reads: list[str] = []

    def read_hash_with_external_session_change(logical_path: str) -> str:
        nonlocal injected_external_write
        current_hash = original_read_current_session_payload_hash(logical_path)
        if logical_path == session_logical_path:
            observed_hash_reads.append(current_hash)
            if not injected_external_write:
                session_path.write_text(external_content, encoding="utf-8")
                injected_external_write = True
        return current_hash

    monkeypatch.setattr(
        service,
        "_read_current_session_payload_hash",
        read_hash_with_external_session_change,
    )

    with pytest.raises(service_module.TaskExecutionSessionWriteConflictError) as exc_info:
        service.heartbeat_execution(
            task_id,
            session_id=session_id,
            lease_ttl_seconds=180,
            context_summary="attempted heartbeat should lose CAS race",
        )

    assert injected_external_write is True
    assert observed_hash_reads[0] == baseline["after_hash"]
    assert observed_hash_reads[-1] == external_hash
    error_message = str(exc_info.value)
    assert session_logical_path in error_message
    assert baseline["after_hash"] in error_message
    assert external_hash in error_message
    assert json.loads(session_path.read_text(encoding="utf-8")) == external_payload
    assert _sha256_utf8_file(session_path) == external_hash
    last_receipt = service.last_session_write_receipt()
    assert last_receipt is not None
    assert last_receipt.to_dict() == baseline


def test_taskboard_save_task_row_write_is_guarded_by_per_row_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    first = service.create_task_row(
        subject="row lock probe one",
        description="baseline row for per-task lock ordering",
        metadata={"phase": "row-lock-baseline", "probe": "one"},
    )
    second = service.create_task_row(
        subject="row lock probe two",
        description="baseline row for per-task lock path separation",
        metadata={"phase": "row-lock-baseline", "probe": "two"},
    )
    first_id = int(first["id"])
    second_id = int(second["id"])

    events: list[str] = []
    active_lock_paths: list[Path] = []
    replace_lock_paths_by_task: dict[int, Path] = {}
    original_replace_task_file = service._board._replace_task_file

    @contextmanager
    def fake_file_lock(lock_file_path: Path) -> Iterator[object]:
        lock_path = Path(lock_file_path)
        events.append(f"lock_enter:{lock_path}")
        active_lock_paths.append(lock_path)
        try:
            yield object()
        finally:
            receipt = service._board._last_row_write_receipt
            if receipt is not None:
                events.append(f"receipt_seen_at_lock_exit:{receipt.task_id}:{lock_path}")
            released_lock_path = active_lock_paths.pop()
            assert released_lock_path == lock_path
            events.append(f"lock_exit:{lock_path}")

    def replace_task_file_with_trace(tmp_path_arg: Path, task_path_arg: Path) -> None:
        assert active_lock_paths, "_replace_task_file must run inside a per-row _file_lock"
        task_id = int(task_path_arg.stem.removeprefix("task_"))
        lock_path = active_lock_paths[-1]
        replace_lock_paths_by_task[task_id] = lock_path
        events.append(f"replace:{task_id}:{task_path_arg}")
        original_replace_task_file(tmp_path_arg, task_path_arg)

    monkeypatch.setattr(service._board, "_file_lock", fake_file_lock)
    monkeypatch.setattr(service._board, "_replace_task_file", replace_task_file_with_trace)

    def index_event_starting_with(prefix: str) -> int:
        return next(index for index, event in enumerate(events) if event.startswith(prefix))

    for task_id, marker in ((first_id, "first-update"), (second_id, "second-update")):
        updated = service.update_task_row(
            f"task-{task_id}",
            metadata={"phase": "row-lock-update", "probe": marker},
        )
        assert updated is not None

        task_path = _task_file_path(workspace, task_id)
        receipt = _assert_task_row_write_receipt(
            service._board.last_row_write_receipt(),
            task_id=task_id,
            task_path=task_path,
        )
        assert receipt["after_hash"] == _sha256_utf8_file(task_path)
        events.append(f"receipt_after_update:{task_id}")

    assert set(replace_lock_paths_by_task) == {first_id, second_id}
    first_lock_path = replace_lock_paths_by_task[first_id]
    second_lock_path = replace_lock_paths_by_task[second_id]
    assert first_lock_path != second_lock_path

    for task_id, lock_path in replace_lock_paths_by_task.items():
        assert f"task_{task_id}" in lock_path.name

        enter_index = events.index(f"lock_enter:{lock_path}")
        replace_index = index_event_starting_with(f"replace:{task_id}:")
        exit_index = events.index(f"lock_exit:{lock_path}")
        receipt_after_update_index = events.index(f"receipt_after_update:{task_id}")
        assert enter_index < replace_index < exit_index < receipt_after_update_index

        receipt_at_exit = f"receipt_seen_at_lock_exit:{task_id}:{lock_path}"
        if receipt_at_exit in events:
            assert replace_index < events.index(receipt_at_exit) < exit_index


def test_taskboard_save_task_fails_closed_when_row_hash_changes_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(
        subject="cas guarded task row",
        description="create establishes the successful row-write baseline",
        metadata={"phase": "row-cas"},
    )
    task_id = int(created["id"])
    task_path = _task_file_path(workspace, task_id)
    create_after_hash = _sha256_utf8_file(task_path)
    create_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert create_receipt["after_hash"] == create_after_hash

    attempted_task = service._board.get(task_id)
    assert attempted_task is not None
    attempted_task.metadata["cas_probe"] = "attempted_update"

    external_payload = json.loads(task_path.read_text(encoding="utf-8"))
    external_metadata = external_payload.setdefault("metadata", {})
    assert isinstance(external_metadata, dict)
    external_metadata["cas_probe"] = "external_update"
    external_content = json.dumps(external_payload, indent=2, ensure_ascii=False) + "\n"
    external_hash = hashlib.sha256(external_content.encode("utf-8")).hexdigest()
    assert external_hash != create_after_hash

    original_write_text = service._board._kernel_fs.write_text
    injected_external_write = False
    attempted_payload_hash: str | None = None

    def write_text_with_external_row_change(
        logical_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> object:
        nonlocal attempted_payload_hash, injected_external_write
        receipt = original_write_text(logical_path, content, encoding=encoding)
        logical_name = Path(str(logical_path)).name
        if (
            not injected_external_write
            and logical_name.startswith(f".task_{task_id}.")
            and logical_name.endswith(".tmp")
        ):
            attempted_payload_hash = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
            task_path.write_text(external_content, encoding="utf-8")
            injected_external_write = True
        return receipt

    monkeypatch.setattr(service._board._kernel_fs, "write_text", write_text_with_external_row_change)

    with pytest.raises(RuntimeError) as exc_info:
        service._board._save_task(attempted_task)

    error_message = str(exc_info.value).lower()
    assert any(term in error_message for term in ("cas", "hash", "concurrent", "changed", "stale", "drift", "conflict"))
    assert injected_external_write is True
    assert attempted_payload_hash is not None
    assert attempted_payload_hash != external_hash

    assert json.loads(task_path.read_text(encoding="utf-8")) == external_payload
    assert _sha256_utf8_file(task_path) == external_hash

    receipt_after_failure = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert receipt_after_failure == create_receipt
    assert receipt_after_failure["after_hash"] == create_after_hash
    assert receipt_after_failure["after_hash"] != attempted_payload_hash


def test_create_task_row_execution_event_details_include_row_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(
        subject="execution event receipt anchored task",
        description="created fact payload must carry the row-write receipt",
        metadata={"phase": "row-write-receipt-execution-event"},
    )
    task_id = int(created["id"])
    task_path = _task_file_path(workspace, task_id)
    after_hash = _sha256_utf8_file(task_path)

    execution_event = created["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=task_path,
    )
    payload = _execution_event_payload_for_result(
        workspace,
        execution_event,
        event_type="created",
    )

    assert payload["task_id"] == str(task_id)
    assert payload["task_row_snapshot"]["id"] == task_id
    assert payload["details"]["source"] == "runtime.task_runtime.create"
    receipt = _assert_execution_event_row_write_receipt(
        payload,
        task_id=task_id,
        task_path=task_path,
    )
    assert receipt["before_hash"] in {
        "",
        "file_absent",
        hashlib.sha256(b"").hexdigest(),
    }
    assert receipt["after_hash"] == after_hash
    assert receipt["operation"] == "replace"


def test_append_execution_event_omits_row_write_receipt_for_wrong_task_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    current = service.create_task_row(subject="current task for stale receipt guard")
    current_id = int(current["id"])
    stale = service.create_task_row(subject="different task with latest receipt")
    stale_id = int(stale["id"])
    assert stale_id != current_id
    stale_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=stale_id,
        task_path=_task_file_path(workspace, stale_id),
    )
    assert stale_receipt["task_id"] != current_id

    current_row = service.get_task(current_id)
    assert isinstance(current_row, dict)
    unknown_task_id = stale_id + 1000
    wrong_task_row = {
        **current_row,
        "id": unknown_task_id,
    }
    execution_event = service._append_execution_event(
        "unit_stale_receipt_probe",
        task_row=wrong_task_row,
        session=None,
        details={"source": "unit.stale_receipt_probe"},
    )

    assert execution_event["ok"] is True
    payload = _execution_event_payload_for_result(
        workspace,
        execution_event,
        event_type="unit_stale_receipt_probe",
    )
    details = payload["details"]
    assert details["source"] == "unit.stale_receipt_probe"
    assert "row_write_receipt" not in details


def test_task_runtime_service_projects_rows_from_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

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
    service = _create_bootstrapped_task_runtime_service(workspace)
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


def test_list_observable_task_rows_does_not_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    file_rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "task_id": "1",
            "subject": "read-only observable projection",
            "status": "pending",
            "metadata": {"source": "file_row"},
        }
    ]
    refresh_calls: list[str] = []

    def reject_refresh_dependency_unblocks() -> NoReturn:
        refresh_calls.append("refresh_dependency_unblocks")
        raise AssertionError("list_observable_task_rows must be a pure read projection")

    def list_file_task_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state in {False, True}
        return [dict(row) for row in file_rows]

    def list_fact_rows() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "_list_file_task_rows", list_file_task_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", list_fact_rows)

    rows = service.list_observable_task_rows()

    assert rows == file_rows
    assert refresh_calls == []


def test_fact_only_task_row_read_model_rows_reads_execution_facts_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    fact_rows: list[dict[str, Any]] = [
        {
            "id": 81,
            "task_id": "81",
            "subject": "fact-only observable row",
            "status": "in_progress",
            "metadata": {"source": "task_runtime.execution_fact"},
        }
    ]
    projected_rows: list[dict[str, Any]] = [
        {
            "id": 81,
            "task_id": "81",
            "subject": "fact-only observable row",
            "status": "in_progress",
            "metadata": {"source": "task_runtime.execution_fact"},
            "running": True,
        }
    ]
    calls: list[str] = []

    def reject_file_task_rows(**_: Any) -> NoReturn:
        calls.append("_list_file_task_rows")
        raise AssertionError("fact-only read model must not read file-backed task rows")

    def reject_refresh_dependency_unblocks() -> NoReturn:
        calls.append("refresh_dependency_unblocks")
        raise AssertionError("fact-only read model must not refresh dependency unblocks")

    def list_fact_rows() -> list[dict[str, Any]]:
        calls.append("list_task_rows_from_execution_facts")
        return [dict(row) for row in fact_rows]

    def project_observable_task_rows(
        file_rows: list[dict[str, Any]],
        received_fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        calls.append("_project_observable_task_rows")
        assert file_rows == []
        assert received_fact_rows == fact_rows
        return [dict(row) for row in projected_rows]

    monkeypatch.setattr(service, "_list_file_task_rows", reject_file_task_rows)
    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", list_fact_rows)
    monkeypatch.setattr(service, "_project_observable_task_rows", project_observable_task_rows)

    rows = service._fact_only_task_row_read_model_rows()

    assert rows == projected_rows
    assert calls == ["list_task_rows_from_execution_facts", "_project_observable_task_rows"]


def test_list_observable_task_rows_uses_fact_only_read_model_when_cutover_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    fact_only_rows: list[dict[str, Any]] = [
        {
            "id": 91,
            "task_id": "91",
            "subject": "fact-only cutover row",
            "status": "completed",
        }
    ]
    calls: list[str] = []

    def cutover_readiness() -> dict[str, Any]:
        calls.append("task_row_read_model_cutover_readiness")
        return {"ready": True, "blocking_reasons": []}

    def fact_only_rows_helper() -> list[dict[str, Any]]:
        calls.append("_fact_only_task_row_read_model_rows")
        return [dict(row) for row in fact_only_rows]

    def reject_transitional_rows_helper() -> NoReturn:
        calls.append("_transitional_task_row_read_model_rows")
        raise AssertionError("ready cutover must not call transitional read model helper")

    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", cutover_readiness)
    monkeypatch.setattr(service, "_fact_only_task_row_read_model_rows", fact_only_rows_helper)
    monkeypatch.setattr(service, "_transitional_task_row_read_model_rows", reject_transitional_rows_helper)

    rows = service.list_observable_task_rows()

    assert rows == fact_only_rows
    assert calls == ["task_row_read_model_cutover_readiness", "_fact_only_task_row_read_model_rows"]


def test_list_observable_task_rows_uses_transitional_read_model_when_cutover_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    transitional_rows: list[dict[str, Any]] = [
        {
            "id": 101,
            "task_id": "101",
            "subject": "transitional fallback row",
            "status": "pending",
            "metadata": {"source": "file_row"},
        }
    ]
    calls: list[str] = []

    def cutover_readiness() -> dict[str, Any]:
        calls.append("task_row_read_model_cutover_readiness")
        return {"ready": False, "blocking_reasons": ["task_row_file_fallback_required"]}

    def reject_fact_only_rows_helper() -> NoReturn:
        calls.append("_fact_only_task_row_read_model_rows")
        raise AssertionError("blocked cutover must not call fact-only read model helper")

    def transitional_rows_helper() -> list[dict[str, Any]]:
        calls.append("_transitional_task_row_read_model_rows")
        return [dict(row) for row in transitional_rows]

    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", cutover_readiness)
    monkeypatch.setattr(service, "_fact_only_task_row_read_model_rows", reject_fact_only_rows_helper)
    monkeypatch.setattr(service, "_transitional_task_row_read_model_rows", transitional_rows_helper)

    rows = service.list_observable_task_rows()

    assert rows == transitional_rows
    assert calls == ["task_row_read_model_cutover_readiness", "_transitional_task_row_read_model_rows"]


def test_observable_task_rows_projection_marks_fact_only_rows_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    readiness = {"ready": True, "blocking_reasons": []}
    rows = [{"id": 1, "status": "completed", "metadata": {"source": "task_runtime.execution_fact"}}]
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", lambda: dict(readiness))
    monkeypatch.setattr(service, "_fact_only_task_row_read_model_rows", lambda: [dict(row) for row in rows])

    projection = service.query_observable_task_rows_projection()

    assert projection.authoritative is True
    assert projection.degraded is False
    assert projection.source == "task_runtime.execution_fact"
    assert projection.rows == tuple(rows)
    assert projection.readiness == readiness


def test_observable_task_rows_projection_marks_file_fallback_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    readiness = {"ready": False, "blocking_reasons": ["task_row_file_fallback_required"]}
    rows = [{"id": 1, "status": "pending", "metadata": {"source": "file_row"}}]
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", lambda: dict(readiness))
    monkeypatch.setattr(service, "_transitional_task_row_read_model_rows", lambda: [dict(row) for row in rows])

    projection = service.query_observable_task_rows_projection()

    assert projection.authoritative is False
    assert projection.degraded is True
    assert projection.source == "task_runtime.transitional_file_fallback"
    assert projection.rows == tuple(rows)
    assert projection.readiness == readiness


def test_observable_task_rows_projection_reads_execution_fact_stream_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    query_calls = 0

    def query_execution_facts(*, limit: int = 500, offset: int = 0) -> SimpleNamespace:
        nonlocal query_calls
        query_calls += 1
        assert limit == 500
        assert offset == 0
        return SimpleNamespace(total=0, events=())

    monkeypatch.setattr(service, "_query_execution_fact_events", query_execution_facts)
    monkeypatch.setattr(service, "_list_file_task_rows", lambda *args, **kwargs: [])

    projection = service.query_observable_task_rows_projection()

    assert projection.authoritative is True
    assert projection.rows == ()
    assert query_calls == 1

    # Cache is projection-scoped: a separate authority query must observe the
    # stream again instead of reusing stale cross-query state.
    service.query_observable_task_rows_projection()
    assert query_calls == 2


def test_task_row_read_model_fallback_coverage_reports_full_file_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(
        service,
        "_list_file_task_rows",
        lambda: [
            {"id": 1, "task_id": "1", "subject": "covered file row one"},
            {"id": 2, "task_id": "2", "subject": "covered file row two"},
        ],
    )
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            {"id": 1, "task_id": "1", "subject": "execution fact one"},
            {"id": 2, "task_id": "2", "subject": "execution fact two"},
        ],
    )

    coverage = service.task_row_read_model_fallback_coverage()

    _assert_task_row_read_model_fallback_coverage(
        coverage,
        coverage_ratio=1.0,
        transitional_file_fallback_required=False,
        file_row_ids_without_execution_fact=[],
        fact_row_ids_without_file_row=[],
    )


def test_task_row_read_model_fallback_coverage_reports_file_rows_without_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(
        service,
        "_list_file_task_rows",
        lambda: [
            {"id": 1, "task_id": "1", "subject": "covered file row one"},
            {"id": 2, "task_id": "2", "subject": "missing fact row"},
            {"id": 3, "task_id": "3", "subject": "covered file row three"},
        ],
    )
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            {"id": 1, "task_id": "1", "subject": "execution fact one"},
            {"id": 3, "task_id": "3", "subject": "execution fact three"},
        ],
    )

    coverage = service.task_row_read_model_fallback_coverage()

    _assert_task_row_read_model_fallback_coverage(
        coverage,
        coverage_ratio=2 / 3,
        transitional_file_fallback_required=True,
        file_row_ids_without_execution_fact=["2"],
        fact_row_ids_without_file_row=[],
    )


def test_task_row_read_model_fallback_coverage_reports_fact_only_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(
        service,
        "_list_file_task_rows",
        lambda: [{"id": 1, "task_id": "1", "subject": "covered file row"}],
    )
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            {"id": 1, "task_id": "1", "subject": "execution fact one"},
            {
                "id": "FACT-ONLY",
                "task_id": "FACT-ONLY",
                "subject": "execution fact without file row",
            },
        ],
    )

    coverage = service.task_row_read_model_fallback_coverage()

    _assert_task_row_read_model_fallback_coverage(
        coverage,
        coverage_ratio=1.0,
        transitional_file_fallback_required=False,
        file_row_ids_without_execution_fact=[],
        fact_row_ids_without_file_row=["FACT-ONLY"],
    )


def test_task_row_read_model_fallback_coverage_does_not_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    refresh_calls: list[str] = []

    def reject_refresh_dependency_unblocks() -> NoReturn:
        refresh_calls.append("refresh_dependency_unblocks")
        raise AssertionError("fallback coverage must be a side-effect-free read projection")

    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "_list_file_task_rows", lambda: [{"id": 1, "task_id": "1"}])
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", lambda: [{"id": 1, "task_id": "1"}])

    coverage = service.task_row_read_model_fallback_coverage()

    assert refresh_calls == []
    _assert_task_row_read_model_fallback_coverage(
        coverage,
        coverage_ratio=1.0,
        transitional_file_fallback_required=False,
        file_row_ids_without_execution_fact=[],
        fact_row_ids_without_file_row=[],
    )


def test_task_row_read_model_projection_parity_coverage_ready_for_fact_only_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(service, "_list_file_task_rows", lambda: [])
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            {
                "id": 41,
                "task_id": "41",
                "subject": "fact-only row is already the future projection",
                "status": "in_progress",
                "metadata": {"source": "task_runtime.execution_fact"},
            }
        ],
    )

    coverage = service.task_row_read_model_projection_parity_coverage()

    _assert_task_row_read_model_projection_parity_coverage(
        coverage,
        parity_ratio=1.0,
        observable_projection_parity_ready=True,
        transitional_only_row_ids=[],
        fact_only_row_ids=[],
        row_ids_with_projection_mismatch=[],
    )


def test_task_row_read_model_projection_parity_coverage_prefers_fact_over_stale_file_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(
        service,
        "_list_file_task_rows",
        lambda: [
            {
                "id": 52,
                "task_id": "52",
                "subject": "file row field that the fact snapshot does not preserve",
                "status": "pending",
                "metadata": {"source": "file_row"},
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            {
                "id": 52,
                "task_id": "52",
                "subject": "fact-only row has different observable content",
                "status": "pending",
                "metadata": {"source": "task_runtime.execution_fact"},
            }
        ],
    )

    coverage = service.task_row_read_model_projection_parity_coverage()

    _assert_task_row_read_model_projection_parity_coverage(
        coverage,
        parity_ratio=1.0,
        observable_projection_parity_ready=True,
        transitional_only_row_ids=[],
        fact_only_row_ids=[],
        row_ids_with_projection_mismatch=[],
    )
