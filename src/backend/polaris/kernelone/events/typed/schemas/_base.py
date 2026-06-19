"""Shared foundation for typed event schemas.

Contains the symbols imported first by every event leaf module:
``EventCategory``, ``EventPayload``, ``EventBase`` and ``ToolErrorKind``.

These bodies are moved verbatim from the original
``polaris/kernelone/events/typed/schemas.py`` module to preserve behavior.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Event Category Enum (for wildcard subscription)
# =============================================================================


class EventCategory(str, Enum):
    """Event categories for wildcard subscription patterns.

    Note: This enum is intentionally separate from ErrorCategory in
    polaris/kernelone/errors.py. EventCategory classifies events for
    subscription/filtering purposes (lifecycle, tool, turn, etc.),
    while ErrorCategory classifies errors for handling/routing purposes
    (provider_error, timeout, validation, etc.).
    """

    LIFECYCLE = "lifecycle"  # Instance, session lifecycle
    TOOL = "tool"  # Tool execution
    TURN = "turn"  # Turn engine
    DIRECTOR = "director"  # Director execution
    CONTEXT = "context"  # Context management
    AUDIT = "audit"  # Audit events
    AUDIT_EXTENDED = "audit_extended"  # Extended audit events (LLM, tool, task, etc.)
    SYSTEM = "system"  # System events
    COGNITIVE = "cognitive"  # Cognitive life form events (thinking, reflection, evolution)


# =============================================================================
# Base Event Model
# =============================================================================


class EventPayload(BaseModel):
    """Base class for all event payloads."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class EventBase(BaseModel):
    """Base class for all typed events.

    Attributes:
        event_id: Unique event identifier (UUID)
        event_name: Event type name (discriminator)
        event_version: Schema version for evolution
        category: Event category for pattern matching
        timestamp: Event timestamp (UTC)
        run_id: Run identifier for correlation
        workspace: Workspace path
        correlation_id: Optional correlation ID for tracing
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        frozen=True,
        use_enum_values=True,
    )

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_name: str = Field(..., description="Event type name (discriminator)")
    event_version: int = Field(default=1, ge=1, description="Schema version")
    category: EventCategory = Field(..., description="Event category")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str = Field(default="", description="Run identifier")
    workspace: str = Field(default="", description="Workspace path")
    correlation_id: str | None = Field(default=None, description="Correlation ID for tracing")


class ToolErrorKind(str, Enum):
    """Classification of tool errors."""

    EXCEPTION = "exception"  # Unhandled exception
    VALIDATION = "validation"  # Invalid arguments
    PERMISSION = "permission"  # Permission denied
    NOT_FOUND = "not_found"  # Tool not found
    RUNTIME = "runtime"  # Runtime error
    TIMEOUT = "timeout"  # Execution timeout
    CANCELLED = "cancelled"  # Execution cancelled
    UNKNOWN = "unknown"  # Unknown error
