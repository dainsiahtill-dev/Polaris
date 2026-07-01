"""Role Execution Kernel Core - 角色执行内核核心

RoleExecutionKernel is the public coordination entrypoint for role turns.

架构:
    - RoleExecutionKernel: public coordination entrypoint
    - LLMInvoker: LLM调用服务 (ILLMInvoker)
    - ToolExecutor: 工具执行服务 (CellToolExecutorPort)
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
import os
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.context_gateway_config_builder import (
    ContextGatewayConfigFactory,
)
from polaris.cells.roles.kernel.internal.kernel.error_handler import KernelEventEmitter
from polaris.cells.roles.kernel.internal.kernel.non_stream_turn_flow import execute_non_stream_role_turn
from polaris.cells.roles.kernel.internal.kernel.stream_turn_flow import execute_stream_role_turn
from polaris.cells.roles.kernel.internal.kernel.suggestions import get_suggestions_for_error
from polaris.cells.roles.kernel.internal.output_parser import OutputParser
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder
from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker
from polaris.cells.roles.kernel.public.config import KernelConfig, get_default_config
from polaris.cells.roles.profile.public.service import (
    RoleProfileRegistry,
    RoleTurnRequest,
    RoleTurnResult,
)

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


class RoleExecutionKernel:
    """角色执行内核 - public coordination entrypoint

    统一执行角色对话的两种模式：
    - CHAT: 聊天模式（用户交互）
    - WORKFLOW: 工作流模式（自动化执行）

    当前架构:
    - RoleExecutionKernel: public coordination entrypoint，提供统一接口并协调服务层
    - LLMInvoker (ILLMInvoker): LLM调用服务
    - ToolExecutor (CellToolExecutorPort): 工具执行服务
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

        # 保存注入的服务（可能为 None，由 provider owner 懒加载处理）
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
        return await execute_non_stream_role_turn(
            kernel=self,
            role=role,
            request=request,
        )

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
        async for event in execute_stream_role_turn(
            kernel=self,
            role=role,
            request=request,
        ):
            yield event


__all__ = [
    "RoleExecutionKernel",
    "get_suggestions_for_error",
]
