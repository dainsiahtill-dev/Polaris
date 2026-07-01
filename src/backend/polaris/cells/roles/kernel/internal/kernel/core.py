"""Role Execution Kernel Core - 角色执行内核核心

RoleExecutionKernel is the public coordination entrypoint for role turns.

架构:
    - RoleExecutionKernel: public coordination entrypoint
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
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.context_gateway_config_builder import (
    ContextGatewayConfigFactory,
)
from polaris.cells.roles.kernel.internal.kernel.context_request_builder import build_context_request
from polaris.cells.roles.kernel.internal.kernel.error_handler import (
    KernelEventEmitter,
    LLMEventType,
)
from polaris.cells.roles.kernel.internal.kernel.helpers import (
    quality_result_to_dict,
)
from polaris.cells.roles.kernel.internal.kernel.output_parser_provider import get_output_parser
from polaris.cells.roles.kernel.internal.kernel.prompt_assembly import (
    append_prompt_profiles_for_request,
    build_system_prompt_for_request,
)
from polaris.cells.roles.kernel.internal.kernel.request_appendix import build_prompt_appendix_from_request
from polaris.cells.roles.kernel.internal.kernel.stream_run_id import resolve_stream_run_id
from polaris.cells.roles.kernel.internal.kernel.suggestions import get_suggestions_for_error
from polaris.cells.roles.kernel.internal.kernel.tool_gateway_turn_key import (
    resolve_explicit_turn_key,
)
from polaris.cells.roles.kernel.internal.kernel.turn_execution import (
    execute_transaction_kernel_stream,
    execute_transaction_kernel_turn,
)
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector
from polaris.cells.roles.kernel.internal.output_parser import OutputParser
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder
from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker, QualityResult
from polaris.cells.roles.kernel.public.config import KernelConfig, get_default_config
from polaris.cells.roles.profile.public.service import (
    RoleProfileRegistry,
    RoleTurnRequest,
    RoleTurnResult,
)
from polaris.kernelone.events.uep_publisher import UEPEventPublisher
from polaris.kernelone.trace import get_tracer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

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


class RoleExecutionKernel:
    """角色执行内核 - public coordination entrypoint

    统一执行角色对话的两种模式：
    - CHAT: 聊天模式（用户交互）
    - WORKFLOW: 工作流模式（自动化执行）

    当前架构:
    - RoleExecutionKernel: public coordination entrypoint，提供统一接口并协调服务层
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
    # Public configuration accessors
    # ═══════════════════════════════════════════════════════════════════════════

    @property
    def config(self) -> KernelConfig:
        """获取当前 Kernel 配置"""
        return self._config

    @property
    def context_gateway_config_factory(self) -> ContextGatewayConfigFactory | None:
        """Return the runtime-injected ContextGatewayConfig factory, if any."""
        return self._context_gateway_config_factory

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

        # 2. 处理请求附录
        try:
            prompt_appendix = build_prompt_appendix_from_request(request)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"参数处理失败: {e}", is_complete=True)

        prompt_appendix = append_prompt_profiles_for_request(
            profile=profile,
            request=request,
            prompt_appendix=prompt_appendix,
            context_override=getattr(request, "context_override", None),
            message=str(getattr(request, "message", "") or ""),
            workspace=self.workspace,
        )

        # 3. 构建提示词指纹
        try:
            fingerprint = self._get_prompt_builder().build_fingerprint(profile, prompt_appendix)
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"提示词构建失败: {e}", is_complete=True)

        # 4. 构建基础系统提示词
        try:
            base_system_prompt = build_system_prompt_for_request(
                prompt_builder=self._get_prompt_builder(),
                profile=profile,
                request=request,
                prompt_appendix=prompt_appendix,
                workspace=self.workspace,
            )
        except (RuntimeError, ValueError) as e:
            return RoleTurnResult(error=f"系统提示词构建失败: {e}", is_complete=True)

        # 5. 构建上下文（验证可用性，结果由 TransactionKernel 使用）
        try:
            _ = build_context_request(request)
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

            response_schema: type | None = None

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
                            candidate = get_output_parser(self).extract_json(effective_content)
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
                    self._get_event_emitter().emit_runtime_llm_event(
                        event_type=LLMEventType.VALIDATION_FAIL,
                        role=role,
                        run_id=observer_run_id,
                        task_id=task_id,
                        attempt=attempt,
                        workspace=self.workspace,
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
                        self._get_event_emitter().emit_runtime_llm_event(
                            event_type=LLMEventType.CALL_RETRY,
                            role=role,
                            run_id=observer_run_id,
                            task_id=task_id,
                            attempt=attempt,
                            workspace=self.workspace,
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

                self._get_event_emitter().emit_runtime_llm_event(
                    event_type=LLMEventType.VALIDATION_PASS,
                    role=role,
                    run_id=observer_run_id,
                    task_id=task_id,
                    attempt=attempt,
                    workspace=self.workspace,
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
        stream_run_id = resolve_stream_run_id(request.run_id, self.workspace)
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

            # 2. 处理请求附录
            prompt_appendix = build_prompt_appendix_from_request(request)
            prompt_appendix = append_prompt_profiles_for_request(
                profile=profile,
                request=request,
                prompt_appendix=prompt_appendix,
                context_override=getattr(request, "context_override", None),
                message=str(getattr(request, "message", "") or ""),
                workspace=self.workspace,
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
            system_prompt = build_system_prompt_for_request(
                prompt_builder=self._get_prompt_builder(),
                profile=profile,
                request=request,
                prompt_appendix=prompt_appendix,
                workspace=self.workspace,
            )

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
    # Public service entrypoints
    # ═══════════════════════════════════════════════════════════════════════════

    async def call(
        self,
        request: Any,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Execute a non-streaming LLM call through the injected invoker.

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
        """Execute a streaming LLM call through the injected invoker.

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

    def reset_tool_gateway_turn_boundary(self, turn_id: str) -> None:
        """Explicitly reset cached gateway counters when the authoritative turn id changes."""
        current_turn_key = resolve_explicit_turn_key(turn_id)
        if not current_turn_key:
            return
        if current_turn_key == self._cached_gateway_turn_id:
            return
        if self._cached_tool_gateway is not None:
            self._cached_tool_gateway.reset_execution_count()
            if hasattr(self._cached_tool_gateway, "_failure_budget") and hasattr(
                self._cached_tool_gateway._failure_budget, "reset"
            ):
                self._cached_tool_gateway._failure_budget.reset()
        self._cached_gateway_turn_id = current_turn_key


__all__ = [
    "RoleExecutionKernel",
    "get_suggestions_for_error",
]
