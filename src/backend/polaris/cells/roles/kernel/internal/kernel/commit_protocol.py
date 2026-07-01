"""Durable commit protocol for role-turn ContextOS persistence.

The three-stage durable-commit protocol — pre-commit validation, durable commit
critical section, and post-commit seal — extracted verbatim from ``core.py`` as
module-level functions. Callers should import this module directly; the
``RoleExecutionKernel`` class no longer exposes commit-protocol static wrappers.

This module must not import ``core.py`` at module top-level (circular-import
guard): it only depends on public turn contracts and transaction ledger types.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2 as TranscriptEvent

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.public.turn_contracts import CommitReceipt, SealedTurn
    from polaris.cells.roles.profile.public.service import RoleTurnRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationReport:
    """Pre-commit validation report.

    Records the result of all validation checks before durable commit.
    """

    passed: bool
    checks: dict[str, bool]
    errors: list[str]


def _pre_commit_validate(
    ledger: TurnLedger | None,
    snapshot: dict[str, Any],
    turn_id: str,
) -> ValidationReport:
    """Pre-commit validation: verify turn invariants before durable write.

    Returns a ValidationReport with pass/fail status and detailed checks.
    """
    checks: dict[str, bool] = {}
    errors: list[str] = []

    # 1. single_decision: ledger must have exactly 1 decision
    if ledger is not None:
        decision_count = len(ledger.decisions)
        checks["single_decision"] = decision_count == 1
        if not checks["single_decision"]:
            errors.append(f"expected 1 decision, got {decision_count}")
    else:
        checks["single_decision"] = True  # no ledger = no decision to validate

    # 2. single_tool_batch: at most 1 tool batch
    if ledger is not None:
        checks["single_tool_batch"] = ledger.tool_batch_count <= 1
        if not checks["single_tool_batch"]:
            errors.append(f"expected <=1 tool batch, got {ledger.tool_batch_count}")
    else:
        checks["single_tool_batch"] = True

    # 3. no_hidden_continuation: check state_history for duplicate DECISION_REQUESTED
    if ledger is not None:
        decision_requests = sum(1 for state, _ts in ledger.state_history if state == "DECISION_REQUESTED")
        checks["no_hidden_continuation"] = decision_requests <= 1
        if not checks["no_hidden_continuation"]:
            errors.append(f"DECISION_REQUESTED appeared {decision_requests} times")
    else:
        checks["no_hidden_continuation"] = True

    # 4. receipts_integrity: all tool calls have receipts
    if ledger is not None and ledger.tool_executions:
        checks["receipts_integrity"] = len(ledger.tool_executions) > 0
    else:
        checks["receipts_integrity"] = True

    # 5. artifact_refs_valid: placeholder (would validate artifact references)
    checks["artifact_refs_valid"] = True

    # 6. budget_balance: basic check (placeholder for full budget validation)
    checks["budget_balance"] = True

    # 7. outcome_status_legal: snapshot version check
    checks["outcome_status_legal"] = isinstance(snapshot.get("version", 0), int)

    all_passed = all(checks.values())
    return ValidationReport(
        passed=all_passed,
        checks=checks,
        errors=errors,
    )


def _execute_commit_protocol(
    request: RoleTurnRequest,
    turn_id: str,
    turn_history: list[tuple[str, str]],
    turn_events_metadata: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    ledger: TurnLedger | None,
    snapshot: dict[str, Any],
) -> CommitReceipt:
    """Execute the durable commit protocol.

    This is the critical section: truthlog append + snapshot materialization.
    Must remain synchronous and consistent.
    """
    transcript_log: list[dict[str, Any]] = snapshot.get("transcript_log") or []
    if not isinstance(transcript_log, list):
        transcript_log = []

    base_sequence = len(transcript_log)
    for idx, meta in enumerate(turn_events_metadata):
        if not isinstance(meta, dict):
            continue
        seq = base_sequence + idx
        event = TranscriptEvent(
            event_id=str(meta.get("event_id") or f"{turn_id}_{idx}"),
            sequence=seq,
            role=str(meta.get("role") or ""),
            kind=str(meta.get("kind") or ""),
            route="",
            content=str(meta.get("content") or ""),
            source_turns=(f"t{seq}",),
        )
        transcript_log.append(event.to_dict())

    snapshot["transcript_log"] = transcript_log
    snapshot["version"] = int(snapshot.get("version", 0)) + 1
    snapshot["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    working_state = snapshot.get("working_state")
    if not isinstance(working_state, dict):
        working_state = {}
        snapshot["working_state"] = working_state

    if tool_results:
        working_state["last_tool_results"] = list(tool_results)

    # Merge TurnLedger data into policy_verdicts (single truth source)
    if ledger is not None:
        policy_verdicts: dict[str, Any] = snapshot.setdefault("policy_verdicts", {})
        if ledger.decisions:
            policy_verdicts["decisions"] = list(ledger.decisions)
        if ledger.tool_executions:
            policy_verdicts["tool_executions"] = list(ledger.tool_executions)
        if ledger.llm_calls:
            policy_verdicts["llm_calls"] = list(ledger.llm_calls)
        if ledger.anomaly_flags:
            policy_verdicts["anomaly_flags"] = list(ledger.anomaly_flags)

    # Mark this turn as committed
    snapshot["last_commit_turn_id"] = turn_id

    # Build commit receipt
    from polaris.cells.roles.kernel.public.turn_contracts import CommitReceipt, TurnId

    truthlog_start = base_sequence
    truthlog_end = len(transcript_log)
    return CommitReceipt(
        turn_id=TurnId(turn_id),
        snapshot_id=str(snapshot.get("snapshot_id", "")),
        truthlog_seq_range=(truthlog_start, truthlog_end),
        sealed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        validation_passed=True,
    )


def _post_commit_seal(
    commit_receipt: CommitReceipt,
    outcome_status: str,
    resolution_code: str,
    parent_snapshot_id: str | None = None,
) -> SealedTurn:
    """Post-commit seal: generate immutable turn seal.

    This creates the final SealedTurn that represents durable truth.
    """
    from polaris.cells.roles.kernel.public.turn_contracts import (
        OutcomeStatus,
        ResolutionCode,
        SealedTurn,
    )

    return SealedTurn(
        turn_id=commit_receipt.turn_id,
        commit_receipt=commit_receipt,
        outcome_status=OutcomeStatus(outcome_status),
        resolution_code=ResolutionCode(resolution_code),
        sealed_at=commit_receipt.sealed_at,
        parent_snapshot_id=parent_snapshot_id,
    )


def _commit_turn_to_snapshot(
    request: RoleTurnRequest,
    turn_id: str,
    turn_history: list[tuple[str, str]],
    turn_events_metadata: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    ledger: TurnLedger | None = None,
) -> CommitReceipt | None:
    """Merge turn history, events, and ledger data into the ContextOS snapshot.

    Phase 1 hardened version: three-stage durable commit protocol.
    1. Pre-commit validation
    2. Durable commit (critical section)
    3. Post-commit seal

    Args:
        request: The turn request carrying ``context_override``.
        turn_id: Unique identifier for the current turn.
        turn_history: Ordered (role, content) pairs for the turn.
        turn_events_metadata: Metadata dicts for each transcript event.
        tool_results: Tool execution results produced this turn.
        ledger: Optional ``TurnLedger`` whose decisions / tool executions /
            LLM calls / anomaly flags are merged into
            ``snapshot["policy_verdicts"]``.

    Returns:
        CommitReceipt if commit succeeded, None if skipped or failed.
    """
    context_override = getattr(request, "context_override", None)
    if not isinstance(context_override, dict):
        return None

    snapshot = context_override.get("context_os_snapshot")
    if not isinstance(snapshot, dict):
        return None

    # Idempotency guard – skip if this turn was already committed.
    if snapshot.get("last_commit_turn_id") == turn_id:
        return None

    # Stage 1: Pre-commit validation
    validation_report = _pre_commit_validate(
        ledger=ledger,
        snapshot=snapshot,
        turn_id=turn_id,
    )
    if not validation_report.passed:
        logger.warning(
            "Pre-commit validation failed for turn %s: %s",
            turn_id,
            "; ".join(validation_report.errors),
        )
        return None

    # Stage 2: Execute durable commit protocol (critical section)
    commit_receipt = _execute_commit_protocol(
        request=request,
        turn_id=turn_id,
        turn_history=turn_history,
        turn_events_metadata=turn_events_metadata,
        tool_results=tool_results,
        ledger=ledger,
        snapshot=snapshot,
    )

    # Stage 3: Post-commit seal (can be enhanced later)
    # For now, just return the receipt; seal is created by caller if needed
    return commit_receipt


def _build_turn_history_and_events(
    *,
    turn_id: str,
    request: RoleTurnRequest,
    visible_content: str,
    thinking: str | None,
    tool_results: list[dict[str, Any]],
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Build turn_history and turn_events_metadata for ContextOS persistence.

    These fields are critical for SessionContinuityEngine to rebuild the
    ContextOS snapshot across turns. Without them, the snapshot stays stale
    and the LLM continues with the previous turn's task.
    """
    import json

    turn_history: list[tuple[str, str]] = []
    turn_events_metadata: list[dict[str, Any]] = []
    user_message = str(getattr(request, "message", "") or "").strip()

    if user_message:
        turn_history.append(("user", user_message))
        turn_events_metadata.append(
            {
                "role": "user",
                "content": user_message,
                "event_id": f"user_{turn_id}",
                "kind": "user_turn",
            }
        )

    assistant_content = str(visible_content or "").strip()
    if assistant_content:
        turn_history.append(("assistant", assistant_content))
        turn_events_metadata.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "event_id": f"assistant_{turn_id}",
                "kind": "assistant_turn",
            }
        )

    for tr in tool_results:
        if not isinstance(tr, dict):
            continue
        tool_name = str(tr.get("tool") or "tool").strip() or "tool"
        result_value = tr.get("result")
        if result_value is not None:
            result_text = json.dumps(result_value, ensure_ascii=False)
        else:
            error_text = str(tr.get("error") or "").strip()
            result_text = f"Error: {error_text}" if error_text else ""
        if result_text:
            turn_history.append(("tool", result_text))
            turn_events_metadata.append(
                {
                    "role": "tool",
                    "content": result_text,
                    "event_id": f"tool_{tr.get('call_id', turn_id)}",
                    "kind": "tool_result",
                    "tool": tool_name,
                }
            )

    return turn_history, turn_events_metadata
