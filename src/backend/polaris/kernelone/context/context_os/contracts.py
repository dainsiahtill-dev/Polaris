"""Public ContextOS contracts for kernelone.core.

These are boundary-facing contracts. They intentionally avoid role-specific
semantics so business Cells can depend on KernelOne ContextOS without importing
internal pipeline objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .decision_log import ProjectionReport
from .policies import ContextWindowPolicy


@dataclass(frozen=True, slots=True)
class ReceiptRef:
    """Reference to a durable or in-memory ContextOS receipt."""

    receipt_id: str
    receipt_type: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "receipt_type": self.receipt_type,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    """Request to project session state for one role/model binding."""

    messages: tuple[dict[str, Any], ...]
    role_id: str = ""
    provider_id: str = ""
    model: str = ""
    recent_window_messages: int = 8
    focus: str = ""
    context_window_policy: ContextWindowPolicy = field(default_factory=ContextWindowPolicy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [dict(item) for item in self.messages],
            "role_id": self.role_id,
            "provider_id": self.provider_id,
            "model": self.model,
            "recent_window_messages": self.recent_window_messages,
            "focus": self.focus,
            "context_window_policy": {
                "model_context_window": self.context_window_policy.model_context_window,
                "default_history_window_messages": self.context_window_policy.default_history_window_messages,
                "max_active_window_messages": self.context_window_policy.max_active_window_messages,
            },
        }


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Result contract for a ContextOS projection."""

    projection_id: str
    context_result_id: str
    messages: tuple[dict[str, Any], ...] = ()
    receipt_refs: tuple[ReceiptRef, ...] = ()
    report: ProjectionReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "context_result_id": self.context_result_id,
            "messages": [dict(item) for item in self.messages],
            "receipt_refs": [item.to_dict() for item in self.receipt_refs],
            "report": self.report.to_dict() if self.report is not None else None,
        }


__all__ = [
    "ContextWindowPolicy",
    "ProjectionReport",
    "ProjectionRequest",
    "ProjectionResult",
    "ReceiptRef",
]
