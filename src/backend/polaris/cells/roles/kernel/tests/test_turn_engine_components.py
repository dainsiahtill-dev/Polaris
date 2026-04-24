"""Tests for TurnEngine sub-components without existing coverage.

验证：
1. TurnQuotaManager 的配额检查与记录
2. ResultBuilder 的元数据构造
3. ContextPruner 的幻觉循环检测与剪枝
4. SingleToolExecutor 的工具执行与错误边界
5. AssistantTurnArtifacts 与 _BracketToolWrapperFilter
6. Utils 函数的独立行为
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.roles.kernel.internal.turn_engine.artifacts import (
    AssistantTurnArtifacts,
    _BracketToolWrapperFilter,
)
from polaris.cells.roles.kernel.internal.turn_engine.config import (
    SafetyState,
    TurnEngineConfig,
)
from polaris.cells.roles.kernel.internal.turn_engine.context_pruner import (
    ContextPruner,
)
from polaris.cells.roles.kernel.internal.turn_engine.quota_manager import (
    TurnQuotaManager,
)
from polaris.cells.roles.kernel.internal.turn_engine.result_builder import (
    ResultBuilder,
)
from polaris.cells.roles.kernel.internal.turn_engine.tool_executor import (
    SingleToolExecutor,
)
from polaris.cells.roles.kernel.internal.turn_engine.utils import (
    dedupe_parsed_tool_calls,
    merge_stream_thinking,
    normalize_stream_tool_call_payload,
    resolve_empty_visible_output_error,
    tool_call_signature,
    tool_call_signature_from_parsed,
    visible_delta,
)

# ============ TurnQuotaManager Tests ============


class TestTurnQuotaManagerInit:
    """测试 TurnQuotaManager 初始化."""

    def test_init_lazy_loads_manager(self) -> None:
        """初始化时不应立即加载全局 quota manager."""
        qm = TurnQuotaManager()
        assert qm._manager is None


class TestTurnQuotaManagerBuildAgentId:
    """测试 build_agent_id."""

    def test_basic_agent_id(self) -> None:
        """基本 agent ID 格式."""
        result = TurnQuotaManager.build_agent_id("pm", "/workspace")
        assert result == "pm@/workspace"

    def test_agent_id_with_run_id(self) -> None:
        """带 run_id 的 agent ID."""
        result = TurnQuotaManager.build_agent_id("pm", "/workspace", "run_123")
        assert result == "pm@/workspace@run_123"


class TestTurnQuotaManagerCheckBeforeTurn:
    """测试 check_before_turn."""

    def test_check_allows_when_manager_fails(self) -> None:
        """quota manager 异常时应允许 turn 继续（fail-open）."""
        qm = TurnQuotaManager()
        with patch.object(qm, "_get_manager", side_effect=RuntimeError("boom")):
            allowed, reason = qm.check_before_turn("pm@ws")
            assert allowed is True
            assert reason == ""


class TestTurnQuotaManagerRecordTurn:
    """测试 record_turn."""

    def test_record_turn_silences_errors(self) -> None:
        """记录异常时不应抛出."""
        qm = TurnQuotaManager()
        with patch.object(qm, "_get_manager", side_effect=RuntimeError("boom")):
            qm.record_turn("pm@ws", wall_time_delta=1.0)


class TestTurnQuotaManagerAcquireRelease:
    """测试并发工具槽位获取与释放."""

    def test_acquire_allows_when_manager_fails(self) -> None:
        """quota manager 异常时应允许执行（fail-open）."""
        qm = TurnQuotaManager()
        with patch.object(qm, "_get_manager", side_effect=RuntimeError("boom")):
            assert qm.acquire_concurrent_tool("pm@ws") is True

    def test_release_silences_errors(self) -> None:
        """释放异常时不应抛出."""
        qm = TurnQuotaManager()
        with patch.object(qm, "_get_manager", side_effect=RuntimeError("boom")):
            qm.release_concurrent_tool("pm@ws")


# ============ TurnEngineConfig Tests ============


class TestTurnEngineConfig:
    """测试 TurnEngineConfig."""

    def test_default_values(self) -> None:
        """默认值应符合预期."""
        config = TurnEngineConfig()
        assert config.max_turns == 64
        assert config.max_total_tool_calls == 64
        assert config.max_stall_cycles == 2
        assert config.max_wall_time_seconds == 900
        assert config.enable_streaming is True

    def test_from_env_with_defaults(self) -> None:
        """无环境变量时应返回默认值."""
        config = TurnEngineConfig.from_env()
        assert config.max_turns == 64

    def test_from_env_respects_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量应覆盖默认值."""
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", "32")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", "1")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS", "300")
        monkeypatch.setenv("KERNELONE_TURN_ENGINE_STREAM", "false")
        config = TurnEngineConfig.from_env()
        assert config.max_turns == 32
        assert config.max_stall_cycles == 1
        assert config.max_wall_time_seconds == 300
        assert config.enable_streaming is False

    def test_from_env_clamps_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量值应被约束在合理范围内."""
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", "1000")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", "20")
        config = TurnEngineConfig.from_env()
        assert config.max_turns == 512
        assert config.max_stall_cycles == 16


# ============ SafetyState Tests ============


class TestSafetyState:
    """测试 SafetyState."""

    def test_default_values(self) -> None:
        """默认值应符合预期."""
        state = SafetyState()
        assert state.total_tool_calls == 0
        assert state.stall_count == 0
        assert state.last_cycle_signature == ""

    def test_check_passes_within_budget(self) -> None:
        """未超过阈值时应返回 None."""
        state = SafetyState()
        config = TurnEngineConfig(max_stall_cycles=2)
        assert state.check(config) is None

    def test_check_fails_on_stall(self) -> None:
        """stall 超过阈值时应返回原因."""
        state = SafetyState(stall_count=3)
        config = TurnEngineConfig(max_stall_cycles=2)
        result = state.check(config)
        assert result is not None
        assert "stalled" in result

    def test_update_signature_detects_stall(self) -> None:
        """相同签名应增加 stall_count."""
        state = SafetyState()
        calls = [Mock(tool="read_file")]
        results = [{"tool": "read_file"}]
        state.update_signature(calls, results)
        first_sig = state.last_cycle_signature
        state.update_signature(calls, results)
        assert state.stall_count == 1
        assert state.last_cycle_signature == first_sig

    def test_update_signature_resets_on_change(self) -> None:
        """不同签名应重置 stall_count."""
        state = SafetyState()
        state.update_signature([Mock(tool="read_file")], [{"tool": "read_file"}])
        state.update_signature([Mock(tool="write_file")], [{"tool": "write_file"}])
        assert state.stall_count == 0


# ============ ResultBuilder Tests ============


class TestResultBuilderBuildMetadata:
    """测试 ResultBuilder.build_turn_result_metadata."""

    def test_raises_when_profile_none(self) -> None:
        """profile 为 None 时应抛出 ValueError."""
        builder = ResultBuilder()
        state = Mock()
        state.turn_id = "turn_1"
        state.version = 1
        request = Mock()
        request.metadata = {}
        request.context_override = None
        request.run_id = "run_1"
        request.task_id = "task_1"

        with pytest.raises(ValueError, match="profile is None"):
            builder.build_turn_result_metadata(state=state, request=request, role="director", profile=None)

    def test_builds_basic_metadata(self) -> None:
        """基本元数据构造."""
        builder = ResultBuilder()
        state = Mock()
        state.turn_id = "turn_1"
        state.version = 1
        request = Mock()
        request.metadata = {"session_id": "sess_1"}
        request.context_override = None
        request.run_id = "run_1"
        request.task_id = "task_1"
        profile = Mock()
        profile.model = "claude"
        profile.provider_id = "anthropic"
        profile.version = "1.0"

        with patch("polaris.kernelone.llm.engine.model_catalog.ModelCatalog") as mock_catalog:
            mock_spec = Mock()
            mock_spec.max_context_tokens = 200000
            mock_catalog.return_value.resolve.return_value = mock_spec
            result = builder.build_turn_result_metadata(state=state, request=request, role="director", profile=profile)

        assert result["turn_id"] == "turn_1"
        assert result["model"] == "claude"
        assert result["provider_id"] == "anthropic"


# ============ ContextPruner Tests ============


class TestContextPrunerInit:
    """测试 ContextPruner 初始化."""

    def test_init_empty_pending(self) -> None:
        """初始化时 pending_loop_break 应为空."""
        pruner = ContextPruner()
        assert pruner._pending_loop_break == {}


class TestContextPrunerResetTurn:
    """测试 reset_turn."""

    def test_reset_clears_pending(self) -> None:
        """reset_turn 应清空 pending 状态."""
        pruner = ContextPruner()
        pruner._pending_loop_break[("tool", "fp")] = "suggestion"
        pruner.reset_turn()
        assert pruner._pending_loop_break == {}


class TestContextPrunerInjectLoopBreakSignal:
    """测试 inject_loop_break_signal."""

    def test_inject_records_signal(self) -> None:
        """loop_break=True 时应记录信号."""
        pruner = ContextPruner()
        pruner.inject_loop_break_signal("precision_edit", {"loop_break": True})
        assert ("precision_edit", "") in pruner._pending_loop_break

    def test_inject_ignores_no_loop_break(self) -> None:
        """loop_break=False 时不应记录."""
        pruner = ContextPruner()
        pruner.inject_loop_break_signal("read_file", {"loop_break": False})
        assert pruner._pending_loop_break == {}

    def test_inject_with_fingerprint(self) -> None:
        """带 fingerprint 时应按 (tool, fingerprint) 记录."""
        pruner = ContextPruner()
        pruner.inject_loop_break_signal("precision_edit", {"loop_break": True}, fingerprint="search_term")
        assert ("precision_edit", "search_term") in pruner._pending_loop_break


class TestContextPrunerCheckAndHandleLoopBreak:
    """测试 check_and_handle_loop_break."""

    def test_no_pending_returns_none(self) -> None:
        """无 pending 信号时应返回 None."""
        pruner = ContextPruner()
        controller = Mock()
        controller._history = []
        result = pruner.check_and_handle_loop_break("read_file", controller)
        assert result is None

    def test_pending_triggers_prune(self) -> None:
        """有 pending 信号时应触发剪枝."""
        pruner = ContextPruner()
        pruner._pending_loop_break[("precision_edit", "")] = "suggestion"
        controller = Mock()
        controller._history = []
        result = pruner.check_and_handle_loop_break("precision_edit", controller)
        assert result is not None
        assert "suggestion" in result["content"] or "HALLUCINATION_LOOP" in result["content"]

    def test_pending_with_fingerprint_match(self) -> None:
        """fingerprint 匹配时应触发."""
        pruner = ContextPruner()
        pruner._pending_loop_break[("precision_edit", "fp1")] = "suggestion"
        controller = Mock()
        controller._history = []
        result = pruner.check_and_handle_loop_break("precision_edit", controller, fingerprint="fp1")
        assert result is not None

    def test_pending_with_fingerprint_mismatch(self) -> None:
        """fingerprint 不匹配时不应触发."""
        pruner = ContextPruner()
        pruner._pending_loop_break[("precision_edit", "fp1")] = "suggestion"
        controller = Mock()
        controller._history = []
        result = pruner.check_and_handle_loop_break("precision_edit", controller, fingerprint="fp2")
        assert result is None


class TestContextPrunerHandleBlockedToolPruning:
    """测试 handle_blocked_tool_pruning."""

    def test_blocked_tool_triggers_pruning(self) -> None:
        """blocked=True 时应记录 pending."""
        pruner = ContextPruner()
        controller = Mock()
        pruner.handle_blocked_tool_pruning("precision_edit", controller, {"blocked": True})
        assert ("precision_edit", "") in pruner._pending_loop_break

    def test_authorization_failure_ignored(self) -> None:
        """授权失败不应触发剪枝."""
        pruner = ContextPruner()
        controller = Mock()
        pruner.handle_blocked_tool_pruning("write_file", controller, {"blocked": True, "authorization_failure": True})
        assert pruner._pending_loop_break == {}


# ============ SingleToolExecutor Tests ============


class TestSingleToolExecutorInit:
    """测试 SingleToolExecutor 初始化."""

    def test_init_with_default_quota_manager(self) -> None:
        """默认应创建 TurnQuotaManager."""
        kernel = Mock()
        executor = SingleToolExecutor(kernel)
        assert isinstance(executor._quota_manager, TurnQuotaManager)

    def test_init_with_injected_quota_manager(self) -> None:
        """注入的 quota_manager 应被使用."""
        kernel = Mock()
        qm = Mock()
        executor = SingleToolExecutor(kernel, quota_manager=qm)
        assert executor._quota_manager is qm


@pytest.mark.asyncio
class TestSingleToolExecutorExecute:
    """测试 SingleToolExecutor.execute."""

    async def test_missing_tool_name(self) -> None:
        """缺少 tool_name 时应返回错误."""
        kernel = Mock()
        executor = SingleToolExecutor(kernel)
        result = await executor.execute(profile=Mock(), request=Mock(), call={})
        assert result["success"] is False
        assert "MISSING_TOOL_NAME" in result["error"]

    async def test_missing_tool_name_from_object(self) -> None:
        """从对象获取时缺少 tool_name 也应返回错误."""
        kernel = Mock()
        executor = SingleToolExecutor(kernel)
        call = Mock()
        call.tool = None
        call.args = {}
        result = await executor.execute(profile=Mock(), request=Mock(), call=call)
        assert result["success"] is False
        assert "MISSING_TOOL_NAME" in result["error"]

    async def test_quota_exceeded(self) -> None:
        """并发配额超限时应返回错误."""
        kernel = Mock()
        kernel.workspace = "/ws"
        kernel._cached_tool_gateway = None
        qm = Mock()
        qm.build_agent_id.return_value = "agent_1"
        qm.acquire_concurrent_tool.return_value = False
        executor = SingleToolExecutor(kernel, quota_manager=qm)

        profile = Mock()
        profile.role_id = "director"
        request = Mock()
        request.run_id = "run_1"

        with patch("polaris.cells.roles.kernel.internal.kernel.tool_executor.KernelToolExecutor") as mock_kte:
            gateway = Mock()
            gateway.check_tool_permission.return_value = (True, "")
            gateway.set_iteration = Mock()
            mock_kte.return_value.create_gateway.return_value = gateway

            result = await executor.execute(
                profile=profile, request=request, call={"tool": "read_file", "args": {"path": "a.py"}}
            )
        assert result["success"] is False
        assert "CONCURRENT_TOOL_QUOTA_EXCEEDED" in result["error"]

    async def test_successful_execution(self) -> None:
        """正常执行应返回 kernel 结果."""
        kernel = Mock()
        kernel.workspace = "/ws"
        kernel._execute_single_tool = AsyncMock(return_value={"success": True, "content": "hello"})
        kernel._cached_tool_gateway = None

        qm = Mock()
        qm.build_agent_id.return_value = "agent_1"
        qm.acquire_concurrent_tool.return_value = True
        executor = SingleToolExecutor(kernel, quota_manager=qm)

        profile = Mock()
        profile.role_id = "director"
        request = Mock()
        request.run_id = "run_1"

        with patch("polaris.cells.roles.kernel.internal.kernel.tool_executor.KernelToolExecutor") as mock_kte:
            gateway = Mock()
            gateway.check_tool_permission.return_value = (True, "")
            gateway.set_iteration = Mock()
            mock_kte.return_value.create_gateway.return_value = gateway

            result = await executor.execute(
                profile=profile, request=request, call={"tool": "read_file", "args": {"path": "a.py"}}
            )

        assert result["success"] is True
        assert result["content"] == "hello"
        qm.release_concurrent_tool.assert_called_once_with("agent_1")

    async def test_tool_blocked_by_policy(self) -> None:
        """工具被策略拦截时应返回 TOOL_BLOCKED."""
        kernel = Mock()
        kernel.workspace = "/ws"
        kernel._cached_tool_gateway = None

        qm = Mock()
        qm.build_agent_id.return_value = "agent_1"
        qm.acquire_concurrent_tool.return_value = True
        executor = SingleToolExecutor(kernel, quota_manager=qm)

        profile = Mock()
        profile.role_id = "director"
        request = Mock()
        request.run_id = "run_1"

        with patch("polaris.cells.roles.kernel.internal.kernel.tool_executor.KernelToolExecutor") as mock_kte:
            gateway = Mock()
            gateway.check_tool_permission.return_value = (False, "not in whitelist")
            gateway.set_iteration = Mock()
            mock_kte.return_value.create_gateway.return_value = gateway

            result = await executor.execute(
                profile=profile, request=request, call={"tool": "dangerous_tool", "args": {}}
            )

        assert result["success"] is False
        assert "TOOL_BLOCKED" in result["error"]
        assert result["authorization_failure"] is True
        # When gateway blocks before quota check, release is not called
        qm.release_concurrent_tool.assert_not_called()


# ============ AssistantTurnArtifacts Tests ============


class TestAssistantTurnArtifacts:
    """测试 AssistantTurnArtifacts."""

    def test_basic_creation(self) -> None:
        """基本构造."""
        artifacts = AssistantTurnArtifacts(raw_content="raw", clean_content="clean", thinking="think")
        assert artifacts.raw_content == "raw"
        assert artifacts.clean_content == "clean"
        assert artifacts.thinking == "think"
        assert artifacts.native_tool_calls == ()
        assert artifacts.native_tool_provider == "auto"


# ============ _BracketToolWrapperFilter Tests ============


class TestBracketToolWrapperFilter:
    """测试 _BracketToolWrapperFilter."""

    def test_empty_feed(self) -> None:
        """空输入应返回空字符串."""
        filt = _BracketToolWrapperFilter()
        assert filt.feed("") == ""

    def test_plain_text_passes_through(self) -> None:
        """普通文本应直接通过."""
        filt = _BracketToolWrapperFilter()
        assert filt.feed("Hello world") == "Hello world"

    def test_strips_tool_call_wrapper(self) -> None:
        """应过滤 [tool_call]...[/tool_call]."""
        filt = _BracketToolWrapperFilter()
        result = filt.feed("Before [tool_call] content [/tool_call] After")
        assert "Before" in result
        assert "After" in result
        assert "[tool_call]" not in result
        assert "[/tool_call]" not in result

    def test_strips_tool_result_wrapper(self) -> None:
        """应过滤 [tool_result]...[/tool_result]."""
        filt = _BracketToolWrapperFilter()
        result = filt.feed("[tool_result] some result [/tool_result]")
        assert "[tool_result]" not in result
        assert "[/tool_result]" not in result

    def test_cross_chunk_wrapper(self) -> None:
        """跨 chunk 的 wrapper 应正确处理 — 文本在 wrapper 开始前被输出."""
        filt = _BracketToolWrapperFilter()
        result1 = filt.feed("Before [tool")
        # "Before " is emitted before the '[' is seen as potential wrapper start
        result2 = filt.feed("_call] content [/tool_call] After")
        # The wrapper content is stripped, " After" remains
        combined = result1 + result2
        assert "After" in combined
        assert "[tool_call]" not in combined

    def test_flush_returns_remaining(self) -> None:
        """flush 应返回剩余缓冲（feed 已将文本输出）."""
        filt = _BracketToolWrapperFilter()
        result = filt.feed("remaining text")
        # feed outputs plain text immediately since no '[' is present
        assert result == "remaining text"
        assert filt.flush() == ""
        assert filt._buffer == ""

    def test_flush_inside_wrapper_returns_empty(self) -> None:
        """flush 时若在 wrapper 内部应返回空."""
        filt = _BracketToolWrapperFilter()
        filt.feed("[tool_call] inside")
        filt._inside_wrapper = True
        assert filt.flush() == ""


# ============ Utils Tests ============


class TestToolCallSignature:
    """测试 tool_call_signature."""

    def test_basic_signature(self) -> None:
        """基本签名格式."""
        sig = tool_call_signature("read_file", {"path": "main.py"})
        assert sig.startswith("read_file::")
        assert "path" in sig

    def test_none_args(self) -> None:
        """None args 应视为空 dict."""
        sig = tool_call_signature("read_file", None)
        assert sig == "read_file::{}"


class TestToolCallSignatureFromParsed:
    """测试 tool_call_signature_from_parsed."""

    def test_from_parsed_call(self) -> None:
        """从解析后的调用对象构造签名."""
        call = Mock()
        call.tool = "read_file"
        call.args = {"path": "main.py"}
        sig = tool_call_signature_from_parsed(call)
        assert "read_file" in sig

    def test_fallback_to_name(self) -> None:
        """无 tool 时应回退到 name."""
        call = Mock()
        call.tool = None
        call.name = "write_file"
        call.args = {}
        sig = tool_call_signature_from_parsed(call)
        assert "write_file" in sig


class TestDedupeParsedToolCalls:
    """测试 dedupe_parsed_tool_calls."""

    def test_removes_duplicates(self) -> None:
        """重复调用应被去重."""
        call1 = Mock()
        call1.tool = "read_file"
        call1.args = {"path": "main.py"}
        call2 = Mock()
        call2.tool = "read_file"
        call2.args = {"path": "main.py"}
        result = dedupe_parsed_tool_calls([call1, call2])
        assert len(result) == 1

    def test_keeps_unique(self) -> None:
        """不同调用应保留."""
        call1 = Mock()
        call1.tool = "read_file"
        call1.args = {"path": "a.py"}
        call2 = Mock()
        call2.tool = "read_file"
        call2.args = {"path": "b.py"}
        result = dedupe_parsed_tool_calls([call1, call2])
        assert len(result) == 2


class TestResolveEmptyVisibleOutputError:
    """测试 resolve_empty_visible_output_error."""

    def test_none_when_has_clean_content(self) -> None:
        """有 clean_content 时应返回 None."""
        artifacts = AssistantTurnArtifacts(raw_content="raw", clean_content="visible")
        assert resolve_empty_visible_output_error(artifacts, []) is None

    def test_none_when_has_native_tool_calls(self) -> None:
        """有 native_tool_calls 时应返回 None."""
        artifacts = AssistantTurnArtifacts(raw_content="raw", clean_content="", native_tool_calls=({"id": "1"},))
        assert resolve_empty_visible_output_error(artifacts, []) is None

    def test_error_when_empty(self) -> None:
        """完全为空时应返回错误."""
        artifacts = AssistantTurnArtifacts(raw_content="", clean_content="")
        result = resolve_empty_visible_output_error(artifacts, [])
        assert result is not None
        assert "assistant_visible_output_empty" in result

    def test_thinking_only_flagged(self) -> None:
        """只有 thinking 时应返回特定错误."""
        artifacts = AssistantTurnArtifacts(raw_content="", clean_content="", thinking="thinking...")
        result = resolve_empty_visible_output_error(artifacts, [])
        assert result is not None
        assert "thinking-only" in result


class TestNormalizeStreamToolCallPayload:
    """测试 normalize_stream_tool_call_payload."""

    def test_basic_normalization(self) -> None:
        """基本归一化."""
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="read_file", tool_args={"path": "main.py"}, call_id="call_1"
        )
        assert payload is not None
        assert payload["type"] == "function"
        assert payload["function"]["name"] == "read_file"
        assert provider == "openai"

    def test_empty_tool_name_returns_none(self) -> None:
        """空 tool_name 应返回 None payload."""
        payload, provider = normalize_stream_tool_call_payload(tool_name="", tool_args={}, call_id="call_1")
        assert payload is None
        assert provider == "auto"

    def test_anthropic_native_format(self) -> None:
        """Anthropic 原生格式应被识别."""
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="read_file",
            tool_args={},
            call_id="call_1",
            metadata={"native_tool_call": {"type": "tool_use", "name": "read_file", "input": {}}},
        )
        assert payload is not None
        assert provider == "anthropic"


class TestMergeStreamThinking:
    """测试 merge_stream_thinking."""

    def test_both_empty_returns_none(self) -> None:
        """两者都为空应返回 None."""
        assert merge_stream_thinking(parsed_thinking=None, streamed_thinking_parts=[]) is None

    def test_only_parsed(self) -> None:
        """只有 parsed 时应返回 parsed."""
        assert merge_stream_thinking(parsed_thinking="think", streamed_thinking_parts=[]) == "think"

    def test_only_streamed(self) -> None:
        """只有 streamed 时应返回 streamed."""
        assert merge_stream_thinking(parsed_thinking=None, streamed_thinking_parts=["stream"]) == "stream"

    def test_merged_when_different(self) -> None:
        """不同时应合并."""
        result = merge_stream_thinking(parsed_thinking="parsed", streamed_thinking_parts=["streamed"])
        assert result is not None
        assert "streamed" in result
        assert "parsed" in result

    def test_streamed_in_parsed_returns_parsed(self) -> None:
        """streamed 是 parsed 子串时应返回 parsed."""
        assert merge_stream_thinking(parsed_thinking="long text", streamed_thinking_parts=["text"]) == "long text"


class TestVisibleDelta:
    """测试 visible_delta."""

    def test_empty_current(self) -> None:
        """current 为空时应返回空 delta."""
        delta, emitted = visible_delta("", "")
        assert delta == ""
        assert emitted == ""

    def test_same_text(self) -> None:
        """相同文本应返回空 delta."""
        delta, emitted = visible_delta("hello", "hello")
        assert delta == ""
        assert emitted == "hello"

    def test_extension(self) -> None:
        """文本扩展时应返回增量."""
        delta, emitted = visible_delta("hello world", "hello")
        assert delta == " world"
        assert emitted == "hello world"

    def test_non_monotonic_rewrite(self) -> None:
        """非单调重写时应返回空 delta."""
        delta, emitted = visible_delta("world", "hello")
        assert delta == ""
        assert emitted == "hello"
