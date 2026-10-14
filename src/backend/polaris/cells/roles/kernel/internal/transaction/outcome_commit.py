"""Canonical durable commit for one TransactionKernel turn.

The execution fact is written once to ``events.fact_stream``. ContextOS,
TaskRuntime, Run Ledger, QA, Factory, and UI consume projections of this fact;
they are not allowed to author a competing terminal outcome.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    AppendPayloadFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    FactEventAppendedV1,
    FactPayloadReferenceV1,
    FactStreamProvenanceV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
    QueryPayloadFactByIdempotencyKeyV1,
    QuerySegmentedFactLedgerHeadV1,
    StoreFactPayloadCommandV1,
    append_fact_event,
    append_payload_fact_event,
    ensure_segmented_fact_ledger,
    query_fact_events,
    query_fact_stream_head,
    query_payload_fact_by_idempotency_key,
    query_payload_fact_ledger_presence,
    store_fact_payload,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.public.outcome_recovery import (
    PreparedTurnOutcomeReferenceV1,
    QueryPreparedTurnOutcomeV1,
    RecoveredTurnOutcomeV1,
    ReplayPreparedTurnOutcomeCommandV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchReceipt,
    CommitReceipt,
    FinalizationRecord,
    FinalizeMode,
    SealedTurn,
    ToolBatchExecution,
    ToolEffectType,
    TurnFailureClass,
    TurnOutcome,
)
from polaris.kernelone.events.sourcing.payload_store import DurablePayloadStore
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_workspace_runtime_identity

TURN_OUTCOME_STREAM = "roles.kernel.turn_outcomes"
TURN_OUTCOME_PAYLOAD_STREAM = "roles.kernel.turn_outcomes.v3"
TURN_OUTCOME_EVENT_TYPE = "turn_outcome_committed"
TURN_OUTCOME_PREPARED_EVENT_TYPE = "turn_outcome_prepared"
TURN_OUTCOME_SOURCE = "roles.kernel.transaction"


class TurnOutcomeCommitError(RuntimeError):
    """Raised when an authoritative turn outcome cannot be committed."""

    prepared_turn_outcome: PreparedTurnOutcomeReferenceV1 | None = None


@dataclass(frozen=True)
class DurableTurnCommit:
    """Committed outcome and its immutable durability evidence."""

    outcome: TurnOutcome
    receipt: CommitReceipt
    sealed_turn: SealedTurn


def commit_turn_result(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    transition_id: str,
    ledger: TurnLedger,
    result: Mapping[str, Any] | None,
    completion_status: str,
    failure_reason: str | None = None,
    session_id: str = "",
    source_state_before: Mapping[str, str] | None = None,
    source_state_after: Mapping[str, str] | None = None,
) -> DurableTurnCommit:
    """Build and atomically persist the canonical result for one turn.

    The operation is idempotent by its typed workspace/run/task/turn/transition
    provenance and rejects a replay whose payload differs from the already
    committed outcome.

    Complexity:
        O(e + p + n) time and O(e + p) memory, where ``e`` is the number of
        tool executions, ``p`` is the serialized outcome size, and ``n`` is
        the number of prior facts for this task when checking unscoped legacy
        events. The underlying JSONL store also performs an O(n) idempotency
        lookup per stream.
    """

    normalized_workspace = str(workspace or "").strip()
    normalized_run_id = str(run_id or "").strip()
    normalized_task_id = str(task_id or "").strip()
    normalized_transition_id = str(transition_id or "").strip()
    if not normalized_workspace or not normalized_run_id or not normalized_task_id or not normalized_transition_id:
        raise TurnOutcomeCommitError(
            "workspace, run_id, task_id, and transition_id are required for durable turn commit"
        )

    storage_identity = resolve_workspace_runtime_identity(normalized_workspace)
    provenance = FactStreamProvenanceV1(
        workspace=storage_identity.workspace_abs,
        run_id=normalized_run_id,
        task_id=normalized_task_id,
        turn_id=str(ledger.turn_id),
        transition_id=normalized_transition_id,
    )
    _reject_unscoped_legacy_transition(provenance)
    # Presence chooses the existing protocol; it grants no integrity or write
    # authority. Resolve the legacy location through its public owner contract.
    legacy_head = query_fact_stream_head(
        QueryFactStreamHeadV1(workspace=normalized_workspace, stream=TURN_OUTCOME_STREAM)
    )
    legacy_present = KernelFileSystem(normalized_workspace, get_default_adapter()).exists(legacy_head.storage_path)
    stream = TURN_OUTCOME_STREAM if legacy_present else TURN_OUTCOME_PAYLOAD_STREAM

    result_payload = dict(result or {})
    failure_class = _failure_class_for(
        status=completion_status,
        result_kind=str(result_payload.get("kind") or ""),
        has_decision=ledger.final_decision is not None,
    )
    execution = _execution_from_result(ledger=ledger, result=result_payload)
    closing = _closing_from_result(ledger=ledger, result=result_payload, execution=execution)
    outcome = ledger.to_turn_outcome(
        run_id=normalized_run_id,
        decision=ledger.final_decision,
        execution=execution,
        closing=closing,
        failure_class=failure_class,
        failure_reason=str(failure_reason or "").strip() or None,
    )
    outcome_payload = outcome.model_dump(mode="json", exclude={"commit_ref"})
    outcome_hash = _stable_hash(outcome_payload)
    event_payload = {
        "schema_version": "roles.kernel.turn_outcome_fact.v2"
        if legacy_present
        else "roles.kernel.turn_outcome_fact.v3",
        "run_id": normalized_run_id,
        "task_id": normalized_task_id,
        "turn_id": str(ledger.turn_id),
        "transition_id": normalized_transition_id,
        "provenance": provenance.to_record(),
        "storage_identity": storage_identity.to_record(),
        "outcome_hash": outcome_hash,
        "outcome": outcome_payload,
        # Preserve the owner's caller-facing result without storing the live
        # TurnLedger or deriving a different kind during restart recovery.
        "result_projection": {
            **{
                key: result_payload[key]
                for key in ("kind", "visible_content", "finalization", "workflow_context")
                if key in result_payload
            },
            "completion_status": completion_status,
        },
    }
    if session_id:
        event_payload["session_binding"] = {
            "session_id": session_id,
            "source_state_before": dict(source_state_before or {}),
            "source_state_after": dict(source_state_after or {}),
        }
    idempotency_key = ":".join(
        (
            stream,
            storage_identity.token,
            normalized_run_id,
            normalized_task_id,
            str(ledger.turn_id),
            normalized_transition_id,
        )
    )
    prepared = None
    try:
        if not legacy_present and not query_payload_fact_ledger_presence(
            QuerySegmentedFactLedgerHeadV1(workspace=normalized_workspace, logical_stream=stream)
        ):
            ensure_segmented_fact_ledger(
                EnsureSegmentedFactLedgerCommandV1(
                    workspace=normalized_workspace,
                    logical_stream=stream,
                    maintenance_reason="roles.kernel.turn_outcome_payload_protocol",
                )
            )
        # The full canonical payload is durable before any prepare fact. Both
        # phases reference identical bytes; only committed is a terminal result.
        payload_ref = store_fact_payload(
            StoreFactPayloadCommandV1(
                workspace=normalized_workspace,
                payload=event_payload,
                payload_schema=event_payload["schema_version"],
                maintenance_reason="roles.kernel.turn_outcome_prepare",
            )
        )
        if legacy_present:
            preparation = append_fact_event(
                AppendFactEventCommandV1(
                    workspace=normalized_workspace,
                    stream=stream,
                    event_type=TURN_OUTCOME_PREPARED_EVENT_TYPE,
                    payload={
                        "schema_version": "roles.kernel.prepared_turn_outcome.v1",
                        "provenance": provenance.to_record(),
                        "payload_ref": payload_ref.to_record(),
                    },
                    source=TURN_OUTCOME_SOURCE,
                    run_id=normalized_run_id,
                    task_id=normalized_task_id,
                    correlation_id=str(ledger.turn_id),
                    provenance=provenance,
                    idempotency_key=idempotency_key + ":prepared",
                    strict_integrity=True,
                    durability="fsync",
                )
            )
        else:
            preparation = append_payload_fact_event(
                AppendPayloadFactEventCommandV1(
                    workspace=normalized_workspace,
                    logical_stream=stream,
                    event_type=TURN_OUTCOME_PREPARED_EVENT_TYPE,
                    source=TURN_OUTCOME_SOURCE,
                    payload_ref=payload_ref,
                    idempotency_key=idempotency_key + ":prepared",
                )
            )
        if preparation.appended_seq is None:
            raise TurnOutcomeCommitError("prepared outcome returned no actual sequence")
        prepared = PreparedTurnOutcomeReferenceV1(
            provenance,
            stream,
            preparation.event_id,
            preparation.appended_seq,
            payload_ref,
        )
        appended = _append_canonical_payload(prepared, event_payload, expected_next_seq=preparation.appended_seq + 1)
    except (RuntimeError, ValueError) as exc:
        error = TurnOutcomeCommitError(f"failed to commit turn outcome: {exc}")
        if prepared is not None:
            error.prepared_turn_outcome = prepared
        raise error from exc
    return _durable_commit_from_payload(event_payload, appended)


def _commit_key(provenance: FactStreamProvenanceV1, stream: str) -> str:
    storage = resolve_workspace_runtime_identity(provenance.workspace)
    return ":".join(
        (
            stream,
            storage.token,
            provenance.run_id or "",
            provenance.task_id or "",
            provenance.turn_id or "",
            provenance.transition_id or "",
        )
    )


def _append_canonical_payload(
    prepared: PreparedTurnOutcomeReferenceV1,
    payload: Mapping[str, Any],
    *,
    expected_next_seq: int,
) -> FactEventAppendedV1:
    provenance = prepared.provenance
    key = _commit_key(provenance, prepared.stream)
    if prepared.stream == TURN_OUTCOME_STREAM:
        return append_fact_event(
            AppendFactEventCommandV1(
                workspace=provenance.workspace,
                stream=prepared.stream,
                event_type=TURN_OUTCOME_EVENT_TYPE,
                payload=payload,
                source=TURN_OUTCOME_SOURCE,
                run_id=provenance.run_id,
                task_id=provenance.task_id,
                correlation_id=provenance.turn_id,
                provenance=provenance,
                idempotency_key=key,
                expected_seq=expected_next_seq,
                strict_integrity=True,
                durability="fsync",
            )
        )
    return append_payload_fact_event(
        AppendPayloadFactEventCommandV1(
            workspace=provenance.workspace,
            logical_stream=prepared.stream,
            event_type=TURN_OUTCOME_EVENT_TYPE,
            source=TURN_OUTCOME_SOURCE,
            payload_ref=prepared.payload_ref,
            idempotency_key=key,
            expected_global_seq=expected_next_seq,
        )
    )


def _durable_commit_from_payload(payload: Mapping[str, Any], appended: FactEventAppendedV1) -> DurableTurnCommit:
    if appended.appended_seq is None:
        raise TurnOutcomeCommitError("fact stream returned no sequence for committed turn outcome")
    outcome = TurnOutcome.model_validate(payload["outcome"])
    receipt = CommitReceipt(
        turn_id=outcome.turn_id,
        snapshot_id=appended.event_id,
        truthlog_seq_range=(appended.appended_seq, appended.appended_seq),
        sealed_at=appended.appended_at,
        validation_passed=True,
        fact_stream=appended.stream,
        fact_event_id=appended.event_id,
        fact_event_seq=appended.appended_seq,
        fact_storage_path=appended.storage_path,
        outcome_hash=str(payload["outcome_hash"]),
    )
    outcome = outcome.model_copy(update={"commit_ref": receipt})
    sealed = SealedTurn(
        turn_id=outcome.turn_id,
        commit_receipt=receipt,
        outcome_status=outcome.outcome_status,
        resolution_code=outcome.resolution_code,
        sealed_at=receipt.sealed_at,
    )
    return DurableTurnCommit(outcome, receipt, sealed)


def _owner_stream_snapshot(query: QueryPreparedTurnOutcomeV1) -> tuple[int, list[dict[str, Any]]]:
    provenance = query.provenance
    workspace = str(Path(provenance.workspace).expanduser().resolve())
    if workspace != provenance.workspace:
        raise TurnOutcomeCommitError("prepared outcome workspace must be canonical")
    events: list[dict[str, Any]] = []
    if query.stream == TURN_OUTCOME_STREAM:
        before = query_fact_stream_head(
            QueryFactStreamHeadV1(workspace=workspace, stream=query.stream, strict_integrity=True)
        )
        offset = 0
        while True:
            page = query_fact_events(
                QueryFactEventsV1(
                    workspace=workspace,
                    stream=query.stream,
                    run_id=provenance.run_id,
                    task_id=provenance.task_id,
                    strict_integrity=True,
                    limit=1000,
                    offset=offset,
                )
            )
            events.extend(page.events)
            if not page.next_offset:
                break
            if page.next_offset <= offset:
                raise TurnOutcomeCommitError("prepared outcome history cursor invalid")
            offset = page.next_offset
        after = query_fact_stream_head(
            QueryFactStreamHeadV1(workspace=workspace, stream=query.stream, strict_integrity=True)
        )
        if before != after:
            raise TurnOutcomeCommitError("prepared outcome head changed")
        return after.current_seq, events
    head_query = QuerySegmentedFactLedgerHeadV1(workspace=workspace, logical_stream=query.stream)
    if not query_payload_fact_ledger_presence(head_query):
        raise TurnOutcomeCommitError("prepared outcome stream missing")
    # Exact lookup validates small-record history/locator custody without
    # expanding unrelated historical outcome blobs on every ordinary turn.
    key = _commit_key(provenance, query.stream)
    prepared_page = query_payload_fact_by_idempotency_key(
        QueryPayloadFactByIdempotencyKeyV1(
            workspace=workspace,
            logical_stream=query.stream,
            idempotency_key=key + ":prepared",
        )
    )
    committed_page = query_payload_fact_by_idempotency_key(
        QueryPayloadFactByIdempotencyKeyV1(
            workspace=workspace,
            logical_stream=query.stream,
            idempotency_key=key,
        )
    )
    if prepared_page.captured_head != committed_page.captured_head:
        raise TurnOutcomeCommitError("prepared outcome head changed")
    return committed_page.captured_head.global_seq, [*prepared_page.events, *committed_page.events]


def _prepared_reference(query: QueryPreparedTurnOutcomeV1, event: Mapping[str, Any]) -> PreparedTurnOutcomeReferenceV1:
    if event.get("event_type") != TURN_OUTCOME_PREPARED_EVENT_TYPE or event.get("source") != TURN_OUTCOME_SOURCE:
        raise TurnOutcomeCommitError("prepared outcome fact kind/source invalid")
    provenance = query.provenance
    if query.stream == TURN_OUTCOME_STREAM:
        small = event.get("payload")
        if (
            not isinstance(small, Mapping)
            or set(small) != {"schema_version", "provenance", "payload_ref"}
            or small.get("schema_version") != "roles.kernel.prepared_turn_outcome.v1"
        ):
            raise TurnOutcomeCommitError("prepared outcome reference shape invalid")
        if small.get("provenance") != provenance.to_record():
            raise TurnOutcomeCommitError("prepared outcome scope mismatch")
        ref = FactPayloadReferenceV1.from_record(small["payload_ref"])
        sequence = event.get("seq")
    else:
        ref = FactPayloadReferenceV1.from_record(event["storage_record"]["payload"]["payload_ref"])
        sequence = event.get("global_seq")
    key = (
        event.get("idempotency_key")
        if query.stream == TURN_OUTCOME_PAYLOAD_STREAM
        else event.get("metadata", {}).get("idempotency_key")
    )
    if key != _commit_key(provenance, query.stream) + ":prepared":
        raise TurnOutcomeCommitError("prepared outcome idempotency scope mismatch")
    if type(sequence) is not int:
        raise TurnOutcomeCommitError("prepared outcome sequence missing")
    return PreparedTurnOutcomeReferenceV1(provenance, query.stream, str(event["event_id"]), sequence, ref)


def find_prepared_turn_outcome(query: QueryPreparedTurnOutcomeV1) -> PreparedTurnOutcomeReferenceV1 | None:
    if type(query) is not QueryPreparedTurnOutcomeV1:
        raise TypeError("prepared outcome query must be typed")
    if query.stream is None:
        legacy = query_fact_stream_head(
            QueryFactStreamHeadV1(workspace=query.provenance.workspace, stream=TURN_OUTCOME_STREAM)
        )
        legacy_present = KernelFileSystem(query.provenance.workspace, get_default_adapter()).exists(legacy.storage_path)
        stream = TURN_OUTCOME_STREAM if legacy_present else TURN_OUTCOME_PAYLOAD_STREAM
        if not legacy_present and not query_payload_fact_ledger_presence(
            QuerySegmentedFactLedgerHeadV1(workspace=query.provenance.workspace, logical_stream=stream)
        ):
            return None
        query = QueryPreparedTurnOutcomeV1(query.provenance, stream)
    if query.stream == TURN_OUTCOME_PAYLOAD_STREAM:
        page = query_payload_fact_by_idempotency_key(
            QueryPayloadFactByIdempotencyKeyV1(
                workspace=query.provenance.workspace,
                logical_stream=query.stream,
                idempotency_key=_commit_key(query.provenance, query.stream) + ":prepared",
            )
        )
        events = list(page.events)
    else:
        _, events = _owner_stream_snapshot(query)
    matching = []
    for event in events:
        if event.get("event_type") == TURN_OUTCOME_PREPARED_EVENT_TYPE:
            if (
                query.stream == TURN_OUTCOME_STREAM
                and event.get("payload", {}).get("provenance") != query.provenance.to_record()
            ):
                continue
            matching.append(_prepared_reference(query, event))
    if len(matching) > 1:
        raise TurnOutcomeCommitError("prepared outcome is ambiguous")
    return matching[0] if matching else None


def replay_prepared_outcome(command: ReplayPreparedTurnOutcomeCommandV1) -> RecoveredTurnOutcomeV1:
    if type(command) is not ReplayPreparedTurnOutcomeCommandV1:
        raise TypeError("prepared outcome replay must be typed")
    prepared = command.prepared
    query = QueryPreparedTurnOutcomeV1(prepared.provenance, prepared.stream)
    head, events = _owner_stream_snapshot(query)
    matching = [event for event in events if event.get("event_id") == prepared.prepared_event_id]
    if len(matching) != 1 or _prepared_reference(query, matching[0]) != prepared:
        raise TurnOutcomeCommitError("prepared outcome exact reference missing or conflicting")
    payload = DurablePayloadStore(prepared.provenance.workspace, read_only=True).read(prepared.payload_ref)
    expected_schema = (
        "roles.kernel.turn_outcome_fact.v2"
        if prepared.stream == TURN_OUTCOME_STREAM
        else "roles.kernel.turn_outcome_fact.v3"
    )
    if payload.get("schema_version") != expected_schema or payload.get("provenance") != prepared.provenance.to_record():
        raise TurnOutcomeCommitError("prepared outcome scope/schema mismatch")
    for key in ("run_id", "task_id", "turn_id", "transition_id"):
        if payload.get(key) != getattr(prepared.provenance, key):
            raise TurnOutcomeCommitError("prepared outcome scope mismatch")
    if payload.get("storage_identity") != resolve_workspace_runtime_identity(prepared.provenance.workspace).to_record():
        raise TurnOutcomeCommitError("prepared outcome storage identity mismatch")
    if _stable_hash(payload.get("outcome", {})) != payload.get("outcome_hash"):
        raise TurnOutcomeCommitError("prepared outcome hash mismatch")
    binding = payload.get("session_binding", {})
    session_id = binding.get("session_id") if isinstance(binding, Mapping) else None
    if (session_id or "") != (command.expected_session_id or ""):
        raise TurnOutcomeCommitError("prepared outcome session mismatch")
    commits = []
    for event in events:
        if event.get("event_type") != TURN_OUTCOME_EVENT_TYPE:
            continue
        body = event.get("payload") if prepared.stream == TURN_OUTCOME_STREAM else event.get("expanded_payload")
        if not isinstance(body, Mapping) or body.get("provenance") != prepared.provenance.to_record():
            continue
        if body != payload or event.get("source") != TURN_OUTCOME_SOURCE:
            raise TurnOutcomeCommitError("committed outcome payload conflicts with preparation")
        metadata = event.get("metadata", {})
        commit_idempotency = (
            event.get("idempotency_key")
            if prepared.stream == TURN_OUTCOME_PAYLOAD_STREAM
            else metadata.get("idempotency_key")
        )
        if commit_idempotency != _commit_key(prepared.provenance, prepared.stream):
            raise TurnOutcomeCommitError("committed outcome idempotency scope mismatch")
        commits.append(event)
    if len(commits) > 1:
        raise TurnOutcomeCommitError("committed outcome is ambiguous")
    if not commits and head != command.expected_head_seq:
        raise TurnOutcomeCommitError("prepared outcome head mismatch")
    # Ordinary strict streams require the original sequence on idempotent
    # replay. Never manufacture a new event or substitute the current head.
    expected_append_seq = (
        (commits[0].get("seq") if prepared.stream == TURN_OUTCOME_STREAM else commits[0].get("global_seq"))
        if commits
        else head + 1
    )
    if type(expected_append_seq) is not int:
        raise TurnOutcomeCommitError("committed outcome actual sequence missing")
    appended = _append_canonical_payload(prepared, payload, expected_next_seq=expected_append_seq)
    committed = _durable_commit_from_payload(payload, appended)
    return RecoveredTurnOutcomeV1(
        committed.outcome,
        committed.receipt,
        committed.sealed_turn,
        json.dumps(payload.get("result_projection", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _execution_from_result(
    *,
    ledger: TurnLedger,
    result: Mapping[str, Any],
) -> ToolBatchExecution | None:
    decision = ledger.final_decision
    if decision is None or decision.tool_batch is None:
        return None
    raw_receipt = result.get("batch_receipt")
    receipt = BatchReceipt.model_validate(raw_receipt) if isinstance(raw_receipt, Mapping) and raw_receipt else None
    batches = list(ledger.invocation_batches)
    captured = (
        [item for batch in batches for item in batch.invocations] if batches else list(decision.tool_batch.invocations)
    )
    by_call = {str(item.call_id): item for item in captured}
    if len(by_call) != len(captured):
        raise TurnOutcomeCommitError("committed_tool_invocation_identity_collision")
    # Missing evidence cannot erase attempted mutations or call inputs.
    invocations = list(captured) if receipt is None else []
    seen: set[str] = set()
    for row in receipt.results if receipt is not None else []:
        call_id = str(row.call_id)
        invocation = by_call.get(call_id)
        if not call_id or call_id in seen or invocation is None or invocation.tool_name != row.tool_name:
            raise TurnOutcomeCommitError("committed_tool_receipt_invocation_mismatch")
        seen.add(call_id)
        invocations.append(invocation)
    if receipt is not None and seen != set(by_call):
        raise TurnOutcomeCommitError("committed_tool_receipt_invocation_mismatch")
    effect_types = {invocation.effect_type for invocation in invocations}
    side_effect_class: Literal["readonly", "local_write", "external_write"]
    if ToolEffectType.ASYNC in effect_types:
        side_effect_class = "external_write"
    elif ToolEffectType.WRITE in effect_types:
        side_effect_class = "local_write"
    else:
        side_effect_class = "readonly"
    return ToolBatchExecution(
        batch_id=receipt.batch_id if receipt is not None else decision.tool_batch.batch_id,
        invocations=invocations,
        receipt=receipt,
        invocation_batches=batches,
        side_effect_class=side_effect_class,
    )


def _reject_unscoped_legacy_transition(provenance: FactStreamProvenanceV1) -> None:
    """Fail closed when a v1 fact cannot prove transition-level equivalence."""

    offset = 0
    while True:
        result = query_fact_events(
            QueryFactEventsV1(
                workspace=provenance.workspace,
                stream=TURN_OUTCOME_STREAM,
                limit=1000,
                offset=offset,
                run_id=provenance.run_id,
                task_id=provenance.task_id,
            )
        )
        for event in result.events:
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if (
                payload.get("schema_version") == "roles.kernel.turn_outcome_fact.v1"
                and str(payload.get("turn_id") or "").strip() == provenance.turn_id
            ):
                raise TurnOutcomeCommitError(
                    "legacy turn outcome lacks transition provenance; explicit migration is required "
                    f"run_id={provenance.run_id!r} task_id={provenance.task_id!r} turn_id={provenance.turn_id!r}"
                )
        if result.next_offset == 0:
            return
        offset = result.next_offset


def _closing_from_result(
    *,
    ledger: TurnLedger,
    result: Mapping[str, Any],
    execution: ToolBatchExecution | None,
) -> FinalizationRecord | None:
    decision = ledger.final_decision
    if decision is None:
        return None
    raw_finalization = result.get("finalization")
    finalization = dict(raw_finalization) if isinstance(raw_finalization, Mapping) else {}
    raw_mode = str(finalization.get("mode") or decision.finalize_mode.value).strip().lower()
    try:
        mode = FinalizeMode(raw_mode)
    except ValueError:
        mode = decision.finalize_mode
    visible_message = str(
        finalization.get("final_visible_message") or result.get("visible_content") or decision.visible_message or ""
    )
    return FinalizationRecord(
        mode=mode,
        final_visible_message=visible_message,
        closed_without_tools=execution is None,
    )


def _failure_class_for(
    *,
    status: str,
    result_kind: str,
    has_decision: bool,
) -> TurnFailureClass | None:
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "suspended":
        return TurnFailureClass.CANCELLATION
    if normalized_status in {"failed", "error"}:
        if result_kind in {"inline_patch_escape_blocked", "mutation_bypass_blocked"}:
            return TurnFailureClass.POLICY_FAILURE
        return TurnFailureClass.RUNTIME_FAILURE
    if not has_decision:
        return TurnFailureClass.CONTRACT_VIOLATION
    return None


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "TURN_OUTCOME_EVENT_TYPE",
    "TURN_OUTCOME_SOURCE",
    "TURN_OUTCOME_STREAM",
    "DurableTurnCommit",
    "TurnOutcomeCommitError",
    "commit_turn_result",
]
