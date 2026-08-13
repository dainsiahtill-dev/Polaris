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






def test_get_task_returns_fact_overlaid_status_for_external_task_id(tmp_path: Path) -> None:
    """``get_task(external_task_id)`` must surface the latest
    ``task_runtime.execution`` fact overlay for an external token such as
    ``TASK-EXT`` when the payload's ``task_row_snapshot`` preserves the
    external id in metadata.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    external_id = "TASK-EXT"
    created = service.create_task_row(
        subject="get_task external overlay",
        description="external-task-id lookup must also overlay facts",
        metadata={"external_task_id": external_id, "source_task_id": external_id},
    )
    created_id = int(created["id"])

    # Sanity: the external-id lookup hits the same row before any fact is
    # appended.
    initial_lookup = service.get_task(external_id)
    assert isinstance(initial_lookup, dict)
    assert initial_lookup["id"] == created_id
    assert initial_lookup["status"] == "pending"

    # Append a terminal execution fact for the SAME numeric task whose
    # ``task_row_snapshot`` preserves the external id in metadata. The fact's
    # ``task_id`` is the numeric file-row id so the observable read model
    # merges the fact onto the file row; the external id stays discoverable
    # through the snapshot metadata.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
            source="runtime.task_runtime",
            task_id=str(created_id),
            run_id="run-fact-completed-external",
            payload={
                "task_id": str(created_id),
                "run_id": "run-fact-completed-external",
                "event_type": "completed",
                "status": "completed",
                "execution_state": "completed",
                "task_row_snapshot": {
                    "id": created_id,
                    "task_id": str(created_id),
                    "subject": "external overlay row",
                    "description": "fact snapshot preserves external id",
                    "metadata": {
                        "external_task_id": external_id,
                        "source_task_id": external_id,
                        "source": "task_runtime.row_snapshot",
                    },
                },
            },
        )
    )

    # Raw file row on disk remains pending.
    on_disk_after = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk_after["status"] == "pending"

    # ``get_task(external_id)`` must surface the fact-overlaid status, not the
    # stale pending file row, while keeping the external id discoverable.
    overlaid = service.get_task(external_id)
    assert isinstance(overlaid, dict)
    assert overlaid["status"] == "completed", (
        "get_task must surface the latest task_runtime.execution fact status "
        f"for external id {external_id!r}; got status={overlaid.get('status')!r}"
    )
    # The fact snapshot is authoritative; its normalized numeric row id and
    # external-id metadata make the row discoverable without merging file state.
    assert overlaid["id"] == created_id
    assert str(overlaid["metadata"].get("external_task_id") or "") == external_id
    assert str(overlaid["metadata"].get("source_task_id") or "") == external_id
    assert overlaid["metadata"].get("source") == "task_runtime.execution_fact"
    assert "previous_status" not in overlaid["metadata"]


# ---------------------------------------------------------------------------
# task_exists observable fact-overlay regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.task_exists`` is the public existence probe consumed by
# role adapters (e.g. ``_board_task_exists`` / ``_update_board_task``). It MUST
# consult the observable read model — i.e. the file row overlaid with the
# latest ``task_runtime.execution`` fact — instead of probing ``self._board``
# alone. Otherwise a task whose existence is only attested by the execution
# ledger (fact-only projection) is silently invisible to roles adapters that
# use ``task_exists`` as a precondition for writes, defeating the read-model
# convergence that ``get_task`` / ``list_observable_task_rows`` already pin.


def test_task_exists_returns_true_for_fact_only_numeric_task_id(tmp_path: Path) -> None:
    """``task_exists(numeric_id)`` must return ``True`` when a
    ``task_runtime.execution`` fact attests to that id, even when no file row
    has ever been created.

    Regression: ``task_exists`` used to consult ``self._board.get(normalized)``
    only, so any fact-only task was reported as ``False`` — making role
    adapters bypass the existence check, write a duplicate file row, and break
    the fact-only read-model convergence. The probe must consult the same
    observable read model ``get_task`` uses, so any task visible to
    ``list_observable_task_rows`` is also visible to ``task_exists``.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    fact_only_id = 4242

    # Sanity: no file row exists for the chosen id and the raw board probe
    # agrees the task is absent.
    assert not _task_file_path(workspace, fact_only_id).exists()
    assert service.task_exists(fact_only_id) is False, (
        "sanity precondition failed: a brand-new workspace must not contain an arbitrary numeric task id"
    )

    # Append a task_runtime.execution fact WITHOUT creating a file row. The
    # payload's ``task_row_snapshot.id`` is the same numeric id, so the
    # observable read model can resolve the task purely from the ledger.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(fact_only_id),
            run_id="run-fact-only-existence",
            payload={
                "task_id": str(fact_only_id),
                "run_id": "run-fact-only-existence",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact-only-existence",
                "task_row_snapshot": {
                    "id": fact_only_id,
                    "task_id": str(fact_only_id),
                    "subject": "fact-only existence probe",
                    "description": "task_exists must see this row via the fact stream",
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    # Observable rows confirm the fact-only projection: this is the same
    # read model ``task_exists`` must now consult.
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert fact_only_id in observable, (
        f"observable rows must surface the fact-only task; got ids={sorted(observable.keys())!r}"
    )
    assert observable[fact_only_id]["status"] == "in_progress"

    # File row is still absent on disk — this is a fact-only existence.
    assert not _task_file_path(workspace, fact_only_id).exists(), (
        "test invariant: the file row must not be created; task_exists must consult the observable read model"
    )

    # The contract: ``task_exists`` must report the fact-only task as
    # existing. This is the regression the production code must satisfy.
    assert service.task_exists(fact_only_id) is True, (
        "task_exists must consult the observable read model so a fact-only "
        "task_runtime.execution projection is reported as existing; got "
        f"task_exists({fact_only_id}) == False"
    )

    # The same verdict must hold for the canonical ``task-<id>`` token used by
    # role adapters when they normalize ids through ``normalize_task_id``.
    assert service.task_exists(f"task-{fact_only_id}") is True, (
        "task_exists must accept the canonical task-<id> token and consult the observable read model"
    )


def test_task_exists_keeps_true_when_observable_overlays_terminal_fact(tmp_path: Path) -> None:
    """``task_exists`` must stay ``True`` for an existing file row once a
    newer ``task_runtime.execution`` fact overlays it, mirroring the
    ``get_task`` fact-overlay contract.

    Regression: role adapters call ``task_exists`` before writes; if the
    probe stops consulting the observable model, an externally-attached
    terminal fact that does NOT mutate the file row would let the probe drift
    away from what ``get_task`` reports. The probe must keep reporting
    existence for any task the observable model still surfaces, regardless of
    the fact-overlaid status (pending, in_progress, terminal — all still
    surface the id).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    created = service.create_task_row(
        subject="task_exists overlay",
        description="file row stays pending; fact overlays status",
    )
    created_id = int(created["id"])

    # Baseline: the file row exists and the probe agrees.
    assert service.task_exists(created_id) is True

    # Append a terminal fact WITHOUT going through the service APIs.
    _append_terminal_fact_event(
        workspace,
        task_id=str(created_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-task-exists",
    )

    # File row stays pending on disk: the fact overlay is read-only.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"

    # Observable model surfaces the file row id with the fact-overlaid
    # status; the task is still present.
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[created_id]["status"] == "completed"

    # ``task_exists`` must keep returning ``True`` for the task id because
    # the observable model still surfaces it. Failing here means the probe
    # falls back to raw board state and ignores the fact overlay.
    assert service.task_exists(created_id) is True, (
        "task_exists must remain True for any task still surfaced by the "
        "observable read model; the fact overlay must not make the probe "
        "report the task as absent"
    )


def test_task_exists_returns_false_for_unknown_task_id_when_facts_present(tmp_path: Path) -> None:
    """``task_exists`` must keep returning ``False`` for ids that the
    observable read model never surfaces — even when the fact stream has
    facts for OTHER tasks.

    This pins the negative side of the contract: the probe must not become a
    blanket ``True`` once any fact exists in the stream. Only ids that the
    observable read model actually projects get reported as existing.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    fact_only_id = 9001

    # Pre-condition: the fact stream is empty for this id; probe must
    # already report False.
    assert service.task_exists(fact_only_id) is False

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(fact_only_id),
            run_id="run-fact-only-existence-negative",
            payload={
                "task_id": str(fact_only_id),
                "run_id": "run-fact-only-existence-negative",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact-only-existence-negative",
                "task_row_snapshot": {
                    "id": fact_only_id,
                    "task_id": str(fact_only_id),
                    "subject": "fact-only existence (negative side)",
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    # The fact-only id is now projectable: the probe returns True.
    assert service.task_exists(fact_only_id) is True

    # An unrelated id remains unknown: the probe still returns False. This
    # pins the negative side of the contract — the probe must scope the
    # fact lookup to the queried id, not the whole stream.
    other_id = 9999
    assert other_id != fact_only_id
    assert service.task_exists(other_id) is False, (
        "task_exists must keep returning False for ids the observable model "
        "does not surface; a fact-only presence must not make the probe "
        "report unrelated ids as existing"
    )


# ---------------------------------------------------------------------------
# _fact_overlaid_dependency_status_rows observable projection regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService._fact_overlaid_dependency_status_rows`` is the private
# dependency-status read model consumed by dependency refresh and blocker
# checks. It must share the pure observable projection helper used by the
# public read model, while still avoiding the public ``list_observable_task_rows``
# API so dependency mutation paths do not depend on an external read endpoint.


def test_dependency_status_read_model_rows_delegates_to_transitional_read_model_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    sentinel_rows: list[dict[str, Any]] = [
        {
            "id": 71,
            "task_id": "sentinel-dependency",
            "subject": "dependency helper sentinel",
            "status": "completed",
            "metadata": {"source": "transitional_helper"},
        }
    ]
    helper_calls: list[str] = []

    def transitional_task_row_read_model_rows() -> list[dict[str, Any]]:
        helper_calls.append("_transitional_task_row_read_model_rows")
        return sentinel_rows

    def reject_file_rows(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("_dependency_status_read_model_rows must delegate file loading to the transitional helper")

    def reject_fact_rows(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("_dependency_status_read_model_rows must not load execution facts directly")

    def reject_projection(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("_dependency_status_read_model_rows must not project rows outside the transitional helper")

    monkeypatch.setattr(
        service,
        "_transitional_task_row_read_model_rows",
        transitional_task_row_read_model_rows,
        raising=False,
    )
    monkeypatch.setattr(service, "_list_file_task_rows", reject_file_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", reject_fact_rows)
    monkeypatch.setattr(service, "_project_observable_task_rows", reject_projection)

    rows = service._dependency_status_read_model_rows()

    assert rows is sentinel_rows
    assert helper_calls == ["_transitional_task_row_read_model_rows"]


def test_fact_overlaid_dependency_status_rows_reuses_observable_projection_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    file_rows: list[dict[str, Any]] = [
        {
            "id": 41,
            "task_id": "41",
            "subject": "stale file-backed dependency",
            "status": "pending",
            "metadata": {"source": "file_row"},
        }
    ]
    fact_rows: list[dict[str, Any]] = [
        {
            "id": 41,
            "task_id": "41",
            "subject": "completed fact-backed dependency",
            "status": "completed",
            "metadata": {"source": "task_runtime.execution_fact"},
        }
    ]
    project_calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    public_read_calls: list[str] = []
    original_projection = service._project_observable_task_rows

    def list_file_task_rows(*, include_terminal: bool = True) -> list[dict[str, Any]]:
        assert include_terminal is True
        return [dict(row) for row in file_rows]

    def list_fact_rows() -> list[dict[str, Any]]:
        return [dict(row) for row in fact_rows]

    def reject_public_observable_rows() -> NoReturn:
        public_read_calls.append("list_observable_task_rows")
        raise AssertionError("_fact_overlaid_dependency_status_rows must not call the public observable read API")

    def project_observable_task_rows(
        projected_file_rows: list[dict[str, Any]],
        projected_fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        project_calls.append(
            (
                [dict(row) for row in projected_file_rows],
                [dict(row) for row in projected_fact_rows],
            )
        )
        return original_projection(projected_file_rows, projected_fact_rows)

    monkeypatch.setattr(service, "_list_file_task_rows", list_file_task_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", list_fact_rows)
    monkeypatch.setattr(service, "list_observable_task_rows", reject_public_observable_rows)
    monkeypatch.setattr(service, "_project_observable_task_rows", project_observable_task_rows)

    status_by_id = service._fact_overlaid_dependency_status_rows()

    assert status_by_id == {41: service_module.TaskStatus.COMPLETED}
    assert project_calls == [(file_rows, fact_rows)]
    assert public_read_calls == []


def test_fact_overlaid_dependency_status_rows_delegates_to_dependency_status_read_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)
    read_model_calls: list[str] = []

    def dependency_status_read_model_rows() -> list[dict[str, Any]]:
        read_model_calls.append("_dependency_status_read_model_rows")
        return [
            {"id": 51, "status": "completed"},
            {"id": "52", "status": "pending"},
            {"id": 53, "status": "unknown-status"},
            {"status": "completed"},
            {"id": "not-an-int", "status": "completed"},
        ]

    def reject_file_rows(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("_fact_overlaid_dependency_status_rows must delegate row loading to read-model helper")

    def reject_fact_rows(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("_fact_overlaid_dependency_status_rows must not load execution facts directly")

    monkeypatch.setattr(service, "_dependency_status_read_model_rows", dependency_status_read_model_rows)
    monkeypatch.setattr(service, "_list_file_task_rows", reject_file_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", reject_fact_rows)

    status_by_id = service._fact_overlaid_dependency_status_rows()

    assert read_model_calls == ["_dependency_status_read_model_rows"]
    assert status_by_id == {
        51: service_module.TaskStatus.COMPLETED,
        52: service_module.TaskStatus.PENDING,
    }


# ---------------------------------------------------------------------------
# _task_has_unresolved_dependencies observable fact-overlay regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService._task_has_unresolved_dependencies`` is the per-task
# blocker probe consulted by the claim path (``claim_execution`` ->
# ``task_blocked`` rejection). It MUST consult the same fact-overlay-aware
# status projection that ``refresh_dependency_unblocks`` /
# ``_fact_overlaid_dependency_status_rows`` expose, instead of reading the raw
# ``self._board.get(dep_id).status`` alone. Without the overlay, a child
# blocked against a parent whose authoritative completion lives only in the
# ``task_runtime.execution`` fact stream stays ``task_blocked`` even after
# ``refresh_dependency_unblocks`` has unblocked the file row, defeating the
# read-model convergence the rest of the selection/claim paths now rely on.


def test_task_has_unresolved_dependencies_uses_fact_overlay_for_completed_parent(
    tmp_path: Path,
) -> None:
    """``_task_has_unresolved_dependencies`` must treat a parent dependency as
    resolved when the latest ``task_runtime.execution`` fact overlays it as
    ``completed``, even when the raw file-backed parent row stays pending.

    Test setup:
      * Parent file row stays ``status=pending`` (no claim / no completion via
        the service APIs).
      * Child file row stays ``status=blocked`` with ``blocked_by=[parent_id]``
        (no ``refresh_dependency_unblocks`` is called — we want the helper to
        see the stale file row alone).
      * A newer ``task_runtime.execution`` ``completed`` fact is appended for
        the parent, so the overlay projects the parent as ``completed``.
    Expected:
      * ``_task_has_unresolved_dependencies(child_task)`` returns ``False``
        because the overlay is authoritative; the raw ``self._board.get``
        path would still see the stale pending parent and incorrectly return
        ``True``.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    parent = service.create_task_row(subject="fact-overlay completed parent")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="child blocked on fact-completed parent",
        blocked_by=[parent["id"]],
    )
    child_id = int(child["id"])

    # Sanity: raw file rows are pending/blocked and no terminal fact exists
    # yet. The board cache must still report the raw (pre-refresh) state.
    raw_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    raw_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert raw_parent["status"] == "pending"
    assert raw_child["status"] == "blocked"
    assert raw_child["blocked_by"] == [parent_id]

    cached_child = service._board.get(child_id)
    assert cached_child is not None
    assert list(cached_child.blocked_by) == [parent_id]
    cached_parent_before = service._board.get(parent_id)
    assert cached_parent_before is not None
    assert cached_parent_before.status.value == "pending", (
        "test invariant: parent cache must still be pending before the fact is appended"
    )

    # Pre-overlay: helper must keep returning True because the raw dependency
    # row is still pending. This pins the baseline the regression is measured
    # against.
    assert service._task_has_unresolved_dependencies(cached_child) is True, (
        "with no execution fact overlay the helper must still see the raw pending parent as an unresolved blocker"
    )

    # Append a terminal execution fact for the parent WITHOUT going through
    # the service APIs. The file row stays stale/pending on disk and the
    # in-memory board cache is also untouched.
    _append_terminal_fact_event(
        workspace,
        task_id=str(parent_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-dep-helper",
    )

    # File row on disk is still pending; observable model is completed.
    on_disk_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    assert on_disk_parent["status"] == "pending"
    observable_parent = service._fact_overlaid_dependency_status_rows()
    assert observable_parent[parent_id].value == "completed", (
        f"fact-overlay projection must report parent as completed; got {observable_parent[parent_id]!r}"
    )

    # The board cache of the parent is still pending — the helper must NOT
    # consult ``self._board.get(parent).status`` alone, or it would still
    # return True here.
    cached_parent_after = service._board.get(parent_id)
    assert cached_parent_after is not None
    assert cached_parent_after.status.value == "pending", (
        "test invariant: parent board cache must remain pending; the helper must "
        "rely on the fact-overlay projection, not on raw board state"
    )

    # The same cached child instance must now resolve to False because the
    # overlay projects the parent as completed.
    resolved_child = service._board.get(child_id)
    assert resolved_child is not None
    has_unresolved = service._task_has_unresolved_dependencies(resolved_child)
    assert has_unresolved is False, (
        "_task_has_unresolved_dependencies must consult the fact-overlay "
        "projection so a fact-only-completed parent unblocks the child even "
        "when the raw TaskBoard cache still reports the parent as pending"
    )


def test_task_has_unresolved_dependencies_returns_true_when_dependency_missing(
    tmp_path: Path,
) -> None:
    """``_task_has_unresolved_dependencies`` must return ``True`` when a child
    lists a dependency that the fact-overlay projection never surfaces — i.e.
    the dependency is missing from the read model.

    This pins the negative side of the contract: the helper must NOT silently
    treat a missing overlay entry as resolved; it must keep flagging the
    blocker so the claim path continues to reject the row.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    missing_parent_id = 7777
    created = service.create_task_row(
        subject="child blocked against missing parent",
        blocked_by=[missing_parent_id],
    )
    child_id = int(created["id"])

    cached_child = service._board.get(child_id)
    assert cached_child is not None
    assert list(cached_child.blocked_by) == [missing_parent_id]

    # Sanity: the fact-overlay projection never surfaces the missing parent.
    overlay = service._fact_overlaid_dependency_status_rows()
    assert missing_parent_id not in overlay, (
        "test invariant: a missing-parent dependency must not appear in the overlay projection"
    )

    # The helper MUST still report the dependency as unresolved.
    assert service._task_has_unresolved_dependencies(cached_child) is True, (
        "_task_has_unresolved_dependencies must keep returning True when the "
        "dependency is absent from the overlay projection; a missing entry "
        "must not be silently treated as resolved"
    )


def test_task_has_unresolved_dependencies_returns_true_when_overlay_status_not_completed(
    tmp_path: Path,
) -> None:
    """``_task_has_unresolved_dependencies`` must return ``True`` when the
    fact-overlay projection shows a non-``completed`` status for the parent
    (e.g. ``in_progress`` or another intermediate state) — only the
    ``completed`` verdict should clear the blocker.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = _create_bootstrapped_task_runtime_service(workspace)

    parent = service.create_task_row(subject="non-completed overlay parent")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="child blocked on in_progress parent",
        blocked_by=[parent["id"]],
    )
    child_id = int(child["id"])

    # Append an intermediate (non-terminal, non-completed) execution fact for
    # the parent. The overlay must project the parent as ``in_progress``,
    # not ``completed``, so the dependency must remain unresolved.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(parent_id),
            run_id="run-fact-in-progress-dep-helper",
            payload={
                "task_id": str(parent_id),
                "run_id": "run-fact-in-progress-dep-helper",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact-in-progress-dep-helper",
                "task_row_snapshot": {
                    "id": parent_id,
                    "task_id": str(parent_id),
                    "subject": parent["subject"],
                    "description": parent.get("description", ""),
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    # Sanity: the overlay projects the parent as in_progress (not completed).
    overlay = service._fact_overlaid_dependency_status_rows()
    assert overlay[parent_id].value == "in_progress", (
        f"overlay must project in_progress for a non-terminal fact; got {overlay[parent_id]!r}"
    )

    cached_child = service._board.get(child_id)
    assert cached_child is not None

    # The helper must still report the dependency as unresolved because the
    # overlay status is not ``completed``.
    assert service._task_has_unresolved_dependencies(cached_child) is True, (
        "_task_has_unresolved_dependencies must keep returning True when the "
        "fact-overlay status for the parent is anything other than 'completed'; "
        "only a completed overlay status clears the blocker"
    )


@pytest.mark.parametrize(
    ("task_role", "terminal_outcome"),
    (("owner", "completed"), ("requester", "failed")),
)
def test_prepare_owner_rework_execution_reopens_terminal_row_and_rotates_session(
    tmp_path: Path,
    task_role: str,
    terminal_outcome: str,
) -> None:
    """Both owner and requester claims reopen only through TaskRuntime."""

    workspace = tmp_path / task_role
    workspace.mkdir()
    service = _create_bootstrapped_task_runtime_service(workspace)
    task_id = "owner-task" if task_role == "owner" else "requester-task"
    created = service.ensure_task_row(external_task_id=task_id, subject=f"{task_role} task")
    runtime_task_id = int(created["id"])
    claimed = service.claim_execution(
        runtime_task_id,
        worker_id="prior-director",
        role_id="director",
        external_task_id=task_id,
    )
    assert claimed["success"] is True
    if terminal_outcome == "completed":
        terminal = _settle_claimed_execution_attempt(service, claimed, outcome="completed", summary="")
    else:
        terminal = _settle_claimed_execution_attempt(
            service, claimed, outcome="failed", summary="owner rework required"
        )
    assert terminal["success"] is True

    prepared = service.prepare_owner_rework_execution(_owner_rework_prepare_command(workspace, task_role=task_role))

    assert prepared.ok is True
    assert prepared.reopened is True
    assert prepared.runtime_task_id == str(runtime_task_id)
    persisted_session = service._read_session(runtime_task_id)
    assert persisted_session is not None
    assert persisted_session.status == "suspended"
    assert prepared.execution_event["event_type"] == "owner_rework_execution_prepared"
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="owner_rework_execution_prepared",
        )
    ).events
    assert len(facts) == 1
    assert facts[0]["payload"]["details"]["task_role"] == task_role

    retried = service.claim_execution(
        runtime_task_id,
        worker_id="director-owner-rework",
        role_id="director",
        external_task_id=task_id,
    )
    assert retried["success"] is True
    assert retried["reason"] == "claimed"


def test_prepare_owner_rework_execution_is_idempotent_for_nonterminal_handoff(tmp_path: Path) -> None:
    """A repeated matching authorization must not append a second prepare fact."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _create_bootstrapped_task_runtime_service(workspace)
    service.ensure_task_row(external_task_id="owner-task", subject="owner task")
    command = _owner_rework_prepare_command(workspace, task_role="owner")

    first = service.prepare_owner_rework_execution(command)
    second = service.prepare_owner_rework_execution(command)

    assert first.ok is True
    assert first.idempotent is False
    assert second.ok is True
    assert second.idempotent is True
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="owner_rework_execution_prepared",
        )
    ).events
    assert len(facts) == 1


def test_prepare_owner_rework_execution_rejects_conflicting_handoff(tmp_path: Path) -> None:
    """A different handoff cannot reuse a non-terminal prepared runtime row."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _create_bootstrapped_task_runtime_service(workspace)
    service.ensure_task_row(external_task_id="owner-task", subject="owner task")

    assert (
        service.prepare_owner_rework_execution(_owner_rework_prepare_command(workspace, task_role="owner")).ok is True
    )
    conflict = service.prepare_owner_rework_execution(
        _owner_rework_prepare_command(
            workspace,
            task_role="owner",
            handoff_id="owner-rework-handoff-conflict",
        )
    )

    assert conflict.ok is False
    assert conflict.code == "owner_rework_authorization_conflict"


def test_prepare_owner_rework_execution_rejects_malformed_handoff_and_missing_row(
    tmp_path: Path,
) -> None:
    """Malformed authority and an absent local execution row both fail closed."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _create_bootstrapped_task_runtime_service(workspace)
    malformed = _owner_rework_prepare_command(workspace, task_role="owner")
    object.__setattr__(malformed.authorization, "handoff", object())

    malformed_result = service.prepare_owner_rework_execution(malformed)
    missing_row_result = service.prepare_owner_rework_execution(
        _owner_rework_prepare_command(workspace, task_role="requester")
    )

    assert malformed_result.ok is False
    assert malformed_result.code == "owner_rework_authorization_malformed"
    assert missing_row_result.ok is False
    assert missing_row_result.code == "runtime_task_not_found"


def test_prepare_same_task_local_rework_reopens_exact_factory_owner_and_reclaims(
    tmp_path: Path,
) -> None:
    """A run-bound QA receipt reopens only its PM owner and preserves diagnostics."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _create_bootstrapped_task_runtime_service(workspace)
    owner = service.ensure_task_row(external_task_id="TASK-1", subject="source owner")
    unrelated = service.ensure_task_row(external_task_id="TASK-2", subject="unrelated task")
    owner_id = int(owner["id"])
    unrelated_id = int(unrelated["id"])
    unrelated_before_status = str(unrelated.get("status") or "")
    assert service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id=str(owner_id),
            factory_run_id="factory-current",
        )
    ).ok is True
    assert service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id=str(unrelated_id),
            factory_run_id="factory-current",
        )
    ).ok is True
    claimed = service.claim_execution(
        owner_id,
        worker_id="director-initial",
        role_id="director",
        run_id="director-initial",
        external_task_id="TASK-1",
    )
    assert claimed["success"] is True
    assert _settle_claimed_execution_attempt(service, claimed, outcome="completed", summary="done")["success"]

    diagnostic = {
        "diagnostic_id": "diagnostic-1",
        "obligation_id": "obligation-1",
        "owner_task_id": "TASK-1",
        "affected_target": "src/main.ts",
        "allowed_next_action": "run_deterministic_repair",
        "required_verifier_ids": ["npm.run.build"],
    }
    command = PrepareSameTaskLocalReworkCommandV1(
        schema_version=SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1,
        workspace=str(workspace),
        factory_run_id="factory-current",
        external_task_id="TASK-1",
        completion_contract_hash="a" * 64,
        action_id="b" * 64,
        diagnostic_id="diagnostic-1",
        obligation_id="obligation-1",
        action_kind="run_deterministic_repair",
        owner_snapshot_hash="c" * 64,
        owner_bundle_hash="d" * 64,
        dispatch_claim={
            "identity": {
                "workspace": str(workspace.resolve()),
                "run_id": "factory-current",
                "completion_contract_hash": "a" * 64,
            },
            "action_id": "b" * 64,
            "claim_id": "e" * 64,
            "attempt_ordinal": 1,
        },
        diagnostic=diagnostic,
    )

    prepared = service.prepare_same_task_local_rework(command)
    repeated = service.prepare_same_task_local_rework(command)

    assert prepared.ok is True
    assert prepared.code == "same_task_local_rework_prepared"
    assert prepared.reopened is True
    assert prepared.runtime_task_id == str(owner_id)
    assert repeated.ok is True
    assert repeated.idempotent is True
    owner_row = service.get_task("TASK-1")
    unrelated_row = service.get_task("TASK-2")
    assert owner_row is not None
    assert owner_row["metadata"]["last_failure"] == diagnostic
    assert unrelated_row is not None
    assert unrelated_row["status"] == unrelated_before_status
    retried = service.claim_execution(
        owner_id,
        worker_id="director-local-rework",
        role_id="director",
        run_id="director-local-rework",
        external_task_id="TASK-1",
    )
    assert retried["success"] is True
    assert retried["task"]["metadata"]["last_failure"] == diagnostic


def test_prepare_same_task_local_rework_rejects_cross_run_claim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _create_bootstrapped_task_runtime_service(workspace)
    created = service.ensure_task_row(external_task_id="TASK-1", subject="owner")
    assert service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id=str(created["id"]),
            factory_run_id="factory-current",
        )
    ).ok is True
    result = service.prepare_same_task_local_rework(
        PrepareSameTaskLocalReworkCommandV1(
            schema_version=SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1,
            workspace=str(workspace),
            factory_run_id="factory-current",
            external_task_id="TASK-1",
            completion_contract_hash="1" * 64,
            action_id="2" * 64,
            diagnostic_id="diagnostic-1",
            obligation_id="obligation-1",
            action_kind="run_required_verifier",
            owner_snapshot_hash="3" * 64,
            owner_bundle_hash="4" * 64,
            dispatch_claim={
                "identity": {
                    "workspace": str(workspace.resolve()),
                    "run_id": "factory-old",
                    "completion_contract_hash": "1" * 64,
                },
                "action_id": "2" * 64,
                "claim_id": "5" * 64,
                "attempt_ordinal": 1,
            },
            diagnostic={
                "diagnostic_id": "diagnostic-1",
                "obligation_id": "obligation-1",
                "owner_task_id": "TASK-1",
                "affected_target": "tests/product.test.ts",
                "allowed_next_action": "run_required_verifier",
            },
        )
    )

    assert result.ok is False
    assert result.code == "same_task_local_rework_receipt_mismatch"


def test_claim_execution_threaded_contenders_have_one_session_winner(tmp_path: Path) -> None:
    """Independent services must serialize one task claim through the file lock."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seed_service = _create_bootstrapped_task_runtime_service(workspace)
    created = seed_service.create_task_row(subject="threaded claim authority")
    task_id = int(created["id"])
    services = (
        _create_bootstrapped_task_runtime_service(workspace),
        _create_bootstrapped_task_runtime_service(workspace),
    )
    start_barrier = threading.Barrier(2)

    def claim(service: TaskRuntimeService, worker_id: str) -> dict[str, Any]:
        start_barrier.wait(timeout=10)
        return service.claim_execution(
            task_id,
            worker_id=worker_id,
            role_id="director",
            run_id="threaded-claim-run",
            selection_source="threaded-claim-regression",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(claim, services[0], "thread-worker-a"),
            executor.submit(claim, services[1], "thread-worker-b"),
        )
        results = [future.result(timeout=15) for future in futures]

    winners = [result for result in results if result["success"] is True]
    losers = [result for result in results if result["success"] is False]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0]["reason"] == "lease_conflict"
    assert winners[0]["session"]["session_id"] == losers[0]["session"]["session_id"]
    assert winners[0]["execution_attempt"]["session_id"] == winners[0]["session"]["session_id"]


