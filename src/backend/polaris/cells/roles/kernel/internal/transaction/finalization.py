"""LLM_ONCE / NONE / LOCAL 收口策略。

负责工具批次执行后的最终输出生成：
- LLM_ONCE: 调用 LLM（tool_choice=none）生成自然语言摘要
- NONE: 直接将工具结果作为可见输出
- LOCAL: 本地模板渲染
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import BlockedReason
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import detect_inline_patch_escape
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger, VisibleOutput
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    FinalizeMode,
    RawLLMResponse,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, TurnEvent, TurnPhaseEvent


class FinalizationHandler:
    """收口处理器 — 将工具执行结果转化为最终用户可见输出。"""

    def __init__(
        self,
        *,
        llm_provider: Callable[..., Any],
        decoder: Any,
        emit_event: Callable[[TurnEvent], None],
        guard_assert_no_finalization_tool_calls: Callable[..., None],
        config: Any | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.decoder = decoder
        self.emit_event = emit_event
        self.guard_assert_no_finalization_tool_calls = guard_assert_no_finalization_tool_calls
        self.config = config

    async def execute_llm_once(
        self,
        decision: TurnDecision,
        receipts: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        context: list[dict],
        *,
        stream: bool = False,
    ) -> dict:
        """LLM_ONCE 收口：强制 tool_choice=none，调用 LLM 生成摘要。"""
        turn_id = decision.get("turn_id")

        state_machine.transition_to(TurnState.FINALIZATION_REQUESTED)
        ledger.state_history.append(("FINALIZATION_REQUESTED", int(time.time() * 1000)))
        self.emit_event(TurnPhaseEvent.create(turn_id, "finalization_requested"))

        finalization_context = self._build_finalization_context(
            context, receipts, ledger.delivery_contract, config=self.config
        )
        request_payload = {
            "messages": finalization_context,
            "tools": None,
            "tool_choice": "none",
            "_control": {
                "phase": "closing",
                "tools_disabled": True,
                "continuation_forbidden": True,
            },
        }
        # BUG-07 fix: Shield the FINALIZATION LLM call from upstream
        # task cancellation.  When the evaluator's suite-level timeout
        # fires via asyncio.wait_for(), it cancels the current task.
        # Without shielding, the CancelledError propagates into this
        # LLM call, aborting the summarization step.  asyncio.shield()
        # decouples the inner coroutine's lifetime from the parent
        # cancellation scope, allowing the finalization to complete
        # even when the outer task is being torn down.
        _finalization_logger = logging.getLogger(__name__)
        try:
            response = await asyncio.shield(self.llm_provider(request_payload))
        except asyncio.CancelledError:
            _finalization_logger.warning(
                "finalization_llm_call_shielded_cancel: turn_id=%s "
                "upstream cancellation intercepted, retrying without shield",
                decision.get("turn_id"),
            )
            # If shield itself is cancelled (double-cancel), fall through
            # with a graceful degradation: use tool results as final answer
            response = {
                "content": "[Finalization call was cancelled. Tool execution results are the final output.]",
                "thinking": None,
                "tool_calls": [],
                "model": "cancelled",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }

        ledger.record_llm_call(
            phase="finalization",
            model=response.get("model", "unknown"),
            tokens_in=response.get("usage", {}).get("prompt_tokens", 0),
            tokens_out=response.get("usage", {}).get("completion_tokens", 0),
        )

        finalize_thinking = response.get("thinking")
        if finalize_thinking is not None and not isinstance(finalize_thinking, str):
            finalize_thinking = None
        finalize_decision = self.decoder.decode_for_finalization(
            RawLLMResponse(
                content=response.get("content", ""),
                thinking=finalize_thinking,
                native_tool_calls=response.get("tool_calls", []),
                model=response.get("model", "unknown"),
                usage=response.get("usage", {}),
            ),
            TurnId(turn_id),
            FinalizeMode.LLM_ONCE,
        )
        decision_kind = finalize_decision.get("kind")
        if decision_kind == TurnDecisionKind.HANDOFF_WORKFLOW:
            # 理论上 decoder 已过滤此情况；保留断言用于回归防护
            _logger = logging.getLogger(__name__)
            _logger.error(
                "finalize_decision_decode_invariant_broken: turn_id=%s decoder_should_have_filtered_handoff",
                turn_id,
            )
            # 降级为 FINAL_ANSWER 继续执行，不 panic
            decision_kind = TurnDecisionKind.FINAL_ANSWER

        state_machine.transition_to(TurnState.FINALIZATION_RECEIVED)
        ledger.state_history.append(("FINALIZATION_RECEIVED", int(time.time() * 1000)))
        self.emit_event(TurnPhaseEvent.create(turn_id, "finalization_completed"))

        self.guard_assert_no_finalization_tool_calls(
            turn_id=str(turn_id or ""),
            tool_calls=response.get("tool_calls"),
            ledger=ledger,
        )

        finalization_output = VisibleOutput(
            content=response.get("content", ""), reasoning=response.get("thinking"), format="markdown"
        )

        # === Phase 4: Two-Stage Review Gate — Pre-Finalization Self-Check ===
        # Agent 报告完成前必须自问三个问题（来自 Superpowers subagent-driven-development skill）。
        # 若任一维度不合格，标记为 DONE_WITH_CONCERNS 而非 DONE。
        self_check = _pre_finalization_self_check(ledger, receipts)
        if not self_check["completeness"] or not self_check["discipline"]:
            logger.warning(
                "pre_finalization_self_check_failed: "
                "turn_id=%s completeness=%s quality=%s discipline=%s",
                turn_id,
                self_check["completeness"],
                self_check["quality"],
                self_check["discipline"],
            )
            # 记录到 ledger 的 anomaly_flags（供后续审计）
            ledger.anomaly_flags.append(
                {
                    "type": "PRE_FINALIZATION_SELF_CHECK_FAILED",
                    "turn_id": turn_id,
                    "check": self_check,
                }
            )

        # === Inline Patch Escape 检测（LLM_ONCE 收口阶段兜底）===
        if ledger.delivery_contract.must_materialize and not ledger.mutation_obligation.mutation_satisfied:
            escape_result = detect_inline_patch_escape(finalization_output.content)
            if escape_result["is_escape"]:
                _logger = logging.getLogger(__name__)
                _logger.warning(
                    "inline-patch-escape-detected-in-finalization: turn_id=%s ratio=%.2f code_blocks=%d chars=%d",
                    turn_id,
                    escape_result["ratio"],
                    escape_result["code_blocks_count"],
                    escape_result["code_block_chars"],
                )
                ledger.mutation_obligation.record_inline_patch_rejected()
                ledger.anomaly_flags.append(
                    {
                        "type": "INLINE_PATCH_ESCAPE",
                        "turn_id": turn_id,
                        "phase": "finalization",
                        "ratio": escape_result["ratio"],
                        "code_block_chars": escape_result["code_block_chars"],
                        "total_chars": escape_result["total_chars"],
                        "code_blocks_count": escape_result["code_blocks_count"],
                    }
                )
                ledger.mutation_obligation.mark_blocked(
                    BlockedReason.SAFETY_CONSTRAINT,
                    detail=f"INLINE_PATCH_ESCAPE detected in LLM_ONCE finalization: "
                    f"token density ratio={escape_result['ratio']:.2f}. "
                    "MATERIALIZE_CHANGES mode requires write tools, not inline code blocks.",
                )
                state_machine.transition_to(TurnState.COMPLETED)
                ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
                ledger.finalize()
                self.emit_event(
                    CompletionEvent(
                        turn_id=turn_id,
                        status="failed",
                        duration_ms=ledger.get_duration_ms(),
                        llm_calls=len(ledger.llm_calls),
                        tool_calls=len(ledger.tool_executions),
                    )
                )
                _metrics: dict[str, float] = {
                    "duration_ms": float(ledger.get_duration_ms()),
                    "llm_calls": float(len(ledger.llm_calls)),
                    "tool_calls": float(len(ledger.tool_executions)),
                }
                _metrics.update(ledger.build_monitoring_metrics(final_kind="inline_patch_escape_blocked"))
                return {
                    "turn_id": turn_id,
                    "kind": "inline_patch_escape_blocked",
                    "visible_content": finalization_output.content,
                    "decision": {
                        "kind": decision.get("kind").value
                        if hasattr(decision.get("kind"), "value")
                        else str(decision.get("kind", "")),
                        "finalize_mode": decision.get("finalize_mode").value
                        if hasattr(decision.get("finalize_mode"), "value")
                        else str(decision.get("finalize_mode", "")),
                    },
                    "metrics": _metrics,
                    "batch_receipt": {"results": [r for receipt in receipts for r in receipt.get("results", [])]},
                    "finalization": {
                        "turn_id": turn_id,
                        "mode": "blocked",
                        "blocked_reason": ledger.mutation_obligation.blocked_reason.value
                        if ledger.mutation_obligation.blocked_reason
                        else None,
                        "blocked_detail": ledger.mutation_obligation.blocked_detail,
                        "needs_followup_workflow": True,
                        "workflow_reason": "inline_patch_escape_blocked",
                    },
                }

        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        self.emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="success",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
            )
        )
        metrics: dict[str, float] = {
            "duration_ms": float(ledger.get_duration_ms()),
            "llm_calls": float(len(ledger.llm_calls)),
            "tool_calls": float(len(ledger.tool_executions)),
        }
        metrics.update(ledger.build_monitoring_metrics(final_kind="tool_batch_with_receipt"))
        return {
            "turn_id": turn_id,
            "kind": "tool_batch_with_receipt",
            "visible_content": finalization_output.content,
            "decision": {
                "kind": decision.get("kind").value
                if hasattr(decision.get("kind"), "value")
                else str(decision.get("kind", "")),
                "finalize_mode": decision.get("finalize_mode").value
                if hasattr(decision.get("finalize_mode"), "value")
                else str(decision.get("finalize_mode", "")),
            },
            "metrics": metrics,
            "batch_receipt": {"results": [r for receipt in receipts for r in receipt.get("results", [])]},
            "finalization": {
                "turn_id": turn_id,
                "mode": "llm_once",
                "final_visible_message": finalization_output.content,
                "needs_followup_workflow": False,
                "workflow_reason": None,
            },
        }

    @staticmethod
    def complete_with_tool_results(
        decision: TurnDecision,
        receipts: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        emit_event: Callable[[TurnEvent], None],
    ) -> dict:
        """NONE 模式：工具结果即最终答案。"""
        turn_id = decision.get("turn_id")
        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        content_lines: list[str] = []
        for receipt in receipts:
            for result in receipt.get("results", []):
                tool_name = result.get("tool_name", "unknown")
                status = result.get("status", "unknown")
                if status == "success":
                    result_value = result.get("result", "")
                    if isinstance(result_value, dict):
                        content_lines.append(f"**{tool_name}**: {result_value.get('result', result_value)}")
                    else:
                        content_lines.append(f"**{tool_name}**: {result_value}")
                else:
                    raw_results = receipt.get("raw_results") or []
                    first_raw_error = (
                        next(iter(raw_results), cast("dict[str, Any]", {})).get("error", "unknown")
                        if raw_results
                        else "unknown"
                    )
                    error = result.get("result") or first_raw_error
                    content_lines.append(f"**{tool_name}**: Error - {error}")

        final_content = "\n\n".join(content_lines)
        emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="success",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
            )
        )
        metrics: dict[str, Any] = {
            "duration_ms": ledger.get_duration_ms(),
            "llm_calls": len(ledger.llm_calls),
            "tool_calls": len(ledger.tool_executions),
        }
        metrics.update(ledger.build_monitoring_metrics(final_kind="tool_batch_with_receipt"))
        return {
            "turn_id": turn_id,
            "kind": "tool_batch_with_receipt",
            "visible_content": final_content,
            "decision": {
                "kind": decision.get("kind").value
                if hasattr(decision.get("kind"), "value")
                else str(decision.get("kind", "")),
                "finalize_mode": decision.get("finalize_mode").value
                if hasattr(decision.get("finalize_mode"), "value")
                else str(decision.get("finalize_mode", "")),
            },
            "metrics": metrics,
            "batch_receipt": {"results": [r for receipt in receipts for r in receipt.get("results", [])]},
            "finalization": None,
        }

    @staticmethod
    def finalize_local(
        decision: TurnDecision,
        receipts: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        emit_event: Callable[[TurnEvent], None],
    ) -> dict:
        """LOCAL 模式：本地模板渲染。"""
        turn_id = decision.get("turn_id")
        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        content = FinalizationHandler._render_local_template(receipts)
        emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="success",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
            )
        )
        return {
            "turn_id": turn_id,
            "kind": "tool_batch_with_receipt",
            "visible_content": content,
            "decision": {
                "kind": decision.get("kind").value
                if hasattr(decision.get("kind"), "value")
                else str(decision.get("kind", "")),
                "finalize_mode": decision.get("finalize_mode").value
                if hasattr(decision.get("finalize_mode"), "value")
                else str(decision.get("finalize_mode", "")),
            },
            "metrics": {
                "duration_ms": ledger.get_duration_ms(),
                "llm_calls": len(ledger.llm_calls),
                "tool_calls": len(ledger.tool_executions),
            },
            "batch_receipt": {"results": [r for receipt in receipts for r in receipt.get("results", [])]},
            "finalization": {
                "turn_id": turn_id,
                "mode": "local",
                "final_visible_message": content,
                "needs_followup_workflow": False,
                "workflow_reason": None,
            },
        }

    @staticmethod
    def _build_finalization_context(
        original_context: list[dict],
        receipts: list[dict],
        delivery_contract: Any | None = None,
        *,
        config: Any | None = None,
    ) -> list[dict]:
        """构建收口上下文，将 tool results 格式化为文本摘要。

        关键设计变更（防贴代码逃逸）：
        1. 保留原始 system prompt（避免 HARD GATE 规则丢失）
        2. 根据 delivery_contract.mode 生成差异化收口指令
        3. 对 MATERIALIZE_CHANGES 模式明确禁止贴完整代码
        """
        from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
            DeliveryMode,
        )

        latest_user_request = ""
        for message in reversed(original_context):
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role != "user":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                latest_user_request = content
                break

        # 保留原始上下文中的 system 消息（避免 HARD GATE 丢失）
        preserved_messages: list[dict] = []
        for msg in original_context:
            if not isinstance(msg, Mapping):
                continue
            role = str(msg.get("role") or "").strip().lower()
            if role == "system":
                content = str(msg.get("content") or "").strip()
                if content:
                    preserved_messages.append({"role": "system", "content": content})

        max_per_tool = getattr(config, "max_per_tool_result_chars", 3000) if config else 3000
        max_total = getattr(config, "max_total_result_chars", 8000) if config else 8000
        summary_parts: list[str] = []
        for receipt in receipts:
            for result in receipt.get("results", []):
                tool_name = result.get("tool_name", "unknown")
                status = result.get("status", "unknown")
                if status == "success":
                    result_value = result.get("result", "")
                    if isinstance(result_value, dict):
                        try:
                            result_text = json.dumps(result_value, ensure_ascii=False, indent=2)
                        except (TypeError, ValueError):
                            result_text = str(result_value)
                    else:
                        result_text = str(result_value)
                    if len(result_text) > max_per_tool:
                        result_text = result_text[:max_per_tool] + "\n[...truncated]"
                    summary_parts.append(f"### Tool: {tool_name}\n```\n{result_text}\n```")
                else:
                    raw_results = receipt.get("raw_results") or []
                    first_raw_error = (
                        next(iter(raw_results), cast("dict[str, Any]", {})).get("error", "unknown")
                        if raw_results
                        else "unknown"
                    )
                    error = result.get("result") or first_raw_error
                    summary_parts.append(f"### Tool: {tool_name} (Error)\n```\n{error}\n```")

        summary = "\n\n".join(summary_parts)
        if len(summary) > max_total:
            summary = summary[:max_total] + "\n\n[... additional results truncated]"

        # 根据 delivery mode 生成差异化收口指令
        mode = DeliveryMode.ANALYZE_ONLY
        if delivery_contract is not None:
            mode = getattr(delivery_contract, "mode", DeliveryMode.ANALYZE_ONLY)

        if mode == DeliveryMode.MATERIALIZE_CHANGES:
            # 【修复根因 B】：MATERIALIZE_CHANGES 模式的收口指令不再一刀切。
            # 检测 receipt 中是否实际执行了写工具：
            #   - 有写工具 → 要求 LLM 确认修改已完成并总结（而非再输出"计划"）
            #   - 只有读工具 → 允许输出修改计划（因为还未执行写入）
            _has_write_in_receipts = any(
                str(r.get("tool_name", "")) in WRITE_TOOLS for receipt in receipts for r in receipt.get("results", [])
            )
            if _has_write_in_receipts:
                finalization_instruction = (
                    "【任务】请确认修改已完成并给出简要总结。\n"
                    "【要求】说明已修改的文件和核心变更点，不要输出完整代码块。\n"
                    "【约束】此阶段已关闭所有工具调用通道；不要调用任何工具，也不要请求额外输入。\n"
                    "请使用用户的语言回复。"
                )
            else:
                finalization_instruction = (
                    "【任务】基于以上读取结果，给出简明的修改计划或当前进度总结。\n"
                    "【要求】只输出文字说明，不要贴出完整的可执行代码块或文件内容。\n"
                    "【约束】此阶段已关闭所有工具调用通道；不要调用任何工具，也不要请求额外输入。\n"
                    "如果需要修改文件，请在下一回合使用 write/edit 工具落盘，而不是把代码贴在回复中。\n"
                    "请使用用户的语言回复。"
                )
        elif mode == DeliveryMode.PROPOSE_PATCH:
            finalization_instruction = (
                "【任务】基于以上结果，给出分析总结或 patch 提案。\n"
                "【要求】可以输出 patch/diff 格式的提案作为参考，但明确说明这只是提案（不会自动落盘）。\n"
                "【约束】此阶段已关闭所有工具调用通道；不要调用任何工具，也不要请求额外输入。\n"
                "请使用用户的语言回复。"
            )
        else:
            # ANALYZE_ONLY：弱化"一次性完成"表述，避免鼓励贴代码
            finalization_instruction = (
                "【任务】基于以上结果，给出分析、总结或建议。\n"
                "【要求】提供清晰、简洁的答复；如需示例，保持简短（片段即可），不要贴出完整文件内容。\n"
                "【约束】此阶段已关闭所有工具调用通道；不要调用任何工具，也不要请求额外输入。\n"
                "请使用用户的语言回复。"
            )

        finalization_message = {
            "role": "user",
            "content": (
                "[FINALIZATION]\n"
                "当前用户最新请求（最高优先级）:\n"
                f"{latest_user_request or '(missing user request)'}\n\n"
                "以下是本回合工具执行结果，请据此完成该请求:\n\n"
                f"{summary}\n\n"
                f"{finalization_instruction}"
            ),
        }

        # 如果保留了 system 消息，把 FINALIZATION 消息接在后面
        if preserved_messages:
            return [*preserved_messages, finalization_message]
        return [finalization_message]

    @staticmethod
    def _render_local_template(receipts: list[dict]) -> str:
        """本地模板渲染。"""
        lines = ["## Execution Results", ""]
        for receipt in receipts:
            for result in receipt.get("results", []):
                tool_name = result.get("tool_name", "unknown")
                status = result.get("status", "unknown")
                status_emoji = "check" if status == "success" else "fail"
                lines.append(f"{status_emoji} **{tool_name}**")
                if status == "success":
                    lines.append(f"```\n{result.get('result', '')}\n```")
                else:
                    error = result.get("result") or "unknown"
                    lines.append(f"```\nError: {error}\n```")
                lines.append("")
        return "\n".join(lines)
