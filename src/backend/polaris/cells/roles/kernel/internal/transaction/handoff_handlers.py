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
from typing import Any, TypeAlias

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
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope

logger = logging.getLogger(__name__)

DecisionLike: TypeAlias = TurnDecision | dict[str, Any]


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


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _build_context_handoff_pack(
    *,
    decision: DecisionLike,
    workflow_context: dict[str, Any],
    handoff_reason: str,
) -> ContextHandoffPack:
    metadata = _safe_mapping(decision.get("metadata", {}))
    recoverable_context = _safe_mapping(workflow_context.get("recoverable_context"))
    recoverable_decision = _safe_mapping(recoverable_context.get("decision"))
    recoverable_metadata = _safe_mapping(recoverable_decision.get("metadata"))
    batch_receipts = recoverable_context.get("batch_receipts") or workflow_context.get("batch_receipts") or []
    receipt_refs: list[str] = []
    if isinstance(batch_receipts, list):
        for receipt in batch_receipts:
            if not isinstance(receipt, dict):
                continue
            batch_id = str(receipt.get("batch_id") or "").strip()
            if batch_id:
                receipt_refs.append(batch_id)

    turn_id = str(decision.get("turn_id") or workflow_context.get("turn_id") or "").strip()
    session_id = str(metadata.get("session_id") or metadata.get("task_id") or turn_id).strip() or turn_id
    run_id = str(metadata.get("run_id") or metadata.get("stream_run_id") or "").strip() or None
    role = str(metadata.get("role") or metadata.get("role_id") or "workflow").strip() or "workflow"
    workspace = (
        str(
            metadata.get("workspace") or metadata.get("workspace_root") or metadata.get("workspace_full") or "."
        ).strip()
        or "."
    )
    current_goal = str(
        metadata.get("current_goal")
        or recoverable_metadata.get("current_goal")
        or decision.get("visible_message")
        or ""
    )
    run_card = _safe_mapping(metadata.get("run_card") or recoverable_metadata.get("run_card"))
    handoff_id = str(metadata.get("handoff_id") or f"handoff_{turn_id}_{int(time.time() * 1000)}").strip()

    return ContextHandoffPack(
        handoff_id=handoff_id,
        workspace=workspace,
        created_at=str(int(time.time())),
        session_id=session_id,
        run_id=run_id,
        reason=handoff_reason,
        current_goal=current_goal,
        run_card=run_card,
        context_slice_plan={"workflow_context": workflow_context},
        decision_log=(recoverable_context,),
        receipt_refs=tuple(dict.fromkeys(receipt_refs)),
        turn_envelope=TurnEnvelope(
            turn_id=turn_id,
            session_id=session_id,
            run_id=run_id,
            role=role,
            receipt_ids=tuple(dict.fromkeys(receipt_refs)),
        ),
    )


def _with_context_handoff_pack(
    decision: DecisionLike,
    workflow_context: dict[str, Any],
    handoff_reason: str,
) -> DecisionLike:
    metadata = _safe_mapping(decision.get("metadata", {}))
    existing_pack = ContextHandoffPack.from_mapping(
        metadata.get("context_handoff_pack") if isinstance(metadata.get("context_handoff_pack"), dict) else None
    )
    handoff_pack = existing_pack or _build_context_handoff_pack(
        decision=decision,
        workflow_context=workflow_context,
        handoff_reason=handoff_reason,
    )
    handoff_payload = handoff_pack.to_dict()
    metadata["context_handoff_pack"] = handoff_payload
    workflow_context["context_handoff_pack"] = handoff_payload
    recoverable_context = workflow_context.get("recoverable_context")
    if isinstance(recoverable_context, dict):
        recoverable_context["context_handoff_pack"] = handoff_payload
    if isinstance(decision, TurnDecision):
        return decision.model_copy(update={"metadata": metadata})
    decision_payload = dict(decision)
    decision_payload["metadata"] = metadata
    return decision_payload


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


def _workflow_result_failure(result: Any) -> str | None:
    """Return an error string when a workflow result is not successful."""

    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)
    status_text = str(status_value or "").strip().lower()
    error = str(getattr(result, "error", "") or "").strip()
    if error:
        return error
    if status_text and status_text not in {"completed", "success", "ok"}:
        return f"workflow_result_status_{status_text}"
    return None


