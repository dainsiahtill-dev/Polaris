"""Tests for OpenTelemetry-based Cognitive Telemetry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.cognitive.telemetry import CognitiveTelemetry, NoOpSpan


class TestNoOpSpan:
    """Tests for NoOpSpan context manager."""

    def test_noop_span_context_manager(self) -> None:
        """Test NoOpSpan works as a context manager."""
        span = NoOpSpan()
        with span as s:
            assert s is span

    def test_noop_span_record_event(self) -> None:
        """Test NoOpSpan record_event is a no-op."""
        span = NoOpSpan()
        # Should not raise
        span.record_event("test_event", {"key": "value"})

    def test_noop_span_set_attribute(self) -> None:
        """Test NoOpSpan set_attribute is a no-op."""
        span = NoOpSpan()
        # Should not raise
        span.set_attribute("key", "value")


class TestCognitiveTelemetry:
    """Tests for CognitiveTelemetry class."""

    def test_telemetry_disabled_by_default(self) -> None:
        """Test telemetry is disabled by default."""
        telemetry = CognitiveTelemetry(enabled=False)
        assert not telemetry.enabled

    def test_telemetry_enabled(self) -> None:
        """Test telemetry can be enabled."""
        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider") as mock_provider,
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            assert telemetry.enabled
            mock_provider.assert_called_once()

    def test_start_span_disabled_returns_noop(self) -> None:
        """Test start_span returns NoOpSpan when disabled."""
        telemetry = CognitiveTelemetry(enabled=False)
        span = telemetry.start_span("test.span")
        assert isinstance(span, NoOpSpan)

    def test_start_span_enabled(self) -> None:
        """Test start_span creates real span when enabled."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span

        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
            patch("polaris.kernelone.cognitive.telemetry.trace.get_tracer", return_value=mock_tracer),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            span = telemetry.start_span("test.span", {"key": "value"})
            mock_tracer.start_as_current_span.assert_called_once_with("test.span", attributes={"key": "value"})
            assert span is mock_span

    def test_record_event_disabled(self) -> None:
        """Test record_event does nothing when disabled."""
        telemetry = CognitiveTelemetry(enabled=False)
        # Should not raise
        telemetry.record_event("test_event", {"key": "value"})

    def test_record_event_enabled_no_current_span(self) -> None:
        """Test record_event handles no current span gracefully."""
        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
            patch("polaris.kernelone.cognitive.telemetry.trace.get_current_span", return_value=None),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            # Should not raise
            telemetry.record_event("test_event")

    def test_record_event_enabled_with_span(self) -> None:
        """Test record_event adds event to current span."""
        mock_span = MagicMock()

        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
            patch("polaris.kernelone.cognitive.telemetry.trace.get_current_span", return_value=mock_span),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            telemetry.record_event("test_event", {"key": "value"})
            mock_span.add_event.assert_called_once_with("test_event", {"key": "value"})

    def test_set_attribute_disabled(self) -> None:
        """Test set_attribute does nothing when disabled."""
        telemetry = CognitiveTelemetry(enabled=False)
        # Should not raise
        telemetry.set_attribute("key", "value")

    def test_set_attribute_enabled_no_current_span(self) -> None:
        """Test set_attribute handles no current span gracefully."""
        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
            patch("polaris.kernelone.cognitive.telemetry.trace.get_current_span", return_value=None),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            # Should not raise
            telemetry.set_attribute("key", "value")

    def test_set_attribute_enabled_with_span(self) -> None:
        """Test set_attribute sets attribute on current span."""
        mock_span = MagicMock()

        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
            patch("polaris.kernelone.cognitive.telemetry.trace.get_current_span", return_value=mock_span),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            telemetry.set_attribute("key", "value")
            mock_span.set_attribute.assert_called_once_with("key", "value")


class TestTelemetryIntegration:
    """Integration tests for telemetry in cognitive operations."""

    @pytest.mark.asyncio
    async def test_orchestrator_with_telemetry_disabled(self) -> None:
        """Test orchestrator works normally when telemetry is disabled."""
        from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator

        # Create orchestrator with telemetry disabled
        orchestrator = CognitiveOrchestrator(
            workspace=".",
            enable_telemetry=False,
            enable_governance=False,
            enable_personality=False,
            enable_evolution=False,
            enable_value_alignment=False,
            use_llm=False,
        )

        # Verify telemetry is disabled
        assert not orchestrator._telemetry.enabled

    @pytest.mark.asyncio
    async def test_orchestrator_with_telemetry_enabled(self) -> None:
        """Test orchestrator initializes telemetry when enabled."""
        from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator

        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
        ):
            orchestrator = CognitiveOrchestrator(
                workspace=".",
                enable_telemetry=True,
                enable_governance=False,
                enable_personality=False,
                enable_evolution=False,
                enable_value_alignment=False,
                use_llm=False,
            )

            # Verify telemetry is enabled
            assert orchestrator._telemetry.enabled


class TestTelemetrySpans:
    """Tests for specific telemetry span operations."""

    def test_span_with_attributes(self) -> None:
        """Test span creation with attributes."""
        mock_tracer = MagicMock()
        mock_span_context = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_context

        with (
            patch("polaris.kernelone.cognitive.telemetry.TracerProvider"),
            patch("polaris.kernelone.cognitive.telemetry.SimpleSpanProcessor"),
            patch("polaris.kernelone.cognitive.telemetry.ConsoleSpanExporter"),
            patch("polaris.kernelone.cognitive.telemetry.trace.set_tracer_provider"),
            patch("polaris.kernelone.cognitive.telemetry.trace.get_tracer", return_value=mock_tracer),
        ):
            telemetry = CognitiveTelemetry(enabled=True)
            attrs = {"session_id": "test123", "role_id": "director"}
            telemetry.start_span("cognitive.process", attrs)
            mock_tracer.start_as_current_span.assert_called_once_with("cognitive.process", attributes=attrs)

    def test_nested_spans(self) -> None:
        """Test nested span creation."""
        telemetry = CognitiveTelemetry(enabled=False)

        # Should work without issues even when nested
        with telemetry.start_span("outer") as outer, telemetry.start_span("inner") as inner:
            assert isinstance(outer, NoOpSpan)
            assert isinstance(inner, NoOpSpan)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
