from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class RateLimiter:
    """Rate limiter for API protection with bounded memory."""

    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: int = 60,
        max_clients: int = 10000,
        cleanup_interval_seconds: float = 300.0,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_clients = max(1, max_clients)
        self._cleanup_interval = cleanup_interval_seconds
        self._requests: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        self._cleanup_task: Any = None

    async def _cleanup_stale_clients(self) -> None:
        """Remove stale entries and enforce max client limit."""
        current_time = time.time()
        window_start = current_time - self._window_seconds

        stale_clients = [
            client_id
            for client_id, timestamps in self._requests.items()
            if not timestamps or all(ts < window_start for ts in timestamps)
        ]
        for client_id in stale_clients:
            del self._requests[client_id]

        while len(self._requests) > self._max_clients:
            self._requests.popitem(last=False)

    async def _periodic_cleanup(self) -> None:
        """Periodically cleanup stale entries to prevent memory growth."""
        while not getattr(self, "_stopped", False):
            await asyncio.sleep(self._cleanup_interval)
            if getattr(self, "_stopped", False):
                break
            async with self._lock:
                if time.time() - self._last_cleanup >= self._cleanup_interval:
                    await self._cleanup_stale_clients()
                    self._last_cleanup = time.time()

    def start(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None:
            self._stopped = False
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    def stop(self) -> None:
        """Stop background cleanup task."""
        self._stopped = True
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    async def check_rate_limit(self, client_id: str) -> tuple[bool, int]:
        """Check if client is within rate limit.

        Returns (allowed, remaining_requests).
        """
        async with self._lock:
            current_time = time.time()
            window_start = current_time - self._window_seconds

            if len(self._requests) >= self._max_clients and client_id not in self._requests:
                await self._cleanup_stale_clients()
                if len(self._requests) >= self._max_clients:
                    return (False, 0)

            if client_id not in self._requests:
                self._requests[client_id] = []
            else:
                self._requests.move_to_end(client_id)

            self._requests[client_id] = [ts for ts in self._requests[client_id] if ts > window_start]

            current_count = len(self._requests[client_id])
            if current_count < self._max_requests:
                self._requests[client_id].append(current_time)
                remaining = self._max_requests - current_count - 1
                return (True, remaining)
            else:
                remaining = 0
                return (False, remaining)

    async def get_remaining(self, client_id: str) -> int:
        """Get remaining requests for client."""
        async with self._lock:
            current_time = time.time()
            window_start = current_time - self._window_seconds

            if client_id not in self._requests:
                return self._max_requests

            recent_requests = [ts for ts in self._requests[client_id] if ts > window_start]
            return max(0, self._max_requests - len(recent_requests))

    async def reset_client(self, client_id: str) -> None:
        """Reset rate limit for a client."""
        async with self._lock:
            if client_id in self._requests:
                self._requests[client_id] = []
                self._requests.move_to_end(client_id)
