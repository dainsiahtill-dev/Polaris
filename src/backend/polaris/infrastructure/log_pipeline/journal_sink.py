"""JournalSink - UEP v2.0 Consumer for LogEventWriter.

Subscribes to MessageBus EVENT messages and writes canonical stream/lifecycle
events to the three-layer journal (raw, norm, enriched).
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.events.constants import (
    EVENT_TYPE_COMPLETE,
    EVENT_TYPE_ERROR,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.events.topics import (
    TOPIC_RUNTIME_FINGERPRINT,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_STREAM,
    UEP_PERSISTENCE_TOPICS,
)

from .canonical_event import CanonicalLogEventV2
from .writer import get_writer

logger = logging.getLogger(__name__)


def _get_writer_for_run(workspace: str, run_id: str) -> Any:
    """Lazy-resolve LogEventWriter for a workspace + run_id."""
    try:
        return get_writer(workspace=workspace, run_id=run_id)
    except (RuntimeError, ValueError) as exc:
        logger.error("JournalSink failed to get writer: workspace=%s run_id=%s error=%s", workspace, run_id, exc)
        return None


def _normalize_uep_payload(message: Message) -> CanonicalLogEventV2 | None:
    """Convert a UEP Message payload into a CanonicalLogEventV2."""
    payload = message.payload
    if not isinstance(payload, dict):
        return None

    topic = payload.get("topic")
    if topic not in UEP_PERSISTENCE_TOPICS:
        return None

    run_id = str(payload.get("run_id") or "")
    role = str(payload.get("role") or "unknown")
    event_type = str(payload.get("event_type") or "")
    timestamp = str(payload.get("timestamp") or "")

    if topic == TOPIC_RUNTIME_STREAM:
        event_payload = payload.get("payload", {})
        event_kind = "output"
        if event_type in {EVENT_TYPE_TOOL_CALL, EVENT_TYPE_TOOL_RESULT}:
            event_kind = "action"
        elif event_type in {EVENT_TYPE_ERROR}:
            event_kind = "error"
        elif event_type in {EVENT_TYPE_COMPLETE}:
            event_kind = "state"

        return CanonicalLogEventV2(
            run_id=run_id,
            channel="llm",
            domain="llm",
            severity="info",
            kind=event_kind,  # type: ignore[arg-type]
            actor=role,
            source="uep.journal_sink.stream",
            message=f"[{event_type}] {str(event_payload)[:200]}",
            raw={
                "event_type": event_type,
                "payload": event_payload,
                "turn_id": payload.get("turn_id"),
                "timestamp": timestamp,
            },
        )

    if topic == TOPIC_RUNTIME_LLM:
        metadata = payload.get("metadata", {})
        severity = "info"
        if event_type in {"call_error"}:
            severity = "error"
        elif event_type in {"call_retry"}:
            severity = "warn"

        return CanonicalLogEventV2(
            run_id=run_id,
            channel="llm",
            domain="llm",
            severity=severity,  # type: ignore[arg-type]
            kind="state",
            actor=role,
            source="uep.journal_sink.llm_lifecycle",
            message=f"LLM {event_type}",
            raw={
                "event_type": event_type,
                "metadata": metadata,
                "timestamp": timestamp,
            },
        )

    if topic == TOPIC_RUNTIME_FINGERPRINT:
        fingerprint = payload.get("fingerprint", {})
        return CanonicalLogEventV2(
            run_id=run_id,
            channel="system",
            domain="system",
            severity="info",
            kind="state",
            actor=role,
            source="uep.journal_sink.fingerprint",
            message="Strategy fingerprint",
            raw={
                "fingerprint": fingerprint,
                "timestamp": timestamp,
            },
        )

    return None


async def _journal_sink_handler(message: Message) -> None:
    """Async handler for MessageBus subscribe."""
    event = _normalize_uep_payload(message)
    if event is None:
        return

    workspace = ""
    payload = message.payload
    if isinstance(payload, dict):
        workspace = str(payload.get("workspace") or "")

    if not workspace:
        logger.debug("JournalSink skipping event without workspace: run_id=%s", event.run_id)
        return

    writer = _get_writer_for_run(workspace, event.run_id)
    if writer is None:
        return

    try:
        writer.write_event(
            channel=event.channel,
            domain=event.domain,
            severity=event.severity,
            kind=event.kind,
            actor=event.actor,
            source=event.source,
            message=event.message,
            raw=event.raw,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error(
            "JournalSink write failed: workspace=%s run_id=%s error=%s",
            workspace,
            event.run_id,
            exc,
        )


class JournalSink:
    """Unified Event Pipeline consumer that writes to journal.{raw,norm,enriched}.jsonl."""

    def __init__(self, bus: MessageBus) -> None:
        """Initialize sink.

        Args:
            bus: MessageBus instance to subscribe to.
        """
        self._bus = bus
        self._subscribed = False

    async def start(self) -> None:
        """Subscribe to EVENT messages on the bus."""
        if not self._subscribed:
            await self._bus.subscribe(MessageType.RUNTIME_EVENT, _journal_sink_handler)
            self._subscribed = True
            logger.info("JournalSink started")

    async def stop(self) -> None:
        """Unsubscribe from EVENT messages."""
        if self._subscribed:
            await self._bus.unsubscribe(MessageType.RUNTIME_EVENT, _journal_sink_handler)
            self._subscribed = False
            logger.info("JournalSink stopped")


__all__ = ["JournalSink", "_journal_sink_handler", "_normalize_uep_payload"]
