from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class RateLimiter:
    """Rate limiter for API protection with bounded memory.

    Thread-safety:
        All public methods are async and use an asyncio.Lock to protect
        shared state. The _periodic_cleanup task also uses the lock.

    Memory bounds:
        - Maximum clients is bounded by max_clients
        - Cleanup removes stale entries and enforces the limit
    """

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
        self._stopped = False

    async def _cleanup_stale_clients(self) -> None:
        """Remove stale entries and enforce max client limit.

        Must be called while holding self._lock.
        """
        current_time = time.time()
        window_start = current_time - self._window_seconds

        # Collect stale clients to remove
        stale_clients = [
            client_id
            for client_id, timestamps in self._requests.items()
            if not timestamps or all(ts < window_start for ts in timestamps)
        ]
        for client_id in stale_clients:
            del self._requests[client_id]

        # If still over limit after stale cleanup, remove oldest entries
        # Batch removal to avoid O(n*m) complexity
        excess = len(self._requests) - self._max_clients
        if excess > 0:
            for _ in range(excess):
                if self._requests:
                    self._requests.popitem(last=False)
                else:
                    break

    async def _periodic_cleanup(self) -> None:
        """Periodically cleanup stale entries to prevent memory growth.

        Uses proper shutdown signaling via asyncio.Event for reliable termination.
        """
        try:
            while not self._stopped:
                await asyncio.sleep(self._cleanup_interval)

                # Check stopped flag inside the loop with lock protection
                async with self._lock:
                    if self._stopped:
                        break
                    if time.time() - self._last_cleanup >= self._cleanup_interval:
                        await self._cleanup_stale_clients()
                        self._last_cleanup = time.time()
        except asyncio.CancelledError:
            # Normal cancellation - cleanup task is being stopped
            pass

    def start(self) -> None:
        """Start background cleanup task.

        Idempotent: calling start() multiple times is safe.
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._stopped = False
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def stop_async(self) -> None:
        """Stop background cleanup task asynchronously.

        Preferred over stop() in async contexts.
        """
        self._stopped = True
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    def stop(self) -> None:
        """Stop background cleanup task synchronously.

        Note: This schedules cancellation but does not wait for the task to complete.
        For async contexts, prefer stop_async().
        """
        self._stopped = True
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    async def check_rate_limit(self, client_id: str) -> tuple[bool, int]:
        """Check if client is within rate limit.

        Returns (allowed, remaining_requests).
        Thread-safe via asyncio.Lock.
        """
        async with self._lock:
            current_time = time.time()
            window_start = current_time - self._window_seconds

            # Cleanup if at capacity
            if len(self._requests) >= self._max_clients and client_id not in self._requests:
                await self._cleanup_stale_clients()
                if len(self._requests) >= self._max_clients:
                    return (False, 0)

            # Initialize or update client entry
            if client_id not in self._requests:
                self._requests[client_id] = []
            else:
                self._requests.move_to_end(client_id)

            # Filter out expired timestamps
            self._requests[client_id] = [ts for ts in self._requests[client_id] if ts > window_start]

            current_count = len(self._requests[client_id])
            if current_count < self._max_requests:
                self._requests[client_id].append(current_time)
                remaining = self._max_requests - current_count - 1
                return (True, remaining)
            else:
                return (False, 0)

    async def get_remaining(self, client_id: str) -> int:
        """Get remaining requests for client.

        Thread-safe via asyncio.Lock.
        """
        async with self._lock:
            current_time = time.time()
            window_start = current_time - self._window_seconds

            if client_id not in self._requests:
                return self._max_requests

            recent_requests = [ts for ts in self._requests[client_id] if ts > window_start]
            return max(0, self._max_requests - len(recent_requests))

    async def reset_client(self, client_id: str) -> None:
        """Reset rate limit for a client.

        Thread-safe via asyncio.Lock.
        """
        async with self._lock:
            if client_id in self._requests:
                self._requests[client_id] = []
                self._requests.move_to_end(client_id)

    async def force_cleanup(self) -> None:
        """Force an immediate cleanup of stale clients.

        Useful for testing or manual cleanup.
        Thread-safe via asyncio.Lock.
        """
        async with self._lock:
            await self._cleanup_stale_clients()
            self._last_cleanup = time.time()
