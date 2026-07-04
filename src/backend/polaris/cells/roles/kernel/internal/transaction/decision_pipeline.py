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
appends are NOT reordered. Controller-bound collaborators (decoder, LLM caller,
delivery filter, guard assert, event emit) are injected as callables/objects so
existing controller-level test seams still penetrate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public import (
    build_tool_call_lifecycle_receipt,
    failure_evidence_from_lifecycle_receipt,
)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    build_native_tool_call_envelope_payloads,
    native_tool_call_count as derive_native_tool_call_count,
    native_tool_call_envelopes_from_metadata,
    native_tool_call_facts_from_response as derive_native_tool_call_facts_from_response,
    native_tool_calls_from_response,
    project_native_tool_call_facts_to_metadata,
)
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


def _native_tool_call_count(response: RawLLMResponse, metadata: Mapping[str, Any] | None = None) -> int:
    return derive_native_tool_call_count(metadata, _native_tool_calls_from_response(response))


def _native_tool_call_facts(response: RawLLMResponse, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return derive_native_tool_call_facts_from_response(response, metadata)


def _native_tool_calls_from_response(response: Any) -> list[dict[str, Any]]:
    return native_tool_calls_from_response(response)


def _provider_response_hash(response: RawLLMResponse, metadata: Mapping[str, Any] | None = None) -> str:
    payload = {
        "content": getattr(response, "content", ""),
        "model": getattr(response, "model", ""),
        "native_tool_call_envelopes": native_tool_call_envelopes_from_metadata(metadata),
        "native_tool_calls": _native_tool_calls_from_response(response),
        "thinking": getattr(response, "thinking", None),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _native_tool_call_provider(metadata: Mapping[str, Any]) -> str:
    for key in ("tool_call_provider", "decision_caller_tool_call_provider", "provider", "provider_id"):
        token = str(metadata.get(key) or "").strip().lower()
        if token:
            return token
    return "auto"


def _native_tool_call_envelopes_for_anomaly(
    response: RawLLMResponse,
    metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metadata_envelopes = [dict(item) for item in native_tool_call_envelopes_from_metadata(metadata)]
    if metadata_envelopes:
        return metadata_envelopes
    raw_calls = _native_tool_calls_from_response(response)
    if not raw_calls:
        return []
    return build_native_tool_call_envelope_payloads(raw_calls, provider=_native_tool_call_provider(metadata))


def build_tool_dispatch_dropped_anomaly(
    *,
    response: RawLLMResponse,
    metadata: Mapping[str, Any],
    turn_id: str,
    streaming: bool = False,
) -> dict[str, Any]:
    """Build the canonical anomaly + lifecycle receipt for dropped tool calls."""

    native_count = _native_tool_call_count(response, metadata)
    provider_response_hash = _provider_response_hash(response, metadata)
    native_envelopes = _native_tool_call_envelopes_for_anomaly(response, metadata)
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=str(metadata.get("run_id") or ""),
        task_id=str(metadata.get("task_id") or ""),
        turn_id=turn_id,
        role=str(metadata.get("role") or ""),
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_count,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=native_envelopes,
        dispatch_status="dropped",
        failure_class="TOOL_DISPATCH_DROPPED",
        reason="provider_emitted_tool_calls_but_no_decoded_tool_batch",
    ).to_dict()
    anomaly = {
        "type": "TOOL_DISPATCH_DROPPED",
        "turn_id": turn_id,
        "native_tool_calls_count": lifecycle["native_tool_calls_count"],
        "native_tool_call_envelopes": lifecycle["native_tool_call_envelope_refs"],
        "provider_response_hash": lifecycle["provider_response_hash"],
        "reason": lifecycle["reason"],
        "dropped_tool_calls": lifecycle["dropped_tool_calls"],
        "tool_call_lifecycle_receipt": lifecycle,
    }
    failure_evidence = failure_evidence_from_lifecycle_receipt(lifecycle)
    if failure_evidence:
        anomaly["failure_evidence"] = [failure_evidence]
    if streaming:
        anomaly["streaming"] = True
    return anomaly


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


def _project_native_tool_call_facts(metadata: dict[str, Any], facts: Mapping[str, Any]) -> None:
    """Project canonical native tool-call facts over legacy top-level aliases."""

    project_native_tool_call_facts_to_metadata(metadata, facts)


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
    decision_metadata = dict(decision.get("metadata") or {})
    native_tool_call_facts = _native_tool_call_facts(llm_response, decision_metadata)
    native_tool_call_count = int(native_tool_call_facts.get("native_tool_calls_count") or 0)
    decision_metadata.setdefault("provider_response_hash", _provider_response_hash(llm_response, decision_metadata))
    _project_native_tool_call_facts(decision_metadata, native_tool_call_facts)
    decision = _with_decision_metadata(decision, decision_metadata)
    if native_tool_call_count > 0 and tool_definitions and not decision.get("tool_batch"):
        ledger.anomaly_flags.append(
            build_tool_dispatch_dropped_anomaly(response=llm_response, metadata=decision_metadata, turn_id=turn_id)
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
                "native_tool_calls_count": native_tool_call_count,
                "decode_failure_count": len(decision_metadata.get("decode_failures") or []),
                "provider_response_hash": decision_metadata.get("provider_response_hash", ""),
            },
        )
    )

    return decision
