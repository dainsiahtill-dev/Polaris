"""Phase-2/3 decision request + decode pipeline for the turn kernel.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Extracted verbatim (behavior-preserving) from
``TurnTransactionController._execute_turn`` Phases 2 and 3 per the REMAINING_06
decomposition blueprint (step 4). Owns:

* Phase 2 — DECISION_REQUESTED transition + the LLM decision call, including the
  ADR-0090 I3 single corrective re-ask before a degraded decode kills the turn.
* Phase 3 — decode, PROPOSE_PATCH/ANALYZE_ONLY write-tool filter, the
  text-only-tool-batch suppression, ``record_decision``, the single-decision
  guard assertion, and the DECISION_DECODED transition / ``decision_completed``
  event emission.

ADR-0071 note: this preserves the exact commit-point ordering — the LLM call,
``record_decision``, ``guard_assert_single_decision``, and the state-history
appends are NOT reordered. Facade-bound helpers (decoder, LLM caller, delivery
filter, guard assert, event emit) are injected as callables/objects so
monkeypatching the facade still penetrates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from polaris.cells.roles.kernel.internal.transaction.decode_corrective import (
    build_corrective_context,
    evaluate_decode_corrective,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.turn_decision_decoder import TurnDecisionDecoder
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import TurnEvent, TurnPhaseEvent

logger = logging.getLogger(__name__)


def _native_tool_call_count(response: RawLLMResponse) -> int:
    native_calls = getattr(response, "native_tool_calls", None)
    return len(native_calls) if isinstance(native_calls, list) else 0


def _provider_response_hash(response: RawLLMResponse) -> str:
    payload = {
        "content": getattr(response, "content", ""),
        "model": getattr(response, "model", ""),
        "native_tool_calls": getattr(response, "native_tool_calls", []),
        "thinking": getattr(response, "thinking", None),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _with_decision_metadata(decision: TurnDecision, metadata: dict[str, Any]) -> TurnDecision:
    required_fields = (
        "turn_id",
        "kind",
        "visible_message",
        "finalize_mode",
        "domain",
    )
    if not isinstance(decision, dict) or not all(field in decision for field in required_fields):
        partial_decision = dict(decision)
        partial_decision["metadata"] = metadata
        return cast(TurnDecision, partial_decision)
    return TurnDecision(
        turn_id=decision["turn_id"],
        kind=decision["kind"],
        visible_message=decision["visible_message"],
        reasoning_summary=decision.get("reasoning_summary"),
        tool_batch=decision.get("tool_batch"),
        finalize_mode=decision["finalize_mode"],
        domain=decision["domain"],
        metadata=metadata,
    )


async def run_decision_pipeline(
    *,
    turn_id: str,
    context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    state_machine: TurnStateMachine,
    ledger: TurnLedger,
    decoder: TurnDecisionDecoder,
    call_llm_for_decision: Callable[
        [list[dict[str, Any]], list[dict[str, Any]], TurnLedger], Awaitable[RawLLMResponse]
    ],
    apply_delivery_mode_filter: Callable[[TurnDecision, TurnLedger], TurnDecision],
    guard_assert_single_decision: Callable[..., None],
    emit_event: Callable[[TurnEvent], None],
) -> TurnDecision:
    """Run Phase 2 (request + corrective re-ask) and Phase 3 (decode) of a turn.

    Returns the final, recorded ``TurnDecision`` ready for Phase-4 dispatch.
    """
    # === Phase 2: 请求决策 ===
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    ledger.state_history.append(("DECISION_REQUESTED", int(time.time() * 1000)))
    logger.debug("[DEBUG] turn_phase: turn_id=%s phase=DECISION_REQUESTED", turn_id)
    emit_event(TurnPhaseEvent.create(turn_id, "decision_requested"))

    llm_response = await call_llm_for_decision(context, tool_definitions, ledger)

    # ADR-0090 I3: one corrective re-ask before a degraded decode kills the
    # turn (all native tool calls unparseable, or a fully empty response).
    probe_decision = decoder.decode(llm_response, TurnId(turn_id))
    corrective_ask = evaluate_decode_corrective(
        probe_decision,
        llm_response,
        tool_definitions=tool_definitions,
    )
    if corrective_ask is not None:
        logger.warning(
            "decode_corrective_retry: reason=%s turn_id=%s",
            corrective_ask.reason,
            turn_id,
        )
        llm_response = await call_llm_for_decision(
            build_corrective_context(context, corrective_ask),
            tool_definitions,
            ledger,
        )

    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    ledger.state_history.append(("DECISION_RECEIVED", int(time.time() * 1000)))
    logger.debug("[DEBUG] turn_phase: turn_id=%s phase=DECISION_RECEIVED", turn_id)

    # === Phase 3: 解码决策 ===
    decision = probe_decision if corrective_ask is None else decoder.decode(llm_response, TurnId(turn_id))
    native_tool_call_count = _native_tool_call_count(llm_response)
    decision_metadata = dict(decision.get("metadata") or {})
    decision_metadata.setdefault("provider_response_hash", _provider_response_hash(llm_response))
    decision_metadata.setdefault("native_tool_calls_count", native_tool_call_count)
    decision = _with_decision_metadata(decision, decision_metadata)
    if native_tool_call_count > 0 and tool_definitions and not decision.get("tool_batch"):
        ledger.anomaly_flags.append(
            {
                "type": "TOOL_DISPATCH_DROPPED",
                "turn_id": turn_id,
                "native_tool_calls_count": native_tool_call_count,
                "provider_response_hash": decision_metadata.get("provider_response_hash"),
                "reason": "provider_emitted_tool_calls_but_no_decoded_tool_batch",
            }
        )
        raise RuntimeError(
            "tool_dispatch_dropped: provider emitted "
            f"{native_tool_call_count} tool call(s), but no executable tool batch was decoded"
        )

    # PROPOSE_PATCH / ANALYZE_ONLY 边界保护：过滤 write tools
    decision = apply_delivery_mode_filter(decision, ledger)
    allowed_tool_names_for_turn = extract_allowed_tool_names_from_definitions(tool_definitions)
    if decision.get("kind") == TurnDecisionKind.TOOL_BATCH and not allowed_tool_names_for_turn:
        logger.warning(
            "text-only-tool-batch-suppressed: turn_id=%s no tool definitions were exposed; "
            "treating decoded tool call text as final answer",
            turn_id,
        )
        ledger.anomaly_flags.append(
            {
                "type": "TEXT_ONLY_TOOL_BATCH_SUPPRESSED",
                "turn_id": turn_id,
                "reason": "no_tool_definitions_exposed",
            }
        )
        decision = TurnDecision(
            turn_id=decision["turn_id"],
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message=str(decision.get("visible_message") or llm_response.content or ""),
            reasoning_summary=decision.get("reasoning_summary"),
            tool_batch=None,
            finalize_mode=decision["finalize_mode"],
            domain=decision["domain"],
            metadata={
                **dict(decision.get("metadata") or {}),
                "suppressed_tool_batch_due_to_no_tools": True,
            },
        )

    ledger.record_decision(decision)
    guard_assert_single_decision(
        turn_id=turn_id,
        decision_count=len(ledger.decisions),
        tool_batch_count=ledger.tool_batch_count,
        ledger=ledger,
    )

    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger.state_history.append(("DECISION_DECODED", int(time.time() * 1000)))
    decision_kind_str = (
        decision.get("kind").value if hasattr(decision.get("kind"), "value") else str(decision.get("kind"))
    )
    logger.debug(
        "[DEBUG] turn_phase: turn_id=%s phase=DECISION_DECODED kind=%s",
        turn_id,
        decision_kind_str,
    )
    emit_event(
        TurnPhaseEvent.create(
            turn_id,
            "decision_completed",
            {
                "kind": decision_kind_str,
                "finalize_mode": decision.get("finalize_mode").value
                if hasattr(decision.get("finalize_mode"), "value")
                else str(decision.get("finalize_mode")),
            },
        )
    )

    return decision
