"""Stable service exports for events.fact_stream."""

from __future__ import annotations

from typing import Any

# Debug-tracing controls are part of this cell's observable surface: delivery
# and bootstrap layers must use these public re-exports instead of importing
# from the internal module directly.
from polaris.cells.events.fact_stream.internal.debug_trace import (
    configure_debug_tracing,
    emit_debug_event,
    install_global_debug_hooks,
    is_debug_tracing_enabled,
    log_stream_token,
    sanitize_headers,
    set_debug_tracing_enabled,
)
from polaris.kernelone.events.sourcing import EventSourcingError, JsonlEventStore

from .contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
)


def append_fact_event(command: AppendFactEventCommandV1) -> FactEventAppendedV1:
    """Append an immutable fact event to the canonical runtime stream."""
    try:
        store = JsonlEventStore(command.workspace)
        metadata = _compact_metadata(
            {
                "run_id": command.run_id,
                "task_id": command.task_id,
                "correlation_id": command.correlation_id,
            }
        )
        event = store.append(
            stream=command.stream,
            event_type=command.event_type,
            source=command.source,
            payload=command.payload,
            event_version=1,
            aggregate_id=command.task_id or command.run_id or None,
            correlation_id=command.correlation_id,
            metadata=metadata,
        )
    except (ValueError, EventSourcingError) as exc:
        raise FactStreamError(
            f"append_fact_event failed: {exc}",
            code="append_failed",
            details={
                "workspace": command.workspace,
                "stream": command.stream,
                "event_type": command.event_type,
            },
        ) from exc

    return FactEventAppendedV1(
        event_id=event.event_id,
        workspace=command.workspace,
        stream=command.stream,
        storage_path=store.stream_logical_path(command.stream),
        appended_at=event.occurred_at,
    )


def query_fact_events(query: QueryFactEventsV1) -> FactStreamQueryResultV1:
    """Query canonical fact events with pagination and optional filters."""
    try:
        store = JsonlEventStore(query.workspace)
        result = store.query(
            stream=query.stream,
            limit=query.limit,
            offset=query.offset,
            event_type=query.event_type,
            run_id=query.run_id,
            task_id=query.task_id,
        )
    except (ValueError, EventSourcingError) as exc:
        raise FactStreamError(
            f"query_fact_events failed: {exc}",
            code="query_failed",
            details={
                "workspace": query.workspace,
                "stream": query.stream,
                "offset": query.offset,
                "limit": query.limit,
            },
        ) from exc

    event_payloads = tuple(_event_to_dict(item.to_record()) for item in result.events)
    return FactStreamQueryResultV1(
        workspace=query.workspace,
        stream=query.stream,
        events=event_payloads,
        total=result.total,
        next_offset=result.next_offset,
    )


def _compact_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        token = str(value or "").strip()
        if token:
            compact[str(key)] = token
    return compact


def _event_to_dict(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    event = dict(record)
    if "run_id" not in event:
        run_id = (
            str(metadata.get("run_id") or payload.get("run_id") or "").strip()
            if isinstance(metadata, dict) and isinstance(payload, dict)
            else ""
        )
        if run_id:
            event["run_id"] = run_id
    if "task_id" not in event:
        task_id = (
            str(metadata.get("task_id") or payload.get("task_id") or "").strip()
            if isinstance(metadata, dict) and isinstance(payload, dict)
            else ""
        )
        if task_id:
            event["task_id"] = task_id
    return event


__all__ = [
    "AppendFactEventCommandV1",
    "FactEventAppendedV1",
    "FactStreamError",
    "FactStreamQueryResultV1",
    "QueryFactEventsV1",
    "append_fact_event",
    # debug trace public surface
    "configure_debug_tracing",
    "emit_debug_event",
    "install_global_debug_hooks",
    "is_debug_tracing_enabled",
    "log_stream_token",
    "query_fact_events",
    "sanitize_headers",
    "set_debug_tracing_enabled",
]
