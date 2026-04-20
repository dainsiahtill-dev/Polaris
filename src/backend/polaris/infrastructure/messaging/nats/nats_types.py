r"""NATS JetStream type definitions for Polaris runtime events.

This module provides type definitions for runtime event envelopes and
JetStream resource constants for the messaging infrastructure layer.

CRITICAL: All text I/O must use UTF-8 encoding explicitly.

NOTE: Event type constants are now imported from polaris.kernelone.events.constants
to ensure consistency across the codebase. This module re-exports them for
backward compatibility with existing NATS code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.events.constants import (
    EVENT_TYPE_ERROR,
    EVENT_TYPE_STATE_SNAPSHOT,
    EVENT_TYPE_TASK_COMPLETED,
    EVENT_TYPE_TASK_CREATED,
    EVENT_TYPE_TASK_FAILED,
    EVENT_TYPE_TASK_UPDATED,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)

# =============================================================================
# JetStream Resource Constants
# =============================================================================


class JetStreamConstants:
    """JetStream resource naming and configuration constants."""

    # Stream configuration
    STREAM_NAME = "HP_RUNTIME"
    STREAM_DESCRIPTION = "Polaris runtime event stream"
    STREAM_SUBJECTS = [
        "hp.runtime.>",
    ]
    STREAM_RETENTION = "limits"  # Custom retention policy
    STREAM_STORAGE = "file"  # File-based storage (vs memory)
    STREAM_REPLICAS = 1  # Single replica for development
    STREAM_MAX_BYTES = 1_073_741_824  # 1GB
    STREAM_MAX_AGE_SECONDS = 86400 * 7  # 7 days
    STREAM_MAX_MSG_SIZE = 262_144  # 256KB per message
    STREAM_DISCARD = "old"  # Discard old messages when limits reached

    # Consumer configuration
    CONSUMER_DURABLE_PREFIX = "hp_consumer_"
    CONSUMER_DELIVERY_PREFIX = "hp_delivery_"
    CONSUMER_ACK_WAIT_SECONDS = 30
    CONSUMER_MAX_DELIVER = 3
    CONSUMER_MAX_ACK_PENDING = 1000

    # Subject patterns
    SUBJECT_PREFIX = "hp.runtime"
    SUBJECT_WILDCARD = "hp.runtime.>"

    # Channel names
    CHANNEL_PM = "pm"
    CHANNEL_DIRECTOR = "director"
    CHANNEL_QA = "qa"
    CHANNEL_ARCHITECT = "architect"
    CHANNEL_CHIEF_ENGINEER = "chief_engineer"
    CHANNEL_SYSTEM = "system"

    # Event kinds (using unified constants from events.constants)
    # NOTE: Legacy aliases for backward compatibility
    EVENT_KIND_TASK_CREATED = EVENT_TYPE_TASK_CREATED
    EVENT_KIND_TASK_UPDATED = EVENT_TYPE_TASK_UPDATED
    EVENT_KIND_TASK_COMPLETED = EVENT_TYPE_TASK_COMPLETED
    EVENT_KIND_TASK_FAILED = EVENT_TYPE_TASK_FAILED
    EVENT_KIND_MESSAGE = "message"
    EVENT_KIND_TOOL_CALL = EVENT_TYPE_TOOL_CALL
    EVENT_KIND_TOOL_RESULT = EVENT_TYPE_TOOL_RESULT
    EVENT_KIND_STATE_SNAPSHOT = EVENT_TYPE_STATE_SNAPSHOT
    EVENT_KIND_ERROR = EVENT_TYPE_ERROR


# =============================================================================
# Runtime Event Envelope
# =============================================================================


@dataclass
class RuntimeEventEnvelope:
    r"""Envelope for runtime events published to JetStream.

    This is the canonical event format for all Polaris runtime
    communication. Events are versioned and include metadata for
    tracing and debugging.

    Schema:
        runtime.v2 - Current version

    Fields:
        schema_version: Version identifier for the event schema
        event_id: Unique identifier for this event (UUID)
        workspace_key: Workspace identifier
        run_id: Execution run identifier
        channel: Target channel (pm, director, qa, etc.)
        kind: Event type (task.created, message, etc.)
        ts: ISO 8601 timestamp with timezone
        cursor: Sequence number for ordering
        trace_id: Optional trace identifier for distributed tracing
        payload: Event-specific data
        meta: Additional metadata (agent, iteration, etc.)

    Example:
        >>> event = RuntimeEventEnvelope(
        ...     workspace_key="demo-project",
        ...     run_id="run_001",
        ...     channel="pm",
        ...     kind="task.created",
        ...     payload={"task_id": "task_123", "title": "Implement login"},
        ... )
        >>> print(event.event_id)
    """

    schema_version: str = "runtime.v2"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workspace_key: str = ""
    run_id: str = ""
    channel: str = ""
    kind: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cursor: int = 0
    trace_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        r"""Convert envelope to dictionary for serialization.

        Returns:
            Dictionary representation of the event envelope.
        """
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "workspace_key": self.workspace_key,
            "run_id": self.run_id,
            "channel": self.channel,
            "kind": self.kind,
            "ts": self.ts,
            "cursor": self.cursor,
            "trace_id": self.trace_id,
            "payload": self.payload,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeEventEnvelope:
        r"""Create envelope from dictionary.

        Args:
            data: Dictionary containing event data.

        Returns:
            RuntimeEventEnvelope instance.
        """
        return cls(
            schema_version=data.get("schema_version", "runtime.v2"),
            event_id=data.get("event_id", str(uuid.uuid4())),
            workspace_key=data.get("workspace_key", ""),
            run_id=data.get("run_id", ""),
            channel=data.get("channel", ""),
            kind=data.get("kind", ""),
            ts=data.get("ts", datetime.now(timezone.utc).isoformat()),
            cursor=data.get("cursor", 0),
            trace_id=data.get("trace_id"),
            payload=data.get("payload", {}),
            meta=data.get("meta", {}),
        )

    def with_cursor(self, cursor: int) -> RuntimeEventEnvelope:
        r"""Create a copy with updated cursor.

        Args:
            cursor: New cursor value.

        Returns:
            New RuntimeEventEnvelope with updated cursor.
        """
        return RuntimeEventEnvelope(
            schema_version=self.schema_version,
            event_id=self.event_id,
            workspace_key=self.workspace_key,
            run_id=self.run_id,
            channel=self.channel,
            kind=self.kind,
            ts=self.ts,
            cursor=cursor,
            trace_id=self.trace_id,
            payload=self.payload,
            meta=self.meta,
        )

    def with_trace_id(self, trace_id: str) -> RuntimeEventEnvelope:
        r"""Create a copy with trace ID for distributed tracing.

        Args:
            trace_id: New trace identifier.

        Returns:
            New RuntimeEventEnvelope with trace_id set.
        """
        return RuntimeEventEnvelope(
            schema_version=self.schema_version,
            event_id=self.event_id,
            workspace_key=self.workspace_key,
            run_id=self.run_id,
            channel=self.channel,
            kind=self.kind,
            ts=self.ts,
            cursor=self.cursor,
            trace_id=trace_id,
            payload=self.payload,
            meta=self.meta,
        )


# =============================================================================
# Event Builder Helper
# =============================================================================


def create_runtime_event(
    workspace_key: str,
    run_id: str,
    channel: str,
    kind: str,
    payload: dict[str, Any],
    meta: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> RuntimeEventEnvelope:
    r"""Factory function to create a RuntimeEventEnvelope.

    Args:
        workspace_key: Workspace identifier.
        run_id: Execution run identifier.
        channel: Target channel (pm, director, qa, etc.).
        kind: Event type (task.created, message, etc.).
        payload: Event-specific data.
        meta: Additional metadata (optional).
        trace_id: Optional trace identifier (optional).

    Returns:
        Configured RuntimeEventEnvelope instance.
    """
    return RuntimeEventEnvelope(
        workspace_key=workspace_key,
        run_id=run_id,
        channel=channel,
        kind=kind,
        payload=payload,
        meta=meta or {},
        trace_id=trace_id,
    )


__all__ = [
    "JetStreamConstants",
    "RuntimeEventEnvelope",
    "create_runtime_event",
]
