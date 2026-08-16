"""JSONL-backed event store for versioned event streams."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.fs import KernelFileSystem, LockedRegularFileError, LockedRegularFileSetV1, StreamLeaseV1
from polaris.kernelone.fs.contracts import DurabilityMode, validate_durability
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import WorkspaceRuntimeIdentity, resolve_workspace_runtime_identity

from .models import (
    EventEnvelope,
    EventQueryResult,
    EventSourcingError,
    ExpectedSequenceDriftError,
    IdempotencyConflictError,
    StrictEventRecordError,
    decode_strict_event_record,
    new_event_id,
    utc_now_iso,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_STRICT_MAX_RECORDS = 4096
_DEFAULT_STRICT_MAX_BYTES = 8 * 1024 * 1024
_STRICT_STREAM_CORRUPTION_CODE = "strict_stream_corruption"
_STRICT_REASON_TORN_TAIL = "torn_tail"
_STRICT_REASON_SEQUENCE_VIOLATION = "sequence_violation"
_STRICT_REASON_MIDDLE_CORRUPTION = "middle_corruption"
_STRICT_REASON_UNKNOWN_SCHEMA = "unknown_schema"
_STRICT_REASON_BY_CODE = {
    "torn_tail": _STRICT_REASON_TORN_TAIL,
    "sequence_violation": _STRICT_REASON_SEQUENCE_VIOLATION,
    "unknown_schema_version": _STRICT_REASON_UNKNOWN_SCHEMA,
    "unknown_event_version": _STRICT_REASON_UNKNOWN_SCHEMA,
    "stream_read_failed": _STRICT_REASON_MIDDLE_CORRUPTION,
    "stream_corruption": _STRICT_REASON_MIDDLE_CORRUPTION,
    "strict_record_corruption": _STRICT_REASON_MIDDLE_CORRUPTION,
    "integrity_digest_mismatch": _STRICT_REASON_MIDDLE_CORRUPTION,
    "invalid_raw_integer": _STRICT_REASON_MIDDLE_CORRUPTION,
    "missing_integrity_digest": _STRICT_REASON_MIDDLE_CORRUPTION,
}
_STRICT_STREAM_REASONS = frozenset(_STRICT_REASON_BY_CODE.values())
# Live L2-12: Factory wait, settlement, turn_outcomes, and control-plane
# all query distinct JSONL streams. A 4-slot LRU evicted the 130MiB
# ``task_runtime.execution`` parse and forced a full rescan every poll.
# DEO parent/operation streams are one-key-per-binding, so a flat LRU of 16
# still evicts the large execution parse on every heartbeat wave. Pin large
# parses. Never let a stale in-flight scan publish over a newer head.
_PARSED_STREAM_CACHE_MAX = 16
_PARSED_STREAM_PIN_MIN_RECORDS = 64
_PARSED_STREAM_TAIL_MIN_BYTES = 2 * 1024 * 1024
_PARSED_STREAM_TAIL_MAX_BYTES = 64 * 1024 * 1024
_PARSED_STREAM_TAIL_BYTES_PER_EVENT = 768 * 1024
_PARSED_STREAM_CACHE: OrderedDict[tuple[str, str, int], tuple[EventEnvelope, ...]] = OrderedDict()
_PARSED_STREAM_CACHE_LOCK = threading.RLock()
_HEARTBEAT_EVENT_TYPES = frozenset({"heartbeat_renewed", "heartbeat"})
_HEARTBEAT_PAYLOAD_KEYS = ("task_id", "status", "session_id", "factory_run_id", "terminal")
_HEARTBEAT_METADATA_KEYS = ("task_id", "run_id", "factory_run_id")


def _compact_non_authoritative_event_record(record: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky heartbeat snapshots before they enter the parsed-stream cache.

    Live L2-12: each ``heartbeat_renewed`` line is ~64KiB because the writer
    embeds the full TaskRuntime row.  Caching 4k of those as Python objects
    grew the isolated backend to 5.8GiB and SIGSEGV'd pid 18062 (signal 11).
    Settlement ignores non-terminal facts; observers only need identity.
    Strict integrity reads do not use this helper.
    """

    event_type = str(record.get("event_type") or "").strip()
    if event_type not in _HEARTBEAT_EVENT_TYPES:
        return record
    compacted = dict(record)
    payload = record.get("payload")
    if isinstance(payload, dict):
        compacted["payload"] = {key: payload[key] for key in _HEARTBEAT_PAYLOAD_KEYS if key in payload}
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        compacted["metadata"] = {key: metadata[key] for key in _HEARTBEAT_METADATA_KEYS if key in metadata}
    return compacted


class _StrictStreamIntegrityError(EventSourcingError):
    """Fail-closed strict JSONL evidence with public-facing classification."""

    def __init__(self, message: str, *, code: str, details: Mapping[str, Any]) -> None:
        normalized_details = dict(details)
        strict_reason = _strict_reason_for(code=code, details=normalized_details)
        if strict_reason is not None:
            reason_code = str(normalized_details.get("reason_code") or code)
            normalized_details["reason_code"] = reason_code
            normalized_details["strict_failure_code"] = reason_code
            normalized_details["strict_reason"] = strict_reason
            code = _STRICT_STREAM_CORRUPTION_CODE
        super().__init__(message, code=code, details=normalized_details)