def _workflow_event_failure(event: Any) -> str | None:
    """Return an error string when a workflow stream event reports failure."""

    event_name = type(event).__name__
    if event_name == "ErrorEvent":
        return str(getattr(event, "message", "") or getattr(event, "error", "") or "workflow_stream_error")

    if event_name != "CompletionEvent":
        return None

    error = str(getattr(event, "error", "") or "").strip()
    if error:
        return error

    status_text = str(getattr(event, "status", "") or "").strip().lower()
    if status_text in {"failed", "failure", "error", "cancelled", "timeout"}:
        return f"workflow_stream_status_{status_text}"
    return None


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
        exploration_error: str | None = None
        if self.workflow_runtime is not None:
            try:
                workflow_decision = _with_context_handoff_pack(decision, workflow_context, handoff_reason)
                exploration_result = await self.workflow_runtime.execute(workflow_decision, TurnId(str(turn_id)))
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
                exploration_error = _workflow_result_failure(exploration_result)
                if exploration_error:
                    workflow_context["exploration_error"] = exploration_error
            except Exception as exc:
                logger.exception("ExplorationWorkflowRuntime execution failed during handoff: turn_id=%s", turn_id)
                exploration_error = str(exc)
                workflow_context["exploration_error"] = exploration_error

        if exploration_error:
            state_machine.transition_to(TurnState.FAILED)
            ledger.state_history.append(("FAILED", int(time.time() * 1000)))
            ledger.finalize()
            visible_content = (
                f"[HANDOFF_ERROR] Exploration workflow failed. Reason: {handoff_reason}. Error: {exploration_error}"
            )
            self.emit_event(
                CompletionEvent(
                    turn_id=turn_id,
                    status="error",
                    duration_ms=ledger.get_duration_ms(),
                    llm_calls=len(ledger.llm_calls),
                    tool_calls=len(ledger.tool_executions),
                    error=exploration_error,
                )
            )
            return self.build_turn_result(
                turn_id=turn_id,
                kind="workflow_execution_error",
                visible_content=visible_content,
                decision=decision,
                batch_receipt=batch_receipt,
                finalization={
                    "error": exploration_error,
                    "phase": "handoff_workflow",
                    "handoff_reason": handoff_reason,
                },
                ledger=ledger,
                workflow_context=workflow_context,
            )

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

        workflow_error: str | None = None
        workflow_decision = _with_context_handoff_pack(decision, workflow_context, handoff_reason)
        if self.workflow_runtime is not None and hasattr(self.workflow_runtime, "execute_stream"):
            try:
                async for event in self.workflow_runtime.execute_stream(workflow_decision, TurnId(str(turn_id))):
                    event_error = _workflow_event_failure(event)
                    if event_error:
                        workflow_error = event_error
                    if isinstance(event, CompletionEvent):
                        continue
                    yield event
            except Exception as exc:
                logger.exception("ExplorationWorkflowRuntime stream failed during handoff: turn_id=%s", turn_id)
                workflow_error = str(exc)
                yield ErrorEvent(
                    turn_id=str(turn_id),
                    error_type="workflow_stream_error",
                    message=str(exc),
                    state_at_error="HANDOFF_WORKFLOW",
                )
        elif self.workflow_runtime is not None:
            try:
                exploration_result = await self.workflow_runtime.execute(workflow_decision, TurnId(str(turn_id)))
                workflow_error = _workflow_result_failure(exploration_result)
                visible_content = exploration_result.synthesis or "[HANDOFF] Exploration completed."
                if visible_content and not workflow_error:
                    yield ContentChunkEvent(
                        turn_id=str(turn_id),
                        chunk=visible_content,
                    )
            except Exception as exc:
                logger.exception("ExplorationWorkflowRuntime execution failed during handoff: turn_id=%s", turn_id)
                workflow_error = str(exc)
                yield ErrorEvent(
                    turn_id=str(turn_id),
                    error_type="workflow_execution_error",
                    message=str(exc),
                    state_at_error="HANDOFF_WORKFLOW",
                )

        final_state = TurnState.FAILED if workflow_error else TurnState.COMPLETED
        state_machine.transition_to(final_state)
        ledger.state_history.append((final_state.name, int(time.time() * 1000)))
        ledger.finalize()

        yield CompletionEvent(
            turn_id=str(turn_id),
            status="error" if workflow_error else "handoff",
            duration_ms=ledger.get_duration_ms(),
            llm_calls=len(ledger.llm_calls),
            tool_calls=len(ledger.tool_executions),
            turn_kind="handoff_workflow",
            error=workflow_error,
            visible_content=(
                f"[HANDOFF_ERROR] Exploration workflow failed. Reason: {handoff_reason}. Error: {workflow_error}"
                if workflow_error
                else f"[HANDOFF] Exploration workflow executed. Reason: {handoff_reason}"
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
        development_error: str | None = None
        if self.development_runtime is not None and hasattr(self.development_runtime, "execute_stream"):
            try:
                session_state = SimpleNamespace(session_id=str(turn_id))
                events: list[Any] = []
                async for event in self.development_runtime.execute_stream(intent, session_state):
                    events.append(event)
                    event_error = _workflow_event_failure(event)
                    if event_error:
                        development_error = event_error
                development_result = {
                    "event_count": len(events),
                    "events": events,
                }
            except Exception as exc:
                logger.exception("DevelopmentWorkflowRuntime execution failed during handoff: turn_id=%s", turn_id)
                development_error = str(exc)
                development_result = {"error": development_error}
        elif self.development_runtime is not None:
            development_error = "DevelopmentWorkflowRuntime does not support execute_stream"
            development_result = {"error": development_error}

        if development_error:
            state_machine.transition_to(TurnState.FAILED)
            ledger.state_history.append(("FAILED", int(time.time() * 1000)))
            ledger.finalize()
            visible_content = (
                f"[HANDOFF_ERROR] Development workflow failed. Intent: {intent}. Error: {development_error}"
            )
            self.emit_event(
                CompletionEvent(
                    turn_id=turn_id,
                    status="error",
                    duration_ms=ledger.get_duration_ms(),
                    llm_calls=len(ledger.llm_calls),
                    tool_calls=len(ledger.tool_executions),
                    turn_kind="handoff_development",
                    error=development_error,
                )
            )
            return self.build_turn_result(
                turn_id=turn_id,
                kind="development_execution_error",
                visible_content=visible_content,
                decision=decision,
                batch_receipt=None,
                finalization={
                    "error": development_error,
                    "phase": "handoff_development",
                    "intent": intent,
                },
                ledger=ledger,
                workflow_context={"development_result": development_result, "intent": intent},
            )

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

        development_error: str | None = None
        if self.development_runtime is not None and hasattr(self.development_runtime, "execute_stream"):
            try:
                session_state = SimpleNamespace(session_id=str(turn_id))
                async for event in self.development_runtime.execute_stream(intent, session_state):
                    event_error = _workflow_event_failure(event)
                    if event_error:
                        development_error = event_error
                    if isinstance(event, CompletionEvent):
                        continue
                    yield event
            except Exception as exc:
                logger.exception("DevelopmentWorkflowRuntime stream failed during handoff: turn_id=%s", turn_id)
                development_error = str(exc)
                yield ErrorEvent(
                    turn_id=str(turn_id),
                    error_type="development_stream_error",
                    message=str(exc),
                    state_at_error="HANDOFF_DEVELOPMENT",
                )
        elif self.development_runtime is not None:
            development_error = "DevelopmentWorkflowRuntime does not support execute_stream"
            yield ErrorEvent(
                turn_id=str(turn_id),
                error_type="development_runtime_error",
                message=development_error,
                state_at_error="HANDOFF_DEVELOPMENT",
            )

        final_state = TurnState.FAILED if development_error else TurnState.COMPLETED
        state_machine.transition_to(final_state)
        ledger.state_history.append((final_state.name, int(time.time() * 1000)))
        ledger.finalize()

        yield CompletionEvent(
            turn_id=str(turn_id),
            status="error" if development_error else "handoff",
            duration_ms=ledger.get_duration_ms(),
            llm_calls=len(ledger.llm_calls),
            tool_calls=len(ledger.tool_executions),
            turn_kind="handoff_development",
            error=development_error,
            visible_content=(
                f"[HANDOFF_ERROR] Development workflow failed. Intent: {intent}. Error: {development_error}"
                if development_error
                else f"[HANDOFF] Development workflow executed. Intent: {intent}"
                if self.development_runtime is not None
                else f"[HANDOFF] Development runtime unavailable. Intent: {intent}"
            ),
            session_patch={"next_intent": intent, "_development_handoff_executed": True}
            if intent and not development_error
            else {},
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
