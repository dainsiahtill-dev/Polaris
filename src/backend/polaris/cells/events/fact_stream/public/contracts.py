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
class AppendFactEventCommandV1:
    workspace: str
    stream: str
    event_type: str
    payload: Mapping[str, Any]
    source: str
    run_id: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        object.__setattr__(self, "event_type", _require_non_empty("event_type", self.event_type))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        payload = _to_dict_copy(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class QueryFactEventsV1:
    workspace: str
    stream: str
    limit: int = 100
    offset: int = 0
    event_type: str | None = None
    run_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True)
class FactEventAppendedV1:
    event_id: str
    workspace: str
    stream: str
    storage_path: str
    appended_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        object.__setattr__(self, "storage_path", _require_non_empty("storage_path", self.storage_path))
        object.__setattr__(self, "appended_at", _require_non_empty("appended_at", self.appended_at))


@dataclass(frozen=True)
class FactStreamQueryResultV1:
    workspace: str
    stream: str
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    total: int = 0
    next_offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        object.__setattr__(self, "events", tuple(dict(v) for v in self.events))
        if self.total < 0:
            raise ValueError("total must be >= 0")
        if self.next_offset < 0:
            raise ValueError("next_offset must be >= 0")


class FactStreamError(RuntimeError):
    """Raised when `events.fact_stream` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "fact_stream_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "AppendFactEventCommandV1",
    "FactEventAppendedV1",
    "FactStreamError",
    "FactStreamQueryResultV1",
    "QueryFactEventsV1",
]
