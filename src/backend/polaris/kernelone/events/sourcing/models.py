"""Event sourcing models shared by runtime event producers/consumers.

This module is intentionally KernelOne-only and business-agnostic.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_str

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _require_token(name: str, value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{name} must be a non-empty string")
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError(f"{name} contains invalid characters: {value!r}")
    return token


def _require_optional_token(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError(f"{name} contains invalid characters: {value!r}")
    return token


def _require_positive(name: str, value: int) -> int:
    coerced = int(value)
    if coerced < 1:
        raise ValueError(f"{name} must be >= 1")
    return coerced


def utc_now_iso() -> str:
    return utc_now_str()


def new_event_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class EventEnvelope:
    """Canonical versioned event envelope."""

    event_id: str
    stream: str
    event_type: str
    event_version: int
    seq: int
    occurred_at: str
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    aggregate_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_token("event_id", self.event_id))
        object.__setattr__(self, "stream", _require_token("stream", self.stream))
        object.__setattr__(self, "event_type", _require_token("event_type", self.event_type))
        object.__setattr__(self, "event_version", _require_positive("event_version", self.event_version))
        object.__setattr__(self, "seq", _require_positive("seq", self.seq))
        object.__setattr__(self, "source", _require_token("source", self.source))

        occurred_at = str(self.occurred_at or "").strip()
        if not occurred_at:
            raise ValueError("occurred_at must be a non-empty string")
        object.__setattr__(self, "occurred_at", occurred_at)

        payload_copy = dict(self.payload or {})
        metadata_copy = dict(self.metadata or {})
        object.__setattr__(self, "payload", payload_copy)
        object.__setattr__(self, "metadata", metadata_copy)
        object.__setattr__(
            self,
            "aggregate_id",
            _require_optional_token("aggregate_id", self.aggregate_id),
        )
        object.__setattr__(
            self,
            "correlation_id",
            _require_optional_token("correlation_id", self.correlation_id),
        )
        object.__setattr__(
            self,
            "causation_id",
            _require_optional_token("causation_id", self.causation_id),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "stream": self.stream,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "seq": self.seq,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "aggregate_id": self.aggregate_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> EventEnvelope:
        payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        return cls(
            event_id=str(record.get("event_id") or ""),
            stream=str(record.get("stream") or ""),
            event_type=str(record.get("event_type") or ""),
            event_version=int(record.get("event_version") or 1),
            seq=int(record.get("seq") or 0),
            occurred_at=str(record.get("occurred_at") or ""),
            source=str(record.get("source") or ""),
            aggregate_id=str(record.get("aggregate_id") or "").strip() or None,
            correlation_id=str(record.get("correlation_id") or "").strip() or None,
            causation_id=str(record.get("causation_id") or "").strip() or None,
            payload=dict(payload) if isinstance(payload, dict) else {},
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )


@dataclass(frozen=True)
class EventQueryResult:
    """Query result for an event stream slice."""

    stream: str
    storage_path: str
    events: tuple[EventEnvelope, ...]
    total: int
    next_offset: int


class EventSourcingError(RuntimeError):
    """Raised when event sourcing operations fail."""
