"""Context Events - Key node event types for Polaris observability.

This module provides event types and the EventWriter for tracking
critical context operations: projection, compression, fallback, HITL, etc.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from polaris.kernelone.telemetry.trace import get_trace_id

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Event types for context operations."""

    CONTEXT_PROJECTION = "context_projection"
    """Context projection completed event."""

    CONTEXT_COMPRESSION = "context_compression"
    """Context compression applied event."""

    COGNITIVE_FALLBACK = "cognitive_fallback"
    """Cognitive fallback triggered event."""

    HITL_TIMEOUT = "hitl_timeout"
    """Human-in-the-loop timeout event."""

    ALIGNMENT_BLOCK = "alignment_block"
    """Value alignment blocking event."""

    SEMANTIC_SEARCH = "semantic_search"
    """Semantic search operation event."""


@dataclass
class ContextEvent:
    """Event record for context operations.

    Attributes:
        event_type: Type of the event.
        trace_id: Distributed trace ID for correlation.
        timestamp: ISO 8601 timestamp when event occurred.
        duration_ms: Duration of the operation in milliseconds.
        metadata: Additional event metadata.
    """

    event_type: EventType
    trace_id: str
    timestamp: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: EventType,
        duration_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> ContextEvent:
        """Create a new ContextEvent with current trace ID and timestamp.

        Args:
            event_type: Type of the event.
            duration_ms: Duration of the operation in milliseconds.
            metadata: Additional event metadata.

        Returns:
            New ContextEvent instance.
        """
        return cls(
            event_type=event_type,
            trace_id=get_trace_id(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            metadata=metadata or {},
        )


class EventWriter:
    """Unified event writer for context events.

    Provides a central point for writing context events to
    logging, tracing, or external observability systems.

    Usage::

        writer = EventWriter()

        # Write an event
        event = ContextEvent.create(
            EventType.CONTEXT_PROJECTION,
            duration_ms=125.5,
            metadata={"token_estimate": 4000}
        )
        writer.write(event)
    """

    def __init__(self) -> None:
        self._enabled = True

    def write(self, event: ContextEvent) -> None:
        """Write an event to the event log.

        Args:
            event: The ContextEvent to write.
        """
        if not self._enabled:
            return

        try:
            logger.info(
                "ContextEvent: type=%s trace_id=%s duration_ms=%.2f metadata=%s",
                event.event_type.value,
                event.trace_id,
                event.duration_ms,
                event.metadata,
            )
        except (RuntimeError, OSError) as e:
            logger.warning("Failed to write ContextEvent: %s", e)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable event writing."""
        self._enabled = enabled


# Global default event writer
_default_event_writer: EventWriter | None = None


def get_event_writer() -> EventWriter:
    """Get the default global EventWriter instance."""
    global _default_event_writer
    if _default_event_writer is None:
        _default_event_writer = EventWriter()
    return _default_event_writer
