"""Realtime fanout for canonical log pipeline events.

This module provides an in-process pub/sub layer for canonical journal events.
It is intentionally process-local and complements filesystem-based incremental
reading. Consumers should still keep file fallback for cross-process writes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_SIZE = 2048


def _normalize_runtime_root(value: str) -> str:
    return os.path.abspath(str(value or "").strip())


@dataclass
class RealtimeLogSubscription:
    """Per-connection realtime subscription state."""

    connection_id: str
    runtime_root: str
    queue: asyncio.Queue[dict[str, Any]]
    loop: asyncio.AbstractEventLoop
    max_queue_size: int = _DEFAULT_QUEUE_SIZE
    _dropped: int = 0
    _dropped_lock: threading.Lock = field(default_factory=threading.Lock)

    def _mark_dropped(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._dropped_lock:
            self._dropped += int(count)

    def consume_dropped(self) -> int:
        with self._dropped_lock:
            value = int(self._dropped)
            self._dropped = 0
            return value

    def matches_runtime(self, runtime_root: str) -> bool:
        return self.runtime_root == _normalize_runtime_root(runtime_root)

    def offer(self, event: dict[str, Any]) -> None:
        """Offer an event to this subscription queue (drop-oldest on overflow)."""

        payload = dict(event)

        def _enqueue() -> None:
            dropped = 0
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                    dropped += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                self.queue.put_nowait(payload)
            except asyncio.QueueFull:
                dropped += 1
            if dropped > 0:
                self._mark_dropped(dropped)

        try:
            self.loop.call_soon_threadsafe(_enqueue)
        except RuntimeError:
            # Event loop already closed.
            self._mark_dropped(1)
        except (RuntimeError, ValueError):
            logger.debug("Failed to schedule realtime event enqueue", exc_info=True)
            self._mark_dropped(1)


class RealtimeLogFanout:
    """Global process-local fanout for canonical log events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscriptions: dict[str, RealtimeLogSubscription] = {}

    async def register_connection(
        self,
        *,
        connection_id: str,
        runtime_root: str,
        max_queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> RealtimeLogSubscription:
        queue_size = max(64, int(max_queue_size or _DEFAULT_QUEUE_SIZE))
        subscription = RealtimeLogSubscription(
            connection_id=connection_id,
            runtime_root=_normalize_runtime_root(runtime_root),
            queue=asyncio.Queue(maxsize=queue_size),
            loop=asyncio.get_running_loop(),
            max_queue_size=queue_size,
        )
        with self._lock:
            self._subscriptions[connection_id] = subscription
        return subscription

    async def unregister_connection(self, connection_id: str) -> bool:
        with self._lock:
            removed = self._subscriptions.pop(connection_id, None)
        return removed is not None

    def publish(self, *, runtime_root: str, event: dict[str, Any]) -> None:
        """Publish one canonical event to matching subscribers."""
        normalized_root = _normalize_runtime_root(runtime_root)
        with self._lock:
            subscriptions = list(self._subscriptions.values())
        for subscription in subscriptions:
            if not subscription.matches_runtime(normalized_root):
                continue
            subscription.offer(event)

    def get_subscription(self, connection_id: str) -> RealtimeLogSubscription | None:
        with self._lock:
            return self._subscriptions.get(connection_id)

    def list_connections(self) -> list[str]:
        with self._lock:
            return list(self._subscriptions.keys())


LOG_REALTIME_FANOUT = RealtimeLogFanout()
