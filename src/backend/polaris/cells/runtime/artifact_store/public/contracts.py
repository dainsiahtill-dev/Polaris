"""Public contracts for `runtime.artifact_store` cell."""

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
class WriteRuntimeArtifactCommandV1:
    workspace: str
    key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    content: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "key", _require_non_empty("key", self.key))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        if self.content is not None:
            object.__setattr__(self, "content", str(self.content))


@dataclass(frozen=True)
class ReadRuntimeArtifactQueryV1:
    workspace: str
    key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "key", _require_non_empty("key", self.key))


@dataclass(frozen=True)
class RuntimeV2ExportQueryV1:
    workspace: str | None = None

    def __post_init__(self) -> None:
        if self.workspace is not None:
            object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class RuntimeArtifactWrittenEventV1:
    event_id: str
    workspace: str
    key: str
    written_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "key", _require_non_empty("key", self.key))
        object.__setattr__(self, "written_at", _require_non_empty("written_at", self.written_at))


@dataclass(frozen=True)
class RuntimeArtifactResultV1:
    ok: bool
    workspace: str
    key: str
    value: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "key", _require_non_empty("key", self.key))
        object.__setattr__(self, "value", _to_dict_copy(self.value))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class RuntimeArtifactStoreError(RuntimeError):
    """Structured contract error for `runtime.artifact_store`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_artifact_store_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IRuntimeArtifactStoreService(Protocol):
    def write_artifact(self, command: WriteRuntimeArtifactCommandV1) -> RuntimeArtifactResultV1:
        """Write one runtime artifact."""

    def read_artifact(self, query: ReadRuntimeArtifactQueryV1) -> RuntimeArtifactResultV1:
        """Read one runtime artifact."""


__all__ = [
    "IRuntimeArtifactStoreService",
    "ReadRuntimeArtifactQueryV1",
    "RuntimeArtifactResultV1",
    "RuntimeArtifactStoreError",
    "RuntimeArtifactWrittenEventV1",
    "RuntimeV2ExportQueryV1",
    "WriteRuntimeArtifactCommandV1",
]
