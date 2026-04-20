"""ContextEvent — Pydantic schema for context management audit.

Captures context operations:
- Prompt template rendering
- Context window utilization
- Memory read/write
- Context compaction triggers
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


class ContextOperation(str, Enum):
    """Context operation type."""

    READ = "read"
    WRITE = "write"
    RENDER = "render"
    COMPACT = "compact"
    TRUNCATE = "truncate"
    CLEAR = "clear"


class ContextEvent(AuditEvent, frozen=True):
    """Context management audit event.

    Tracks context operations for:
    - Context window utilization analysis
    - Memory pressure detection
    - Prompt template effectiveness
    - Compaction frequency optimization

    Attributes:
        operation: Type of context operation.
        template_name: Prompt template name if rendering.
        context_window_used: Tokens used in context window.
        context_window_limit: Maximum context window size.
        utilization_percent: Context window utilization %.
        memory_items: Number of items in context.
        compaction_triggered: Whether compaction was triggered.
        items_removed: Number of items removed during compaction.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    domain: EventDomain = Field(default=EventDomain.CONTEXT)
    event_type: str = Field(default="context_management")

    operation: ContextOperation = Field(default=ContextOperation.READ)
    template_name: str = Field(default="", max_length=128)
    context_window_used: int = Field(default=0, ge=0)
    context_window_limit: int = Field(default=0, ge=0)
    utilization_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    memory_items: int = Field(default=0, ge=0)
    compaction_triggered: bool = Field(default=False)
    items_removed: int = Field(default=0, ge=0)

    def to_audit_dict(self) -> dict[str, Any]:
        base = super().to_audit_dict()
        base.update(
            {
                "operation": self.operation.value,
                "template_name": self.template_name,
                "context_window_used": self.context_window_used,
                "context_window_limit": self.context_window_limit,
                "utilization_percent": self.utilization_percent,
                "memory_items": self.memory_items,
                "compaction_triggered": self.compaction_triggered,
                "items_removed": self.items_removed,
            }
        )
        return base

    @classmethod
    def from_audit_dict(cls, data: dict[str, Any]) -> Self:
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
            operation=ContextOperation(data.get("operation", "read").lower()),
            template_name=data.get("template_name", ""),
            context_window_used=data.get("context_window_used", 0),
            context_window_limit=data.get("context_window_limit", 0),
            utilization_percent=data.get("utilization_percent", 0.0),
            memory_items=data.get("memory_items", 0),
            compaction_triggered=data.get("compaction_triggered", False),
            items_removed=data.get("items_removed", 0),
            data=data.get("data", {}),
            correlation_context=data.get("correlation_context", {}),
        )

    @classmethod
    def create(
        cls,
        operation: ContextOperation,
        context_window_used: int = 0,
        context_window_limit: int = 0,
        template_name: str = "",
        role: str = "",
        workspace: str = "",
        trace_id: str = "",
        run_id: str = "",
        **kwargs: Any,
    ) -> Self:
        utilization = 0.0
        if context_window_limit > 0:
            utilization = (context_window_used / context_window_limit) * 100

        return cls(
            operation=operation,
            template_name=template_name,
            context_window_used=context_window_used,
            context_window_limit=context_window_limit,
            utilization_percent=utilization,
            role=role,
            workspace=workspace,
            trace_id=trace_id,
            run_id=run_id,
            **kwargs,
        )
