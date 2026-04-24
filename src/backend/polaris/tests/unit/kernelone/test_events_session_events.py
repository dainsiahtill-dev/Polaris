"""Tests for polaris.kernelone.events.session_events."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.events.session_events import _resolve_session_event_path, emit_session_event


class TestResolveSessionEventPath:
    def test_returns_path(self) -> None:
        assert _resolve_session_event_path("/tmp") == "runtime/sessions/events"


class TestEmitSessionEvent:
    def test_emits_with_mock(self) -> None:
        with patch("polaris.kernelone.events.session_events.emit_event") as mock_emit:
            emit_session_event("/tmp", "session_created", "sess_1", {"role": "pm"})
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs
            assert call_kwargs["kind"] == "action"
            assert call_kwargs["name"] == "session_created"
            assert call_kwargs["refs"]["session_id"] == "sess_1"
