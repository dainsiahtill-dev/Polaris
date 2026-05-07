"""Runtime Event Fanout Service.

This module provides a centralized event fanout service that:
1. Subscribes to MessageBus events (FILE_WRITTEN, TASK_TRACE) exactly once per process
2. Manages per-connection bounded buffers with backpressure handling
3. Triggers resync when events are dropped

Usage:
    # Register a connection
    sink = await RUNTIME_EVENT_FANOUT.register_connection(
        connection_id="conn-123",
        workspace="/path/to/workspace",
        cache_root="/path/to/cache",
    )

    # Drain events
    events = await RUNTIME_EVENT_FANOUT.drain_events("conn-123")

    # Unregister when done
    await RUNTIME_EVENT_FANOUT.unregister_connection("conn-123")
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import inspect
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.infrastructure.di.container import get_container
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

logger = logging.getLogger(__name__)

# Buffer sizes for connection sinks
FILE_EDIT_BUFFER_SIZE = 256
TASK_TRACE_BUFFER_SIZE = 256


@dataclass
class ConnectionSink:
    """Per-connection event sink with bounded buffers."""

    connection_id: str
    workspace: str
    cache_root: str

    # Bounded buffers for different event types
    file_edit_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=FILE_EDIT_BUFFER_SIZE))
    task_trace_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=TASK_TRACE_BUFFER_SIZE))
    sequential_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=TASK_TRACE_BUFFER_SIZE))

    # Drop counters for backpressure tracking
    file_edit_dropped: int = 0
    task_trace_dropped: int = 0
    sequential_dropped: int = 0

    # Lock for thread-safe access
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Last activity timestamp
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_file_edit(self, event: dict[str, Any]) -> bool:
        """Add a file edit event to the buffer.

        Returns:
            True if added, False if dropped due to buffer full
        """
        with self._lock:
            self.last_activity = datetime.now(timezone.utc)
            if len(self.file_edit_events) >= FILE_EDIT_BUFFER_SIZE:
                self.file_edit_dropped += 1
                # Remove oldest to make room
                with contextlib.suppress(IndexError):
                    self.file_edit_events.popleft()
            self.file_edit_events.append(event)
            return True

    def add_task_trace(self, event: dict[str, Any]) -> bool:
        """Add a task trace event to the buffer.

        Returns:
            True if added, False if dropped due to buffer full
        """
        with self._lock:
            self.last_activity = datetime.now(timezone.utc)
            if len(self.task_trace_events) >= TASK_TRACE_BUFFER_SIZE:
                self.task_trace_dropped += 1
                # Remove oldest to make room
                with contextlib.suppress(IndexError):
                    self.task_trace_events.popleft()
            self.task_trace_events.append(event)
            return True

    def drain_file_edits(self) -> tuple[list[dict[str, Any]], int]:
        """Drain all file edit events and return with drop count.

        Returns:
            Tuple of (events list, dropped count since last drain)
        """
        with self._lock:
            events = list(self.file_edit_events)
            self.file_edit_events.clear()
            dropped = self.file_edit_dropped
            self.file_edit_dropped = 0
            self.last_activity = datetime.now(timezone.utc)
            return events, dropped

    def drain_task_traces(self) -> tuple[list[dict[str, Any]], int]:
        """Drain all task trace events and return with drop count.

        Returns:
            Tuple of (events list, dropped count since last drain)
        """
        with self._lock:
            events = list(self.task_trace_events)
            self.task_trace_events.clear()
            dropped = self.task_trace_dropped
            self.task_trace_dropped = 0
            self.last_activity = datetime.now(timezone.utc)
            return events, dropped

    def add_sequential(self, event: dict[str, Any]) -> bool:
        """Add a sequential event to the buffer.

        Returns:
            True if added, False if dropped due to buffer full
        """
        with self._lock:
            self.last_activity = datetime.now(timezone.utc)
            if len(self.sequential_events) >= TASK_TRACE_BUFFER_SIZE:
                self.sequential_dropped += 1
                with contextlib.suppress(IndexError):
                    self.sequential_events.popleft()
            self.sequential_events.append(event)
            return True

    def drain_sequential(self) -> tuple[list[dict[str, Any]], int]:
        """Drain all sequential events and return with drop count.

        Returns:
            Tuple of (events list, dropped count since last drain)
        """
        with self._lock:
            events = list(self.sequential_events)
            self.sequential_events.clear()
            dropped = self.sequential_dropped
            self.sequential_dropped = 0
            self.last_activity = datetime.now(timezone.utc)
            return events, dropped

    def get_stats(self) -> dict[str, Any]:
        """Get current sink statistics."""
        with self._lock:
            return {
                "file_edit_pending": len(self.file_edit_events),
                "task_trace_pending": len(self.task_trace_events),
                "sequential_pending": len(self.sequential_events),
                "file_edit_dropped": self.file_edit_dropped,
                "task_trace_dropped": self.task_trace_dropped,
                "sequential_dropped": self.sequential_dropped,
                "last_activity": self.last_activity.isoformat(),
            }


class RuntimeEventFanout:
    """Global event fanout service with single MessageBus subscription.

    This class ensures that FILE_WRITTEN and TASK_TRACE events from the
    MessageBus are subscribed to exactly once per process, and distributed
    to registered connection sinks with proper backpressure handling.
    """

    def __init__(self) -> None:
        self._sinks: dict[str, ConnectionSink] = {}
        self._lock = threading.RLock()
        self._subscribed = False
        self._bus: Any | None = None
        self._file_handler: Any | None = None
        self._trace_handler: Any | None = None
        self._sequential_handlers: dict[MessageType, Any] = {}
        self._closed = False

        # Statistics (use threading.Lock for sync access)
        self._stats = {
            "file_written_events": 0,
            "task_trace_events": 0,
            "connections_registered": 0,
            "connections_unregistered": 0,
        }
        self._stats_lock = threading.Lock()

    @staticmethod
    async def _call_maybe_async(func: Any, *args: Any) -> Any:
        """Call function that may return either sync result or awaitable."""
        result = func(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def ensure_subscribed(self) -> bool:
        """Ensure MessageBus subscription is established.

        Returns:
            True if subscription is active
        """
        if self._closed:
            return False

        with self._lock:
            if self._subscribed and self._bus is not None:
                return True

        try:
            container = await get_container()
            bus = await container.resolve_async(MessageBus)
        except (RuntimeError, ValueError) as e:
            logger.warning(f"Failed to resolve MessageBus: {e}")
            return False
        nested_bus = getattr(bus, "_bus", None)
        if nested_bus is not None and hasattr(nested_bus, "subscribe"):
            bus = nested_bus

        with self._lock:
            # Double-check after async
            if self._subscribed and self._bus is not None:
                return True

            # Unsubscribe from previous bus if different
            if self._bus is not None and self._bus is not bus:
                await self._unsubscribe_from_bus()

            self._bus = bus
            loop = asyncio.get_running_loop()

            # Create handlers
            self._file_handler = self._make_file_written_handler(loop)
            self._trace_handler = self._make_task_trace_handler(loop)

            # Subscribe
            await self._call_maybe_async(
                bus.subscribe,
                MessageType.FILE_WRITTEN,
                self._file_handler,
            )
            await self._call_maybe_async(
                bus.subscribe,
                MessageType.TASK_TRACE,
                self._trace_handler,
            )

            # Subscribe to Sequential events (vNext)
            self._sequential_handlers = {
                MessageType.SEQ_START: self._make_sequential_handler("seq.start"),
                MessageType.SEQ_STEP: self._make_sequential_handler("seq.step"),
                MessageType.SEQ_PROGRESS: self._make_sequential_handler("seq.progress"),
                MessageType.SEQ_NO_PROGRESS: self._make_sequential_handler("seq.no_progress"),
                MessageType.SEQ_TERMINATION: self._make_sequential_handler("seq.termination"),
                MessageType.SEQ_ERROR: self._make_sequential_handler("seq.error"),
                MessageType.SEQ_RESERVED_KEY_VIOLATION: self._make_sequential_handler("seq.reserved_key_violation"),
            }
            for msg_type, handler in self._sequential_handlers.items():
                await self._call_maybe_async(bus.subscribe, msg_type, handler)

            self._subscribed = True

            logger.debug("RuntimeEventFanout: subscribed to MessageBus")
            return True

    async def _unsubscribe_from_bus(self) -> None:
        """Unsubscribe handlers from current bus."""
        if self._bus is None:
            return

        if self._file_handler is not None:
            try:
                await self._call_maybe_async(
                    self._bus.unsubscribe,
                    MessageType.FILE_WRITTEN,
                    self._file_handler,
                )
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to unsubscribe file handler: {e}")

        if self._trace_handler is not None:
            try:
                await self._call_maybe_async(
                    self._bus.unsubscribe,
                    MessageType.TASK_TRACE,
                    self._trace_handler,
                )
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to unsubscribe trace handler: {e}")

        for msg_type, handler in list(self._sequential_handlers.items()):
            try:
                await self._call_maybe_async(self._bus.unsubscribe, msg_type, handler)
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to unsubscribe sequential handler {msg_type}: {e}")
        self._sequential_handlers.clear()

        self._subscribed = False

    async def close(self) -> None:
        """Close the fanout service and clean up."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        await self._unsubscribe_from_bus()

        # Clear all sinks
        with self._lock:
            self._sinks.clear()

        logger.debug("RuntimeEventFanout: closed")

    def _make_file_written_handler(self, loop: asyncio.AbstractEventLoop):
        """Create handler for FILE_WRITTEN messages."""

        def handler(message: Message) -> None:
            try:
                payload = message.payload if isinstance(message.payload, dict) else {}
                event = self._build_file_edit_event(payload)

                # Use threading.Lock for sync access from sync handler
                with self._stats_lock:
                    self._stats["file_written_events"] += 1

                # Copy sinks list - need to handle both sync and async locks
                # Since this is called from sync handler, use direct access
                sinks = list(self._sinks.values())

                for sink in sinks:
                    sink.add_file_edit(event)

            except (RuntimeError, ValueError) as e:
                logger.debug(f"Error handling FILE_WRITTEN: {e}")

        return handler

    def _make_task_trace_handler(self, loop: asyncio.AbstractEventLoop):
        """Create handler for TASK_TRACE messages."""

        def handler(message: Message) -> None:
            try:
                payload = message.payload if isinstance(message.payload, dict) else {}
                event = {
                    "schema_version": "runtime.v2",
                    "event_schema": "runtime.event.task_trace.v1",
                    "channel": "event.task_trace",
                    "kind": "task_trace",
                    "source": "message_bus.task_trace",
                    "type": "task_trace",
                    "event": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                # Use threading.Lock for sync access from sync handler
                with self._stats_lock:
                    self._stats["task_trace_events"] += 1

                # Copy sinks list - need to handle both sync and async locks
                # Since this is called from sync handler, use direct access
                sinks = list(self._sinks.values())

                for sink in sinks:
                    sink.add_task_trace(event)

            except (RuntimeError, ValueError) as e:
                logger.debug(f"Error handling TASK_TRACE: {e}")

        return handler

    def _make_sequential_handler(self, event_type: str):
        """Create handler for Sequential events."""

        def handler(message: Message) -> None:
            try:
                payload = message.payload if isinstance(message.payload, dict) else {}
                event = {
                    "schema_version": "runtime.v2",
                    "event_schema": "runtime.event.sequential.v1",
                    "channel": "event.sequential",
                    "kind": event_type,
                    "source": "message_bus.sequential",
                    "type": event_type,
                    "event": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                with self._stats_lock:
                    self._stats["sequential_events"] = self._stats.get("sequential_events", 0) + 1

                sinks = list(self._sinks.values())
                for sink in sinks:
                    sink.add_sequential(event)

            except (RuntimeError, ValueError) as e:
                logger.debug(f"Error handling {event_type}: {e}")

        return handler

    def _build_file_edit_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build file edit event from MessageBus payload."""
        file_path = str(payload.get("file_path") or "").strip()
        timestamp = str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat())
        operation_raw = str(payload.get("operation") or "modify").strip().lower()
        operation = operation_raw if operation_raw in {"create", "modify", "delete"} else "modify"

        try:
            added_lines = max(0, int(payload.get("added_lines") or 0))
        except (RuntimeError, ValueError):
            added_lines = 0
        try:
            deleted_lines = max(0, int(payload.get("deleted_lines") or 0))
        except (RuntimeError, ValueError):
            deleted_lines = 0
        try:
            modified_lines = max(0, int(payload.get("modified_lines") or 0))
        except (RuntimeError, ValueError):
            modified_lines = 0

        return {
            "schema_version": "runtime.v2",
            "event_schema": "runtime.event.file_edit.v1",
            "channel": "event.file_edit",
            "kind": "file_edit",
            "source": "message_bus.file_written",
            "id": f"{file_path}-{timestamp}",
            "file_path": file_path,
            "operation": operation,
            "content_size": int(payload.get("content_size") or 0),
            "task_id": str(payload.get("task_id") or "").strip() or None,
            "patch": str(payload.get("patch") or "").strip() or None,
            "added_lines": added_lines,
            "deleted_lines": deleted_lines,
            "modified_lines": modified_lines,
            "timestamp": timestamp,
        }

    async def register_connection(
        self,
        connection_id: str,
        workspace: str,
        cache_root: str,
    ) -> ConnectionSink:
        """Register a new connection and return its sink.

        Args:
            connection_id: Unique connection identifier
            workspace: Workspace path for the connection
            cache_root: Runtime cache root for the connection

        Returns:
            ConnectionSink for the registered connection
        """
        # Ensure we're subscribed first
        await self.ensure_subscribed()

        sink = ConnectionSink(
            connection_id=connection_id,
            workspace=workspace,
            cache_root=cache_root,
        )

        with self._lock:
            self._sinks[connection_id] = sink

        with self._stats_lock:
            self._stats["connections_registered"] += 1

        logger.debug(f"RuntimeEventFanout: registered connection {connection_id}")
        return sink

    async def unregister_connection(self, connection_id: str) -> bool:
        """Unregister a connection and clean up its sink.

        Args:
            connection_id: Connection identifier to unregister

        Returns:
            True if connection was found and removed
        """
        with self._lock:
            sink = self._sinks.pop(connection_id, None)

        if sink is not None:
            with self._stats_lock:
                self._stats["connections_unregistered"] += 1
            logger.debug(f"RuntimeEventFanout: unregistered connection {connection_id}")
            return True
        return False

    async def drain_events(
        self,
        connection_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
        """Drain events for a connection.

        Args:
            connection_id: Connection identifier

        Returns:
            Tuple of (file_edit_events, task_trace_events, sequential_events, total_dropped)
        """
        with self._lock:
            sink = self._sinks.get(connection_id)

        if sink is None:
            return [], [], [], 0

        file_events, file_dropped = sink.drain_file_edits()
        trace_events, trace_dropped = sink.drain_task_traces()
        seq_events, seq_dropped = sink.drain_sequential()

        return file_events, trace_events, seq_events, file_dropped + trace_dropped + seq_dropped

    def get_sink_stats(self, connection_id: str) -> dict[str, Any] | None:
        """Get statistics for a specific connection sink."""
        with self._lock:
            sink = self._sinks.get(connection_id)

        if sink is None:
            return None

        return sink.get_stats()

    def get_global_stats(self) -> dict[str, Any]:
        """Get global fanout statistics."""
        with self._stats_lock:
            return dict(self._stats)

    def list_connections(self) -> list[str]:
        """List all registered connection IDs."""
        with self._lock:
            return list(self._sinks.keys())


# Global singleton instance
RUNTIME_EVENT_FANOUT = RuntimeEventFanout()


def _cleanup_runtime_fanout() -> None:
    """Module cleanup on exit."""
    try:
        asyncio.run(RUNTIME_EVENT_FANOUT.close())
    except RuntimeError:
        # Fallback when event loop constraints prevent asyncio.run().
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(RUNTIME_EVENT_FANOUT.close())
            finally:
                loop.close()
        except (RuntimeError, ValueError) as exc:
            logger.debug("event loop cleanup failed (best-effort): %s", exc)
    except ValueError as exc:
        logger.debug("outer fanout cleanup failed (best-effort): %s", exc)


atexit.register(_cleanup_runtime_fanout)
