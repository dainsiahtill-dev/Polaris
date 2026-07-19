from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from polaris.kernelone.events.sourcing.guarded import (
    AppendIfGuardedSnapshotCommandV1,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotProofV1,
    GuardedFactSnapshotV1,
    ReadGuardedFactSnapshotCommandV1,
)
from polaris.kernelone.fs.contracts import DurabilityMode, validate_durability


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _detach_error_detail_value(value: Any) -> Any:
    """Return a recursively detached JSON-compatible public error value."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _detach_error_detail_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_detach_error_detail_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_detach_error_detail_value(item) for item in value)
    return str(value)


def _detach_error_details(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Detach details according to the public JSON serialization policy."""

    if payload is None:
        return {}
    return {str(key): _detach_error_detail_value(value) for key, value in payload.items()}


def _optional_text(value: object) -> str | None:
    return str(value or "").strip() or None


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256_hex(name: str, value: str) -> str:
    normalized = _require_non_empty(name, value)
    if _SHA256_HEX_RE.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return normalized


def _require_int(name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an int >= {minimum}")
    return value


def _require_exact_non_empty(name: str, value: object) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be an exact non-empty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be an exact non-empty string")
    return normalized


def _require_exact_sha256_hex(name: str, value: object) -> str:
    normalized = _require_exact_non_empty(name, value)
    if _SHA256_HEX_RE.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return normalized


def _require_exact_int(name: str, value: object, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an exact int >= {minimum}")
    return value


def _optional_exact_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_exact_non_empty(name, value)


@dataclass(frozen=True)
class FactStreamProvenanceV1:
    """Typed identity for one durable role-transition fact.

    ``transition_id`` identifies the logical state transition. It remains
    stable when that transition is retried, while a new execution must mint a
    different transition identity before it appends a new fact.
    """

    workspace: str
    run_id: str
    task_id: str
    turn_id: str
    transition_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "turn_id", _require_non_empty("turn_id", self.turn_id))
        object.__setattr__(self, "transition_id", _require_non_empty("transition_id", self.transition_id))

    def to_record(self) -> dict[str, str]:
        """Return a detached JSON-serializable provenance record."""

        return {
            "workspace": self.workspace,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "transition_id": self.transition_id,
        }


@dataclass(frozen=True)
class AppendFactEventCommandV1:
    """Append one FactStream event with optional strictness and durability."""

    workspace: str
    stream: str
    event_type: str
    payload: Mapping[str, Any]
    source: str
    run_id: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None
    provenance: FactStreamProvenanceV1 | None = None
    idempotency_key: str | None = None
    expected_seq: int | None = None
    durability: DurabilityMode = "buffered"
    strict_integrity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        object.__setattr__(self, "event_type", _require_non_empty("event_type", self.event_type))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        payload = _to_dict_copy(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "run_id", _optional_text(self.run_id))
        object.__setattr__(self, "task_id", _optional_text(self.task_id))
        object.__setattr__(self, "correlation_id", _optional_text(self.correlation_id))
        if self.provenance is not None and not isinstance(self.provenance, FactStreamProvenanceV1):
            raise ValueError("provenance must be FactStreamProvenanceV1 or None")
        idempotency_key = _optional_text(self.idempotency_key)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        # Coerce and validate expected_seq. None is the default (no CAS) and
        # preserves the historic append semantics — only when callers opt in
        # do we enforce the sequence contract. bool is rejected explicitly
        # because it's a subclass of int but never a meaningful seq.
        expected = self.expected_seq
        if expected is not None:
            if isinstance(expected, bool) or not isinstance(expected, int):
                raise ValueError("expected_seq must be an int or None")
            if expected < 1:
                raise ValueError("expected_seq must be >= 1")
            object.__setattr__(self, "expected_seq", int(expected))
        object.__setattr__(self, "durability", validate_durability(self.durability))
        if not isinstance(self.strict_integrity, bool):
            raise ValueError("strict_integrity must be a bool")


@dataclass(frozen=True)
class QueryFactEventsV1:
    """Read FactStream events; strict reads fail closed on ambiguity."""

    workspace: str
    stream: str
    limit: int = 100
    offset: int = 0
    event_type: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    strict_integrity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")
        if not isinstance(self.strict_integrity, bool):
            raise ValueError("strict_integrity must be a bool")


@dataclass(frozen=True)
class QueryFactStreamHeadV1:
    """Read-only query for one stream's durable CAS cursor."""

    workspace: str
    stream: str
    strict_integrity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        if not isinstance(self.strict_integrity, bool):
            raise ValueError("strict_integrity must be a bool")


@dataclass(frozen=True)
class EnsureSegmentedFactLedgerCommandV1:
    """Enroll and validate one dynamic segmented authority ledger."""

    workspace: str
    logical_stream: str
    maintenance_reason: str
    retention: Literal["pinned_audit_no_delete"] = "pinned_audit_no_delete"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        object.__setattr__(
            self,
            "maintenance_reason",
            _require_exact_non_empty("maintenance_reason", self.maintenance_reason),
        )
        if type(self.retention) is not str or self.retention != "pinned_audit_no_delete":
            raise ValueError("segmented fact ledgers require pinned_audit_no_delete retention")


@dataclass(frozen=True)
class AppendSegmentedFactEventCommandV1:
    """Append one fsync fact to a previously ensured segmented ledger."""

    workspace: str
    logical_stream: str
    event_type: str
    source: str
    payload: Mapping[str, Any]
    idempotency_key: str
    expected_global_seq: int | None = None
    require_idempotency_replay: bool = False
    durability: Literal["fsync"] = "fsync"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        object.__setattr__(self, "event_type", _require_exact_non_empty("event_type", self.event_type))
        object.__setattr__(self, "source", _require_exact_non_empty("source", self.source))
        object.__setattr__(
            self,
            "idempotency_key",
            _require_exact_non_empty("idempotency_key", self.idempotency_key),
        )
        if type(self.payload) is not dict or any(type(key) is not str for key in self.payload):
            raise ValueError("payload must be an exact dict")
        payload = dict(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)
        expected = self.expected_global_seq
        if expected is not None and (type(expected) is not int or expected < 1):
            raise ValueError("expected_global_seq must be an int >= 1 or None")
        if type(self.require_idempotency_replay) is not bool:
            raise ValueError("require_idempotency_replay must be a bool")
        if type(self.durability) is not str or self.durability != "fsync":
            raise ValueError("segmented authority facts require fsync durability")


@dataclass(frozen=True)
class QuerySegmentedFactLedgerHeadV1:
    workspace: str
    logical_stream: str
    strict_integrity: Literal[True] = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        if self.strict_integrity is not True:
            raise ValueError("segmented authority head queries require strict_integrity=true")


@dataclass(frozen=True)
class QuerySegmentedFactEventsV1:
    workspace: str
    logical_stream: str
    limit: int = 100
    continuation: str | None = None
    strict_integrity: Literal[True] = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        if type(self.limit) is not int or not 1 <= self.limit <= 511:
            raise ValueError("limit must be between 1 and 511")
        object.__setattr__(
            self,
            "continuation",
            _optional_exact_text("continuation", self.continuation),
        )
        if self.strict_integrity is not True:
            raise ValueError("segmented authority queries require strict_integrity=true")


@dataclass(frozen=True)
class SegmentedFactLedgerHeadV1:
    workspace: str
    logical_stream: str
    storage_prefix: str
    total_count: int
    segment_count: int
    global_seq: int
    next_expected_global_seq: int
    tail_segment_index: int | None
    tail_local_seq: int
    head_hash: str
    storage_bytes: int
    retention: Literal["pinned_audit_no_delete"] = "pinned_audit_no_delete"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        object.__setattr__(
            self,
            "storage_prefix",
            _require_exact_non_empty("storage_prefix", self.storage_prefix),
        )
        object.__setattr__(self, "head_hash", _require_exact_sha256_hex("head_hash", self.head_hash))
        _require_exact_int("total_count", self.total_count, minimum=0)
        _require_exact_int("segment_count", self.segment_count, minimum=0)
        _require_exact_int("global_seq", self.global_seq, minimum=0)
        _require_exact_int("next_expected_global_seq", self.next_expected_global_seq, minimum=1)
        _require_exact_int("tail_local_seq", self.tail_local_seq, minimum=0)
        _require_exact_int("storage_bytes", self.storage_bytes, minimum=0)
        if self.tail_segment_index is not None:
            _require_exact_int("tail_segment_index", self.tail_segment_index, minimum=0)
        if self.global_seq != self.total_count:
            raise ValueError("global_seq must equal total_count")
        if self.next_expected_global_seq != self.global_seq + 1:
            raise ValueError("next_expected_global_seq must equal global_seq + 1")
        if self.total_count == 0:
            if (
                self.segment_count != 0
                or self.tail_segment_index is not None
                or self.tail_local_seq != 0
                or self.storage_bytes != 0
            ):
                raise ValueError("empty segmented ledger head has inconsistent count/tail/bytes state")
        elif (
            self.segment_count < 1
            or self.segment_count > self.total_count
            or self.tail_segment_index != self.segment_count - 1
            or self.tail_local_seq < 1
            or self.tail_local_seq > self.total_count
            or self.storage_bytes < 1
        ):
            raise ValueError("non-empty segmented ledger head has inconsistent count/tail/bytes state")
        if type(self.retention) is not str or self.retention != "pinned_audit_no_delete":
            raise ValueError("segmented fact ledgers require pinned_audit_no_delete retention")


@dataclass(frozen=True)
class SegmentedFactLedgerReadyV1:
    workspace: str
    logical_stream: str
    storage_prefix: str
    storage_identity_token: str
    retention: Literal["pinned_audit_no_delete"]
    head: SegmentedFactLedgerHeadV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        object.__setattr__(
            self,
            "storage_prefix",
            _require_exact_non_empty("storage_prefix", self.storage_prefix),
        )
        object.__setattr__(
            self,
            "storage_identity_token",
            _require_exact_non_empty("storage_identity_token", self.storage_identity_token),
        )
        if type(self.retention) is not str or self.retention != "pinned_audit_no_delete":
            raise ValueError("segmented fact ledgers require pinned_audit_no_delete retention")
        if type(self.head) is not SegmentedFactLedgerHeadV1:
            raise ValueError("head must be exact SegmentedFactLedgerHeadV1")
        SegmentedFactLedgerHeadV1.__post_init__(self.head)
        if (
            self.head.workspace != self.workspace
            or self.head.logical_stream != self.logical_stream
            or self.head.storage_prefix != self.storage_prefix
            or self.head.retention != self.retention
        ):
            raise ValueError("segmented ledger ready identity must exactly match its head")


@dataclass(frozen=True)
class SegmentedFactEventAppendedV1:
    workspace: str
    logical_stream: str
    event_id: str
    global_seq: int
    segment_index: int
    local_seq: int
    event_hash: str
    appended_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        object.__setattr__(self, "event_id", _require_exact_non_empty("event_id", self.event_id))
        _require_exact_int("global_seq", self.global_seq, minimum=1)
        _require_exact_int("segment_index", self.segment_index, minimum=0)
        _require_exact_int("local_seq", self.local_seq, minimum=1)
        if self.local_seq > self.global_seq or self.segment_index >= self.global_seq:
            raise ValueError("segmented append sequence coordinates are inconsistent")
        object.__setattr__(self, "event_hash", _require_exact_sha256_hex("event_hash", self.event_hash))
        object.__setattr__(self, "appended_at", _require_exact_non_empty("appended_at", self.appended_at))


@dataclass(frozen=True)
class SegmentedFactQueryResultV1:
    workspace: str
    logical_stream: str
    events: tuple[dict[str, Any], ...]
    captured_head: SegmentedFactLedgerHeadV1
    continuation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "logical_stream",
            _require_exact_non_empty("logical_stream", self.logical_stream),
        )
        if type(self.captured_head) is not SegmentedFactLedgerHeadV1:
            raise ValueError("captured_head must be exact SegmentedFactLedgerHeadV1")
        SegmentedFactLedgerHeadV1.__post_init__(self.captured_head)
        if self.captured_head.workspace != self.workspace or self.captured_head.logical_stream != self.logical_stream:
            raise ValueError("segmented query identity must exactly match captured_head")
        if type(self.events) is not tuple:
            raise ValueError("segmented query events must be an exact tuple")
        if any(type(item) is not dict or any(type(key) is not str for key in item) for item in self.events):
            raise ValueError("segmented query event entries must be exact dicts")
        events = tuple(dict(item) for item in self.events)
        if len(events) > 511:
            raise ValueError("segmented query result may contain at most 511 events")
        previous_global_seq: int | None = None
        for event in events:
            event_stream = _require_exact_non_empty("event.logical_stream", event.get("logical_stream"))
            if event_stream != self.logical_stream:
                raise ValueError("segmented query event logical_stream mismatch")
            _require_exact_non_empty("event.event_id", event.get("event_id"))
            global_seq = _require_exact_int("event.global_seq", event.get("global_seq"), minimum=1)
            _require_exact_int("event.segment_index", event.get("segment_index"), minimum=0)
            local_seq = _require_exact_int("event.local_seq", event.get("local_seq"), minimum=1)
            if local_seq > global_seq or global_seq > self.captured_head.global_seq:
                raise ValueError("segmented query event sequence exceeds captured head")
            if previous_global_seq is not None and global_seq != previous_global_seq + 1:
                raise ValueError("segmented query events must have contiguous global sequence")
            previous_global_seq = global_seq
            _require_exact_non_empty("event.event_type", event.get("event_type"))
            _require_exact_non_empty("event.source", event.get("source"))
            _require_exact_non_empty("event.idempotency_key", event.get("idempotency_key"))
            _require_exact_non_empty("event.occurred_at", event.get("occurred_at"))
            if (
                type(event.get("payload")) is not dict
                or not event["payload"]
                or any(type(key) is not str for key in event["payload"])
            ):
                raise ValueError("segmented query event payload must be a non-empty mapping")
            _require_exact_sha256_hex("event.previous_event_hash", event.get("previous_event_hash"))
            _require_exact_sha256_hex("event.event_hash", event.get("event_hash"))
        object.__setattr__(self, "events", events)
        object.__setattr__(
            self,
            "continuation",
            _optional_exact_text("continuation", self.continuation),
        )
        if self.continuation is not None and (not events or events[-1]["global_seq"] >= self.captured_head.global_seq):
            raise ValueError("segmented query continuation requires unread events below captured head")


