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
class EvaluatePermissionCommandV1:
    role: str
    action: str
    resource: str
    workspace: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "resource", _require_non_empty("resource", self.resource))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class QueryPermissionMatrixV1:
    role: str
    workspace: str
    include_inherited: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class PermissionDeniedEventV1:
    event_id: str
    role: str
    action: str
    resource: str
    reason: str
    occurred_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "resource", _require_non_empty("resource", self.resource))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))


@dataclass(frozen=True)
class PermissionDecisionResultV1:
    allowed: bool
    role: str
    action: str
    resource: str
    reason: str = ""
    matched_policy: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "resource", _require_non_empty("resource", self.resource))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


class PermissionPolicyError(RuntimeError):
    """Raised when `policy.permission` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "permission_policy_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "EvaluatePermissionCommandV1",
    "PermissionDecisionResultV1",
    "PermissionDeniedEventV1",
    "PermissionPolicyError",
    "QueryPermissionMatrixV1",
]
