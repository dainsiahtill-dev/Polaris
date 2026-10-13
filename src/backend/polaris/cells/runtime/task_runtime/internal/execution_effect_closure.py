"""Terminal observation reuses the TaskRuntime strict DEO reducers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from polaris.cells.events.fact_stream.public import FactStreamError, QueryFactEventsV1, query_fact_events
from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
    _registry_stream_token,
)
from polaris.cells.runtime.task_runtime.internal.execution_session import TaskExecutionSession
from polaris.cells.runtime.task_runtime.internal.terminal_intent import validate_fulfilled_terminal_intent
from polaris.cells.runtime.task_runtime.public.contracts import (
    DirectedEffectOperationResultV1,
    DirectedEffectParentRegistryIdentityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    ExecutionEffectClosureObservationV1,
    ExecutionEffectCommitObservationV1,
    QueryExecutionEffectClosureV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def observe_execution_effect_closure(query: QueryExecutionEffectClosureV1) -> ExecutionEffectClosureObservationV1:
    if not isinstance(query, QueryExecutionEffectClosureV1):
        raise TypeError("query must be QueryExecutionEffectClosureV1")
    attempt = query.execution_attempt

    def refused(
        reason: str, *, active: bool = False, evidence_refs: tuple[str, ...] = ()
    ) -> ExecutionEffectClosureObservationV1:
        return ExecutionEffectClosureObservationV1(
            "active" if active else "unknown", reason, attempt, evidence_refs=evidence_refs
        )

    if Path(query.workspace).resolve() != Path(attempt.workspace).resolve():
        return refused("workspace_mismatch")
    fs = KernelFileSystem(query.workspace, get_default_adapter())
    path = f"runtime/tasks/task_{attempt.task_id}.session.json"
    repository = DirectedEffectOperationRepository()
    try:
        before = fs.read_text(path, encoding="utf-8")
        current_session = json.loads(before)
        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt)
        if not isinstance(current_session, dict):
            return refused("session_identity_mismatch")
        owner_events = _read_owner_events(query.workspace)
        current_matches = _session_matches_identity(current_session, identity)
        if current_matches and not _terminal(current_session):
            return refused("session_not_terminal", active=True)
        terminal = _owner_terminal_session(owner_events, identity, attempt)
        if terminal is None:
            return refused("owner_terminal_fact_missing")
        session, owner_terminal = terminal
        # The current attempt retains its strict disk guard. A historical query
        # requires a real successor claim; a foreign replacement session cannot
        # manufacture a history route.
        if current_matches:
            if not _same_terminal_proof(current_session, session):
                return refused("terminal_owner_session_mismatch")
        elif not _owner_session_known(owner_events, current_session, query.workspace, attempt.task_id):
            return refused("session_identity_mismatch")
        current_identity = _attempt_from_session(query.workspace, session)
        validation = validate_fulfilled_terminal_intent(TaskExecutionSession.from_dict(session), current_identity)
        if isinstance(validation, DirectedEffectSettlementPreBarrierVerdictV1):
            return refused(str(validation.evidence.get("reason") or validation.code))
        metadata = session["metadata"]
        proof = metadata["terminal_settlement_proof"]
        registry = repository._load_registry(query.workspace, identity)
        no_parent = False
        if isinstance(registry, DirectedEffectOperationResultV1):
            # A trusted owner no-effect verdict plus physical absence is needed.
            # A missing stream lock alone never authenticates a terminal effect.
            if proof.get("registry_state") != "unenrolled_or_nonexistent" or not _registry_absent(
                registry, query.workspace, identity
            ):
                return refused(registry.code)
            no_parent = True
        else:
            if registry.open_binding is not None:
                return refused("parent_registry_open", active=True)
            if not registry.bindings_by_id:
                if (
                    proof.get("registry_state") != "strict_empty"
                    or proof.get("registry_version") != registry.registry_version
                ):
                    return refused("terminal_parent_closure_missing")
                no_parent = True
            elif registry.settlement_close_proof is None:
                return refused("terminal_parent_closure_missing")
            elif any(proof.get(key) != value for key, value in registry.settlement_close_proof.items()):
                return refused("terminal_parent_closure_mismatch")
        receipts: list[ExecutionEffectCommitObservationV1] = []
        stream_reads = []
        refs = [f"task_runtime.execution:{owner_terminal['event_id']}:{owner_terminal['seq']}"]
        if not isinstance(registry, DirectedEffectOperationResultV1):
            refs.append(f"{registry.stream_token}:{registry.source_head_seq}")
        else:
            refs.append(f"{_registry_stream_token(identity)}:owner_terminal_no_effect")
        for binding in () if no_parent else registry.bindings_by_id.values():
            read = repository._read_stream(
                query.workspace, binding.operation_stream_token, max_events=4096, stream_kind="operation"
            )
            if isinstance(read, DirectedEffectOperationResultV1):
                return refused(read.code)
            stream_reads.append((binding.operation_stream_token, read))
            reduced = repository._reduce_operation_stream(read, binding, target=None)
            if isinstance(reduced, DirectedEffectOperationResultV1):
                return refused(reduced.code)
            inventory = registry.sealed_inventories_by_binding_id.get(binding.binding_id)
            if inventory is None or {a.operation.operation_id for a in reduced.aggregates} != {
                member.operation_id for member in inventory.members
            }:
                return refused("operation_inventory_mismatch")
            refs.append(f"{binding.operation_stream_token}:{reduced.source_head_seq}")
            for aggregate in reduced.aggregates:
                if aggregate.state == "ABORTED":
                    continue
                if aggregate.state != "CLOSED_BY_PARENT":
                    return refused("effect_not_closed")
                transition = repository._transition_for_kind(aggregate, "commit_receipt")
                if transition is None:
                    return refused("effect_commit_missing")
                descriptor = transition.descriptor
                receipts.append(
                    ExecutionEffectCommitObservationV1(
                        operation_id=aggregate.operation.operation_id,
                        batch_id=binding.correlation.batch_id,
                        tool_call_id=aggregate.operation.tool_call_id,
                        normalized_tool_name=next(
                            member.normalized_tool_name
                            for member in inventory.members
                            if member.operation_id == aggregate.operation.operation_id
                        ),
                        receipt_ref=str(descriptor["receipt_ref"]),
                        receipt_hash=str(descriptor["receipt_hash"]),
                        receipt_binding_hash=str(descriptor["receipt_binding_hash"]),
                        receipt_outcome=str(descriptor["receipt_outcome"]),
                        commit_event_id=transition.event_id,
                        commit_version=transition.version,
                    )
                )
        if (
            fs.read_text(path, encoding="utf-8") != before
            or repository._load_registry(query.workspace, identity) != registry
            or _read_owner_events(query.workspace) != owner_events
        ):
            return refused("closure_observation_changed")
        if any(
            repository._read_stream(query.workspace, token, max_events=4096, stream_kind="operation") != read
            for token, read in stream_reads
        ):
            return refused("closure_observation_changed")
        basis = json.dumps(
            {
                "identity": identity.to_record(),
                "session": session,
                "owner_terminal": owner_terminal,
                "refs": refs,
                "receipts": [asdict(receipt) for receipt in receipts],
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return ExecutionEffectClosureObservationV1(
            "closed",
            "terminal_no_effect_closure_observed" if no_parent else "terminal_effect_closure_observed",
            attempt,
            tuple(receipts),
            tuple(refs),
            hashlib.sha256(basis).hexdigest(),
        )
    except FactStreamError as exc:
        # Keep canonical failure attribution while exposing only bounded public
        # location fields. Provider text, arbitrary details and tokens stay out.
        diagnostics: list[str] = []
        stream = exc.details.get("stream")
        safe_stream = (
            isinstance(stream, str)
            and 0 < len(stream) <= 256
            and all(char.isascii() and (char.isalnum() or char in "_.-") for char in stream)
        )
        if safe_stream:
            diagnostics.append(f"fact_stream_error:stream={stream}")
        sequence = exc.details.get("seq")
        if type(sequence) is int and sequence >= 0:
            diagnostics.append(f"fact_stream_error:seq={sequence}")
        storage_path = exc.details.get("storage_path")
        if safe_stream and storage_path == f"runtime/events/{stream}.jsonl":
            diagnostics.append(f"fact_stream_error:storage_path={storage_path}")
        if exc.details.get("workspace") == query.workspace:
            diagnostics.append(f"fact_stream_error:workspace={query.workspace}")
        code = (
            exc.code
            if 0 < len(exc.code) <= 128
            and all(char.isascii() and (char.isalnum() or char in "_.-") for char in exc.code)
            else "fact_stream_error"
        )
        return refused(f"closure_observation_unavailable:{code}", evidence_refs=tuple(diagnostics))
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        return refused(f"closure_observation_unavailable:{type(exc).__name__}")


def _read_owner_events(workspace: str) -> tuple[Mapping[str, Any], ...]:
    """Read complete canonical history with KernelOne integrity and pagination."""
    events: list[Mapping[str, Any]] = []
    offset = 0
    expected_total: int | None = None
    while True:
        page = query_fact_events(
            QueryFactEventsV1(
                workspace=workspace,
                stream="task_runtime.execution",
                limit=1000,
                offset=offset,
                strict_integrity=True,
            )
        )
        if expected_total is None:
            expected_total = page.total
        if page.total != expected_total:
            raise ValueError("owner fact history changed during observation")
        events.extend(page.events)
        if page.next_offset == 0:
            if len(events) != page.total or any(e.get("seq") != i for i, e in enumerate(events, 1)):
                raise ValueError("owner fact history is incomplete")
            return tuple(events)
        if page.next_offset <= offset:
            raise ValueError("owner fact history pagination did not advance")
        offset = page.next_offset


def _session_matches_identity(session: Mapping[str, Any], identity: DirectedEffectParentRegistryIdentityV1) -> bool:
    return all(
        session.get(key) == value
        for key, value in identity.to_record().items()
        if key not in {"workspace", "schema_version"}
    )


def _terminal(session: Mapping[str, Any]) -> bool:
    return session.get("status") in {"completed", "failed", "cancelled"} and session.get("resumable") is False


def _event_session(event: Mapping[str, Any], workspace: str) -> Mapping[str, Any] | None:
    """Check owner provenance independently of the envelope's byte digest."""
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or event.get("source") != "runtime.task_runtime":
        return None
    row = payload.get("task_row_snapshot")
    metadata = row.get("metadata") if isinstance(row, Mapping) else None
    session = metadata.get("runtime_execution") if isinstance(metadata, Mapping) else None
    if not isinstance(row, Mapping) or not isinstance(session, Mapping):
        return None
    envelope_metadata = event.get("metadata")
    if not isinstance(envelope_metadata, Mapping) or (
        payload.get("schema_version") != "task-runtime.execution-fact/1"
        or payload.get("workspace") != workspace
        or event.get("stream") != "task_runtime.execution"
        or event.get("event_type") != payload.get("event_type")
        or str(row.get("id")) != str(session.get("task_id"))
        or str(payload.get("task_id")) != str(session.get("task_id"))
        or str(event.get("aggregate_id")) != str(session.get("task_id"))
        or payload.get("run_id") != session.get("run_id")
        or envelope_metadata.get("run_id") != session.get("run_id")
    ):
        return None
    return session


