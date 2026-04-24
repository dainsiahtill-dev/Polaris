"""Tests for TurnEngine module.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.kernel.internal.turn_engine.config import (
    SafetyState,
    TurnEngineConfig,
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


class TestTurnEngineConfig:
    """Test suite for TurnEngineConfig dataclass."""

    @pytest.fixture
    def default_config(self) -> TurnEngineConfig:
        """Create a default turn engine config."""
        return TurnEngineConfig()

    @pytest.fixture
    def configured_config(self) -> TurnEngineConfig:
        """Create a configured turn engine config."""
        return TurnEngineConfig(
            max_turns=32,
            max_total_tool_calls=128,
            max_stall_cycles=3,
            max_wall_time_seconds=1800,
            enable_streaming=False,
        )

    def test_default_values(self, default_config: TurnEngineConfig) -> None:
        """Test TurnEngineConfig default values."""
        assert default_config.max_turns == 64
        assert default_config.max_total_tool_calls == 64
        assert default_config.max_stall_cycles == 2
        assert default_config.max_wall_time_seconds == 900
        assert default_config.enable_streaming is True

    def test_configured_values(self, configured_config: TurnEngineConfig) -> None:
        """Test TurnEngineConfig configured values."""
        assert configured_config.max_turns == 32
        assert configured_config.max_total_tool_calls == 128
        assert configured_config.max_stall_cycles == 3
        assert configured_config.max_wall_time_seconds == 1800
        assert configured_config.enable_streaming is False

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test TurnEngineConfig.from_env() with environment variables."""
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", "128")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", "5")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS", "1800")
        monkeypatch.setenv("KERNELONE_TURN_ENGINE_STREAM", "false")

        config = TurnEngineConfig.from_env()
        assert config.max_turns == 128
        assert config.max_total_tool_calls == 128
        assert config.max_stall_cycles == 5
        assert config.max_wall_time_seconds == 1800
        assert config.enable_streaming is False

    def test_from_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test TurnEngineConfig.from_env() with no environment variables."""
        # Clear any existing env vars
        for key in [
            "KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS",
            "KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES",
            "KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS",
            "KERNELONE_TURN_ENGINE_STREAM",
        ]:
            monkeypatch.delenv(key, raising=False)

        config = TurnEngineConfig.from_env()
        assert config.max_turns == 64
        assert config.max_total_tool_calls == 64
        assert config.max_stall_cycles == 2
        assert config.max_wall_time_seconds == 900
        assert config.enable_streaming is True

    def test_from_env_invalid_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test TurnEngineConfig.from_env() with invalid values."""
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", "invalid")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", "not_a_number")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS", "bad")

        config = TurnEngineConfig.from_env()
        # Should use defaults for invalid values
        assert config.max_turns == 64
        assert config.max_total_tool_calls == 64
        assert config.max_stall_cycles == 2
        assert config.max_wall_time_seconds == 900

    def test_from_env_clamps_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test TurnEngineConfig.from_env() clamps values to valid range."""
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", "1000")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", "100")
        monkeypatch.setenv("KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS", "10000")

        config = TurnEngineConfig.from_env()
        assert config.max_turns == 512
        assert config.max_total_tool_calls == 512
        assert config.max_stall_cycles == 16
        assert config.max_wall_time_seconds == 7200


class TestSafetyState:
    """Test suite for SafetyState dataclass."""

    @pytest.fixture
    def state(self) -> SafetyState:
        """Create a safety state."""
        return SafetyState()

    def test_default_values(self, state: SafetyState) -> None:
        """Test SafetyState default values."""
        assert state.total_tool_calls == 0
        assert state.stall_count == 0
        assert state.last_cycle_signature == ""
        assert state.started_at > 0

    def test_check_no_stall(self, state: SafetyState) -> None:
        """Test check returns None when no stall."""
        config = TurnEngineConfig(max_stall_cycles=2)
        result = state.check(config)
        assert result is None

    def test_check_stall_detected(self, state: SafetyState) -> None:
        """Test check detects stall."""
        config = TurnEngineConfig(max_stall_cycles=2)
        state.stall_count = 3
        result = state.check(config)
        assert result is not None
        assert "tool_loop_stalled" in result

    def test_check_boundary_stall(self, state: SafetyState) -> None:
        """Test check at exact stall limit."""
        config = TurnEngineConfig(max_stall_cycles=2)
        state.stall_count = 2
        result = state.check(config)
        # stall_count (2) is not > max_stall_cycles (2), so no stall
        assert result is None

    def test_update_signature_same(self, state: SafetyState) -> None:
        """Test update_signature with same calls increments stall count."""
        calls = [MagicMock(tool="search")]
        results = [{"tool": "search", "result": "ok"}]

        state.update_signature(calls, results)
        assert state.stall_count == 0
        assert state.last_cycle_signature != ""

        # Same signature again
        state.update_signature(calls, results)
        assert state.stall_count == 1

    def test_update_signature_different(self, state: SafetyState) -> None:
        """Test update_signature with different calls resets stall count."""
        calls1 = [MagicMock(tool="search")]
        results1 = [{"tool": "search", "result": "ok"}]
        calls2 = [MagicMock(tool="read")]
        results2 = [{"tool": "read", "result": "ok"}]

        state.update_signature(calls1, results1)
        state.update_signature(calls2, results2)
        assert state.stall_count == 0

    def test_update_signature_empty(self, state: SafetyState) -> None:
        """Test update_signature with empty lists."""
        state.update_signature([], [])
        assert state.stall_count == 0
        assert state.last_cycle_signature != ""


class TestToolCallSignature:
    """Test suite for tool_call_signature function."""

    def test_simple_signature(self) -> None:
        """Test tool_call_signature with simple inputs."""
        sig = tool_call_signature("search", {"query": "test"})
        assert "search" in sig
        assert "query" in sig

    def test_deterministic(self) -> None:
        """Test tool_call_signature is deterministic."""
        sig1 = tool_call_signature("search", {"b": 2, "a": 1})
        sig2 = tool_call_signature("search", {"a": 1, "b": 2})
        assert sig1 == sig2

    def test_case_insensitive(self) -> None:
        """Test tool_call_signature is case insensitive for tool name."""
        sig1 = tool_call_signature("SEARCH", {"query": "test"})
        sig2 = tool_call_signature("search", {"query": "test"})
        assert sig1 == sig2

    def test_none_args(self) -> None:
        """Test tool_call_signature with None args."""
        sig = tool_call_signature("search", None)
        assert "search" in sig
        assert "{}" in sig

    def test_empty_tool(self) -> None:
        """Test tool_call_signature with empty tool name."""
        sig = tool_call_signature("", {"query": "test"})
        assert "::" in sig

    def test_complex_args(self) -> None:
        """Test tool_call_signature with complex args."""
        args = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        sig = tool_call_signature("test", args)
        assert "test" in sig


class TestToolCallSignatureFromParsed:
    """Test suite for tool_call_signature_from_parsed function."""

    def test_with_tool_attribute(self) -> None:
        """Test with tool attribute."""
        call = MagicMock()
        call.tool = "search"
        call.args = {"query": "test"}
        sig = tool_call_signature_from_parsed(call)
        assert "search" in sig

    def test_with_name_attribute(self) -> None:
        """Test with name attribute."""
        call = MagicMock()
        call.name = "search"
        call.arguments = {"query": "test"}
        sig = tool_call_signature_from_parsed(call)
        assert "search" in sig

    def test_dict_input(self) -> None:
        """Test with dict input."""
        call = {"tool": "search", "args": {"query": "test"}}
        sig = tool_call_signature_from_parsed(call)
        assert "search" in sig

    def test_case_insensitive(self) -> None:
        """Test tool name is case insensitive."""
        call = MagicMock()
        call.tool = "SEARCH"
        call.args = {"query": "test"}
        sig = tool_call_signature_from_parsed(call)
        assert "search" in sig.lower()


class TestDedupeParsedToolCalls:
    """Test suite for dedupe_parsed_tool_calls function."""

    def test_no_duplicates(self) -> None:
        """Test dedupe with no duplicates."""
        calls = [
            MagicMock(tool="search", args={"query": "test1"}),
            MagicMock(tool="read", args={"path": "file.txt"}),
        ]
        result = dedupe_parsed_tool_calls(calls)
        assert len(result) == 2

    def test_with_duplicates(self) -> None:
        """Test dedupe removes duplicates."""
        call1 = MagicMock(tool="search", args={"query": "test"})
        call2 = MagicMock(tool="search", args={"query": "test"})
        call3 = MagicMock(tool="read", args={"path": "file.txt"})
        calls = [call1, call2, call3]

        result = dedupe_parsed_tool_calls(calls)
        assert len(result) == 2
        assert result[0].tool == "search"
        assert result[1].tool == "read"

    def test_empty_list(self) -> None:
        """Test dedupe with empty list."""
        result = dedupe_parsed_tool_calls([])
        assert result == []

    def test_single_call(self) -> None:
        """Test dedupe with single call."""
        call = MagicMock(tool="search", args={})
        result = dedupe_parsed_tool_calls([call])
        assert len(result) == 1


class TestResolveEmptyVisibleOutputError:
    """Test suite for resolve_empty_visible_output_error function."""

    def test_with_parsed_tool_calls(self) -> None:
        """Test returns None when parsed tool calls present."""
        turn = MagicMock()
        turn.native_tool_calls = []
        turn.clean_content = ""
        turn.thinking = ""

        result = resolve_empty_visible_output_error(turn, [{"tool": "search"}])
        assert result is None

    def test_with_native_tool_calls(self) -> None:
        """Test returns None when native tool calls present."""
        turn = MagicMock()
        turn.native_tool_calls = [{"tool": "search"}]
        turn.clean_content = ""
        turn.thinking = ""

        result = resolve_empty_visible_output_error(turn, [])
        assert result is None

    def test_with_content(self) -> None:
        """Test returns None when content present."""
        turn = MagicMock()
        turn.native_tool_calls = []
        turn.clean_content = "Hello world"
        turn.thinking = ""

        result = resolve_empty_visible_output_error(turn, [])
        assert result is None

    def test_with_thinking_only(self) -> None:
        """Test returns error for thinking-only response."""
        turn = MagicMock()
        turn.native_tool_calls = []
        turn.clean_content = ""
        turn.thinking = "Some thinking"

        result = resolve_empty_visible_output_error(turn, [])
        assert result is not None
        assert "thinking-only" in result

    def test_empty_output(self) -> None:
        """Test returns error for completely empty output."""
        turn = MagicMock()
        turn.native_tool_calls = []
        turn.clean_content = ""
        turn.thinking = ""

        result = resolve_empty_visible_output_error(turn, [])
        assert result is not None
        assert "no visible output" in result

    def test_whitespace_content(self) -> None:
        """Test whitespace-only content is considered empty."""
        turn = MagicMock()
        turn.native_tool_calls = []
        turn.clean_content = "   "
        turn.thinking = ""

        result = resolve_empty_visible_output_error(turn, [])
        assert result is not None


class TestNormalizeStreamToolCallPayload:
    """Test suite for normalize_stream_tool_call_payload function."""

    def test_openai_format(self) -> None:
        """Test normalizes OpenAI format."""
        metadata = {
            "native_tool_call": {
                "type": "function",
                "function": {"name": "search", "arguments": '{"query": "test"}'},
            }
        }
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="search",
            tool_args={"query": "test"},
            call_id="call_123",
            metadata=metadata,
        )
        assert provider == "openai"
        assert payload["type"] == "function"

    def test_anthropic_format(self) -> None:
        """Test normalizes Anthropic format."""
        metadata = {
            "native_tool_call": {
                "type": "tool_use",
                "name": "search",
                "input": {"query": "test"},
            }
        }
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="search",
            tool_args={"query": "test"},
            call_id="call_123",
            metadata=metadata,
        )
        assert provider == "anthropic"
        assert payload["type"] == "tool_use"

    def test_auto_format(self) -> None:
        """Test auto format for unknown provider."""
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="search",
            tool_args={"query": "test"},
            call_id="call_123",
            metadata={},
        )
        assert provider == "openai"
        assert payload["type"] == "function"
        assert payload["function"]["name"] == "search"

    def test_empty_tool_name(self) -> None:
        """Test returns None for empty tool name."""
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="",
            tool_args={},
            call_id="call_123",
            metadata={},
        )
        assert payload is None
        assert provider == "auto"

    def test_none_args(self) -> None:
        """Test handles None args."""
        payload, provider = normalize_stream_tool_call_payload(
            tool_name="search",
            tool_args=None,
            call_id="call_123",
            metadata={},
        )
        assert provider == "openai"
        assert payload["function"]["arguments"] == "{}"


class TestMergeStreamThinking:
    """Test suite for merge_stream_thinking function."""

    def test_both_empty(self) -> None:
        """Test returns None when both empty."""
        result = merge_stream_thinking(parsed_thinking=None, streamed_thinking_parts=[])
        assert result is None

    def test_parsed_only(self) -> None:
        """Test returns parsed when only parsed present."""
        result = merge_stream_thinking(
            parsed_thinking="parsed thinking",
            streamed_thinking_parts=[],
        )
        assert result == "parsed thinking"

    def test_streamed_only(self) -> None:
        """Test returns streamed when only streamed present."""
        result = merge_stream_thinking(
            parsed_thinking=None,
            streamed_thinking_parts=["streamed thinking"],
        )
        assert result == "streamed thinking"

    def test_identical(self) -> None:
        """Test returns single when identical."""
        result = merge_stream_thinking(
            parsed_thinking="same thinking",
            streamed_thinking_parts=["same thinking"],
        )
        assert result == "same thinking"

    def test_streamed_in_parsed(self) -> None:
        """Test returns parsed when streamed is subset."""
        result = merge_stream_thinking(
            parsed_thinking="longer parsed thinking with more content",
            streamed_thinking_parts=["parsed thinking"],
        )
        assert result == "longer parsed thinking with more content"

    def test_parsed_in_streamed(self) -> None:
        """Test returns streamed when parsed is subset."""
        result = merge_stream_thinking(
            parsed_thinking="parsed",
            streamed_thinking_parts=["longer streamed thinking with parsed inside"],
        )
        assert result == "longer streamed thinking with parsed inside"

    def test_different_content(self) -> None:
        """Test merges different content."""
        result = merge_stream_thinking(
            parsed_thinking="parsed",
            streamed_thinking_parts=["streamed"],
        )
        assert "streamed" in result
        assert "parsed" in result

    def test_multiple_streamed_parts(self) -> None:
        """Test merges multiple streamed parts."""
        result = merge_stream_thinking(
            parsed_thinking="parsed",
            streamed_thinking_parts=["part1", "part2"],
        )
        assert "part1" in result
        assert "part2" in result
        assert "parsed" in result


class TestVisibleDelta:
    """Test suite for visible_delta function."""

    def test_empty_current(self) -> None:
        """Test returns empty delta when current is empty."""
        delta, emitted = visible_delta(None, "hello")
        assert delta == ""
        assert emitted == "hello"

    def test_same_content(self) -> None:
        """Test returns empty delta when content is same."""
        delta, emitted = visible_delta("hello", "hello")
        assert delta == ""
        assert emitted == "hello"

    def test_monotonic_extension(self) -> None:
        """Test returns delta for monotonic extension."""
        delta, emitted = visible_delta("hello world", "hello")
        assert delta == " world"
        assert emitted == "hello world"

    def test_non_monotonic(self) -> None:
        """Test returns empty for non-monotonic change."""
        delta, emitted = visible_delta("different", "hello")
        assert delta == ""
        assert emitted == "hello"

    def test_shrink(self) -> None:
        """Test returns empty for shrink."""
        delta, emitted = visible_delta("hi", "hello")
        assert delta == ""
        assert emitted == "hello"

    def test_empty_emitted(self) -> None:
        """Test returns full content when emitted is empty."""
        delta, emitted = visible_delta("hello", "")
        assert delta == "hello"
        assert emitted == "hello"

    def test_both_empty(self) -> None:
        """Test returns empty when both empty."""
        delta, emitted = visible_delta("", "")
        assert delta == ""
        assert emitted == ""


class TestTurnEngineConfigEdgeCases:
    """Test edge cases for TurnEngineConfig."""

    def test_zero_values(self) -> None:
        """Test TurnEngineConfig with zero values."""
        config = TurnEngineConfig(
            max_turns=0,
            max_total_tool_calls=0,
            max_stall_cycles=0,
            max_wall_time_seconds=0,
        )
        assert config.max_turns == 0
        assert config.max_total_tool_calls == 0
        assert config.max_stall_cycles == 0
        assert config.max_wall_time_seconds == 0

    def test_negative_stall_cycles(self) -> None:
        """Test TurnEngineConfig with negative stall cycles."""
        config = TurnEngineConfig(max_stall_cycles=-1)
        assert config.max_stall_cycles == -1

    def test_from_env_stream_variations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test from_env with different stream env var values."""
        for value, expected in [
            ("true", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("no", False),
        ]:
            monkeypatch.setenv("KERNELONE_TURN_ENGINE_STREAM", value)
            config = TurnEngineConfig.from_env()
            assert config.enable_streaming is expected


class TestSafetyStateEdgeCases:
    """Test edge cases for SafetyState."""

    def test_high_stall_count(self) -> None:
        """Test SafetyState with high stall count."""
        state = SafetyState()
        state.stall_count = 100
        config = TurnEngineConfig(max_stall_cycles=2)
        result = state.check(config)
        assert result is not None
        assert "tool_loop_stalled" in result

    def test_update_signature_with_dict_calls(self) -> None:
        """Test update_signature with dict calls."""
        state = SafetyState()
        calls = [{"tool": "search"}]
        results = [{"tool": "search"}]
        state.update_signature(calls, results)
        assert state.last_cycle_signature != ""

    def test_update_signature_with_string_results(self) -> None:
        """Test update_signature with string results."""
        state = SafetyState()
        calls: list[Any] = []
        results = ["result1", "result2"]
        state.update_signature(calls, results)
        assert state.last_cycle_signature != ""
