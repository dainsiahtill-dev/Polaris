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

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public import (
    build_tool_dispatch_dropped_anomaly_from_sources,
    native_tool_call_facts_from_sources,
    native_tool_call_names_from_facts,
    project_native_tool_call_facts_to_metadata,
    tool_dispatch_dropped_error_message,
    tool_dispatch_dropped_guard_applies,
)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    native_tool_call_envelopes_from_response,
    native_tool_calls_from_response,
    provider_response_hash,
)
from polaris.cells.roles.kernel.internal.transaction.decode_corrective import (
    build_corrective_context,
    evaluate_decode_corrective,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.internal.turn_decision_decoder import TurnDecisionDecoder
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import TurnEvent, TurnPhaseEvent
from polaris.kernelone.tools.tool_kinds import is_write_tool_name

logger = logging.getLogger(__name__)


def build_tool_dispatch_dropped_anomaly(
    *,
    response: RawLLMResponse,
    metadata: Mapping[str, Any],
    turn_id: str,
    streaming: bool = False,
) -> dict[str, Any]:
    """Build the canonical anomaly + lifecycle receipt for dropped tool calls."""

    native_tool_calls = native_tool_calls_from_response(response)
    response_hash = provider_response_hash(response, metadata)
    native_envelopes = native_tool_call_envelopes_from_response(response, metadata)
    return build_tool_dispatch_dropped_anomaly_from_sources(
        run_id=str(metadata.get("run_id") or ""),
        task_id=str(metadata.get("task_id") or ""),
        turn_id=turn_id,
        role=str(metadata.get("role") or ""),
        provider_response_hash=response_hash,
        metadata=metadata,
        native_tool_calls=native_tool_calls,
        native_tool_call_envelopes=native_envelopes,
        streaming=streaming,
        reason="provider_emitted_tool_calls_but_no_decoded_tool_batch",
    )


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


def _suppressed_tool_batch_tool_refs(decision: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return audit-safe references for decoded tool calls suppressed by policy."""

    tool_batch = decision.get("tool_batch")
    invocations = (
        tool_batch.get("invocations") if isinstance(tool_batch, Mapping) else getattr(tool_batch, "invocations", None)
    )
    if not isinstance(invocations, list):
        return []

    suppressed_tool_calls: list[dict[str, str]] = []
    for invocation in invocations:
        tool_ref = tool_invocation_audit_ref(invocation, reason="no_tool_definitions_exposed")
        if not tool_ref.get("tool_name"):
            continue
        suppressed_tool_calls.append(tool_ref)
    return suppressed_tool_calls


def _decision_has_executable_tool_batch(decision: Any) -> bool:
    """Return True when the decision carries at least one tool invocation."""

    if decision is None:
        return False
    tool_batch = decision.get("tool_batch") if isinstance(decision, Mapping) else getattr(decision, "tool_batch", None)
    if tool_batch is None:
        return False
    if isinstance(tool_batch, Mapping):
        invocations = tool_batch.get("invocations")
        serial_writes = tool_batch.get("serial_writes")
    else:
        invocations = getattr(tool_batch, "invocations", None)
        serial_writes = getattr(tool_batch, "serial_writes", None)
    for candidate in (invocations, serial_writes):
        if candidate is None:
            continue
        try:
            if len(list(candidate)) > 0:
                return True
        except TypeError:
            continue
    return False


def _native_facts_include_write_tools(
    native_tool_call_facts: Mapping[str, Any] | None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when native facts/envelopes name at least one write tool."""

    from polaris.cells.control_plane.run_ledger.public import native_tool_call_envelope_refs_from_metadata

    names = native_tool_call_names_from_facts(native_tool_call_facts)
    if any(is_write_tool_name(name) for name in names):
        return True
    if metadata is not None:
        for envelope in native_tool_call_envelope_refs_from_metadata(metadata):
            if is_write_tool_name(str(envelope.get("tool_name") or "")):
                return True
    return False


def ensure_native_write_tool_batch_or_fail(
    *,
    decision: TurnDecision,
    llm_response: RawLLMResponse,
    decoder: TurnDecisionDecoder,
    turn_id: str,
    decision_metadata: Mapping[str, Any],
    streaming: bool = False,
) -> TurnDecision:
    """Recover an executable TOOL_BATCH for native writes, else fail-closed.

    R134 boundary:
        Call after any transform that can clear ``tool_batch`` (delivery-mode
        filter, text-only suppression, premature FINAL_ANSWER). Provider-native
        write tools must either become an executable batch or raise
        ``tool_dispatch_dropped`` before process terminal so Run Ledger never
        sees claimed materialization without dispatch evidence.

    Complexity:
        O(n) over native tool calls for recovery decode; O(1) for the guard.
    """

    if _decision_has_executable_tool_batch(decision):
        return decision

    recovered = decoder.recover_executable_tool_batch_decision(llm_response, TurnId(turn_id))
    if recovered is not None and _decision_has_executable_tool_batch(recovered):
        recovered_metadata = dict(recovered.get("metadata") or {})
        recovered_metadata.update(
            {
                key: value
                for key, value in dict(decision_metadata).items()
                if key not in recovered_metadata or key in {"run_id", "task_id", "role", "provider_response_hash"}
            }
        )
        recovered_metadata["r134_recovered_tool_batch"] = True
        project_native_tool_call_facts_to_metadata(
            recovered_metadata,
            native_tool_call_facts_from_sources(
                recovered_metadata,
                native_tool_calls_from_response(llm_response),
            ),
        )
        logger.warning(
            "r134_native_write_tool_batch_recovered: turn_id=%s tool_count=%s",
            turn_id,
            recovered_metadata.get("tool_count"),
        )
        return _with_decision_metadata(recovered, recovered_metadata)

    merged_metadata = dict(decision_metadata)
    decision_meta = decision.get("metadata") if isinstance(decision, Mapping) else None
    if isinstance(decision_meta, Mapping):
        merged_metadata = {**merged_metadata, **dict(decision_meta)}
    native_tool_call_facts = native_tool_call_facts_from_sources(
        merged_metadata,
        native_tool_calls_from_response(llm_response),
    )
    has_native = int(native_tool_call_facts.get("native_tool_calls_count") or 0) > 0 or bool(
        native_tool_call_names_from_facts(native_tool_call_facts)
    )
    has_write = _native_facts_include_write_tools(native_tool_call_facts, metadata=merged_metadata)
    # R134: undischarged native write tools must never reach process terminal.
    # Any remaining native tools without a batch also fail-closed (definitions
    # treated present so empty tool-surface cannot silently swallow provider calls).
    if has_write or (
        has_native
        and tool_dispatch_dropped_guard_applies(
            native_tool_call_facts=native_tool_call_facts,
            tool_definitions_present=True,
            decoded_tool_batch_present=False,
        )
    ):
        anomaly = build_tool_dispatch_dropped_anomaly(
            response=llm_response,
            metadata=merged_metadata,
            turn_id=turn_id,
            streaming=streaming,
        )
        raise RuntimeError(tool_dispatch_dropped_error_message(anomaly))
    return decision


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
    native_tool_call_facts = native_tool_call_facts_from_sources(
        decision_metadata,
        native_tool_calls_from_response(llm_response),
    )
    decision_metadata.setdefault("provider_response_hash", provider_response_hash(llm_response, decision_metadata))
    project_native_tool_call_facts_to_metadata(decision_metadata, native_tool_call_facts)
    decision = _with_decision_metadata(decision, decision_metadata)
    if tool_dispatch_dropped_guard_applies(
        native_tool_call_facts=native_tool_call_facts,
        tool_definitions_present=bool(tool_definitions),
        decoded_tool_batch_present=bool(decision.get("tool_batch")),
    ):
        anomaly = build_tool_dispatch_dropped_anomaly(
            response=llm_response,
            metadata=decision_metadata,
            turn_id=turn_id,
        )
        ledger.anomaly_flags.append(anomaly)
        raise RuntimeError(tool_dispatch_dropped_error_message(anomaly))

    # PROPOSE_PATCH / ANALYZE_ONLY 边界保护：过滤 write tools
    decision = apply_delivery_mode_filter(decision, ledger)
    allowed_tool_names_for_turn = extract_allowed_tool_names_from_definitions(tool_definitions)
    if decision.get("kind") == TurnDecisionKind.TOOL_BATCH and not allowed_tool_names_for_turn:
        suppressed_tool_calls = _suppressed_tool_batch_tool_refs(decision)
        write_suppressed = any(is_write_tool_name(str(item.get("tool_name") or "")) for item in suppressed_tool_calls)
        if write_suppressed:
            # R134: never silently convert write tool batches into final answers.
            anomaly = build_tool_dispatch_dropped_anomaly(
                response=llm_response,
                metadata=decision_metadata,
                turn_id=turn_id,
            )
            ledger.anomaly_flags.append(anomaly)
            raise RuntimeError(tool_dispatch_dropped_error_message(anomaly))
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
                "suppressed_tool_calls": suppressed_tool_calls,
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
                "suppressed_tool_calls": suppressed_tool_calls,
            },
        )

    # R134: recover or fail-closed after transforms that may have cleared tool_batch.
    try:
        decision = ensure_native_write_tool_batch_or_fail(
            decision=decision,
            llm_response=llm_response,
            decoder=decoder,
            turn_id=turn_id,
            decision_metadata=decision_metadata,
            streaming=False,
        )
    except RuntimeError as exc:
        if "tool_dispatch_dropped" in str(exc):
            anomaly = build_tool_dispatch_dropped_anomaly(
                response=llm_response,
                metadata=decision_metadata,
                turn_id=turn_id,
            )
            ledger.anomaly_flags.append(anomaly)
        raise

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
    decision_completed_metadata = {
        "decode_failure_count": len(decision_metadata.get("decode_failures") or []),
        "provider_response_hash": decision_metadata.get("provider_response_hash", ""),
    }
    project_native_tool_call_facts_to_metadata(
        decision_completed_metadata,
        native_tool_call_facts,
        project_names=False,
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
                **decision_completed_metadata,
            },
        )
    )

    return decision
