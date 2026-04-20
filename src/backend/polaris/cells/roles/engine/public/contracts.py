"""Public contracts for `roles.engine` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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
class ClassifyTaskQueryV1:
    task: str
    role: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task", _require_non_empty("task", self.task))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class SelectEngineCommandV1:
    workspace: str
    task: str
    role: str | None = None
    preferred_strategy: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task", _require_non_empty("task", self.task))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))
        if self.preferred_strategy is not None:
            object.__setattr__(
                self,
                "preferred_strategy",
                _require_non_empty("preferred_strategy", self.preferred_strategy),
            )
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class RegisterEngineCommandV1:
    strategy: str
    engine_class: str
    workspace: str | None = None
    defaults: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy", _require_non_empty("strategy", self.strategy))
        object.__setattr__(self, "engine_class", _require_non_empty("engine_class", self.engine_class))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "defaults", _to_dict_copy(self.defaults))


@dataclass(frozen=True)
class EngineRegistrySnapshotQueryV1:
    include_instances: bool = False


@dataclass(frozen=True)
class EngineSelectedEventV1:
    event_id: str
    workspace: str
    role: str
    strategy: str
    selected_at: str
    task: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "strategy", _require_non_empty("strategy", self.strategy))
        object.__setattr__(self, "selected_at", _require_non_empty("selected_at", self.selected_at))
        if self.task is not None:
            object.__setattr__(self, "task", _require_non_empty("task", self.task))


@dataclass(frozen=True)
class EngineSelectionResultV1:
    ok: bool
    status: str
    strategy: str
    engine_class: str | None = None
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "strategy", _require_non_empty("strategy", self.strategy))
        object.__setattr__(self, "reason", str(self.reason))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.engine_class is not None:
            object.__setattr__(self, "engine_class", _require_non_empty("engine_class", self.engine_class))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class EngineExecutionResultV1:
    ok: bool
    status: str
    strategy: str
    final_answer: str
    total_steps: int = 0
    total_tool_calls: int = 0
    execution_time_seconds: float = 0.0
    termination_reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "strategy", _require_non_empty("strategy", self.strategy))
        object.__setattr__(self, "final_answer", str(self.final_answer))
        object.__setattr__(self, "termination_reason", str(self.termination_reason))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.total_steps < 0 or self.total_tool_calls < 0 or self.execution_time_seconds < 0:
            raise ValueError("execution counters must be >= 0")
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class EngineRegistrySnapshotResultV1:
    strategies: tuple[str, ...] = field(default_factory=tuple)
    registered_count: int = 0
    cached_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "strategies",
            tuple(str(item).strip() for item in self.strategies if str(item).strip()),
        )
        if self.registered_count < 0 or self.cached_count < 0:
            raise ValueError("counts must be >= 0")


class RolesEngineError(RuntimeError):
    """Structured contract error for `roles.engine`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "roles_engine_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IRoleEngineService(Protocol):
    def classify_task(self, query: ClassifyTaskQueryV1) -> str:
        """Classify one task and return a strategy name."""

    def select_engine(self, command: SelectEngineCommandV1) -> EngineSelectionResultV1:
        """Select one engine for execution."""

    def get_registry_snapshot(
        self,
        query: EngineRegistrySnapshotQueryV1,
    ) -> EngineRegistrySnapshotResultV1:
        """Inspect registry state."""


__all__ = [
    "ClassifyTaskQueryV1",
    "EngineExecutionResultV1",
    "EngineRegistrySnapshotQueryV1",
    "EngineRegistrySnapshotResultV1",
    "EngineSelectedEventV1",
    "EngineSelectionResultV1",
    "IRoleEngineService",
    "RegisterEngineCommandV1",
    "RolesEngineError",
    "SelectEngineCommandV1",
]
