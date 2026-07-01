"""Role Execution Kernel Core - 角色执行内核核心

重构为 Facade 模式的 RoleExecutionKernel。

架构:
    - RoleExecutionKernel: Facade，协调各服务
    - LLMInvoker: LLM调用服务 (ILLMInvoker)
    - ToolExecutor: 工具执行服务 (IToolExecutor)
    - PromptBuilder: 提示词构建服务
    - OutputParser: 输出解析服务
    - QualityChecker: 质量检查服务
    - EventEmitter: 事件发射服务

依赖注入:
    所有服务可通过 __init__ 注入，便于测试和定制。
    使用 create_default() 工厂方法创建生产环境实例。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.kernel.internal.context_gateway import ContextGatewayConfig, ContextRequest
from polaris.cells.roles.kernel.internal.kernel.error_handler import (
    KernelEventEmitter,
    LLMEventType,
)
from polaris.cells.roles.kernel.internal.kernel.helpers import (
    quality_result_to_dict,
)
from polaris.cells.roles.kernel.internal.kernel.request_tool_gating import tool_contract_requires_single_batch
from polaris.cells.roles.kernel.internal.kernel.suggestions import get_suggestions_for_error
from polaris.cells.roles.kernel.internal.kernel.tool_policy import (
    _cognitive_runtime_blocked_tools,
    _normalize_tool_policy_name,
)
from polaris.cells.roles.kernel.internal.kernel.turn_execution import (
    execute_transaction_kernel_stream,
    execute_transaction_kernel_turn,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.output_parser import OutputParser, ToolCallResult
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder
from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker, QualityResult
from polaris.cells.roles.kernel.public.config import KernelConfig, get_default_config
from polaris.cells.roles.profile.public.service import (
    RoleProfile,
    RoleProfileRegistry,
    RoleTurnRequest,
    RoleTurnResult,
)
from polaris.infrastructure.log_pipeline.writer import LogEventWriter, get_writer
from polaris.kernelone.events.uep_publisher import UEPEventPublisher
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
    from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
    from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort
    from polaris.cells.roles.kernel.services.contracts import (
        CellToolExecutorPort,
        IEventEmitter,
        ILLMInvoker,
        IOutputParser,
        IPromptBuilder,
        IQualityChecker,
    )
    from polaris.cells.roles.session.public.service import RoleDataStore
    from polaris.kernelone.context.compaction import RoleContextCompressor

logger = logging.getLogger(__name__)

ContextGatewayConfigFactory = Callable[[str, RoleProfile, RoleTurnRequest], ContextGatewayConfig | None]


class RoleExecutionKernel:
    """角色执行内核 - Facade 模式实现

    统一执行角色对话的两种模式：
    - CHAT: 聊天模式（用户交互）
    - WORKFLOW: 工作流模式（自动化执行）

    重构后架构（Facade 模式）:
    - RoleExecutionKernel: Facade，提供统一接口，委托给服务层
    - LLMInvoker (ILLMInvoker): LLM调用服务
    - ToolExecutor (IToolExecutor): 工具执行服务
    - PromptBuilder: 提示词构建服务
    - OutputParser: 输出解析服务
    - QualityChecker: 质量检查服务
    - EventEmitter: 事件发射服务

    依赖注入:
        >>> # 生产环境（使用默认服务）
        >>> kernel = RoleExecutionKernel.create_default(workspace=".")
        >>>
        >>> # 自定义服务注入
        >>> kernel = RoleExecutionKernel(
        ...     workspace=".",
        ...     llm_invoker=custom_llm_invoker,
        ...     tool_executor=custom_tool_executor,
        ... )
        >>>
        >>> # 测试环境（使用 Mock）
        >>> kernel = RoleExecutionKernel(
        ...     workspace=".",
        ...     llm_invoker=MockLLMInvoker(),
        ...     tool_executor=MockToolExecutor(),
        ... )
    """

    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        use_structured_output: bool | None = None,
        config: KernelConfig | None = None,
        tool_gateway: ToolGatewayPort | None = None,
        # 新增：服务层依赖注入
        llm_invoker: ILLMInvoker | None = None,
        tool_executor: CellToolExecutorPort | None = None,
        prompt_builder: IPromptBuilder | None = None,
        output_parser: IOutputParser | None = None,
        quality_checker: IQualityChecker | None = None,
        event_emitter: IEventEmitter | None = None,
        context_gateway_config_factory: ContextGatewayConfigFactory | None = None,
    ) -> None:
        """初始化执行内核

        Args:
            workspace: 工作区路径
            registry: 角色注册表（默认使用全局实例）
            use_structured_output: 是否启用结构化输出（默认从环境变量读取）
            config: Kernel 执行配置（默认使用全局默认配置）
            tool_gateway: 工具网关实现（支持 ToolGatewayPort Protocol）
            llm_invoker: LLM调用服务（可选，用于依赖注入）
            tool_executor: 工具执行服务（可选，用于依赖注入）
            prompt_builder: 提示词构建服务（可选，用于依赖注入）
            output_parser: 输出解析服务（可选，用于依赖注入）
            quality_checker: 质量检查服务（可选，用于依赖注入）
            event_emitter: 事件发射服务（可选，用于依赖注入）
            context_gateway_config_factory: 上下文网关配置工厂（可选，由 runtime/adapters 注入）
        """
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()  # type: ignore[no-untyped-call]

        # 保存注入的服务（可能为 None，由 _get_* 方法处理）
        self._injected_llm_invoker = llm_invoker
        self._injected_tool_executor = tool_executor
        self._injected_prompt_builder = prompt_builder
        self._injected_output_parser = output_parser
        self._injected_quality_checker = quality_checker
        self._injected_event_emitter = event_emitter
        self._context_gateway_config_factory = context_gateway_config_factory

        # M1: 工具网关 DI 支持
        self._tool_gateway = tool_gateway

        # Cache RoleToolGateway per-turn for FailureBudget persistence (HALLUCINATION_LOOP detection)
        self._cached_tool_gateway: Any | None = None
        self._cached_gateway_profile: Any | None = None
        self._cached_gateway_turn_id: str | None = None  # Track turn boundary for counter reset

        # Kernel 配置
        self._config = config if config is not None else get_default_config()

        # 结构化输出配置
        if use_structured_output is None:
            use_structured_output = os.environ.get("KERNELONE_USE_STRUCTURED_OUTPUT", "false").lower() in (
                "true",
                "1",
                "yes",
            )
        self._use_structured_output = bool(use_structured_output)

        # 初始化各组件（懒加载，仅在需要时创建）
        self._prompt_builder: PromptBuilder | None = None
        self._output_parser: OutputParser | None = None
        self._quality_checker: QualityChecker | None = None
        self._llm_invoker: Any | None = None
        self._event_emitter: KernelEventEmitter | None = None

        # 状态管理
        self._data_stores: dict[str, RoleDataStore] = {}
        self._state_lock = asyncio.Lock()

        # H1: 上下文压缩配置
        self._context_compaction_enabled = os.environ.get("KERNELONE_CONTEXT_COMPACTION", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self._context_compaction_threshold = int(os.environ.get("KERNELONE_CONTEXT_COMPACTION_THRESHOLD", "50000"))
        self._context_compressor: RoleContextCompressor | None = None

    # ═══════════════════════════════════════════════════════════════════════════
    # 工厂方法
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def create_default(
        cls,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        config: KernelConfig | None = None,
        **kwargs: Any,
    ) -> RoleExecutionKernel:
        """创建默认配置的内核实例（生产环境使用）

        Args:
            workspace: 工作区路径
            registry: 角色注册表
            config: Kernel 配置
            **kwargs: 额外的配置参数

        Returns:
            配置好的 RoleExecutionKernel 实例
        """
        return cls(
            workspace=workspace,
            registry=registry,
            config=config,
            # 使用默认服务（None 表示使用内部默认实现）
            llm_invoker=None,
            tool_executor=None,
            prompt_builder=None,
            output_parser=None,
            quality_checker=None,
            event_emitter=None,
            **kwargs,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 属性访问器（向后兼容）
    # ═══════════════════════════════════════════════════════════════════════════

    @property
    def config(self) -> KernelConfig:
        """获取当前 Kernel 配置"""
        return self._config

    def _build_context_gateway_config(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> ContextGatewayConfig | None:
        """Build ContextGatewayConfig through the injected owner-agnostic runtime factory."""
        if self._context_gateway_config_factory is None:
            return None
        try:
            return self._context_gateway_config_factory(role, profile, request)
        except Exception:  # noqa: BLE001 - context asset providers must degrade to baseline context
            logger.debug("ContextGatewayConfig factory failed", exc_info=True)
            return None

    @staticmethod
    def _use_transaction_kernel() -> bool:
        """Return the canonical execution engine selection.

        TransactionKernel is now the only production role-turn execution path.
        """
        return True

    def _get_response_schema(self, role: str) -> type | None:
        """Resolve explicit structured output schema for this turn.

        roles.kernel must not import role-specific schema bridges from
        roles.adapters. Future structured-output contracts must be supplied
        through roles.profile or roles.runtime public contracts.
        """

        if not self._use_structured_output:
            return None
        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 服务层访问器（懒加载 + 依赖注入支持）
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_prompt_builder(self) -> PromptBuilder:
        """获取提示词构建器（支持依赖注入）"""
        if self._injected_prompt_builder is not None:
            # 类型检查：确保注入的服务实现了必要的方法
            return self._injected_prompt_builder  # type: ignore[return-value]
        if self._prompt_builder is None:
            self._prompt_builder = PromptBuilder(self.workspace)
        return self._prompt_builder

    def _get_output_parser(self) -> OutputParser:
        """获取输出解析器（支持依赖注入）"""
        if self._injected_output_parser is not None:
            return self._injected_output_parser  # type: ignore[return-value]
        if self._output_parser is None:
            self._output_parser = OutputParser()
        return self._output_parser

    def _get_quality_checker(self) -> QualityChecker:
        """获取质量检查器（支持依赖注入）"""
        if self._injected_quality_checker is not None:
            return self._injected_quality_checker  # type: ignore[return-value]
        if self._quality_checker is None:
            self._quality_checker = QualityChecker(self.workspace)
        return self._quality_checker

    def _get_event_emitter(self) -> KernelEventEmitter:
        """获取事件发射器（支持依赖注入）"""
        if self._injected_event_emitter is not None:
            return self._injected_event_emitter  # type: ignore[return-value]
        if self._event_emitter is None:
            self._event_emitter = KernelEventEmitter()
        return self._event_emitter

    # ─────────────────────────────────────────────────────────────────────────────
    # 公共 DI 注入方法（用于测试和扩展）
    # ─────────────────────────────────────────────────────────────────────────────

    def inject_llm_invoker(self, invoker: ILLMInvoker | Any | None) -> None:
        """注入 LLMInvoker（支持测试和扩展）

        Args:
            invoker: LLM 调用服务实例，传入 None 可清除注入
        """
        self._injected_llm_invoker = invoker

    def inject_tool_executor(self, executor: CellToolExecutorPort | None) -> None:
        """注入工具执行器（支持测试和扩展）

        Args:
            executor: 工具执行器实例，传入 None 可清除注入
        """
        self._injected_tool_executor = executor

    def inject_prompt_builder(self, builder: IPromptBuilder | None) -> None:
        """注入提示词构建器（支持测试和扩展）

        Args:
            builder: 提示词构建器实例，传入 None 可清除注入
        """
        self._injected_prompt_builder = builder

    def inject_output_parser(self, parser: IOutputParser | None) -> None:
        """注入输出解析器（支持测试和扩展）

        Args:
            parser: 输出解析器实例，传入 None 可清除注入
        """
        self._injected_output_parser = parser

    def inject_event_emitter(self, emitter: IEventEmitter | None) -> None:
        """注入事件发射器（支持测试和扩展）

        Args:
            emitter: 事件发射器实例，传入 None 可清除注入
        """
        self._injected_event_emitter = emitter

    def _get_llm_invoker(self) -> Any:
        """获取 canonical LLMInvoker。"""
        # 1. 优先使用注入的调用服务
        if self._injected_llm_invoker is not None:
            return self._injected_llm_invoker
        # 2. 默认懒加载创建 canonical LLMInvoker
        if self._llm_invoker is None:
            self._llm_invoker = LLMInvoker(self.workspace)
        return self._llm_invoker

    # ═══════════════════════════════════════════════════════════════════════════
    # 主要公开 API
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(
        self,
        role: str,
        request: RoleTurnRequest,
    ) -> RoleTurnResult:
        """执行角色回合（带重试机制）

        Args:
            role: 角色标识
            request: 回合请求

        Returns:
            回合结果
        """
        # 1. 加载角色Profile
        try:
            profile = self.registry.get_profile_or_raise(role)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"角色加载失败: {e}", is_complete=True)

        # 2. 处理废弃参数
        try:
            prompt_appendix = self._process_deprecated_params(request)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"参数处理失败: {e}", is_complete=True)

        prompt_appendix = self._append_prompt_profiles_for_request(
            profile=profile,
            request=request,
            prompt_appendix=prompt_appendix,
            context_override=getattr(request, "context_override", None),
            message=str(getattr(request, "message", "") or ""),
        )

        # 3. 构建提示词指纹
        try:
            fingerprint = self._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"提示词构建失败: {e}", is_complete=True)

        # 4. 构建基础系统提示词
        try:
            base_system_prompt = self._build_system_prompt_for_request(profile, request, prompt_appendix)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"系统提示词构建失败: {e}", is_complete=True)

        # 5. 构建上下文（验证可用性，结果由 TransactionKernel 使用）
        try:
            _ = self._build_context(profile, request)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"上下文构建失败: {e}", is_complete=True)

        # Reset cached gateway for new turn (FailureBudget should not persist across turns)
        self._cached_tool_gateway = None
        self._cached_gateway_profile = None

        # 6. 重试循环配置
        max_retries = request.max_retries if request.max_retries > 0 else self._config.max_retries
        validate_output = request.validate_output
        last_validation: QualityResult | None = None
        last_error: str | None = None

        # 结构化输出相关
        pre_validated_data: dict[str, Any] | None = None
        instructor_validated = False

        # 重试统计
        total_platform_retry_count = 0
        kernel_repair_retry_count = 0
        kernel_repair_reasons: list[str] = []

        # 获取 run_id
        task_id = str(getattr(request, "task_id", None) or "").strip()
        observer_run_id = self._get_event_emitter().resolve_observer_run_id(role, getattr(request, "run_id", None))
        # 将 resolved run_id 写回 request，确保下游（TransactionKernel/RoleToolGateway）能获取到
        if request.run_id is None:
            request.run_id = observer_run_id

        for attempt in range(max_retries + 1):
            # 构建当前尝试的系统提示词
            system_prompt = self._get_prompt_builder().build_retry_prompt(
                base_system_prompt, quality_result_to_dict(last_validation), attempt
            )

            response_schema = self._get_response_schema(role)

            # Get tracer for OpenTelemetry integration
            tracer = get_tracer()

            # Track LLM latency
            with tracer.span(
                "role.kernel.llm_call",
                tags={"role": role, "attempt": attempt, "model": profile.model},
            ) as span:
                llm_start_time = time.monotonic()
                te_result = await execute_transaction_kernel_turn(
                    self,
                    role=role,
                    profile=profile,
                    request=request,
                    system_prompt=system_prompt,
                    fingerprint=fingerprint,
                    observer_run_id=observer_run_id,
                    response_schema=response_schema,
                )
                llm_latency = time.monotonic() - llm_start_time

                # Record LLM latency to metrics
                try:
                    metrics = get_metrics_collector()
                    metrics.record_llm_latency(llm_latency)
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record LLM latency metric")

                span.set_tag("llm_latency_seconds", llm_latency)
                span.set_tag("has_content", bool(te_result.content))
                span.set_tag("has_tool_calls", bool(te_result.tool_calls))

            # TransactionKernel 返回错误，不重试
            if te_result.error:
                return RoleTurnResult(
                    content=te_result.content or "",
                    thinking=te_result.thinking,
                    tool_calls=te_result.tool_calls or [],
                    tool_results=te_result.tool_results or [],
                    batch_receipt=dict(te_result.batch_receipt) if isinstance(te_result.batch_receipt, dict) else None,
                    profile_version=profile.version,
                    prompt_fingerprint=fingerprint,
                    tool_policy_id=profile.tool_policy.policy_id,
                    quality_score=last_validation.quality_score if last_validation else 0.0,
                    quality_suggestions=last_validation.suggestions if last_validation else [],
                    error=te_result.error,
                    is_complete=False,
                    tool_execution_error=getattr(te_result, "tool_execution_error", None),
                    should_retry=getattr(te_result, "should_retry", False),
                    execution_stats={
                        "platform_retry_count": total_platform_retry_count,
                        "kernel_repair_retry_count": kernel_repair_retry_count,
                        "kernel_repair_reasons": kernel_repair_reasons,
                        "kernel_repair_exhausted": True,
                        **te_result.execution_stats,
                    },
                    turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                    turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
                    metadata=dict(getattr(te_result, "metadata", {}) or {}),
                )

            # Quality validation
            effective_content = te_result.content or ""
            last_validation = None
            final_structured_output: dict[str, Any] | None = None
            if validate_output:
                tool_only_turn = not str(effective_content or "").strip() and bool(
                    te_result.tool_calls or te_result.tool_results
                )
                if tool_only_turn:
                    quality_result = QualityResult(
                        success=True,
                        errors=[],
                        suggestions=[],
                        data={"tool_only_turn": True},
                        quality_score=100.0,
                        quality_passed=True,
                    )
                else:
                    pre_validated_data = None
                    instructor_validated = False
                    if response_schema is not None:
                        try:
                            candidate = self._get_output_parser().extract_json(effective_content)
                            if candidate is None:
                                raise ValueError("No JSON found in content")
                            validated = response_schema(**candidate)
                            pre_validated_data = validated.model_dump()
                            instructor_validated = True
                        except (RuntimeError, ValueError):
                            pre_validated_data = None
                            instructor_validated = False
                    try:
                        quality_result = self._get_quality_checker().validate_output(
                            effective_content,
                            profile,
                            pre_validated_data=pre_validated_data,
                            instructor_validated=instructor_validated,
                        )
                    except (RuntimeError, ValueError) as e:
                        logger.warning("质量检查失败 (attempt=%d): %s", attempt, e)
                        last_error = f"质量检查失败: {e}"
                        quality_result = QualityResult(
                            success=False,
                            errors=[f"质量检查失败: {e}"],
                            suggestions=["请确保输出内容完整准确"] if attempt < max_retries else [],
                            data={"quality_check_error": True},
                            quality_score=0.0,
                            quality_passed=False,
                        )

                last_validation = quality_result
                if isinstance(quality_result.data, dict):
                    final_structured_output = dict(quality_result.data)

                # Record quality score
                try:
                    metrics = get_metrics_collector()
                    metrics.record_quality_score(quality_result.quality_score)
                except (RuntimeError, ValueError):
                    logger.warning("Failed to record quality score metric")

                if not quality_result.success:
                    self._emit_event(
                        event_type=LLMEventType.VALIDATION_FAIL,
                        role=role,
                        run_id=observer_run_id,
                        task_id=task_id,
                        attempt=attempt,
                        errors=quality_result.errors,
                        quality_score=quality_result.quality_score,
                        model=profile.model,
                        publish_realtime=False,
                    )
                    kernel_repair_retry_count += 1
                    kernel_repair_reasons.append(
                        f"attempt_{attempt}: "
                        f"{quality_result.errors[-1] if quality_result.errors else 'validation_failed'}"
                    )

                    # Record retry
                    try:
                        metrics = get_metrics_collector()
                        metrics.record_retry(role, "validation_failed")
                    except (RuntimeError, ValueError):
                        logger.warning("Failed to record retry metric")

                    if attempt < max_retries:
                        self._emit_event(
                            event_type=LLMEventType.CALL_RETRY,
                            role=role,
                            run_id=observer_run_id,
                            task_id=task_id,
                            attempt=attempt,
                            error_category="validation_failed",
                            model=profile.model,
                            publish_realtime=False,
                        )
                        continue

                    error_msg = f"验证失败，已重试{max_retries}次"
                    if last_validation and last_validation.errors:
                        error_msg += f": {last_validation.errors[-1]}"
                    elif last_error:
                        error_msg += f": {last_error}"

                    # Record failed execution
                    try:
                        metrics = get_metrics_collector()
                        metrics.record_execution(role, "validation_failed")
                    except (RuntimeError, ValueError):
                        logger.warning("Failed to record execution metric")

                    return RoleTurnResult(
                        content=effective_content,
                        thinking=te_result.thinking,
                        tool_calls=te_result.tool_calls or [],
                        tool_results=te_result.tool_results or [],
                        batch_receipt=dict(te_result.batch_receipt)
                        if isinstance(te_result.batch_receipt, dict)
                        else None,
                        profile_version=profile.version,
                        prompt_fingerprint=fingerprint,
                        tool_policy_id=profile.tool_policy.policy_id,
                        quality_score=last_validation.quality_score if last_validation else 0.0,
                        quality_suggestions=last_validation.suggestions if last_validation else [],
                        error=error_msg,
                        is_complete=True,
                        execution_stats={
                            "platform_retry_count": total_platform_retry_count,
                            "kernel_repair_retry_count": kernel_repair_retry_count,
                            "kernel_repair_reasons": kernel_repair_reasons,
                            "kernel_repair_exhausted": True,
                            **te_result.execution_stats,
                        },
                        turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                        turn_events_metadata=list(te_result.turn_events_metadata)
                        if te_result.turn_events_metadata
                        else [],
                        metadata=dict(getattr(te_result, "metadata", {}) or {}),
                    )

                self._emit_event(
                    event_type=LLMEventType.VALIDATION_PASS,
                    role=role,
                    run_id=observer_run_id,
                    task_id=task_id,
                    attempt=attempt,
                    quality_score=quality_result.quality_score,
                    model=profile.model,
                    publish_realtime=False,
                )

            # 最终结果
            try:
                metrics = get_metrics_collector()
                metrics.record_execution(role, "success")
            except (RuntimeError, ValueError):
                logger.warning("Failed to record execution success metric")

            return RoleTurnResult(
                content=te_result.content or "",
                thinking=te_result.thinking,
                structured_output=final_structured_output,
                tool_calls=te_result.tool_calls or [],
                tool_results=te_result.tool_results or [],
                batch_receipt=dict(te_result.batch_receipt) if isinstance(te_result.batch_receipt, dict) else None,
                profile_version=profile.version,
                prompt_fingerprint=fingerprint,
                tool_policy_id=profile.tool_policy.policy_id,
                quality_score=last_validation.quality_score if last_validation else 0.0,
                quality_suggestions=last_validation.suggestions if last_validation else [],
                error=None,
                is_complete=True,
                tool_execution_error=getattr(te_result, "tool_execution_error", None),
                should_retry=getattr(te_result, "should_retry", False),
                execution_stats={
                    "platform_retry_count": total_platform_retry_count,
                    "kernel_repair_retry_count": kernel_repair_retry_count,
                    "kernel_repair_reasons": kernel_repair_reasons,
                    "kernel_repair_exhausted": False,
                    **te_result.execution_stats,
                },
                turn_history=list(te_result.turn_history) if te_result.turn_history else [],
                turn_events_metadata=list(te_result.turn_events_metadata) if te_result.turn_events_metadata else [],
                metadata=dict(getattr(te_result, "metadata", {}) or {}),
            )

        # unreachable
        raise RuntimeError("Unexpected fallthrough in RoleExecutionKernel.run")

    async def run_stream(
        self,
        role: str,
        request: RoleTurnRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行角色回合

        Args:
            role: 角色标识
            request: 回合请求

        Yields:
            流式事件字典
        """
        stream_run_id = self._resolve_stream_run_id(request.run_id)
        # 将 resolved run_id 写回 request，确保下游（TransactionKernel/RoleToolGateway）能获取到
        # 只有当 request.run_id 为 None 且 stream_run_id 非空时才设置
        original_run_id = request.run_id
        if original_run_id is None and stream_run_id:
            request.run_id = stream_run_id
        logger.warning(
            "[run_stream] run_id resolved: original=%s stream_run_id=%s final=%s role=%s",
            original_run_id,
            stream_run_id,
            request.run_id,
            role,
        )
        inner_error: Exception | None = None
        uep_publisher = UEPEventPublisher()

        try:
            # 1. 加载角色Profile
            profile = self.registry.get_profile_or_raise(role)

            # Reset cached gateway for new turn (FailureBudget should not persist across turns)
            self._cached_tool_gateway = None
            self._cached_gateway_profile = None

            # 2. 处理废弃参数
            prompt_appendix = self._process_deprecated_params(request)
            prompt_appendix = self._append_prompt_profiles_for_request(
                profile=profile,
                request=request,
                prompt_appendix=prompt_appendix,
                context_override=getattr(request, "context_override", None),
                message=str(getattr(request, "message", "") or ""),
            )

            # 3. 构建提示词指纹
            fingerprint = self._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
            await uep_publisher.publish_stream_event(
                workspace=self.workspace or os.getcwd(),
                run_id=stream_run_id,
                role=role,
                event_type="fingerprint",
                payload={"fingerprint": str(fingerprint.full_hash or "")},
            )
            yield {"type": "fingerprint", "fingerprint": fingerprint}

            # 4. 构建系统提示词
            system_prompt = self._build_system_prompt_for_request(profile, request, prompt_appendix)

            try:
                async for event in execute_transaction_kernel_stream(
                    self,
                    role=role,
                    profile=profile,
                    request=request,
                    system_prompt=system_prompt,
                    fingerprint=fingerprint,
                    stream_run_id=stream_run_id,
                    uep_publisher=uep_publisher,
                ):
                    yield event
            except (RuntimeError, ValueError) as e:
                inner_error = e
                logger.exception("流式执行失败 (TransactionKernel)")
                await uep_publisher.publish_stream_event(
                    workspace=self.workspace or os.getcwd(),
                    run_id=stream_run_id,
                    role=role,
                    event_type="error",
                    payload={"error": str(e)},
                )
                yield {"type": "error", "error": str(e)}

        except (RuntimeError, ValueError):
            if inner_error is None:
                raise

    # ═══════════════════════════════════════════════════════════════════════════
    # Facade 模式：服务层委托方法（新增）
    # ═══════════════════════════════════════════════════════════════════════════

    async def call(
        self,
        request: Any,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Facade: LLM 非流式调用

        委托给 llm_invoker.invoke()

        Args:
            request: AI 请求
            timeout_seconds: 超时时间

        Returns:
            InvokeResult
        """
        if self._injected_llm_invoker is not None:
            return await self._injected_llm_invoker.invoke(request, timeout_seconds)
        raise NotImplementedError("call() requires injected llm_invoker")

    async def call_stream(
        self,
        request: Any,
        timeout_seconds: float | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Facade: LLM 流式调用

        委托给 llm_invoker.invoke_stream()

        Args:
            request: AI 请求
            timeout_seconds: 超时时间

        Yields:
            StreamEvent
        """
        if self._injected_llm_invoker is not None:
            # Use async for delegation pattern with proper type handling
            stream_gen = self._injected_llm_invoker.invoke_stream(request, timeout_seconds)
            async for event in stream_gen:
                yield event
            return
        raise NotImplementedError("call_stream() requires injected llm_invoker")

    @staticmethod
    def _resolve_tool_gateway_turn_key(request_obj: Any) -> str:
        """Resolve a stable per-turn cache key for gateway counters."""

        def _normalize_id(value: Any) -> str:
            if value is None or isinstance(value, bool):
                return ""
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, int):
                return str(value)
            return ""

        run_id = _normalize_id(getattr(request_obj, "run_id", None))
        task_id = _normalize_id(getattr(request_obj, "task_id", None))
        if run_id and task_id:
            return f"{run_id}:task:{task_id}"
        if run_id:
            return run_id
        turn_id = _normalize_id(getattr(request_obj, "turn_id", None))
        if turn_id and task_id:
            return f"turn_id:{turn_id}:task:{task_id}"
        if turn_id:
            return f"turn_id:{turn_id}"
        if task_id:
            return f"task_id:{task_id}"
        return f"request_obj:{id(request_obj)}"

    def reset_tool_gateway_turn_boundary(self, turn_id: str) -> None:
        """Explicitly reset cached gateway counters when the authoritative turn id changes."""
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            return
        current_turn_key = f"turn_id:{normalized_turn_id}"
        if current_turn_key == self._cached_gateway_turn_id:
            return
        if self._cached_tool_gateway is not None:
            self._cached_tool_gateway.reset_execution_count()
            if hasattr(self._cached_tool_gateway, "_failure_budget") and hasattr(
                self._cached_tool_gateway._failure_budget, "reset"
            ):
                self._cached_tool_gateway._failure_budget.reset()
        self._cached_gateway_turn_id = current_turn_key

    async def _execute_single_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Facade: 执行单个工具

        委托给 tool_executor.execute_single()

        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文，可包含 'profile' 和 'request' 用于工具执行上下文

        Returns:
            工具执行结果
        """
        request_for_policy = context.get("request") if context else None
        if request_for_policy is not None:
            cognitive_blocked_tools = _cognitive_runtime_blocked_tools(cast(RoleTurnRequest, request_for_policy))
            if _normalize_tool_policy_name(tool_name) in cognitive_blocked_tools:
                from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

                raise ToolAuthorizationError(f"Cognitive Runtime blocked tool '{tool_name}'")

        if self._injected_tool_executor is not None:
            # BUG FIX: Even injected executors must go through authorization.
            # Previously bypassed RoleToolGateway entirely — no counting, whitelist,
            # path traversal protection, or FailureBudget.
            profile = context.get("profile") if context else None
            if profile is not None:
                from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

                executor = KernelToolExecutor(self, self.workspace)
                request = context.get("request") if context else None
                if request is None:
                    request = RoleTurnRequest(message="")

                # Reuse only within the same task-scoped turn. A new turn may
                # carry a different immutable capability scope.
                current_turn_id = self._resolve_tool_gateway_turn_key(request)
                if (
                    self._cached_tool_gateway is not None
                    and self._cached_gateway_profile is profile
                    and current_turn_id == self._cached_gateway_turn_id
                ):
                    gateway = self._cached_tool_gateway
                else:
                    reset_cached = getattr(self._cached_tool_gateway, "reset_execution_count", None)
                    if callable(reset_cached):
                        reset_cached()
                    cached_failure_budget = getattr(self._cached_tool_gateway, "_failure_budget", None)
                    reset_failure_budget = getattr(cached_failure_budget, "reset", None)
                    if callable(reset_failure_budget):
                        reset_failure_budget()
                    close_cached = getattr(self._cached_tool_gateway, "close", None)
                    if callable(close_cached):
                        close_cached()
                    gateway = executor.create_gateway(
                        profile=profile,
                        request=request,
                        tool_gateway=self._tool_gateway,
                    )
                    self._cached_tool_gateway = gateway
                    self._cached_gateway_profile = profile
                    self._cached_gateway_turn_id = current_turn_id

                can_execute, reason = gateway.check_tool_permission(tool_name, args)
                if not can_execute:
                    from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

                    raise ToolAuthorizationError(reason)

            logger.debug(
                "[_execute_single_tool] _injected_tool_executor (with auth gate): tool=%s",
                tool_name,
            )
            return await self._injected_tool_executor.execute(tool_name, args, context=context)
        # 向后兼容：使用旧的 KernelToolExecutor
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)

        # FIX: 从context中获取profile和request，如果未提供则使用默认值
        profile = None
        request = None
        if context:
            profile = context.get("profile")
            request = context.get("request")

        # 如果没有提供profile，尝试获取第一个可用角色
        if profile is None:
            available_roles = ["director", "pm", "architect", "chief_engineer", "qa"]
            for role in available_roles:
                try:
                    profile = self.registry.get_profile_or_raise(role)
                    break
                except ValueError:
                    continue

        if profile is None:
            raise ValueError("No available role profile found for tool execution")

        if request is None:
            request = RoleTurnRequest(message="")

        logger.debug(
            "[_execute_single_tool] request.run_id=%s tool=%s",
            getattr(request, "run_id", None),
            tool_name,
        )

        # Reuse cached gateway if profile matches (FailureBudget persistence for HALLUCINATION_LOOP detection)
        # BUG FIX: Reset execution_count on turn boundary to prevent cross-turn accumulation.
        # The _execution_count tracks per-turn tool calls but was never reset when the
        # gateway was reused across turns, causing permanent tool lockout.
        # Also reset FailureBudget on turn boundary to prevent stale failure state
        # from one task/turn affecting the next one.
        current_turn_id = self._resolve_tool_gateway_turn_key(request)
        if (
            self._cached_tool_gateway is not None
            and self._cached_gateway_profile is profile
            and current_turn_id == self._cached_gateway_turn_id
        ):
            gateway = self._cached_tool_gateway
        else:
            # Create a new gateway at task/turn boundary so capability scope and
            # FailureBudget cannot leak across independent tasks.
            reset_cached = getattr(self._cached_tool_gateway, "reset_execution_count", None)
            if callable(reset_cached):
                reset_cached()
            cached_failure_budget = getattr(self._cached_tool_gateway, "_failure_budget", None)
            reset_failure_budget = getattr(cached_failure_budget, "reset", None)
            if callable(reset_failure_budget):
                reset_failure_budget()
            close_cached = getattr(self._cached_tool_gateway, "close", None)
            if callable(close_cached):
                close_cached()
            gateway = executor.create_gateway(
                profile=profile,
                request=request,
                tool_gateway=self._tool_gateway,
            )
            self._cached_tool_gateway = gateway
            self._cached_gateway_profile = profile
            self._cached_gateway_turn_id = current_turn_id

        return gateway.execute_tool(tool_name, args)

    # ═══════════════════════════════════════════════════════════════════════════
    # 辅助方法（委托到各模块）
    # ═══════════════════════════════════════════════════════════════════════════

    def _emit_event(
        self,
        *,
        event_type: str,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int = 0,
        publish_realtime: bool = True,
        **kwargs: Any,
    ) -> None:
        """发射 LLM 事件（委托到 KernelEventEmitter）"""
        self._get_event_emitter().emit_runtime_llm_event(
            event_type=event_type,
            role=role,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            publish_realtime=publish_realtime,
            workspace=self.workspace,
            **kwargs,
        )

    def _emit_stream_log_event(
        self,
        *,
        writer: LogEventWriter | None,
        role: str,
        run_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """发射流日志事件（委托到 KernelEventEmitter）"""
        self._get_event_emitter().emit_stream_log_event(
            writer=writer,
            role=role,
            run_id=run_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )

    def _resolve_stream_run_id(self, request_run_id: str | None) -> str:
        """Resolve stream run_id from request or workspace runtime metadata."""
        requested = str(request_run_id or "").strip()
        if requested:
            return requested

        workspace = str(self.workspace or "").strip() or os.getcwd()
        try:
            roots = resolve_storage_roots(workspace)
            latest_run_file = os.path.join(roots.runtime_root, "latest_run.json")
            if os.path.isfile(latest_run_file):
                with open(latest_run_file, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and payload.get("run_id"):
                    return str(payload.get("run_id", "").strip())
        except (RuntimeError, ValueError):
            logger.warning("Failed to resolve stream run_id from latest_run.json", exc_info=True)
        # Fallback: generate a new run_id so tool events can be journaled
        import uuid

        return f"auto_{uuid.uuid4().hex[:12]}"

    def _build_stream_log_writer(self, run_id: str) -> LogEventWriter | None:
        """Create a log writer for streaming events."""
        if not run_id:
            return None
        workspace = str(self.workspace or "").strip() or os.getcwd()
        try:
            return get_writer(workspace=workspace, run_id=run_id)
        except (RuntimeError, ValueError):
            logger.warning("Failed to create stream log writer for run_id=%s", run_id, exc_info=True)
            return None

    def _process_deprecated_params(self, request: RoleTurnRequest) -> str:
        """处理废弃参数"""
        appendix_parts: list[str] = []
        seen: set[str] = set()

        if request.prompt_appendix:
            token = str(request.prompt_appendix).strip()
            if token and token not in seen:
                seen.add(token)
                appendix_parts.append(token)

        if request.system_prompt:
            token = str(request.system_prompt).strip()
            if token:
                warnings.warn(
                    "RoleTurnRequest.system_prompt is deprecated; use prompt_appendix instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                if token not in seen:
                    seen.add(token)
                    appendix_parts.append(token)

        extra_context = getattr(request, "extra_context", None)
        if extra_context:
            token = f"【额外上下文】\n{extra_context}"
            if token not in seen:
                seen.add(token)
                appendix_parts.append(token)

        return "\n\n".join(appendix_parts)

    def _build_context(self, profile: RoleProfile, request: RoleTurnRequest) -> ContextRequest:
        """构建上下文请求"""
        context_os_snapshot = None
        context_override = dict(request.context_override) if isinstance(request.context_override, dict) else {}
        if isinstance(request.context_override, dict):
            context_os_snapshot = request.context_override.get("context_os_snapshot")
        return ContextRequest(
            message=request.message,
            history=tuple(request.history) if request.history else (),
            task_id=request.task_id,
            context_os_snapshot=context_os_snapshot,
            context_override=context_override or None,
        )

    def _build_system_prompt_for_request(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
        prompt_appendix: str,
    ) -> str:
        """Build system prompt with domain-aware fallback compatibility."""
        domain = str(getattr(request, "domain", "") or "").strip().lower() or "code"
        context_override = getattr(request, "context_override", None)
        request_message = str(getattr(request, "message", "") or "")
        prompt_layer_options = self._resolve_prompt_layer_options(context_override, message=request_message)
        effective_prompt_appendix = self._append_prompt_profiles_for_request(
            profile=profile,
            request=request,
            prompt_appendix=prompt_appendix,
            context_override=context_override,
            message=request_message,
        )
        try:
            if prompt_layer_options:
                # Explicit kwargs (not **options) so a stray key can never bind
                # to a positional parameter; options only ever carries these two.
                return self._get_prompt_builder().build_system_prompt(
                    profile,
                    effective_prompt_appendix,
                    domain=domain,
                    message=request_message,
                    include_working_memory_contract=prompt_layer_options.get("include_working_memory_contract", True),
                    include_tool_policy=prompt_layer_options.get("include_tool_policy", True),
                )
            return self._get_prompt_builder().build_system_prompt(
                profile,
                effective_prompt_appendix,
                domain=domain,
                message=request_message,
            )
        except TypeError:
            return self._get_prompt_builder().build_system_prompt(profile, effective_prompt_appendix)

    def _append_prompt_profiles_for_request(
        self,
        *,
        profile: RoleProfile,
        request: RoleTurnRequest,
        prompt_appendix: str,
        context_override: Any,
        message: str,
    ) -> str:
        """Append language/task prompt profiles when this turn is an engineering task."""

        if "[POLARIS PROMPT PROFILE]" in str(prompt_appendix or ""):
            return prompt_appendix
        if not self._should_attach_prompt_profiles(context_override, message=message):
            return prompt_appendix
        try:
            from polaris.cells.roles.kernel.internal.prompt_profiles import build_prompt_profile_appendix

            profile_appendix, audit = build_prompt_profile_appendix(
                workspace=str(getattr(request, "workspace", "") or self.workspace or ""),
                role_id=str(getattr(profile, "role_id", "") or ""),
                message=message,
                context_override=dict(context_override) if isinstance(context_override, dict) else None,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("prompt_profile_selection_failed: %s", exc)
            return prompt_appendix

        if isinstance(context_override, dict):
            context_override["prompt_profile_audit"] = audit
            context_override["selected_prompt_profile_ids"] = list(audit.get("selected_prompt_profile_ids") or [])
            context_override["prompt_profile_appendix"] = profile_appendix
        if not profile_appendix:
            return prompt_appendix
        if not str(prompt_appendix or "").strip():
            return profile_appendix
        return f"{prompt_appendix.rstrip()}\n\n{profile_appendix}"

    @staticmethod
    def _should_attach_prompt_profiles(context_override: Any, *, message: str) -> bool:
        """Avoid profile bloat for ordinary chat; default it for concrete engineering tasks."""

        if isinstance(context_override, dict):
            delivery_mode = str(context_override.get("delivery_mode") or "").strip().lower()
            codegen_mode = str(context_override.get("director_runtime_codegen_mode") or "").strip().lower()
            if bool(context_override.get("director_runtime_codegen")) and (
                delivery_mode == "propose_patch" or codegen_mode == "proposal_then_apply"
            ):
                return False
            explicit_keys = {
                "prompt_profile",
                "prompt_profile_id",
                "prompt_profile_ids",
                "prompt_profiles",
            }
            if any(key in context_override for key in explicit_keys):
                return True
            engineering_keys = {
                "target_files",
                "files",
                "changed_files",
                "repair_target_files",
                "missing_target_files",
                "director_quality_repair",
                "delivery_mode",
                "task_type",
                "artifact",
                "artifact_type",
                "language",
                "prompt_language",
            }
            if any(key in context_override for key in engineering_keys):
                return True
            metadata = context_override.get("metadata")
            if isinstance(metadata, dict) and any(key in metadata for key in explicit_keys | engineering_keys):
                return True

        message_text = str(message or "")
        message_lower = message_text.lower()
        if "pm task contract /" in message_lower or "chief engineer blueprint" in message_lower:
            return True
        if any(token in message_lower for token in ("typescript", "python", "react", "vue", "rust", "golang")):
            return True
        return any(
            suffix in message_lower
            for suffix in (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".vue",
                ".go",
                ".rs",
                ".java",
                "package.json",
                "tsconfig.json",
                "pyproject.toml",
            )
        )

    @staticmethod
    def _resolve_prompt_layer_options(context_override: Any, *, message: str | None = None) -> dict[str, bool]:
        """Resolve per-turn prompt layer switches from explicit runtime context."""
        if not isinstance(context_override, dict):
            return {}

        def _forced_tool_choice_name(raw_choice: Any) -> str:
            if isinstance(raw_choice, dict):
                function_payload = raw_choice.get("function")
                if isinstance(function_payload, dict):
                    return str(function_payload.get("name") or "").strip().lower()
                return str(raw_choice.get("name") or "").strip().lower()
            return str(raw_choice or "").strip().lower()

        delivery_mode = str(context_override.get("delivery_mode") or "").strip().lower()
        codegen_mode = str(context_override.get("director_runtime_codegen_mode") or "").strip().lower()
        forced_tool_name = _forced_tool_choice_name(context_override.get("_transaction_kernel_forced_tool_choice"))
        is_forced_write_turn = forced_tool_name in {
            "append_to_file",
            "edit_blocks",
            "edit_file",
            "precision_edit",
            "repo_apply_diff",
            "write_file",
        }
        message_text = str(message or "")
        message_lower = message_text.lower()
        is_director_codegen_bridge = bool(context_override.get("director_runtime_codegen")) and (
            delivery_mode == "propose_patch" or codegen_mode == "proposal_then_apply"
        )
        is_factory_contract_materialization = (
            "pm task contract /" in message_lower
            and "chief engineer blueprint" in message_lower
            and "请通过运行时正式写入工具完成修改" in message_text
        )
        is_single_batch_execution = (
            delivery_mode in {"materialize_changes", "propose_patch"}
            or tool_contract_requires_single_batch(context_override)
            or is_factory_contract_materialization
            or "materialization quality repair mode" in message_lower
            or "[director_quality_repair:" in message_lower
            or ("artifact quality scan failed" in message_lower and "do not read files first" in message_lower)
        )
        suppress_working_memory = bool(
            context_override.get("suppress_working_memory_contract")
            or context_override.get("_transaction_kernel_suppress_session_patch")
            or is_director_codegen_bridge
            or is_single_batch_execution
            or is_forced_write_turn
        )
        suppress_tool_policy = bool(context_override.get("suppress_tool_policy_prompt") or is_director_codegen_bridge)

        options: dict[str, bool] = {}
        if suppress_working_memory:
            options["include_working_memory_contract"] = False
        if suppress_tool_policy:
            options["include_tool_policy"] = False
        return options

    def _create_gateway(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> RoleToolGateway | _DelegatingToolGateway:
        """Create one per-request tool gateway (委托给 KernelToolExecutor)."""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        return executor.create_gateway(profile, request, self._tool_gateway)

    async def _execute_tools(
        self, profile: RoleProfile, request: RoleTurnRequest, tool_calls: list[ToolCallResult]
    ) -> list[dict[str, Any]]:
        """执行工具调用（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        return await executor.execute_tools(profile, request, tool_calls, self._tool_gateway)

    def _split_tool_calls_by_write_budget(
        self,
        role_id: str,
        tool_calls: list[ToolCallResult],
    ) -> tuple[list[ToolCallResult], list[ToolCallResult], int]:
        """Split tool calls by write budget（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        return KernelToolExecutor.split_tool_calls_by_write_budget(role_id, tool_calls)

    def _emit_tool_execute_events(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
    ) -> None:
        """发射工具执行前事件（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        executor.emit_tool_execute_events(profile, run_id, task_id, attempt, mode_value, tool_calls, self._emit_event)

    def _emit_tool_result_events_and_collect_errors(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
        executed_tool_results: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """发射工具结果事件并收集错误（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self, self.workspace)
        return executor.emit_tool_result_events_and_collect_errors(
            profile, run_id, task_id, attempt, mode_value, tool_calls, executed_tool_results, self._emit_event
        )

    @staticmethod
    def _append_deferred_notice(
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
        executed_tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """追加 deferred notice（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        return KernelToolExecutor.append_deferred_notice(deferred_tool_calls, write_call_limit, executed_tool_results)

    @staticmethod
    def _log_deferred_write_calls(
        role_id: str,
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
    ) -> None:
        """记录 deferred write calls（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        KernelToolExecutor.log_deferred_write_calls(role_id, deferred_tool_calls, write_call_limit)

    def _parse_content_and_thinking_tool_calls(
        self,
        content: str,
        thinking: str | None,
        profile: Any,
        native_tool_calls: list[dict[str, Any]] | None,
        native_tool_provider: str,
    ) -> list[Any]:
        """Parse tool calls from content and thinking, filtering out thinking-only calls.

        Args:
            content: Raw text content from LLM
            thinking: Thinking content (may contain [TOOL_CALL]...[/TOOL_CALL] markers)
            profile: Role profile for allowed tool names
            native_tool_calls: Native tool calls from provider
            native_tool_provider: Provider hint for parsing

        Returns:
            List of parsed and filtered ToolCallResult objects
        """

        # Filter out tool calls that are only in thinking (not in main content)
        # by parsing only the main content (not thinking)
        result: list[ToolCallResult] = []
        seen: set[tuple[str, str]] = set()

        # Parse tool calls from main content and/or native_tool_calls
        # Note: native_tool_calls must be parsed even if content is empty
        # because LLM may emit tools via native protocol without content
        valid_parsed = self._get_output_parser().parse_tool_calls(
            content or "",  # Ensure content is never None
            native_tool_calls=native_tool_calls,
            native_provider=native_tool_provider,
        )
        for call in valid_parsed:
            key = (call.tool, str(call.args.get("path", "") or call.args.get("file", "")))
            if key not in seen:
                seen.add(key)
                result.append(call)

        return result


__all__ = [
    "RoleExecutionKernel",
    "get_suggestions_for_error",
]
