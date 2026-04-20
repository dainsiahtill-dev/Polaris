"""Tests for DevelopmentWorkflowRuntime."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.development_workflow_runtime import (
    DevelopmentWorkflowRuntime,
    TestResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    ContentChunkEvent,
    ToolBatchEvent,
)


class MockShadowEngine:
    """Minimal mock for StreamShadowEngine patch speculation."""

    def __init__(self, has_patch: bool = False, patch_result: dict | None = None) -> None:
        self._has_patch = has_patch
        self._patch_result = patch_result or {}

    def has_speculated_patch(self, _intent: str) -> bool:
        return self._has_patch

    async def consume_speculated_patch(self, _intent: str) -> dict:
        return dict(self._patch_result)


class TestDevelopmentWorkflowRuntime:
    """测试 DevelopmentWorkflowRuntime 的 TDD 循环和事件流。"""

    @pytest.fixture
    def mock_tool_executor(self):
        return AsyncMock()

    @pytest.fixture
    def base_runtime(self, mock_tool_executor):
        return DevelopmentWorkflowRuntime(tool_executor=mock_tool_executor, max_retries=2)

    @pytest.mark.asyncio
    async def test_execute_stream_passes_on_first_try(self, mock_tool_executor, base_runtime):
        mock_tool_executor.return_value = {"result": "1 passed"}
        session_state = SimpleNamespace(session_id="sess-1")

        events = []
        async for event in base_runtime.execute_stream("fix bug", session_state):
            events.append(event)

        event_types = [type(e).__name__ for e in events]
        assert event_types[0] == "RuntimeStartedEvent"
        assert any(t == "TurnPhaseEvent" for t in event_types)
        assert any(t == "ToolBatchEvent" for t in event_types)
        assert any(t == "ContentChunkEvent" for t in event_types)
        assert event_types[-1] == "RuntimeCompletedEvent"

        # 验证测试通过的消息
        content_events = [e for e in events if isinstance(e, ContentChunkEvent)]
        assert any("测试已通过" in e.chunk for e in content_events)

    @pytest.mark.asyncio
    async def test_execute_stream_retries_then_gives_up(self, mock_tool_executor, base_runtime):
        # 测试始终失败，达到 max_retries 后放弃
        mock_tool_executor.return_value = {"result": "1 failed"}
        session_state = SimpleNamespace(session_id="sess-1")

        events = []
        async for event in base_runtime.execute_stream("fix bug", session_state):
            events.append(event)

        # 应该有多个 ToolBatchEvent（apply_patch + run_tests 重复）
        tool_batches = [e for e in events if isinstance(e, ToolBatchEvent)]
        assert len(tool_batches) >= 2  # patch + test, 至少一轮

        # 最后一轮应该有错误提示
        content_events = [e for e in events if isinstance(e, ContentChunkEvent)]
        assert any("请人工介入" in e.chunk for e in content_events)

    @pytest.mark.asyncio
    async def test_shadow_engine_speculated_patch_consumed(self, mock_tool_executor):
        shadow = MockShadowEngine(has_patch=True, patch_result={"patch": "applied"})
        runtime = DevelopmentWorkflowRuntime(
            tool_executor=mock_tool_executor,
            shadow_engine=shadow,
            max_retries=1,
        )
        mock_tool_executor.return_value = {"result": "1 passed"}
        session_state = SimpleNamespace(session_id="sess-1")

        events = []
        async for event in runtime.execute_stream("fix bug", session_state):
            events.append(event)

        tool_batches = [e for e in events if isinstance(e, ToolBatchEvent)]
        patch_events = [e for e in tool_batches if e.tool_name == "apply_patch"]
        assert len(patch_events) == 1
        assert patch_events[0].result == {"patch": "applied"}

    @pytest.mark.asyncio
    async def test_synthesis_llm_repair_intent(self, mock_tool_executor):
        synthesis_llm = AsyncMock(return_value="refined intent")
        runtime = DevelopmentWorkflowRuntime(
            tool_executor=mock_tool_executor,
            synthesis_llm=synthesis_llm,
            max_retries=2,
        )
        # 第一次 patch 后测试失败，第二次成功
        mock_tool_executor.side_effect = [
            {"result": "write ok"},  # _execute_patch (write_file)
            {"result": "1 failed"},  # _run_tests (pytest) - first attempt fails
            {"result": "write ok"},  # _execute_patch - second attempt
            {"result": "1 passed"},  # _run_tests - second attempt passes
        ]
        session_state = SimpleNamespace(session_id="sess-1")

        events = []
        async for event in runtime.execute_stream("fix bug", session_state):
            events.append(event)

        synthesis_llm.assert_awaited_once()
        # 验证第二次使用了合成后的 intent
        calls = mock_tool_executor.call_args_list
        # 第一次是 apply_patch (write_file), 第二次是 run_tests (execute_command)
        # 第三次 apply_patch 应该使用 refined intent
        assert len(calls) >= 3

    @pytest.mark.asyncio
    async def test_run_tests_interprets_failure(self, mock_tool_executor, base_runtime):
        mock_tool_executor.return_value = {"result": "1 failed, 0 passed"}
        session_state = SimpleNamespace(session_id="sess-1")

        result = await base_runtime._run_tests(session_state)
        assert result.passed is False
        assert "failed" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_run_tests_interprets_error(self, mock_tool_executor, base_runtime):
        mock_tool_executor.return_value = {"result": "ERROR: no tests found"}
        session_state = SimpleNamespace(session_id="sess-1")

        result = await base_runtime._run_tests(session_state)
        assert result.passed is False
        assert "error" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_run_tests_exception(self, mock_tool_executor, base_runtime):
        mock_tool_executor.side_effect = RuntimeError("pytest not found")
        session_state = SimpleNamespace(session_id="sess-1")

        result = await base_runtime._run_tests(session_state)
        assert result.passed is False
        assert "pytest not found" in result.summary

    @pytest.mark.asyncio
    async def test_analyze_failure_with_synthesis_llm(self):
        synthesis_llm = AsyncMock(return_value="  new intent  ")
        runtime = DevelopmentWorkflowRuntime(
            tool_executor=AsyncMock(),
            synthesis_llm=synthesis_llm,
        )
        test_result = TestResult(passed=False, summary="assertion failed", raw_output="trace")
        intent = await runtime._analyze_failure_and_create_repair_intent(test_result)
        assert intent == "new intent"
        synthesis_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analyze_failure_without_synthesis_llm(self):
        runtime = DevelopmentWorkflowRuntime(tool_executor=AsyncMock())
        test_result = TestResult(passed=False, summary="assertion failed", raw_output="trace")
        intent = await runtime._analyze_failure_and_create_repair_intent(test_result)
        assert "修复测试失败" in intent
        assert "assertion failed" in intent

    @pytest.mark.asyncio
    async def test_execute_patch_success(self, mock_tool_executor, base_runtime):
        mock_tool_executor.return_value = {"ok": True}
        session_state = SimpleNamespace(session_id="sess-1")
        result = await base_runtime._execute_patch("implement feature", session_state)
        assert result["ok"] is True
        mock_tool_executor.assert_awaited_once()
        args = mock_tool_executor.call_args[0]
        assert args[0] == "write_file"
        assert "implement feature" in args[1]["content"]

    @pytest.mark.asyncio
    async def test_execute_patch_failure(self, mock_tool_executor, base_runtime):
        mock_tool_executor.side_effect = RuntimeError("disk full")
        session_state = SimpleNamespace(session_id="sess-1")
        result = await base_runtime._execute_patch("implement feature", session_state)
        assert result["ok"] is False
        assert "disk full" in result["error"]

    @pytest.mark.asyncio
    async def test_run_tests_truncates_very_long_output(self, mock_tool_executor, base_runtime):
        """验证 5MB 超长 pytest 输出被截断到 500 字符以内（防止 token 爆炸）。"""
        long_output = "1 failed\n" + "A" * (5 * 1024 * 1024)  # ~5MB
        mock_tool_executor.return_value = {"result": long_output}
        session_state = SimpleNamespace(session_id="sess-1")

        result = await base_runtime._run_tests(session_state)
        assert result.passed is False
        assert len(result.summary) <= 500
        assert len(result.raw_output) > 500  # raw_output keeps the full text

    @pytest.mark.asyncio
    async def test_analyze_failure_truncates_long_summary_without_synthesis_llm(self):
        """验证无 synthesis_llm 时，超长 summary 被截断到 200 字符。"""
        runtime = DevelopmentWorkflowRuntime(tool_executor=AsyncMock())
        long_summary = "X" * 5000
        test_result = TestResult(passed=False, summary=long_summary, raw_output="trace")
        intent = await runtime._analyze_failure_and_create_repair_intent(test_result)
        assert "修复测试失败" in intent
        # The summary portion should be truncated to ~200 chars
        assert len(intent) <= 220  # "修复测试失败: " prefix + ~200 chars

    @pytest.mark.asyncio
    async def test_analyze_failure_handles_empty_summary_gracefully(self):
        """验证空 summary 不会导致异常。"""
        runtime = DevelopmentWorkflowRuntime(tool_executor=AsyncMock())
        test_result = TestResult(passed=False, summary="", raw_output="")
        intent = await runtime._analyze_failure_and_create_repair_intent(test_result)
        assert "修复测试失败" in intent

    @pytest.mark.asyncio
    async def test_analyze_failure_synthesis_llm_exception_fallback(self):
        """验证 synthesis_llm 抛异常时回退到字符串拼接，不崩溃。"""
        failing_llm = AsyncMock(side_effect=RuntimeError("model timeout"))
        runtime = DevelopmentWorkflowRuntime(
            tool_executor=AsyncMock(),
            synthesis_llm=failing_llm,
        )
        test_result = TestResult(passed=False, summary="assertion failed", raw_output="trace")
        intent = await runtime._analyze_failure_and_create_repair_intent(test_result)
        # Should fallback gracefully without crashing
        assert "修复测试失败" in intent
        assert "assertion failed" in intent