def _strict_reason_for(*, code: str, details: Mapping[str, Any]) -> str | None:
    """Map parser evidence to the stable public strict-corruption taxonomy."""

    existing_reason = details.get("strict_reason")
    if isinstance(existing_reason, str) and existing_reason in _STRICT_STREAM_REASONS:
        return existing_reason
    reason_code = str(details.get("reason_code") or code)
    return _STRICT_REASON_BY_CODE.get(reason_code)


def normalize_strict_stream_failure(
    error: EventSourcingError,
    *,
    stream: str,
    storage_path: str,
) -> EventSourcingError:
    """Project known strict parser evidence to the stable public category.

    Scan limits and non-parser failures keep their own stable codes.  The helper
    is reused by guarded sourcing so a descriptor-bound strict read has the same
    public contract as the ordinary strict store API.
    """

    details = dict(error.details)
    strict_reason = _strict_reason_for(code=error.code, details=details)
    if strict_reason is None:
        return error
    details.setdefault("stream", stream)
    details.setdefault("storage_path", storage_path)
    reason_code = str(details.get("reason_code") or error.code)
    details["reason_code"] = reason_code
    details["strict_failure_code"] = reason_code
    details["strict_reason"] = strict_reason
    return EventSourcingError(
        "strict event stream corruption",
        code=_STRICT_STREAM_CORRUPTION_CODE,
        details=details,
    )


@dataclass(frozen=True, slots=True)
class _EventSemanticEnvelope:
    """Event meaning used for idempotency comparison.

    Allocation fields (``event_id``, ``occurred_at``, and ``seq``) are omitted
    intentionally because an identical replay necessarily regenerates them.
    """

    stream: str
    event_type: str
    event_version: int
    source: str
    aggregate_id: str | None
    correlation_id: str | None
    causation_id: str | None
    payload: dict[str, Any]
    metadata: dict[str, Any]


