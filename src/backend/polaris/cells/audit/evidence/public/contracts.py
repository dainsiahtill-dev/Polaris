from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any

MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1 = "runtime/evidence/managed_process_receipts.jsonl"


def _normalize_workspace(value: str) -> str:
    workspace = str(value or "").strip()
    if not workspace:
        raise ValueError("workspace must be a non-empty string")
    path = Path(workspace).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"workspace must be an existing directory: {path}")
    return str(path)


def _require_lower_sha256(name: str, value: str) -> str:
    digest = str(value or "").strip()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return digest


def _freeze_json_value(value: object, *, path: str) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise TypeError(f"{path} keys must be non-empty exact strings")
            frozen[key] = _freeze_json_value(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, path=f"{path}[]") for item in value)
    raise TypeError(f"{path} must contain only deterministic JSON values")


def _freeze_receipt(value: Mapping[str, Any]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise TypeError("receipt must be a non-empty mapping")
    frozen = _freeze_json_value(value, path="receipt")
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise TypeError("receipt must be a mapping")
    return frozen


def _require_receipt_ref(value: str, *, receipt_hash: str) -> str:
    receipt_ref = str(value or "").strip()
    expected = f"{MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1}#{receipt_hash}"
    if receipt_ref != expected:
        raise ValueError("receipt_ref must bind the managed-process receipt hash")
    return receipt_ref


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


@dataclass(frozen=True, slots=True)
class PersistManagedProcessReceiptCommandV1:
    """Request durable receipt ownership without trusting caller identity."""

    workspace: str
    receipt: Mapping[str, Any]
    claimed_receipt_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        object.__setattr__(self, "receipt", _freeze_receipt(self.receipt))
        if self.claimed_receipt_hash is not None:
            object.__setattr__(
                self,
                "claimed_receipt_hash",
                _require_lower_sha256("claimed_receipt_hash", self.claimed_receipt_hash),
            )


@dataclass(frozen=True, slots=True)
class ReadManagedProcessReceiptQueryV1:
    """Read one workspace-scoped receipt by its owner-calculated hash."""

    workspace: str
    receipt_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        object.__setattr__(self, "receipt_hash", _require_lower_sha256("receipt_hash", self.receipt_hash))


@dataclass(frozen=True, slots=True)
class ManagedProcessReceiptPersistResultV1:
    """Content-addressed append outcome."""

    workspace: str
    receipt_ref: str
    receipt_hash: str
    already_present: bool

    def __post_init__(self) -> None:
        receipt_hash = _require_lower_sha256("receipt_hash", self.receipt_hash)
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        object.__setattr__(self, "receipt_hash", receipt_hash)
        object.__setattr__(self, "receipt_ref", _require_receipt_ref(self.receipt_ref, receipt_hash=receipt_hash))
        if type(self.already_present) is not bool:
            raise TypeError("already_present must be an exact bool")


@dataclass(frozen=True, slots=True)
class ManagedProcessReceiptRecordV1:
    """Immutable full receipt body reconstructed from durable evidence."""

    workspace: str
    receipt_ref: str
    receipt_hash: str
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        receipt_hash = _require_lower_sha256("receipt_hash", self.receipt_hash)
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        object.__setattr__(self, "receipt_hash", receipt_hash)
        object.__setattr__(self, "receipt_ref", _require_receipt_ref(self.receipt_ref, receipt_hash=receipt_hash))
        object.__setattr__(self, "receipt", _freeze_receipt(self.receipt))


__all__ = [
    "MANAGED_PROCESS_RECEIPT_LOGICAL_PATH_V1",
    "AppendEvidenceEventCommandV1",
    "EvidenceAppendedEventV1",
    "EvidenceAuditError",
    "EvidenceQueryResultV1",
    "EvidenceVerificationResultV1",
    "ManagedProcessReceiptPersistResultV1",
    "ManagedProcessReceiptRecordV1",
    "PersistManagedProcessReceiptCommandV1",
    "QueryEvidenceEventsV1",
    "ReadManagedProcessReceiptQueryV1",
    "VerifyEvidenceChainV1",
]