def _attempt_from_session(workspace: str, session: Mapping[str, Any]) -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(
        {
            "workspace": workspace,
            "schema_version": "task-runtime.execution-attempt-identity/1",
            **{
                key: session[key]
                for key in (
                    "task_id",
                    "external_task_id",
                    "session_id",
                    "attempt",
                    "role_id",
                    "worker_id",
                    "run_id",
                    "lease_expires_at",
                )
            },
        }
    )


def _owner_terminal_session(
    events: tuple[Mapping[str, Any], ...],
    identity: DirectedEffectParentRegistryIdentityV1,
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
) -> tuple[dict[str, Any], Mapping[str, Any]] | None:
    result = None
    known_leases = set()
    for event in events:
        session = _event_session(event, attempt.workspace)
        if session is None or not _session_matches_identity(session, identity):
            continue
        known_leases.add(session.get("lease_expires_at"))
        metadata = session.get("metadata")
        if isinstance(metadata, Mapping):
            known_leases.add(metadata.get("settlement_identity_lease_expires_at"))
        payload = event["payload"]
        transition = session.get("terminal_transition_id")
        if (
            event.get("event_type") not in {"completed", "failed"}
            or event.get("event_type") != session.get("status")
            or payload.get("terminal") is not True
            or payload.get("status") != session.get("status")
            or payload.get("execution_state") != session.get("status")
            or payload["task_row_snapshot"].get("status") != session.get("status")
            or payload.get("attempt") != session.get("attempt")
            or event["metadata"].get("task_id") != str(session.get("task_id"))
            or event["metadata"].get("correlation_id") != session.get("session_id")
            or not _terminal(session)
            or not transition
            or payload.get("transition_id") != transition
            or payload.get("session_id") != session.get("session_id")
            or event.get("correlation_id") != session.get("session_id")
            or event["metadata"].get("idempotency_key") != f"task_runtime.execution:{event['event_type']}:{transition}"
        ):
            continue
        result = (dict(session), event)
    return result if attempt.lease_expires_at in known_leases else None