@dataclass(frozen=True)
class ProvisionFactStreamLockAuthorityCommandV1:
    """Explicitly provision FactStream's platform lock authority.

    This maintenance command is the only public FactStream surface that may
    create an authority or enroll stream lock keys. Normal reads and appends
    only acquire already-provisioned authority state.
    """

    workspace: str
    maintenance_reason: str
    streams: tuple[str, ...] = ()
    platform_lock_root: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        streams = tuple(_require_non_empty("streams entry", value) for value in self.streams)
        if len(set(streams)) != len(streams):
            raise ValueError("streams must be distinct")
        object.__setattr__(self, "streams", streams)
        object.__setattr__(
            self,
            "maintenance_reason",
            _require_non_empty("maintenance_reason", self.maintenance_reason),
        )
        root = self.platform_lock_root
        if root is not None:
            object.__setattr__(self, "platform_lock_root", _require_non_empty("platform_lock_root", root))


@dataclass(frozen=True)
class EnrollFactStreamStreamsCommandV1:
    """Explicitly enroll existing authority keys for canonical FactStream streams.

    This maintenance command never provisions, repairs, rotates, or rebinds the
    lock authority. A missing or invalid authority therefore remains a typed
    fail-closed error from the KernelOne capability.
    """

    workspace: str
    streams: tuple[str, ...]
    maintenance_reason: str
    platform_lock_root: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        streams = tuple(_require_non_empty("streams entry", value) for value in self.streams)
        if not streams:
            raise ValueError("streams must contain at least one stream")
        if len(set(streams)) != len(streams):
            raise ValueError("streams must be distinct")
        object.__setattr__(self, "streams", tuple(sorted(streams)))
        object.__setattr__(
            self,
            "maintenance_reason",
            _require_non_empty("maintenance_reason", self.maintenance_reason),
        )
        root = self.platform_lock_root
        if root is not None:
            object.__setattr__(self, "platform_lock_root", _require_non_empty("platform_lock_root", root))


