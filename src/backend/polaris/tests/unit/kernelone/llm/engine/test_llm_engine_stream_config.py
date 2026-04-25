"""Tests for polaris.kernelone.llm.engine.stream.config."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest
from polaris.kernelone.llm.engine.stream.config import (
    LLMStreamResult,
    StreamConfig,
    StreamState,
    get_default_stream_config,
    get_stream_timeout,
    reset_stream_timeout,
    set_stream_timeout,
    validate_stream_result,
)


class TestStreamConfig:
    def test_default_values(self) -> None:
        cfg = StreamConfig()
        assert cfg.timeout_sec > 0
        assert cfg.max_retries >= 0
        assert cfg.buffer_size > 0
        assert cfg.max_pending_calls > 0
        assert cfg.token_timeout_sec > 0

    def test_post_init_validation_negative_timeout(self) -> None:
        cfg = StreamConfig(timeout_sec=-1.0)
        assert cfg.timeout_sec > 0

    def test_post_init_validation_negative_retries(self) -> None:
        cfg = StreamConfig(max_retries=-5)
        assert cfg.max_retries >= 0

    def test_post_init_validation_negative_buffer(self) -> None:
        cfg = StreamConfig(buffer_size=-100)
        assert cfg.buffer_size > 0

    def test_post_init_validation_negative_pending(self) -> None:
        cfg = StreamConfig(max_pending_calls=-1)
        assert cfg.max_pending_calls > 0

    def test_post_init_validation_negative_token_timeout(self) -> None:
        cfg = StreamConfig(token_timeout_sec=-10.0)
        assert cfg.token_timeout_sec > 0

    def test_custom_values(self) -> None:
        cfg = StreamConfig(
            timeout_sec=120.0,
            max_retries=5,
            buffer_size=500,
            max_pending_calls=50,
            token_timeout_sec=30.0,
        )
        assert cfg.timeout_sec == 120.0
        assert cfg.max_retries == 5
        assert cfg.buffer_size == 500
        assert cfg.max_pending_calls == 50
        assert cfg.token_timeout_sec == 30.0

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_LLM_STREAM_BUFFER_SIZE", "2048")
        monkeypatch.setenv("KERNELONE_LLM_MAX_PENDING_CALLS", "200")
        cfg = StreamConfig.from_env()
        assert cfg.buffer_size == 2048
        assert cfg.max_pending_calls == 200

    def test_to_dict(self) -> None:
        cfg = StreamConfig(
            timeout_sec=60.0, max_retries=2, buffer_size=100, max_pending_calls=10, token_timeout_sec=30.0
        )
        d = cfg.to_dict()
        assert d["timeout_sec"] == 60.0
        assert d["max_retries"] == 2
        assert d["buffer_size"] == 100
        assert d["max_pending_calls"] == 10
        assert d["token_timeout_sec"] == 30.0

    def test_immutable(self) -> None:
        cfg = StreamConfig()
        # Regular attribute assignment on frozen dataclass should raise
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            cfg.timeout_sec = 10.0

    def test_from_env_uses_unified_timeout(self) -> None:
        with (
            patch("polaris.kernelone.llm.engine.stream.config._get_stream_timeout_unified", return_value=45.0),
            patch("polaris.kernelone.llm.engine.stream.config._get_token_timeout_unified", return_value=15.0),
        ):
            cfg = StreamConfig.from_env()
            assert cfg.timeout_sec == 45.0
            assert cfg.token_timeout_sec == 15.0


class TestStreamState:
    def test_idle_transitions(self) -> None:
        assert StreamState.IDLE.can_transition_to(StreamState.IN_THINKING) is True
        assert StreamState.IDLE.can_transition_to(StreamState.IN_CONTENT) is True
        assert StreamState.IDLE.can_transition_to(StreamState.IN_TOOL_CALL) is True
        assert StreamState.IDLE.can_transition_to(StreamState.COMPLETE) is False
        assert StreamState.IDLE.can_transition_to(StreamState.ERROR) is False

    def test_in_thinking_transitions(self) -> None:
        assert StreamState.IN_THINKING.can_transition_to(StreamState.IN_CONTENT) is True
        assert StreamState.IN_THINKING.can_transition_to(StreamState.IN_TOOL_CALL) is True
        assert StreamState.IN_THINKING.can_transition_to(StreamState.COMPLETE) is True
        assert StreamState.IN_THINKING.can_transition_to(StreamState.ERROR) is True
        assert StreamState.IN_THINKING.can_transition_to(StreamState.IDLE) is False

    def test_in_content_transitions(self) -> None:
        assert StreamState.IN_CONTENT.can_transition_to(StreamState.IN_TOOL_CALL) is True
        assert StreamState.IN_CONTENT.can_transition_to(StreamState.COMPLETE) is True
        assert StreamState.IN_CONTENT.can_transition_to(StreamState.ERROR) is True
        assert StreamState.IN_CONTENT.can_transition_to(StreamState.IDLE) is False

    def test_in_tool_call_transitions(self) -> None:
        assert StreamState.IN_TOOL_CALL.can_transition_to(StreamState.IN_CONTENT) is True
        assert StreamState.IN_TOOL_CALL.can_transition_to(StreamState.IN_TOOL_CALL) is True
        assert StreamState.IN_TOOL_CALL.can_transition_to(StreamState.COMPLETE) is True
        assert StreamState.IN_TOOL_CALL.can_transition_to(StreamState.ERROR) is True
        assert StreamState.IN_TOOL_CALL.can_transition_to(StreamState.IDLE) is False

    def test_complete_no_transitions(self) -> None:
        assert StreamState.COMPLETE.can_transition_to(StreamState.IDLE) is False
        assert StreamState.COMPLETE.can_transition_to(StreamState.IN_CONTENT) is False
        assert StreamState.COMPLETE.can_transition_to(StreamState.ERROR) is False

    def test_error_no_transitions(self) -> None:
        assert StreamState.ERROR.can_transition_to(StreamState.IDLE) is False
        assert StreamState.ERROR.can_transition_to(StreamState.COMPLETE) is False

    def test_values(self) -> None:
        assert StreamState.IDLE.value == "idle"
        assert StreamState.IN_THINKING.value == "in_thinking"
        assert StreamState.IN_TOOL_CALL.value == "in_tool_call"
        assert StreamState.IN_CONTENT.value == "in_content"
        assert StreamState.COMPLETE.value == "complete"
        assert StreamState.ERROR.value == "error"


class TestLLMStreamResult:
    def test_default_values(self) -> None:
        result = LLMStreamResult()
        assert result.events == []
        assert result.is_complete is False
        assert result.validation_errors == []
        assert result.collected_output == ""
        assert result.collected_reasoning == ""
        assert result.tool_calls_count == 0
        assert result.chunk_count == 0
        assert result.latency_ms == 0
        assert result.trace_id is None

    def test_add_validation_error(self) -> None:
        result = LLMStreamResult()
        result.add_validation_error("test error")
        assert result.validation_errors == ["test error"]

    def test_to_dict(self) -> None:
        result = LLMStreamResult(
            is_complete=True,
            chunk_count=5,
            tool_calls_count=2,
            latency_ms=100,
            trace_id="t1",
        )
        d = result.to_dict()
        assert d["is_complete"] is True
        assert d["validation_errors"] == []
        assert d["collected_output_length"] == 0
        assert d["collected_reasoning_length"] == 0
        assert d["tool_calls_count"] == 2
        assert d["chunk_count"] == 5
        assert d["latency_ms"] == 100
        assert d["trace_id"] == "t1"


class TestValidateStreamResult:
    def test_valid_complete(self) -> None:
        result = LLMStreamResult(is_complete=True)
        assert validate_stream_result(result) is True

    def test_invalid_incomplete(self) -> None:
        result = LLMStreamResult(is_complete=False)
        assert validate_stream_result(result) is False
        assert len(result.validation_errors) == 1

    def test_invalid_with_errors(self) -> None:
        result = LLMStreamResult(is_complete=True, validation_errors=["error1"])
        assert validate_stream_result(result) is False


class TestBackwardCompatibility:
    def test_get_stream_timeout(self) -> None:
        with patch("polaris.kernelone.llm.engine.stream.config._get_stream_timeout_unified", return_value=300.0):
            assert get_stream_timeout() == 300.0

    def test_set_stream_timeout(self) -> None:
        with patch("polaris.kernelone.llm.engine.stream.config._set_stream_timeout_unified") as mock_set:
            set_stream_timeout(120.0)
            mock_set.assert_called_once_with(120.0)

    def test_reset_stream_timeout(self) -> None:
        with patch("polaris.kernelone.llm.engine.stream.config._reset_unified_config") as mock_reset:
            reset_stream_timeout()
            mock_reset.assert_called_once()

    def test_get_default_stream_config(self) -> None:
        cfg = get_default_stream_config()
        assert isinstance(cfg, StreamConfig)

    def test_stream_result_alias(self) -> None:
        from polaris.kernelone.llm.engine.stream.config import StreamResult

        assert StreamResult is LLMStreamResult
