"""WORKFLOW / DEVELOPMENT / ASK_USER 移交处理。

负责各类非直接完成决策的收尾：
- handoff_workflow: 移交 ExplorationWorkflowRuntime
- handoff_development: 移交 DevelopmentWorkflowRuntime
- ask_user: 模型输出为空，等待用户输入
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import TurnDecision, TurnId
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    TurnEvent,
    TurnPhaseEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workflow handoff 上下文构建
# ---------------------------------------------------------------------------


def select_primary_batch_receipt(receipts: list[dict]) -> dict | None:
    """选择用于恢复上下文的主 receipt。"""
    if not receipts:
        return None
    for receipt in receipts:
        if receipt.get("has_pending_async"):
            return receipt
    return receipts[0]


def summarize_batch_receipts(receipts: list[dict]) -> list[dict[str, object]]:
    """压缩 batch receipt 以便放入 handoff 上下文。"""
    summary: list[dict[str, object]] = []
    for receipt in receipts:
        summary.append(
            {
                "batch_id": str(receipt.get("batch_id", "")),
                "success_count": int(receipt.get("success_count", 0)),
                "failure_count": int(receipt.get("failure_count", 0)),
                "pending_async_count": int(receipt.get("pending_async_count", 0)),
                "has_pending_async": bool(receipt.get("has_pending_async", False)),
            }
        )
    return summary


def build_workflow_handoff_context(
    *,
    decision: TurnDecision,
    receipts: list[dict],
    ledger: TurnLedger,
    handoff_reason: str,
    handoff_source: str,
) -> dict:
    """构建可恢复的 workflow handoff 上下文。"""
    metadata = decision.get("metadata", {})
    tool_batch = decision.get("tool_batch") or {}
    primary_receipt = select_primary_batch_receipt(receipts)
    pending_receipts = [
        receipt
        for receipt in receipts
        if receipt.get("has_pending_async") or int(receipt.get("pending_async_count", 0)) > 0
    ]
    turn_id = str(decision.get("turn_id", ""))

    recoverable_context = {
        "turn_id": turn_id,
        "decision": {
            "kind": decision.get("kind").value
            if hasattr(decision.get("kind"), "value")
            else str(decision.get("kind", "")),
            "finalize_mode": decision.get("finalize_mode").value
            if hasattr(decision.get("finalize_mode"), "value")
            else str(decision.get("finalize_mode", "")),
            "metadata": dict(metadata),
        },
        "tool_batch": tool_batch,
        "batch_receipts": receipts,
        "pending_async_receipts": pending_receipts,
        "batch_summary": summarize_batch_receipts(receipts),
        "state_trajectory": [s[0] for s in ledger.state_history],
    }

    return {
        "handoff_reason": handoff_reason,
        "handoff_source": handoff_source,
        "turn_id": turn_id,
        "batch_id": str((primary_receipt or {}).get("batch_id", "")),
        "tool_count": len(tool_batch.get("invocations", [])),
        "pending_async_count": sum(int(receipt.get("pending_async_count", 0)) for receipt in receipts),
        "initial_tools": metadata.get("initial_tools", []),
        "batch_receipt": primary_receipt,
        "batch_receipts": receipts,
        "recoverable_context": recoverable_context,
    }


# ---------------------------------------------------------------------------
# Handoff Handler
# ---------------------------------------------------------------------------


class HandoffHandler:
    """移交处理器 — 处理 workflow / development / ask_user 决策。"""

    def __init__(
        self,
        *,
        workflow_runtime: Any | None = None,
        development_runtime: Any | None = None,
        emit_event: Callable[[TurnEvent], None],
        build_turn_result: Callable[..., dict],
    ) -> None:
        self.workflow_runtime = workflow_runtime
        self.development_runtime = development_runtime
        self.emit_event = emit_event
        self.build_turn_result = build_turn_result

    # --- Workflow handoff (run mode) ---

    async def handle_handoff(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        workflow_context: dict | None = None,
        handoff_reason: str | None = None,
        batch_receipt: dict | None = None,
    ) -> dict:
        turn_id = decision.get("turn_id")
        metadata = decision.get("metadata", {})
        handoff_reason = handoff_reason or metadata.get("handoff_reason", "unknown")
        workflow_context = workflow_context or build_workflow_handoff_context(
            decision=decision,
            receipts=[batch_receipt] if batch_receipt else [],
            ledger=ledger,
            handoff_reason=handoff_reason,
            handoff_source="decision_handoff",
        )

        state_machine.transition_to(TurnState.HANDOFF_WORKFLOW)
        ledger.state_history.append(("HANDOFF_WORKFLOW", int(time.time() * 1000)))
        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "workflow_handoff",
                {
                    "handoff_reason": handoff_reason,
                    "handoff_source": workflow_context.get("handoff_source", "decision_handoff"),
                    "batch_id": workflow_context.get("batch_id", ""),
                    "pending_async_count": workflow_context.get("pending_async_count", 0),
                    "recoverable_context": workflow_context.get("recoverable_context", {}),
                },
            )
        )

        exploration_result_dict: dict[str, Any] | None = None
        if self.workflow_runtime is not None:
            try:
                exploration_result = await self.workflow_runtime.execute(decision, TurnId(str(turn_id)))
                exploration_result_dict = {
                    "turn_id": str(exploration_result.turn_id),
                    "status": exploration_result.status.value,
                    "steps_completed": exploration_result.steps_completed,
                    "discoveries": exploration_result.discoveries,
                    "synthesis": exploration_result.synthesis,
                    "duration_ms": exploration_result.duration_ms,
                    "error": exploration_result.error,
                }
                workflow_context["exploration_result"] = exploration_result_dict
            except Exception as exc:
                logger.exception("ExplorationWorkflowRuntime execution failed during handoff: turn_id=%s", turn_id)
                workflow_context["exploration_error"] = str(exc)

        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        if self.workflow_runtime is not None:
            visible_content = f"[HANDOFF] Complex exploration handed to workflow runtime. Reason: {handoff_reason}"
        else:
            visible_content = f"[HANDOFF] Workflow runtime unavailable. Reason: {handoff_reason}"
        if workflow_context.get("batch_id"):
            visible_content += f" Batch: {workflow_context['batch_id']}"

        self.emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="handoff",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
            )
        )

        return self.build_turn_result(
            turn_id=turn_id,
            kind="handoff_workflow",
            visible_content=visible_content,
            decision=decision,
            batch_receipt=batch_receipt,
            finalization=None,
            ledger=ledger,
            workflow_context=workflow_context,
        )

    # --- Workflow handoff (stream mode) ---

    async def handle_handoff_stream(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        workflow_context: dict | None = None,
        handoff_reason: str | None = None,
        batch_receipt: dict | None = None,
    ) -> AsyncIterator[TurnEvent]:
        turn_id = decision.get("turn_id")
        metadata = decision.get("metadata", {})
        handoff_reason = handoff_reason or metadata.get("handoff_reason", "unknown")
        workflow_context = workflow_context or build_workflow_handoff_context(
            decision=decision,
            receipts=[batch_receipt] if batch_receipt else [],
            ledger=ledger,
            handoff_reason=handoff_reason,
            handoff_source="decision_handoff",
        )

        state_machine.transition_to(TurnState.HANDOFF_WORKFLOW)
        ledger.state_history.append(("HANDOFF_WORKFLOW", int(time.time() * 1000)))
        yield TurnPhaseEvent.create(
            str(turn_id),
            "workflow_handoff",
            {
                "handoff_reason": handoff_reason,
                "handoff_source": workflow_context.get("handoff_source", "decision_handoff"),
                "batch_id": workflow_context.get("batch_id", ""),
                "pending_async_count": workflow_context.get("pending_async_count", 0),
                "recoverable_context": workflow_context.get("recoverable_context", {}),
            },
        )

        if self.workflow_runtime is not None and hasattr(self.workflow_runtime, "execute_stream"):
            try:
                async for event in self.workflow_runtime.execute_stream(decision, TurnId(str(turn_id))):
                    yield event
            except Exception as exc:
                logger.exception("ExplorationWorkflowRuntime stream failed during handoff: turn_id=%s", turn_id)
                yield ErrorEvent(
                    turn_id=str(turn_id),
                    error_type="workflow_stream_error",
                    message=str(exc),
                    state_at_error="HANDOFF_WORKFLOW",
                )
        elif self.workflow_runtime is not None:
            try:
                exploration_result = await self.workflow_runtime.execute(decision, TurnId(str(turn_id)))
                visible_content = exploration_result.synthesis or "[HANDOFF] Exploration completed."
                if visible_content:
                    yield ContentChunkEvent(
                        turn_id=str(turn_id),
                        chunk=visible_content,
                    )
            except Exception as exc:
                logger.exception("ExplorationWorkflowRuntime execution failed during handoff: turn_id=%s", turn_id)
                yield ErrorEvent(
                    turn_id=str(turn_id),
                    error_type="workflow_execution_error",
                    message=str(exc),
                    state_at_error="HANDOFF_WORKFLOW",
                )

        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        yield CompletionEvent(
            turn_id=str(turn_id),
            status="handoff",
            duration_ms=ledger.get_duration_ms(),
            llm_calls=len(ledger.llm_calls),
            tool_calls=len(ledger.tool_executions),
            turn_kind="handoff_workflow",
            visible_content=(
                f"[HANDOFF] Exploration workflow executed. Reason: {handoff_reason}"
                if self.workflow_runtime is not None
                else f"[HANDOFF] Workflow runtime unavailable. Reason: {handoff_reason}"
            ),
        )

    # --- Development handoff (run mode) ---

    async def handle_development_handoff(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
    ) -> dict:
        turn_id = decision.get("turn_id")
        metadata = decision.get("metadata", {})
        intent = str(metadata.get("next_intent") or metadata.get("intent") or "")

        state_machine.transition_to(TurnState.HANDOFF_DEVELOPMENT)
        ledger.state_history.append(("HANDOFF_DEVELOPMENT", int(time.time() * 1000)))
        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "workflow_handoff",
                {
                    "handoff_reason": "development",
                    "handoff_target": "development",
                    "intent": intent,
                },
            )
        )

        development_result: dict[str, Any] | None = None
        if self.development_runtime is not None and hasattr(self.development_runtime, "execute_stream"):
            try:
                session_state = SimpleNamespace(session_id=str(turn_id))
                events: list[Any] = []
                async for event in self.development_runtime.execute_stream(intent, session_state):
                    events.append(event)
                development_result = {
                    "event_count": len(events),
                    "events": events,
                }
            except Exception as exc:
                logger.exception("DevelopmentWorkflowRuntime execution failed during handoff: turn_id=%s", turn_id)
                development_result = {"error": str(exc)}

        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        if self.development_runtime is not None:
            visible_content = f"[HANDOFF] Development workflow executed. Intent: {intent}"
        else:
            visible_content = f"[HANDOFF] Development runtime unavailable. Intent: {intent}"
        self.emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="handoff",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
            )
        )

        return self.build_turn_result(
            turn_id=turn_id,
            kind="handoff_development",
            visible_content=visible_content,
            decision=decision,
            batch_receipt=None,
            finalization=None,
            ledger=ledger,
            workflow_context={"development_result": development_result, "intent": intent},
        )

    # --- Development handoff (stream mode) ---

    async def handle_development_handoff_stream(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
    ) -> AsyncIterator[TurnEvent]:
        turn_id = decision.get("turn_id")
        metadata = decision.get("metadata", {})
        intent = str(metadata.get("next_intent") or metadata.get("intent") or "")

        state_machine.transition_to(TurnState.HANDOFF_DEVELOPMENT)
        ledger.state_history.append(("HANDOFF_DEVELOPMENT", int(time.time() * 1000)))
        yield TurnPhaseEvent.create(
            str(turn_id),
            "workflow_handoff",
            {
                "handoff_reason": "development",
                "handoff_target": "development",
                "intent": intent,
            },
        )

        if self.development_runtime is not None and hasattr(self.development_runtime, "execute_stream"):
            try:
                session_state = SimpleNamespace(session_id=str(turn_id))
                async for event in self.development_runtime.execute_stream(intent, session_state):
                    yield event
            except Exception as exc:
                logger.exception("DevelopmentWorkflowRuntime stream failed during handoff: turn_id=%s", turn_id)
                yield ErrorEvent(
                    turn_id=str(turn_id),
                    error_type="development_stream_error",
                    message=str(exc),
                    state_at_error="HANDOFF_DEVELOPMENT",
                )
        elif self.development_runtime is not None:
            yield ErrorEvent(
                turn_id=str(turn_id),
                error_type="development_runtime_error",
                message="DevelopmentWorkflowRuntime does not support execute_stream",
                state_at_error="HANDOFF_DEVELOPMENT",
            )

        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        yield CompletionEvent(
            turn_id=str(turn_id),
            status="handoff",
            duration_ms=ledger.get_duration_ms(),
            llm_calls=len(ledger.llm_calls),
            tool_calls=len(ledger.tool_executions),
            turn_kind="handoff_development",
            visible_content=(
                f"[HANDOFF] Development workflow executed. Intent: {intent}"
                if self.development_runtime is not None
                else f"[HANDOFF] Development runtime unavailable. Intent: {intent}"
            ),
            session_patch={"next_intent": intent, "_development_handoff_executed": True} if intent else {},
        )

    # --- Ask user ---

    async def handle_ask_user(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
    ) -> dict:
        turn_id = decision.get("turn_id")
        reasoning = decision.get("reasoning_summary")
        visible_message = decision.get("visible_message", "")

        state_machine.transition_to(TurnState.SUSPENDED)
        ledger.state_history.append(("SUSPENDED", int(time.time() * 1000)))
        ledger.finalize()

        if str(reasoning or "").strip():
            suspend_msg = "model returned thinking-only response; awaiting user clarification"
        else:
            suspend_msg = "model returned no visible output or tool calls; awaiting user clarification"

        self.emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="suspended",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
                error=suspend_msg,
            )
        )

        return self.build_turn_result(
            turn_id=turn_id,
            kind="ask_user",
            visible_content=visible_message,
            decision=decision,
            batch_receipt=None,
            finalization={"suspended_reason": suspend_msg},
            ledger=ledger,
        )