@dataclass(frozen=True)
class BootstrapFactStreamWorkspaceCommandV1:
    """Bootstrap one workspace authority and its declared static stream catalog."""

    workspace: str
    maintenance_reason: str
    streams: tuple[str, ...]
    platform_lock_root: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "maintenance_reason",
            _require_non_empty("maintenance_reason", self.maintenance_reason),
        )
        streams = tuple(_require_non_empty("streams entry", value) for value in self.streams)
        if not streams:
            raise ValueError("streams must contain at least one stream")
        if len(set(streams)) != len(streams):
            raise ValueError("streams must be distinct")
        object.__setattr__(self, "streams", tuple(sorted(streams)))
        root = self.platform_lock_root
        if root is not None:
            object.__setattr__(self, "platform_lock_root", _require_non_empty("platform_lock_root", root))


@dataclass(frozen=True)
class FactStreamLockIdentityV1:
    """Immutable physical identity projected from a KernelOne maintenance proof."""

    device: int
    inode: int

    def __post_init__(self) -> None:
        if isinstance(self.device, bool) or not isinstance(self.device, int) or self.device < 0:
            raise ValueError("device must be an int >= 0")
        if isinstance(self.inode, bool) or not isinstance(self.inode, int) or self.inode < 1:
            raise ValueError("inode must be an int >= 1")


