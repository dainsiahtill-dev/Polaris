"""Segmented JSONL authority storage with bounded healthy-path I/O."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from polaris.kernelone.events.sourcing.models import new_event_id, utc_now_iso
from polaris.kernelone.fs import KernelFileSystem, LockedRegularFileError, LockedRegularFileSetV1
from polaris.kernelone.fs.contracts import DurabilityMode, validate_durability
from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import WorkspaceRuntimeIdentity, resolve_workspace_runtime_identity

_STREAM_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SEGMENT_RE = re.compile(r"^segment-(\d{6})\.jsonl$")
_LOCATOR_MANIFEST_RE = re.compile(r"^([0-9a-f]{2})\.json$")
_EVENT_SCHEMA = "kernelone.segmented_event.v1"
_SEAL_SCHEMA = "kernelone.segmented_seal.v1"
_CURSOR_SCHEMA = "kernelone.segmented_cursor.v1"
_LOCATOR_SCHEMA = "kernelone.segmented_locator.v1"
_LOCATOR_MANIFEST_SCHEMA = "kernelone.segmented_locator_manifest.v1"
_MAX_SEGMENT_EVENTS = 511
_MAX_RECORD_BYTES = 4096
_ZERO_HASH = "0" * 64


class SegmentedEventStoreError(RuntimeError):
    def __init__(self, message: str, *, code: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class SegmentedStoredEventV1:
    event_id: str
    logical_stream: str
    global_seq: int
    segment_index: int
    local_seq: int
    event_type: str
    source: str
    payload: dict[str, Any]
    idempotency_key: str
    occurred_at: str
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class SegmentedLedgerHeadV1:
    logical_stream: str
    total_count: int
    segment_count: int
    global_seq: int
    tail_segment_index: int | None
    tail_local_seq: int
    head_hash: str
    storage_prefix: str
    storage_bytes: int


@dataclass(frozen=True, slots=True)
class SegmentedQueryResultV1:
    logical_stream: str
    events: tuple[SegmentedStoredEventV1, ...]
    total_count: int
    continuation: str | None
    head_hash: str
    captured_head: SegmentedLedgerHeadV1


@dataclass(frozen=True, slots=True)
class _Cursor:
    schema: str
    logical_stream: str
    total_count: int
    segment_count: int
    tail_segment_index: int | None
    tail_segment_event_count: int
    head_hash: str
    tail_seal_hash: str
    last_seal_hash: str
    tail_previous_seal_hash: str
    tail_first_previous_event_hash: str
    tail_content_hash: str
    storage_bytes: int
    locator_shard_hashes: dict[str, str]


class SegmentedJsonlEventStore:
    """One logical ledger split into sealed, independently bounded segments."""

    def __init__(
        self,
        workspace: str,
        *,
        logical_stream: str = "kernelone.segmented.default",
        segment_max_events: int = _MAX_SEGMENT_EVENTS,
        kernel_fs: KernelFileSystem | None = None,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        workspace_token = str(workspace or "").strip()
        stream_token = str(logical_stream or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        if not _STREAM_RE.fullmatch(stream_token):
            raise ValueError("logical_stream must use only letters, digits, dot, underscore, or dash")
        if isinstance(segment_max_events, bool) or not isinstance(segment_max_events, int):
            raise ValueError("segment_max_events must be an int")
        if not 1 <= segment_max_events <= _MAX_SEGMENT_EVENTS:
            raise ValueError(f"segment_max_events must be between 1 and {_MAX_SEGMENT_EVENTS}")
        self._storage_identity = resolve_workspace_runtime_identity(workspace_token)
        self._kernel_fs = kernel_fs or KernelFileSystem(self._storage_identity.workspace_abs, get_default_adapter())
        self._logical_stream = stream_token
        self._segment_max_events = segment_max_events
        self._lock_timeout_seconds = float(lock_timeout_seconds)
        key = hashlib.sha256(stream_token.encode("utf-8")).hexdigest()[:24]
        self._storage_prefix = f"runtime/events/.segmented/{key}"
        self._cursor_path = f"{self._storage_prefix}/cursor.json"
        self._control_logical_path = f"runtime/events/{stream_token}.segmented.control"

    @property
    def storage_identity(self) -> WorkspaceRuntimeIdentity:
        return self._storage_identity

    @property
    def logical_stream(self) -> str:
        return self._logical_stream

    @property
    def storage_prefix(self) -> str:
        return self._storage_prefix

    @property
    def control_logical_path(self) -> str:
        return self._control_logical_path

    @property
    def cursor_absolute_path(self) -> str:
        return str(self._kernel_fs.resolve_path(self._cursor_path))

    def segment_logical_path(self, segment_index: int) -> str:
        if segment_index < 0:
            raise ValueError("segment_index must be >= 0")
        return f"{self._storage_prefix}/segments/segment-{segment_index:06d}.jsonl"

    def segment_absolute_path(self, segment_index: int) -> str:
        return str(self._kernel_fs.resolve_path(self.segment_logical_path(segment_index)))

    def locator_logical_path(self, idempotency_key: str) -> str:
        digest = hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()
        return f"{self._storage_prefix}/locators/{digest[:2]}/{digest}.json"

    def locator_absolute_path(self, idempotency_key: str) -> str:
        return str(self._kernel_fs.resolve_path(self.locator_logical_path(idempotency_key)))

    def ensure(self) -> SegmentedLedgerHeadV1:
        """Perform the restart/full-integrity pass and atomically rebuild indexes."""

        with self._locked():
            _events, cursor = self._full_scan_and_rebuild_locked()
            return self._head(cursor)

    def append(
        self,
        *,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        durability: DurabilityMode = "fsync",
        expected_global_seq: int | None = None,
        require_idempotency_replay: bool = False,
    ) -> SegmentedStoredEventV1:
        durability = validate_durability(durability)
        event_type = str(event_type or "").strip()
        source = str(source or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not event_type or not source or not idempotency_key:
            raise ValueError("event_type, source, and idempotency_key are required")
        payload_copy = self._canonical_mapping(payload)
        if expected_global_seq is not None and (
            isinstance(expected_global_seq, bool) or not isinstance(expected_global_seq, int) or expected_global_seq < 1
        ):
            raise ValueError("expected_global_seq must be >= 1 or None")
        if not isinstance(require_idempotency_replay, bool):
            raise ValueError("require_idempotency_replay must be a bool")

        with self._locked():
            cursor = self._load_or_rebuild_cursor_locked()
            cursor = self._validate_tail_cursor_locked(cursor)
            replay, cursor = self._read_locator_event_locked(idempotency_key, cursor=cursor)
            if replay is not None:
                self._require_same_semantics(replay, event_type=event_type, source=source, payload=payload_copy)
                return replay
            if require_idempotency_replay:
                raise SegmentedEventStoreError(
                    "required idempotency replay is missing",
                    code="idempotency_replay_missing",
                    details={"idempotency_key": idempotency_key},
                )
            next_seq = cursor.total_count + 1
            if expected_global_seq is not None and expected_global_seq != next_seq:
                raise SegmentedEventStoreError(
                    "expected global sequence drift",
                    code="expected_global_sequence_drift",
                    details={"expected": expected_global_seq, "actual": next_seq},
                )
            segment_index = cursor.tail_segment_index if cursor.tail_segment_index is not None else 0
            local_seq = cursor.tail_segment_event_count + 1
            if local_seq > self._segment_max_events:
                segment_index += 1
                local_seq = 1
            new_tail = segment_index != cursor.tail_segment_index
            tail_previous_seal_hash = cursor.last_seal_hash if new_tail else cursor.tail_previous_seal_hash
            tail_first_previous_event_hash = cursor.head_hash if new_tail else cursor.tail_first_previous_event_hash
            record: dict[str, Any] = {
                "schema": _EVENT_SCHEMA,
                "event_id": new_event_id(),
                "logical_stream": self._logical_stream,
                "global_seq": next_seq,
                "segment_index": segment_index,
                "local_seq": local_seq,
                "event_type": event_type,
                "source": source,
                "payload": payload_copy,
                "idempotency_key": idempotency_key,
                "occurred_at": utc_now_iso(),
                "previous_event_hash": cursor.head_hash,
            }
            record["event_hash"] = self._hash_mapping(record)
            self._validate_record_size(record)
            line_bytes = self._line_size(record)
            path = self.segment_logical_path(segment_index)
            try:
                self._kernel_fs.append_jsonl(path, record, durability=durability)
            except Exception:
                recovered = self._reconcile_ambiguous_event_locked(
                    cursor=cursor,
                    expected_record=record,
                    durability=durability,
                )
                if recovered is None:
                    raise
                return recovered

            stored = self._decode_event(record)
            seal_hash = _ZERO_HASH if new_tail else cursor.tail_seal_hash
            seal_bytes = 0
            if local_seq == self._segment_max_events:
                try:
                    seal_hash, seal_bytes = self._ensure_tail_seal_locked(
                        stored,
                        previous_seal_hash=tail_previous_seal_hash,
                        durability=durability,
                    )
                except Exception:
                    recovered = self._reconcile_ambiguous_event_locked(
                        cursor=cursor,
                        expected_record=record,
                        durability=durability,
                    )
                    if recovered is None:
                        raise
                    return recovered
            updated = _Cursor(
                schema=_CURSOR_SCHEMA,
                logical_stream=self._logical_stream,
                total_count=next_seq,
                segment_count=segment_index + 1,
                tail_segment_index=segment_index,
                tail_segment_event_count=local_seq,
                head_hash=stored.event_hash,
                tail_seal_hash=seal_hash,
                last_seal_hash=seal_hash if seal_hash != _ZERO_HASH else cursor.last_seal_hash,
                tail_previous_seal_hash=tail_previous_seal_hash,
                tail_first_previous_event_hash=tail_first_previous_event_hash,
                tail_content_hash=self._hash_bytes(self._kernel_fs.read_bytes(path)),
                storage_bytes=cursor.storage_bytes + line_bytes + seal_bytes,
                locator_shard_hashes=dict(cursor.locator_shard_hashes),
            )
            try:
                self._write_locator_locked(stored)
                updated = self._update_locator_manifest_locked(updated, stored.idempotency_key)
                self._write_cursor_locked(updated)
            except (OSError, SegmentedEventStoreError, TypeError, ValueError) as reconciliation_error:
                _events, rebuilt = self._full_scan_and_rebuild_locked()
                replay, rebuilt = self._read_locator_event_locked(idempotency_key, cursor=rebuilt)
                if replay is None or rebuilt.total_count < next_seq:
                    raise SegmentedEventStoreError(
                        "ambiguous append reconciliation failed",
                        code="ambiguous_append_reconciliation_failed",
                    ) from reconciliation_error
                self._require_same_semantics(replay, event_type=event_type, source=source, payload=payload_copy)
                return replay
            return stored

    def head(self, *, strict_integrity: bool = True) -> SegmentedLedgerHeadV1:
        with self._locked():
            if strict_integrity:
                _events, cursor = self._full_scan_and_rebuild_locked()
            else:
                cursor = self._load_or_rebuild_cursor_locked()
                cursor = self._validate_tail_cursor_locked(cursor)
            return self._head(cursor)

    def query(
        self,
        *,
        limit: int = 100,
        continuation: str | None = None,
        strict_integrity: bool = True,
    ) -> SegmentedQueryResultV1:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 511:
            raise ValueError("limit must be between 1 and 511")
        with self._locked():
            if continuation:
                state = self._decode_continuation(continuation)
                cursor = self._load_or_rebuild_cursor_locked()
                cursor = self._validate_tail_cursor_locked(cursor)
                if cursor.total_count < state["head_seq"]:
                    raise SegmentedEventStoreError(
                        "captured head is no longer readable", code="captured_head_unreadable"
                    )
                self._validate_captured_head_locked(state)
                events, next_state = self._read_page_from_locked(state=state, limit=limit)
                captured = self._head_from_continuation(state)
            else:
                if strict_integrity:
                    all_events, cursor = self._full_scan_and_rebuild_locked()
                    events = list(all_events[:limit])
                else:
                    cursor = self._load_or_rebuild_cursor_locked()
                    cursor = self._validate_tail_cursor_locked(cursor)
                    state = self._initial_page_state(cursor)
                    events, _next = self._read_page_from_locked(state=state, limit=limit)
                captured = self._head(cursor)
                next_state = self._next_page_state(captured, events) if events else None
            if events and events[-1].global_seq == captured.global_seq:
                if events[-1].event_hash != captured.head_hash:
                    raise SegmentedEventStoreError("captured head hash mismatch", code="captured_head_hash_mismatch")
                next_state = None
            token = self._encode_continuation(next_state) if next_state is not None else None
            return SegmentedQueryResultV1(
                logical_stream=self._logical_stream,
                events=tuple(events),
                total_count=captured.total_count,
                continuation=token,
                head_hash=captured.head_hash,
                captured_head=captured,
            )

    def _full_scan_and_rebuild_locked(self) -> tuple[tuple[SegmentedStoredEventV1, ...], _Cursor]:
        indices = self._discover_segments_locked()
        events: list[SegmentedStoredEventV1] = []
        prior_hash = _ZERO_HASH
        prior_seal = _ZERO_HASH
        storage_bytes = 0
        tail_seal = _ZERO_HASH
        tail_previous_seal = _ZERO_HASH
        tail_first_previous_event = _ZERO_HASH
        tail_content_hash = _ZERO_HASH
        for position, index in enumerate(indices):
            raw = self._kernel_fs.read_bytes(self.segment_logical_path(index))
            storage_bytes += len(raw)
            segment_previous_event = prior_hash
            segment_previous_seal = prior_seal
            segment_events, seal = self._parse_segment_bytes(
                raw,
                segment_index=index,
                expected_global_seq=len(events) + 1,
                prior_event_hash=prior_hash,
            )
            is_tail = position == len(indices) - 1
            if not is_tail or len(segment_events) == self._segment_max_events:
                prior_seal = self._validate_seal(
                    seal,
                    segment_index=index,
                    events=segment_events,
                    previous_seal_hash=prior_seal,
                )
                tail_seal = prior_seal
            elif seal is not None:
                raise SegmentedEventStoreError("premature segment seal", code="premature_segment_seal")
            if segment_events:
                prior_hash = segment_events[-1].event_hash
            if is_tail:
                tail_seal = prior_seal if seal is not None else _ZERO_HASH
                tail_previous_seal = segment_previous_seal
                tail_first_previous_event = segment_previous_event
                tail_content_hash = self._hash_bytes(raw)
            events.extend(segment_events)
        tail = events[-1] if events else None
        idempotency_keys = tuple(event.idempotency_key for event in events)
        if len(set(idempotency_keys)) != len(idempotency_keys):
            raise SegmentedEventStoreError(
                "duplicate idempotency key in authoritative segments",
                code="duplicate_idempotency_key",
            )
        locator_shard_hashes = self._rebuild_locator_indexes_locked(tuple(events))
        cursor = _Cursor(
            schema=_CURSOR_SCHEMA,
            logical_stream=self._logical_stream,
            total_count=len(events),
            segment_count=len(indices),
            tail_segment_index=indices[-1] if indices else None,
            tail_segment_event_count=sum(1 for event in events if indices and event.segment_index == indices[-1]),
            head_hash=tail.event_hash if tail else _ZERO_HASH,
            tail_seal_hash=tail_seal,
            last_seal_hash=prior_seal,
            tail_previous_seal_hash=tail_previous_seal,
            tail_first_previous_event_hash=tail_first_previous_event,
            tail_content_hash=tail_content_hash,
            storage_bytes=storage_bytes,
            locator_shard_hashes=locator_shard_hashes,
        )
        self._write_cursor_locked(cursor)
        return tuple(events), cursor

    def _discover_segments_locked(self) -> tuple[int, ...]:
        logical_directory = f"{self._storage_prefix}/segments"
        if not self._kernel_fs.exists(logical_directory):
            return ()
        directory = self._kernel_fs.resolve_path(logical_directory)
        names = tuple(sorted(entry.name for entry in os.scandir(directory)))
        matches = [_SEGMENT_RE.fullmatch(name) for name in names]
        if any(match is None for match in matches):
            raise SegmentedEventStoreError("unexpected segmented filename", code="unexpected_segment_filename")
        indices = tuple(int(match.group(1)) for match in matches if match is not None)
        if indices != tuple(range(len(indices))):
            raise SegmentedEventStoreError("segment gap", code="segment_gap", details={"indices": indices})
        return indices

    def _load_or_rebuild_cursor_locked(self) -> _Cursor:
        try:
            cursor = self._load_cursor_locked()
        except SegmentedEventStoreError:
            cursor = None
        if cursor is None:
            _events, cursor = self._full_scan_and_rebuild_locked()
        return cursor

    def _validate_tail_cursor_locked(self, cursor: _Cursor) -> _Cursor:
        if cursor.total_count == 0:
            if self._discover_segments_locked():
                _events, cursor = self._full_scan_and_rebuild_locked()
            return cursor
        if cursor.tail_segment_index is None:
            raise SegmentedEventStoreError("invalid cursor tail", code="invalid_cursor")
        try:
            raw = self._kernel_fs.read_bytes(self.segment_logical_path(cursor.tail_segment_index))
            if self._hash_bytes(raw) != cursor.tail_content_hash:
                raise SegmentedEventStoreError("tail content drift", code="tail_content_drift")
            first_global = cursor.total_count - cursor.tail_segment_event_count + 1
            events, seal = self._parse_segment_bytes(
                raw,
                segment_index=cursor.tail_segment_index,
                expected_global_seq=first_global,
                prior_event_hash=cursor.tail_first_previous_event_hash,
            )
            if (
                len(events) != cursor.tail_segment_event_count
                or not events
                or events[-1].global_seq != cursor.total_count
                or events[-1].event_hash != cursor.head_hash
            ):
                raise SegmentedEventStoreError("cursor drift", code="cursor_drift")
            if cursor.tail_segment_event_count == self._segment_max_events:
                seal_hash = self._validate_seal(
                    seal,
                    segment_index=cursor.tail_segment_index,
                    events=events,
                    previous_seal_hash=cursor.tail_previous_seal_hash,
                )
                if seal_hash != cursor.tail_seal_hash or seal_hash != cursor.last_seal_hash:
                    raise SegmentedEventStoreError("tail seal anchor drift", code="tail_seal_anchor_drift")
            elif seal is not None or cursor.tail_seal_hash != _ZERO_HASH:
                raise SegmentedEventStoreError("premature segment seal", code="premature_segment_seal")
            return cursor
        except (OSError, SegmentedEventStoreError, UnicodeDecodeError, json.JSONDecodeError):
            _events, rebuilt = self._full_scan_and_rebuild_locked()
            return rebuilt

    def _read_locator_event_locked(
        self,
        key: str,
        *,
        cursor: _Cursor,
    ) -> tuple[SegmentedStoredEventV1 | None, _Cursor]:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        shard = digest[:2]
        try:
            digests = self._read_locator_manifest_locked(cursor, shard)
        except (OSError, SegmentedEventStoreError, TypeError, ValueError, json.JSONDecodeError):
            _events, cursor = self._full_scan_and_rebuild_locked()
            digests = self._read_locator_manifest_locked(cursor, shard)
        if digest not in digests:
            return None, cursor
        path = self.locator_logical_path(key)
        if not self._kernel_fs.exists(path):
            _events, cursor = self._full_scan_and_rebuild_locked()
            if not self._kernel_fs.exists(path):
                raise SegmentedEventStoreError("declared locator is missing", code="locator_missing")
        try:
            return self._read_locator_target_locked(key), cursor
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, SegmentedEventStoreError):
            _events, cursor = self._full_scan_and_rebuild_locked()
            try:
                return self._read_locator_target_locked(key), cursor
            except Exception as exc:
                raise SegmentedEventStoreError("locator rebuild failed", code="locator_rebuild_failed") from exc

    def _read_locator_target_locked(self, key: str) -> SegmentedStoredEventV1:
        locator = self._kernel_fs.read_json(self.locator_logical_path(key))
        if (
            locator["schema"] != _LOCATOR_SCHEMA
            or locator["logical_stream"] != self._logical_stream
            or locator["idempotency_key"] != key
        ):
            raise ValueError("locator binding mismatch")
        segment = int(locator["segment_index"])
        local = int(locator["local_seq"])
        raw = self._kernel_fs.read_bytes(self.segment_logical_path(segment))
        events, _seal = self._parse_segment_bytes(
            raw,
            segment_index=segment,
            expected_global_seq=int(locator["global_seq"]) - local + 1,
            prior_event_hash=None,
        )
        event = events[local - 1]
        if (
            event.idempotency_key != key
            or event.global_seq != int(locator["global_seq"])
            or event.event_hash != locator["event_hash"]
        ):
            raise ValueError("locator target mismatch")
        return event

    def _reconcile_ambiguous_event_locked(
        self,
        *,
        cursor: _Cursor,
        expected_record: Mapping[str, Any],
        durability: DurabilityMode,
    ) -> SegmentedStoredEventV1 | None:
        segment_index = int(expected_record["segment_index"])
        path = self.segment_logical_path(segment_index)
        if not self._kernel_fs.exists(path):
            return None
        raw = self._kernel_fs.read_bytes(path)
        expected_first = cursor.total_count - cursor.tail_segment_event_count + 1
        if segment_index != cursor.tail_segment_index:
            expected_first = cursor.total_count + 1
        events, _seal = self._parse_segment_bytes(
            raw,
            segment_index=segment_index,
            expected_global_seq=expected_first,
            prior_event_hash=None,
        )
        expected_hash = str(expected_record["event_hash"])
        event = next((item for item in events if item.event_hash == expected_hash), None)
        if event is None:
            return None
        self._require_same_semantics(
            event,
            event_type=str(expected_record["event_type"]),
            source=str(expected_record["source"]),
            payload=dict(expected_record["payload"]),
        )
        if event.local_seq == self._segment_max_events:
            self._ensure_tail_seal_locked(
                event,
                previous_seal_hash=cursor.tail_previous_seal_hash,
                durability=durability,
            )
        _events, rebuilt = self._full_scan_and_rebuild_locked()
        replay, _cursor = self._read_locator_event_locked(event.idempotency_key, cursor=rebuilt)
        return replay

    def _ensure_tail_seal_locked(
        self,
        event: SegmentedStoredEventV1,
        *,
        previous_seal_hash: str,
        durability: DurabilityMode,
    ) -> tuple[str, int]:
        path = self.segment_logical_path(event.segment_index)
        raw = self._kernel_fs.read_bytes(path)
        lines = raw.decode("utf-8").splitlines()
        if lines:
            last = json.loads(lines[-1])
            if isinstance(last, dict) and last.get("schema") == _SEAL_SCHEMA:
                seal_hash = self._validate_seal(
                    last,
                    segment_index=event.segment_index,
                    events=self._parse_segment_bytes(
                        raw,
                        segment_index=event.segment_index,
                        expected_global_seq=event.global_seq - event.local_seq + 1,
                        prior_event_hash=None,
                    )[0],
                    previous_seal_hash=previous_seal_hash,
                )
                return seal_hash, 0
        seal: dict[str, Any] = {
            "schema": _SEAL_SCHEMA,
            "logical_stream": self._logical_stream,
            "segment_index": event.segment_index,
            "event_count": event.local_seq,
            "last_global_seq": event.global_seq,
            "head_hash": event.event_hash,
            "previous_seal_hash": previous_seal_hash,
            "next_segment_index": event.segment_index + 1,
        }
        seal["seal_hash"] = self._hash_mapping(seal)
        self._validate_record_size(seal)
        self._kernel_fs.append_jsonl(path, seal, durability=durability)
        return str(seal["seal_hash"]), self._line_size(seal)

    def _parse_segment_bytes(
        self,
        raw: bytes,
        *,
        segment_index: int,
        expected_global_seq: int,
        prior_event_hash: str | None,
    ) -> tuple[list[SegmentedStoredEventV1], dict[str, Any] | None]:
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise SegmentedEventStoreError("invalid segment encoding", code="invalid_segment_encoding") from exc
        if not lines:
            raise SegmentedEventStoreError("empty segment", code="empty_segment")
        events: list[SegmentedStoredEventV1] = []
        seal: dict[str, Any] | None = None
        prior = prior_event_hash
        for number, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SegmentedEventStoreError("invalid segmented JSONL record", code="invalid_jsonl_record") from exc
            if not isinstance(record, dict):
                raise SegmentedEventStoreError("invalid segmented record", code="invalid_record")
            self._validate_record_size(record)
            if record.get("schema") == _SEAL_SCHEMA:
                if number != len(lines) or seal is not None:
                    raise SegmentedEventStoreError("invalid segment seal position", code="invalid_segment_seal")
                seal = record
                continue
            if seal is not None or record.get("schema") != _EVENT_SCHEMA:
                raise SegmentedEventStoreError("unknown segmented record schema", code="unknown_record_schema")
            event = self._decode_event(record)
            expected_hash = self._hash_mapping({key: value for key, value in record.items() if key != "event_hash"})
            if (
                event.logical_stream != self._logical_stream
                or event.segment_index != segment_index
                or event.local_seq != len(events) + 1
                or event.global_seq != expected_global_seq + len(events)
                or event.event_hash != expected_hash
                or (prior is not None and event.previous_event_hash != prior)
            ):
                raise SegmentedEventStoreError("segmented event integrity violation", code="event_integrity_violation")
            prior = event.event_hash
            events.append(event)
        if not events or len(events) > self._segment_max_events:
            raise SegmentedEventStoreError("invalid segment event count", code="segment_event_limit_exceeded")
        return events, seal

    def _validate_seal(
        self,
        seal: Mapping[str, Any] | None,
        *,
        segment_index: int,
        events: list[SegmentedStoredEventV1],
        previous_seal_hash: str,
    ) -> str:
        if seal is None or not events:
            raise SegmentedEventStoreError("missing segment seal", code="missing_segment_seal")
        expected_hash = self._hash_mapping({key: value for key, value in seal.items() if key != "seal_hash"})
        if (
            seal.get("logical_stream") != self._logical_stream
            or seal.get("segment_index") != segment_index
            or seal.get("event_count") != len(events)
            or seal.get("last_global_seq") != events[-1].global_seq
            or seal.get("head_hash") != events[-1].event_hash
            or seal.get("previous_seal_hash") != previous_seal_hash
            or seal.get("next_segment_index") != segment_index + 1
            or seal.get("seal_hash") != expected_hash
        ):
            raise SegmentedEventStoreError("invalid segment seal", code="invalid_segment_seal")
        return str(seal["seal_hash"])

    def _read_page_from_locked(
        self,
        *,
        state: Mapping[str, Any],
        limit: int,
    ) -> tuple[list[SegmentedStoredEventV1], dict[str, Any] | None]:
        next_seq = int(state["next_seq"])
        segment_index = int(state["next_segment"])
        local_seq = int(state["next_local"])
        prior_hash = str(state["running_hash"])
        head_seq = int(state["head_seq"])
        page: list[SegmentedStoredEventV1] = []
        while next_seq <= head_seq and len(page) < limit:
            raw = self._kernel_fs.read_bytes(self.segment_logical_path(segment_index))
            first_global = next_seq - local_seq + 1
            events, _seal = self._parse_segment_bytes(
                raw,
                segment_index=segment_index,
                expected_global_seq=first_global,
                prior_event_hash=None,
            )
            while local_seq <= len(events) and next_seq <= head_seq and len(page) < limit:
                event = events[local_seq - 1]
                if event.global_seq != next_seq or event.previous_event_hash != prior_hash:
                    raise SegmentedEventStoreError(
                        "continuation integrity mismatch", code="continuation_integrity_mismatch"
                    )
                page.append(event)
                prior_hash = event.event_hash
                next_seq += 1
                local_seq += 1
            if local_seq > len(events):
                segment_index += 1
                local_seq = 1
        next_state = None
        if next_seq <= head_seq:
            next_state = dict(state)
            next_state.update(
                {
                    "next_seq": next_seq,
                    "next_segment": segment_index,
                    "next_local": local_seq,
                    "running_hash": prior_hash,
                }
            )
        elif prior_hash != state["head_hash"]:
            raise SegmentedEventStoreError("captured head hash mismatch", code="captured_head_hash_mismatch")
        return page, next_state

    def _initial_page_state(self, head: _Cursor) -> dict[str, Any]:
        return {
            "schema": "kernelone.segmented_continuation.v1",
            "logical_stream": self._logical_stream,
            "head_seq": head.total_count,
            "head_hash": head.head_hash,
            "head_segment_count": head.segment_count,
            "head_tail_segment": head.tail_segment_index,
            "head_tail_local": head.tail_segment_event_count,
            "head_storage_bytes": head.storage_bytes,
            "next_seq": 1,
            "next_segment": 0,
            "next_local": 1,
            "running_hash": _ZERO_HASH,
        }

    def _next_page_state(
        self,
        head: SegmentedLedgerHeadV1,
        events: tuple[SegmentedStoredEventV1, ...] | list[SegmentedStoredEventV1],
    ) -> dict[str, Any] | None:
        if not events or events[-1].global_seq >= head.global_seq:
            return None
        last = events[-1]
        next_segment = last.segment_index
        next_local = last.local_seq + 1
        if next_local > self._segment_max_events:
            next_segment += 1
            next_local = 1
        return {
            "schema": "kernelone.segmented_continuation.v1",
            "logical_stream": self._logical_stream,
            "head_seq": head.global_seq,
            "head_hash": head.head_hash,
            "head_segment_count": head.segment_count,
            "head_tail_segment": head.tail_segment_index,
            "head_tail_local": head.tail_local_seq,
            "head_storage_bytes": head.storage_bytes,
            "next_seq": last.global_seq + 1,
            "next_segment": next_segment,
            "next_local": next_local,
            "running_hash": last.event_hash,
        }

    def _head_from_continuation(self, state: Mapping[str, Any]) -> SegmentedLedgerHeadV1:
        return SegmentedLedgerHeadV1(
            logical_stream=self._logical_stream,
            total_count=int(state["head_seq"]),
            segment_count=int(state["head_segment_count"]),
            global_seq=int(state["head_seq"]),
            tail_segment_index=(
                int(state["head_tail_segment"]) if state.get("head_tail_segment") is not None else None
            ),
            tail_local_seq=int(state["head_tail_local"]),
            head_hash=str(state["head_hash"]),
            storage_prefix=self._storage_prefix,
            storage_bytes=int(state["head_storage_bytes"]),
        )

    def _validate_captured_head_locked(self, state: Mapping[str, Any]) -> None:
        segment_index = int(state["head_tail_segment"])
        local_seq = int(state["head_tail_local"])
        head_seq = int(state["head_seq"])
        raw = self._kernel_fs.read_bytes(self.segment_logical_path(segment_index))
        events, _seal = self._parse_segment_bytes(
            raw,
            segment_index=segment_index,
            expected_global_seq=head_seq - local_seq + 1,
            prior_event_hash=None,
        )
        try:
            event = events[local_seq - 1]
        except IndexError as exc:
            raise SegmentedEventStoreError(
                "captured head position is unreadable", code="captured_head_unreadable"
            ) from exc
        if event.global_seq != head_seq or event.event_hash != state["head_hash"]:
            raise SegmentedEventStoreError("captured head binding mismatch", code="captured_head_binding_mismatch")

    def _encode_continuation(self, state: Mapping[str, Any] | None) -> str | None:
        if state is None:
            return None
        payload = dict(state)
        payload["binding_hash"] = self._hash_mapping(payload)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _decode_continuation(self, token: str) -> dict[str, Any]:
        try:
            padding = "=" * (-len(token) % 4)
            raw = json.loads(base64.urlsafe_b64decode(token + padding).decode("utf-8"))
            required = {
                "schema",
                "logical_stream",
                "head_seq",
                "head_hash",
                "head_segment_count",
                "head_tail_segment",
                "head_tail_local",
                "head_storage_bytes",
                "next_seq",
                "next_segment",
                "next_local",
                "running_hash",
                "binding_hash",
            }
            if not isinstance(raw, dict) or set(raw) != required:
                raise ValueError("continuation shape")
            if raw["schema"] != "kernelone.segmented_continuation.v1" or raw["logical_stream"] != self._logical_stream:
                raise ValueError("continuation binding")
            binding_hash = str(raw.pop("binding_hash"))
            if binding_hash != self._hash_mapping(raw):
                raise ValueError("continuation hash")
            return raw
        except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SegmentedEventStoreError("invalid continuation", code="invalid_continuation") from exc

    def _load_cursor_locked(self) -> _Cursor | None:
        if not self._kernel_fs.exists(self._cursor_path):
            return None
        try:
            raw = self._kernel_fs.read_json(self._cursor_path)
            cursor = _Cursor(
                schema=str(raw["schema"]),
                logical_stream=str(raw["logical_stream"]),
                total_count=int(raw["total_count"]),
                segment_count=int(raw["segment_count"]),
                tail_segment_index=int(raw["tail_segment_index"])
                if raw.get("tail_segment_index") is not None
                else None,
                tail_segment_event_count=int(raw["tail_segment_event_count"]),
                head_hash=str(raw["head_hash"]),
                tail_seal_hash=str(raw["tail_seal_hash"]),
                last_seal_hash=str(raw["last_seal_hash"]),
                tail_previous_seal_hash=str(raw["tail_previous_seal_hash"]),
                tail_first_previous_event_hash=str(raw["tail_first_previous_event_hash"]),
                tail_content_hash=str(raw["tail_content_hash"]),
                storage_bytes=int(raw["storage_bytes"]),
                locator_shard_hashes={str(key): str(value) for key, value in dict(raw["locator_shard_hashes"]).items()},
            )
            if cursor.schema != _CURSOR_SCHEMA or cursor.logical_stream != self._logical_stream:
                raise ValueError("cursor binding")
            return cursor
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SegmentedEventStoreError("invalid segmented cursor", code="invalid_cursor") from exc

    def _write_cursor_locked(self, cursor: _Cursor) -> None:
        self._kernel_fs.write_json_atomic(self._cursor_path, asdict(cursor), indent=2)
        if self._load_cursor_locked() != cursor:
            raise SegmentedEventStoreError("cursor durability verification failed", code="cursor_re_read_failed")

    def _ensure_locator_locked(self, event: SegmentedStoredEventV1) -> None:
        path = self.locator_logical_path(event.idempotency_key)
        expected = self._locator_payload(event)
        if self._kernel_fs.exists(path):
            try:
                if self._kernel_fs.read_json(path) == expected:
                    return
            except (OSError, SegmentedEventStoreError, TypeError, ValueError, json.JSONDecodeError):
                pass
        self._kernel_fs.write_json_atomic(path, expected, indent=2)
        if self._kernel_fs.read_json(path) != expected:
            raise SegmentedEventStoreError("locator durability verification failed", code="locator_re_read_failed")

    def _rebuild_locator_indexes_locked(
        self,
        events: tuple[SegmentedStoredEventV1, ...],
    ) -> dict[str, str]:
        shards: dict[str, set[str]] = {}
        for event in events:
            self._ensure_locator_locked(event)
            digest = hashlib.sha256(event.idempotency_key.encode("utf-8")).hexdigest()
            shards.setdefault(digest[:2], set()).add(digest)
        hashes: dict[str, str] = {}
        for shard, digests in sorted(shards.items()):
            payload = self._locator_manifest_payload(shard, tuple(sorted(digests)))
            self._write_locator_manifest_locked(shard, payload)
            hashes[shard] = self._hash_mapping(payload)
        manifest_directory_logical = f"{self._storage_prefix}/locator-manifests"
        if self._kernel_fs.exists(manifest_directory_logical):
            manifest_directory = self._kernel_fs.resolve_path(manifest_directory_logical)
            for entry in os.scandir(manifest_directory):
                match = _LOCATOR_MANIFEST_RE.fullmatch(entry.name)
                if match is None:
                    raise SegmentedEventStoreError(
                        "unexpected locator manifest filename",
                        code="unexpected_locator_manifest_filename",
                    )
                if match.group(1) not in shards:
                    self._kernel_fs.remove(f"{manifest_directory_logical}/{entry.name}", missing_ok=False)
        return hashes

    def _locator_manifest_logical_path(self, shard: str) -> str:
        return f"{self._storage_prefix}/locator-manifests/{shard}.json"

    def _locator_manifest_payload(self, shard: str, digests: tuple[str, ...]) -> dict[str, Any]:
        return {
            "schema": _LOCATOR_MANIFEST_SCHEMA,
            "logical_stream": self._logical_stream,
            "shard": shard,
            "digests": list(digests),
        }

    def _write_locator_manifest_locked(self, shard: str, payload: Mapping[str, Any]) -> None:
        path = self._locator_manifest_logical_path(shard)
        self._kernel_fs.write_json_atomic(path, dict(payload), indent=2)
        if self._kernel_fs.read_json(path) != dict(payload):
            raise SegmentedEventStoreError(
                "locator manifest durability verification failed",
                code="locator_manifest_re_read_failed",
            )

    def _read_locator_manifest_locked(self, cursor: _Cursor, shard: str) -> tuple[str, ...]:
        path = self._locator_manifest_logical_path(shard)
        expected_hash = cursor.locator_shard_hashes.get(shard)
        if not self._kernel_fs.exists(path):
            if expected_hash is not None:
                raise SegmentedEventStoreError("locator manifest is missing", code="locator_manifest_missing")
            return ()
        payload = self._kernel_fs.read_json(path)
        if (
            payload.get("schema") != _LOCATOR_MANIFEST_SCHEMA
            or payload.get("logical_stream") != self._logical_stream
            or payload.get("shard") != shard
            or not isinstance(payload.get("digests"), list)
        ):
            raise SegmentedEventStoreError("locator manifest is invalid", code="locator_manifest_invalid")
        actual_hash = self._hash_mapping(payload)
        if expected_hash is None or actual_hash != expected_hash:
            raise SegmentedEventStoreError("locator manifest hash drift", code="locator_manifest_hash_drift")
        digests = tuple(str(item) for item in payload["digests"])
        if digests != tuple(sorted(set(digests))):
            raise SegmentedEventStoreError("locator manifest order drift", code="locator_manifest_order_drift")
        return digests

    def _update_locator_manifest_locked(self, cursor: _Cursor, key: str) -> _Cursor:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        shard = digest[:2]
        digests = set(self._read_locator_manifest_locked(cursor, shard))
        digests.add(digest)
        payload = self._locator_manifest_payload(shard, tuple(sorted(digests)))
        self._write_locator_manifest_locked(shard, payload)
        hashes = dict(cursor.locator_shard_hashes)
        hashes[shard] = self._hash_mapping(payload)
        return replace(cursor, locator_shard_hashes=hashes)

    def _write_locator_locked(self, event: SegmentedStoredEventV1) -> None:
        self._kernel_fs.write_json_atomic(
            self.locator_logical_path(event.idempotency_key), self._locator_payload(event), indent=2
        )
        if self._kernel_fs.read_json(self.locator_logical_path(event.idempotency_key)) != self._locator_payload(event):
            raise SegmentedEventStoreError("locator durability verification failed", code="locator_re_read_failed")

    def _locator_payload(self, event: SegmentedStoredEventV1) -> dict[str, Any]:
        return {
            "schema": _LOCATOR_SCHEMA,
            "logical_stream": self._logical_stream,
            "idempotency_key": event.idempotency_key,
            "global_seq": event.global_seq,
            "segment_index": event.segment_index,
            "local_seq": event.local_seq,
            "event_hash": event.event_hash,
        }

    def _locked(self) -> LockedRegularFileSetV1:
        try:
            return LockedRegularFileSetV1.acquire(
                runtime_root=self._storage_identity.runtime_root,
                storage_identity_token=self._storage_identity.token,
                logical_paths=(self._control_logical_path,),
                platform_lock_root=default_platform_lock_root(),
                timeout_seconds=self._lock_timeout_seconds,
            )
        except LockedRegularFileError as exc:
            raise SegmentedEventStoreError(
                "segmented ledger lock unavailable", code=exc.code, details=exc.details
            ) from exc

    def _head(self, cursor: _Cursor) -> SegmentedLedgerHeadV1:
        return SegmentedLedgerHeadV1(
            logical_stream=self._logical_stream,
            total_count=cursor.total_count,
            segment_count=cursor.segment_count,
            global_seq=cursor.total_count,
            tail_segment_index=cursor.tail_segment_index,
            tail_local_seq=cursor.tail_segment_event_count,
            head_hash=cursor.head_hash,
            storage_prefix=self._storage_prefix,
            storage_bytes=cursor.storage_bytes,
        )

    def _empty_cursor(self) -> _Cursor:
        return _Cursor(
            _CURSOR_SCHEMA,
            self._logical_stream,
            0,
            0,
            None,
            0,
            _ZERO_HASH,
            _ZERO_HASH,
            _ZERO_HASH,
            _ZERO_HASH,
            _ZERO_HASH,
            _ZERO_HASH,
            0,
            {},
        )

    @staticmethod
    def _require_same_semantics(
        event: SegmentedStoredEventV1,
        *,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event.event_type != event_type or event.source != source or event.payload != dict(payload):
            raise SegmentedEventStoreError(
                "idempotency conflict",
                code="idempotency_conflict",
                details={"idempotency_key": event.idempotency_key, "global_seq": event.global_seq},
            )

    @staticmethod
    def _canonical_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        try:
            value = json.loads(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON serializable") from exc
        if not isinstance(value, dict):
            raise ValueError("payload must encode as an object")
        return value

    @staticmethod
    def _hash_mapping(payload: Mapping[str, Any]) -> str:
        raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _line_size(payload: Mapping[str, Any]) -> int:
        return len(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1

    @staticmethod
    def _validate_record_size(payload: Mapping[str, Any]) -> None:
        size = len(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if size >= _MAX_RECORD_BYTES:
            raise SegmentedEventStoreError(
                "segmented record exceeds 4KiB fact limit",
                code="record_too_large",
                details={"bytes": size, "limit": _MAX_RECORD_BYTES - 1},
            )

    @staticmethod
    def _decode_event(record: Mapping[str, Any]) -> SegmentedStoredEventV1:
        try:
            payload = record["payload"]
            if not isinstance(payload, dict):
                raise TypeError("payload")
            return SegmentedStoredEventV1(
                event_id=str(record["event_id"]),
                logical_stream=str(record["logical_stream"]),
                global_seq=int(record["global_seq"]),
                segment_index=int(record["segment_index"]),
                local_seq=int(record["local_seq"]),
                event_type=str(record["event_type"]),
                source=str(record["source"]),
                payload=dict(payload),
                idempotency_key=str(record["idempotency_key"]),
                occurred_at=str(record["occurred_at"]),
                previous_event_hash=str(record["previous_event_hash"]),
                event_hash=str(record["event_hash"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SegmentedEventStoreError("invalid segmented event", code="invalid_event") from exc
