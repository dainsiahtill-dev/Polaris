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


@dataclass(frozen=True)
class GenerateTaskBlueprintCommandV1:
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "constraints", _to_dict_copy(self.constraints))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class GetBlueprintStatusQueryV1:
    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class TaskBlueprintGeneratedEventV1:
    event_id: str
    task_id: str
    workspace: str
    blueprint_path: str
    generated_at: str
    risk_level: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "blueprint_path", _require_non_empty("blueprint_path", self.blueprint_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class TaskBlueprintResultV1:
    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_path: str | None = None
    summary: str = ""
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "recommendations", tuple(str(v) for v in self.recommendations))
        object.__setattr__(self, "risks", tuple(str(v) for v in self.risks))


class ChiefEngineerBlueprintErrorV1(RuntimeError):  # noqa: N818
    """Raised when `chief_engineer.blueprint` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "chief_engineer_blueprint_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Backward-compatible alias — do not remove; external consumers may still import the old name.
ChiefEngineerBlueprintError = ChiefEngineerBlueprintErrorV1

__all__ = [
    "ChiefEngineerBlueprintError",
    "ChiefEngineerBlueprintErrorV1",
    "GenerateTaskBlueprintCommandV1",
    "GetBlueprintStatusQueryV1",
    "TaskBlueprintGeneratedEventV1",
    "TaskBlueprintResultV1",
]