@dataclass(frozen=True)
class FactStreamLockKeyEvidenceV1:
    """Canonical stream-lock-key evidence from maintenance final validation."""

    logical_path: str
    lock_key: str
    verdict: Literal["created", "already_present"]
    identity: FactStreamLockIdentityV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_path", _require_non_empty("logical_path", self.logical_path))
        object.__setattr__(self, "lock_key", _require_non_empty("lock_key", self.lock_key))
        if self.verdict not in {"created", "already_present"}:
            raise ValueError("lock-key verdict must be created or already_present")


@dataclass(frozen=True)
class FactStreamMaintenanceProofV1:
    """Complete public projection of one KernelOne authority maintenance proof."""

    operation: Literal["provision_authority", "enroll_stream_lock_keys"]
    verdict: Literal["created", "already_present"]
    storage_identity_token: str
    runtime_root: str
    format_revision: str
    root_identity: FactStreamLockIdentityV1
    anchor_identity: FactStreamLockIdentityV1
    realm_identity: FactStreamLockIdentityV1
    lock_keys: tuple[FactStreamLockKeyEvidenceV1, ...]
    final_validation: Literal[True]

    def __post_init__(self) -> None:
        if self.operation not in {"provision_authority", "enroll_stream_lock_keys"}:
            raise ValueError("maintenance proof operation is unsupported")
        if self.verdict not in {"created", "already_present"}:
            raise ValueError("maintenance proof verdict must be created or already_present")
        object.__setattr__(
            self,
            "storage_identity_token",
            _require_non_empty("storage_identity_token", self.storage_identity_token),
        )
        object.__setattr__(self, "runtime_root", _require_non_empty("runtime_root", self.runtime_root))
        object.__setattr__(self, "format_revision", _require_non_empty("format_revision", self.format_revision))
        keys = tuple(self.lock_keys)
        if len({item.lock_key for item in keys}) != len(keys):
            raise ValueError("maintenance proof lock keys must be distinct")
        if keys != tuple(sorted(keys, key=lambda item: (item.lock_key, item.logical_path))):
            raise ValueError("maintenance proof lock keys must be canonical sorted")
        object.__setattr__(self, "lock_keys", keys)
        if self.final_validation is not True:
            raise ValueError("maintenance proof requires final_validation=true")


