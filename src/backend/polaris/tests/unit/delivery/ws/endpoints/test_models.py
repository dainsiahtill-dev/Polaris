"""Tests for polaris.delivery.ws.endpoints.models."""

from __future__ import annotations

from polaris.delivery.ws.endpoints.models import (
    JOURNAL_CHANNELS,
    LEGACY_LLM_CHANNELS,
    V2_CHANNEL_TO_SUBJECT,
    WebSocketSendError,
)


class TestWebSocketSendError:
    def test_basic_error(self) -> None:
        err = WebSocketSendError("serialization_error", "Failed to serialize")
        assert err.error_type == "serialization_error"
        assert err.message == "Failed to serialize"
        assert err.original_error is None
        assert str(err) == "Failed to serialize"

    def test_error_with_original(self) -> None:
        original = ValueError("original")
        err = WebSocketSendError("connection_reset", "Connection lost", original)
        assert err.original_error is original

    def test_inheritance(self) -> None:
        err = WebSocketSendError("test", "msg")
        assert isinstance(err, Exception)


class TestChannelConstants:
    def test_legacy_llm_channels(self) -> None:
        assert "pm_llm" in LEGACY_LLM_CHANNELS
        assert "director_llm" in LEGACY_LLM_CHANNELS

    def test_journal_channels(self) -> None:
        assert "system" in JOURNAL_CHANNELS
        assert "process" in JOURNAL_CHANNELS
        assert "llm" in JOURNAL_CHANNELS

    def test_v2_channel_to_subject(self) -> None:
        assert "log.system" in V2_CHANNEL_TO_SUBJECT
        assert "log.process" in V2_CHANNEL_TO_SUBJECT
        assert "log.llm" in V2_CHANNEL_TO_SUBJECT
        assert "event.file_edit" in V2_CHANNEL_TO_SUBJECT
        assert "status.snapshot" in V2_CHANNEL_TO_SUBJECT

    def test_v2_subject_values(self) -> None:
        assert V2_CHANNEL_TO_SUBJECT["log.system"] == "log.system"
        assert V2_CHANNEL_TO_SUBJECT["event.file_edit"] == "event.file_edit"
