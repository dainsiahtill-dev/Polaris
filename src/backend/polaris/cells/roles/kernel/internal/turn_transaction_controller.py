"""
Turn Transaction Controller - 事务化Turn执行器 (Facade)

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

## 职责边界（P0-012 明确化）

TurnTransactionController 是**新架构**的事务化执行器，与 TurnEngine（旧架构）职责边界：

| 方法 | TurnEngine（旧） | TurnTransactionController（新） |
|------|-----------------|-------------------------------|
| 执行入口 | `run()` / `run_stream()` | `execute()` / `execute_stream()` |
| 执行模式 | while循环直到停止 | 单次事务化执行 |
| 状态管理 | ConversationState + PolicyLayer | TurnStateMachine + TurnLedger |
| 工具执行 | `kernel._execute_single_tool()` | `self.tool_runtime()` |
| 停止条件 | PolicyLayer.evaluate() | State Machine 状态转换 |
| LLM调用 | `self._llm_caller.call()` | `self.llm_provider()` |

**执行路径**：
- TransactionKernel is the canonical execution path.
- Workflow handoff is explicit and must surface runtime failures as turn errors.

核心职责：
1. 替代旧的continuation loop，执行显式事务化turn
2. 确保LLM_ONCE finalization强制tool_choice=none
3. 协调state machine、decision decoder、tool runtime
4. 提供流式/run两种执行模式

关键约束：
- 工具执行后禁止自动继续（continuation loop已死）
- LLM_ONCE收口时tool_choice=none，LLM不能触发新工具
- 复杂探索必须移交ExplorationWorkflow

## Facade 架构

本文件已从 3900+ 行的 God Class 瘦身为 Facade，所有子域逻辑已下沉到
`transaction/` 子模块：

| 子域 | 模块 |
|------|------|
| 审计账本 | transaction.ledger |
| 意图分类 | transaction.intent_classifier |
| 合约守卫 | transaction.contract_guards |
| 任务契约 | transaction.task_contract_builder |
| 工具批次执行 | transaction.tool_batch_executor |
| 收口策略 | transaction.finalization |
| 移交处理 | transaction.handoff_handlers |
| 重试编排 | transaction.retry_orchestrator |
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.speculation.chain_speculator import ChainSpeculator
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.registry import EphemeralSpecCache, ShadowTaskRegistry
from polaris.cells.roles.kernel.internal.speculation.resolver import SpeculationResolver
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor
from polaris.cells.roles.kernel.internal.speculation.task_group import TurnScopedTaskGroup
from polaris.cells.roles.kernel.internal.speculative_executor import SpeculativeExecutor
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction import (
    adaptive_session_state,
    constants as tx_constants,
    correlation,
    delivery_intent_resolver,
    kernel_guard_asserts,
)
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    is_mutation_contract_violation,
)
from polaris.cells.roles.kernel.internal.transaction.correlation import (
    _TURN_PARENT_SPAN_ID_CONTEXT,
    _TURN_REQUEST_ID_CONTEXT,
    _TURN_SPAN_ID_CONTEXT,
)
from polaris.cells.roles.kernel.internal.transaction.decision_message_builder import (
    build_decision_messages as _build_decision_messages_impl,
)
from polaris.cells.roles.kernel.internal.transaction.decision_pipeline import (
    run_decision_pipeline,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract_resolver import (
    resolve_turn_delivery_contract,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_intent_resolver import (
    _EXPLICIT_MATERIALIZE_MODE_MARKERS,
    enforce_explicit_materialize_delivery_marker as _enforce_explicit_materialize_delivery_marker,
    has_explicit_materialize_delivery_marker as _has_explicit_materialize_delivery_marker,
)
from polaris.cells.roles.kernel.internal.transaction.final_answer_gates import (
    evaluate_materialize_violation_gate,
    evaluate_recon_required_gate,
)
from polaris.cells.roles.kernel.internal.transaction.finalization import FinalizationHandler
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import HandoffHandler
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.modification_contract import ModificationContract
from polaris.cells.roles.kernel.internal.transaction.mutation_contract_guard import (
    apply_mutation_contract_guard,
)
from polaris.cells.roles.kernel.internal.transaction.phase_manager import PhaseManager
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import RetryOrchestrator
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import StreamOrchestrator
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.transaction.truthlog_recorder import TurnTruthLogRecorder
from polaris.cells.roles.kernel.internal.transaction.turn_session_scope import turn_session_scope
from polaris.cells.roles.kernel.internal.turn_decision_decoder import DecodeConfig, TurnDecisionDecoder
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecision,
    TurnDecisionKind,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ErrorEvent,
    TurnEvent,
)

logger = logging.getLogger(__name__)

# Backward-compatible re-exports. Canonical homes:
#   transaction.delivery_intent_resolver — materialize-marker helpers
#   transaction.correlation              — correlation ContextVars
# Listed in __all__ so they remain importable from this module (tests import
# ``_enforce_explicit_materialize_delivery_marker`` here) and so ruff treats the
# imports as intentional re-exports rather than unused.
__all__ = [
    "_EXPLICIT_MATERIALIZE_MODE_MARKERS",
    "_TURN_PARENT_SPAN_ID_CONTEXT",
    "_TURN_REQUEST_ID_CONTEXT",
    "_TURN_SPAN_ID_CONTEXT",
    "TransactionConfig",
    "TurnTransactionController",
    "_enforce_explicit_materialize_delivery_marker",
    "_has_explicit_materialize_delivery_marker",
]

_MONITORING_METRIC_KEYS: tuple[str, ...] = (
    "transaction_kernel.violation_count",
    "turn.single_batch_ratio",
    "workflow.handoff_rate",
    "kernel_guard.assert_fail_rate",
    "speculative.hit_rate",
    "speculative.false_positive_rate",
)


class TurnTransactionController:
    """
    事务化Turn执行控制器（Facade）

    ## 职责边界（P0-012）

    **核心职责**：
    - 事务状态管理（TurnStateMachine）
    - 审计账本记录（TurnLedger）
    - 单次决策执行（无循环）
    - LLM_ONCE 收口强制 tool_choice=none
    - Workflow handoff 处理

    **与 TurnEngine 区别**：
    - TurnEngine: 循环引擎，while True 直到停止
    - Controller: 单次事务，状态机驱动流程

    **不负责**：
    - 循环控制（TurnEngine 负责）
    - PolicyLayer 评估（TurnEngine 负责）
    - ConversationState 管理（TurnEngine 负责）

    核心方法：
    - execute(): 执行完整turn（run模式）
    - execute_stream(): 执行turn并流式输出

    关键约束：
    1. 每个turn最多一次LLM决策请求
    2. 工具执行后要么完成，要么进入LLM_ONCE收口
    3. LLM_ONCE收口时强制tool_choice=none
    4. 禁止continuation loop
    """

    # 意图分类常量 — 单一真相来源: transaction/constants.py
    ANALYSIS_ONLY_SIGNALS = tx_constants.ANALYSIS_ONLY_SIGNALS
    STRONG_MUTATION_CN_MARKERS = tx_constants.STRONG_MUTATION_CN_MARKERS
    STRONG_MUTATION_EN_MARKERS = tx_constants.STRONG_MUTATION_EN_MARKERS
    WEAK_MUTATION_CN_MARKERS = tx_constants.WEAK_MUTATION_CN_MARKERS
    WEAK_MUTATION_EN_MARKERS = tx_constants.WEAK_MUTATION_EN_MARKERS
    DEBUG_AND_FIX_CN_MARKERS = tx_constants.DEBUG_AND_FIX_CN_MARKERS
    DEBUG_AND_FIX_EN_MARKERS = tx_constants.DEBUG_AND_FIX_EN_MARKERS
    TESTING_SIGNALS = tx_constants.TESTING_SIGNALS
    DEVOPS_CONFIG_SIGNALS = tx_constants.DEVOPS_CONFIG_SIGNALS
    PLANNING_SIGNALS = tx_constants.PLANNING_SIGNALS
    INTENT_MARKERS_REGISTRY = tx_constants.INTENT_MARKERS_REGISTRY
    _EN_ANALYSIS_RE = tx_constants._EN_ANALYSIS_RE
    _EN_STRONG_MUTATION_RE = tx_constants._EN_STRONG_MUTATION_RE
    _EN_WEAK_MUTATION_RE = tx_constants._EN_WEAK_MUTATION_RE
    _EN_DEBUG_FIX_RE = tx_constants._EN_DEBUG_FIX_RE
    _EN_TESTING_RE = tx_constants._EN_TESTING_RE
    _EN_DEVOPS_RE = tx_constants._EN_DEVOPS_RE
    _EN_PLANNING_RE = tx_constants._EN_PLANNING_RE

    def __init__(
        self,
        llm_provider: Callable,  # LLM调用接口
        tool_runtime: Callable,  # 工具运行时
        config: TransactionConfig | None = None,
        workflow_runtime: ExplorationWorkflowRuntime | None = None,
        llm_provider_stream: Callable | None = None,  # 流式LLM调用接口
        development_runtime: Any | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self.tool_runtime = tool_runtime
        self.config = config or TransactionConfig()
        self.workflow_runtime = workflow_runtime
        self.llm_provider_stream = llm_provider_stream
        self.development_runtime = development_runtime or self.config.development_runtime

        self.decoder = TurnDecisionDecoder(
            DecodeConfig(
                domain=self.config.domain,
                max_tools_per_turn=self.config.handoff_threshold_tools,
                enable_textual_fallback=True,
            )
        )

        # 事件回调
        self._event_handlers: list[Callable[[TurnEvent], None]] = []

        # 子域处理器 — 通过依赖注入解耦
        self._finalization_handler = FinalizationHandler(
            llm_provider=self.llm_provider,
            decoder=self.decoder,
            emit_event=self._emit_phase_event,
            guard_assert_no_finalization_tool_calls=self._guard_assert_no_finalization_tool_calls,
        )
        self._handoff_handler = HandoffHandler(
            workflow_runtime=self.workflow_runtime,
            development_runtime=self.development_runtime,
            emit_event=self._emit_phase_event,
            build_turn_result=self._build_turn_result,
        )
        self._tool_batch_executor = ToolBatchExecutor(
            tool_runtime=self.tool_runtime,
            config=self.config,
            emit_event=self._emit_phase_event,
            guard_assert_single_tool_batch=self._guard_assert_single_tool_batch,
            finalization_handler=self._finalization_handler,
            handoff_handler=self._handoff_handler,
            requires_mutation_intent=self._requires_mutation_intent,
        )

        # RetryOrchestrator 使用动态代理，确保 monkeypatch 能穿透到子模块
        async def _proxy_call_llm_for_decision(*a: Any, **kw: Any) -> Any:
            return await self._call_llm_for_decision(*a, **kw)

        async def _proxy_call_llm_for_decision_stream(*a: Any, **kw: Any) -> AsyncIterator[Any]:
            async for item in self._call_llm_for_decision_stream(*a, **kw):
                yield item

        async def _proxy_execute_tool_batch(*a: Any, **kw: Any) -> Any:
            return await self._tool_batch_executor.execute_tool_batch(*a, **kw)

        def _proxy_guard_assert_single_tool_batch(*a: Any, **kw: Any) -> None:
            self._guard_assert_single_tool_batch(*a, **kw)

        # Phase 3.2: Cross-turn learning state
        self._turn_outcome_history: list[dict[str, Any]] = []
        self._max_outcome_history = 50

        # Phase 3.3: Budget tracking
        self._session_token_budget = 0
        self._session_tokens_used = 0
        self._session_cost_budget = 0.0
        self._session_cost_used = 0.0

        # FIX-20250422: Session-level PhaseManager persistence across turns
        # TurnLedger is recreated per-turn, but phase must survive across turns
        self._session_phase_manager: PhaseManager | None = None
        # FIX-20250422-v3: Session-level ModificationContract persistence across turns
        self._session_modification_contract: ModificationContract | None = None

        self._retry_orchestrator = RetryOrchestrator(
            tool_runtime=self.tool_runtime,
            config=self.config,
            decoder=self.decoder,
            call_llm_for_decision=_proxy_call_llm_for_decision,
            call_llm_for_decision_stream=_proxy_call_llm_for_decision_stream,
            execute_tool_batch=_proxy_execute_tool_batch,
            guard_assert_single_tool_batch=_proxy_guard_assert_single_tool_batch,
            emit_event=self._emit_phase_event,
        )
        # Active TruthLog recorder — set during execute()/execute_stream(),
        # used by _emit_phase_event() for best-effort callback-path recording.
        self._active_truthlog_recorder: TurnTruthLogRecorder | None = None

        self._stream_orchestrator = StreamOrchestrator(
            llm_provider=self.llm_provider,
            llm_provider_stream=self.llm_provider_stream,
            decoder=self.decoder,
            emit_event=self._emit_phase_event,
            build_decision_messages=self._build_decision_messages,
            build_stream_shadow_engine=self._build_stream_shadow_engine,
            resolve_shadow_workspace=self._resolve_shadow_workspace,
            call_llm_for_decision=self._call_llm_for_decision,
            handoff_handler=self._handoff_handler,
            tool_batch_executor=self._tool_batch_executor,
            retry_orchestrator=self._retry_orchestrator,
            handle_final_answer=self._handle_final_answer,
            requires_mutation_intent_hybrid=self._requires_mutation_intent_hybrid,
            extract_monitoring_metrics=self._extract_monitoring_metrics,
            config=self.config,
        )

    @property
    def llm_provider(self) -> Callable:
        return self._llm_provider

    @llm_provider.setter
    def llm_provider(self, value: Callable) -> None:
        self._llm_provider = value
        # Propagate to submodules so monkeypatching the facade works
        if hasattr(self, "_finalization_handler") and self._finalization_handler is not None:
            self._finalization_handler.llm_provider = value
        if (
            hasattr(self, "_retry_orchestrator")
            and self._retry_orchestrator is not None
            and hasattr(self._retry_orchestrator, "llm_provider")
        ):
            self._retry_orchestrator.llm_provider = value

    def _build_tool_batch_runtime(self, workspace: str = ".") -> ToolBatchRuntime:
        """构建统一工具批运行时。"""
        return ToolBatchRuntime(
            executor=self.tool_runtime,
            context=ToolExecutionContext(
                workspace=workspace or ".",
                timeout_ms=self.config.max_tool_execution_time_ms,
            ),
        )

    def _build_stream_shadow_engine(
        self,
        workspace: str = ".",
        turn_id: str = "",
    ) -> StreamShadowEngine | None:
        """Build speculative shadow engine for stream pre-execution."""
        speculative_executor = SpeculativeExecutor(
            self._build_tool_batch_runtime(workspace),
        )
        if not speculative_executor.enabled:
            return None
        metrics = SpeculationMetrics()
        registry = ShadowTaskRegistry(
            speculative_executor=speculative_executor,
            metrics=metrics,
            cache=EphemeralSpecCache(),
        )
        resolver = SpeculationResolver(
            registry=registry,
            metrics=metrics,
        )
        salvage_governor = SalvageGovernor()
        task_group = TurnScopedTaskGroup(
            turn_id=turn_id or "unknown",
            salvage_governor=salvage_governor,
        )
        chain_speculator = ChainSpeculator(registry=registry)
        return StreamShadowEngine(
            speculative_executor,
            registry=registry,
            resolver=resolver,
            salvage_governor=salvage_governor,
            task_group=task_group,
            chain_speculator=chain_speculator,
            metrics=metrics,
        )

    def _resolve_shadow_workspace(self, context: list[dict]) -> str:
        """Resolve the target workspace for speculative execution side work."""

        configured = str(getattr(self.config, "workspace", "") or "").strip()
        if configured:
            return configured
        for message in reversed(context or []):
            if not isinstance(message, dict):
                continue
            for source in (
                message,
                message.get("context"),
                message.get("metadata"),
                message.get("context_override"),
            ):
                if not isinstance(source, dict):
                    continue
                for key in ("workspace", "workspace_full", "workspace_root"):
                    token = str(source.get(key) or "").strip()
                    if token:
                        return token
        return "."

    @staticmethod
    def _detect_target_files_known(context: list[dict]) -> bool:
        """检测上下文中是否包含明确的文件路径信息。"""
        return delivery_intent_resolver.detect_target_files_known(context)

    @staticmethod
    def _is_refusal_response(response: RawLLMResponse) -> bool:
        """检测 LLM 响应是否为拒绝执行（refusal）."""
        return delivery_intent_resolver.is_refusal_response(response)

    @staticmethod
    def _inherit_materialize_from_history(context: list[dict], latest_user_request: str) -> DeliveryContract | None:
        """多轮对话意图继承：最新消息丢失 mutation 意图时，从历史消息中恢复。

        场景：用户先说"实现 XX 功能"，之后说"继续""开始吧""OK"等短指令。
        此时 latest_user_request 不含 mutation 标记，但任务本质仍需 MATERIALIZE。

        继承条件（全部满足）：
        1. 最新消息是短指令（<=20 字符或匹配 continuation markers）
        2. 最近 3 轮历史用户消息中存在 MATERIALIZE_CHANGES 意图
        3. 无显式 [mode:analyze] 等降级指令
        """
        return delivery_intent_resolver.inherit_materialize_from_history(context, latest_user_request)

    @staticmethod
    def _apply_delivery_mode_filter(decision: TurnDecision, ledger: TurnLedger) -> TurnDecision:
        """根据 delivery_contract 过滤决策中的 write tools。

        PROPOSE_PATCH / ANALYZE_ONLY 模式下禁止 write tools。
        若检测到 write tools，过滤后降级为 FINAL_ANSWER。

        实现统一委托给 ``contract_guards.apply_delivery_mode_filter``，使 run 模式
        与 stream 模式共用同一只读/提案边界语义。
        """
        return delivery_intent_resolver.apply_delivery_mode_filter(decision, ledger)

    async def _drain_speculative_tasks(
        self,
        tasks: list[tuple[str, asyncio.Task[dict[str, Any]]]],
        *,
        ledger: TurnLedger | None = None,
        timeout_s: float = 0.2,
        shadow_engine: StreamShadowEngine | None = None,
    ) -> None:
        """Drain speculative tasks and cancel leftovers to avoid task leaks."""
        from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import drain_speculative_tasks

        await drain_speculative_tasks(tasks, ledger=ledger, timeout_s=timeout_s, shadow_engine=shadow_engine)

    @staticmethod
    def _extract_monitoring_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
        """Extract monitoring metrics from a turn metrics dict."""
        extracted: dict[str, float] = {}
        for key in _MONITORING_METRIC_KEYS:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                extracted[key] = float(value)
        return extracted

    @staticmethod
    def _guard_assert_single_decision(
        *,
        turn_id: str,
        decision_count: int,
        tool_batch_count: int | None,
        ledger: TurnLedger,
    ) -> None:
        kernel_guard_asserts.guard_assert_single_decision(
            turn_id=turn_id,
            decision_count=decision_count,
            tool_batch_count=tool_batch_count,
            ledger=ledger,
        )

    @staticmethod
    def _guard_assert_single_tool_batch(*, turn_id: str, tool_batch_count: int, ledger: TurnLedger) -> None:
        kernel_guard_asserts.guard_assert_single_tool_batch(
            turn_id=turn_id, tool_batch_count=tool_batch_count, ledger=ledger
        )

    @staticmethod
    def _guard_assert_no_hidden_continuation(
        *,
        turn_id: str,
        state_trajectory: list[str] | tuple[str, ...],
        ledger: TurnLedger,
    ) -> None:
        kernel_guard_asserts.guard_assert_no_hidden_continuation(
            turn_id=turn_id, state_trajectory=state_trajectory, ledger=ledger
        )

    @staticmethod
    def _guard_assert_no_finalization_tool_calls(
        *, turn_id: str, tool_calls: list[Any] | None, ledger: TurnLedger
    ) -> None:
        kernel_guard_asserts.guard_assert_no_finalization_tool_calls(
            turn_id=turn_id, tool_calls=tool_calls, ledger=ledger
        )

    def on_event(self, handler: Callable[[TurnEvent], None]) -> None:
        """注册事件处理器"""
        self._event_handlers.append(handler)

    @staticmethod
    def _generate_turn_request_id() -> str:
        """生成单次 execute_stream 调用内稳定的 request id。"""
        return correlation.generate_turn_request_id()

    @staticmethod
    def _generate_span_id(*, prefix: str = "span") -> str:
        """生成 span id。"""
        return correlation.generate_span_id(prefix=prefix)

    @classmethod
    def _attach_event_correlation(
        cls,
        event: TurnEvent,
        *,
        turn_request_id: str | None,
        turn_span_id: str | None,
        parent_span_id: str | None,
    ) -> TurnEvent:
        """给事件附加 correlation 信息（request/span/parent_span）。"""
        return correlation.attach_event_correlation(
            event,
            turn_request_id=turn_request_id,
            turn_span_id=turn_span_id,
            parent_span_id=parent_span_id,
        )

    @staticmethod
    def _resolve_workspace_for_truthlog(context: list[dict]) -> str:
        """Resolve workspace path for turn truthlog persistence."""
        return correlation.resolve_workspace_for_truthlog(context)

    @classmethod
    def _build_turn_truthlog_recorder(cls, context: list[dict]) -> TurnTruthLogRecorder | None:
        """Build per-turn truthlog recorder. Failures are non-fatal for turn execution."""
        return correlation.build_turn_truthlog_recorder(context)

    @staticmethod
    async def _record_turn_truthlog_event(
        recorder: TurnTruthLogRecorder,
        *,
        event: TurnEvent,
        turn_id_fallback: str,
        turn_request_id_fallback: str,
    ) -> None:
        """Best-effort append of one turn event into TruthLog."""
        await correlation.record_turn_truthlog_event(
            recorder,
            event=event,
            turn_id_fallback=turn_id_fallback,
            turn_request_id_fallback=turn_request_id_fallback,
        )

    @staticmethod
    async def _shutdown_turn_truthlog_recorder(recorder: TurnTruthLogRecorder) -> None:
        """Best-effort flush and shutdown for TruthLog recorder."""
        await correlation.shutdown_turn_truthlog_recorder(recorder)

    def _emit_phase_event(self, event: TurnEvent) -> None:
        """发送事件"""
        event_with_request_id = self._attach_event_correlation(
            event,
            turn_request_id=_TURN_REQUEST_ID_CONTEXT.get(),
            turn_span_id=_TURN_SPAN_ID_CONTEXT.get(),
            parent_span_id=_TURN_PARENT_SPAN_ID_CONTEXT.get(),
        )
        for handler in self._event_handlers:
            try:
                handler(event_with_request_id)
            except (RuntimeError, ValueError) as e:
                logger.warning("Event handler failed: %s", e)
                continue

        # Best-effort TruthLog recording for callback-emitted events.
        # _emit_phase_event is synchronous but recorder.record() is async,
        # so we schedule a fire-and-forget task on the running event loop.
        recorder = self._active_truthlog_recorder
        if recorder is not None:
            try:
                loop = asyncio.get_running_loop()
                turn_id_fallback = getattr(event_with_request_id, "turn_id", "") or ""
                request_id_fallback = (
                    getattr(event_with_request_id, "turn_request_id", "") or _TURN_REQUEST_ID_CONTEXT.get() or ""
                )
                _task = loop.create_task(
                    self._record_turn_truthlog_event(
                        recorder,
                        event=event_with_request_id,
                        turn_id_fallback=turn_id_fallback,
                        turn_request_id_fallback=request_id_fallback,
                    ),
                    name="truthlog_callback_record",
                )
                # Fire-and-forget: suppress unhandled exception warnings
                _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except RuntimeError:
                # No running event loop (e.g., during shutdown)
                pass

    # ---------------------------------------------------------------------------
    # 意图分类（hybrid 版本保留在 Facade，纯 regex 版本已下沉到 intent_classifier）
    # ---------------------------------------------------------------------------

    @classmethod
    def _classify_user_intent(cls, message: str) -> str:
        """对用户消息进行意图分类，返回最匹配的意图类别。

        委托给 intent_classifier.classify_intent_regex 以消除代码重复。
        """
        return delivery_intent_resolver.classify_user_intent(message)

    async def _requires_mutation_intent_hybrid(self, message: str) -> bool:
        """Async hybrid version of _requires_mutation_intent.

        统一委托 CognitiveGateway（Embedding -> SLM -> Regex 级联瀑布），
        不再保留本地 hybrid 路径，确保全系统意图分类单一真相来源。
        """
        return await delivery_intent_resolver.requires_mutation_intent_hybrid(message)

    @classmethod
    def _requires_mutation_intent(cls, message: str) -> bool:
        """判定用户请求是否要求代码/文件突变（需要写工具）。"""
        return delivery_intent_resolver.requires_mutation_intent(message)

    @staticmethod
    async def _resolve_delivery_mode_hybrid(user_message: str) -> DeliveryContract:
        """SLM 优先、regex 兜底的 delivery mode 解析。

        先尝试 CognitiveGateway（统一级联入口），若不可用则回退到
        本地 regex 规则引擎。保证永远有返回值。
        """
        return await delivery_intent_resolver.resolve_delivery_mode_hybrid(user_message)

    @classmethod
    def _requires_verification_intent(cls, message: str) -> bool:
        """判定用户请求是否要求验证/测试（需要 test/verify 类工具）。"""
        return delivery_intent_resolver.requires_verification_intent(message)

    # ---------------------------------------------------------------------------
    # Backward-compat proxies (tests monkeypatch these on the controller instance)
    # ---------------------------------------------------------------------------

    async def _execute_tool_batch(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        context: list[dict],
        *,
        stream: bool = False,
        shadow_engine: Any | None = None,
        allowed_tool_names: set[str] | None = None,
        count_towards_batch_limit: bool = True,
    ) -> dict:
        """Proxy to ToolBatchExecutor.execute_tool_batch."""
        return await self._tool_batch_executor.execute_tool_batch(
            decision,
            state_machine,
            ledger,
            context,
            stream=stream,
            shadow_engine=shadow_engine,
            allowed_tool_names=allowed_tool_names,
            count_towards_batch_limit=count_towards_batch_limit,
        )

    async def _retry_tool_batch_after_contract_violation(
        self,
        *,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        stream: bool = False,
        shadow_engine: Any | None = None,
        original_decision: Any | None = None,
    ) -> dict:
        """Proxy to RetryOrchestrator.retry_tool_batch_after_contract_violation."""
        return await self._retry_orchestrator.retry_tool_batch_after_contract_violation(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            state_machine=state_machine,
            ledger=ledger,
            stream=stream,
            shadow_engine=shadow_engine,
            original_decision=original_decision,
        )

    async def _execute_read_bootstrap_batch(
        self,
        *,
        turn_id: str,
        workspace: str,
        tool_batch: Any,
        ledger: TurnLedger,
    ) -> dict[str, Any] | None:
        """Proxy to RetryOrchestrator.execute_read_bootstrap_batch."""
        return await self._retry_orchestrator.execute_read_bootstrap_batch(
            turn_id=turn_id,
            workspace=workspace,
            tool_batch=tool_batch,
            ledger=ledger,
        )

    def _build_finalization_context(self, original_context: list[dict], receipts: list[dict]) -> list[dict]:
        """Proxy to FinalizationHandler._build_finalization_context."""
        return FinalizationHandler._build_finalization_context(original_context, receipts)

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def execute(
        self,
        turn_id: str,
        context: list[dict],  # 对话上下文
        tool_definitions: list[dict],
    ) -> dict:
        """
        执行完整turn（run模式）

        执行流程：
        1. 构建context -> DECISION_REQUESTED
        2. 调用LLM -> DECISION_RECEIVED
        3. 解码决策 -> DECISION_DECODED
        4. [分支] 直接回答 -> FINAL_ANSWER_READY
        5. [分支] 工具调用 -> TOOL_BATCH_EXECUTING -> TOOL_BATCH_EXECUTED
        6. [分支] LLM_ONCE收口 -> FINALIZATION_REQUESTED
        7. 完成 -> COMPLETED

        Returns TurnResult dict
        """
        # Correlation context vars — mirror execute_stream() so that
        # _emit_phase_event() can attach request/span IDs in non-stream mode.
        # Session/correlation/truthlog setup + teardown is shared with
        # execute_stream via turn_session_scope.
        async with turn_session_scope(
            self,
            turn_id=turn_id,
            context=context,
            turn_request_id=None,
            parent_span_id=None,
        ) as scope:
            state_machine = scope.state_machine
            ledger = scope.ledger
            try:
                logger.debug("[DEBUG] turn_execute_start: turn_id=%s mode=run", turn_id)
                result = await self._execute_turn(
                    turn_id, context, tool_definitions, state_machine, ledger, stream=False
                )
                result.setdefault("ledger", ledger)
                result["state_trajectory"] = [s[0] for s in ledger.state_history]
                logger.debug(
                    "[DEBUG] turn_execute_end: turn_id=%s kind=%s terminal=%s",
                    turn_id,
                    result.get("kind", "unknown"),
                    state_machine.is_terminal(),
                )

                # Phase 3.2: Record successful turn outcome
                metrics = result.get("metrics", {})
                tokens_used = metrics.get("llm_calls", 0) * 500
                self._record_turn_outcome(
                    turn_id=turn_id,
                    success=True,
                    tokens_used=tokens_used,
                )

                return result
            except Exception as e:
                logger.exception("execute failed: turn_id=%s", turn_id)

                # Phase 3.2: Record failed turn outcome
                self._record_turn_outcome(
                    turn_id=turn_id,
                    success=False,
                    error=str(e),
                )

                ledger.finalize()
                with contextlib.suppress(TypeError):
                    vars(e)["turn_ledger"] = ledger
                self._emit_phase_event(
                    ErrorEvent(
                        turn_id=turn_id,
                        error_type=type(e).__name__,
                        message=str(e),
                        state_at_error=state_machine.state.name,
                    )
                )
                raise

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        turn_request_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """
        流式执行turn

        产出事件序列，供CLI实时渲染
        """
        # Session/correlation/truthlog setup + teardown is shared with
        # execute() via turn_session_scope.
        async with turn_session_scope(
            self,
            turn_id=turn_id,
            context=context,
            turn_request_id=turn_request_id,
            parent_span_id=parent_span_id,
        ) as scope:
            state_machine = scope.state_machine
            ledger = scope.ledger
            effective_turn_request_id = scope.effective_turn_request_id
            effective_turn_span_id = scope.effective_turn_span_id
            truthlog_recorder = scope.truthlog_recorder
            try:
                async for event in self._execute_turn_stream(turn_id, context, tool_definitions, state_machine, ledger):
                    event_with_request_id = self._attach_event_correlation(
                        event,
                        turn_request_id=effective_turn_request_id,
                        turn_span_id=effective_turn_span_id,
                        parent_span_id=parent_span_id,
                    )
                    if truthlog_recorder is not None:
                        await self._record_turn_truthlog_event(
                            truthlog_recorder,
                            event=event_with_request_id,
                            turn_id_fallback=turn_id,
                            turn_request_id_fallback=effective_turn_request_id,
                        )
                    yield event_with_request_id
            except Exception as e:
                logger.exception("execute_stream failed: turn_id=%s", turn_id)
                ledger.finalize()
                error_event = self._attach_event_correlation(
                    ErrorEvent(
                        turn_id=turn_id,
                        error_type=type(e).__name__,
                        message=str(e),
                        state_at_error=state_machine.state.name,
                    ),
                    turn_request_id=effective_turn_request_id,
                    turn_span_id=effective_turn_span_id,
                    parent_span_id=parent_span_id,
                )
                if truthlog_recorder is not None:
                    await self._record_turn_truthlog_event(
                        truthlog_recorder,
                        event=error_event,
                        turn_id_fallback=turn_id,
                        turn_request_id_fallback=effective_turn_request_id,
                    )
                yield error_event
                raise

    # ---------------------------------------------------------------------------
    # Core orchestration
    # ---------------------------------------------------------------------------

    async def _execute_turn(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        stream: bool = False,
    ) -> dict:
        """核心turn执行逻辑（run模式）"""

        # === Phase 1: 构建Context ===
        state_machine.transition_to(TurnState.CONTEXT_BUILT)
        ledger.state_history.append(("CONTEXT_BUILT", int(time.time() * 1000)))
        logger.debug("[DEBUG] turn_phase: turn_id=%s phase=CONTEXT_BUILT", turn_id)

        # === Phase 1b: 解析交付契约 ===
        # Facade-bound helpers are passed as callables so test-time monkeypatch
        # of ``_resolve_delivery_mode_hybrid`` / ``_inherit_materialize_from_history``
        # on the instance still penetrates the resolver module.
        delivery_contract = await resolve_turn_delivery_contract(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            ledger=ledger,
            resolve_delivery_mode_hybrid=self._resolve_delivery_mode_hybrid,
            inherit_materialize_from_history=self._inherit_materialize_from_history,
            role_id=self.config.role_id,
        )

        ledger.set_delivery_contract(delivery_contract)
        ledger.mutation_obligation.target_files_known = self._detect_target_files_known(context)
        logger.debug(
            "[DEBUG] turn_delivery_contract: turn_id=%s mode=%s requires_mutation=%s",
            turn_id,
            delivery_contract.mode.value,
            delivery_contract.requires_mutation,
        )

        # === Phase 2+3: 请求决策 + 解码 ===
        # Facade-bound helpers are passed as callables/objects so test-time
        # monkeypatch of decoder / _call_llm_for_decision / _apply_delivery_mode_filter
        # on the instance still penetrates the pipeline module.
        decision = await run_decision_pipeline(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            state_machine=state_machine,
            ledger=ledger,
            decoder=self.decoder,
            call_llm_for_decision=self._call_llm_for_decision,
            apply_delivery_mode_filter=self._apply_delivery_mode_filter,
            guard_assert_single_decision=self._guard_assert_single_decision,
            emit_event=self._emit_phase_event,
        )

        # === Phase 4: 执行决策 ===
        decision_kind = decision.get("kind")
        guard_mode = str(getattr(self.config, "mutation_guard_mode", "warn"))
        # Phase-4 mutation-contract guard reconciliation. Facade-bound helpers are
        # passed as callables so test-time monkeypatch on the instance penetrates.
        # A non-None result is the blocking retry result to return directly.
        guard_result = await apply_mutation_contract_guard(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            decision_kind=decision_kind,
            state_machine=state_machine,
            ledger=ledger,
            guard_mode=guard_mode,
            requires_mutation_intent_hybrid=self._requires_mutation_intent_hybrid,
            build_stream_shadow_engine=self._build_stream_shadow_engine,
            resolve_shadow_workspace=self._resolve_shadow_workspace,
            retry_tool_batch_after_contract_violation=(
                self._retry_orchestrator.retry_tool_batch_after_contract_violation
            ),
        )
        if guard_result is not None:
            return guard_result

        if decision_kind == TurnDecisionKind.FINAL_ANSWER:
            return await self._handle_final_answer(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.HANDOFF_WORKFLOW:
            return await self._handoff_handler.handle_handoff(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.HANDOFF_DEVELOPMENT:
            return await self._handoff_handler.handle_development_handoff(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.ASK_USER:
            return await self._handoff_handler.handle_ask_user(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.TOOL_BATCH:
            shadow_engine = self._build_stream_shadow_engine(
                workspace=self._resolve_shadow_workspace(context),
                turn_id=turn_id,
            )
            allowed_tool_names = extract_allowed_tool_names_from_definitions(tool_definitions)
            try:
                return await self._tool_batch_executor.execute_tool_batch(
                    decision,
                    state_machine,
                    ledger,
                    context,
                    stream=False,
                    shadow_engine=shadow_engine,
                    allowed_tool_names=allowed_tool_names,
                )
            except RuntimeError as exc:
                if not is_mutation_contract_violation(exc):
                    raise
                return await self._retry_orchestrator.retry_tool_batch_after_contract_violation(
                    turn_id=turn_id,
                    context=context,
                    tool_definitions=tool_definitions,
                    state_machine=state_machine,
                    ledger=ledger,
                    stream=False,
                    shadow_engine=shadow_engine,
                    # Wave-5: bootstrap a READ-ONLY violating batch directly instead
                    # of discarding the model's reads and re-asking.
                    original_decision=decision,
                )

        else:
            raise ValueError(f"Unknown decision kind: {decision_kind}")

    async def _execute_turn_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
    ) -> AsyncIterator[TurnEvent]:
        """Proxy to StreamOrchestrator.execute_turn_stream."""
        async for event in self._stream_orchestrator.execute_turn_stream(
            turn_id,
            context,
            tool_definitions,
            state_machine,
            ledger,
            call_llm_for_decision_stream=self._call_llm_for_decision_stream,
        ):
            yield event

    # ---------------------------------------------------------------------------
    # LLM 调用
    # ---------------------------------------------------------------------------

    async def _call_llm_for_decision(
        self,
        context: list[dict],
        tool_definitions: list[dict],
        ledger: TurnLedger,
        *,
        tool_choice_override: Any | None = None,
        model_override: str | None = None,
        temperature_override: float | None = None,
        max_tokens_floor: int | None = None,
    ) -> RawLLMResponse:
        """调用LLM获取决策

        Phase 3.1: Integrates adaptive model routing based on task complexity.
        Phase 3.3: Tracks token usage for budget management.
        I3-r22 (F10): ``max_tokens_floor`` reserves a reasoning-sized output
        budget for retry/re-ask calls so a large prompt cannot starve generation.
        """
        decision_messages = self._build_decision_messages(context, tool_definitions, ledger)

        # Phase 3.1: Adaptive model routing
        task_complexity = self._estimate_task_complexity(context)
        adaptive_model = self._select_model_for_task(context, task_complexity)

        # Use adaptive model if no explicit override provided
        effective_model = model_override if model_override else adaptive_model
        normalized_model_override = str(effective_model or "").strip() or None

        request_payload = {
            "messages": decision_messages,
            "tools": tool_definitions if tool_definitions else None,
            "tool_choice": (
                tool_choice_override if tool_choice_override is not None else ("auto" if tool_definitions else None)
            ),
            "model_override": normalized_model_override,
            # ADR-0090 W2.6: phase-aware low temperature for escalated retries.
            "temperature_override": temperature_override,
            # I3-r22 (F10): reasoning-sized reserved output floor for retry calls.
            "max_tokens_floor": max_tokens_floor,
        }

        # Phase 3.3: Check budget before making call
        budget_status = self._check_budget()
        if budget_status.get("token_exceeded") or budget_status.get("cost_exceeded"):
            logger.warning(
                "budget_exceeded_before_llm: token_exceeded=%s cost_exceeded=%s",
                budget_status.get("token_exceeded"),
                budget_status.get("cost_exceeded"),
            )

        response = await self.llm_provider(request_payload)
        response_usage = response.get("usage", {}) if isinstance(response.get("usage", {}), dict) else {}
        response_context_os_audit = response_usage.get("context_os_audit") if isinstance(response_usage, dict) else None
        response_llm_metadata: dict[str, Any] = {}
        if isinstance(response_usage, dict):
            for key in (
                "context_os_audit",
                "final_request_context_audit",
                "context_snapshot_ref",
                "context_snapshot_degraded",
                "context_snapshot_degraded_reason",
                "context_tokens_after",
                "contextTokens",
                "usage",
                "usage_source",
            ):
                if key in response_usage:
                    value = response_usage.get(key)
                    response_llm_metadata[key] = dict(value) if isinstance(value, dict) else value

        # Phase 3.3: Track usage
        raw_provider_usage = response_usage.get("usage")
        provider_usage: dict[str, Any] = dict(raw_provider_usage) if isinstance(raw_provider_usage, dict) else {}

        def _safe_token_count(value: Any) -> int:
            if isinstance(value, bool) or value is None:
                return 0
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        prompt_tokens = _safe_token_count(response_usage.get("prompt_tokens") or provider_usage.get("prompt_tokens"))
        completion_tokens = _safe_token_count(
            response_usage.get("completion_tokens") or provider_usage.get("completion_tokens")
        )
        tokens_used = prompt_tokens + completion_tokens
        cost = response.get("cost", 0.0)
        self._track_token_usage(tokens_used, cost)

        ledger.record_llm_call(
            phase="decision",
            model=response.get("model", "unknown"),
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
            metadata=response_llm_metadata
            or (
                {"context_os_audit": dict(response_context_os_audit)}
                if isinstance(response_context_os_audit, dict)
                else None
            ),
        )

        thinking = response.get("thinking")
        if thinking is not None and not isinstance(thinking, str):
            thinking = None
        return RawLLMResponse(
            content=response.get("content", ""),
            thinking=thinking,
            native_tool_calls=response.get("tool_calls", []),
            model=response.get("model", "unknown"),
            usage=response_usage,
        )

    async def _call_llm_for_decision_stream(
        self,
        context: list[dict],
        tool_definitions: list[dict],
        ledger: TurnLedger,
        shadow_engine: StreamShadowEngine | None = None,
        *,
        tool_choice_override: Any | None = None,
        model_override: str | None = None,
        temperature_override: float | None = None,
        max_tokens_floor: int | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Proxy to StreamOrchestrator._call_llm_for_decision_stream_impl."""
        async for event in self._stream_orchestrator._call_llm_for_decision_stream_impl(
            context,
            tool_definitions,
            ledger,
            shadow_engine=shadow_engine,
            tool_choice_override=tool_choice_override,
            model_override=model_override,
            temperature_override=temperature_override,
            max_tokens_floor=max_tokens_floor,
        ):
            yield event

    # ---------------------------------------------------------------------------
    # 决策消息构建
    # ---------------------------------------------------------------------------

    def _build_decision_messages(
        self,
        context: list[dict],
        tool_definitions: list[dict],
        ledger: TurnLedger | None = None,
    ) -> list[dict]:
        """Build decision-stage messages with single-batch execution constraints.

        Delegates to ``decision_message_builder.build_decision_messages``; the
        method is preserved on the facade because it is injected as a callback
        into ``StreamOrchestrator`` and is monkeypatched by tests.
        """
        return _build_decision_messages_impl(context, tool_definitions, ledger)

    # ---------------------------------------------------------------------------
    # 结果构建
    # ---------------------------------------------------------------------------

    def _build_turn_result(
        self,
        turn_id: str,
        kind: str,
        visible_content: str,
        decision: TurnDecision,
        batch_receipt: dict | None,
        finalization: dict | None,
        ledger: TurnLedger,
        workflow_context: dict | None = None,
    ) -> dict:
        """构建符合契约的 TurnResult dict.

        Session-level state (``_session_phase_manager``,
        ``_session_modification_contract``) that persists across turns is
        snapshotted into the ledger *before* finalization so that cross-turn
        mutations are auditable via the single commit path.
        """
        # --- Snapshot session-level state into the ledger (single commit point) ---
        ledger.record_session_state_snapshot(
            phase_manager_state=(
                self._session_phase_manager.to_dict() if self._session_phase_manager is not None else None
            ),
            modification_contract_state=(
                self._session_modification_contract.to_dict()
                if self._session_modification_contract is not None
                else None
            ),
        )

        ledger.record_tool_batch_resolved(kind)
        self._guard_assert_no_hidden_continuation(
            turn_id=turn_id,
            state_trajectory=[state for state, _ in ledger.state_history],
            ledger=ledger,
        )
        metrics: dict[str, int | float] = {
            "duration_ms": ledger.get_duration_ms(),
            "llm_calls": len(ledger.llm_calls),
            "tool_calls": len(ledger.tool_executions),
        }
        metrics.update(ledger.build_monitoring_metrics(final_kind=kind))

        try:
            get_metrics_collector().record_transaction_metrics(metrics)
        except (ConnectionError, RuntimeError, TypeError, ValueError):
            logger.exception("Failed to record transaction metrics")

        result: dict = {
            "turn_id": turn_id,
            "kind": kind,
            "visible_content": visible_content,
            "decision": {
                "kind": decision.get("kind").value
                if hasattr(decision.get("kind"), "value")
                else str(decision.get("kind", "")),
                "finalize_mode": decision.get("finalize_mode").value
                if hasattr(decision.get("finalize_mode"), "value")
                else str(decision.get("finalize_mode", "")),
            },
            "metrics": metrics,
            "state_trajectory": [s[0] for s in ledger.state_history],
        }

        if batch_receipt:
            result["batch_receipt"] = batch_receipt
        if finalization:
            result["finalization"] = finalization
        if workflow_context:
            result["workflow_context"] = workflow_context

        for llm_call in reversed(ledger.llm_calls):
            raw_metadata = llm_call.get("metadata") if isinstance(llm_call, dict) else None
            if isinstance(raw_metadata, dict) and raw_metadata:
                result["llm_response_metadata"] = dict(raw_metadata)
                break

        # Expose the full ledger so callers can commit it to the ContextOS snapshot
        result["ledger"] = ledger

        return result

    # ---------------------------------------------------------------------------
    # Phase 3.1: Adaptive Model Routing
    # ---------------------------------------------------------------------------

    def _select_model_for_task(
        self,
        context: list[dict],
        task_complexity: str = "medium",
    ) -> str | None:
        """Phase 3.1: Select optimal model based on task characteristics.

        Args:
            context: Conversation context
            task_complexity: Estimated task complexity (low/medium/high/complex)

        Returns:
            Model name to use, or None for default model
        """
        return adaptive_session_state.select_model_for_task(self._turn_outcome_history, task_complexity)

    def _estimate_task_complexity(self, context: list[dict]) -> str:
        """Estimate task complexity from context.

        Args:
            context: Conversation context

        Returns:
            Complexity level: low/medium/high/complex
        """
        return adaptive_session_state.estimate_task_complexity(context)

    # ---------------------------------------------------------------------------
    # Phase 3.2: Cross-Turn Learning
    # ---------------------------------------------------------------------------

    def _record_turn_outcome(
        self,
        turn_id: str,
        success: bool,
        error: str | None = None,
        tokens_used: int = 0,
        cost: float = 0.0,
    ) -> None:
        """Phase 3.2: Record turn outcome for learning.

        Args:
            turn_id: Turn identifier
            success: Whether turn succeeded
            error: Error message if failed
            tokens_used: Total tokens consumed
            cost: Total cost incurred
        """
        self._turn_outcome_history = adaptive_session_state.record_turn_outcome(
            self._turn_outcome_history,
            self._max_outcome_history,
            turn_id=turn_id,
            success=success,
            error=error,
            tokens_used=tokens_used,
            cost=cost,
        )

    def _learn_from_history(self, error_pattern: str) -> list[str]:
        """Phase 3.2: Generate correction hints based on failure patterns.

        Args:
            error_pattern: Error type to analyze

        Returns:
            List of correction hints
        """
        return adaptive_session_state.learn_from_history(self._turn_outcome_history, error_pattern)

    def _get_learned_constraints(self) -> dict[str, Any]:
        """Phase 3.2: Get constraints learned from turn history.

        Returns:
            Dict of learned constraints for this session
        """
        return adaptive_session_state.get_learned_constraints(self._turn_outcome_history)

    # ---------------------------------------------------------------------------
    # Phase 3.3: Budget-Aware Execution
    # ---------------------------------------------------------------------------

    def _init_session_budget(
        self,
        token_budget: int = 0,
        cost_budget: float = 0.0,
    ) -> None:
        """Phase 3.3: Initialize session budget.

        Args:
            token_budget: Maximum tokens for session (0 = unlimited)
            cost_budget: Maximum cost for session (0.0 = unlimited)
        """
        self._session_token_budget = token_budget
        self._session_cost_budget = cost_budget
        self._session_tokens_used = 0
        self._session_cost_used = 0.0
        adaptive_session_state.log_session_budget_initialized(token_budget, cost_budget)

    def _track_token_usage(self, tokens: int, cost: float = 0.0) -> None:
        """Phase 3.3: Track token and cost usage.

        Args:
            tokens: Tokens consumed this turn
            cost: Cost incurred this turn
        """
        self._session_tokens_used += tokens
        self._session_cost_used += cost

    def _check_budget(self) -> dict[str, Any]:
        """Phase 3.3: Check budget status and return warnings.

        Returns:
            Budget status with warnings if approaching limits
        """
        return adaptive_session_state.check_budget(
            session_tokens_used=self._session_tokens_used,
            session_token_budget=self._session_token_budget,
            session_cost_used=self._session_cost_used,
            session_cost_budget=self._session_cost_budget,
        )

    # ---------------------------------------------------------------------------
    # Final answer handler（保留在 Facade，因需调用 _build_turn_result）
    # ---------------------------------------------------------------------------

    async def _handle_final_answer(
        self, decision: TurnDecision, state_machine: TurnStateMachine, ledger: TurnLedger
    ) -> dict:
        """处理直接回答"""
        turn_id = decision.get("turn_id")

        state_machine.transition_to(TurnState.FINAL_ANSWER_READY)
        ledger.state_history.append(("FINAL_ANSWER_READY", int(time.time() * 1000)))
        logger.debug("[DEBUG] turn_phase: turn_id=%s phase=FINAL_ANSWER_READY", turn_id)

        visible_content = decision.get("visible_message", "")

        # MATERIALIZE_CHANGES 写侧门禁（Invariant A）与 recon_required 读侧门禁
        # (ADR-0091 R1) 共享同一 block tail。门禁判定 + 账本副作用下沉到
        # ``final_answer_gates``；两个门禁均为 block-only（不注入第二个
        # TurnDecision、不注入 ToolBatch，ADR-0071 兼容）。
        block = evaluate_materialize_violation_gate(
            turn_id=turn_id,
            visible_content=visible_content,
            ledger=ledger,
        ) or evaluate_recon_required_gate(
            turn_id=turn_id,
            visible_content=visible_content,
            ledger=ledger,
            recon_required=self.config.recon_required,
        )
        if block is not None:
            state_machine.transition_to(TurnState.COMPLETED)
            ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
            ledger.finalize()
            self._emit_phase_event(
                CompletionEvent(
                    turn_id=turn_id,
                    status="failed",
                    duration_ms=ledger.get_duration_ms(),
                    llm_calls=len(ledger.llm_calls),
                    tool_calls=0,
                )
            )
            return self._build_turn_result(
                turn_id=turn_id,
                kind=block.kind,
                visible_content=visible_content,
                decision=decision,
                batch_receipt=None,
                finalization=block.finalization,
                ledger=ledger,
            )

        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()
        logger.debug("[DEBUG] turn_phase: turn_id=%s phase=COMPLETED kind=final_answer", turn_id)

        self._emit_phase_event(
            CompletionEvent(
                turn_id=turn_id,
                status="success",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=0,
            )
        )
        return self._build_turn_result(
            turn_id=turn_id,
            kind="final_answer",
            visible_content=visible_content,
            decision=decision,
            batch_receipt=None,
            finalization=None,
            ledger=ledger,
        )