@dataclass(frozen=True)
class FactStreamMaintenanceReceiptV1:
    """Non-authoritative DTO recording one maintenance operation's evidence.

    Callers may construct this observational receipt, but possession never
    grants authority. Maintenance, enrollment, bootstrap, and write services
    derive authority only by revalidating current physical state under the
    applicable platform lock.
    """

    workspace: str
    storage_identity_token: str
    maintenance_reason: str
    operation: str
    streams: tuple[str, ...] = ()
    proofs: tuple[FactStreamMaintenanceProofV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "storage_identity_token",
            _require_non_empty("storage_identity_token", self.storage_identity_token),
        )
        object.__setattr__(
            self,
            "maintenance_reason",
            _require_non_empty("maintenance_reason", self.maintenance_reason),
        )
        object.__setattr__(self, "operation", _require_non_empty("operation", self.operation))
        streams = tuple(_require_non_empty("streams entry", value) for value in self.streams)
        if len(set(streams)) != len(streams):
            raise ValueError("streams must be distinct")
        object.__setattr__(self, "streams", tuple(sorted(streams)))
        proofs = tuple(self.proofs)
        if not proofs:
            raise ValueError("maintenance receipt requires at least one final proof")
        if any(proof.storage_identity_token != self.storage_identity_token for proof in proofs):
            raise ValueError("maintenance receipt proof storage identity does not match receipt")
        expected_operations: tuple[str, ...]
        if self.operation == "provision_authority":
            expected_operations = (
                ("provision_authority", "enroll_stream_lock_keys") if streams else ("provision_authority",)
            )
        elif self.operation == "enroll_streams":
            expected_operations = ("enroll_stream_lock_keys",)
        elif self.operation == "bootstrap_workspace":
            expected_operations = ("provision_authority", "enroll_stream_lock_keys")
        else:
            raise ValueError("maintenance receipt operation is unsupported")
        actual_operations = tuple(proof.operation for proof in proofs)
        if actual_operations != expected_operations:
            raise ValueError("maintenance receipt proofs do not match operation")
        object.__setattr__(self, "proofs", proofs)


