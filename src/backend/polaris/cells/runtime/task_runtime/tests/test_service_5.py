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




def test_claim_execution_spawn_contenders_have_one_session_winner(tmp_path: Path) -> None:
    """Spawned interpreters must not split persisted session claim authority."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seed_service = _create_bootstrapped_task_runtime_service(workspace)
    created = seed_service.create_task_row(subject="spawn claim authority")
    task_id = int(created["id"])
    package_parent = str(Path(__file__).resolve().parents[6])
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)
    context = mp.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue(maxsize=2)
    processes = [
        context.Process(
            target=_multiprocess_claim_execution,
            args=(str(workspace), task_id, worker_id, start_event, result_queue),
        )
        for worker_id in ("spawn-worker-a", "spawn-worker-b")
    ]

    try:
        for process in processes:
            process.start()
        start_event.set()
        try:
            results = [result_queue.get(timeout=20) for _ in processes]
        except Empty:
            pytest.fail("spawn claim contenders did not report within the timeout")
    finally:
        start_event.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert all(process.exitcode == 0 for process in processes)
    winners = [result for result in results if result["success"] is True]
    losers = [result for result in results if result["success"] is False]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0]["reason"] == "lease_conflict"
    assert winners[0]["session"]["session_id"] == losers[0]["session"]["session_id"]


def test_claim_replay_resume_and_reload_keep_execution_attempt_authority(tmp_path: Path) -> None:
    """Replay renews one session; a resumable requeue creates a new attempt."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(subject="claim replay and resume identity")
    task_id = int(created["id"])

    first = service.claim_execution(
        task_id,
        worker_id="director-worker",
        role_id="director",
        run_id="identity-run",
        selection_source="identity-regression",
    )
    replay = service.claim_execution(
        task_id,
        worker_id="director-worker",
        role_id="director",
        run_id="identity-run",
        selection_source="identity-regression",
    )

    assert first["success"] is True
    assert replay["success"] is True
    assert replay["reason"] == "claim_renewed"
    assert replay["session"]["session_id"] == first["session"]["session_id"]
    assert replay["execution_attempt"]["attempt"] == first["execution_attempt"]["attempt"]

    suspended = _settle_claimed_execution_attempt(service, replay, outcome="suspended", summary="requeue-for-resume")
    resumed = service.claim_execution(
        task_id,
        worker_id="director-worker",
        role_id="director",
        run_id="identity-run",
        selection_source="identity-resume-regression",
    )

    assert suspended["success"] is True
    assert resumed["success"] is True
    assert resumed["resumed"] is True
    assert resumed["session"]["session_id"] != first["session"]["session_id"]
    assert resumed["execution_attempt"]["attempt"] == first["execution_attempt"]["attempt"] + 1

    reloaded = _create_bootstrapped_task_runtime_service(workspace)
    identity = TaskRuntimeExecutionAttemptIdentityV1(**dict(resumed["execution_attempt"]))
    verdict = validate_task_runtime_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(workspace=str(workspace), identity=identity)
    )
    assert verdict.valid is True
    assert verdict.code == "valid"
    assert verdict.identity == identity
    assert reloaded._read_session(task_id) is not None


