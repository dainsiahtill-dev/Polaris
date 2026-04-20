from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AppendEvidenceEventCommandV1:
    kind: str
    payload: dict[str, Any]


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