class JsonlEventStore:
    """Append-only file-backed event store.

    Stream data is persisted under:
    ``runtime/events/<stream>.jsonl``
    """

    def __init__(
        self,
        workspace: str,
        *,
        root_logical_dir: str = "runtime/events",
        kernel_fs: KernelFileSystem | None = None,
        strict_max_records: int = _DEFAULT_STRICT_MAX_RECORDS,
        strict_max_bytes: int = _DEFAULT_STRICT_MAX_BYTES,
    ) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        self._storage_identity = resolve_workspace_runtime_identity(workspace_token)
        self._workspace = self._storage_identity.workspace_abs
        self._root_logical_dir = self._normalize_root(root_logical_dir)
        self._kernel_fs = kernel_fs or KernelFileSystem(self._workspace, get_default_adapter())
        self._strict_max_records = self._validate_strict_limit(
            strict_max_records,
            field="strict_max_records",
        )
        self._strict_max_bytes = self._validate_strict_limit(
            strict_max_bytes,
            field="strict_max_bytes",
        )

    @property
    def workspace(self) -> str:
        return self._workspace

    @property
    def storage_identity(self) -> WorkspaceRuntimeIdentity:
        """Return the immutable workspace/runtime binding used by this store."""

        return self._storage_identity

    def stream_logical_path(self, stream: str) -> str:
        stream_token = self._normalize_stream(stream)
        return f"{self._root_logical_dir}/{stream_token}.jsonl"

    def append(
        self,
        *,
        stream: str,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
        event_version: int = 1,
        aggregate_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        expected_seq: int | None = None,
        idempotency_key: str | None = None,
        durability: DurabilityMode = "buffered",
        strict_integrity: bool = False,
    ) -> EventEnvelope:
        """Atomically append one event or return its idempotent predecessor.

        Sequence allocation, idempotency lookup, JSONL persistence, and cursor
        advancement share one stream-scoped critical section.  Callers therefore
        never have to implement their own query-then-append race window.

        Strict mode validates the complete stream in one bounded pass before
        allocating a sequence; its configured record and byte limits are
        explicit fail-closed complexity bounds, not timing-based test gates.
        """
        if not isinstance(strict_integrity, bool):
            raise ValueError("strict_integrity must be a bool")
        normalized_durability = validate_durability(durability)
        payload_dict = self._canonical_mapping(payload, field="payload")
        if not payload_dict:
            raise ValueError("payload must not be empty")
        if expected_seq is not None:
            # Coerce defensively: only ints are accepted. bool is a subclass of
            # int but is not a meaningful sequence number, so reject it.
            if isinstance(expected_seq, bool) or not isinstance(expected_seq, int):
                raise ValueError("expected_seq must be an int or None")
            if expected_seq < 1:
                raise ValueError("expected_seq must be >= 1")
        metadata_dict = self._canonical_mapping(metadata or {}, field="metadata")
        argument_idempotency_key = str(idempotency_key or "").strip()
        metadata_idempotency_key = str(metadata_dict.get("idempotency_key") or "").strip()
        if (
            argument_idempotency_key
            and metadata_idempotency_key
            and argument_idempotency_key != metadata_idempotency_key
        ):
            raise ValueError("idempotency_key argument does not match metadata.idempotency_key")
        normalized_idempotency_key = argument_idempotency_key or metadata_idempotency_key
        if normalized_idempotency_key:
            metadata_dict["idempotency_key"] = normalized_idempotency_key
        semantic = self._build_semantic_envelope(
            stream=stream,
            event_type=event_type,
            source=source,
            payload=payload_dict,
            event_version=event_version,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata_dict,
        )
        logical_path = self.stream_logical_path(semantic.stream)
        # Preserve the legacy adapter-bound escape check.  Actual stream I/O
        # below is descriptor-bound through the runtime-root lease.
        self._resolve_runtime_stream_path(logical_path)
        return self._append_locked(
            logical_path=logical_path,
            semantic=semantic,
            expected_seq=expected_seq,
            idempotency_key=normalized_idempotency_key,
            durability=normalized_durability,
            strict_integrity=strict_integrity,
        )

    def query(
        self,
        *,
        stream: str,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        strict_integrity: bool = False,
    ) -> EventQueryResult:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        if not isinstance(strict_integrity, bool):
            raise ValueError("strict_integrity must be a bool")
        stream_token = self._normalize_stream(stream)
        logical_path = self.stream_logical_path(stream_token)
        with self._locked_streams(logical_path) as leases:
            lease = leases.lease(logical_path)
            lease.open_existing(writable=False)
            records = self._read_lease_records_cached(
                stream=stream_token,
                logical_path=logical_path,
                lease=lease,
                strict_integrity=strict_integrity,
            )
        filtered = [
            envelope
            for envelope in records
            if self._matches(
                envelope=envelope,
                event_type=event_type,
                run_id=run_id,
                task_id=task_id,
            )
        ]
        total = len(filtered)
        window = filtered[offset : offset + int(limit)]
        next_offset = offset + len(window)
        if next_offset >= total:
            next_offset = 0
        return EventQueryResult(
            stream=stream_token,
            storage_path=logical_path,
            events=tuple(window),
            total=total,
            next_offset=next_offset,
        )

    def current_seq(self, stream: str, *, strict_integrity: bool = False) -> int:
        """Return JSONL head truth under the same persistent stream lock as append.

        Legacy ``.seq`` cursors are deliberately ignored: they are neither
        authoritative nor permitted to revise the outcome of a durable append.
        """

        if not isinstance(strict_integrity, bool):
            raise ValueError("strict_integrity must be a bool")
        stream_token = self._normalize_stream(stream)
        logical_path = self.stream_logical_path(stream_token)
        with self._locked_streams(logical_path) as leases:
            lease = leases.lease(logical_path)
            lease.open_existing(writable=False)
            if not strict_integrity:
                return self._read_lease_tail_seq(
                    stream=stream_token,
                    logical_path=logical_path,
                    lease=lease,
                )
            records = self._read_lease_records(
                stream=stream_token,
                logical_path=logical_path,
                lease=lease,
                strict_integrity=strict_integrity,
            )
            return records[-1].seq if records else 0

    def _read_lease_tail_seq(
        self,
        *,
        stream: str,
        logical_path: str,
        lease: StreamLeaseV1,
    ) -> int:
        """Read the latest valid append envelope without parsing stream history.

        Ordinary FactStream appends are monotonic and descriptor-locked.  A
        head query therefore needs only the last valid physical record; parsing
        every historical heartbeat snapshot made each settlement wake O(total
        stream bytes).  Strict-integrity callers retain the complete scan in
        ``current_seq``.
        """

        if not lease.exists:
            return 0
        try:
            # One TaskRuntime envelope can be large because it carries a full
            # task-row snapshot.  Read a bounded tail and expand only if that
            # window does not contain a complete record boundary.
            tail_budget = 1024 * 1024
            remaining = lease.read_tail_bytes(tail_budget).rstrip(b"\r\n")
        except LockedRegularFileError as exc:
            raise EventSourcingError(
                f"stream descriptor read failed stream={stream!r}: {exc}",
                code=exc.code,
                details=dict(exc.details),
            ) from exc
        except (OSError, ValueError) as exc:
            raise _StrictStreamIntegrityError(
                f"head read failed stream={stream!r}: {exc}",
                code="stream_read_failed",
                details={"stream": stream, "storage_path": logical_path},
            ) from exc

        while remaining:
            prefix, separator, raw_line = remaining.rpartition(b"\n")
            line = raw_line.strip()
            if line:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        return EventEnvelope.from_record(record).seq
                except (RuntimeError, UnicodeDecodeError, ValueError) as exc:
                    logger.debug("skip malformed tail event record path=%s: %s", logical_path, exc)
            if not separator:
                # A single envelope larger than the initial window is rare
                # but valid. Expand geometrically, still avoiding a routine
                # full-history read.
                tail_budget *= 2
                expanded = lease.read_tail_bytes(tail_budget).rstrip(b"\r\n")
                if len(expanded) <= len(remaining):
                    break
                remaining = expanded
                continue
            remaining = prefix.rstrip(b"\r\n")
        return 0

    def _append_locked(
        self,
        *,
        logical_path: str,
        semantic: _EventSemanticEnvelope,
        expected_seq: int | None,
        idempotency_key: str,
        durability: DurabilityMode,
        strict_integrity: bool,
    ) -> EventEnvelope:
        """Append through the central persistent lock and descriptor lease."""

        with self._locked_streams(logical_path) as leases:
            lease = leases.lease(logical_path)
            lease.open_existing(writable=True)
            records = self._read_lease_records_cached(
                stream=semantic.stream,
                logical_path=logical_path,
                lease=lease,
                strict_integrity=strict_integrity,
            )
            if idempotency_key:
                idempotent_event = next(
                    (
                        event
                        for event in records
                        if str(event.metadata.get("idempotency_key") or "").strip() == idempotency_key
                    ),
                    None,
                )
                if idempotent_event is not None:
                    existing_semantic = self._semantic_from_event(idempotent_event)
                    if existing_semantic != semantic:
                        drift_fields = self._semantic_drift_fields(
                            existing=existing_semantic,
                            requested=semantic,
                        )
                        raise IdempotencyConflictError(
                            "idempotency conflict: existing event does not match requested event "
                            f"key={idempotency_key!r} fields={','.join(drift_fields)} "
                            f"path={logical_path!r}",
                            drift_fields=drift_fields,
                        )
                    if expected_seq is not None and idempotent_event.seq != expected_seq:
                        raise ExpectedSequenceDriftError(
                            "expected_seq drift for idempotent event: "
                            f"requested={expected_seq} actual={idempotent_event.seq} path={logical_path!r}",
                            requested_seq=expected_seq,
                            actual_seq=idempotent_event.seq,
                        )
                    return idempotent_event
            if strict_integrity and len(records) >= self._strict_max_records:
                raise _StrictStreamIntegrityError(
                    "strict event stream append exceeds the configured record limit",
                    code="strict_scan_limit_exceeded",
                    details={
                        "stream": semantic.stream,
                        "storage_path": logical_path,
                        "limit": "max_records",
                        "max_records": self._strict_max_records,
                        "actual_records": len(records) + 1,
                    },
                )
            next_seq = (records[-1].seq if records else 0) + 1
            if expected_seq is not None and next_seq != expected_seq:
                raise ExpectedSequenceDriftError(
                    f"expected_seq drift: requested={expected_seq} actual={next_seq} path={logical_path!r}",
                    requested_seq=expected_seq,
                    actual_seq=next_seq,
                )
            envelope = self._build_envelope(semantic=semantic, seq=next_seq)
            record = envelope.to_record(include_integrity_digest=strict_integrity)
            encoded = (
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            if strict_integrity and len(lease.read_bytes()) + len(encoded) > self._strict_max_bytes:
                raise _StrictStreamIntegrityError(
                    "strict event stream append exceeds the configured byte limit",
                    code="strict_scan_limit_exceeded",
                    details={"stream": semantic.stream, "storage_path": logical_path},
                )
            try:
                lease.append_bytes(
                    encoded,
                    fsync_file=durability in {"flush", "fsync"},
                    fsync_parent_on_create=durability == "fsync",
                )
            except LockedRegularFileError as exc:
                raise EventSourcingError(
                    f"failed to append event stream={semantic.stream!r}: {exc}",
                    code=exc.code,
                    details=dict(exc.details),
                ) from exc
            except (OSError, RuntimeError, ValueError) as exc:
                raise EventSourcingError(
                    f"failed to append event stream={semantic.stream!r}: {exc}",
                ) from exc
            if not strict_integrity:
                self._publish_parsed_stream_cache(
                    logical_path=logical_path,
                    stream_head=envelope.seq,
                    records=[*records, envelope],
                )
            return envelope

    def _build_envelope(
        self,
        *,
        semantic: _EventSemanticEnvelope,
        seq: int,
    ) -> EventEnvelope:
        return EventEnvelope(
            event_id=new_event_id(),
            stream=semantic.stream,
            event_type=semantic.event_type,
            event_version=semantic.event_version,
            seq=seq,
            occurred_at=utc_now_iso(),
            source=semantic.source,
            aggregate_id=semantic.aggregate_id,
            correlation_id=semantic.correlation_id,
            causation_id=semantic.causation_id,
            payload=dict(semantic.payload),
            metadata=dict(semantic.metadata),
        )

    def _build_semantic_envelope(
        self,
        *,
        stream: str,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
        event_version: int,
        aggregate_id: str | None,
        correlation_id: str | None,
        causation_id: str | None,
        metadata: Mapping[str, Any],
    ) -> _EventSemanticEnvelope:
        if isinstance(event_version, bool):
            raise ValueError("event_version must be a positive integer")
        try:
            normalized_version = int(event_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("event_version must be a positive integer") from exc
        if normalized_version < 1:
            raise ValueError("event_version must be a positive integer")
        return _EventSemanticEnvelope(
            stream=self._normalize_stream(stream),
            event_type=self._normalize_stream(event_type),
            event_version=normalized_version,
            source=self._normalize_stream(source),
            aggregate_id=self._normalize_optional(aggregate_id),
            correlation_id=self._normalize_optional(correlation_id),
            causation_id=self._normalize_optional(causation_id),
            payload=self._canonical_mapping(payload, field="payload"),
            metadata=self._canonical_mapping(metadata, field="metadata"),
        )

    def _semantic_from_event(self, event: EventEnvelope) -> _EventSemanticEnvelope:
        return _EventSemanticEnvelope(
            stream=event.stream,
            event_type=event.event_type,
            event_version=event.event_version,
            source=event.source,
            aggregate_id=event.aggregate_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            payload=self._canonical_mapping(event.payload, field="payload"),
            metadata=self._canonical_mapping(event.metadata, field="metadata"),
        )

    @staticmethod
    def _semantic_drift_fields(
        *,
        existing: _EventSemanticEnvelope,
        requested: _EventSemanticEnvelope,
    ) -> list[str]:
        return [
            field
            for field in (
                "stream",
                "event_type",
                "event_version",
                "source",
                "aggregate_id",
                "correlation_id",
                "causation_id",
                "payload",
                "metadata",
            )
            if getattr(existing, field) != getattr(requested, field)
        ]

    @staticmethod
    def _canonical_mapping(
        value: Mapping[str, Any],
        *,
        field: str,
    ) -> dict[str, Any]:
        """Round-trip a mapping through canonical JSON for durable comparison."""

        try:
            encoded = json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a JSON-compatible mapping") from exc
        if not isinstance(decoded, dict):
            raise ValueError(f"{field} must be a JSON-compatible mapping")
        return decoded

    def _parse_records(self, *, content: str, storage_path: str) -> list[EventEnvelope]:
        events: list[EventEnvelope] = []
        for raw_line in str(content or "").splitlines():
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                events.append(EventEnvelope.from_record(_compact_non_authoritative_event_record(record)))
            except (RuntimeError, ValueError) as exc:
                logger.debug("skip malformed event record path=%s: %s", storage_path, exc)
                continue
        events.sort(key=lambda item: item.seq)
        return events

    def _locked_streams(self, *logical_paths: str) -> LockedRegularFileSetV1:
        """Return the central lock namespace shared by legacy and guarded writes.

        Default 2s acquisition is too short when factory director cutoff fsync
        appends contend with task-runtime heartbeat/settlement queries under the
        same lock realm (R143/R144 advisory lock timeouts).
        """

        try:
            return LockedRegularFileSetV1.acquire(
                runtime_root=self._storage_identity.runtime_root,
                storage_identity_token=self._storage_identity.token,
                logical_paths=logical_paths,
                timeout_seconds=15.0,
            )
        except LockedRegularFileError as exc:
            raise EventSourcingError(
                "event stream lock or descriptor capability failed",
                code=exc.code,
                details=dict(exc.details),
            ) from exc

    def _read_strict_records_locked(self, *, stream: str, logical_path: str) -> list[EventEnvelope]:
        """Read and validate one stream under the central persistent stream lock."""

        with self._locked_streams(logical_path) as leases:
            lease = leases.lease(logical_path)
            lease.open_existing(writable=False)
            return self._read_lease_records(
                stream=stream,
                logical_path=logical_path,
                lease=lease,
                strict_integrity=True,
            )

    def _read_strict_records(self, *, stream: str, logical_path: str) -> list[EventEnvelope]:
        """Compatibility wrapper that performs a strict central-lock scan."""

        return self._read_strict_records_locked(stream=stream, logical_path=logical_path)

    def _parsed_stream_cache_key(self, *, logical_path: str, stream_head: int) -> tuple[str, str, int]:
        return (self._storage_identity.token, logical_path, stream_head)

    def _publish_parsed_stream_cache(
        self,
        *,
        logical_path: str,
        stream_head: int,
        records: list[EventEnvelope],
    ) -> None:
        key = self._parsed_stream_cache_key(logical_path=logical_path, stream_head=stream_head)
        identity = key[:2]
        with _PARSED_STREAM_CACHE_LOCK:
            if any(candidate[:2] == identity and candidate[2] > stream_head for candidate in _PARSED_STREAM_CACHE):
                # A later append already installed a newer snapshot. Publishing
                # this stale scan would delete that head and force the next
                # observer to re-parse the whole 300MiB execution stream.
                return
            stale_keys = [
                candidate
                for candidate in _PARSED_STREAM_CACHE
                if candidate[:2] == identity and candidate[2] < stream_head
            ]
            for stale_key in stale_keys:
                _PARSED_STREAM_CACHE.pop(stale_key, None)
            _PARSED_STREAM_CACHE[key] = tuple(records)
            _PARSED_STREAM_CACHE.move_to_end(key)
            while len(_PARSED_STREAM_CACHE) > _PARSED_STREAM_CACHE_MAX:
                victim = next(
                    (
                        candidate
                        for candidate in _PARSED_STREAM_CACHE
                        if candidate != key and len(_PARSED_STREAM_CACHE[candidate]) < _PARSED_STREAM_PIN_MIN_RECORDS
                    ),
                    None,
                )
                if victim is None:
                    victim = next(
                        (candidate for candidate in _PARSED_STREAM_CACHE if candidate != key),
                        None,
                    )
                if victim is None:
                    break
                _PARSED_STREAM_CACHE.pop(victim, None)

    def _latest_parsed_stream_cache(
        self,
        *,
        logical_path: str,
    ) -> tuple[int, tuple[EventEnvelope, ...]] | None:
        """Return the newest in-process parse for this stream identity."""

        identity = (self._storage_identity.token, logical_path)
        with _PARSED_STREAM_CACHE_LOCK:
            candidates = [key for key in _PARSED_STREAM_CACHE if key[:2] == identity]
            if not candidates:
                return None
            newest = max(candidates, key=lambda key: key[2])
            records = _PARSED_STREAM_CACHE.get(newest)
            if records is None:
                return None
            _PARSED_STREAM_CACHE.move_to_end(newest)
            return newest[2], records

    def _extend_parsed_records_from_tail(
        self,
        *,
        lease: StreamLeaseV1,
        cached_records: tuple[EventEnvelope, ...],
        cached_head: int,
        stream_head: int,
        storage_path: str,
    ) -> list[EventEnvelope] | None:
        """Parse only new tail envelopes when the cached head is behind.

        Live L2-12: each Director write/heartbeat advanced the execution head
        and forced a 545MiB ``_read_lease_records`` under the stream lock.
        Task heartbeats then missed their 120s lease. Extend the pinned parse
        from the descriptor tail when the new records fit that window.
        """

        if not cached_records or cached_head < 1 or stream_head <= cached_head:
            return None
        missing = stream_head - cached_head
        budget = min(
            _PARSED_STREAM_TAIL_MAX_BYTES,
            max(_PARSED_STREAM_TAIL_MIN_BYTES, missing * _PARSED_STREAM_TAIL_BYTES_PER_EVENT),
        )
        # Live L2-12 task 266: heartbeat envelopes are ~64KiB.  A 16MiB tail
        # that started mid-line dropped seq cached_head+1, extend returned
        # None, and query() reread 696MiB under the stream lock.  Director
        # heartbeats then missed the 120s lease.  Double the tail until the
        # new seq window is contiguous; never treat a truncated first line
        # as "must full-scan".
        while budget <= _PARSED_STREAM_TAIL_MAX_BYTES:
            try:
                tail = lease.read_tail_bytes(budget)
            except (LockedRegularFileError, OSError, ValueError) as exc:
                logger.debug("parsed-stream tail extend failed path=%s: %s", storage_path, exc)
                return None
            extras = [
                event
                for event in self._parse_records(
                    content=tail.decode("utf-8", errors="replace"),
                    storage_path=storage_path,
                )
                if event.seq > cached_head
            ]
            extras.sort(key=lambda item: item.seq)
            if (
                extras
                and extras[0].seq == cached_head + 1
                and extras[-1].seq == stream_head
                and all(event.seq == cached_head + 1 + index for index, event in enumerate(extras))
            ):
                return [*cached_records, *extras]
            if budget >= _PARSED_STREAM_TAIL_MAX_BYTES or len(tail) < budget:
                return None
            budget = min(_PARSED_STREAM_TAIL_MAX_BYTES, budget * 2)
        return None

    def _read_lease_records_cached(
        self,
        *,
        stream: str,
        logical_path: str,
        lease: StreamLeaseV1,
        strict_integrity: bool,
    ) -> list[EventEnvelope]:
        """Reuse a parsed non-strict stream while its durable head is unchanged.

        The descriptor lock and tail-derived sequence fence the cache.  Strict
        integrity reads deliberately keep their complete validation pass.
        When the head advanced a few envelopes, extend the newest older parse
        from the file tail instead of rereading hundreds of MiB.
        """

        if strict_integrity or not lease.exists:
            return self._read_lease_records(
                stream=stream,
                logical_path=logical_path,
                lease=lease,
                strict_integrity=strict_integrity,
            )
        stream_head = self._read_lease_tail_seq(
            stream=stream,
            logical_path=logical_path,
            lease=lease,
        )
        key = self._parsed_stream_cache_key(logical_path=logical_path, stream_head=stream_head)
        with _PARSED_STREAM_CACHE_LOCK:
            cached = _PARSED_STREAM_CACHE.get(key)
            if cached is not None:
                _PARSED_STREAM_CACHE.move_to_end(key)
                return list(cached)
        older = self._latest_parsed_stream_cache(logical_path=logical_path)
        if older is not None and 0 < older[0] < stream_head:
            extended = self._extend_parsed_records_from_tail(
                lease=lease,
                cached_records=older[1],
                cached_head=older[0],
                stream_head=stream_head,
                storage_path=logical_path,
            )
            if extended is not None:
                self._publish_parsed_stream_cache(
                    logical_path=logical_path,
                    stream_head=stream_head,
                    records=extended,
                )
                return extended
        records = self._read_lease_records(
            stream=stream,
            logical_path=logical_path,
            lease=lease,
            strict_integrity=False,
        )
        observed_head = records[-1].seq if records else 0
        if observed_head == stream_head:
            self._publish_parsed_stream_cache(
                logical_path=logical_path,
                stream_head=stream_head,
                records=records,
            )
        return records

    def _read_lease_records(
        self,
        *,
        stream: str,
        logical_path: str,
        lease: StreamLeaseV1,
        strict_integrity: bool,
    ) -> list[EventEnvelope]:
        """Read a held no-follow lease and parse it without logical-path I/O."""

        if not lease.exists:
            return []
        started_at = time.perf_counter()
        try:
            content_bytes = lease.read_bytes()
            content = content_bytes.decode("utf-8")
        except LockedRegularFileError as exc:
            raise EventSourcingError(
                f"stream descriptor read failed stream={stream!r}: {exc}",
                code=exc.code,
                details=dict(exc.details),
            ) from exc
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise _StrictStreamIntegrityError(
                f"strict read failed stream={stream!r}: {exc}",
                code="stream_read_failed",
                details={"stream": stream, "storage_path": logical_path},
            ) from exc
        if strict_integrity:
            records = self._parse_strict_records(
                content=content,
                storage_path=logical_path,
                stream=stream,
                byte_length=len(content_bytes),
            )
        else:
            records = self._parse_records(content=content, storage_path=logical_path)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if duration_ms >= 250:
            caller_path = " > ".join(
                f"{frame.name}@{Path(frame.filename).name}:{frame.lineno}"
                for frame in traceback.extract_stack(limit=7)[:-1]
            )
            logger.warning(
                "[event_stream.full_scan] stream=%s bytes=%d records=%d strict=%s duration_ms=%d callers=%s",
                stream,
                len(content_bytes),
                len(records),
                strict_integrity,
                duration_ms,
                caller_path,
            )
        return records

    def _parse_strict_records(
        self,
        *,
        content: str,
        storage_path: str,
        stream: str,
        byte_length: int | None = None,
    ) -> list[EventEnvelope]:
        """Parse one bounded strict stream pass without skipping or reordering."""

        observed_bytes = len(content.encode("utf-8")) if byte_length is None else byte_length
        if observed_bytes > self._strict_max_bytes:
            raise _StrictStreamIntegrityError(
                "strict event stream exceeds the configured byte limit",
                code="strict_scan_limit_exceeded",
                details={
                    "stream": stream,
                    "storage_path": storage_path,
                    "limit": "max_bytes",
                    "max_bytes": self._strict_max_bytes,
                    "actual_bytes": observed_bytes,
                },
            )
        events: list[EventEnvelope] = []
        physical_lines = self._split_strict_physical_lines(content)
        if physical_lines and not physical_lines[-1][1]:
            raise _StrictStreamIntegrityError(
                "strict event stream has a malformed non-newline-terminated tail",
                code="torn_tail",
                details={
                    "stream": stream,
                    "storage_path": storage_path,
                    "physical_line": len(physical_lines),
                    "recovery_required": True,
                },
            )
        for index, (line, has_newline) in enumerate(physical_lines, start=1):
            if index > self._strict_max_records:
                raise _StrictStreamIntegrityError(
                    "strict event stream exceeds the configured record limit",
                    code="strict_scan_limit_exceeded",
                    details={
                        "stream": stream,
                        "storage_path": storage_path,
                        "limit": "max_records",
                        "max_records": self._strict_max_records,
                        "actual_records": index,
                    },
                )
            is_final_line = index == len(physical_lines)
            if not line.strip(" \t\r"):
                raise _StrictStreamIntegrityError(
                    "strict event stream contains an empty physical record",
                    code="stream_corruption",
                    details={"stream": stream, "storage_path": storage_path, "physical_line": index},
                )
            try:
                event = decode_strict_event_record(line)
                if event.stream != stream:
                    raise StrictEventRecordError(reason="stream_mismatch", field="stream")
            except _StrictStreamIntegrityError:
                raise
            except StrictEventRecordError as exc:
                reason = exc.details["reason"]
                if is_final_line and not has_newline and reason in {"invalid_json", "non_finite_number"}:
                    raise _StrictStreamIntegrityError(
                        "strict event stream has a malformed non-newline-terminated tail",
                        code="torn_tail",
                        details={
                            "stream": stream,
                            "storage_path": storage_path,
                            "physical_line": index,
                            "recovery_required": True,
                        },
                    ) from exc
                raise _StrictStreamIntegrityError(
                    "strict event stream record failed raw-shape validation",
                    code=self._strict_record_error_code(
                        reason,
                        field=exc.details.get("field"),
                    ),
                    details={
                        "stream": stream,
                        "storage_path": storage_path,
                        "physical_line": index,
                        **exc.details,
                    },
                ) from exc
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                if is_final_line and not has_newline:
                    raise _StrictStreamIntegrityError(
                        "strict event stream has a malformed non-newline-terminated tail",
                        code="torn_tail",
                        details={
                            "stream": stream,
                            "storage_path": storage_path,
                            "physical_line": index,
                            "recovery_required": True,
                        },
                    ) from exc
                raise _StrictStreamIntegrityError(
                    "strict event stream has malformed middle corruption",
                    code="stream_corruption",
                    details={"stream": stream, "storage_path": storage_path, "physical_line": index},
                ) from exc
            expected_seq = len(events) + 1
            if event.seq != expected_seq:
                raise _StrictStreamIntegrityError(
                    "strict event stream sequence is not contiguous",
                    code="sequence_violation",
                    details={
                        "stream": stream,
                        "storage_path": storage_path,
                        "physical_line": index,
                        "expected_seq": expected_seq,
                        "actual_seq": event.seq,
                    },
                )
            events.append(event)
        return events

    @staticmethod
    def _split_strict_physical_lines(content: str) -> list[tuple[str, bool]]:
        """Split JSONL only at LF boundaries, recognizing an adjacent CR as CRLF."""

        physical_lines: list[tuple[str, bool]] = []
        offset = 0
        while True:
            newline_index = content.find("\n", offset)
            if newline_index < 0:
                if offset < len(content):
                    physical_lines.append((content[offset:], False))
                return physical_lines
            line = content[offset:newline_index]
            if line.endswith("\r"):
                line = line[:-1]
            physical_lines.append((line, True))
            offset = newline_index + 1
            if offset == len(content):
                return physical_lines

    @staticmethod
    def _strict_record_error_code(reason: str, *, field: str | None) -> str:
        """Preserve established stream classifications while keeping raw errors typed."""

        return {
            "unknown_schema_version": "unknown_schema_version",
            "unknown_event_version": "unknown_event_version",
            "integrity_digest_mismatch": "integrity_digest_mismatch",
            "invalid_integer": "invalid_raw_integer",
            "invalid_json": "stream_corruption",
        }.get(
            reason,
            "missing_integrity_digest"
            if reason == "missing_required_field" and field == "integrity_digest"
            else "strict_record_corruption",
        )

    @staticmethod
    def _validate_strict_limit(value: int, *, field: str) -> int:
        if type(value) is not int or value < 1:
            raise ValueError(f"{field} must be an exact positive integer")
        return value

    def _matches(
        self,
        *,
        envelope: EventEnvelope,
        event_type: str | None,
        run_id: str | None,
        task_id: str | None,
    ) -> bool:
        if event_type:
            normalized_type = self._normalize_stream(event_type)
            if envelope.event_type != normalized_type:
                return False
        if run_id:
            run_token = str(run_id).strip()
            event_run_id = str(envelope.metadata.get("run_id") or envelope.payload.get("run_id") or "").strip()
            if event_run_id != run_token:
                return False
        if task_id:
            task_token = str(task_id).strip()
            event_task_id = str(envelope.metadata.get("task_id") or envelope.payload.get("task_id") or "").strip()
            if event_task_id != task_token:
                return False
        return True

    def _resolve_runtime_stream_path(self, logical_path: str) -> str:
        """Return a checked path for compatibility diagnostics, never for stream I/O."""

        resolved = Path(self._kernel_fs.resolve_path(logical_path)).resolve()
        runtime_root = Path(self._storage_identity.runtime_root).resolve()
        try:
            common_root = os.path.commonpath((str(runtime_root), str(resolved)))
        except ValueError as exc:
            raise EventSourcingError(
                "event stream storage root is incompatible with workspace identity",
            ) from exc
        if common_root != str(runtime_root):
            raise EventSourcingError("event stream escaped workspace runtime root")
        return str(resolved)

    def _normalize_root(self, value: str) -> str:
        token = str(value or "").strip().replace("\\", "/")
        if not token:
            raise ValueError("root_logical_dir is required")
        return token.rstrip("/")

    def _normalize_stream(self, value: str) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("stream token is required")
        invalid_chars = ("/", "\\", "..", " ")
        if any(ch in token for ch in invalid_chars):
            raise ValueError(f"stream token contains invalid characters: {value!r}")
        return token

    def _normalize_optional(self, value: str | None) -> str | None:
        if value is None:
            return None
        token = str(value).strip()
        if not token:
            return None
        return self._normalize_stream(token)


def query_stream_events(
    workspace: str,
    *,
    stream: str,
    limit: int = 100,
    offset: int = 0,
    event_type: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
) -> EventQueryResult:
    store = JsonlEventStore(workspace)
    return store.query(
        stream=stream,
        limit=limit,
        offset=offset,
        event_type=event_type,
        run_id=run_id,
        task_id=task_id,
    )