def test_execution_attempt_validation_is_typed_fail_closed_and_read_only(tmp_path: Path) -> None:
    """Attempt validation rejects forged authority fields without mutation."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.create_task_row(
        subject="attempt validation authority",
        metadata={"external_task_id": "external-validation-task"},
    )
    task_id = int(created["id"])
    claimed = service.claim_execution(
        task_id,
        worker_id="validation-worker",
        role_id="director",
        run_id="validation-run",
        external_task_id="external-validation-task",
        selection_source="validation-regression",
    )
    assert claimed["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1(**dict(claimed["execution_attempt"]))
    session_path = _session_file_path(workspace, task_id)
    before_hash = _sha256_utf8_file(session_path)
    before_mtime_ns = session_path.stat().st_mtime_ns

    valid = service.validate_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(workspace=str(workspace), identity=identity)
    )
    assert valid.valid is True
    assert valid.code == "valid"
    assert _sha256_utf8_file(session_path) == before_hash
    assert session_path.stat().st_mtime_ns == before_mtime_ns

    mismatches = (
        (
            "workspace",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=f"{workspace}-forged",
                identity=identity,
            ),
            "workspace_mismatch",
        ),
        (
            "task",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, task_id=task_id + 1000),
            ),
            "session_not_found",
        ),
        (
            "forged_session",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, session_id="forged-session"),
            ),
            "session_mismatch",
        ),
        (
            "attempt",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, attempt=identity.attempt + 1),
            ),
            "attempt_mismatch",
        ),
        (
            "role",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, role_id="qa"),
            ),
            "role_mismatch",
        ),
        (
            "worker",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, worker_id="forged-worker"),
            ),
            "worker_mismatch",
        ),
        (
            "run",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, run_id="forged-run"),
            ),
            "run_mismatch",
        ),
        (
            "external",
            ValidateTaskRuntimeExecutionAttemptQueryV1(
                workspace=str(workspace),
                identity=replace(identity, external_task_id="forged-external-task"),
            ),
            "external_task_id_mismatch",
        ),
    )
    for field_name, query, expected_code in mismatches:
        verdict = service.validate_execution_attempt(query)
        assert verdict.valid is False, field_name
        assert verdict.code == expected_code

    # R145: same-owner renewable lease is not a fencing token for read-only
    # validate. Stale lease_expires_at still validates while the session is
    # active and not expired (concurrent heartbeat during DEO prepare).
    stale_lease = service.validate_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(
            workspace=str(workspace),
            identity=replace(identity, lease_expires_at="2000-01-01T00:00:00+00:00"),
        )
    )
    assert stale_lease.valid is True
    assert stale_lease.code == "valid"

    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    session_payload["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    session_path.write_text(
        json.dumps(session_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    expired_identity = replace(identity, lease_expires_at=session_payload["lease_expires_at"])
    expired = service.validate_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(workspace=str(workspace), identity=expired_identity)
    )
    assert expired.valid is False
    assert expired.code == "session_lease_expired"

    stale_task = service.create_task_row(subject="stale execution attempt")
    stale_task_id = int(stale_task["id"])
    stale_claim = service.claim_execution(
        stale_task_id,
        worker_id="stale-worker",
        role_id="director",
        run_id="stale-run",
        selection_source="validation-regression",
    )
    stale_identity = TaskRuntimeExecutionAttemptIdentityV1(**dict(stale_claim["execution_attempt"]))
    suspended = _settle_claimed_execution_attempt(
        service, stale_claim, outcome="suspended", summary="stale-identity-requeue"
    )
    reclaimed = service.claim_execution(
        stale_task_id,
        worker_id="stale-worker",
        role_id="director",
        run_id="stale-run",
        selection_source="validation-regression",
    )
    assert suspended["success"] is True
    assert reclaimed["success"] is True
    stale = service.validate_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(workspace=str(workspace), identity=stale_identity)
    )
    assert stale.valid is False
    assert stale.code == "session_mismatch"


def test_typed_heartbeat_is_bounded_by_real_spawned_session_lock(tmp_path: Path) -> None:
    """A held cooperative lock rejects in time, then permits a real renewal."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    task_id = int(service.create_task_row(subject="bounded typed heartbeat")["id"])
    claim = service.claim_execution(
        task_id,
        worker_id="heartbeat-worker",
        role_id="chief_engineer",
        run_id="heartbeat-run",
        selection_source="heartbeat-bound-regression",
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1(**dict(claim["execution_attempt"]))
    session_path = _session_file_path(workspace, task_id)
    before_hash = _sha256_utf8_file(session_path)
    package_parent = str(Path(__file__).resolve().parents[6])
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)
    context = mp.get_context("spawn")
    ready_marker_path = workspace / "session-lock-ready.txt"
    holder = context.Process(
        target=_multiprocess_hold_session_lock,
        args=(str(workspace), task_id, str(ready_marker_path), 1.0),
    )
    holder.start()
    try:
        readiness_deadline = time.monotonic() + 10
        while not ready_marker_path.is_file() and time.monotonic() < readiness_deadline:
            time.sleep(0.01)
        assert ready_marker_path.read_text(encoding="utf-8") == "locked\n"
        started_at = time.monotonic()
        blocked = heartbeat_task_runtime_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(workspace),
                identity=identity,
                lease_ttl_seconds=30,
                lock_timeout_seconds=0.1,
                context_summary="bounded-lock-regression",
            )
        )
        elapsed_seconds = time.monotonic() - started_at
        assert blocked.success is False
        assert blocked.reason == "file_lock_timeout"
        assert 0.08 <= elapsed_seconds < 1.0
        assert _sha256_utf8_file(session_path) == before_hash
    finally:
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
    assert holder.exitcode == 0

    renewed = heartbeat_task_runtime_execution_attempt(
        HeartbeatTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(workspace),
            identity=identity,
            lease_ttl_seconds=30,
            lock_timeout_seconds=0.5,
            context_summary="bounded-lock-released",
        )
    )
    assert renewed.success is True
    assert renewed.reason == "heartbeat_renewed"
    assert renewed.renewed_identity is not None
    assert renewed.renewed_identity.lease_expires_at != identity.lease_expires_at
    assert renewed.evidence_anchor["session_write_receipt"]["session_id"] == identity.session_id


