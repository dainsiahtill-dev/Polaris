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






def test_task_row_read_model_projection_parity_coverage_reports_projection_only_row_ids(
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
                "id": 61,
                "task_id": "61",
                "subject": "file-only transitional row",
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
                "id": 62,
                "task_id": "62",
                "subject": "fact-only future row",
                "status": "in_progress",
                "metadata": {"source": "task_runtime.execution_fact"},
            }
        ],
    )
    projection_calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    def project_observable_task_rows(
        file_rows: list[dict[str, Any]],
        fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        projection_calls.append(
            (
                [dict(row) for row in file_rows],
                [dict(row) for row in fact_rows],
            )
        )
        if file_rows:
            return [dict(file_rows[0])]
        return [dict(fact_rows[0])]

    monkeypatch.setattr(service, "_project_observable_task_rows", project_observable_task_rows)

    coverage = service.task_row_read_model_projection_parity_coverage()

    _assert_task_row_read_model_projection_parity_coverage(
        coverage,
        parity_ratio=0.0,
        observable_projection_parity_ready=False,
        transitional_only_row_ids=["61"],
        fact_only_row_ids=["62"],
        row_ids_with_projection_mismatch=[],
    )
    assert len(projection_calls) == 2


def test_observable_task_row_stats_include_delegated_read_model_fallback_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    observable_rows: list[dict[str, Any]] = [
        {"id": 1, "task_id": "1", "status": "pending", "blocked_by": []},
        {"id": 2, "task_id": "2", "status": "in_progress", "blocked_by": []},
        {"id": 3, "task_id": "3", "status": "completed", "blocked_by": []},
        {"id": 4, "task_id": "4", "status": "pending", "blocked_by": [1]},
    ]
    sentinel_coverage: dict[str, Any] = {
        "file_rows_count": 4,
        "fact_rows_count": 3,
        "projected_rows_count": 4,
        "file_row_ids_without_execution_fact": ["4"],
        "fact_row_ids_without_file_row": [],
        "coverage_ratio": 0.75,
        "transitional_file_fallback_required": True,
        "sentinel": "coverage-from-task-row-read-model-fallback",
    }
    sentinel_parity_coverage: dict[str, Any] = {
        "observable_projection_parity_ready": False,
        "sentinel": "task-row-read-model-projection-parity",
    }
    sentinel_readiness: dict[str, Any] = {
        "ready": False,
        "blocking_reasons": ["sentinel-readiness"],
        "task_row_read_model_projection_parity_coverage": sentinel_parity_coverage,
    }
    coverage_calls: list[str] = []

    def fallback_coverage() -> dict[str, Any]:
        coverage_calls.append("task_row_read_model_fallback_coverage")
        return sentinel_coverage

    monkeypatch.setattr(service, "list_observable_task_rows", lambda: [dict(row) for row in observable_rows])
    monkeypatch.setattr(service, "task_row_read_model_fallback_coverage", fallback_coverage)
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", lambda: sentinel_readiness)

    stats = service.get_observable_task_row_stats()

    assert stats["total"] == 4
    assert stats["ready"] == 1
    assert stats["pending"] == 2
    assert stats["in_progress"] == 1
    assert stats["completed"] == 1
    assert stats["read_model_fallback_coverage"] is sentinel_coverage
    assert stats["read_model_cutover_readiness"] is sentinel_readiness
    assert (
        stats["read_model_cutover_readiness"]["task_row_read_model_projection_parity_coverage"]
        is sentinel_parity_coverage
    )
    assert coverage_calls == ["task_row_read_model_fallback_coverage"]


def test_get_task_row_stats_delegates_to_observable_stats_without_rebuilding_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    sentinel_stats: dict[str, Any] = {
        "total": 7,
        "ready": 2,
        "pending": 3,
        "read_model_fallback_coverage": {"sentinel": "observable-stats"},
    }
    observable_stats_calls: list[str] = []

    def observable_stats() -> dict[str, Any]:
        observable_stats_calls.append("get_observable_task_row_stats")
        return sentinel_stats

    def reject_fallback_coverage() -> NoReturn:
        raise AssertionError("get_task_row_stats must not rebuild fallback coverage")

    def reject_observable_rows() -> NoReturn:
        raise AssertionError("get_task_row_stats must delegate instead of rebuilding status counts")

    monkeypatch.setattr(service, "get_observable_task_row_stats", observable_stats)
    monkeypatch.setattr(service, "task_row_read_model_fallback_coverage", reject_fallback_coverage)
    monkeypatch.setattr(service, "list_observable_task_rows", reject_observable_rows)

    stats = service.get_task_row_stats()

    assert stats is sentinel_stats
    assert observable_stats_calls == ["get_observable_task_row_stats"]


def test_projected_runtime_execution_session_fallback_coverage_reports_full_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state is True
        return [
            _runtime_execution_projected_row(1, subject="file projected session one"),
            _runtime_execution_projected_row(2, subject="file projected session two"),
        ]

    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            _runtime_execution_projected_row(1, subject="fact projected session one"),
            _runtime_execution_projected_row(2, subject="fact projected session two"),
        ],
    )

    coverage = service.projected_runtime_execution_session_fallback_coverage()

    _assert_projected_runtime_execution_session_fallback_coverage(
        coverage,
        file_projected_session_rows_count=2,
        fact_projected_session_rows_count=2,
        coverage_ratio=1.0,
        projected_session_file_fallback_required=False,
        file_projected_session_task_ids_without_execution_fact=[],
        fact_projected_session_task_ids_without_file_row=[],
    )


