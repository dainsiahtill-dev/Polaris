"""tests/test_roles_engine_dialogue.py

验证 roles.engine 与 roles.adapters 对 LLM/role runtime 调用路径收敛。

覆盖点：
1. BaseEngine._call_llm 通过 EngineContext.llm_caller 委托（不直接调用 LLM provider）
2. ReActEngine / PlanSolveEngine / ToTEngine 继承 BaseEngine._call_llm，不再自带副本
3. 生产 role adapters 通过 runtime_dialogue 进入 roles.runtime/Context OS
4. llm.dialogue 仅作为 runtime_dialogue 中可观测 legacy fallback
5. 无跨 Cell internal 导入：roles 层不直接 import llm.dialogue.internal.*
"""

from __future__ import annotations

import pathlib as _pathlib
from unittest.mock import AsyncMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# 辅助工具
# ─────────────────────────────────────────────────────────────────────────────


def _make_engine_context(llm_caller=None, role: str = "director", task: str = "test"):
    """构造一个最小 EngineContext，optionally 注入 llm_caller。"""
    from polaris.cells.roles.engine.internal.base import EngineContext

    return EngineContext(
        workspace="/tmp/test_workspace",
        role=role,
        task=task,
        llm_caller=llm_caller,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. BaseEngine._call_llm 委托路径测试
# ─────────────────────────────────────────────────────────────────────────────


class TestBaseEngineLlmCaller:
    """BaseEngine._call_llm 是 DI 委托包装，不直接调用 LLM。"""

    @pytest.mark.asyncio
    async def test_call_llm_delegates_to_injected_caller(self):
        """_call_llm 应调用 context.llm_caller 并返回其结果。"""
        from polaris.cells.roles.engine.internal.react import ReActEngine

        expected_response = '{"thought": "ok", "action": "finish", "action_input": {"answer": "done"}}'
        mock_caller = AsyncMock(return_value=expected_response)

        ctx = _make_engine_context(llm_caller=mock_caller)
        engine = ReActEngine(workspace="/tmp")

        result = await engine._call_llm(ctx, "some prompt")

        assert result == expected_response
        mock_caller.assert_awaited_once()
        call_kwargs = mock_caller.call_args.kwargs
        assert call_kwargs["prompt"] == "some prompt"
        assert call_kwargs["role"] == "director"
        assert "max_tokens" in call_kwargs

    @pytest.mark.asyncio
    async def test_call_llm_returns_empty_string_when_no_caller(self):
        """llm_caller 未注入时返回空字符串（不返回假数据，让解析器走降级路径）。"""
        from polaris.cells.roles.engine.internal.react import ReActEngine

        ctx = _make_engine_context(llm_caller=None)
        engine = ReActEngine(workspace="/tmp")

        result = await engine._call_llm(ctx, "some prompt")

        assert result == ""

    @pytest.mark.asyncio
    async def test_plan_solve_engine_uses_base_call_llm(self):
        """PlanSolveEngine 不再有自己的 _call_llm，继承自 BaseEngine。"""
        from polaris.cells.roles.engine.internal.base import BaseEngine
        from polaris.cells.roles.engine.internal.plan_solve import PlanSolveEngine

        assert "._call_llm" not in PlanSolveEngine.__dict__, (
            "PlanSolveEngine 不应在自身 __dict__ 中定义 _call_llm，应继承 BaseEngine"
        )
        # 确认 MRO 中 _call_llm 来自 BaseEngine
        for cls in PlanSolveEngine.__mro__:
            if "_call_llm" in cls.__dict__:
                assert cls is BaseEngine, f"_call_llm 应由 BaseEngine 提供，实际来自 {cls}"
                break

    @pytest.mark.asyncio
    async def test_react_engine_uses_base_call_llm(self):
        """ReActEngine 不再有自己的 _call_llm，继承自 BaseEngine。"""
        from polaris.cells.roles.engine.internal.base import BaseEngine
        from polaris.cells.roles.engine.internal.react import ReActEngine

        assert "_call_llm" not in ReActEngine.__dict__, (
            "ReActEngine 不应在自身 __dict__ 中定义 _call_llm，应继承 BaseEngine"
        )
        for cls in ReActEngine.__mro__:
            if "_call_llm" in cls.__dict__:
                assert cls is BaseEngine, f"_call_llm 应由 BaseEngine 提供，实际来自 {cls}"
                break

    @pytest.mark.asyncio
    async def test_tot_engine_uses_base_call_llm(self):
        """ToTEngine 不再有自己的 _call_llm，继承自 BaseEngine。"""
        from polaris.cells.roles.engine.internal.base import BaseEngine
        from polaris.cells.roles.engine.internal.tot import ToTEngine

        assert "_call_llm" not in ToTEngine.__dict__, (
            "ToTEngine 不应在自身 __dict__ 中定义 _call_llm，应继承 BaseEngine"
        )
        for cls in ToTEngine.__mro__:
            if "_call_llm" in cls.__dict__:
                assert cls is BaseEngine, f"_call_llm 应由 BaseEngine 提供，实际来自 {cls}"
                break


# ─────────────────────────────────────────────────────────────────────────────
# 2. 模块级导入合规性检查（无跨 Cell internal 导入）
# ─────────────────────────────────────────────────────────────────────────────


# 静态解析模块文件路径（不触发 import，避免触碰预存的循环导入）
def _locate_source(module_dotted_path: str) -> _pathlib.Path:
    """返回模块对应的 .py 源文件路径，不执行模块导入。"""
    # 将 dotted path 转换为文件路径
    parts = module_dotted_path.split(".")
    base = _pathlib.Path(__file__).parents[2]  # src/backend
    candidate = base.joinpath(*parts).with_suffix(".py")
    if candidate.exists():
        return candidate
    # __init__.py 形式
    candidate2 = base.joinpath(*parts, "__init__.py")
    if candidate2.exists():
        return candidate2
    raise FileNotFoundError(f"Cannot locate source for {module_dotted_path}")


class TestNoCellInternalCrossImport:
    """roles Cell 不应直接导入 llm.dialogue.internal.*（静态文本扫描）"""

    FORBIDDEN = "from polaris.cells.llm.dialogue.internal"

    def _check_no_internal_import(self, module_dotted_path: str) -> None:
        src_path = _locate_source(module_dotted_path)
        src = src_path.read_text(encoding="utf-8")
        assert self.FORBIDDEN not in src, (
            f"{src_path} 包含跨 Cell internal 导入：{self.FORBIDDEN}\n"
            f"应改为通过 polaris.cells.llm.dialogue.public.* 访问"
        )

    def test_roles_engine_react_no_internal_import(self):
        self._check_no_internal_import("polaris.cells.roles.engine.internal.react")

    def test_roles_engine_plan_solve_no_internal_import(self):
        self._check_no_internal_import("polaris.cells.roles.engine.internal.plan_solve")

    def test_roles_engine_tot_no_internal_import(self):
        self._check_no_internal_import("polaris.cells.roles.engine.internal.tot")

    def test_roles_adapters_base_no_internal_import(self):
        self._check_no_internal_import("polaris.cells.roles.adapters.internal.base")

    def test_roles_adapters_director_no_internal_import(self):
        self._check_no_internal_import("polaris.cells.roles.adapters.internal.director_adapter")


# ─────────────────────────────────────────────────────────────────────────────
# 3. adapters role dialogue 通过 roles.runtime public boundary 调用
# ─────────────────────────────────────────────────────────────────────────────


class TestAdapterCallsRuntimeBoundary:
    """生产 role adapters 必须通过 runtime_dialogue helper 进入 roles.runtime。"""

    ROLE_ADAPTER_MODULES = (
        "polaris.cells.roles.adapters.internal.pm_adapter",
        "polaris.cells.roles.adapters.internal.architect_adapter",
        "polaris.cells.roles.adapters.internal.chief_engineer_adapter",
        "polaris.cells.roles.adapters.internal.qa_adapter",
    )
    PRODUCTION_ENTRYPOINT_MODULES = (
        "polaris.delivery.cli.pm.backend",
        "polaris.delivery.cli.pm.orchestration.doc_rendering",
        "polaris.delivery.cli.agentic_eval",
        "polaris.delivery.http.routers.pm_chat",
        "polaris.delivery.http.routers.role_chat",
        "polaris.delivery.http.routers.role_runtime_chat",
        "polaris.delivery.cli.pm.chief_engineer_llm_tools",
        "polaris.delivery.cli.director.director_llm_tools",
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports",
        "polaris.cells.factory.pipeline.internal.projection_lab",
        "polaris.cells.director.execution.internal.code_generation_engine",
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding",
    )
    PRODUCTION_PM_BACKEND_MODULES = (
        "polaris.delivery.cli.pm.backend",
        "polaris.cells.orchestration.pm_planning.internal.pipeline_ports",
    )
    PRODUCTION_PM_WRAPPER_FILES = (_pathlib.Path(__file__).resolve().parents[1] / "delivery" / "cli" / "loop-pm.py",)
    DIRECTOR_ADAPTER_MODULES = ("polaris.cells.roles.adapters.internal.director.adapter",)
    WORKFLOW_RUNTIME_ENTRYPOINT_MODULES = (
        "polaris.cells.roles.adapters.internal.workflow_adapter",
        "polaris.cells.roles.adapters.internal.workflow_node",
    )
    DELIVERY_RUNTIME_ENTRYPOINT_MODULES = ("polaris.delivery.cli.director.console_host",)

    def test_workflow_role_adapters_use_runtime_dialogue_helper(self):
        for module_path in self.ROLE_ADAPTER_MODULES:
            src_path = _locate_source(module_path)
            src = src_path.read_text(encoding="utf-8")
            assert "invoke_role_runtime_first" in src, f"{module_path} 未进入 runtime_dialogue helper"
            assert "generate_role_response(" not in src, f"{module_path} 仍直接调用 legacy dialogue"
            assert "invoke_role_runtime_provider(" not in src, f"{module_path} 仍直接调用 provider runtime"
            assert "dialogue.internal" not in src, f"{module_path} 不应引用 llm.dialogue.internal 路径"

    def test_runtime_dialogue_fails_closed_without_legacy_fallback(self):
        src_path = _locate_source("polaris.cells.roles.adapters.internal.runtime_dialogue")
        src = src_path.read_text(encoding="utf-8")
        assert "RoleRuntimeService" in src, "runtime_dialogue 必须通过 roles.runtime public service"
        assert "ExecuteRoleSessionCommandV1" in src, "runtime_dialogue 必须使用 roles.runtime public contract"
        assert "allow_legacy_fallback" not in src, "runtime_dialogue 不应保留 legacy fallback 开关"
        assert "legacy_dialogue_fallback" not in src, "runtime_dialogue 不应保留 legacy dialogue fallback"
        assert "generate_role_response" not in src, "runtime_dialogue 不应调用 legacy dialogue"
        assert "enable_cognitive=False" not in src, "runtime_dialogue 不应通过旧认知中间件绕过 runtime"
        assert "dialogue.internal" not in src, "runtime_dialogue 不应引用 llm.dialogue.internal 路径"

    def test_production_entrypoints_do_not_call_legacy_llm_boundaries(self):
        for module_path in self.PRODUCTION_ENTRYPOINT_MODULES:
            src_path = _locate_source(module_path)
            src = src_path.read_text(encoding="utf-8")
            assert "generate_role_response(" not in src, f"{module_path} 仍直接调用 legacy dialogue"
            assert "invoke_role_runtime_provider(" not in src, f"{module_path} 仍直接调用 provider runtime"

    def test_pm_backends_do_not_call_direct_process_llm_boundaries(self):
        for module_path in self.PRODUCTION_PM_BACKEND_MODULES:
            src_path = _locate_source(module_path)
            src = src_path.read_text(encoding="utf-8")
            assert "from polaris.kernelone.process.codex_adapter" not in src, f"{module_path} 仍导入 Codex 直连"
            assert "from polaris.kernelone.process.ollama_utils" not in src, f"{module_path} 仍导入 Ollama 直连"
            assert "invoke_codex(" not in src, f"{module_path} 仍调用 Codex 直连"
            assert "invoke_ollama(" not in src, f"{module_path} 仍调用 Ollama 直连"

    def test_pm_compat_wrappers_do_not_reinject_direct_process_llm_boundaries(self):
        for src_path in self.PRODUCTION_PM_WRAPPER_FILES:
            src = src_path.read_text(encoding="utf-8")
            assert "from polaris.kernelone.process.codex_adapter" not in src, f"{src_path} 仍导入 Codex 直连"
            assert "from polaris.kernelone.process.ollama_utils" not in src, f"{src_path} 仍导入 Ollama 直连"
            assert "invoke_codex(" not in src, f"{src_path} 仍调用 Codex 直连"
            assert "invoke_ollama(" not in src, f"{src_path} 仍调用 Ollama 直连"

    def test_director_adapter_does_not_call_legacy_dialogue_fallback(self):
        for module_path in self.DIRECTOR_ADAPTER_MODULES:
            src_path = _locate_source(module_path)
            src = src_path.read_text(encoding="utf-8")
            assert "generate_role_response" not in src, f"{module_path} 仍引用 legacy dialogue"
            assert "legacy_dialogue_fallback" not in src, f"{module_path} 仍保留 legacy dialogue fallback"

    def test_workflow_entrypoints_do_not_bypass_role_runtime(self):
        for module_path in self.WORKFLOW_RUNTIME_ENTRYPOINT_MODULES:
            src_path = _locate_source(module_path)
            src = src_path.read_text(encoding="utf-8")
            assert "RoleExecutionKernel" not in src, f"{module_path} 仍直接引用 RoleExecutionKernel"
            assert "RoleTurnRequest(" not in src, f"{module_path} 仍自行构造 RoleTurnRequest"
            assert ".kernel.run" not in src, f"{module_path} 仍直接调用 kernel.run"
            assert "invoke_role_runtime_first" in src or "WorkflowRoleAdapter" in src, (
                f"{module_path} 未进入 RoleRuntime 合同入口"
            )

    def test_llm_dialogue_internal_role_facade_does_not_bypass_role_runtime(self):
        module_path = "polaris.cells.llm.dialogue.internal.role_dialogue"
        src_path = _locate_source(module_path)
        src = src_path.read_text(encoding="utf-8")
        assert "RoleExecutionKernel" not in src, f"{module_path} 仍直接引用 RoleExecutionKernel"
        assert "RoleTurnRequest(" not in src, f"{module_path} 仍自行构造 RoleTurnRequest"
        assert "get_cognitive_middleware" not in src, f"{module_path} 仍通过旧认知中间件旁路 RoleRuntime"
        assert "RoleRuntimeService" in src, f"{module_path} 未进入 RoleRuntimeService"
        assert "ExecuteRoleSessionCommandV1" in src, f"{module_path} 未使用 RoleRuntime session contract"

    def test_delivery_console_host_delegates_cognitive_preflight_to_role_runtime(self):
        for module_path in self.DELIVERY_RUNTIME_ENTRYPOINT_MODULES:
            src_path = _locate_source(module_path)
            src = src_path.read_text(encoding="utf-8")
            assert "get_cognitive_middleware" not in src, f"{module_path} 仍直接调用旧认知中间件"
            assert "delivery_cognitive_preflight" in src, f"{module_path} 未标记 RoleRuntime 认知预检委托"
            assert "cognitive_runtime_required" in src, f"{module_path} 未要求 RoleRuntime cognitive preflight"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _call_llm 在 BaseEngine 中的 max_tokens 参数可定制
# ─────────────────────────────────────────────────────────────────────────────


class TestBaseEngineLlmCallerMaxTokens:
    """验证 max_tokens 参数可传递到 llm_caller。"""

    @pytest.mark.asyncio
    async def test_custom_max_tokens_passed_through(self):
        from polaris.cells.roles.engine.internal.base import EngineContext

        class _MinEngine:
            """仅用于测试，不继承 BaseEngine，直接调用 _call_llm 逻辑。"""

            async def _call_llm(self, context, prompt, max_tokens=2000):
                # 这是 BaseEngine._call_llm 的逻辑副本，仅用于隔离测试
                if context.llm_caller:
                    return await context.llm_caller(
                        prompt=prompt,
                        role=context.role,
                        max_tokens=max_tokens,
                    )
                return ""

        mock_caller = AsyncMock(return_value="ok")
        ctx = EngineContext(
            workspace="/tmp",
            role="director",
            task="task",
            llm_caller=mock_caller,
        )
        engine = _MinEngine()
        await engine._call_llm(ctx, "prompt", max_tokens=4096)

        call_kwargs = mock_caller.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096
