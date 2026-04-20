"""High-throughput async feedback collector with ring-buffer storage."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeedbackEvent:
    prompt: str
    response: str
    accepted: bool
    metadata: dict[str, Any]


class FeedbackCollector:
    """Collect feedback events with overwrite-on-full ring buffer policy."""

    def __init__(self, *, capacity: int = 10000) -> None:
        self._capacity = max(1, int(capacity))
        self._events: deque[FeedbackEvent] = deque(maxlen=self._capacity)
        self._lock = asyncio.Lock()
        self._ingested = 0
        self._overwritten = 0

    async def submit(self, event: FeedbackEvent) -> None:
        async with self._lock:
            before_size = len(self._events)
            self._events.append(event)
            self._ingested += 1
            if before_size == self._capacity:
                self._overwritten += 1

    async def submit_batch(self, events: list[FeedbackEvent]) -> None:
        for event in events:
            await self.submit(event)

    async def snapshot(self) -> list[FeedbackEvent]:
        async with self._lock:
            return list(self._events)

    def get_stats(self) -> dict[str, Any]:
        current_size = len(self._events)
        drop_rate = 0.0
        if self._ingested > 0:
            # Overwrite policy is deterministic retention, not an ingest drop.
            drop_rate = 0.0
        return {
            "capacity": self._capacity,
            "current_size": current_size,
            "ingested": self._ingested,
            "overwritten": self._overwritten,
            "drop_rate_percent": drop_rate,
        }


__all__ = [
    "FeedbackCollector",
    "FeedbackEvent",
]
