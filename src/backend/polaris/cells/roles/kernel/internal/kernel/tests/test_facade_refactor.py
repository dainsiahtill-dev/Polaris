"""向后兼容性测试 - RoleExecutionKernel Facade 重构

验证重构后的 RoleExecutionKernel 保持向后兼容性。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.public.config import KernelConfig
from polaris.cells.roles.kernel.services.contracts import (
    CellToolExecutorPort,
    IEventEmitter,
    ILLMInvoker,
    IOutputParser,
    IPromptBuilder,
    IQualityChecker,
)
from polaris.cells.roles.profile.public.service import RoleProfileRegistry


class TestBackwardCompatibility:
    """向后兼容性测试"""

    def test_basic_initialization(self) -> None:
        """测试基本初始化（向后兼容）"""
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel.workspace == "."
        assert kernel.registry is not None
        assert kernel.config is not None

    def test_initialization_with_registry(self) -> None:
        """测试带注册表的初始化"""
        registry = RoleProfileRegistry()
        kernel = RoleExecutionKernel(workspace=".", registry=registry)
        assert kernel.registry is registry

    def test_initialization_with_config(self) -> None:
        """测试带配置的初始化"""
        config = KernelConfig(max_retries=5)
        kernel = RoleExecutionKernel(workspace=".", config=config)
        assert kernel.config.max_retries == 5

    def test_create_default_factory(self) -> None:
        """测试 create_default 工厂方法"""
        kernel = RoleExecutionKernel.create_default(workspace=".")
        assert kernel.workspace == "."
        assert kernel.registry is not None
        assert kernel.config is not None


class TestDependencyInjection:
    """依赖注入测试"""

    def test_inject_llm_invoker(self) -> None:
        """测试注入 LLM Invoker"""
        mock_invoker = MagicMock(spec=ILLMInvoker)
        kernel = RoleExecutionKernel(
            workspace=".",
            llm_invoker=mock_invoker,
        )
        assert kernel._injected_llm_invoker is mock_invoker

    def test_inject_tool_executor(self) -> None:
        """测试注入 Tool Executor"""
        mock_executor = MagicMock(spec=CellToolExecutorPort)
        kernel = RoleExecutionKernel(
            workspace=".",
            tool_executor=mock_executor,
        )
        assert kernel._injected_tool_executor is mock_executor

    def test_inject_prompt_builder(self) -> None:
        """测试注入 Prompt Builder"""
        mock_builder = MagicMock(spec=IPromptBuilder)
        kernel = RoleExecutionKernel(
            workspace=".",
            prompt_builder=mock_builder,
        )
        assert kernel._injected_prompt_builder is mock_builder

    def test_inject_output_parser(self) -> None:
        """测试注入 Output Parser"""
        mock_parser = MagicMock(spec=IOutputParser)
        kernel = RoleExecutionKernel(
            workspace=".",
            output_parser=mock_parser,
        )
        assert kernel._injected_output_parser is mock_parser

    def test_inject_quality_checker(self) -> None:
        """测试注入 Quality Checker"""
        mock_checker = MagicMock(spec=IQualityChecker)
        kernel = RoleExecutionKernel(
            workspace=".",
            quality_checker=mock_checker,
        )
        assert kernel._injected_quality_checker is mock_checker

    def test_inject_event_emitter(self) -> None:
        """测试注入 Event Emitter"""
        mock_emitter = MagicMock(spec=IEventEmitter)
        kernel = RoleExecutionKernel(
            workspace=".",
            event_emitter=mock_emitter,
        )
        assert kernel._injected_event_emitter is mock_emitter

    def test_inject_all_services(self) -> None:
        """测试同时注入所有服务"""
        kernel = RoleExecutionKernel(
            workspace=".",
            llm_invoker=MagicMock(spec=ILLMInvoker),
            tool_executor=MagicMock(spec=CellToolExecutorPort),
            prompt_builder=MagicMock(spec=IPromptBuilder),
            output_parser=MagicMock(spec=IOutputParser),
            quality_checker=MagicMock(spec=IQualityChecker),
            event_emitter=MagicMock(spec=IEventEmitter),
        )
        assert kernel._injected_llm_invoker is not None
        assert kernel._injected_tool_executor is not None
        assert kernel._injected_prompt_builder is not None
        assert kernel._injected_output_parser is not None
        assert kernel._injected_quality_checker is not None
        assert kernel._injected_event_emitter is not None


class TestFacadeMethods:
    """Facade 方法测试"""

    @pytest.mark.asyncio
    async def test_call_delegates_to_invoker(self) -> None:
        """测试 call() 方法委托给 llm_invoker"""
        mock_invoker = MagicMock(spec=ILLMInvoker)
        mock_invoker.invoke = AsyncMock(return_value=MagicMock())

        kernel = RoleExecutionKernel(
            workspace=".",
            llm_invoker=mock_invoker,
        )

        mock_request = MagicMock()
        result = await kernel.call(mock_request, timeout_seconds=30.0)

        mock_invoker.invoke.assert_called_once_with(mock_request, 30.0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_call_raises_without_invoker(self) -> None:
        """测试 call() 在没有注入 invoker 时抛出异常"""
        kernel = RoleExecutionKernel(workspace=".")

        with pytest.raises(NotImplementedError):
            await kernel.call(MagicMock())

    @pytest.mark.asyncio
    async def test_call_stream_delegates_to_invoker(self) -> None:
        """测试 call_stream() 方法委托给 llm_invoker"""
        mock_invoker = MagicMock(spec=ILLMInvoker)

        async def mock_stream(*args, **kwargs):
            yield MagicMock()
            yield MagicMock()

        mock_invoker.invoke_stream = mock_stream

        kernel = RoleExecutionKernel(
            workspace=".",
            llm_invoker=mock_invoker,
        )

        mock_request = MagicMock()
        events = []
        async for event in kernel.call_stream(mock_request):
            events.append(event)

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_execute_single_tool_delegates_to_executor(self) -> None:
        """测试 _execute_single_tool() 方法委托给 tool_executor"""
        mock_executor = MagicMock(spec=CellToolExecutorPort)
        mock_executor.execute = AsyncMock(return_value={"success": True})

        kernel = RoleExecutionKernel(
            workspace=".",
            tool_executor=mock_executor,
        )

        result = await kernel._execute_single_tool("read_file", {"path": "test.py"})

        mock_executor.execute.assert_called_once()
        assert result == {"success": True}


class TestLazyLoading:
    """懒加载测试"""

    def test_prompt_builder_lazy_loaded(self) -> None:
        """测试 Prompt Builder 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        # 初始状态为 None
        assert kernel._prompt_builder is None

        # 访问时创建
        builder = kernel._get_prompt_builder()
        assert builder is not None
        assert kernel._prompt_builder is builder

        # 再次访问返回同一实例
        assert kernel._get_prompt_builder() is builder

    def test_output_parser_lazy_loaded(self) -> None:
        """测试 Output Parser 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._output_parser is None

        parser = kernel._get_output_parser()
        assert parser is not None
        assert kernel._get_output_parser() is parser

    def test_quality_checker_lazy_loaded(self) -> None:
        """测试 Quality Checker 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._quality_checker is None

        checker = kernel._get_quality_checker()
        assert checker is not None
        assert kernel._get_quality_checker() is checker

    def test_event_emitter_lazy_loaded(self) -> None:
        """测试 Event Emitter 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._event_emitter is None

        emitter = kernel._get_event_emitter()
        assert emitter is not None
        assert kernel._get_event_emitter() is emitter

    def test_injected_services_take_precedence(self) -> None:
        """测试注入的服务优先于懒加载"""
        mock_builder = MagicMock(spec=IPromptBuilder)
        kernel = RoleExecutionKernel(
            workspace=".",
            prompt_builder=mock_builder,
        )

        # 返回注入的 mock，而不是创建新的
        assert kernel._get_prompt_builder() is mock_builder
        assert kernel._prompt_builder is None  # 从未创建


class TestExistingAPICompatibility:
    """现有 API 兼容性测试"""

    def test_run_method_exists(self) -> None:
        """测试 run() 方法存在"""
        kernel = RoleExecutionKernel(workspace=".")
        assert hasattr(kernel, "run")
        assert callable(kernel.run)

    def test_run_stream_method_exists(self) -> None:
        """测试 run_stream() 方法存在"""
        kernel = RoleExecutionKernel(workspace=".")
        assert hasattr(kernel, "run_stream")
        assert callable(kernel.run_stream)

    def test_config_property_exists(self) -> None:
        """测试 config 属性存在"""
        kernel = RoleExecutionKernel(workspace=".")
        assert hasattr(kernel, "config")
        assert kernel.config is not None

    def test_tool_gateway_injection(self) -> None:
        """测试 tool_gateway 注入（原有功能）"""
        mock_gateway = MagicMock()
        kernel = RoleExecutionKernel(
            workspace=".",
            tool_gateway=mock_gateway,
        )
        assert kernel._tool_gateway is mock_gateway


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
