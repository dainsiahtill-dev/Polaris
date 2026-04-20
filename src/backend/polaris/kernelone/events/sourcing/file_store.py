"""JSONL-backed event store for versioned event streams."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.jsonl.ops import _next_seq_for_path
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
    ) -> EventEnvelope:
        payload_dict = dict(payload or {})
        if not payload_dict:
            raise ValueError("payload must not be empty")
        logical_path = self.stream_logical_path(stream)
        absolute_path = str(self._kernel_fs.resolve_path(logical_path))
        seq = self._next_seq(absolute_path)
        envelope = EventEnvelope(
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
            payload=payload_dict,
            metadata=dict(metadata or {}),
        )
        try:
            self._kernel_fs.append_jsonl(logical_path, envelope.to_record())
        except (RuntimeError, ValueError) as exc:
            raise EventSourcingError(
                f"failed to append event stream={stream!r}: {exc}",
            ) from exc
        return envelope

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

    def _next_seq(self, absolute_path: str) -> int:
        try:
            seq = int(_next_seq_for_path(absolute_path, 0, key="seq"))
            if seq < 1:
                return 1
            return seq
        except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive fallback
            logger.warning("failed to allocate seq for %s: %s", absolute_path, exc)
            return 1

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