@dataclass(frozen=True)
class FactStreamHeadV1:
    """Immutable head projection used to derive optimistic expected_seq."""

    workspace: str
    stream: str
    storage_path: str
    current_seq: int
    next_expected_seq: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        object.__setattr__(self, "storage_path", _require_non_empty("storage_path", self.storage_path))
        if isinstance(self.current_seq, bool) or not isinstance(self.current_seq, int) or self.current_seq < 0:
            raise ValueError("current_seq must be an int >= 0")
        if (
            isinstance(self.next_expected_seq, bool)
            or not isinstance(self.next_expected_seq, int)
            or self.next_expected_seq < 1
        ):
            raise ValueError("next_expected_seq must be an int >= 1")
        if self.next_expected_seq != self.current_seq + 1:
            raise ValueError("next_expected_seq must equal current_seq + 1")


@dataclass(frozen=True)
class FactEventAppendedV1:
    event_id: str
    workspace: str
    stream: str
    storage_path: str
    appended_at: str
    appended_seq: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stream", _require_non_empty("stream", self.stream))
        object.__setattr__(self, "storage_path", _require_non_empty("storage_path", self.storage_path))
        object.__setattr__(self, "appended_at", _require_non_empty("appended_at", self.appended_at))
        appended_seq = self.appended_seq
        if appended_seq is not None:
            if isinstance(appended_seq, bool) or not isinstance(appended_seq, int):
                raise ValueError("appended_seq must be an int or None")
            if appended_seq < 1:
                raise ValueError("appended_seq must be >= 1")


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
        self.details = _detach_error_details(details)


__all__ = [
    "AppendFactEventCommandV1",
    "AppendIfGuardedSnapshotCommandV1",
    "AppendSegmentedFactEventCommandV1",
    "BootstrapFactStreamWorkspaceCommandV1",
    "EnrollFactStreamStreamsCommandV1",
    "EnsureSegmentedFactLedgerCommandV1",
    "FactEventAppendedV1",
    "FactStreamError",
    "FactStreamHeadV1",
    "FactStreamMaintenanceReceiptV1",
    "FactStreamProvenanceV1",
    "FactStreamQueryResultV1",
    "GuardedFactAppendedV1",
    "GuardedFactEventV1",
    "GuardedFactSnapshotProofV1",
    "GuardedFactSnapshotV1",
    "ProvisionFactStreamLockAuthorityCommandV1",
    "QueryFactEventsV1",
    "QueryFactStreamHeadV1",
    "QuerySegmentedFactEventsV1",
    "QuerySegmentedFactLedgerHeadV1",
    "ReadGuardedFactSnapshotCommandV1",
    "SegmentedFactEventAppendedV1",
    "SegmentedFactLedgerHeadV1",
    "SegmentedFactLedgerReadyV1",
    "SegmentedFactQueryResultV1",
]
