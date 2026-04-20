from __future__ import annotations

from dataclasses import dataclass
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
class RefreshStorageLayoutCommandV1:
    workspace: str
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class ResolveStorageLayoutQueryV1:
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class ResolveRuntimePathQueryV1:
    workspace: str
    relative_path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "relative_path", _require_non_empty("relative_path", self.relative_path))


@dataclass(frozen=True)
class ResolveWorkspacePathQueryV1:
    workspace: str
    relative_path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "relative_path", _require_non_empty("relative_path", self.relative_path))


@dataclass(frozen=True)
class StorageLayoutResolvedEventV1:
    event_id: str
    workspace: str
    runtime_root: str
    resolved_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "runtime_root", _require_non_empty("runtime_root", self.runtime_root))
        object.__setattr__(self, "resolved_at", _require_non_empty("resolved_at", self.resolved_at))


@dataclass(frozen=True)
class StorageLayoutResultV1:
    workspace: str
    runtime_root: str
    history_root: str
    meta_root: str
    extras: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "runtime_root", _require_non_empty("runtime_root", self.runtime_root))
        object.__setattr__(self, "history_root", _require_non_empty("history_root", self.history_root))
        object.__setattr__(self, "meta_root", _require_non_empty("meta_root", self.meta_root))
        object.__setattr__(self, "extras", _to_dict_copy(self.extras))


class StorageLayoutErrorV1(RuntimeError):
    """Raised when ``storage.layout`` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "storage_layout_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Backward-compat alias — prefer StorageLayoutErrorV1 in new code.
StorageLayoutError = StorageLayoutErrorV1


__all__ = [
    "RefreshStorageLayoutCommandV1",
    "ResolveRuntimePathQueryV1",
    "ResolveStorageLayoutQueryV1",
    "ResolveWorkspacePathQueryV1",
    "StorageLayoutError",
    "StorageLayoutErrorV1",
    "StorageLayoutResolvedEventV1",
    "StorageLayoutResultV1",
]
