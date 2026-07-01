"""RoleExecutionKernel public contract tests.

These tests describe the supported public construction, dependency injection,
tool dispatch, and retired LLMCaller boundary for the current Role Kernel path.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.output_parser_provider import get_output_parser
from polaris.cells.roles.kernel.internal.kernel.prompt_builder_provider import get_prompt_builder
from polaris.cells.roles.kernel.internal.kernel.quality_checker_provider import get_quality_checker
from polaris.cells.roles.kernel.internal.kernel.tool_gateway_turn_key import resolve_tool_gateway_turn_key
from polaris.cells.roles.kernel.internal.kernel.tool_policy import (
    _apply_runtime_tool_policy,
    _cognitive_runtime_blocked_tools,
    _filter_cognitive_blocked_tool_definitions,
)
from polaris.cells.roles.kernel.internal.kernel.tool_runtime_executor import execute_single_tool
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


class TestRoleExecutionKernelConstruction:
    """RoleExecutionKernel construction contract."""

    def test_basic_initialization(self) -> None:
        """测试基本初始化。"""
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

    def test_internal_llm_invoker_accessor_returns_invoker_without_deprecation(self) -> None:
        """Kernel LLM accessor returns canonical LLMInvoker without warnings."""
        from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker

        kernel = RoleExecutionKernel(workspace=".")

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            caller = kernel._get_llm_invoker()

        assert isinstance(caller, LLMInvoker)
        assert not any("LLMCaller" in str(item.message) for item in captured)

    def test_llm_caller_is_not_public_kernel_export(self) -> None:
        """LLMCaller must not be exported from any kernel boundary."""
        import polaris.cells.roles.kernel as kernel_public_root
        import polaris.cells.roles.kernel.internal.llm_caller as llm_caller_package
        import polaris.cells.roles.kernel.public as kernel_public

        assert "LLMCaller" not in kernel_public_root.__all__
        assert "LLMCaller" not in kernel_public.__all__
        assert "LLMCaller" not in llm_caller_package.__all__
        with pytest.raises(AttributeError):
            kernel_public_root.__getattr__("LLMCaller")
        with pytest.raises(AttributeError):
            kernel_public.__getattr__("LLMCaller")
        assert "LLMCaller" not in vars(llm_caller_package)

    def test_llm_caller_shell_file_is_retired(self) -> None:
        """The retired LLMCaller shell file must not reappear."""
        import polaris

        package_root = Path(polaris.__file__).resolve().parent
        caller_file = package_root / "cells/roles/kernel/internal/llm_caller/caller.py"

        assert not caller_file.exists()

    def test_llm_caller_direct_imports_are_retired(self) -> None:
        """New production callers must not depend on retired LLMCaller."""
        import polaris

        package_root = Path(polaris.__file__).resolve().parent
        allowed: set[Path] = set()
        offenders: list[str] = []
        for path in package_root.rglob("*.py"):
            rel = path.relative_to(package_root)
            if "tests" in rel.parts:
                continue
            text = path.read_text(encoding="utf-8")
            imports_llm_caller = "llm_caller.caller import LLMCaller" in text or "from .caller import LLMCaller" in text
            if imports_llm_caller and path not in allowed:
                offenders.append(rel.as_posix())

        assert offenders == []

    def test_kernel_no_longer_exposes_llm_caller_injection_names(self) -> None:
        """Kernel DI names must stay aligned with LLMInvoker, not LLMCaller."""
        assert not hasattr(RoleExecutionKernel, "inject_llm_caller")
        assert not hasattr(RoleExecutionKernel, "_get_llm_caller")


class TestInjectedServiceEntrypoints:
    """Injected service entrypoint tests."""

    @pytest.mark.asyncio
    async def test_call_uses_invoker(self) -> None:
        """测试 call() 方法调用 llm_invoker。"""
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
    async def test_call_stream_uses_invoker(self) -> None:
        """测试 call_stream() 方法使用 llm_invoker。"""
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
    async def test_execute_single_tool_uses_executor(self) -> None:
        """测试 execute_single_tool() owner 调用 tool_executor。"""
        mock_executor = MagicMock(spec=CellToolExecutorPort)
        mock_executor.execute = AsyncMock(return_value={"success": True})

        kernel = RoleExecutionKernel(
            workspace=".",
            tool_executor=mock_executor,
        )

        result = await execute_single_tool(kernel, tool_name="read_file", args={"path": "test.py"})

        mock_executor.execute.assert_called_once()
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_execute_single_tool_enforces_cognitive_runtime_blocked_tools(self) -> None:
        """Cognitive Runtime blocked tools must be rejected before executor dispatch."""
        from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError
        from polaris.cells.roles.profile.public.service import RoleTurnRequest

        mock_executor = MagicMock(spec=CellToolExecutorPort)
        mock_executor.execute = AsyncMock(return_value={"success": True})
        kernel = RoleExecutionKernel(workspace=".", tool_executor=mock_executor)
        request = RoleTurnRequest(
            message="do not delete files",
            metadata={
                "cognitive_tool_policy": {
                    "source": "cognitive_runtime_mainline",
                    "blocked_tools": ("delete_file",),
                }
            },
        )

        with pytest.raises(ToolAuthorizationError, match="Cognitive Runtime blocked tool"):
            await execute_single_tool(
                kernel,
                tool_name="delete_file",
                args={"file": "src/app.py"},
                context={"request": request},
            )

        mock_executor.execute.assert_not_called()

    def test_cognitive_runtime_blocked_tools_filter_native_tool_definitions(self) -> None:
        """Cognitive Runtime policy must remove blocked tools from native schemas."""
        from polaris.cells.roles.profile.public.service import RoleTurnRequest

        request = RoleTurnRequest(
            message="safe edit only",
            metadata={"cognitive_tool_policy": {"blocked_tools": ("delete_file",)}},
        )
        blocked = _cognitive_runtime_blocked_tools(request)
        filtered = _filter_cognitive_blocked_tool_definitions(
            [
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "delete_file"}},
            ],
            blocked,
        )

        assert [item["function"]["name"] for item in filtered] == ["read_file"]

    def test_context_budget_pressure_filters_expensive_context_tools(self) -> None:
        """ContextGateway budget pressure must reduce expensive context tools before LLM decision."""
        from polaris.cells.roles.profile.public.service import RoleTurnRequest

        request = RoleTurnRequest(message="inspect efficiently")
        context_result = SimpleNamespace(
            metadata={
                "context_decision_hints": {
                    "source": "roles.kernel.context_gateway",
                    "budget_pressure": True,
                    "suppress_expensive_context_tools": True,
                }
            }
        )

        filtered, audit = _apply_runtime_tool_policy(
            request=request,
            context_result=context_result,
            tool_definitions=[
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "repo_read_slice"}},
                {"type": "function", "function": {"name": "repo_rg"}},
            ],
        )

        assert [item["function"]["name"] for item in filtered] == ["repo_read_slice", "repo_rg"]
        assert audit["context_tool_policy_applied"] is True
        assert audit["context_blocked_tools"] == ["read_file"]

    def test_context_read_only_hints_filter_filesystem_and_exec_tools(self) -> None:
        """ContextGateway read-only hints must suppress mutating/exec tools without expanding access."""
        from polaris.cells.roles.profile.public.service import RoleTurnRequest

        request = RoleTurnRequest(message="review only")
        context_result = SimpleNamespace(
            metadata={
                "context_decision_hints": {
                    "source": "roles.kernel.context_gateway",
                    "suppress_mutating_tools": True,
                }
            }
        )

        filtered, audit = _apply_runtime_tool_policy(
            request=request,
            context_result=context_result,
            tool_definitions=[
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "edit_file"}},
                {"type": "function", "function": {"name": "execute_command"}},
                {"type": "function", "function": {"name": "update_session_state"}},
                {"type": "function", "function": {"name": "compact_context"}},
            ],
        )

        assert [item["function"]["name"] for item in filtered] == [
            "read_file",
            "update_session_state",
            "compact_context",
        ]
        assert audit["context_tool_policy_applied"] is True
        assert "edit_file" in audit["context_blocked_tools"]
        assert "execute_command" in audit["context_blocked_tools"]
        assert "update_session_state" not in audit["context_blocked_tools"]
        assert "compact_context" not in audit["context_blocked_tools"]

    @pytest.mark.asyncio
    async def test_execute_single_tool_resets_counter_between_none_run_id_requests(self, monkeypatch) -> None:
        """run_id 缺失时，不同 request 对象之间应触发计数重置。"""
        kernel = RoleExecutionKernel(workspace=".")

        mock_gateway = MagicMock()
        mock_gateway.check_tool_permission.return_value = (True, "授权通过")
        mock_gateway.execute_tool.return_value = {"success": True}
        mock_gateway.reset_execution_count = MagicMock()

        mock_executor_instance = MagicMock()
        mock_executor_instance.create_gateway.return_value = mock_gateway

        class _PatchedKernelToolExecutor:
            def __init__(self, _kernel, _workspace) -> None:
                pass

            def create_gateway(self, profile, request, tool_gateway=None):
                return mock_executor_instance.create_gateway(profile, request, tool_gateway=tool_gateway)

        monkeypatch.setattr(
            "polaris.cells.roles.kernel.internal.kernel.tool_executor.KernelToolExecutor",
            _PatchedKernelToolExecutor,
        )

        profile = MagicMock()
        request_a = MagicMock()
        request_a.run_id = None
        request_a.turn_id = ""
        request_b = MagicMock()
        request_b.run_id = None
        request_b.turn_id = ""

        await execute_single_tool(
            kernel,
            tool_name="read_file",
            args={"file": "a.py"},
            context={"profile": profile, "request": request_a},
        )
        await execute_single_tool(
            kernel,
            tool_name="read_file",
            args={"file": "b.py"},
            context={"profile": profile, "request": request_b},
        )

        assert mock_gateway.reset_execution_count.call_count == 1

    def test_resolve_tool_gateway_turn_key_prefers_run_id(self) -> None:
        """run_id 存在时，turn key 必须稳定使用 run_id。"""
        request = MagicMock()
        request.run_id = "run_123"
        request.turn_id = "ignored"

        key = resolve_tool_gateway_turn_key(request)

        assert key == "run_123"

    def test_resolve_tool_gateway_turn_key_uses_turn_id_when_run_id_missing(self) -> None:
        """run_id 缺失时应优先使用显式 turn_id。"""
        request = MagicMock()
        request.run_id = None
        request.turn_id = "turn_456"

        key = resolve_tool_gateway_turn_key(request)

        assert key == "turn_id:turn_456"

    def test_resolve_tool_gateway_turn_key_includes_task_id(self) -> None:
        """task_id 必须参与 key，避免同一 run 下不同任务共享工具预算。"""
        request = MagicMock()
        request.run_id = "run_123"
        request.task_id = "task_456"
        request.turn_id = "ignored"

        key = resolve_tool_gateway_turn_key(request)

        assert key == "run_123:task:task_456"

    def test_resolve_tool_gateway_turn_key_falls_back_to_request_identity(self) -> None:
        """run_id 缺失时应回退到 request identity，避免跨回合计数串扰。"""
        request_a = MagicMock()
        request_a.run_id = None
        request_a.turn_id = ""
        request_b = MagicMock()
        request_b.run_id = None
        request_b.turn_id = ""

        key_a = resolve_tool_gateway_turn_key(request_a)
        key_b = resolve_tool_gateway_turn_key(request_b)

        assert key_a.startswith("request_obj:")
        assert key_b.startswith("request_obj:")
        assert key_a != key_b

    def test_reset_tool_gateway_turn_boundary_is_idempotent_per_turn(self) -> None:
        """显式 turn reset 对同一 turn 不重复清零，对新 turn 必须清零。"""
        kernel = RoleExecutionKernel(workspace=".")
        mock_gateway = MagicMock()
        mock_gateway.reset_execution_count = MagicMock()
        mock_failure_budget = MagicMock()
        mock_failure_budget.reset = MagicMock()
        mock_gateway._failure_budget = mock_failure_budget
        kernel._cached_tool_gateway = mock_gateway
        kernel._cached_gateway_turn_id = "request_obj:previous"

        kernel.reset_tool_gateway_turn_boundary("turn_a")
        kernel.reset_tool_gateway_turn_boundary("turn_a")
        kernel.reset_tool_gateway_turn_boundary("turn_b")

        assert mock_gateway.reset_execution_count.call_count == 2
        assert mock_failure_budget.reset.call_count == 2
        assert kernel._cached_gateway_turn_id == "turn_id:turn_b"


class TestLazyLoading:
    """懒加载测试"""

    def test_prompt_builder_lazy_loaded(self) -> None:
        """测试 Prompt Builder 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        # 初始状态为 None
        assert kernel._prompt_builder is None

        # 访问时创建
        builder = get_prompt_builder(kernel)
        assert builder is not None
        assert kernel._prompt_builder is builder

        # 再次访问返回同一实例
        assert get_prompt_builder(kernel) is builder

    def test_output_parser_lazy_loaded(self) -> None:
        """测试 Output Parser 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._output_parser is None

        parser = get_output_parser(kernel)
        assert parser is not None
        assert get_output_parser(kernel) is parser

    def test_quality_checker_lazy_loaded(self) -> None:
        """测试 Quality Checker 懒加载"""
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._quality_checker is None

        checker = get_quality_checker(kernel)
        assert checker is not None
        assert get_quality_checker(kernel) is checker

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
        assert get_prompt_builder(kernel) is mock_builder
        assert kernel._prompt_builder is None  # 从未创建


class TestPublicKernelApi:
    """Public RoleExecutionKernel API contract tests."""

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
        """测试 tool_gateway 注入。"""
        mock_gateway = MagicMock()
        kernel = RoleExecutionKernel(
            workspace=".",
            tool_gateway=mock_gateway,
        )
        assert kernel._tool_gateway is mock_gateway


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
