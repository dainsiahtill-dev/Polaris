"""Terminal observation reuses the TaskRuntime strict DEO reducers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from polaris.cells.director.runtime.public import hash_directed_effect_arguments
from polaris.cells.events.fact_stream.public import (
    FactPayloadReferenceV1,
    FactStreamError,
    QueryFactEventsV1,
    QueryPayloadFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    query_fact_events,
    query_payload_fact_events,
    query_payload_fact_ledger_presence,
)
from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
    _registry_stream_token,
)
from polaris.cells.runtime.task_runtime.internal.execution_session import TaskExecutionSession
from polaris.cells.runtime.task_runtime.internal.terminal_intent import (
    _canonical_sha256,
    validate_fulfilled_terminal_intent,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    DirectedEffectOperationResultV1,
    DirectedEffectParentRegistryIdentityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    ExecutionEffectClosureObservationV1,
    ExecutionEffectCommitObservationV1,
    ExecutionEffectTerminalFailureObservationV1,
    QueryExecutionEffectClosuresV1,
    QueryExecutionEffectClosureV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_workspace_runtime_identity


def observe_execution_effect_closure(query: QueryExecutionEffectClosureV1) -> ExecutionEffectClosureObservationV1:
    return _observe_execution_effect_closure(query)


def _observe_execution_effect_closure(
    query: QueryExecutionEffectClosureV1,
    *,
    owner_history: tuple[Mapping[str, Any], ...] | None = None,
) -> ExecutionEffectClosureObservationV1:
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
        try:
            before = fs.read_text(path, encoding="utf-8")
        except FileNotFoundError:
            before = None
        current_session = json.loads(before) if before is not None else None
        identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt)
        if before is not None and not isinstance(current_session, dict):
            return refused("session_identity_mismatch")
        owner_events = _read_owner_events(query.workspace) if owner_history is None else owner_history
        current_matches = isinstance(current_session, dict) and _session_matches_identity(current_session, identity)
        if current_matches and isinstance(current_session, dict) and not _terminal(current_session):
            return refused("session_not_terminal", active=True)
        terminal = _owner_terminal_session(owner_events, identity, attempt)
        if terminal is None:
            return refused("owner_terminal_fact_missing")
        session, owner_terminal = terminal
        removal_witness = None
        # The current attempt retains its strict disk guard. A historical query
        # requires a real successor claim; a foreign replacement session cannot
        # manufacture a history route.
        if current_session is None:
            removal_witness = _owner_reset_removed_session(
                owner_events, identity, session, owner_terminal, attempt.workspace
            )
            if removal_witness is None:
                return refused("owner_reset_removal_fact_missing_or_invalid")
        elif current_matches:
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
        terminal_failures: list[ExecutionEffectTerminalFailureObservationV1] = []
        role_owner_events = None
        parent_owner_events = None
        refs = [f"task_runtime.execution:{owner_terminal['event_id']}:{owner_terminal['seq']}"]
        if removal_witness is not None:
            refs.append(f"task_runtime.execution:{removal_witness['event_id']}:{removal_witness['seq']}")
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
                if aggregate.state == "DEAD_LETTER":
                    if session.get("status") != "failed":
                        return refused("negative_effect_requires_failed_owner")
                    if role_owner_events is None:
                        role_owner_events = _read_role_owner_events(
                            query.workspace,
                            attempt,
                            turn_ids=frozenset(item.correlation.turn_id for item in registry.bindings_by_id.values()),
                        )
                        parent_owner_events = _read_owner_events(query.workspace, stream=registry.stream_token)
                    member = next(m for m in inventory.members if m.operation_id == aggregate.operation.operation_id)
                    assert parent_owner_events is not None
                    failure = _observe_terminal_failure(
                        attempt,
                        binding,
                        member,
                        aggregate,
                        reduced,
                        registry,
                        owner_terminal,
                        role_owner_events.events,
                        parent_owner_events,
                        repository,
                    )
                    if failure is None:
                        return refused("terminal_failure_proof_missing_or_invalid")
                    terminal_failures.append(failure)
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
        try:
            after = fs.read_text(path, encoding="utf-8")
        except FileNotFoundError:
            after = None
        if (
            after != before
            or repository._load_registry(query.workspace, identity) != registry
            or (owner_history is None and _read_owner_events(query.workspace) != owner_events)
        ):
            return refused("closure_observation_changed")
        if any(
            repository._read_stream(query.workspace, token, max_events=4096, stream_kind="operation") != read
            for token, read in stream_reads
        ):
            return refused("closure_observation_changed")
        if role_owner_events is not None:
            assert not isinstance(registry, DirectedEffectOperationResultV1)
        if role_owner_events is not None and (
            _read_role_owner_events(
                query.workspace,
                attempt,
                turn_ids=frozenset(item.correlation.turn_id for item in registry.bindings_by_id.values()),
            )
            != role_owner_events
            or _read_owner_events(query.workspace, stream=registry.stream_token) != parent_owner_events
        ):
            return refused("closure_observation_changed")
        basis = json.dumps(
            {
                "identity": identity.to_record(),
                "session": session,
                "owner_terminal": owner_terminal,
                "refs": refs,
                "receipts": [asdict(receipt) for receipt in receipts],
                "terminal_failures": [asdict(failure) for failure in terminal_failures],
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
            terminal_failures=tuple(terminal_failures),
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, FactStreamError) as exc:
        return _history_error_observation(query, exc)


def _history_error_observation(
    query: QueryExecutionEffectClosureV1, exc: Exception
) -> ExecutionEffectClosureObservationV1:
    if isinstance(exc, FactStreamError):
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
        return ExecutionEffectClosureObservationV1(
            "unknown",
            f"closure_observation_unavailable:{code}",
            query.execution_attempt,
            evidence_refs=tuple(diagnostics),
        )

    return ExecutionEffectClosureObservationV1(
        "unknown", f"closure_observation_unavailable:{type(exc).__name__}", query.execution_attempt
    )


def observe_execution_effect_closures(
    query: QueryExecutionEffectClosuresV1,
) -> tuple[ExecutionEffectClosureObservationV1, ...]:
    if not isinstance(query, QueryExecutionEffectClosuresV1):
        raise TypeError("query must be QueryExecutionEffectClosuresV1")
    queries = tuple(QueryExecutionEffectClosureV1(query.workspace, attempt) for attempt in query.execution_attempts)
    if not queries:
        return ()
    eligible = tuple(
        Path(item.workspace).resolve() == Path(item.execution_attempt.workspace).resolve() for item in queries
    )
    if not any(eligible):
        return tuple(_observe_execution_effect_closure(item) for item in queries)
    try:
        before = _read_owner_events(query.workspace)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, FactStreamError) as exc:
        return tuple(
            _history_error_observation(item, exc) if same_workspace else _observe_execution_effect_closure(item)
            for item, same_workspace in zip(queries, eligible, strict=True)
        )
    observations = tuple(_observe_execution_effect_closure(item, owner_history=before) for item in queries)
    try:
        after = _read_owner_events(query.workspace)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, FactStreamError) as exc:
        return tuple(
            _history_error_observation(item, exc) if same_workspace else observation
            for item, observation, same_workspace in zip(queries, observations, eligible, strict=True)
        )
    if after != before:
        return tuple(
            ExecutionEffectClosureObservationV1("unknown", "closure_observation_changed", item.execution_attempt)
            if same_workspace
            else observation
            for item, observation, same_workspace in zip(queries, observations, eligible, strict=True)
        )
    return observations


def _read_owner_events(workspace: str, *, stream: str = "task_runtime.execution") -> tuple[Mapping[str, Any], ...]:
    """Read complete canonical history with KernelOne integrity and pagination."""
    events: list[Mapping[str, Any]] = []
    offset = 0
    expected_total: int | None = None
    while True:
        page = query_fact_events(
            QueryFactEventsV1(
                workspace=workspace,
                stream=stream,
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


_V3_ROLE_STREAM = "roles.kernel.turn_outcomes.v3"
_V3_ROLE_SCHEMA = "roles.kernel.turn_outcome_fact.v3"


@dataclass(frozen=True)
class _RoleOwnerHistory:
    events: tuple[Mapping[str, Any], ...]
    # Query snapshot only; never persisted or accepted as a new fact authority.
    v3_head: Any = None


def _read_role_owner_events(
    workspace: str, attempt: TaskRuntimeExecutionAttemptIdentityV1, *, turn_ids: frozenset[str]
) -> _RoleOwnerHistory:
    """Observe both immutable protocols without remapping V3 hashes or seqs."""
    legacy = _read_owner_events(workspace, stream="roles.kernel.turn_outcomes")
    if not query_payload_fact_ledger_presence(
        QuerySegmentedFactLedgerHeadV1(workspace=workspace, logical_stream=_V3_ROLE_STREAM)
    ):
        return _RoleOwnerHistory(legacy)
    events: list[Mapping[str, Any]] = []
    observed_count = 0
    storage = resolve_workspace_runtime_identity(workspace)
    continuation = None
    captured_head = None
    seen: set[str] = set()
    while True:
        page = query_payload_fact_events(
            QueryPayloadFactEventsV1(
                workspace=workspace, logical_stream=_V3_ROLE_STREAM, limit=32, continuation=continuation
            )
        )
        if captured_head is None:
            captured_head = page.captured_head
        if page.captured_head != captured_head:
            raise ValueError("role payload fact history changed during observation")
        # Expand at most one bounded page, retain only this exact owner. The
        # captured head still detects concurrent changes anywhere in history.
        for event in page.events:
            observed_count += 1
            if event.get("global_seq") != observed_count:
                raise ValueError("role payload fact history is incomplete")
            payload = _validated_v3_role_payload(event, storage)
            session_binding = payload.get("session_binding")
            if (
                payload.get("run_id") == attempt.run_id
                and payload.get("task_id") == attempt.external_task_id
                and isinstance(session_binding, Mapping)
                and session_binding.get("session_id") == attempt.session_id
                and payload.get("turn_id") in turn_ids
            ):
                events.append(event)
        if page.continuation is None:
            if observed_count != captured_head.total_count:
                raise ValueError("role payload fact history is incomplete")
            return _RoleOwnerHistory((*legacy, *events), captured_head)
        if page.continuation in seen:
            raise ValueError("role payload fact pagination did not advance")
        seen.add(page.continuation)
        continuation = page.continuation


def _validated_v3_role_payload(event: Mapping[str, Any], storage: Any) -> Mapping[str, Any]:
    """Bind expanded owner data to the actual small sealed storage record."""
    raw = event.get("storage_record")
    payload = event.get("expanded_payload")
    fields = {
        "schema",
        "event_id",
        "logical_stream",
        "global_seq",
        "segment_index",
        "local_seq",
        "event_type",
        "source",
        "payload",
        "idempotency_key",
        "occurred_at",
        "previous_event_hash",
        "event_hash",
    }
    if (
        type(raw) is not dict
        or set(raw) != fields
        or raw.get("schema") != "kernelone.segmented_event.v1"
        or raw.get("logical_stream") != _V3_ROLE_STREAM
        or any(raw[key] != event.get(key) for key in fields - {"schema"})
        or type(payload) is not dict
        or payload.get("schema_version") != _V3_ROLE_SCHEMA
    ):
        raise ValueError("role payload storage record or expanded schema mismatch")
    body = {key: value for key, value in raw.items() if key != "event_hash"}
    if _canonical_sha256(body) != raw["event_hash"]:
        raise ValueError("role payload raw storage hash mismatch")
    reference_payload = raw["payload"]
    if (
        type(reference_payload) is not dict
        or set(reference_payload) != {"schema_version", "payload_ref"}
        or reference_payload.get("schema_version") != "kernelone.payload_fact.v1"
    ):
        raise ValueError("role payload reference shape mismatch")
    ref = FactPayloadReferenceV1.from_record(reference_payload["payload_ref"])
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    if (
        ref.storage_identity_token != storage.token
        or ref.payload_schema != _V3_ROLE_SCHEMA
        or ref.byte_size != len(data)
        or ref.sha256 != hashlib.sha256(data).hexdigest()
    ):
        raise ValueError("role expanded payload integrity or workspace mismatch")
    return payload


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


def _owner_reset_removed_session(
    events: tuple[Mapping[str, Any], ...],
    identity: DirectedEffectParentRegistryIdentityV1,
    terminal_session: Mapping[str, Any],
    owner_terminal: Mapping[str, Any],
    workspace: str,
) -> Mapping[str, Any] | None:
    """Only the latest canonical task fact may explain an absent session."""
    scoped = [
        event
        for event in events
        if isinstance(event.get("payload"), Mapping)
        and str(event["payload"].get("task_id")) == str(terminal_session.get("task_id"))
    ]
    if not scoped:
        return None
    latest = scoped[-1]
    session = _event_session(latest, workspace)
    if session is None or not _owner_session_known(events, session, workspace, identity.task_id):
        return None
    successor_attempt = _attempt_from_session(workspace, session)
    successor_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(successor_attempt)
    if successor_identity.external_task_id != identity.external_task_id:
        return None
    successor_terminal = _owner_terminal_session(events, successor_identity, successor_attempt)
    if successor_terminal is None:
        return None
    successor_session, successor_owner = successor_terminal
    if successor_owner.get("seq", 0) < owner_terminal.get("seq", 0):
        return None
    validation = validate_fulfilled_terminal_intent(
        TaskExecutionSession.from_dict(successor_session), successor_attempt
    )
    if isinstance(validation, DirectedEffectSettlementPreBarrierVerdictV1):
        return None
    successor_registry = DirectedEffectOperationRepository()._load_registry(workspace, successor_identity)
    successor_proof = successor_session["metadata"]["terminal_settlement_proof"]
    if isinstance(successor_registry, DirectedEffectOperationResultV1):
        if successor_proof.get("registry_state") != "unenrolled_or_nonexistent" or not _registry_absent(
            successor_registry, workspace, successor_identity
        ):
            return None
    elif successor_registry.open_binding is not None:
        return None
    elif not successor_registry.bindings_by_id:
        if (
            successor_proof.get("registry_state") != "strict_empty"
            or successor_proof.get("registry_version") != successor_registry.registry_version
        ):
            return None
    elif successor_registry.settlement_close_proof is None or any(
        successor_proof.get(key) != value for key, value in successor_registry.settlement_close_proof.items()
    ):
        return None
    payload = latest["payload"]
    row = payload.get("task_row_snapshot")
    metadata = row.get("metadata") if isinstance(row, Mapping) else None
    reference = metadata.get("task_runtime_settlement") if isinstance(metadata, Mapping) else None
    envelope_metadata = latest.get("metadata")
    transition = payload.get("transition_id")
    reset_key = f"task_runtime.execution:runtime_reset_removed:{transition}"
    valid = bool(
        latest.get("event_type") == "runtime_reset_removed"
        and isinstance(row, Mapping)
        and row.get("status") == "removed"
        and payload.get("status") == payload.get("execution_state") == "removed"
        and payload.get("attempt") == successor_session.get("attempt")
        and payload.get("session_id") == ""
        and isinstance(transition, str)
        and transition
        and payload.get("idempotency_key") == reset_key
        and isinstance(envelope_metadata, Mapping)
        and envelope_metadata.get("idempotency_key") == reset_key
        and isinstance(reference, Mapping)
        and reference.get("fact_event_id") == successor_owner.get("event_id")
        and reference.get("fact_event_seq") == successor_owner.get("seq")
        and reference.get("outcome") == successor_session.get("status")
        and reference.get("terminal_transition_id") == successor_session.get("terminal_transition_id")
        and _session_matches_identity(session, successor_identity)
        and _same_terminal_proof(session, successor_session)
        and _terminal(session)
    )
    return latest if valid else None


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


def _observe_terminal_failure(
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    binding: Any,
    member: Any,
    aggregate: Any,
    reduced: Any,
    registry: Any,
    owner_terminal: Mapping[str, Any],
    role_events: tuple[Mapping[str, Any], ...],
    parent_events: tuple[Mapping[str, Any], ...],
    repository: DirectedEffectOperationRepository,
) -> ExecutionEffectTerminalFailureObservationV1 | None:
    """Bind the negative result to real role, operation, parent and task owners."""
    dead = repository._transition_for_kind(aggregate, "dead_letter")
    recovery = repository._transition_for_kind(aggregate, "mark_recovery_pending")
    claim = repository._transition_for_kind(aggregate, "claim")
    if (
        dead is None
        or recovery is None
        or claim is None
        or repository._transition_for_kind(aggregate, "commit_receipt") is not None
    ):
        return None
    descriptor = dead.descriptor
    if descriptor.get("expected_receipt_binding_hash") != member.expected_receipt_binding_hash:
        return None
    parents = [
        e
        for e in parent_events
        if e.get("event_type") == "task_runtime.deo_parent_registry.v1.closed"
        and e.get("source") == "runtime.task_runtime"
        and e.get("payload", {}).get("binding_id") == binding.binding_id
    ]
    if len(parents) != 1:
        return None
    parent = parents[0]
    close = parent["payload"]
    if (
        close.get("stable_registry_identity") != registry.identity.to_record()
        or close.get("operation_source_head_seq") != reduced.source_head_seq
        or close.get("dead_letter_count") != sum(a.state == "DEAD_LETTER" for a in reduced.aggregates)
        or close.get("receipt_count") != sum(a.state == "CLOSED_BY_PARENT" for a in reduced.aggregates)
        or close.get("actor") not in {"runtime.task_runtime.settlement", "runtime.task_runtime.batch_rollover"}
    ):
        return None
    storage = resolve_workspace_runtime_identity(attempt.workspace)
    matches = []
    for event in role_events:
        is_v3 = event.get("logical_stream") == _V3_ROLE_STREAM
        payload = _validated_v3_role_payload(event, storage) if is_v3 else event.get("payload", {})
        if not isinstance(payload, Mapping):
            continue
        outcome = payload.get("outcome")
        metadata = event.get("metadata")
        if not isinstance(outcome, Mapping) or (not is_v3 and not isinstance(metadata, Mapping)):
            continue
        execution = outcome.get("execution")
        if not isinstance(execution, Mapping):
            continue
        batch = execution.get("receipt")
        if not isinstance(batch, Mapping):
            continue
        transition = payload.get("transition_id")
        provenance = {
            "workspace": attempt.workspace,
            "run_id": attempt.run_id,
            "task_id": attempt.external_task_id,
            "turn_id": binding.correlation.turn_id,
            "transition_id": transition,
        }
        expected_key = ":".join(
            (
                _V3_ROLE_STREAM if is_v3 else "roles.kernel.turn_outcomes",
                storage.token,
                attempt.run_id,
                attempt.external_task_id,
                binding.correlation.turn_id,
                str(transition),
            )
        )
        if is_v3:
            if event.get("idempotency_key") != expected_key:
                continue
        else:
            assert isinstance(metadata, Mapping)
            if (
                metadata.get("provenance") != provenance
                or metadata.get("storage_identity") != storage.to_record()
                or metadata.get("run_id") != attempt.run_id
                or metadata.get("task_id") != attempt.external_task_id
                or event.get("aggregate_id") != attempt.external_task_id
                or event.get("correlation_id") != binding.correlation.turn_id
                or metadata.get("idempotency_key") != expected_key
            ):
                continue
        if (
            event.get("source") != "roles.kernel.transaction"
            or event.get("event_type") != "turn_outcome_committed"
            or payload.get("schema_version") != (_V3_ROLE_SCHEMA if is_v3 else "roles.kernel.turn_outcome_fact.v2")
            or outcome.get("schema_version") != "roles.kernel.turn_outcome.v1"
            or not isinstance(transition, str)
            or not transition
            or payload.get("provenance") != provenance
            or payload.get("storage_identity") != storage.to_record()
            or not isinstance(payload.get("session_binding"), Mapping)
            or payload["session_binding"].get("session_id") != attempt.session_id
            or payload.get("run_id") != attempt.run_id
            or payload.get("task_id") != attempt.external_task_id
            or payload.get("turn_id") != binding.correlation.turn_id
            or outcome.get("turn_id") != binding.correlation.turn_id
            or outcome.get("run_id") != attempt.run_id
            or _canonical_sha256(outcome) != payload.get("outcome_hash")
            or batch.get("turn_id") != binding.correlation.turn_id
            or execution.get("batch_id") != batch.get("batch_id")
            or batch.get("pending_async_count") != 0
            or batch.get("has_pending_async") is not False
        ):
            continue
        rows = batch.get("results")
        raw_rows = batch.get("raw_results")
        if not isinstance(rows, list) or not isinstance(raw_rows, list) or len(rows) != len(raw_rows):
            continue
        selected = [row for row in rows if isinstance(row, dict) and row.get("call_id") == member.tool_call_id]
        if len(selected) != 1:
            continue
        row = selected[0]
        raw_selected = [r for r in raw_rows if isinstance(r, dict) and r.get("call_id") == member.tool_call_id]
        if len(raw_selected) != 1:
            continue
        raw_row = raw_selected[0]
        authority_fields = (
            "call_id",
            "tool_name",
            "status",
            "result",
            "error",
            "effect_receipt",
            "effect_receipt_commit",
            "directed_effect_claim_status",
            "directed_effect_mutation_status",
        )
        if any(raw_row.get(k) != row.get(k) for k in authority_fields):
            continue
        invocations = execution.get("invocations")
        decision = outcome.get("decision")
        decision_metadata = decision.get("metadata") if isinstance(decision, Mapping) else None
        envelopes = (
            decision_metadata.get("native_tool_call_envelopes") if isinstance(decision_metadata, Mapping) else None
        )
        if not isinstance(invocations, list) or not isinstance(envelopes, list):
            continue
        calls = [i for i in invocations if isinstance(i, dict) and i.get("call_id") == member.tool_call_id]
        native = [e for e in envelopes if isinstance(e, dict) and e.get("call_id") == member.tool_call_id]
        if len(calls) != 1 or len(native) != 1:
            continue
        arguments = calls[0].get("arguments")
        if (
            not isinstance(arguments, dict)
            or calls[0].get("tool_name") != member.normalized_tool_name
            or native[0].get("tool_name") != member.normalized_tool_name
            or native[0].get("arguments_hash") != _canonical_sha256(arguments)
        ):
            continue
        physical = row.get("result")
        if not isinstance(physical, dict):
            continue
        fact = physical.get("effect_recovery")
        if not isinstance(fact, dict):
            continue
        ref = descriptor.get("resolution_evidence_ref")
        context = ref.removeprefix("dead-letter://director/") if isinstance(ref, str) else ""
        code = physical.get("exit_code")
        if (
            row.get("status") not in {"error", "failed", "cancelled"}
            or row.get("tool_name") != member.normalized_tool_name
            or row.get("directed_effect_claim_status") != "claimed"
            or row.get("directed_effect_mutation_status") != "failed"
            or row.get("effect_receipt") is not None
            or row.get("effect_receipt_commit") is not None
            or physical.get("schema_version") != "roles.adapters.directed_effect_physical_failure.v1"
            or physical.get("failure_kind") != "physical_result_failed"
            or physical.get("error_code") != "deo_physical_execution_failed"
            or type(code) is not int
            or code <= 0
            or not isinstance(physical.get("physical_error"), str)
            or len(context) != 64
            or any(c not in "0123456789abcdef" for c in context)
            or ref != f"dead-letter://director/{context}"
            or fact.get("schema_version") != "roles.adapters.directed_effect_recovery_fact.v1"
            or fact.get("state") != "DEAD_LETTER"
            or fact.get("operation_id") != aggregate.operation.operation_id
            or fact.get("event_id") != dead.event_id
            or fact.get("version") != dead.version
            or fact.get("resolution_evidence_ref") != ref
            or fact.get("resolution_evidence_hash") != descriptor.get("resolution_evidence_hash")
        ):
            continue
        evidence = (
            ("context_id", context),
            ("failure_kind", "physical_result_failed"),
            ("result_type", "dict"),
            ("physical_error", physical["physical_error"][:400]),
            ("physical_error_type", str(physical.get("physical_error_type") or "")[:80]),
        )
        if (
            hash_directed_effect_arguments(evidence) != recovery.descriptor.get("recovery_evidence_hash")
            or recovery.descriptor.get("recovery_evidence_ref") != f"recovery://director/{context}"
            or hash_directed_effect_arguments((*evidence, ("recovery_event_id", recovery.event_id)))
            != descriptor.get("resolution_evidence_hash")
        ):
            continue
        matches.append(
            ExecutionEffectTerminalFailureObservationV1(
                operation_id=aggregate.operation.operation_id,
                parent_binding_id=binding.binding_id,
                batch_id=binding.correlation.batch_id,
                turn_id=binding.correlation.turn_id,
                tool_call_id=member.tool_call_id,
                normalized_tool_name=member.normalized_tool_name,
                expected_receipt_binding_hash=member.expected_receipt_binding_hash,
                context_id=context,
                resolution_evidence_ref=ref,
                resolution_evidence_hash=descriptor["resolution_evidence_hash"],
                dead_letter_event_id=dead.event_id,
                dead_letter_version=dead.version,
                exit_code=code,
                physical_failure_hash=_canonical_sha256(physical),
                owner_result_hash=_canonical_sha256({k: v for k, v in row.items() if k != "execution_time_ms"}),
                owner_raw_result_hash=_canonical_sha256({k: v for k, v in raw_row.items() if k != "execution_time_ms"}),
                owner_turn_event_id=str(event["event_id"]),
                owner_turn_event_seq=int(event["global_seq"] if is_v3 else event["seq"]),
                parent_close_event_id=str(parent["event_id"]),
                parent_close_version=int(close["version"]),
                owner_terminal_event_id=str(owner_terminal["event_id"]),
                owner_terminal_event_seq=int(owner_terminal["seq"]),
            )
        )
    return matches[0] if len(matches) == 1 else None
