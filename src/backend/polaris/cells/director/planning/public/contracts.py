"""Public contracts for director.planning cell.

These contracts define the stable public interface for Director task planning,
main execution loop, risk/quality tracking, and context gathering.

All symbols here should be imported by external consumers (Facade, other Cells).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanDirectorTaskCommandV1:
    """Command to invoke Director planning for a task."""

    task_id: str
    workspace: str
    instruction: str
    run_id: str | None = None
    attempt: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "instruction", _require_non_empty("instruction", self.instruction))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GetDirectorStatusQueryV1:
    """Query Director planning/status information."""

    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectorPlanningResultV1:
    """Result of Director planning operation."""

    ok: bool
    task_id: str
    workspace: str
    status: str
    run_id: str | None = None
    plan_summary: str = ""
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DirectorPlanningError(RuntimeError):
    """Raised when director.planning contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_planning_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "DirectorPlanningError",
    "DirectorPlanningResultV1",
    "GetDirectorStatusQueryV1",
    "PlanDirectorTaskCommandV1",
]
