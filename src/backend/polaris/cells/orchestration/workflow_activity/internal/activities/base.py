"""Base activity primitives for workflow_activity Cell workers.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/base.py

ACGA 2.0: This module lives in the Cell's internal/ and is imported only by
other workflow_activity internal modules.  It must NOT be imported by other Cells
without going through the public contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_REGISTERED_ACTIVITIES: dict[str, Callable[..., Awaitable[Any]]] = {}


def register_activity(
    name: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Register an activity implementation for future worker bootstrap."""

    def _decorator(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        _REGISTERED_ACTIVITIES[str(name or "").strip()] = handler
        return handler

    return _decorator


def get_registered_activity(name: str) -> Callable[..., Awaitable[Any]] | None:
    """Return a registered activity implementation by its public name."""
    return _REGISTERED_ACTIVITIES.get(str(name or "").strip())


def list_registered_activities() -> dict[str, Callable[..., Awaitable[Any]]]:
    """Return a copy of all registered activity handlers."""
    return dict(_REGISTERED_ACTIVITIES)


@dataclass(frozen=True)
class ActivityExecutionContext:
    """Minimal execution context passed into activity adapters."""

    workspace: str
    run_id: str = ""
    task_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityExecutionResult:
    """Serializable activity result payload."""

    success: bool
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    error_code: str | None = None
    step_title: str = ""
    step_detail: str = ""
    changed_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": self.success,
            "summary": self.summary,
            "payload": dict(self.payload),
            "errors": list(self.errors),
        }
        if self.error_code:
            result["error_code"] = self.error_code
        if self.step_title:
            result["step_title"] = self.step_title
        if self.step_detail:
            result["step_detail"] = self.step_detail
        if self.changed_files:
            result["changed_files"] = list(self.changed_files)
        return result
