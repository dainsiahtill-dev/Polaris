"""Tests for StreamShadowEngine cross-turn speculation."""

import pytest
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine


class MockSpeculativeExecutor:
    """Minimal mock for SpeculativeExecutor."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    async def speculate(self, invocation):
        return {
            "enabled": self.enabled,
            "result": {"mock": "result"},
        }


class TestStreamShadowEngineCrossTurnSpeculation:
    """测试 StreamShadowEngine 的跨 Turn 推测缓存。"""

    @pytest.fixture
    def engine(self):
        return StreamShadowEngine(speculative_executor=MockSpeculativeExecutor())

    def test_has_valid_speculation_false_when_empty(self, engine):
        assert engine.has_valid_speculation("sess-1") is False

    def test_has_valid_speculation_true_with_tools(self, engine):
        engine.start_cross_turn_speculation("sess-1", predicted_next_tools=[{"tool_name": "read_file"}])
        assert engine.has_valid_speculation("sess-1") is True

    def test_has_valid_speculation_true_with_content(self, engine):
        engine.start_cross_turn_speculation("sess-1", hints={}, predicted_next_tools=None)
        # 没有 tools 也没有 content，应该是 False
        assert engine.has_valid_speculation("sess-1") is False

        engine._cross_turn_cache["sess-1"]["content"] = "some content"
        assert engine.has_valid_speculation("sess-1") is True

    @pytest.mark.asyncio
    async def test_consume_speculation_clears_cache(self, engine):
        engine.start_cross_turn_speculation("sess-1", predicted_next_tools=[{"tool_name": "read_file"}])
        cached = await engine.consume_speculation("sess-1")
        assert cached["tools"] == [{"tool_name": "read_file"}]
        assert engine.has_valid_speculation("sess-1") is False

    def test_start_cross_turn_speculation_ignores_empty_session(self, engine):
        engine.start_cross_turn_speculation("", predicted_next_tools=[{"tool_name": "read_file"}])
        assert engine.has_valid_speculation("") is False
        assert "" not in engine._cross_turn_cache

    def test_has_speculated_patch(self, engine):
        assert engine.has_speculated_patch("fix-bug") is False
        engine.cache_speculated_patch("fix-bug", {"patch": "data"})
        assert engine.has_speculated_patch("fix-bug") is True

    @pytest.mark.asyncio
    async def test_consume_speculated_patch_clears_cache(self, engine):
        engine.cache_speculated_patch("fix-bug", {"patch": "data"})
        result = await engine.consume_speculated_patch("fix-bug")
        assert result == {"patch": "data"}
        assert engine.has_speculated_patch("fix-bug") is False

    def test_cache_speculated_patch_ignores_empty_intent(self, engine):
        engine.cache_speculated_patch("", {"patch": "data"})
        assert "" not in engine._speculated_patch_cache

    def test_consume_speculated_patch_returns_empty_for_missing(self, engine):
        asyncio_result = pytest.importorskip("asyncio").run(engine.consume_speculated_patch("missing"))
        assert asyncio_result == {}


class TestStreamShadowEngineConsumeDelta:
    """测试 consume_delta 基本行为。"""

    @pytest.fixture
    def engine(self):
        return StreamShadowEngine(speculative_executor=MockSpeculativeExecutor())

    def test_consume_delta_empty_returns_none(self, engine):
        assert engine.consume_delta("") is None

    def test_consume_delta_buffers_and_triggers_heuristic(self, engine):
        result = engine.consume_delta("<tool_call>")
        assert result is not None
        assert result["confidence"] == 0.1

    def test_consume_delta_no_trigger(self, engine):
        result = engine.consume_delta("hello world")
        assert result is not None
        assert result["confidence"] == 0.0

    def test_reset_clears_buffer(self, engine):
        engine.consume_delta("hello")
        engine.reset()
        assert engine._buffer == []


class TestStreamShadowEngineSpeculateToolCall:
    """测试 speculate_tool_call。"""

    @pytest.fixture
    def engine(self):
        return StreamShadowEngine(speculative_executor=MockSpeculativeExecutor(enabled=True))

    @pytest.mark.asyncio
    async def test_speculate_readonly_tool(self, engine):
        from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime

        # ensure read_file is in readonly list
        assert "read_file" in ToolBatchRuntime.READONLY_TOOLS
        result = await engine.speculate_tool_call(
            tool_name="read_file",
            arguments={"path": "main.py"},
            call_id="call-1",
        )
        assert result["enabled"] is True
        assert result["result"] == {"mock": "result"}

    @pytest.mark.asyncio
    async def test_speculate_write_tool_rejected(self, engine):
        result = await engine.speculate_tool_call(
            tool_name="write_file",
            arguments={"path": "main.py", "content": "x"},
            call_id="call-2",
        )
        assert result["error"] == "non_readonly_tool"

    @pytest.mark.asyncio
    async def test_speculate_from_buffer_skeleton(self, engine):
        engine.consume_delta("some text")
        result = await engine.speculate_from_buffer()
        assert result["enabled"] is True
        assert result["buffer_length"] == 9
        assert result["speculation"] is None
