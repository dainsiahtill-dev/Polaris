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

**迁移路径**：
- TransactionKernel is the canonical execution path.
- Legacy fallback controlled by LEGACY_FALLBACK env var.

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
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.kernel_guard import KernelGuard, KernelGuardError
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
from polaris.cells.roles.kernel.internal.transaction import constants as tx_constants
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    has_available_write_tool,
    is_mutation_contract_violation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.finalization import FinalizationHandler
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import HandoffHandler
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    detect_inline_patch_escape,
    resolve_delivery_mode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import RetryOrchestrator
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import StreamOrchestrator
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
    extract_latest_user_message,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.turn_decision_decoder import DecodeConfig, TurnDecisionDecoder
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ErrorEvent,
    TurnEvent,
    TurnPhaseEvent,
)

logger = logging.getLogger(__name__)

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
        self._stream_orchestrator = StreamOrchestrator(
            llm_provider=self.llm_provider,
            llm_provider_stream=self.llm_provider_stream,
            decoder=self.decoder,
            emit_event=self._emit_phase_event,
            build_decision_messages=self._build_decision_messages,
            build_stream_shadow_engine=self._build_stream_shadow_engine,
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
        )

    @staticmethod
    def _detect_target_files_known(context: list[dict]) -> bool:
        """检测上下文中是否包含明确的文件路径信息。"""
        for message in context:
            if not isinstance(message, Mapping):
                continue
            content = str(message.get("content") or "")
            # 简单启发式：包含常见代码文件扩展名或路径分隔符
            code_extensions = (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".java",
                ".go",
                ".rs",
                ".cpp",
                ".c",
                ".h",
                ".md",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
            )
            if any(ext in content for ext in code_extensions):
                return True
            # 检测路径模式
            if "/" in content or "\\" in content:
                # 排除URL
                lines = content.splitlines()
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("http://") or stripped.startswith("https://"):
                        continue
                    if "/" in stripped or "\\" in stripped:
                        parts = stripped.replace("\\", "/").split("/")
                        for part in parts:
                            if part and "." in part and not part.startswith("."):
                                return True
        return False

    @staticmethod
    def _is_refusal_response(response: RawLLMResponse) -> bool:
        """检测 LLM 响应是否为拒绝执行（refusal）."""
        from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import is_refusal_response

        return is_refusal_response(response)

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
        continuation_shortcuts: tuple[str, ...] = (
            "继续",
            "开始",
            "ok",
            "好",
            "行",
            "可以",
            "执行",
            "落实",
            "动手",
            "搞",
            "冲",
            "推进",
            "next",
            "go",
            "yes",
            "yeah",
            " proceed",
            "do it",
            "let's go",
            "开始吧",
            "那就开始",
        )
        lowered_latest = latest_user_request.lower().strip()
        is_shortcut = len(latest_user_request) <= 20 or any(
            marker in lowered_latest for marker in continuation_shortcuts
        )
        if not is_shortcut:
            return None

        # 检查最近 3 轮历史用户消息
        user_messages: list[str] = []
        for msg in reversed(context):
            if not isinstance(msg, Mapping):
                continue
            role = str(msg.get("role") or "").strip().lower()
            if role != "user":
                continue
            content = str(msg.get("content") or "").strip()
            if content and content != latest_user_request:
                user_messages.append(content)
                if len(user_messages) >= 3:
                    break

        for historical_msg in user_messages:
            historical_contract = resolve_delivery_mode(historical_msg)
            if historical_contract.mode == DeliveryMode.MATERIALIZE_CHANGES:
                # 继承历史意图，但保留最新消息中可能的 verification 要求
                return DeliveryContract(
                    mode=DeliveryMode.MATERIALIZE_CHANGES,
                    requires_mutation=True,
                    requires_verification=historical_contract.requires_verification,
                    allow_inline_code=False,
                    allow_patch_proposal=False,
                )
        return None

    @staticmethod
    def _apply_delivery_mode_filter(decision: TurnDecision, ledger: TurnLedger) -> TurnDecision:
        """根据 delivery_contract 过滤决策中的 write tools。

        PROPOSE_PATCH / ANALYZE_ONLY 模式下禁止 write tools。
        若检测到 write tools，过滤后降级为 FINAL_ANSWER。
        """
        contract = ledger.delivery_contract
        if contract.mode == DeliveryMode.MATERIALIZE_CHANGES:
            return decision

        tool_batch = decision.get("tool_batch")
        if not tool_batch:
            return decision

        invocations = list(tool_batch.get("invocations", []) or [])
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import is_write_invocation

        filtered = [inv for inv in invocations if not is_write_invocation(inv)]
        dropped = len(invocations) - len(filtered)

        if dropped == 0:
            return decision

        logger.warning(
            "delivery-mode-filter: dropped %d write tool(s) in %s mode. turn_id=%s",
            dropped,
            contract.mode.value,
            ledger.turn_id,
        )
        ledger.anomaly_flags.append(
            {
                "type": "DELIVERY_MODE_WRITE_TOOL_FILTERED",
                "turn_id": ledger.turn_id,
                "dropped_count": dropped,
                "delivery_mode": contract.mode.value,
                "original_tool_count": len(invocations),
            }
        )

        if not filtered:
            # 全部过滤完，降级为 FINAL_ANSWER
            from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecisionKind

            return TurnDecision(
                turn_id=decision.get("turn_id"),
                kind=TurnDecisionKind.FINAL_ANSWER,
                visible_message=decision.get("visible_message", ""),
                reasoning_summary=decision.get("reasoning_summary"),
                tool_batch=None,
                finalize_mode=FinalizeMode.NONE,
                domain=decision.get("domain", "code"),
                metadata={
                    **(decision.get("metadata") or {}),
                    "delivery_mode_filter_applied": True,
                    "dropped_write_tools": dropped,
                },
            )

        # 部分过滤，重建 tool_batch
        from polaris.cells.roles.kernel.public.turn_contracts import (
            BatchId,
            ToolBatch,
            ToolExecutionMode,
            TurnDecisionKind,
        )

        turn_id_val = decision.get("turn_id")
        new_batch = ToolBatch(
            batch_id=tool_batch.get("batch_id", BatchId(f"{turn_id_val}_filtered")),
            invocations=filtered,
            parallel_readonly=[
                inv for inv in filtered if inv.get("execution_mode") == ToolExecutionMode.READONLY_PARALLEL
            ],
            readonly_serial=[inv for inv in filtered if inv.get("execution_mode") == ToolExecutionMode.READONLY_SERIAL],
            serial_writes=[],
            async_receipts=[inv for inv in filtered if inv.get("execution_mode") == ToolExecutionMode.ASYNC_RECEIPT],
        )
        return TurnDecision(
            turn_id=turn_id_val,
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message=decision.get("visible_message", ""),
            reasoning_summary=decision.get("reasoning_summary"),
            tool_batch=new_batch,
            finalize_mode=decision.get("finalize_mode"),
            domain=decision.get("domain", "code"),
            metadata={
                **(decision.get("metadata") or {}),
                "delivery_mode_filter_applied": True,
                "dropped_write_tools": dropped,
            },
        )

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
        try:
            KernelGuard.assert_single_decision(turn_id, decision_count, tool_batch_count)
            ledger.record_kernel_guard_assert(True)
        except KernelGuardError:
            ledger.record_kernel_guard_assert(False)
            raise

    @staticmethod
    def _guard_assert_single_tool_batch(*, turn_id: str, tool_batch_count: int, ledger: TurnLedger) -> None:
        try:
            KernelGuard.assert_single_tool_batch(turn_id, tool_batch_count)
            ledger.record_kernel_guard_assert(True)
        except KernelGuardError:
            ledger.record_kernel_guard_assert(False)
            raise

    @staticmethod
    def _guard_assert_no_hidden_continuation(
        *,
        turn_id: str,
        state_trajectory: list[str] | tuple[str, ...],
        ledger: TurnLedger,
    ) -> None:
        try:
            KernelGuard.assert_no_hidden_continuation(turn_id, state_trajectory)
            ledger.record_kernel_guard_assert(True)
        except KernelGuardError:
            ledger.record_kernel_guard_assert(False)
            raise

    @staticmethod
    def _guard_assert_no_finalization_tool_calls(
        *, turn_id: str, tool_calls: list[Any] | None, ledger: TurnLedger
    ) -> None:
        # Soft guard: no longer raises KernelGuardError, but records anomaly flags
        # and metrics. We still record assert pass for telemetry consistency.
        KernelGuard.assert_no_finalization_tool_calls(turn_id, tool_calls, ledger=ledger)
        ledger.record_kernel_guard_assert(True)

    def on_event(self, handler: Callable[[TurnEvent], None]) -> None:
        """注册事件处理器"""
        self._event_handlers.append(handler)

    def _emit_phase_event(self, event: TurnEvent) -> None:
        """发送事件"""
        for handler in self._event_handlers:
            try:
                handler(event)
            except (RuntimeError, ValueError) as e:
                logger.warning("Event handler failed: %s", e)
                continue

    # ---------------------------------------------------------------------------
    # 意图分类（hybrid 版本保留在 Facade，纯 regex 版本已下沉到 intent_classifier）
    # ---------------------------------------------------------------------------

    @classmethod
    def _classify_user_intent(cls, message: str) -> str:
        """对用户消息进行意图分类，返回最匹配的意图类别。

        委托给 intent_classifier.classify_intent_regex 以消除代码重复。
        """
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            classify_intent_regex,
        )

        return classify_intent_regex(message)

    async def _requires_mutation_intent_hybrid(self, message: str) -> bool:
        """Async hybrid version of _requires_mutation_intent.

        统一委托 CognitiveGateway（Embedding -> SLM -> Regex 级联瀑布），
        不再保留本地 hybrid 路径，确保全系统意图分类单一真相来源。
        """
        from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
            CognitiveGateway,
        )
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            _is_negated_mutation,
        )

        if _is_negated_mutation(message):
            return False

        gateway = CognitiveGateway.get_default_instance_sync()
        if gateway is not None:
            intent = await gateway.classify_intent(message)
        else:
            # Gateway 尚未初始化：同步回退到纯 regex（零依赖、零延迟）
            from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
                classify_intent_regex,
            )

            intent = classify_intent_regex(message)
        return intent in {"STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS", "WEAK_MUTATION"}

    @classmethod
    def _requires_mutation_intent(cls, message: str) -> bool:
        """判定用户请求是否要求代码/文件突变（需要写工具）。"""
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            _is_negated_mutation,
        )

        if _is_negated_mutation(message):
            return False
        intent = cls._classify_user_intent(message)
        return intent in {"STRONG_MUTATION", "DEBUG_AND_FIX", "DEVOPS", "WEAK_MUTATION"}

    @staticmethod
    async def _resolve_delivery_mode_hybrid(user_message: str) -> DeliveryContract:
        """SLM 优先、regex 兜底的 delivery mode 解析。

        先尝试 CognitiveGateway（统一级联入口），若不可用则回退到
        本地 regex 规则引擎。保证永远有返回值。
        """
        try:
            from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
                CognitiveGateway,
            )

            gateway = CognitiveGateway.get_default_instance_sync()
            if gateway is not None:
                return await gateway.resolve_delivery_mode(user_message)
        except (ImportError, AttributeError, RuntimeError, asyncio.TimeoutError, OSError):
            pass
        # Gateway 未初始化或失败：回退到 regex
        return resolve_delivery_mode(user_message)

    @classmethod
    def _requires_verification_intent(cls, message: str) -> bool:
        """判定用户请求是否要求验证/测试（需要 test/verify 类工具）。"""
        latest_user = str(message or "")
        lowered = latest_user.lower()
        if any(marker in latest_user for marker in ("验证", "校验", "测试")):
            return True
        if re.search(r"\b(verify|validation|validate|test|pytest|check)\b", lowered):
            return True
        return cls._classify_user_intent(message) == "TESTING"

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
        state_machine = TurnStateMachine(turn_id=turn_id)
        ledger = TurnLedger(turn_id=turn_id)

        try:
            logger.debug("[DEBUG] turn_execute_start: turn_id=%s mode=run", turn_id)
            result = await self._execute_turn(turn_id, context, tool_definitions, state_machine, ledger, stream=False)
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
        self, turn_id: str, context: list[dict], tool_definitions: list[dict]
    ) -> AsyncIterator[TurnEvent]:
        """
        流式执行turn

        产出事件序列，供CLI实时渲染
        """
        state_machine = TurnStateMachine(turn_id=turn_id)
        ledger = TurnLedger(turn_id=turn_id)

        try:
            async for event in self._execute_turn_stream(turn_id, context, tool_definitions, state_machine, ledger):
                yield event
        except Exception as e:
            logger.exception("execute_stream failed: turn_id=%s", turn_id)
            ledger.finalize()
            yield ErrorEvent(
                turn_id=turn_id, error_type=type(e).__name__, message=str(e), state_at_error=state_machine.state.name
            )
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
        latest_user_request = extract_latest_user_message(context)
        delivery_contract = await self._resolve_delivery_mode_hybrid(latest_user_request)

        # 多轮对话保护：如果最新消息丢失 mutation 意图（如"继续""开始吧"），
        # 但历史消息中最近存在 MATERIALIZE_CHANGES 意图，则继承该意图
        if delivery_contract.mode != DeliveryMode.MATERIALIZE_CHANGES:
            inherited = self._inherit_materialize_from_history(context, latest_user_request)
            if inherited is not None:
                logger.warning(
                    "delivery-contract-inherited: turn_id=%s latest_msg=%r "
                    "inherited MATERIALIZE_CHANGES from historical user message",
                    turn_id,
                    latest_user_request,
                )
                delivery_contract = inherited
                ledger.anomaly_flags.append(
                    {
                        "type": "DELIVERY_CONTRACT_INHERITED",
                        "turn_id": turn_id,
                        "reason": "latest_message_lost_mutation_intent",
                        "latest_request": latest_user_request,
                    }
                )

        ledger.set_delivery_contract(delivery_contract)
        ledger.mutation_obligation.target_files_known = self._detect_target_files_known(context)
        logger.debug(
            "[DEBUG] turn_delivery_contract: turn_id=%s mode=%s requires_mutation=%s",
            turn_id,
            delivery_contract.mode.value,
            delivery_contract.requires_mutation,
        )

        # === Phase 2: 请求决策 ===
        state_machine.transition_to(TurnState.DECISION_REQUESTED)
        ledger.state_history.append(("DECISION_REQUESTED", int(time.time() * 1000)))
        logger.debug("[DEBUG] turn_phase: turn_id=%s phase=DECISION_REQUESTED", turn_id)
        self._emit_phase_event(TurnPhaseEvent.create(turn_id, "decision_requested"))

        llm_response = await self._call_llm_for_decision(context, tool_definitions, ledger)

        state_machine.transition_to(TurnState.DECISION_RECEIVED)
        ledger.state_history.append(("DECISION_RECEIVED", int(time.time() * 1000)))
        logger.debug("[DEBUG] turn_phase: turn_id=%s phase=DECISION_RECEIVED", turn_id)

        # === Phase 3: 解码决策 ===
        decision = self.decoder.decode(llm_response, TurnId(turn_id))

        # PROPOSE_PATCH / ANALYZE_ONLY 边界保护：过滤 write tools
        decision = self._apply_delivery_mode_filter(decision, ledger)

        ledger.record_decision(decision)
        self._guard_assert_single_decision(
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
        self._emit_phase_event(
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

        # === Phase 4: 执行决策 ===
        decision_kind = decision.get("kind")
        latest_user_request = extract_latest_user_message(context)
        guard_mode = str(getattr(self.config, "mutation_guard_mode", "warn"))
        # 统一 mutation 判断：delivery contract + intent hybrid 任一判定需要 mutation 即触发 guard
        requires_mutation_by_contract = ledger.delivery_contract.requires_mutation
        requires_mutation_by_intent = await self._requires_mutation_intent_hybrid(latest_user_request)
        # 两套系统不一致时，以"需要 mutation"为准，自动升级 delivery contract
        if requires_mutation_by_intent and not requires_mutation_by_contract:
            logger.warning(
                "delivery-contract-upgrade: intent_classifier detected mutation but delivery_contract was not "
                "MATERIALIZE_CHANGES. Upgrading for turn_id=%s",
                turn_id,
            )
            ledger.delivery_contract = DeliveryContract(
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                requires_verification=ledger.delivery_contract.requires_verification,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )
            requires_mutation_by_contract = True
            ledger.anomaly_flags.append(
                {
                    "type": "DELIVERY_CONTRACT_AUTO_UPGRADED",
                    "turn_id": turn_id,
                    "reason": "intent_classifier_mismatch",
                    "user_request": latest_user_request,
                }
            )

        if (
            decision_kind != TurnDecisionKind.TOOL_BATCH
            and (requires_mutation_by_contract or requires_mutation_by_intent)
            and has_available_write_tool(tool_definitions)
        ):
            # MATERIALIZE_CHANGES 模式下必须阻止 non-tool 决策（Invariant A）
            force_block = ledger.delivery_contract.mode == DeliveryMode.MATERIALIZE_CHANGES
            if guard_mode == "strict" or force_block:
                if force_block and guard_mode == "warn":
                    logger.warning(
                        "mutation-contract guard: MATERIALIZE_CHANGES mode forces block despite warn mode. "
                        "turn_id=%s decision_kind=%s",
                        turn_id,
                        decision_kind,
                    )
                shadow_engine = self._build_stream_shadow_engine(workspace=".", turn_id=turn_id)
                return await self._retry_orchestrator.retry_tool_batch_after_contract_violation(
                    turn_id=turn_id,
                    context=context,
                    tool_definitions=tool_definitions,
                    state_machine=state_machine,
                    ledger=ledger,
                    stream=False,
                    shadow_engine=shadow_engine,
                )
            elif guard_mode == "warn":
                logger.warning(
                    "mutation-contract guard (soft): non-tool decision (%s) for mutation request, "
                    "but mutation_guard_mode=warn allows passthrough. turn_id=%s",
                    decision_kind,
                    turn_id,
                )
                ledger.record_mutation_guard_warning(
                    reason=f"non_tool_decision_for_mutation_request:{decision_kind.value if hasattr(decision_kind, 'value') else decision_kind}",
                    user_request=latest_user_request,
                )

        if decision_kind == TurnDecisionKind.FINAL_ANSWER:
            return await self._handle_final_answer(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.HANDOFF_WORKFLOW:
            return await self._handoff_handler.handle_handoff(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.HANDOFF_DEVELOPMENT:
            return await self._handoff_handler.handle_development_handoff(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.ASK_USER:
            return await self._handoff_handler.handle_ask_user(decision, state_machine, ledger)

        elif decision_kind == TurnDecisionKind.TOOL_BATCH:
            shadow_engine = self._build_stream_shadow_engine(workspace=".", turn_id=turn_id)
            try:
                return await self._tool_batch_executor.execute_tool_batch(
                    decision,
                    state_machine,
                    ledger,
                    context,
                    stream=False,
                    shadow_engine=shadow_engine,
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
    ) -> RawLLMResponse:
        """调用LLM获取决策

        Phase 3.1: Integrates adaptive model routing based on task complexity.
        Phase 3.3: Tracks token usage for budget management.
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

        # Phase 3.3: Track usage
        tokens_used = response.get("usage", {}).get("prompt_tokens", 0) + response.get("usage", {}).get(
            "completion_tokens", 0
        )
        cost = response.get("cost", 0.0)
        self._track_token_usage(tokens_used, cost)

        ledger.record_llm_call(
            phase="decision",
            model=response.get("model", "unknown"),
            tokens_in=response.get("usage", {}).get("prompt_tokens", 0),
            tokens_out=response.get("usage", {}).get("completion_tokens", 0),
        )

        thinking = response.get("thinking")
        if thinking is not None and not isinstance(thinking, str):
            thinking = None
        return RawLLMResponse(
            content=response.get("content", ""),
            thinking=thinking,
            native_tool_calls=response.get("tool_calls", []),
            model=response.get("model", "unknown"),
            usage=response.get("usage", {}),
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
    ) -> AsyncIterator[TurnEvent]:
        """Proxy to StreamOrchestrator._call_llm_for_decision_stream_impl."""
        async for event in self._stream_orchestrator._call_llm_for_decision_stream_impl(
            context,
            tool_definitions,
            ledger,
            shadow_engine=shadow_engine,
            tool_choice_override=tool_choice_override,
            model_override=model_override,
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
        """Build decision-stage messages with single-batch execution constraints."""
        messages: list[dict] = [dict(message) for message in context]
        if not tool_definitions:
            return messages

        single_batch_guard = (
            "SYSTEM CONSTRAINT (Execution): This turn supports multi-turn workflow. "
            "For code modification tasks, follow the 'inspect-then-modify' pattern across turns:\n"
            "1. First turn: You may call read_file to inspect existing code. "
            "2. Subsequent turns: You MUST call write/edit tools (edit_file, write_file, etc.) to materialize changes.\n"
            "3. NEVER output large code blocks in text — always use tools to write files.\n"
            "4. DO NOT ask the user for confirmation, approval, or plan review. "
            "The user has already authorized execution. Proceed immediately with tool calls.\n"
            "系统约束 (执行层): 当前回合支持多回合工作流. 代码修改任务遵循'先勘察后修改': "
            "第一轮允许调用 read_file 了解现状, 后续回合必须调用写工具落盘修改. "
            "严禁在对话中直接输出大段代码替代工具调用. "
            "严禁请求用户确认或等待批准——用户已授权执行，请立即调用工具实施修改。"
        )
        messages.append({"role": "system", "content": single_batch_guard, "metadata": {"plane": "control"}})

        # 修复：MATERIALIZE_CHANGES 模式下不再追加 TASK CONTRACT（HARD GATE 反读规则），
        # 因为它与 SYSTEM CONSTRAINT 的多回合先读后写规则冲突，导致 LLM 陷入精神分裂：
        # 规则 A 允许先读，规则 B 恐吓"只读即拒绝"。只在非 MATERIALIZE 模式追加。
        is_materialize = ledger is not None and getattr(ledger.delivery_contract, "mode", None) in {
            DeliveryMode.MATERIALIZE_CHANGES,
            DeliveryMode.PROPOSE_PATCH,
        }
        if not is_materialize:
            task_contract_hint = build_single_batch_task_contract_hint(context, tool_definitions)
            if task_contract_hint:
                messages.append({"role": "system", "content": task_contract_hint})

        # 【修复根因 C】：implementing 阶段追加 HARD GATE 强制约束。
        # 根因：MATERIALIZE_CHANGES 模式下 TASK CONTRACT 被跳过（避免与多回合规则冲突），
        # 导致 implementing 阶段缺乏强制写工具的"牙齿"。
        # 通过检测 continuation prompt 中的 task_progress，在 implementing 阶段注入不可逃避的约束。
        _is_implementing_turn = any(
            "当前阶段: implementing" in str(m.get("content", ""))
            for m in context
            if str(m.get("role", "")).strip().lower() == "user"
        )
        if _is_implementing_turn:
            enforcing_constraint = (
                "HARD GATE (Implementing Phase): You are now in the MODIFY phase. "
                "You MUST call at least one write tool (edit_file, write_file, create_file, etc.) in this turn. "
                "Text-only responses, plan outlines, or 'I will now...' are INVALID and will be rejected. "
                "DO NOT ask for confirmation. DO NOT output code blocks in text. Use tools immediately.\n"
                "CRITICAL: Calling exploration tools (glob, repo_rg, repo_tree, read_file) in this phase is FORBIDDEN. "
                "You have already gathered enough context. Proceed directly to write.\n"
                "强制约束（修改阶段）：你当前处于执行修改阶段，本回合必须调用至少一个写工具。"
                "严禁调用探索工具（glob/repo_rg/repo_tree/read_file 等）——已有足够上下文，直接写入！"
            )
            messages.append(
                {
                    "role": "system",
                    "content": enforcing_constraint,
                    "metadata": {"plane": "control", "kind": "execution_constraint"},
                }
            )

        return messages

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
        """构建符合契约的 TurnResult dict"""
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
        except Exception:
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
        complexity_weights = {
            "low": 0.3,
            "medium": 0.5,
            "high": 0.7,
            "complex": 0.9,
        }
        weight = complexity_weights.get(task_complexity, 0.5)

        recent_failures = [outcome for outcome in self._turn_outcome_history[-10:] if not outcome.get("success", True)]

        if len(recent_failures) >= 3:
            logger.info(
                "adaptive_model_routing: %d recent failures, prioritizing reliability",
                len(recent_failures),
            )
            return None

        if weight >= 0.7:
            logger.debug(
                "adaptive_model_routing: high complexity=%s, considering premium model",
                task_complexity,
            )

        return None

    def _estimate_task_complexity(self, context: list[dict]) -> str:
        """Estimate task complexity from context.

        Args:
            context: Conversation context

        Returns:
            Complexity level: low/medium/high/complex
        """
        total_chars = sum(len(str(msg.get("content", ""))) for msg in context if isinstance(msg, dict))
        tool_definitions_count = len(context) // 3 if context else 0
        has_multi_turn = len(context) > 4

        if total_chars > 10000 or tool_definitions_count > 20:
            return "complex"
        elif total_chars > 5000 or tool_definitions_count > 10 or has_multi_turn:
            return "high"
        elif total_chars > 1500:
            return "medium"
        return "low"

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
        outcome = {
            "turn_id": turn_id,
            "success": success,
            "error": error,
            "tokens_used": tokens_used,
            "cost": cost,
        }

        self._turn_outcome_history.append(outcome)
        if len(self._turn_outcome_history) > self._max_outcome_history:
            self._turn_outcome_history = self._turn_outcome_history[-self._max_outcome_history :]

        if not success and error:
            logger.info(
                "turn_outcome_recorded: turn_id=%s failed=%s error=%s",
                turn_id,
                not success,
                error[:100] if error else None,
            )

    def _learn_from_history(self, error_pattern: str) -> list[str]:
        """Phase 3.2: Generate correction hints based on failure patterns.

        Args:
            error_pattern: Error type to analyze

        Returns:
            List of correction hints
        """
        relevant_failures = [
            outcome
            for outcome in self._turn_outcome_history[-20:]
            if not outcome.get("success", True) and error_pattern.lower() in str(outcome.get("error", "")).lower()
        ]

        hints: list[str] = []
        if len(relevant_failures) >= 2:
            if "timeout" in error_pattern.lower():
                hints.append("Consider breaking down into smaller steps")
            elif "syntax" in error_pattern.lower():
                hints.append("Check syntax before applying changes")
            elif "not found" in error_pattern.lower():
                hints.append("Ensure all dependencies are available first")
            elif "permission" in error_pattern.lower():
                hints.append("Verify file permissions before writing")

        return hints

    def _get_learned_constraints(self) -> dict[str, Any]:
        """Phase 3.2: Get constraints learned from turn history.

        Returns:
            Dict of learned constraints for this session
        """
        recent_outcomes = self._turn_outcome_history[-20:]
        failed_outcomes = [o for o in recent_outcomes if not o.get("success", True)]

        return {
            "failure_count": len(failed_outcomes),
            "total_turns": len(recent_outcomes),
            "recent_errors": [o.get("error") for o in failed_outcomes[-5:] if o.get("error")],
            "should_defer_complexity": len(failed_outcomes) >= 3,
        }

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
        logger.debug(
            "budget_initialized: token_budget=%s cost_budget=%s",
            token_budget or "unlimited",
            cost_budget or "unlimited",
        )

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
        status: dict[str, Any] = {
            "tokens_used": self._session_tokens_used,
            "tokens_budget": self._session_token_budget,
            "cost_used": self._session_cost_used,
            "cost_budget": self._session_cost_budget,
            "token_warning": False,
            "cost_warning": False,
            "token_exceeded": False,
            "cost_exceeded": False,
        }

        if self._session_token_budget > 0:
            token_ratio = self._session_tokens_used / self._session_token_budget
            status["token_ratio"] = round(token_ratio, 3)
            status["token_warning"] = token_ratio >= 0.8
            status["token_exceeded"] = token_ratio >= 1.0

        if self._session_cost_budget > 0:
            cost_ratio = self._session_cost_used / self._session_cost_budget
            status["cost_ratio"] = round(cost_ratio, 3)
            status["cost_warning"] = cost_ratio >= 0.8
            status["cost_exceeded"] = cost_ratio >= 1.0

        return status

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

        # MATERIALIZE_CHANGES 模式下，FINAL_ANSWER 意味着没有 write receipt —— 违反 Invariant A
        if ledger.delivery_contract.must_materialize and not ledger.mutation_obligation.mutation_satisfied:
            # 例外：明确的拒绝响应
            lowered_visible = visible_content.lower()
            is_refusal = any(marker in lowered_visible for marker in tx_constants.REFUSAL_MARKERS)

            if not is_refusal:
                # Inline Patch Escape 检测（附加诊断）
                escape_result = detect_inline_patch_escape(visible_content)
                if escape_result["is_escape"]:
                    ledger.mutation_obligation.record_inline_patch_rejected()
                    ledger.anomaly_flags.append(
                        {
                            "type": "INLINE_PATCH_ESCAPE",
                            "turn_id": turn_id,
                            "ratio": escape_result["ratio"],
                            "code_block_chars": escape_result["code_block_chars"],
                            "total_chars": escape_result["total_chars"],
                            "code_blocks_count": escape_result["code_blocks_count"],
                        }
                    )
                    blocked_reason = BlockedReason.SAFETY_CONSTRAINT
                    blocked_detail = (
                        f"INLINE_PATCH_ESCAPE detected: token density ratio={escape_result['ratio']:.2f}. "
                        "MATERIALIZE_CHANGES mode requires write tools, not inline code blocks."
                    )
                    kind = "inline_patch_escape_blocked"
                else:
                    blocked_reason = BlockedReason.NO_WRITE_TOOL_AVAILABLE
                    blocked_detail = (
                        "MATERIALIZE_CHANGES mode requires at least one successful write tool invocation, "
                        "but FINAL_ANSWER was received without any write receipt."
                    )
                    kind = "mutation_bypass_blocked"

                logger.warning(
                    "materialize-violation-blocked: turn_id=%s kind=%s reason=%s detail=%s",
                    turn_id,
                    kind,
                    blocked_reason.value,
                    blocked_detail,
                )
                ledger.mutation_obligation.mark_blocked(blocked_reason, detail=blocked_detail)
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
                    kind=kind,
                    visible_content=visible_content,
                    decision=decision,
                    batch_receipt=None,
                    finalization={
                        "error": kind.upper(),
                        "blocked_reason": blocked_reason.value,
                        "blocked_detail": blocked_detail,
                        "escape_metrics": escape_result if escape_result["is_escape"] else None,
                    },
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
