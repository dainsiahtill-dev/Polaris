"""Round executor - Execute one turn round including tool execution and policy eval.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    封装 TurnEngine 中每轮循环的工具执行、策略评估、和结果收集逻辑。
    提供非流式和流式两种执行路径。
"""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any, AsyncIterator

from polaris.cells.roles.kernel.internal.policy.layer.exploration import EDIT_TOOLS
from polaris.cells.roles.kernel.internal.recovery_state_machine import (
    CircuitBreakerContext,
    get_recovery_state_machine,
)
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopCircuitBreakerError
from polaris.kernelone.events.typed import ToolError, ToolErrorKind, emit_event

from .context_pruner import ContextPruner
from .tool_executor import SingleToolExecutor
from .utils import append_transcript_cycle, tool_call_signature

logger = logging.getLogger(__name__)


class RoundExecutor:
    """Executes the tool-execution phase of one turn round."""

    def __init__(
        self,
        kernel: Any,
        tool_executor: SingleToolExecutor,
        pruner: ContextPruner,
        cognitive_pipeline: Any | None = None,
    ) -> None:
        """Initialize round executor.

        Args:
            kernel: RoleExecutionKernel instance.
            tool_executor: SingleToolExecutor for tool calls.
            pruner: ContextPruner for hallucination loop handling.
            cognitive_pipeline: Optional CognitivePipelinePort.
        """
        self._kernel = kernel
        self._tool_executor = tool_executor
        self._pruner = pruner
        self._cognitive_pipeline = cognitive_pipeline

    def _evaluate_policy(
        self,
        policy: Any,
        exec_tool_calls: list[Any],
        deferred_tool_calls: list[Any],
        round_tool_results: list[dict[str, Any]],
        state: Any,
    ) -> Any:
        """Run policy evaluation after tool execution.

        Args:
            policy: PolicyLayer instance.
            exec_tool_calls: Executed tool calls.
            deferred_tool_calls: Deferred tool calls.
            round_tool_results: Results from this round.
            state: ConversationState instance.

        Returns:
            PolicyLayer evaluation result.
        """
        from polaris.cells.roles.kernel.internal.policy import CanonicalToolCall

        current_canonical: list[Any] = []
        for c in list(exec_tool_calls) + list(deferred_tool_calls):
            call_id = getattr(c, "call_id", "") or getattr(c, "id", "") or ""
            tool = getattr(c, "tool", "") or getattr(c, "name", "")
            args: dict[str, Any] = getattr(c, "args", {}) or getattr(c, "arguments", {}) or {}
            raw = getattr(c, "raw", "") or ""
            current_canonical.append(CanonicalToolCall(tool=tool, args=args, call_id=call_id, raw_content=raw))

        pre_stall = policy.precheck_stall(current_canonical)

        last_tool_failed = None
        if round_tool_results:
            last_result = round_tool_results[-1]
            last_tool = last_result.get("tool", "") if isinstance(last_result, dict) else ""
            if last_tool in EDIT_TOOLS:
                has_error = last_result.get("error") if isinstance(last_result, dict) else None
                is_ok = last_result.get("ok", True) if isinstance(last_result, dict) else True
                is_success = last_result.get("success", True) if isinstance(last_result, dict) else True
                if has_error or not is_ok or not is_success:
                    last_tool_failed = {
                        "tool": last_tool,
                        "failed": True,
                        "error": str(has_error) if has_error else "unknown",
                    }

        return policy.evaluate(
            current_canonical,
            budget_state={
                "tool_call_count": state.budgets.tool_call_count,
                "turn_count": state.budgets.turn_count,
                "stall_count": pre_stall,
            },
            precheck_stall_count=pre_stall,
            task_metadata={"last_tool_failed": last_tool_failed} if last_tool_failed else None,
        )

    async def execute_tools(
        self,
        *,
        turn: Any,
        exec_tool_calls: list[Any],
        deferred_tool_calls: list[Any],
        controller: Any,
        state: Any,
        profile: Any,
        request: Any,
        round_index: int,
        all_tool_calls: list[dict[str, Any]],
        all_tool_results: list[dict[str, Any]],
        all_blocked_calls: list[dict[str, Any]],
        policy: Any,
    ) -> tuple[str | None, Any]:
        """Execute tools for non-stream mode and return round error + policy result.

        Returns:
            Tuple of (round_error, policy_result).
        """
        append_transcript_cycle(controller=controller, turn=turn, tool_results=[])
        round_tool_results: list[dict[str, Any]] = []

        for call in exec_tool_calls:
            tool_name_str = str(getattr(call, "tool", "") or "").strip() or str(
                call.get("tool") if isinstance(call, dict) else ""
            )
            call_args = (
                getattr(call, "args", {}) or {}
                if hasattr(call, "args")
                else (call.get("args", {}) if isinstance(call, dict) else {})
            )
            search_val = call_args.get("search") if isinstance(call_args, dict) else None
            fp = str(search_val)[:200] if search_val else None
            prune_result = self._pruner.check_and_handle_loop_break(
                tool_name_str, controller, skip_tool=True, fingerprint=fp
            )
            if prune_result is not None:
                prune_event_result = {
                    "ok": False,
                    "success": False,
                    "tool": tool_name_str,
                    "error": f"[Context Pruning] {prune_result.get('content', 'HALLUCINATION_LOOP detected')[:200]}",
                    "error_type": "HALLUCINATION_LOOP",
                    "retryable": False,
                    "loop_break": True,
                }
                round_tool_results.append(prune_event_result)
                all_tool_results.append(prune_event_result)
                all_tool_calls.append({"tool": tool_name_str, "args": getattr(call, "args", {}) or {}})
                controller.append_tool_result(prune_event_result)
                continue

            try:
                result = await self._tool_executor.execute(
                    profile=profile,
                    request=request,
                    call=call,
                    round_index=round_index,
                )
            except (RuntimeError, ValueError) as exc:
                logger.exception("[TurnEngine] _execute_single_tool 调用异常")
                tool_name = str(
                    getattr(call, "tool", "?")
                    if hasattr(call, "tool")
                    else (call.get("tool") if isinstance(call, dict) else "?") or "?"
                )
                _call_id = getattr(call, "call_id", "") or getattr(call, "id", "") or str(uuid.uuid4().hex[:12])
                _error_event = ToolError.create(
                    tool_name=tool_name,
                    tool_call_id=_call_id,
                    error=str(exc),
                    error_type=ToolErrorKind.EXCEPTION,
                    stack_trace=traceback.format_exc(),
                    run_id=str(getattr(request, "run_id", "") or ""),
                    workspace=str(self._kernel.workspace or ""),
                )
                await emit_event(_error_event)
                result = {
                    "ok": False,
                    "success": False,
                    "tool": tool_name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "retryable": False,
                }

            self._pruner.inject_loop_break_signal(tool_name_str, result, fingerprint=fp)
            if result.get("blocked"):
                self._pruner.handle_blocked_tool_pruning(tool_name_str, controller, result, fingerprint=fp)

            if self._cognitive_pipeline is not None:
                _tool_result_str = str(result.get("error", "") or result.get("content", ""))[:500]
                _assess = await self._cognitive_pipeline.post_tool_cognitive_assess(
                    tool_name=tool_name_str,
                    tool_result=_tool_result_str,
                    session_id=str(request.run_id or request.task_id or ""),
                )
                if not _assess.should_continue:
                    logger.info(
                        "[TurnEngine] Cognitive assess suggests stop: quality=%.2f note=%s",
                        _assess.quality_score,
                        _assess.assessment_note,
                    )

            round_tool_results.append(result)
            try:
                state.record_tool_call()
            except (RuntimeError, ValueError):
                logger.exception("[TurnEngine] state.record_tool_call() 异常")
            all_tool_results.append(result if isinstance(result, dict) else {"value": result})
            all_tool_calls.append(
                {
                    "tool": getattr(call, "tool", "") or (call.get("tool") if isinstance(call, dict) else ""),
                    "args": getattr(call, "args", {}) or (call.get("args", {}) if isinstance(call, dict) else {}),
                }
            )

            try:
                controller.append_tool_result(result, tool_args=getattr(call, "args", None))
            except ToolLoopCircuitBreakerError as cb_exc:
                recovery_sm = get_recovery_state_machine()
                recovery_sm.handle_circuit_breaker(
                    CircuitBreakerContext(
                        breaker_type=getattr(cb_exc, "breaker_type", "unknown"),
                        tool_name=call.tool,
                        reason=str(cb_exc),
                        recovery_hint=getattr(cb_exc, "recovery_hint", ""),
                    )
                )
                logger.error("[TurnEngine] Circuit breaker triggered: %s", cb_exc)
                return (
                    str(cb_exc),
                    None,
                )
            except (RuntimeError, ValueError):
                logger.exception("[TurnEngine] _controller.append_tool_result() 异常")

        for d_call in deferred_tool_calls:
            all_tool_calls.append(
                {
                    "tool": getattr(d_call, "tool", "") or (d_call.get("tool") if isinstance(d_call, dict) else ""),
                    "args": getattr(d_call, "args", {}) or (d_call.get("args", {}) if isinstance(d_call, dict) else {}),
                }
            )

        state.tick_wall_time()
        safety_stop_reason = controller.register_cycle(
            executed_tool_calls=list(exec_tool_calls),
            deferred_tool_calls=list(deferred_tool_calls),
            tool_results=round_tool_results,
        )
        round_error: str | None = safety_stop_reason

        try:
            policy_result = self._evaluate_policy(
                policy, exec_tool_calls, deferred_tool_calls, round_tool_results, state
            )
        except (RuntimeError, ValueError) as exc:
            logger.exception("[TurnEngine] policy.evaluate() 异常 (循环内)")
            return (f"PolicyLayer 评估异常: {exc}", None)

        for call in policy_result.blocked_calls:
            tool_name = getattr(call, "tool", "?") if isinstance(call, object) else str(call)
            for v in policy_result.violations:
                if v.tool == tool_name:
                    logger.debug(
                        "[TurnEngine] PolicyLayer 拦截工具: tool=%s reason=%s is_critical=%s",
                        tool_name,
                        v.reason,
                        v.is_critical,
                    )
                    all_blocked_calls.append(
                        {
                            "tool": tool_name,
                            "args": dict(getattr(call, "args", {})),
                            "policy": getattr(v, "policy", "?") or "?",
                            "reason": getattr(v, "reason", "?") or "?",
                            "is_critical": getattr(v, "is_critical", False),
                        }
                    )

        if policy_result.stop_reason and not round_error:
            round_error = policy_result.stop_reason

        return (round_error, policy_result)

    async def execute_tools_stream(
        self,
        *,
        turn: Any,
        exec_tool_calls: list[Any],
        deferred_tool_calls: list[Any],
        controller: Any,
        state: Any,
        profile: Any,
        request: Any,
        round_index: int,
        all_tool_calls: list[dict[str, Any]],
        all_tool_results: list[dict[str, Any]],
        realtime_seen_tool_signatures: set[str],
        policy: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute tools for stream mode, yielding events.

        Yields:
            tool_call, tool_result, error, or policy_blocked events.
        """
        append_transcript_cycle(controller=controller, turn=turn, tool_results=[])
        round_tool_results: list[dict[str, Any]] = []

        for call in exec_tool_calls:
            safe_args = call.args if isinstance(call.args, dict) else {}
            signature = tool_call_signature(call.tool, safe_args)
            if signature not in realtime_seen_tool_signatures:
                realtime_seen_tool_signatures.add(signature)
                yield {"type": "tool_call", "tool": call.tool, "args": safe_args, "iteration": round_index}

            tool_name_str = str(call.tool or "").strip()
            search_val = safe_args.get("search") if isinstance(safe_args, dict) else None
            fp = str(search_val)[:200] if search_val else None
            prune_result = self._pruner.check_and_handle_loop_break(
                tool_name_str, controller, skip_tool=True, fingerprint=fp
            )
            if prune_result is not None:
                prune_event_result = {
                    "ok": False,
                    "success": False,
                    "tool": tool_name_str,
                    "error": f"[Context Pruning] {prune_result.get('content', 'HALLUCINATION_LOOP detected')[:200]}",
                    "error_type": "HALLUCINATION_LOOP",
                    "retryable": False,
                    "loop_break": True,
                }
                round_tool_results.append(prune_event_result)
                all_tool_results.append(prune_event_result)
                all_tool_calls.append({"tool": tool_name_str, "args": safe_args})
                yield {
                    "type": "tool_result",
                    "tool": tool_name_str,
                    "result": prune_event_result,
                    "iteration": round_index,
                }
                controller.append_tool_result(prune_event_result)
                continue

            try:
                tool_result = await self._tool_executor.execute(
                    profile=profile,
                    request=request,
                    call=call,
                    round_index=round_index,
                )
            except (RuntimeError, ValueError) as exc:
                logger.exception("[TurnEngine.run_stream] _execute_single_tool 调用异常")
                tool_name = str(
                    getattr(call, "tool", "?")
                    if hasattr(call, "tool")
                    else (call.get("tool") if isinstance(call, dict) else "?") or "?"
                )
                _call_id = getattr(call, "call_id", "") or getattr(call, "id", "") or str(uuid.uuid4().hex[:12])
                _error_event = ToolError.create(
                    tool_name=tool_name,
                    tool_call_id=_call_id,
                    error=str(exc),
                    error_type=ToolErrorKind.EXCEPTION,
                    stack_trace=traceback.format_exc(),
                    run_id=str(getattr(request, "run_id", "") or ""),
                    workspace=str(self._kernel.workspace or ""),
                )
                await emit_event(_error_event)
                tool_result = {
                    "ok": False,
                    "success": False,
                    "tool": tool_name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "retryable": False,
                }

            self._pruner.inject_loop_break_signal(tool_name_str, tool_result, fingerprint=fp)
            if tool_result.get("blocked"):
                self._pruner.handle_blocked_tool_pruning(tool_name_str, controller, tool_result, fingerprint=fp)

            if self._cognitive_pipeline is not None:
                _tool_result_str = str(tool_result.get("error", "") or tool_result.get("content", ""))[:500]
                _assess = await self._cognitive_pipeline.post_tool_cognitive_assess(
                    tool_name=tool_name_str,
                    tool_result=_tool_result_str,
                    session_id=str(request.run_id or request.task_id or ""),
                )
                if not _assess.should_continue:
                    logger.info(
                        "[TurnEngine.run_stream] Cognitive assess suggests stop: quality=%.2f note=%s",
                        _assess.quality_score,
                        _assess.assessment_note,
                    )

            round_tool_results.append(tool_result)
            try:
                state.record_tool_call()
            except (RuntimeError, ValueError):
                logger.exception("[TurnEngine.run_stream] state.record_tool_call() 异常")
            all_tool_results.append(tool_result if isinstance(tool_result, dict) else {"value": tool_result})
            all_tool_calls.append(
                {
                    "tool": getattr(call, "tool", "") or (call.get("tool") if isinstance(call, dict) else ""),
                    "args": getattr(call, "args", {}) or (call.get("args", {}) if isinstance(call, dict) else {}),
                }
            )
            yield {"type": "tool_result", "tool": call.tool, "result": tool_result, "iteration": round_index}

            try:
                controller.append_tool_result(tool_result, tool_args=getattr(call, "args", None))
            except ToolLoopCircuitBreakerError as cb_exc:
                recovery_sm = get_recovery_state_machine()
                recovery_sm.handle_circuit_breaker(
                    CircuitBreakerContext(
                        breaker_type=getattr(cb_exc, "breaker_type", "unknown"),
                        tool_name=call.tool,
                        reason=str(cb_exc),
                        recovery_hint=getattr(cb_exc, "recovery_hint", ""),
                    )
                )
                logger.error("[TurnEngine.run_stream] Circuit breaker triggered: %s", cb_exc)
                yield {
                    "type": "error",
                    "error": str(cb_exc),
                    "recovery_state": recovery_sm.get_status(),
                    "iteration": round_index,
                }
                return
            except (RuntimeError, ValueError):
                logger.exception("[TurnEngine.run_stream] _controller.append_tool_result() 异常")

        for d_call in deferred_tool_calls:
            all_tool_calls.append(
                {
                    "tool": getattr(d_call, "tool", "") or (d_call.get("tool") if isinstance(d_call, dict) else ""),
                    "args": getattr(d_call, "args", {}) or (d_call.get("args", {}) if isinstance(d_call, dict) else {}),
                }
            )

        state.tick_wall_time()
        safety_stop_reason = controller.register_cycle(
            executed_tool_calls=list(exec_tool_calls),
            deferred_tool_calls=list(deferred_tool_calls),
            tool_results=round_tool_results,
        )
        if safety_stop_reason:
            logger.debug("[TurnEngine.run_stream] ToolLoopController stop: %s", safety_stop_reason)
            yield {"type": "error", "error": safety_stop_reason, "iteration": round_index}
            return

        try:
            policy_result = self._evaluate_policy(
                policy, exec_tool_calls, deferred_tool_calls, round_tool_results, state
            )
        except (RuntimeError, ValueError) as exc:
            logger.exception("[TurnEngine.run_stream] policy.evaluate() 异常 (循环内)")
            yield {"type": "error", "error": f"PolicyLayer 评估异常: {exc}", "iteration": round_index}
            return

        for call in policy_result.blocked_calls:
            tool_name = getattr(call, "tool", "?") if isinstance(call, object) else str(call)
            for v in policy_result.violations:
                if v.tool == tool_name:
                    logger.debug(
                        "[TurnEngine.run_stream] PolicyLayer 拦截工具: tool=%s reason=%s",
                        tool_name,
                        v.reason,
                    )
                    yield {
                        "type": "policy_blocked",
                        "tool": tool_name,
                        "args": dict(getattr(call, "args", {})),
                        "policy": getattr(v, "policy", "?") or "?",
                        "reason": getattr(v, "reason", "?") or "?",
                        "is_critical": getattr(v, "is_critical", False),
                        "iteration": round_index,
                    }

        if policy_result.stop_reason:
            yield {"type": "error", "error": f"policy_stop: {policy_result.stop_reason}", "iteration": round_index}
            return

        if policy_result.has_approval_required:
            for call in policy_result.requires_approval:
                tool_name = getattr(call, "tool", "?") if isinstance(call, object) else str(call)
                yield {
                    "type": "requires_approval",
                    "tool": tool_name,
                    "call_id": getattr(call, "call_id", ""),
                    "args": dict(getattr(call, "args", {})),
                    "iteration": round_index,
                }

        # Return policy_result through a special event type that the caller can intercept
        yield {"type": "_policy_result", "policy_result": policy_result}


__all__ = ["RoundExecutor"]
