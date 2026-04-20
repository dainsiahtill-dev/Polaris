"""AuditEvent base schema — Pydantic v2 with CloudEvents alignment.

Design principles:
- All events are frozen (immutable) after creation
- Version field enables schema evolution without breaking existing consumers
- schema_uri points to canonical schema definition
- Domain + event_type provide hierarchical namespacing
- Correlation context carried inline for traceability
- Schema validation at emission time, not just at rest

Reference: CloudEvents spec v1.0.2 — https://cloudevents.io/
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Self

# Import AuditPriority from bus for consistency
# This IntEnum is used for comparisons in the bus layer
from polaris.kernelone.audit.omniscient.bus import AuditPriority
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventDomain(str, Enum):
    """Event domain classification for the omniscient audit system.

    Provides hierarchical namespacing for event types.
    """

    LLM = "llm"  # LLM interaction events
    TOOL = "tool"  # Tool/function call events
    DIALOGUE = "dialogue"  # Role communication events
    CONTEXT = "context"  # Context management events
    TASK = "task"  # Task orchestration events
    SYSTEM = "system"  # System-level events (audit itself)
    SECURITY = "security"  # Security-related events


# Backwards-compatibility alias for any code importing AuditPriority from schemas.base
AuditPriorityStr = AuditPriority


class AuditEvent(BaseModel, frozen=True):
    """Base audit event schema with CloudEvents alignment.

    All omniscient audit events inherit from this base class.
    Provides common fields for event correlation, prioritization,
    and schema evolution.

    Attributes:
        event_id: Unique identifier for this event (UUID4 hex, 32 chars).
        version: Schema version for evolution (semver-ish).
        schema_uri: Canonical URI to schema definition.
        domain: High-level event domain (LLM, TOOL, DIALOGUE, etc.).
        event_type: Specific event type within domain.
        timestamp: When the event occurred (UTC, ISO 8601).
        trace_id: Correlation ID for distributed tracing (16-char hex).
        run_id: Execution session identifier.
        span_id: Current operation span identifier.
        parent_span_id: Parent span for call chain.
        priority: Processing priority for the audit bus.
        workspace: Workspace path for multi-tenant isolation.
        role: Role that emitted this event (pm, director, qa, etc.).
        data: Event-specific payload (schema varies by event_type).
        correlation_context: Additional context for event correlation.

    Schema evolution:
        - Minor additions: Add new optional fields with defaults
        - Major changes: Bump version, maintain from_dict_v1(), from_dict_v2()
        - Breaking changes: New event_type, not modification of existing

    Example:
        event = AuditEvent(
            domain=EventDomain.LLM,
            event_type="llm_call",
            trace_id="abc123",
            role="director",
            data={"model": "claude-3-sonnet", "tokens": 1000},
        )
    """

    model_config = ConfigDict(
        frozen=True,  # Immutable after creation
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,  # Keep enum objects for serialization
    )

    # -------------------------------------------------------------------------
    # Identity fields
    # -------------------------------------------------------------------------

    event_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique event identifier (UUID4 hex)",
        max_length=64,
    )

    version: str = Field(
        default="3.0",
        description="Schema version for evolution",
        max_length=16,
    )

    schema_uri: str = Field(
        default="https://polaris.dev/schemas/audit/v3.0",
        description="Canonical URI to schema definition",
        max_length=256,
    )

    # -------------------------------------------------------------------------
    # Classification fields
    # -------------------------------------------------------------------------

    domain: EventDomain = Field(
        default=EventDomain.SYSTEM,
        description="High-level event domain",
    )

    event_type: str = Field(
        default="",
        description="Specific event type within domain",
        max_length=64,
    )

    # -------------------------------------------------------------------------
    # Temporal fields
    # -------------------------------------------------------------------------

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the event occurred (UTC)",
    )

    # -------------------------------------------------------------------------
    # Correlation/tracing fields
    # -------------------------------------------------------------------------

    trace_id: str = Field(
        default="",
        description="Correlation ID for distributed tracing",
        max_length=32,
    )

    run_id: str = Field(
        default="",
        description="Execution session identifier",
        max_length=64,
    )

    span_id: str = Field(
        default="",
        description="Current operation span identifier",
        max_length=32,
    )

    parent_span_id: str = Field(
        default="",
        description="Parent span for call chain",
        max_length=32,
    )

    # -------------------------------------------------------------------------
    # Processing fields
    # -------------------------------------------------------------------------

    priority: AuditPriority = Field(
        default=AuditPriority.INFO,
        description="Processing priority for audit bus",
    )

    # -------------------------------------------------------------------------
    # Attribution fields
    # -------------------------------------------------------------------------

    workspace: str = Field(
        default="",
        description="Workspace path for multi-tenant isolation",
        max_length=512,
    )

    role: str = Field(
        default="",
        description="Role that emitted this event",
        max_length=32,
    )

    # -------------------------------------------------------------------------
    # Payload
    # -------------------------------------------------------------------------

    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific payload (schema varies by event_type)",
    )

    # -------------------------------------------------------------------------
    # Extended context
    # -------------------------------------------------------------------------

    correlation_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context for event correlation",
    )

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("trace_id", "run_id", "span_id", "parent_span_id", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        """Strip whitespace from ID fields."""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("trace_id", mode="after")
    @classmethod
    def _validate_trace_id(cls, v: str) -> str:
        """Validate trace_id format (hex, 16-32 chars)."""
        if v and (len(v) < 16 or len(v) > 32):
            raise ValueError(f"trace_id must be 16-32 chars, got {len(v)}")
        if v and not all(c in "0123456789abcdef" for c in v.lower()):
            raise ValueError("trace_id must be hexadecimal")
        return v.lower()

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to dict for audit logging (CloudEvents-compatible).

        Returns:
            Dictionary representation compatible with KernelAuditEvent.
        """
        return {
            "event_id": self.event_id,
            "version": self.version,
            "schema_uri": self.schema_uri,
            "domain": self.domain.value,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "priority": self.priority.value,
            "workspace": self.workspace,
            "role": self.role,
            "data": dict(self.data),
            "correlation_context": dict(self.correlation_context),
        }

    @classmethod
    def from_audit_dict(cls, data: dict[str, Any]) -> Self:
        """Deserialize from audit dict (CloudEvents-compatible).

        Args:
            data: Dictionary representation from to_audit_dict().

        Returns:
            AuditEvent instance.
        """
        # Handle timestamp parsing
        ts = data.get("timestamp", "")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else datetime.now(timezone.utc)
        elif not ts:
            ts = datetime.now(timezone.utc)

        # Handle enum conversion
        domain = data.get("domain", "system")
        if isinstance(domain, str):
            domain = EventDomain(domain.lower())

        priority = data.get("priority", "info")
        if isinstance(priority, str):
            priority = AuditPriority[priority.upper()]

        return cls(
            event_id=data.get("event_id", uuid.uuid4().hex),
            version=data.get("version", "3.0"),
            schema_uri=data.get("schema_uri", "https://polaris.dev/schemas/audit/v3.0"),
            domain=domain,
            event_type=data.get("event_type", ""),
            timestamp=ts,
            trace_id=data.get("trace_id", ""),
            run_id=data.get("run_id", ""),
            span_id=data.get("span_id", ""),
            parent_span_id=data.get("parent_span_id", ""),
            priority=priority,
            workspace=data.get("workspace", ""),
            role=data.get("role", ""),
            data=dict(data.get("data", {})),
            correlation_context=dict(data.get("correlation_context", {})),
        )

    # -------------------------------------------------------------------------
    # Factory methods for common patterns
    # -------------------------------------------------------------------------

    def with_trace(
        self,
        trace_id: str,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> Self:
        """Create a new event with updated trace context.

        Args:
            trace_id: New trace ID.
            span_id: New span ID (generates if None).
            parent_span_id: Parent span ID (uses current span_id if None).

        Returns:
            New AuditEvent with updated trace context.
        """
        return self.model_copy(
            update={
                "trace_id": trace_id,
                "span_id": span_id or uuid.uuid4().hex[:16],
                "parent_span_id": parent_span_id or self.span_id,
            }
        )

    def with_priority(self, priority: AuditPriority) -> Self:
        """Create a new event with updated priority.

        Args:
            priority: New priority level.

        Returns:
            New AuditEvent with updated priority.
        """
        return self.model_copy(update={"priority": priority})

    def with_data(self, **kwargs: Any) -> Self:
        """Create a new event with merged data.

        Args:
            **kwargs: Data fields to merge.

        Returns:
            New AuditEvent with merged data.
        """
        new_data = dict(self.data)
        new_data.update(kwargs)
        return self.model_copy(update={"data": new_data})
