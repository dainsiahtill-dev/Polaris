"""Public contracts for `roles.profile` cell."""

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
class RegisterRoleProfileCommandV1:
    profile: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", _to_dict_copy(self.profile))
        if not self.profile.get("role_id"):
            raise ValueError("profile.role_id must be set")


@dataclass(frozen=True)
class LoadRoleProfilesCommandV1:
    filepath: str
    format: str = "yaml"

    def __post_init__(self) -> None:
        object.__setattr__(self, "filepath", _require_non_empty("filepath", self.filepath))
        object.__setattr__(self, "format", _require_non_empty("format", self.format).lower())


@dataclass(frozen=True)
class SaveRoleProfilesCommandV1:
    filepath: str
    format: str = "yaml"

    def __post_init__(self) -> None:
        object.__setattr__(self, "filepath", _require_non_empty("filepath", self.filepath))
        object.__setattr__(self, "format", _require_non_empty("format", self.format).lower())


@dataclass(frozen=True)
class GetRoleProfileQueryV1:
    role_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))


@dataclass(frozen=True)
class ListRoleProfilesQueryV1:
    include_loaded_files: bool = False


@dataclass(frozen=True)
class RoleProfileRegisteredEventV1:
    event_id: str
    role_id: str
    registered_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "registered_at", _require_non_empty("registered_at", self.registered_at))


@dataclass(frozen=True)
class RoleProfilesLoadedEventV1:
    event_id: str
    filepath: str
    loaded_count: int
    loaded_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "filepath", _require_non_empty("filepath", self.filepath))
        object.__setattr__(self, "loaded_at", _require_non_empty("loaded_at", self.loaded_at))
        if self.loaded_count < 0:
            raise ValueError("loaded_count must be >= 0")


@dataclass(frozen=True)
class RoleProfileResultV1:
    ok: bool
    role_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class RoleProfilesResultV1:
    ok: bool
    profiles: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    loaded_files: tuple[str, ...] = field(default_factory=tuple)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", tuple(dict(item) for item in self.profiles))
        object.__setattr__(
            self, "loaded_files", tuple(str(item).strip() for item in self.loaded_files if str(item).strip())
        )
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class RoleProfileError(RuntimeError):
    """Structured contract error for `roles.profile`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "roles_profile_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IRoleProfileService(Protocol):
    def register_profile(self, command: RegisterRoleProfileCommandV1) -> RoleProfileResultV1:
        """Register one profile."""

    def load_profiles(self, command: LoadRoleProfilesCommandV1) -> RoleProfilesResultV1:
        """Load profiles from file."""

    def save_profiles(self, command: SaveRoleProfilesCommandV1) -> RoleProfilesResultV1:
        """Persist profiles to file."""

    def get_profile(self, query: GetRoleProfileQueryV1) -> RoleProfileResultV1:
        """Get one profile."""

    def list_profiles(self, query: ListRoleProfilesQueryV1) -> RoleProfilesResultV1:
        """List profiles."""


__all__ = [
    "GetRoleProfileQueryV1",
    "IRoleProfileService",
    "ListRoleProfilesQueryV1",
    "LoadRoleProfilesCommandV1",
    "RegisterRoleProfileCommandV1",
    "RoleProfileError",
    "RoleProfileRegisteredEventV1",
    "RoleProfileResultV1",
    "RoleProfilesLoadedEventV1",
    "RoleProfilesResultV1",
    "SaveRoleProfilesCommandV1",
]