def _owner_session_known(
    events: tuple[Mapping[str, Any], ...], current: Mapping[str, Any], workspace: str, task_id: int
) -> bool:
    try:
        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            _attempt_from_session(workspace, current)
        )
    except (KeyError, TypeError, ValueError):
        return False
    if identity.task_id != task_id:
        return False
    return any(
        event.get("event_type") in {"claimed", "claim_renewed", "completed", "failed"}
        and (session := _event_session(event, workspace)) is not None
        and _session_matches_identity(session, identity)
        and event["payload"].get("session_id") == current.get("session_id")
        for event in events
    )


def _same_terminal_proof(current: Mapping[str, Any], owner: Mapping[str, Any]) -> bool:
    keys = ("status", "resumable", "terminal_transition_id")
    if any(current.get(key) != owner.get(key) for key in keys):
        return False
    current_metadata = current.get("metadata")
    owner_metadata = owner.get("metadata")
    return (
        isinstance(current_metadata, Mapping)
        and isinstance(owner_metadata, Mapping)
        and all(
            current_metadata.get(key) == owner_metadata.get(key)
            for key in ("pending_terminal_intent", "settlement_identity_lease_expires_at", "terminal_settlement_proof")
        )
    )


def _registry_absent(
    result: DirectedEffectOperationResultV1, workspace: str, identity: DirectedEffectParentRegistryIdentityV1
) -> bool:
    token = _registry_stream_token(identity)
    details = result.evidence.get("fact_stream_details")
    return (
        result.code == "stream_lock_missing"
        and isinstance(details, Mapping)
        and (
            details.get("workspace") == workspace
            and details.get("stream") == token
            and details.get("stream_leaf_presence") == "missing"
            and details.get("storage_path") == f"runtime/events/{token}.jsonl"
        )
    )
