"""ToolEvent — Pydantic schema for tool/function call audit.

Captures complete tool execution context:
- Input/output with sanitization before storage
- Execution timing (queue, wall-clock, CPU)
- Error classification and stack trace
- External API status codes
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Self

from polaris.kernelone.audit.omniscient.schemas.base import (
    AuditEvent,
    AuditPriority,
    EventDomain,
)
from pydantic import ConfigDict, Field


class ToolCategory(str, Enum):
    """Tool category for classification."""

    FILE = "file"  # File operations
    SEARCH = "search"  # Code search
    EXECUTION = "execution"  # Command execution
    API = "api"  # External API
    LLM = "llm"  # LLM invocation
    CONTEXT = "context"  # Context management
    MEMORY = "memory"  # Memory operations
    OTHER = "other"


class ToolEvent(AuditEvent, frozen=True):  # type: ignore[call-arg]  # frozen=True inherited from AuditEvent model_config; mypy flags redundant kwarg
    """Tool/function call audit event.

    Captures the full lifecycle of a tool call for:
    - Operation analysis (which tools are slow/failing)
    - Error pattern detection
    - External API health monitoring
    - Cost attribution (API calls, execution time)

    Attributes:
        tool_name: Canonical tool name (e.g., "repo_read_head").
        category: Tool category for grouping.
        input_args: Tool arguments (sanitized before storage).
        output_summary: Output summary or error message.
        status_code: HTTP status code for API calls, 0 for others.
        latency_ms: Wall-clock time in milliseconds.
        queue_latency_ms: Time spent waiting in queue.
        cpu_time_ms: CPU time consumed.
        error: Error message if failed.
        error_type: Error category (timeout, permission, not_found, etc.).
        exception_stack: Exception stack trace (sanitized).
        cache_hit: Whether result was served from cache.
        read_only: Whether tool is read-only.

    Example:
        event = ToolEvent(
            tool_name="repo_read_head",
            category=ToolCategory.FILE,
            input_args={"path": "/file.py", "limit": 50},
            output_summary="50 lines read",
            latency_ms=5.0,
            cache_hit=False,
            role="director",
        )
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    # -------------------------------------------------------------------------
    # Tool-specific classification (override base)
    # -------------------------------------------------------------------------

    domain: EventDomain = Field(default=EventDomain.TOOL)
    event_type: str = Field(default="tool_execution")

    # -------------------------------------------------------------------------
    # Tool identification
    # -------------------------------------------------------------------------

    tool_name: str = Field(
        default="",
        description="Canonical tool name",
        max_length=128,
    )

    category: ToolCategory = Field(
        default=ToolCategory.OTHER,
        description="Tool category for classification",
    )

    # -------------------------------------------------------------------------
    # Input/output
    # -------------------------------------------------------------------------

    input_args: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments (sanitized before storage)",
    )

    output_summary: str = Field(
        default="",
        description="Output summary or error message",
        max_length=1024,
    )

    # -------------------------------------------------------------------------
    # Timing
    # -------------------------------------------------------------------------

    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock time in milliseconds",
    )

    queue_latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Time spent waiting in queue",
    )

    cpu_time_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="CPU time consumed in milliseconds",
    )

    # -------------------------------------------------------------------------
    # Status and error
    # -------------------------------------------------------------------------

    status_code: int = Field(
        default=0,
        ge=0,
        le=999,
        description="HTTP status code for API calls, 0 for others",
    )

    error: str = Field(
        default="",
        description="Error message if failed",
        max_length=1024,
    )

    error_type: str = Field(
        default="",
        description="Error category",
        max_length=64,
    )

    exception_stack: str = Field(
        default="",
        description="Exception stack trace (sanitized)",
        max_length=4096,
    )

    # -------------------------------------------------------------------------
    # Cache and optimization
    # -------------------------------------------------------------------------

    cache_hit: bool = Field(
        default=False,
        description="Whether result was served from cache",
    )

    read_only: bool = Field(
        default=True,
        description="Whether tool is read-only",
    )

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    def _validate_input_args(self) -> None:
        """Validate input args are serializable."""
        # Input args should be JSON-serializable
        pass

    # -------------------------------------------------------------------------
    # Computed fields
    # -------------------------------------------------------------------------

    @property
    def total_latency_ms(self) -> float:
        """Total latency including queue time."""
        return self.latency_ms + self.queue_latency_ms

    @property
    def is_success(self) -> bool:
        """Whether the tool call succeeded."""
        return not self.error and not (self.status_code > 0 and self.status_code >= 400)

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to audit dict (extends base)."""
        base = super().to_audit_dict()
        base.update(
            {
                "domain": self.domain.value,
                "event_type": self.event_type,
                "tool_name": self.tool_name,
                "category": self.category.value,
                "input_args": self.input_args,
                "output_summary": self.output_summary,
                "status_code": self.status_code,
                "latency_ms": self.latency_ms,
                "queue_latency_ms": self.queue_latency_ms,
                "cpu_time_ms": self.cpu_time_ms,
                "error": self.error,
                "error_type": self.error_type,
                "exception_stack": self.exception_stack,
                "cache_hit": self.cache_hit,
                "read_only": self.read_only,
                "total_latency_ms": self.total_latency_ms,
                "is_success": self.is_success,
            }
        )
        return base

    @classmethod
    def from_audit_dict(cls, data: dict[str, Any]) -> Self:
        """Deserialize from audit dict."""
        category = data.get("category", "other")
        if isinstance(category, str):
            category = ToolCategory(category.lower())

        return cls(
            event_id=data.get("event_id", ""),
            version=data.get("version", "3.0"),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            trace_id=data.get("trace_id", ""),
            run_id=data.get("run_id", ""),
            span_id=data.get("span_id", ""),
            parent_span_id=data.get("parent_span_id", ""),
            priority=AuditPriority[data.get("priority", "info").upper()],
            workspace=data.get("workspace", ""),
            role=data.get("role", ""),
            tool_name=data.get("tool_name", ""),
            category=category,
            input_args=data.get("input_args", {}),
            output_summary=data.get("output_summary", ""),
            status_code=data.get("status_code", 0),
            latency_ms=data.get("latency_ms", 0.0),
            queue_latency_ms=data.get("queue_latency_ms", 0.0),
            cpu_time_ms=data.get("cpu_time_ms", 0.0),
            error=data.get("error", ""),
            error_type=data.get("error_type", ""),
            exception_stack=data.get("exception_stack", ""),
            cache_hit=data.get("cache_hit", False),
            read_only=data.get("read_only", True),
            data=data.get("data", {}),
            correlation_context=data.get("correlation_context", {}),
        )

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        tool_name: str,
        category: ToolCategory = ToolCategory.OTHER,
        input_args: dict[str, Any] | None = None,
        output_summary: str = "",
        latency_ms: float = 0.0,
        error: str = "",
        error_type: str = "",
        role: str = "",
        workspace: str = "",
        trace_id: str = "",
        run_id: str = "",
        **kwargs: Any,
    ) -> Self:
        """Factory to create a ToolEvent.

        Args:
            tool_name: Canonical tool name.
            category: Tool category.
            input_args: Tool arguments.
            output_summary: Output summary.
            latency_ms: Wall-clock time.
            error: Error message if failed.
            error_type: Error category.
            role: Emitting role.
            workspace: Workspace path.
            trace_id: Correlation ID.
            run_id: Session ID.
            **kwargs: Additional fields.

        Returns:
            ToolEvent instance.
        """
        return cls(
            tool_name=tool_name,
            category=category,
            input_args=input_args or {},
            output_summary=output_summary,
            latency_ms=latency_ms,
            error=error,
            error_type=error_type,
            role=role,
            workspace=workspace,
            trace_id=trace_id,
            run_id=run_id,
            **kwargs,
        )
