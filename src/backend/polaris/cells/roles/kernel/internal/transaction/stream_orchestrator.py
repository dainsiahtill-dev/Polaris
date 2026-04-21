"""流式决策与 Turn 执行编排器。

职责：
- 流式 LLM 调用（call_llm_for_decision_stream）
- 流式 Turn 执行编排（execute_turn_stream）
- speculative 任务管理（drain_speculative_tasks）
- 拒绝响应检测（is_refusal_response）

设计原则：
- 所有外部依赖通过 __init__ 注入，零隐式耦合
- Facade 中的 _execute_turn_stream / _call_llm_for_decision_stream 退化为 proxy
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Literal, cast

from polaris.cells.roles.kernel.internal.speculation.cancel import CancellationCoordinator
from polaris.cells.roles.kernel.internal.speculation.task_group import TurnScopedTaskGroup
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import HandoffHandler
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import RetryOrchestrator
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    ToolBatchEvent,
    TurnEvent,
    TurnPhaseEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 拒绝响应检测
# ---------------------------------------------------------------------------


def is_refusal_response(response: RawLLMResponse) -> bool:
    """检测 LLM 响应是否为拒绝执行（refusal）."""
    native_calls = response.get("native_tool_calls") or []
    if native_calls:
        return False
    content = str(response.get("content") or "").strip()
    if not content:
        return False
    refusal_markers = (
        "i cannot",
        "i can't",
        "i'm sorry",
        "i am sorry",
        "i cannot assist",
        "i can't assist",
        "不能",
        "禁止",
        "不允许",
        "无法",
        "对不起",
        "抱歉",
    )
    lowered = content.lower()
    return any(marker in lowered for marker in refusal_markers)


def _extract_read_tools_from_receipt(batch_receipt: dict[str, Any] | None) -> list[str]:
    """从 batch_receipt 中提取读工具名称列表（去重）。"""
    if not batch_receipt:
        return []
    results = batch_receipt.get("results") or batch_receipt.get("raw_results") or []
    seen: set[str] = set()
    reads: list[str] = []
    for item in results:
        name = str(item.get("tool_name") or "").strip()
        if not name:
            continue
        if name in seen:
            continue
        # 识别读工具：以 read_ 或 repo_read_ 开头，且不是 write 工具
        if name.startswith(("read_", "repo_read_", "search_", "grep", "find_")):
            seen.add(name)
            reads.append(name)
    return reads


def _build_continue_visible_content(read_tools: list[str], current_progress: str = "implementing") -> str:
    """构建 continue_multi_turn 的 visible_content，内嵌 SESSION_PATCH。

    利用 ADR-0080 机制：Orchestrator 会自动提取 <SESSION_PATCH> 块并注入
    structured_findings，使下一回合的 _build_continuation_prompt 能包含
    recent_reads 信息。

    Args:
        read_tools: 已调用的读工具列表
        current_progress: 当前 task_progress（implementing/verifying/done等）
    """
    # 根据当前 progress 动态生成 instruction，不再强制重置为 implementing
    if current_progress == "verifying":
        instruction = (
            "验证阶段。请运行测试或手动验证修复效果，确保无回归。严禁调用探索工具（glob/repo_rg/repo_tree 等）。"
        )
        visible_prefix = "验证阶段继续"
    elif current_progress == "implementing":
        instruction = (
            "读阶段已完成，现在请调用写工具（edit_file / write_file 等）执行修改。"
            "严禁调用探索工具（glob/repo_rg/repo_tree 等）。"
        )
        visible_prefix = "写阶段继续"
    elif current_progress == "done":
        instruction = "任务已完成。请汇总结果并以 END_SESSION 结束。"
        visible_prefix = "完成阶段"
    else:
        instruction = "继续执行任务。"
        visible_prefix = f"继续（{current_progress}）"

    patch: dict[str, Any] = {
        # FIX-20250421: 不再强制覆盖 task_progress，保持当前阶段
        # 只在有读工具时记录 recent_reads
    }
    if read_tools:
        patch["recent_reads"] = read_tools
    import json

    return (
        f"[系统提示] 多回合工作流继续：{visible_prefix}。\n"
        f"{instruction}\n"
        "严禁输出文字计划或代码块。必须调用工具！\n"
        f"<SESSION_PATCH>\n{json.dumps(patch, ensure_ascii=False)}\n</SESSION_PATCH>"
    )


# ---------------------------------------------------------------------------
# Speculative 任务清理
# ---------------------------------------------------------------------------


async def drain_speculative_tasks(
    tasks: list[tuple[str, asyncio.Task[dict[str, Any]]]],
    *,
    ledger: TurnLedger | None = None,
    timeout_s: float = 0.2,
    shadow_engine: StreamShadowEngine | None = None,
) -> None:
    """Drain speculative tasks and cancel leftovers to avoid task leaks."""
    if tasks:
        task_list = [task for _, task in tasks]
        call_ids_by_task = {task: call_id for call_id, task in tasks}
        done, pending = await asyncio.wait(task_list, timeout=timeout_s)
        for task in done:
            call_id = str(call_ids_by_task.get(task, "") or "")
            if ledger is None or not call_id or task.cancelled():
                continue
            try:
                outcome = task.result()
            except Exception as exc:  # noqa: BLE001
                outcome = {"enabled": True, "result": None, "error": str(exc)}
            if isinstance(outcome, Mapping):
                ledger.record_speculative_outcome(call_id, outcome)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        logger.debug(
            "speculative tasks drained: done=%s pending_cancelled=%s",
            len(done),
            len(pending),
        )

    if (
        shadow_engine is not None
        and hasattr(shadow_engine, "_registry")
        and shadow_engine._registry is not None
        and ledger is not None
    ):
        records = shadow_engine._registry.get_turn_records(ledger.turn_id)
        running_futures = [
            record.future for record in records if record.future is not None and not record.future.done()
        ]
        if running_futures:
            await asyncio.wait(running_futures, timeout=0.1)
        await shadow_engine._registry.drain_turn(
            ledger.turn_id,
            timeout_s=timeout_s,
            salvage_governor=getattr(shadow_engine, "_salvage_governor", None),
            task_group=getattr(shadow_engine, "_task_group", None),
        )
    if shadow_engine is not None and hasattr(shadow_engine, "_task_group") and shadow_engine._task_group is not None:
        shadow_engine._task_group.close()


# ---------------------------------------------------------------------------
# StreamOrchestrator
# ---------------------------------------------------------------------------


class StreamOrchestrator:
    """流式决策与 Turn 执行编排器 — 将 Facade 的流式专用逻辑集中管理。"""

    def __init__(
        self,
        *,
        llm_provider: Callable,
        llm_provider_stream: Callable | None,
        decoder: Any,
        emit_event: Callable[[TurnEvent], None],
        build_decision_messages: Callable[[list[dict], list[dict]], list[dict]],
        build_stream_shadow_engine: Callable[..., StreamShadowEngine | None],
        call_llm_for_decision: Callable[..., Any],
        handoff_handler: HandoffHandler,
        tool_batch_executor: ToolBatchExecutor,
        retry_orchestrator: RetryOrchestrator,
        handle_final_answer: Callable[..., Any],
        requires_mutation_intent_hybrid: Callable[..., Any],
        extract_monitoring_metrics: Callable[..., dict[str, float]],
        config: Any | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.llm_provider_stream = llm_provider_stream
        self.decoder = decoder
        self.emit_event = emit_event
        self.build_decision_messages = build_decision_messages
        self.build_stream_shadow_engine = build_stream_shadow_engine
        self.call_llm_for_decision = call_llm_for_decision
        self.handoff_handler = handoff_handler
        self.tool_batch_executor = tool_batch_executor
        self.retry_orchestrator = retry_orchestrator
        self.handle_final_answer = handle_final_answer
        self.requires_mutation_intent_hybrid = requires_mutation_intent_hybrid
        self.extract_monitoring_metrics = extract_monitoring_metrics
        self.config = config
        self._session_turn_count = 0

    # -----------------------------------------------------------------------
    # 流式 LLM 决策调用
    # -----------------------------------------------------------------------

    async def _call_llm_for_decision_stream_impl(
        self,
        context: list[dict],
        tool_definitions: list[dict],
        ledger: TurnLedger,
        shadow_engine: StreamShadowEngine | None = None,
        *,
        tool_choice_override: Any | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """流式调用LLM获取决策，yield 事件并返回最终 RawLLMResponse（通过内部 materialize 事件）。"""
        from polaris.cells.roles.kernel.internal.turn_engine.stream_handler import StreamEventHandler

        decision_messages = self.build_decision_messages(context, tool_definitions)
        normalized_model_override = str(model_override or "").strip() or None
        request_payload = {
            "messages": decision_messages,
            "tools": tool_definitions if tool_definitions else None,
            "tool_choice": (
                tool_choice_override if tool_choice_override is not None else ("auto" if tool_definitions else None)
            ),
            "model_override": normalized_model_override,
        }

        if self.llm_provider_stream is None:
            response = await self.call_llm_for_decision(
                context,
                tool_definitions,
                ledger,
                tool_choice_override=tool_choice_override,
            )
            yield cast(
                TurnEvent,
                {
                    "type": "_internal_materialize",
                    "response": response,
                },
            )
            return

        handler = StreamEventHandler(workspace=".")
        if shadow_engine is None:
            shadow_engine = self.build_stream_shadow_engine(workspace=".", turn_id=ledger.turn_id)
        speculative_tasks: list[tuple[str, asyncio.Task[dict[str, Any]]]] = []

        async def _try_speculate_tool_call(
            tool_name: str,
            tool_args: dict[str, Any],
            call_id: str,
        ) -> None:
            if shadow_engine is None:
                return
            speculative_tasks.append(
                (
                    call_id,
                    asyncio.create_task(
                        shadow_engine.speculate_tool_call(
                            tool_name=tool_name,
                            arguments=tool_args,
                            call_id=call_id,
                            turn_id=ledger.turn_id,
                        )
                    ),
                )
            )

        try:
            async for event in handler.process_stream(
                self.llm_provider_stream(request_payload),
                round_index=0,
                start_time=time.time(),
                profile=None,
            ):
                event_type = str(event.get("type") or "").strip()
                if event_type == "thinking_chunk":
                    content = str(event.get("content") or "")
                    if shadow_engine is not None:
                        shadow_engine.consume_delta(content)
                    yield ContentChunkEvent(turn_id=ledger.turn_id, chunk=content, is_thinking=True)
                elif event_type == "content_chunk":
                    content = str(event.get("content") or "")
                    if shadow_engine is not None:
                        shadow_engine.consume_delta(content)
                    yield ContentChunkEvent(turn_id=ledger.turn_id, chunk=content, is_thinking=False)
                elif event_type == "tool_call":
                    tool_name = str(event.get("tool") or "").strip()
                    call_id = str(event.get("call_id") or "").strip()
                    raw_args = event.get("args")
                    tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}
                    if tool_name:
                        if shadow_engine is not None:
                            shadow_engine.consume_delta(f"<tool_call:{tool_name}>")
                            await _try_speculate_tool_call(tool_name, tool_args, call_id)
                        yield ToolBatchEvent(
                            turn_id=ledger.turn_id,
                            batch_id="",
                            tool_name=tool_name,
                            call_id=call_id,
                            status="started",
                            progress=0.0,
                            arguments=tool_args,
                        )
                elif event_type == "error":
                    error_message = str(event.get("error") or event.get("message") or "unknown_error")
                    yield ErrorEvent(
                        turn_id=ledger.turn_id,
                        error_type="stream_error",
                        message=error_message,
                        state_at_error="DECISION_REQUESTED",
                    )
                    return
                elif event_type == "_internal_materialize":
                    thinking_parts = event.get("thinking_content")
                    thinking = "".join(thinking_parts) if thinking_parts else None
                    visible_content = str(event.get("emitted_round_content") or event.get("raw_output") or "")
                    response = RawLLMResponse(
                        content=visible_content,
                        thinking=thinking,
                        native_tool_calls=list(event.get("native_tool_calls") or []),
                        model="unknown",
                        usage={},
                    )
                    yield cast(
                        TurnEvent,
                        {
                            "type": "_internal_materialize",
                            "response": response,
                        },
                    )
        finally:
            await drain_speculative_tasks(speculative_tasks, ledger=ledger, shadow_engine=shadow_engine)

    # -----------------------------------------------------------------------
    # 流式 Turn 执行编排
    # -----------------------------------------------------------------------

    async def execute_turn_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        *,
        call_llm_for_decision_stream: Callable[..., AsyncIterator[TurnEvent]] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """流式执行turn的内部实现。"""
        from polaris.cells.roles.kernel.internal.kernel_guard import KernelGuardError
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            has_available_write_tool,
            is_mutation_contract_violation,
        )
        from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
            extract_latest_user_message,
        )

        # H2 物理熔断器：session 级 turn 硬限制，防止无限循环
        self._session_turn_count += 1
        max_session_turns = getattr(self.config, "max_session_turns", 20)
        if self._session_turn_count > max_session_turns:
            logger.warning(
                "session_turn_limit_exceeded: turn_id=%s count=%d max=%d",
                turn_id,
                self._session_turn_count,
                max_session_turns,
            )
            state_machine.transition_to(TurnState.FAILED)
            ledger.state_history.append(("FAILED", int(time.time() * 1000)))
            ledger.finalize()
            ledger.anomaly_flags.append(
                {
                    "type": "SESSION_TURN_LIMIT_EXCEEDED",
                    "turn_id": turn_id,
                    "session_turn_count": self._session_turn_count,
                    "max_session_turns": max_session_turns,
                }
            )
            yield ErrorEvent(
                turn_id=turn_id,
                error_type="MAX_SESSION_TURNS_EXCEEDED",
                message=(
                    f"Session exceeded {max_session_turns} turns without completing contract obligations. "
                    "Raising to human for review."
                ),
                state_at_error=state_machine.current_state.name,
            )
            return

        state_machine.transition_to(TurnState.CONTEXT_BUILT)
        ledger.state_history.append(("CONTEXT_BUILT", int(time.time() * 1000)))

        # Phase 1b: 解析交付契约（与 run 模式保持一致）
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            resolve_delivery_mode,
        )

        latest_user_request = extract_latest_user_message(context)
        # Guard: 如果 context 包含 orchestrator 续写 prompt（<Goal>/<Progress> XML 块），
        # 说明这是 continuation turn，直接返回 ANALYZE_ONLY，不走 SLM 路由（防止死循环）。
        _raw_user = str(
            next(
                (m.get("content", "") for m in reversed(context) if str(m.get("role", "")).strip().lower() == "user"),
                "",
            )
        )
        _is_continuation_prompt = "<Goal>" in _raw_user and "<Progress>" in _raw_user
        if _is_continuation_prompt:
            from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
                DeliveryContract,
                DeliveryMode,
            )

            # 【修复根因 A】：continuation prompt 不再盲目降级为 ANALYZE_ONLY。
            # 从 <Progress> 块解析当前阶段（如 implementing），据此选择正确的 delivery mode。
            # 根因：implementing 阶段被强制 ANALYZE_ONLY，导致写工具通道被关闭。
            _progress_match = re.search(r"当前阶段:\s*(\w+)", _raw_user)
            _parsed_progress = _progress_match.group(1) if _progress_match else "exploring"
            if _parsed_progress == "implementing":
                delivery_contract = DeliveryContract(
                    mode=DeliveryMode.MATERIALIZE_CHANGES,
                    requires_mutation=True,
                    requires_verification=False,
                    allow_inline_code=False,
                    allow_patch_proposal=False,
                )
                logger.debug(
                    "continuation_prompt_delivery_mode: progress=%s mode=MATERIALIZE_CHANGES",
                    _parsed_progress,
                )
            else:
                delivery_contract = DeliveryContract(
                    mode=DeliveryMode.ANALYZE_ONLY,
                    requires_mutation=False,
                    requires_verification=False,
                    allow_inline_code=True,
                    allow_patch_proposal=False,
                )
                logger.debug(
                    "continuation_prompt_delivery_mode: progress=%s mode=ANALYZE_ONLY",
                    _parsed_progress,
                )
        else:
            # SLM 优先、regex 兜底
            try:
                from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
                    CognitiveGateway,
                )

                gateway = CognitiveGateway.get_default_instance_sync()
                if gateway is not None:
                    delivery_contract = await gateway.resolve_delivery_mode(latest_user_request)
                else:
                    delivery_contract = resolve_delivery_mode(latest_user_request)
            except (ImportError, AttributeError, RuntimeError, asyncio.TimeoutError, OSError):
                delivery_contract = resolve_delivery_mode(latest_user_request)
        ledger.set_delivery_contract(delivery_contract)

        state_machine.transition_to(TurnState.DECISION_REQUESTED)
        ledger.state_history.append(("DECISION_REQUESTED", int(time.time() * 1000)))
        self.emit_event(TurnPhaseEvent.create(turn_id, "decision_requested"))

        shadow_engine = self.build_stream_shadow_engine(workspace=".", turn_id=turn_id)
        llm_response: RawLLMResponse | None = None
        _call_llm_stream = (
            call_llm_for_decision_stream
            if call_llm_for_decision_stream is not None
            else self._call_llm_for_decision_stream_impl
        )
        async for event in _call_llm_stream(context, tool_definitions, ledger, shadow_engine=shadow_engine):
            if isinstance(event, dict) and event.get("type") == "_internal_materialize":
                llm_response = event.get("response")
                continue
            yield event

        if llm_response is None:
            yield ErrorEvent(
                turn_id=turn_id,
                error_type="stream_error",
                message="No LLM response materialized from stream",
                state_at_error="DECISION_REQUESTED",
            )
            return

        if self.llm_provider_stream is not None:
            ledger.record_llm_call(
                phase="decision",
                model=llm_response.get("model", "unknown"),
                tokens_in=llm_response.get("usage", {}).get("prompt_tokens", 0),
                tokens_out=llm_response.get("usage", {}).get("completion_tokens", 0),
            )

        if (
            shadow_engine is not None
            and is_refusal_response(llm_response)
            and hasattr(shadow_engine, "_registry")
            and shadow_engine._registry is not None
        ):
            coordinator = CancellationCoordinator()
            task_group = getattr(shadow_engine, "_task_group", None)
            await coordinator.refuse_turn(
                turn_id=turn_id,
                registry=shadow_engine._registry,
                task_group=task_group if isinstance(task_group, TurnScopedTaskGroup) else None,
            )

        state_machine.transition_to(TurnState.DECISION_RECEIVED)
        ledger.state_history.append(("DECISION_RECEIVED", int(time.time() * 1000)))

        decision = self.decoder.decode(llm_response, TurnId(turn_id))
        ledger.record_decision(decision)
        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "decision_completed",
                {
                    "kind": decision.get("kind").value
                    if hasattr(decision.get("kind"), "value")
                    else str(decision.get("kind")),
                    "finalize_mode": decision.get("finalize_mode").value
                    if hasattr(decision.get("finalize_mode"), "value")
                    else str(decision.get("finalize_mode")),
                },
            )
        )

        state_machine.transition_to(TurnState.DECISION_DECODED)
        ledger.state_history.append(("DECISION_DECODED", int(time.time() * 1000)))

        decision_kind = decision.get("kind")
        result: dict
        batch_receipt: dict[str, Any] = {}
        forced_retry_result: dict | None = None
        latest_user_request = extract_latest_user_message(context)
        guard_mode = str(getattr(self.config, "mutation_guard_mode", "warn"))
        # 当 delivery contract 明确要求 MATERIALIZE_CHANGES 时，无视 guard_mode 强制 strict
        # 防止 LLM 以"请求确认"的 FINAL_ANSWER 逃避工具调用（用户已确认多次但仍不执行）
        _contract_requires_tools = False
        try:
            from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
                DeliveryMode,
            )

            _contract_requires_tools = ledger.delivery_contract.mode == DeliveryMode.MATERIALIZE_CHANGES
        except Exception:  # noqa: BLE001
            pass
        if (
            decision_kind != TurnDecisionKind.TOOL_BATCH
            and await self.requires_mutation_intent_hybrid(latest_user_request)
            and has_available_write_tool(tool_definitions)
        ):
            if guard_mode == "strict" or _contract_requires_tools:
                logger.warning(
                    "mutation-contract guard(stream): non-tool decision (%s) for mutation request, "
                    "forcing retry path (guard=%s contract_requires_tools=%s)",
                    decision_kind,
                    guard_mode,
                    _contract_requires_tools,
                )
                forced_retry_result = await self.retry_orchestrator.retry_tool_batch_after_contract_violation(
                    turn_id=turn_id,
                    context=context,
                    tool_definitions=tool_definitions,
                    state_machine=state_machine,
                    ledger=ledger,
                    stream=True,
                    shadow_engine=shadow_engine,
                )
            elif guard_mode == "warn":
                logger.warning(
                    "mutation-contract guard(stream, soft): non-tool decision (%s) for mutation request, "
                    "but mutation_guard_mode=warn allows passthrough. turn_id=%s",
                    decision_kind,
                    turn_id,
                )
                ledger.record_mutation_guard_warning(
                    reason=f"non_tool_decision_for_mutation_request:{decision_kind.value if hasattr(decision_kind, 'value') else decision_kind}",
                    user_request=latest_user_request,
                )

        if forced_retry_result is not None:
            result = forced_retry_result
            batch_receipt = dict(result.get("batch_receipt") or {})
            if batch_receipt:
                for item in batch_receipt.get("results", []):
                    yield ToolBatchEvent(
                        turn_id=turn_id,
                        batch_id=str(batch_receipt.get("batch_id", "")),
                        tool_name=str(item.get("tool_name", "")),
                        call_id=str(item.get("call_id", "")),
                        status="success" if item.get("status") == "success" else "error",
                        progress=1.0,
                        result=item.get("result"),
                        error=item.get("error"),
                    )
            if result.get("kind") == "handoff_workflow":
                handoff_decision = result.get("decision") or decision
                async for event in self.handoff_handler.handle_handoff_stream(
                    handoff_decision,
                    state_machine,
                    ledger,
                    workflow_context=result.get("workflow_context"),
                    handoff_reason=result.get("handoff_reason"),
                    batch_receipt=batch_receipt,
                ):
                    yield event
                return
        else:
            if decision_kind == TurnDecisionKind.FINAL_ANSWER:
                result = await self.handle_final_answer(decision, state_machine, ledger)
            elif decision_kind == TurnDecisionKind.HANDOFF_WORKFLOW:
                async for event in self.handoff_handler.handle_handoff_stream(decision, state_machine, ledger):
                    yield event
                return
            elif decision_kind == TurnDecisionKind.HANDOFF_DEVELOPMENT:
                async for event in self.handoff_handler.handle_development_handoff_stream(
                    decision, state_machine, ledger
                ):
                    yield event
                return
            elif decision_kind == TurnDecisionKind.ASK_USER:
                result = await self.handoff_handler.handle_ask_user(decision, state_machine, ledger)
            elif decision_kind == TurnDecisionKind.TOOL_BATCH:
                try:
                    result = await self.tool_batch_executor.execute_tool_batch(
                        decision,
                        state_machine,
                        ledger,
                        context,
                        stream=True,
                        shadow_engine=shadow_engine,
                    )
                except (RuntimeError, KernelGuardError) as exc:
                    if not isinstance(exc, RuntimeError) or not is_mutation_contract_violation(exc):
                        raise
                    result = await self.retry_orchestrator.retry_tool_batch_after_contract_violation(
                        turn_id=turn_id,
                        context=context,
                        tool_definitions=tool_definitions,
                        state_machine=state_machine,
                        ledger=ledger,
                        stream=True,
                        shadow_engine=shadow_engine,
                    )
                batch_receipt = dict(result.get("batch_receipt") or {})
                if batch_receipt:
                    for item in batch_receipt.get("results", []):
                        yield ToolBatchEvent(
                            turn_id=turn_id,
                            batch_id=str(batch_receipt.get("batch_id", "")),
                            tool_name=str(item.get("tool_name", "")),
                            call_id=str(item.get("call_id", "")),
                            status="success" if item.get("status") == "success" else "error",
                            progress=1.0,
                            result=item.get("result"),
                            error=item.get("error"),
                        )
                if result.get("kind") == "handoff_workflow":
                    handoff_decision = result.get("decision") or decision
                    async for event in self.handoff_handler.handle_handoff_stream(
                        handoff_decision,
                        state_machine,
                        ledger,
                        workflow_context=result.get("workflow_context"),
                        handoff_reason=result.get("handoff_reason"),
                        batch_receipt=batch_receipt,
                    ):
                        yield event
                    return
            else:
                raise ValueError(f"Unknown decision kind: {decision_kind}")

        _kind = result.get("kind", "")

        # continue_multi_turn: 构建包含 SESSION_PATCH 的 visible_content，
        # 让 Orchestrator 通过 ADR-0080 机制自动注入 structured_findings
        if _kind == "continue_multi_turn":
            read_tools = _extract_read_tools_from_receipt(result.get("batch_receipt"))
            result["visible_content"] = _build_continue_visible_content(read_tools)

        visible_content = result.get("visible_content", "")
        if visible_content and result.get("kind") != "final_answer":
            from polaris.kernelone.llm.reasoning import strip_reasoning_tags

            clean_visible = strip_reasoning_tags(str(visible_content))
            yield ContentChunkEvent(
                turn_id=turn_id,
                chunk=clean_visible,
                is_thinking=False,
                is_finalization=True,
            )

        _kind = result.get("kind", "")
        completion_status: Literal["success", "failed", "handoff", "suspended"] = (
            "handoff" if _kind == "handoff_workflow" else "suspended" if _kind == "ask_user" else "success"
        )
        _finalization = result.get("finalization") or {}
        yield CompletionEvent(
            turn_id=turn_id,
            status=completion_status,
            duration_ms=result.get("metrics", {}).get("duration_ms", 0),
            llm_calls=result.get("metrics", {}).get("llm_calls", 0),
            tool_calls=result.get("metrics", {}).get("tool_calls", 0),
            monitoring=self.extract_monitoring_metrics(result.get("metrics", {})),
            visible_content=visible_content,
            turn_kind=_kind,
            batch_receipt=batch_receipt or {},
            error=_finalization.get("suspended_reason") or _finalization.get("error") if _kind == "ask_user" else None,
        )
