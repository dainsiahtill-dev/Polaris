"""ArchiveSink - UEP v2.0 Consumer for StreamArchiver.

Subscribes to MessageBus stream events and archives them per turn
via StreamArchiver. Replaces the CLI-only @audit_stream_turn decorator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.events.topics import TOPIC_RUNTIME_STREAM

from .stream_archiver import create_stream_archiver

logger = logging.getLogger(__name__)

_ARCHIVE_FLUSH_EVENTS = {"complete", "error"}


class ArchiveSink:
    """UEP consumer that buffers stream events and archives them per turn."""

    def __init__(self, bus: MessageBus) -> None:
        """Initialize sink.

        Args:
            bus: MessageBus instance to subscribe to.
        """
        self._bus = bus
        self._subscribed = False
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._meta: dict[str, dict[str, str]] = {}  # turn_id -> {"workspace": ..., "session_id": ...}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Subscribe to EVENT messages on the bus."""
        if not self._subscribed:
            await self._bus.subscribe(MessageType.RUNTIME_EVENT, self._handle_message)
            self._subscribed = True
            logger.info("ArchiveSink started")

    async def stop(self) -> None:
        """Unsubscribe and flush any remaining buffers."""
        if self._subscribed:
            await self._bus.unsubscribe(MessageType.RUNTIME_EVENT, self._handle_message)
            self._subscribed = False
            await self._flush_all()
            logger.info("ArchiveSink stopped")

    async def _handle_message(self, message: Message) -> None:
        payload = message.payload
        if not isinstance(payload, dict):
            return
        if payload.get("topic") != TOPIC_RUNTIME_STREAM:
            return

        turn_id = str(payload.get("turn_id") or "")
        workspace = str(payload.get("workspace") or "")
        session_id = str(payload.get("run_id") or "")
        event_type = str(payload.get("event_type") or "")
        event_payload = payload.get("payload", {})

        if not turn_id or not workspace:
            return

        record = {"type": event_type, **dict(event_payload)}

        async with self._lock:
            self._buffers.setdefault(turn_id, []).append(record)
            self._meta.setdefault(turn_id, {"workspace": workspace, "session_id": session_id})
            should_flush = event_type in _ARCHIVE_FLUSH_EVENTS

        if should_flush:
            await self._flush_turn(turn_id)

    async def _flush_turn(self, turn_id: str) -> None:
        async with self._lock:
            events = self._buffers.pop(turn_id, [])
            meta = self._meta.pop(turn_id, {})

        if not events:
            return

        workspace = meta.get("workspace", "")
        session_id = meta.get("session_id", "")
        if not workspace:
            logger.warning(
                "ArchiveSink cannot flush turn without workspace: turn_id=%s event_count=%s",
                turn_id,
                len(events),
            )
            return

        try:
            archiver = create_stream_archiver(workspace)
            archive_id = await archiver.archive_turn(
                session_id=session_id,
                turn_id=turn_id,
                events=events,
            )
            logger.info(
                "ArchiveSink archived turn: turn_id=%s archive_id=%s event_count=%s",
                turn_id,
                archive_id,
                len(events),
            )
        except OSError as exc:
            logger.error(
                "ArchiveSink failed to archive turn: turn_id=%s error=%s",
                turn_id,
                exc,
            )

    async def _flush_all(self) -> None:
        async with self._lock:
            remaining_turns = list(self._buffers.keys())

        for turn_id in remaining_turns:
            await self._flush_turn(turn_id)


__all__ = ["ArchiveSink"]
