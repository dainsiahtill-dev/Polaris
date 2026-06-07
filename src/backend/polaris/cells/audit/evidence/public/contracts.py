from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AppendEvidenceEventCommandV1:
    kind: str
    payload: Mapping[str, Any]
    workspace: str = "."
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip()
        if not kind:
            raise ValueError("kind must be a non-empty string")
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class QueryEvidenceEventsV1:
    limit: int = 50


@dataclass(frozen=True)
class VerifyEvidenceChainV1:
    start_at: str | None = None


@dataclass(frozen=True)
class EvidenceQueryResultV1:
    events: tuple[dict[str, Any], ...]
    total: int


@dataclass(frozen=True)
class EvidenceVerificationResultV1:
    ok: bool
    checked_events: int


@dataclass(frozen=True)
class EvidenceAppendedEventV1:
    kind: str
    receipt_path: str


class EvidenceAuditError(Exception):
    """Raised when evidence append or verification fails."""


__all__ = [
    "AppendEvidenceEventCommandV1",
    "EvidenceAppendedEventV1",
    "EvidenceAuditError",
    "EvidenceQueryResultV1",
    "EvidenceVerificationResultV1",
    "QueryEvidenceEventsV1",
    "VerifyEvidenceChainV1",
]
