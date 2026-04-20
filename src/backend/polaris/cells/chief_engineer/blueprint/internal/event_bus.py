"""Lightweight in-process event bus for DirectorPool observability.

This is a synchronous, local-only event bus. It avoids external
dependencies (Redis, NATS) while providing a clean upgrade path:
subscribers can be swapped for streaming backends in Phase C.
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, dict[str, Any]], None]


class EventBus:
    """Synchronous pub/sub event bus.

    Events are delivered synchronously in the publisher's thread.
    Subscribers are expected to be fast and non-blocking; any heavy
    work should be handed off to a background task by the subscriber.
    """

    def __init__(self) -> None:
        """Initialize an empty event bus."""
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._lock = threading.RLock()

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event to all subscribers of ``event_type``.

        Individual subscriber failures are isolated and logged; they do
        not propagate to the publisher or affect other subscribers.

        Args:
            event_type: Event category (e.g. ``director.assigned``).
            payload: Structured event payload.
        """
        token = str(event_type or "").strip()
        if not token:
            return
        with self._lock:
            handlers = list(self._subscribers.get(token, []))
        for handler in handlers:
            try:
                handler(token, copy.deepcopy(payload))
            except Exception as exc:  # noqa: BLE001
                logger.warning("EventBus subscriber error for %s: %s", token, exc)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for the given event type.

        Args:
            event_type: Event category to subscribe to.
            handler: Callable receiving (event_type, payload).
        """
        token = str(event_type or "").strip()
        if not token:
            return
        with self._lock:
            self._subscribers.setdefault(token, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a previously registered handler.

        Args:
            event_type: Event category.
            handler: The exact callable to remove.
        """
        token = str(event_type or "").strip()
        if not token:
            return
        with self._lock:
            if token not in self._subscribers:
                return
            handlers = self._subscribers[token]
            if handler in handlers:
                handlers.remove(handler)
            if not handlers:
                del self._subscribers[token]

    def clear(self) -> None:
        """Remove all subscribers."""
        with self._lock:
            self._subscribers.clear()
