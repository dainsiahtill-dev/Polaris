from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class BuildRoleContextCommandV1:
    role_id: str
    objective: str


@dataclass(frozen=True)
class ResolveRoleContextQueryV1:
    role_id: str
    limit: int = 8


@dataclass(frozen=True)
class RoleContextResultV1:
    context_items: tuple[str, ...]
    source_cells: tuple[str, ...]


@dataclass(frozen=True)
class ContextResolvedEventV1:
    role_id: str
    source_cells: tuple[str, ...]


@dataclass(frozen=True)
class QueryFinalProviderRequestAuditV1:
    workspace: str
    context_snapshot_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "context_snapshot_ref",
            _require_non_empty("context_snapshot_ref", self.context_snapshot_ref),
        )


@dataclass(frozen=True)
class QueryFactoryRunContextSnapshotsV1:
    workspace: str
    factory_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )


@dataclass(frozen=True)
class FactoryRunContextSnapshotsResultV1:
    ok: bool
    status: str
    workspace: str
    factory_run_id: str
    pins: tuple[Mapping[str, Any], ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )
        object.__setattr__(self, "pins", tuple(dict(pin) for pin in self.pins))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class FinalProviderRequestAuditResultV1:
    ok: bool
    status: str
    workspace: str
    context_snapshot_ref: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "context_snapshot_ref",
            _require_non_empty("context_snapshot_ref", self.context_snapshot_ref),
        )
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class ContextEngineError(Exception):
    """Raised when graph-constrained context assembly fails."""


__all__ = [
    "BuildRoleContextCommandV1",
    "ContextEngineError",
    "ContextResolvedEventV1",
    "FactoryRunContextSnapshotsResultV1",
    "FinalProviderRequestAuditResultV1",
    "QueryFactoryRunContextSnapshotsV1",
    "QueryFinalProviderRequestAuditV1",
    "ResolveRoleContextQueryV1",
    "RoleContextResultV1",
]
