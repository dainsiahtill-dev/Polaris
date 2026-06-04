"""OpenTelemetry-based telemetry for cognitive pipeline operations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any


class NoOpSpan:
    """No-op span context manager for when telemetry is disabled."""

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

    def record_event(
        self,
        event_name: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """No-op event recording."""
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        """No-op attribute setting."""
        pass


class CognitiveTelemetry:
    """
    OpenTelemetry-based telemetry for cognitive pipeline operations.

    Provides distributed tracing capabilities for:
    - Perception layer operations
    - Reasoning chain execution
    - Pipeline execution phases
    - Evolution and learning cycles

    Usage:
        telemetry = CognitiveTelemetry(enabled=True)
        with telemetry.start_span("perception.process", {"intent_type": "create"}):
            result = await perception.process(message, session_id)
    """

    def __init__(self, enabled: bool = False) -> None:
        """
        Initialize cognitive telemetry.

        Args:
            enabled: Whether to enable telemetry collection and export.
        """
        self._enabled = enabled
        self._trace_api: Any = None
        self._tracer: Any = None
        if enabled:
            try:
                from opentelemetry import trace
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

                provider = TracerProvider()
                processor = SimpleSpanProcessor(ConsoleSpanExporter())
                provider.add_span_processor(processor)
                trace.set_tracer_provider(provider)
                self._trace_api = trace
                self._tracer = trace.get_tracer("cognitive_pipeline")
            except ImportError:
                self._enabled = False

    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> AbstractContextManager[Any]:
        """
        Start a new span for tracing a cognitive operation.

        Args:
            name: The span name (e.g., "perception.process", "reasoning.analyze")
            attributes: Optional span attributes as key-value pairs

        Returns:
            A span context manager (real or no-op depending on enabled state)
        """
        if not self._enabled or self._tracer is None:
            return NoOpSpan()
        return self._tracer.start_as_current_span(name, attributes=attributes)

    def record_event(
        self,
        event_name: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a cognitive event to the current span.

        Args:
            event_name: Name of the event (e.g., "intent.detected", "reasoning.completed")
            attributes: Optional event attributes
        """
        if not self._enabled:
            return

        if self._trace_api is None:
            return
        current_span = self._trace_api.get_current_span()
        if current_span:
            current_span.add_event(event_name, attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        """
        Set an attribute on the current span.

        Args:
            key: Attribute name
            value: Attribute value
        """
        if not self._enabled:
            return

        if self._trace_api is None:
            return
        current_span = self._trace_api.get_current_span()
        if current_span:
            current_span.set_attribute(key, value)

    @property
    def enabled(self) -> bool:
        """Return whether telemetry is enabled."""
        return self._enabled
