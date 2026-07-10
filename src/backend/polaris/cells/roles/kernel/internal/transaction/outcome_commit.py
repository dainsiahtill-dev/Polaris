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
from typing import Any, Literal

from polaris.cells.events.fact_stream.public import AppendFactEventCommandV1, append_fact_event
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
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

TURN_OUTCOME_STREAM = "roles.kernel.turn_outcomes"
TURN_OUTCOME_EVENT_TYPE = "turn_outcome_committed"
TURN_OUTCOME_SOURCE = "roles.kernel.transaction"


class TurnOutcomeCommitError(RuntimeError):
    """Raised when an authoritative turn outcome cannot be committed."""


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
    ledger: TurnLedger,
    result: Mapping[str, Any] | None,
    completion_status: str,
    failure_reason: str | None = None,
) -> DurableTurnCommit:
    """Build and atomically persist the canonical result for one turn.

    The operation is idempotent by ``(run_id, task_id, turn_id)`` and rejects
    a replay whose payload differs from the already committed outcome.

    Complexity:
        O(e + p) time and O(e + p) memory, where ``e`` is the number of tool
        executions and ``p`` is the serialized outcome size. The underlying
        JSONL store currently performs an O(n) idempotency lookup per stream.
    """

    normalized_workspace = str(workspace or "").strip()
    normalized_run_id = str(run_id or "").strip()
    normalized_task_id = str(task_id or "").strip()
    if not normalized_workspace or not normalized_run_id or not normalized_task_id:
        raise TurnOutcomeCommitError("workspace, run_id, and task_id are required for durable turn commit")

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
        "schema_version": "roles.kernel.turn_outcome_fact.v1",
        "run_id": normalized_run_id,
        "task_id": normalized_task_id,
        "turn_id": str(ledger.turn_id),
        "outcome_hash": outcome_hash,
        "outcome": outcome_payload,
    }
    idempotency_key = f"{normalized_run_id}:{normalized_task_id}:{ledger.turn_id}"
    try:
        appended = append_fact_event(
            AppendFactEventCommandV1(
                workspace=normalized_workspace,
                stream=TURN_OUTCOME_STREAM,
                event_type=TURN_OUTCOME_EVENT_TYPE,
                payload=event_payload,
                source=TURN_OUTCOME_SOURCE,
                run_id=normalized_run_id,
                task_id=normalized_task_id,
                correlation_id=str(ledger.turn_id),
                idempotency_key=idempotency_key,
            )
        )
    except (RuntimeError, ValueError) as exc:
        raise TurnOutcomeCommitError(f"failed to commit turn outcome: {exc}") from exc

    if appended.appended_seq is None:
        raise TurnOutcomeCommitError("fact stream returned no sequence for committed turn outcome")
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
        outcome_hash=outcome_hash,
    )
    committed_outcome = outcome.model_copy(update={"commit_ref": receipt})
    sealed_turn = SealedTurn(
        turn_id=outcome.turn_id,
        commit_receipt=receipt,
        outcome_status=outcome.outcome_status,
        resolution_code=outcome.resolution_code,
        sealed_at=receipt.sealed_at,
    )
    return DurableTurnCommit(
        outcome=committed_outcome,
        receipt=receipt,
        sealed_turn=sealed_turn,
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
    invocations = list(decision.tool_batch.invocations)
    effect_types = {invocation.effect_type for invocation in invocations}
    side_effect_class: Literal["readonly", "local_write", "external_write"]
    if ToolEffectType.ASYNC in effect_types:
        side_effect_class = "external_write"
    elif ToolEffectType.WRITE in effect_types:
        side_effect_class = "local_write"
    else:
        side_effect_class = "readonly"
    return ToolBatchExecution(
        batch_id=decision.tool_batch.batch_id,
        invocations=invocations,
        receipt=receipt,
        side_effect_class=side_effect_class,
    )


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
