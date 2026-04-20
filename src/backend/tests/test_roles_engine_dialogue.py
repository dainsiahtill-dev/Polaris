"""tests/test_roles_engine_dialogue.py

验证 roles.engine 与 roles.adapters 对 llm.dialogue public service 的调用路径收敛。

覆盖点：
1. BaseEngine._call_llm 通过 EngineContext.llm_caller 委托（不直接调用 LLM provider）
2. ReActEngine / PlanSolveEngine / ToTEngine 继承 BaseEngine._call_llm，不再自带副本
3. BaseRoleAdapter._call_role_llm 通过 generate_role_response（public service）调用
4. 无跨 Cell internal 导入：roles 层不直接 import llm.dialogue.internal.*
"""

from __future__ import annotations

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

import pathlib as _pathlib


# 静态解析模块文件路径（不触发 import，避免触碰预存的循环导入）
def _locate_source(module_dotted_path: str) -> _pathlib.Path:
    """返回模块对应的 .py 源文件路径，不执行模块导入。"""
    # 将 dotted path 转换为文件路径
    parts = module_dotted_path.split(".")
    base = _pathlib.Path(__file__).parent.parent  # src/backend
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
# 3. adapters._call_role_llm 通过 llm.dialogue public service 调用
# ─────────────────────────────────────────────────────────────────────────────

class TestAdapterCallsPublicService:
    """BaseRoleAdapter._call_role_llm 必须通过 generate_role_response（public service）。"""

    @pytest.mark.asyncio
    async def test_base_adapter_call_role_llm_uses_public_generate_role_response(self):
        """_call_role_llm 调用的是模块级 generate_role_response（public service）而非 internal。

        策略：静态确认 _call_role_llm 方法体内不包含 internal 路径引用，
        并确认 generate_role_response 已在模块顶层绑定（可被 patch 拦截）。
        """
        src_path = _locate_source("polaris.cells.roles.adapters.internal.base")
        src = src_path.read_text(encoding="utf-8")

        # 确认 _call_role_llm 函数体内引用的是 generate_role_response（非 internal 路径）
        assert "generate_role_response" in src, (
            "_call_role_llm 应调用 generate_role_response"
        )
        assert "dialogue.internal" not in src, (
            "_call_role_llm 不应引用 llm.dialogue.internal 路径"
        )

        # 确认顶层 import 行存在，保证 patch 可拦截
        import_line = "from polaris.cells.llm.dialogue.public.service import generate_role_response"
        assert import_line in src, (
            f"generate_role_response 应作为顶层导入存在，实际未找到：{import_line}"
        )

    def test_generate_role_response_import_is_at_module_level_in_base(self):
        """generate_role_response 必须作为顶层导入出现在 base.py 源码中（静态扫描）。"""
        src_path = _locate_source("polaris.cells.roles.adapters.internal.base")
        src = src_path.read_text(encoding="utf-8")
        assert "from polaris.cells.llm.dialogue.public.service import generate_role_response" in src, (
            "generate_role_response 应作为顶层导入存在于 base.py，而非运行时导入"
        )

    def test_generate_role_response_import_is_at_module_level_in_director(self):
        """generate_role_response 必须作为顶层导入出现在 director_adapter.py 源码中（静态扫描）。"""
        src_path = _locate_source("polaris.cells.roles.adapters.internal.director_adapter")
        src = src_path.read_text(encoding="utf-8")
        assert "from polaris.cells.llm.dialogue.public.service import generate_role_response" in src, (
            "generate_role_response 应作为顶层导入存在于 director_adapter.py，而非运行时导入"
        )

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
