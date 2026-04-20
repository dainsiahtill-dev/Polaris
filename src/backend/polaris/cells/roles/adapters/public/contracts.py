"""Public contracts for `roles.adapters` cell."""

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
class CreateRoleAdapterCommandV1:
    role_id: str
    workspace: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class ListSupportedRoleAdaptersQueryV1:
    workspace: str | None = None

    def __post_init__(self) -> None:
        if self.workspace is not None:
            object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class RoleAdapterRegisteredEventV1:
    event_id: str
    role_id: str
    adapter_type: str
    registered_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "adapter_type", _require_non_empty("adapter_type", self.adapter_type))
        object.__setattr__(self, "registered_at", _require_non_empty("registered_at", self.registered_at))


@dataclass(frozen=True)
class RoleAdapterResultV1:
    ok: bool
    role_id: str
    adapter_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "adapter_type", _require_non_empty("adapter_type", self.adapter_type))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class RoleAdaptersError(RuntimeError):
    """Structured contract error for `roles.adapters`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "roles_adapters_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IRoleAdaptersService(Protocol):
    def create_adapter(self, command: CreateRoleAdapterCommandV1) -> IRoleAdapter:
        """Create one role adapter instance."""

    def list_supported_roles(self, query: ListSupportedRoleAdaptersQueryV1) -> tuple[str, ...]:
        """List supported adapter role IDs."""


@runtime_checkable
class IRoleAdapter(Protocol):
    @property
    def role_id(self) -> str:
        """Return the adapter role id."""

    def get_capabilities(self) -> list[str]:
        """Return supported capabilities."""

    async def execute(
        self,
        task_id: str,
        input_data: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Execute one adapter task."""


__all__ = [
    "CreateRoleAdapterCommandV1",
    "IRoleAdapter",
    "IRoleAdaptersService",
    "ListSupportedRoleAdaptersQueryV1",
    "RoleAdapterRegisteredEventV1",
    "RoleAdapterResultV1",
    "RoleAdaptersError",
]