def test_typed_heartbeat_rejects_every_forged_stable_identity_field_without_mutation(tmp_path: Path) -> None:
    """The bounded mutation path fences every stable authority component."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    task_id = int(
        service.create_task_row(
            subject="typed heartbeat identity fence",
            metadata={"external_task_id": "typed-heartbeat-task"},
        )["id"]
    )
    claim = service.claim_execution(
        task_id,
        worker_id="typed-worker",
        role_id="chief_engineer",
        run_id="typed-run",
        external_task_id="typed-heartbeat-task",
        selection_source="typed-heartbeat-regression",
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1(**dict(claim["execution_attempt"]))
    session_path = _session_file_path(workspace, task_id)
    before_hash = _sha256_utf8_file(session_path)
    forged_identities = (
        (replace(identity, task_id=task_id + 1), "session_not_found"),
        (replace(identity, session_id="forged-session"), "session_mismatch"),
        (replace(identity, attempt=identity.attempt + 1), "attempt_mismatch"),
        (replace(identity, role_id="director"), "role_mismatch"),
        (replace(identity, worker_id="forged-worker"), "worker_mismatch"),
        (replace(identity, run_id="forged-run"), "run_mismatch"),
        (replace(identity, external_task_id="forged-task"), "external_task_id_mismatch"),
    )
    for forged_identity, expected_reason in forged_identities:
        verdict = heartbeat_task_runtime_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(workspace),
                identity=forged_identity,
                lease_ttl_seconds=30,
                lock_timeout_seconds=0.5,
            )
        )
        assert verdict.success is False
        assert verdict.reason == expected_reason
        assert _sha256_utf8_file(session_path) == before_hash

    # R145/R171: active same-owner lease expiry is a renewable TTL, not an
    # authority/fencing token. Multi-step Director/DEO work may retain an old
    # expiry snapshot while another heartbeat advances the durable session.
    # The service ignores the caller's expiry value and renews from server time
    # only after every stable identity field above has matched.
    stale_lease = replace(identity, lease_expires_at="2000-01-01T00:00:00+00:00")
    renewed = heartbeat_task_runtime_execution_attempt(
        HeartbeatTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(workspace),
            identity=stale_lease,
            lease_ttl_seconds=30,
            lock_timeout_seconds=0.5,
        )
    )
    assert renewed.success is True
    assert renewed.reason == "heartbeat_renewed"
    assert renewed.renewed_identity is not None
    assert renewed.renewed_identity.lease_expires_at not in {
        stale_lease.lease_expires_at,
        identity.lease_expires_at,
    }
