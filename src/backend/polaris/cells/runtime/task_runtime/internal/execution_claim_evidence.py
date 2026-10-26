"""TaskRuntime's optimistic, durable claim-evidence observation boundary."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.execution_claim_evidence import (
    ExecutionClaimEvidenceError,
    ExecutionClaimEvidenceV1,
    QueryExecutionClaimEvidenceV1,
)

from .execution_effect_closure import _read_owner_events
from .service import TaskRuntimeService
from .task_board import TaskBoardFileLockTimeoutError


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _same_attempt(runtime: Any, identity: TaskRuntimeExecutionAttemptIdentityV1) -> bool:
    return isinstance(runtime, Mapping) and all(
        type(runtime.get(key)) is type(getattr(identity, key)) and runtime.get(key) == getattr(identity, key)
        for key in ("task_id", "external_task_id", "session_id", "attempt", "run_id", "role_id", "worker_id")
    )


@dataclass(frozen=True)
class _DurableState:
    status: Literal["active", "completed"]
    task_json: str
    session_json: str


def _durable_state(service: TaskRuntimeService, query: QueryExecutionClaimEvidenceV1, deadline: float) -> _DurableState:
    """Read session/row under existing locks; never query FactStream while held."""
    identity = query.identity
    lock = service._get_session_lock(identity.task_id)
    if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise ExecutionClaimEvidenceError("file_lock_timeout")
    try:
        remaining = deadline - time.monotonic()
        if remaining < 0:
            raise ExecutionClaimEvidenceError("file_lock_timeout")
        with (
            service._board._file_lock(service._session_file_lock_path(identity.task_id), timeout_seconds=remaining),
            service._board.transaction(),
        ):
            validation = service._validate_execution_attempt_locked(identity, raise_infrastructure_errors=True)
            session = service._read_session_locked(identity.task_id, raise_infrastructure_errors=True)
            # Validation checks every stable attempt field before status.
            # Receipt provenance grants no settlement authority: the terminal
            # row's lease timestamp is not the original active lease snapshot.
            if not validation.valid and not (
                query.purpose == "receipt_revalidation"
                and validation.code == "session_not_active"
                and session is not None
                and session.status == "completed"
            ):
                raise ExecutionClaimEvidenceError(validation.code)
            if session is None:
                raise ExecutionClaimEvidenceError("session_not_found")
            task = service._board.get_persisted(identity.task_id)
            if task is None:
                raise ExecutionClaimEvidenceError("current_task_missing")
            task_row = task.to_dict()
            expected_status = "in_progress" if session.status == "active" else "completed"
            if task_row["status"] != expected_status:
                raise ExecutionClaimEvidenceError("current_task_status_mismatch")
            if not _same_attempt(task_row["metadata"].get("runtime_execution"), identity):
                raise ExecutionClaimEvidenceError("current_task_identity_mismatch")
            status: Literal["active", "completed"] = "active" if session.status == "active" else "completed"
            return _DurableState(status, _json(task_row), _json(session.to_dict()))
    except TaskBoardFileLockTimeoutError as exc:
        raise ExecutionClaimEvidenceError("file_lock_timeout") from exc
    finally:
        lock.release()


def _task_events(
    events: tuple[Mapping[str, Any], ...], query: QueryExecutionClaimEvidenceV1
) -> tuple[Mapping[str, Any], ...]:
    selected = []
    task_id = str(query.identity.task_id)
    for event in events:
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        row = payload.get("task_row_snapshot")
        row = row if isinstance(row, Mapping) else {}
        if task_id in {str(event.get("aggregate_id")), str(payload.get("task_id")), str(row.get("id"))}:
            if (
                event.get("source") != "runtime.task_runtime"
                or payload.get("schema_version") != TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1
                or payload.get("workspace") != query.workspace
                or str(event.get("aggregate_id")) != task_id
                or payload.get("task_id") != task_id
                or type(row.get("id")) is not int
                or row.get("id") != query.identity.task_id
                or not event.get("event_id")
                or type(event.get("seq")) is not int
            ):
                raise ExecutionClaimEvidenceError("claim_fact_identity_invalid")
            selected.append(event)
    return tuple(selected)


def _original_claim(events: tuple[Mapping[str, Any], ...], query: QueryExecutionClaimEvidenceV1) -> Mapping[str, Any]:
    identity = query.identity
    matches = []
    for event in events:
        payload = event["payload"]
        if event.get("event_type") != "claimed" or payload.get("session_id") != identity.session_id:
            continue
        row = payload["task_row_snapshot"]
        metadata = row.get("metadata")
        runtime = metadata.get("runtime_execution") if isinstance(metadata, Mapping) else None
        if (
            not _same_attempt(runtime, identity)
            or payload.get("run_id") != identity.run_id
            or type(payload.get("attempt")) is not int
            or payload.get("attempt") != identity.attempt
            or row.get("workflow_run_id") != identity.run_id
        ):
            raise ExecutionClaimEvidenceError("original_claim_identity_mismatch")
        matches.append(event)
    if not matches:
        raise ExecutionClaimEvidenceError("original_claim_missing")
    if len(matches) != 1:
        raise ExecutionClaimEvidenceError("original_claim_ambiguous")
    return matches[0]


def query_execution_claim_evidence(query: QueryExecutionClaimEvidenceV1) -> ExecutionClaimEvidenceV1:
    """Prove one original claim and a stable current durable observation.

    Fact history is captured outside session locks. Two persisted snapshots and
    two strict history cuts detect relevant concurrent changes, retaining the
    existing settlement lock order. Unrelated task events do not change proof.
    Callers re-query this evidence at their own receipt decision frontier.
    """
    if type(query) is not QueryExecutionClaimEvidenceV1:
        raise TypeError("query must be QueryExecutionClaimEvidenceV1")
    if query.identity.workspace != query.workspace:
        raise ExecutionClaimEvidenceError("workspace_mismatch")
    # Historical read-only services deliberately refuse lock-file authority.
    # This live-owner query uses the same cooperative lock primitive as public
    # validation, after a read-only existence check prevents namespace creation.
    observation = TaskRuntimeService(query.workspace, read_only=True)
    if not observation._kernel_fs.exists(observation._session_logical_path(query.identity.task_id)):
        raise ExecutionClaimEvidenceError("session_not_found")
    service = TaskRuntimeService(query.workspace)
    deadline = time.monotonic() + query.lock_timeout_seconds
    before_events = _task_events(_read_owner_events(query.workspace), query)
    before_state = _durable_state(service, query, deadline)
    claim = _original_claim(before_events, query)
    after_events = _task_events(_read_owner_events(query.workspace), query)
    after_state = _durable_state(service, query, deadline)
    if before_events != after_events or before_state != after_state:
        raise ExecutionClaimEvidenceError("claim_evidence_drift")
    current = after_events[-1]
    current_row = current["payload"]["task_row_snapshot"]
    current_metadata = current_row.get("metadata")
    runtime = current_metadata.get("runtime_execution") if isinstance(current_metadata, Mapping) else None
    if not _same_attempt(runtime, query.identity) or current_row.get("status") != (
        "in_progress" if after_state.status == "active" else "completed"
    ):
        raise ExecutionClaimEvidenceError("current_fact_state_mismatch")
    claim_payload = claim["payload"]
    task_row = json.loads(after_state.task_json)
    return ExecutionClaimEvidenceV1(
        identity=query.identity,
        purpose=query.purpose,
        status=after_state.status,
        acceptance_basis="completed_receipt_source"
        if after_state.status == "completed"
        else "active_unexpired_attempt",
        claim_event_id=str(claim["event_id"]),
        claim_event_seq=claim["seq"],
        current_event_id=str(current["event_id"]),
        current_event_seq=current["seq"],
        task_content_hash=_hash(task_row),
        session_content_hash=hashlib.sha256(after_state.session_json.encode("utf-8")).hexdigest(),
        claim_metadata_json=_json(claim_payload["task_row_snapshot"]["metadata"]),
        current_metadata_json=_json(task_row["metadata"]),
        factory_run_id=str(claim_payload.get("factory_run_id") or ""),
        project_id=str(claim_payload.get("project_id") or claim_payload.get("factory_bench_project_id") or ""),
    )