def test_projected_runtime_execution_session_fallback_coverage_reports_file_session_without_fact_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state is True
        return [
            _runtime_execution_projected_row(1, subject="covered file projected session"),
            _runtime_execution_projected_row(2, subject="file projected session without fact"),
        ]

    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            _runtime_execution_projected_row(1, subject="covered fact projected session"),
            {"id": 2, "task_id": "2", "subject": "fact row without projected runtime execution"},
        ],
    )

    coverage = service.projected_runtime_execution_session_fallback_coverage()

    _assert_projected_runtime_execution_session_fallback_coverage(
        coverage,
        file_projected_session_rows_count=2,
        fact_projected_session_rows_count=1,
        coverage_ratio=0.5,
        projected_session_file_fallback_required=True,
        file_projected_session_task_ids_without_execution_fact=["2"],
        fact_projected_session_task_ids_without_file_row=[],
    )


def test_projected_runtime_execution_session_fallback_coverage_reports_fact_only_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state is True
        return [_runtime_execution_projected_row(1, subject="covered file projected session")]

    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            _runtime_execution_projected_row(1, subject="covered fact projected session"),
            _runtime_execution_projected_row(3, subject="fact-only projected session"),
        ],
    )

    coverage = service.projected_runtime_execution_session_fallback_coverage()

    _assert_projected_runtime_execution_session_fallback_coverage(
        coverage,
        file_projected_session_rows_count=1,
        fact_projected_session_rows_count=2,
        coverage_ratio=1.0,
        projected_session_file_fallback_required=False,
        file_projected_session_task_ids_without_execution_fact=[],
        fact_projected_session_task_ids_without_file_row=["3"],
    )


def test_observable_task_row_stats_include_delegated_projected_runtime_execution_session_fallback_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    sentinel_coverage: dict[str, Any] = {
        "file_projected_session_rows_count": 1,
        "fact_projected_session_rows_count": 1,
        "file_projected_session_task_ids_without_execution_fact": [],
        "fact_projected_session_task_ids_without_file_row": [],
        "coverage_ratio": 1.0,
        "projected_session_file_fallback_required": False,
        "sentinel": "projected-runtime-execution-session-coverage",
    }
    sentinel_parity_coverage: dict[str, Any] = {
        "observable_projection_parity_ready": True,
        "sentinel": "task-row-read-model-projection-parity",
    }
    sentinel_readiness: dict[str, Any] = {
        "ready": True,
        "blocking_reasons": [],
        "task_row_read_model_projection_parity_coverage": sentinel_parity_coverage,
    }
    coverage_calls: list[str] = []

    def projected_session_fallback_coverage() -> dict[str, Any]:
        coverage_calls.append("projected_runtime_execution_session_fallback_coverage")
        return sentinel_coverage

    monkeypatch.setattr(
        service,
        "list_observable_task_rows",
        lambda: [{"id": 1, "task_id": "1", "status": "pending", "blocked_by": []}],
    )
    monkeypatch.setattr(
        service,
        "task_row_read_model_fallback_coverage",
        lambda: {"sentinel": "read-model-fallback-coverage"},
    )
    monkeypatch.setattr(
        service,
        "projected_runtime_execution_session_fallback_coverage",
        projected_session_fallback_coverage,
    )
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", lambda: sentinel_readiness)

    stats = service.get_observable_task_row_stats()

    assert stats["total"] == 1
    assert stats["pending"] == 1
    assert stats["projected_runtime_execution_session_fallback_coverage"] is sentinel_coverage
    assert stats["read_model_cutover_readiness"] is sentinel_readiness
    assert (
        stats["read_model_cutover_readiness"]["task_row_read_model_projection_parity_coverage"]
        is sentinel_parity_coverage
    )
    assert coverage_calls == ["projected_runtime_execution_session_fallback_coverage"]


