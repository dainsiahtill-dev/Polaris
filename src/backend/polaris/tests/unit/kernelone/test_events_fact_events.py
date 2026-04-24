"""Tests for polaris.kernelone.events.fact_events."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.events.fact_events import _resolve_fact_event_path, emit_fact_event


class TestResolveFactEventPath:
    def test_returns_path(self) -> None:
        assert _resolve_fact_event_path("/tmp") == "runtime/events//tmp/facts"


class TestEmitFactEvent:
    def test_emits_with_mock(self) -> None:
        with patch("polaris.kernelone.events.fact_events.emit_event") as mock_emit:
            emit_fact_event("/tmp", "file_created", {"path": "/tmp/file.txt"})
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs
            assert call_kwargs["kind"] == "observation"
            assert call_kwargs["name"] == "file_created"
            assert call_kwargs["ok"] is True
