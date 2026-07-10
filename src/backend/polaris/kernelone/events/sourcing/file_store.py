"""JSONL-backed event store for versioned event streams."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.jsonl.locking import acquire_lock_fd, release_lock_fd
from polaris.kernelone.fs.jsonl.ops import (
    _read_seq_file,
    _write_seq_file,
    scan_last_seq,
)
from polaris.kernelone.fs.registry import get_default_adapter

from .models import EventEnvelope, EventQueryResult, EventSourcingError, new_event_id, utc_now_iso

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


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
    ) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        self._workspace = workspace_token
        self._root_logical_dir = self._normalize_root(root_logical_dir)
        self._kernel_fs = kernel_fs or KernelFileSystem(self._workspace, get_default_adapter())

    @property
    def workspace(self) -> str:
        return self._workspace

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
    ) -> EventEnvelope:
        """Atomically append one event or return its idempotent predecessor.

        Sequence allocation, idempotency lookup, JSONL persistence, and cursor
        advancement share one stream-scoped critical section.  Callers therefore
        never have to implement their own query-then-append race window.
        """
        payload_dict = dict(payload or {})
        if not payload_dict:
            raise ValueError("payload must not be empty")
        if expected_seq is not None:
            # Coerce defensively: only ints are accepted. bool is a subclass of
            # int but is not a meaningful sequence number, so reject it.
            if isinstance(expected_seq, bool) or not isinstance(expected_seq, int):
                raise ValueError("expected_seq must be an int or None")
            if expected_seq < 1:
                raise ValueError("expected_seq must be >= 1")
        normalized_idempotency_key = str(idempotency_key or "").strip()
        logical_path = self.stream_logical_path(stream)
        absolute_path = str(self._kernel_fs.resolve_path(logical_path))
        return self._append_locked(
            logical_path=logical_path,
            absolute_path=absolute_path,
            stream=stream,
            event_type=event_type,
            source=source,
            payload=payload_dict,
            event_version=event_version,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata,
            expected_seq=expected_seq,
            idempotency_key=normalized_idempotency_key,
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
    ) -> EventQueryResult:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        stream_token = self._normalize_stream(stream)
        logical_path = self.stream_logical_path(stream_token)
        if not self._kernel_fs.exists(logical_path):
            return EventQueryResult(
                stream=stream_token,
                storage_path=logical_path,
                events=(),
                total=0,
                next_offset=0,
            )

        try:
            content = self._kernel_fs.read_text(logical_path, encoding="utf-8")
        except (RuntimeError, ValueError) as exc:
            raise EventSourcingError(
                f"failed to read event stream={stream!r}: {exc}",
            ) from exc

        records = self._parse_records(content=content, storage_path=logical_path)
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

    def _append_locked(
        self,
        *,
        logical_path: str,
        absolute_path: str,
        stream: str,
        event_type: str,
        source: str,
        payload: Mapping[str, Any],
        event_version: int,
        aggregate_id: str | None,
        correlation_id: str | None,
        causation_id: str | None,
        metadata: Mapping[str, Any] | None,
        expected_seq: int | None,
        idempotency_key: str,
    ) -> EventEnvelope:
        """Append under the stream sequence lock.

        The JSONL file is the durable source of truth; the ``.seq`` file is a
        recoverable cursor.  A cursor write failure after a successful JSONL
        append is logged but must not turn a committed effect into an ambiguous
        failure response.
        """

        seq_path = absolute_path + ".seq"
        lock_path = seq_path + ".lock"
        fd = acquire_lock_fd(lock_path, timeout_sec=2.0)
        if fd is None:
            raise EventSourcingError(
                f"failed to acquire event stream lock path={absolute_path!r}",
            )
        try:
            if idempotency_key:
                idempotent_event = self._find_idempotent_event(
                    logical_path=logical_path,
                    idempotency_key=idempotency_key,
                )
                if idempotent_event is not None:
                    if (
                        idempotent_event.event_type != self._normalize_stream(event_type)
                        or idempotent_event.source != self._normalize_stream(source)
                        or idempotent_event.payload != dict(payload)
                    ):
                        raise EventSourcingError(
                            "idempotency conflict: existing event does not match requested event "
                            f"key={idempotency_key!r} path={absolute_path!r}",
                        )
                    if expected_seq is not None and idempotent_event.seq != expected_seq:
                        raise EventSourcingError(
                            "expected_seq drift for idempotent event: "
                            f"requested={expected_seq} actual={idempotent_event.seq} "
                            f"path={absolute_path!r}",
                        )
                    return idempotent_event

            existing = _read_seq_file(seq_path)
            if existing <= 0:
                existing = scan_last_seq(absolute_path, key="seq")
            next_seq = existing + 1
            if expected_seq is not None and next_seq != expected_seq:
                raise EventSourcingError(
                    f"expected_seq drift: requested={expected_seq} actual={next_seq} path={absolute_path!r}",
                )
            envelope = self._build_envelope(
                stream=stream,
                event_type=event_type,
                source=source,
                payload=payload,
                event_version=event_version,
                aggregate_id=aggregate_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                metadata=metadata,
                seq=next_seq,
            )
            try:
                self._kernel_fs.append_jsonl(logical_path, envelope.to_record())
            except (RuntimeError, ValueError) as exc:
                raise EventSourcingError(
                    f"failed to append event stream={stream!r}: {exc}",
                ) from exc
            try:
                _write_seq_file(seq_path, next_seq)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning(
                    "event committed but sequence cursor update failed seq=%s path=%s: %s",
                    next_seq,
                    absolute_path,
                    exc,
                )
            return envelope
        finally:
            release_lock_fd(fd, lock_path)

    def _find_idempotent_event(
        self,
        *,
        logical_path: str,
        idempotency_key: str,
    ) -> EventEnvelope | None:
        if not self._kernel_fs.exists(logical_path):
            return None
        try:
            content = self._kernel_fs.read_text(logical_path, encoding="utf-8")
        except (RuntimeError, ValueError) as exc:
            raise EventSourcingError(
                f"failed to read event stream for idempotency path={logical_path!r}: {exc}",
            ) from exc
        for event in self._parse_records(content=content, storage_path=logical_path):
            if str(event.metadata.get("idempotency_key") or "").strip() == idempotency_key:
                return event
        return None

    def _build_envelope(
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
        metadata: Mapping[str, Any] | None,
        seq: int,
    ) -> EventEnvelope:
        return EventEnvelope(
            event_id=new_event_id(),
            stream=self._normalize_stream(stream),
            event_type=self._normalize_stream(event_type),
            event_version=int(event_version),
            seq=seq,
            occurred_at=utc_now_iso(),
            source=self._normalize_stream(source),
            aggregate_id=self._normalize_optional(aggregate_id),
            correlation_id=self._normalize_optional(correlation_id),
            causation_id=self._normalize_optional(causation_id),
            payload=dict(payload or {}),
            metadata=dict(metadata or {}),
        )

    def _parse_records(self, *, content: str, storage_path: str) -> list[EventEnvelope]:
        events: list[EventEnvelope] = []
        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                events.append(EventEnvelope.from_record(record))
            except (RuntimeError, ValueError) as exc:
                logger.debug("skip malformed event record path=%s: %s", storage_path, exc)
                continue
        events.sort(key=lambda item: item.seq)
        return events

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
