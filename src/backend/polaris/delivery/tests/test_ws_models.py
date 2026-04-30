"""Tests for polaris.delivery.ws.endpoints.models module."""

from __future__ import annotations

import pytest
from polaris.delivery.ws.endpoints.models import (
    JOURNAL_CHANNELS,
    LEGACY_LLM_CHANNELS,
    V2_CHANNEL_TO_SUBJECT,
    WebSocketSendError,
)


class TestWebSocketSendError:
    """Tests for WebSocketSendError exception."""

    def test_basic_initialization(self) -> None:
        """Test basic error initialization."""
        err = WebSocketSendError("serialization_error", "Failed to serialize")
        assert err.error_type == "serialization_error"
        assert err.message == "Failed to serialize"
        assert err.original_error is None

    def test_initialization_with_original_error(self) -> None:
        """Test error initialization with original exception."""
        original = ValueError("original problem")
        err = WebSocketSendError("connection_reset", "Connection lost", original)
        assert err.original_error is original
        assert str(err) == "Connection lost"

    def test_error_type_connection_reset(self) -> None:
        """Test connection_reset error type."""
        err = WebSocketSendError("connection_reset", "Connection reset by peer")
        assert err.error_type == "connection_reset"

    def test_is_subclass_of_exception(self) -> None:
        """Test WebSocketSendError is an Exception subclass."""
        assert issubclass(WebSocketSendError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """Test that the exception can be raised and caught."""
        with pytest.raises(WebSocketSendError) as exc_info:
            raise WebSocketSendError("test", "test message")
        assert exc_info.value.error_type == "test"
        assert exc_info.value.message == "test message"

    def test_str_representation(self) -> None:
        """Test string representation is the message."""
        err = WebSocketSendError("type", "my message")
        assert str(err) == "my message"

    def test_empty_message(self) -> None:
        """Test with empty message."""
        err = WebSocketSendError("type", "")
        assert err.message == ""
        assert str(err) == ""

    def test_long_message(self) -> None:
        """Test with very long message."""
        long_msg = "x" * 10000
        err = WebSocketSendError("type", long_msg)
        assert err.message == long_msg

    def test_special_chars_in_message(self) -> None:
        """Test with special characters in message."""
        msg = "Error: \n\t\"quoted\" \u4e2d\u6587"
        err = WebSocketSendError("type", msg)
        assert err.message == msg

    def test_none_original_error(self) -> None:
        """Test explicit None for original_error."""
        err = WebSocketSendError("type", "msg", None)
        assert err.original_error is None

    def test_nested_exception_as_original(self) -> None:
        """Test nested exception as original error."""
        inner = RuntimeError("inner")
        outer = WebSocketSendError("wrapped", "outer", inner)
        assert outer.original_error is inner


class TestLegacyLLMChannels:
    """Tests for LEGACY_LLM_CHANNELS constant."""

    def test_contains_pm_llm(self) -> None:
        """Test that 'pm_llm' is in legacy channels."""
        assert "pm_llm" in LEGACY_LLM_CHANNELS

    def test_contains_director_llm(self) -> None:
        """Test that 'director_llm' is in legacy channels."""
        assert "director_llm" in LEGACY_LLM_CHANNELS

    def test_is_set(self) -> None:
        """Test that LEGACY_LLM_CHANNELS is a set."""
        assert isinstance(LEGACY_LLM_CHANNELS, set)

    def test_expected_count(self) -> None:
        """Test expected channel count."""
        assert len(LEGACY_LLM_CHANNELS) == 2

    def test_elements_are_strings(self) -> None:
        """Test that all elements are strings."""
        for ch in LEGACY_LLM_CHANNELS:
            assert isinstance(ch, str)


class TestJournalChannels:
    """Tests for JOURNAL_CHANNELS constant."""

    def test_contains_system(self) -> None:
        """Test that 'system' is in journal channels."""
        assert "system" in JOURNAL_CHANNELS

    def test_contains_process(self) -> None:
        """Test that 'process' is in journal channels."""
        assert "process" in JOURNAL_CHANNELS

    def test_contains_llm(self) -> None:
        """Test that 'llm' is in journal channels."""
        assert "llm" in JOURNAL_CHANNELS

    def test_is_set(self) -> None:
        """Test that JOURNAL_CHANNELS is a set."""
        assert isinstance(JOURNAL_CHANNELS, set)

    def test_expected_count(self) -> None:
        """Test expected channel count."""
        assert len(JOURNAL_CHANNELS) == 3


class TestV2ChannelToSubject:
    """Tests for V2_CHANNEL_TO_SUBJECT mapping."""

    def test_contains_log_system(self) -> None:
        """Test log.system channel mapping."""
        assert V2_CHANNEL_TO_SUBJECT["log.system"] == "log.system"

    def test_contains_log_process(self) -> None:
        """Test log.process channel mapping."""
        assert V2_CHANNEL_TO_SUBJECT["log.process"] == "log.process"

    def test_contains_log_llm(self) -> None:
        """Test log.llm channel mapping."""
        assert V2_CHANNEL_TO_SUBJECT["log.llm"] == "log.llm"

    def test_contains_event_file_edit(self) -> None:
        """Test event.file_edit channel mapping."""
        assert V2_CHANNEL_TO_SUBJECT["event.file_edit"] == "event.file_edit"

    def test_contains_event_task_trace(self) -> None:
        """Test event.task_trace channel mapping."""
        assert V2_CHANNEL_TO_SUBJECT["event.task_trace"] == "event.task_trace"

    def test_contains_status_snapshot(self) -> None:
        """Test status.snapshot channel mapping."""
        assert V2_CHANNEL_TO_SUBJECT["status.snapshot"] == "status.snapshot"

    def test_is_dict(self) -> None:
        """Test that V2_CHANNEL_TO_SUBJECT is a dict."""
        assert isinstance(V2_CHANNEL_TO_SUBJECT, dict)

    def test_expected_count(self) -> None:
        """Test expected mapping count."""
        assert len(V2_CHANNEL_TO_SUBJECT) == 6

    def test_values_are_strings(self) -> None:
        """Test that all values are strings."""
        for v in V2_CHANNEL_TO_SUBJECT.values():
            assert isinstance(v, str)

    def test_unknown_channel_returns_itself(self) -> None:
        """Test that unknown channel can be looked up (returns key via get default)."""
        assert V2_CHANNEL_TO_SUBJECT.get("unknown", "unknown") == "unknown"

    def test_keys_have_dot_separator(self) -> None:
        """Test that keys use dot notation."""
        for key in V2_CHANNEL_TO_SUBJECT:
            assert "." in key


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_defined(self) -> None:
        """Test that __all__ exports are importable."""
        from polaris.delivery.ws.endpoints.models import __all__

        assert "JOURNAL_CHANNELS" in __all__
        assert "LEGACY_LLM_CHANNELS" in __all__
        assert "V2_CHANNEL_TO_SUBJECT" in __all__
        assert "WebSocketSendError" in __all__

    def test_all_count(self) -> None:
        """Test expected export count."""
        from polaris.delivery.ws.endpoints.models import __all__

        assert len(__all__) == 4