def test_projected_runtime_execution_session_fallback_coverage_does_not_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    refresh_calls: list[str] = []

    def reject_refresh_dependency_unblocks() -> NoReturn:
        refresh_calls.append("refresh_dependency_unblocks")
        raise AssertionError("projected runtime-execution session coverage must be a read-only projection")

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state is True
        return [_runtime_execution_projected_row(1)]

    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [_runtime_execution_projected_row(1)],
    )

    coverage = service.projected_runtime_execution_session_fallback_coverage()

    assert refresh_calls == []
    _assert_projected_runtime_execution_session_fallback_coverage(
        coverage,
        file_projected_session_rows_count=1,
        fact_projected_session_rows_count=1,
        coverage_ratio=1.0,
        projected_session_file_fallback_required=False,
        file_projected_session_task_ids_without_execution_fact=[],
        fact_projected_session_task_ids_without_file_row=[],
    )


def test_find_projected_runtime_execution_session_returns_fact_session_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    fact_row = _runtime_execution_projected_row(244, subject="fact session wins")
    fact_lookup_calls: list[int] = []

    def latest_fact_row(
        task_id: int,
        *,
        page_size: int = 500,
    ) -> dict[str, Any]:
        assert page_size == 500
        fact_lookup_calls.append(task_id)
        return fact_row

    def reject_fallback_gate() -> NoReturn:
        raise AssertionError("fact-projected session must bypass the file fallback gate")

    def reject_file_fallback(
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> NoReturn:
        raise AssertionError(
            f"fact-projected session must bypass file fallback for task_id={task_id} "
            f"augment_runtime_state={augment_runtime_state}"
        )

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", latest_fact_row)
    monkeypatch.setattr(service, "_projected_runtime_execution_session_file_fallback_allowed", reject_fallback_gate)
    monkeypatch.setattr(service, "_find_projected_runtime_execution_session_from_file_rows", reject_file_fallback)

    session = service._find_projected_runtime_execution_session(244)

    assert session is not None
    assert session.task_id == 244
    assert session.run_id == "run-projected-runtime-execution-244"
    assert fact_lookup_calls == [244]


def test_find_projected_runtime_execution_session_uses_file_fallback_when_readiness_requires_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    fallback_row = _runtime_execution_projected_row(245, subject="allowed fallback session")
    fallback_session = service._runtime_execution_session_from_projected_row(fallback_row)
    assert fallback_session is not None
    fallback_calls: list[tuple[int, bool]] = []
    readiness_calls: list[str] = []

    def file_fallback(
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> TaskExecutionSession | None:
        fallback_calls.append((task_id, augment_runtime_state))
        return fallback_session

    def readiness() -> dict[str, Any]:
        readiness_calls.append("task_row_read_model_cutover_readiness")
        return _projected_session_file_fallback_readiness(required=True)

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", lambda task_id: None)
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", readiness)
    monkeypatch.setattr(service, "_find_projected_runtime_execution_session_from_file_rows", file_fallback)

    session = service._find_projected_runtime_execution_session(245)

    assert session is fallback_session
    assert fallback_calls == [(245, True)]
    assert readiness_calls == ["task_row_read_model_cutover_readiness"]


def test_find_projected_runtime_execution_session_skips_file_fallback_when_readiness_disallows_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    readiness_calls: list[str] = []

    def readiness() -> dict[str, Any]:
        readiness_calls.append("task_row_read_model_cutover_readiness")
        return _projected_session_file_fallback_readiness(required=False)

    def reject_file_fallback(
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> NoReturn:
        raise AssertionError(
            f"file fallback must stay disabled for task_id={task_id} augment_runtime_state={augment_runtime_state}"
        )

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", lambda task_id: None)
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", readiness)
    monkeypatch.setattr(service, "_find_projected_runtime_execution_session_from_file_rows", reject_file_fallback)

    session = service._find_projected_runtime_execution_session(246)

    assert session is None
    assert readiness_calls == ["task_row_read_model_cutover_readiness"]


def test_find_projected_runtime_execution_session_locked_uses_file_fallback_without_runtime_augmentation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    fallback_row = _runtime_execution_projected_row(247, subject="locked fallback session")
    fallback_session = service._runtime_execution_session_from_projected_row(fallback_row)
    assert fallback_session is not None
    fallback_calls: list[tuple[int, bool]] = []

    def file_fallback(
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> TaskExecutionSession | None:
        fallback_calls.append((task_id, augment_runtime_state))
        return fallback_session

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", lambda task_id: None)
    monkeypatch.setattr(
        service,
        "task_row_read_model_cutover_readiness",
        lambda: pytest.fail("locked projected-session lookup must not evaluate cutover readiness"),
    )
    monkeypatch.setattr(service, "_find_projected_runtime_execution_session_from_file_rows", file_fallback)

    session = service._find_projected_runtime_execution_session_locked(247)

    assert session is fallback_session
    assert fallback_calls == [(247, False)]


def test_find_projected_runtime_execution_session_locked_preserves_file_fallback_without_readiness_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    fallback_row = _runtime_execution_projected_row(248, subject="locked fallback ignores readiness gate")
    fallback_session = service._runtime_execution_session_from_projected_row(fallback_row)
    assert fallback_session is not None
    fallback_calls: list[tuple[int, bool]] = []

    def reject_readiness() -> NoReturn:
        raise AssertionError("locked projected-session lookup must not evaluate cutover readiness")

    def file_fallback(
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> TaskExecutionSession | None:
        fallback_calls.append((task_id, augment_runtime_state))
        return fallback_session

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", lambda task_id: None)
    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", reject_readiness)
    monkeypatch.setattr(service, "_find_projected_runtime_execution_session_from_file_rows", file_fallback)

    session = service._find_projected_runtime_execution_session_locked(248)

    assert session is fallback_session
    assert fallback_calls == [(248, False)]


def test_task_row_read_model_cutover_readiness_ready_when_file_rows_and_projected_sessions_have_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = False,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state in {False, True}
        projected_session_row = _runtime_execution_projected_row(
            2,
            subject="covered projected session",
        )
        projected_session_row["metadata"] = {
            **dict(projected_session_row["metadata"]),
            "previous_status": "",
        }
        return [
            {
                "id": 1,
                "task_id": "1",
                "subject": "covered row",
                "status": "pending",
                "metadata": {"source": "shared_projection", "previous_status": "pending"},
            },
            projected_session_row,
        ]

    def fact_rows() -> list[dict[str, Any]]:
        projected_session_row = _runtime_execution_projected_row(
            2,
            subject="covered projected session",
        )
        projected_session_row["metadata"] = {
            **dict(projected_session_row["metadata"]),
            "previous_status": "",
        }
        return [
            {
                "id": 1,
                "task_id": "1",
                "subject": "covered row",
                "status": "pending",
                "metadata": {"source": "shared_projection", "previous_status": "pending"},
            },
            projected_session_row,
        ]

    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", fact_rows)

    readiness = service.task_row_read_model_cutover_readiness()

    _assert_task_row_read_model_cutover_readiness(
        readiness,
        ready=True,
        blocking_reasons=[],
    )
    assert readiness["observable_projection_parity_ready"] is True
    _assert_task_row_read_model_projection_parity_coverage(
        readiness["task_row_read_model_projection_parity_coverage"],
        parity_ratio=1.0,
        observable_projection_parity_ready=True,
        transitional_only_row_ids=[],
        fact_only_row_ids=[],
        row_ids_with_projection_mismatch=[],
    )


def test_task_row_read_model_cutover_readiness_ignores_stale_file_projection_when_fact_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(
        service,
        "_list_file_task_rows",
        lambda **_: [
            {
                "id": 71,
                "task_id": "71",
                "subject": "file row needs fact parity",
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
                "id": 71,
                "task_id": "71",
                "subject": "fact row differs from transitional overlay",
                "status": "pending",
                "metadata": {"source": "task_runtime.execution_fact"},
            }
        ],
    )

    readiness = service.task_row_read_model_cutover_readiness()

    assert readiness["ready"] is True
    assert readiness["observable_projection_parity_ready"] is True
    assert "observable_projection_parity_mismatch" not in readiness["blocking_reasons"]
    _assert_task_row_read_model_projection_parity_coverage(
        readiness["task_row_read_model_projection_parity_coverage"],
        parity_ratio=1.0,
        observable_projection_parity_ready=True,
        transitional_only_row_ids=[],
        fact_only_row_ids=[],
        row_ids_with_projection_mismatch=[],
    )


def test_task_row_read_model_cutover_readiness_blocks_when_file_row_requires_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    monkeypatch.setattr(
        service,
        "_list_file_task_rows",
        lambda **_: [
            {"id": 1, "task_id": "1", "subject": "covered file row", "status": "pending"},
            {"id": 2, "task_id": "2", "subject": "missing execution fact", "status": "pending"},
        ],
    )
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [{"id": 1, "task_id": "1", "subject": "covered fact row", "status": "pending"}],
    )

    readiness = service.task_row_read_model_cutover_readiness()

    assert readiness["ready"] is False
    assert "task_row_file_fallback_required" in readiness["blocking_reasons"]


def test_task_row_read_model_cutover_readiness_blocks_when_projected_session_requires_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = False,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state in {False, True}
        return [
            _runtime_execution_projected_row(1, subject="covered projected session"),
            _runtime_execution_projected_row(2, subject="projected session missing fact session"),
        ]

    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(
        service,
        "list_task_rows_from_execution_facts",
        lambda: [
            _runtime_execution_projected_row(1, subject="covered fact projected session"),
            {"id": 2, "task_id": "2", "subject": "fact row without projected runtime execution"},
        ],
    )

    readiness = service.task_row_read_model_cutover_readiness()

    assert readiness["ready"] is False
    assert "projected_session_file_fallback_required" in readiness["blocking_reasons"]


def test_observable_task_row_stats_include_read_model_cutover_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    def file_rows(
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = False,
    ) -> list[dict[str, Any]]:
        assert include_terminal is True
        assert augment_runtime_state in {False, True}
        projected_session_row = _runtime_execution_projected_row(
            2,
            subject="stats covered projected session",
        )
        projected_session_row["metadata"] = {
            **dict(projected_session_row["metadata"]),
            "previous_status": "",
        }
        return [
            {
                "id": 1,
                "task_id": "1",
                "subject": "stats covered row",
                "status": "pending",
                "blocked_by": [],
                "metadata": {"source": "shared_projection", "previous_status": "pending"},
            },
            projected_session_row,
        ]

    def fact_rows() -> list[dict[str, Any]]:
        projected_session_row = _runtime_execution_projected_row(
            2,
            subject="stats covered projected session",
        )
        projected_session_row["metadata"] = {
            **dict(projected_session_row["metadata"]),
            "previous_status": "",
        }
        return [
            {
                "id": 1,
                "task_id": "1",
                "subject": "stats covered row",
                "status": "pending",
                "blocked_by": [],
                "metadata": {"source": "shared_projection", "previous_status": "pending"},
            },
            projected_session_row,
        ]

    monkeypatch.setattr(service, "_list_file_task_rows", file_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", fact_rows)

    direct_readiness = service.task_row_read_model_cutover_readiness()
    stats = service.get_observable_task_row_stats()

    assert stats["read_model_cutover_readiness"] == direct_readiness
    _assert_task_row_read_model_cutover_readiness(
        stats["read_model_cutover_readiness"],
        ready=True,
        blocking_reasons=[],
    )
    _assert_task_row_read_model_projection_parity_coverage(
        stats["read_model_cutover_readiness"]["task_row_read_model_projection_parity_coverage"],
        parity_ratio=1.0,
        observable_projection_parity_ready=True,
        transitional_only_row_ids=[],
        fact_only_row_ids=[],
        row_ids_with_projection_mismatch=[],
    )


def test_task_row_stats_outlets_do_not_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    refresh_calls: list[str] = []

    def reject_refresh_dependency_unblocks() -> NoReturn:
        refresh_calls.append("refresh_dependency_unblocks")
        raise AssertionError("task-row stats must be read-only")

    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "list_observable_task_rows", lambda: [{"id": 1, "task_id": "1", "status": "pending"}])
    monkeypatch.setattr(
        service,
        "task_row_read_model_fallback_coverage",
        lambda: {"sentinel": "read-only-fallback-coverage"},
    )

    observable_stats = service.get_observable_task_row_stats()
    compatibility_stats = service.get_task_row_stats()

    assert observable_stats["total"] == 1
    assert compatibility_stats["total"] == 1
    assert refresh_calls == []


def test_list_observable_task_rows_delegates_to_transitional_read_model_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    sentinel_rows: list[dict[str, Any]] = [
        {
            "id": 61,
            "task_id": "sentinel-observable",
            "subject": "observable helper sentinel",
            "status": "in_progress",
            "metadata": {"source": "transitional_helper"},
        }
    ]
    helper_calls: list[str] = []

    def cutover_readiness() -> dict[str, Any]:
        helper_calls.append("task_row_read_model_cutover_readiness")
        return {"ready": False, "blocking_reasons": ["task_row_file_fallback_required"]}

    def transitional_task_row_read_model_rows() -> list[dict[str, Any]]:
        helper_calls.append("_transitional_task_row_read_model_rows")
        return sentinel_rows

    def reject_file_rows(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("list_observable_task_rows must delegate file/fact loading to the transitional helper")

    def reject_fact_rows(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("list_observable_task_rows must not load execution facts outside the transitional helper")

    def reject_projection(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("list_observable_task_rows must not project rows outside the transitional helper")

    monkeypatch.setattr(service, "task_row_read_model_cutover_readiness", cutover_readiness)
    monkeypatch.setattr(
        service,
        "_transitional_task_row_read_model_rows",
        transitional_task_row_read_model_rows,
        raising=False,
    )
    monkeypatch.setattr(service, "_list_file_task_rows", reject_file_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", reject_fact_rows)
    monkeypatch.setattr(service, "_project_observable_task_rows", reject_projection)

    rows = service.list_observable_task_rows()

    assert rows is sentinel_rows
    assert helper_calls == ["task_row_read_model_cutover_readiness", "_transitional_task_row_read_model_rows"]


def test_list_task_rows_continues_to_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    file_rows: list[dict[str, Any]] = [
        {
            "id": 2,
            "task_id": "2",
            "subject": "mutable file-backed projection",
            "status": "pending",
            "metadata": {"source": "file_row"},
        }
    ]
    events: list[tuple[str, bool | None]] = []

    def record_refresh_dependency_unblocks() -> dict[str, Any]:
        events.append(("refresh_dependency_unblocks", None))
        return {"unblocked_task_ids": [], "execution_events": []}

    def list_file_task_rows(*, include_terminal: bool = True) -> list[dict[str, Any]]:
        events.append(("_list_file_task_rows", include_terminal))
        return [dict(row) for row in file_rows]

    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "_list_file_task_rows", list_file_task_rows)

    rows_without_terminal = service.list_task_rows(include_terminal=False)
    rows_with_default_terminal = service.list_task_rows()

    assert rows_without_terminal == file_rows
    assert rows_with_default_terminal == file_rows
    assert events == [
        ("refresh_dependency_unblocks", None),
        ("_list_file_task_rows", False),
        ("refresh_dependency_unblocks", None),
        ("_list_file_task_rows", True),
    ]


def test_list_ready_task_rows_refreshes_before_observable_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    events: list[str] = []

    def record_refresh_dependency_unblocks() -> dict[str, Any]:
        events.append("refresh_dependency_unblocks")
        return {"unblocked_task_ids": [], "execution_events": []}

    def list_observable_task_rows() -> list[dict[str, Any]]:
        events.append("list_observable_task_rows")
        return [
            {
                "id": 3,
                "task_id": "3",
                "subject": "ready row",
                "status": "pending",
                "blocked_by": [],
            },
            {
                "id": 4,
                "task_id": "4",
                "subject": "blocked row",
                "status": "pending",
                "blocked_by": [3],
            },
        ]

    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "list_observable_task_rows", list_observable_task_rows)

    rows = service.list_ready_task_rows()

    assert [row["id"] for row in rows] == [3]
    assert events == ["refresh_dependency_unblocks", "list_observable_task_rows"]


def test_selection_entrypoints_refresh_before_observable_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    events: list[str] = []

    def record_refresh_dependency_unblocks() -> dict[str, Any]:
        events.append("refresh_dependency_unblocks")
        return {"unblocked_task_ids": [], "execution_events": []}

    def list_observable_task_rows() -> list[dict[str, Any]]:
        events.append("list_observable_task_rows")
        return []

    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "list_observable_task_rows", list_observable_task_rows)

    selected = service.select_next_task()
    claim_next = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        selection_source="test-selection-refresh",
    )

    assert selected is None
    assert claim_next["success"] is False
    assert claim_next["reason"] == "no_claimable_tasks"
    assert events == [
        "refresh_dependency_unblocks",
        "list_observable_task_rows",
        "refresh_dependency_unblocks",
        "list_observable_task_rows",
    ]


