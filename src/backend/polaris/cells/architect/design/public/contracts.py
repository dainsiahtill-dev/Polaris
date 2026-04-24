"""Public contracts for `architect.design`."""

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
class GenerateArchitectureDesignCommandV1:
    workspace: str
    objective: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "constraints", _to_dict_copy(self.constraints))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class QueryArchitectureDesignStatusV1:
    workspace: str
    design_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.design_id is not None:
            object.__setattr__(self, "design_id", _require_non_empty("design_id", self.design_id))


@dataclass(frozen=True)
class ArchitectureDesignGeneratedEventV1:
    event_id: str
    workspace: str
    design_id: str
    output_path: str
    generated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "design_id", _require_non_empty("design_id", self.design_id))
        object.__setattr__(self, "output_path", _require_non_empty("output_path", self.output_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class ArchitectureDesignResultV1:
    ok: bool
    workspace: str
    design_id: str
    status: str
    summary: str = ""
    recommendation_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "design_id", _require_non_empty("design_id", self.design_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(
            self,
            "recommendation_paths",
            tuple(str(v) for v in self.recommendation_paths if str(v).strip()),
        )


class ArchitectDesignErrorV1(RuntimeError):  # noqa: N818
    """Raised when `architect.design` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "architect_design_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Backward-compatible alias — do not remove; external consumers may still import the old name.
ArchitectDesignError = ArchitectDesignErrorV1

__all__ = [
    "ArchitectDesignError",
    "ArchitectDesignErrorV1",
    "ArchitectureDesignGeneratedEventV1",
    "ArchitectureDesignResultV1",
    "GenerateArchitectureDesignCommandV1",
    "QueryArchitectureDesignStatusV1",
]
