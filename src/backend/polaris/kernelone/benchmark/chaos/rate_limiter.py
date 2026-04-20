"""Rate Limiting Simulator for Chaos Testing.

This module provides rate limiting simulation capabilities for
testing system behavior under controlled load conditions.

Example
-------
    limiter = TokenBucketRateLimiter(max_requests_per_second=10.0)
    await limiter.acquire()
    # Proceed with request
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS
from polaris.kernelone.errors import ChaosCircuitBreakerError, RateLimitExceededError

# Backward compatibility alias (deprecated, use ChaosCircuitBreakerError directly)
CircuitBreakerOpenError = ChaosCircuitBreakerError

if TYPE_CHECKING:
    from collections.abc import Callable

# ------------------------------------------------------------------
# Rate Limiters
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RateLimiterStats:
    """Statistics for rate limiter."""

    total_requests: int = 0
    allowed_requests: int = 0
    rejected_requests: int = 0
    total_wait_time_ms: float = 0.0

    @property
    def rejection_rate(self) -> float:
        """Calculate rejection rate."""
        if self.total_requests == 0:
            return 0.0
        return self.rejected_requests / self.total_requests

    @property
    def average_wait_time_ms(self) -> float:
        """Calculate average wait time."""
        if self.allowed_requests == 0:
            return 0.0
        return self.total_wait_time_ms / self.allowed_requests


class TokenBucketRateLimiter:
    """Token bucket rate limiter with async support.

    Implements the token bucket algorithm for smooth rate limiting
    with burst capability.

    Attributes:
        rate: Maximum requests per second.
        capacity: Maximum burst capacity.
    """

    __slots__ = ("_capacity", "_last_update", "_rate", "_stats", "_tokens")

    def __init__(
        self,
        max_requests_per_second: float,
        burst_capacity: float | None = None,
    ) -> None:
        self._rate = max_requests_per_second
        self._capacity = burst_capacity or max_requests_per_second
        self._tokens = float(self._capacity)
        self._last_update = time.monotonic()
        self._stats = RateLimiterStats()

    @property
    def stats(self) -> RateLimiterStats:
        """Get current statistics."""
        return self._stats

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_update = now

    async def acquire(self, timeout: float | None = None) -> bool:
        """Acquire a token, waiting if necessary.

        Args:
            timeout: Maximum time to wait in seconds. None means no limit.

        Returns:
            True if token was acquired.

        Raises:
            RateLimitExceededError: If timeout is exceeded.
        """
        start = time.monotonic()
        self._stats = RateLimiterStats(
            total_requests=self._stats.total_requests + 1,
            allowed_requests=self._stats.allowed_requests,
            rejected_requests=self._stats.rejected_requests,
            total_wait_time_ms=self._stats.total_wait_time_ms,
        )

        while True:
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                wait_time = (time.monotonic() - start) * 1000
                self._stats = RateLimiterStats(
                    total_requests=self._stats.total_requests,
                    allowed_requests=self._stats.allowed_requests + 1,
                    rejected_requests=self._stats.rejected_requests,
                    total_wait_time_ms=self._stats.total_wait_time_ms + wait_time,
                )
                return True

            if timeout is not None:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    self._stats = RateLimiterStats(
                        total_requests=self._stats.total_requests,
                        allowed_requests=self._stats.allowed_requests,
                        rejected_requests=self._stats.rejected_requests + 1,
                        total_wait_time_ms=self._stats.total_wait_time_ms,
                    )
                    raise RateLimitExceededError(
                        f"Rate limit of {self._rate} req/s exceeded",
                        retry_after=1.0 / self._rate,
                    )

            await _async_sleep(0.01)

    def try_acquire(self) -> bool:
        """Try to acquire a token without waiting.

        Returns:
            True if token was acquired, False otherwise.
        """
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            self._stats = RateLimiterStats(
                total_requests=self._stats.total_requests + 1,
                allowed_requests=self._stats.allowed_requests + 1,
                rejected_requests=self._stats.rejected_requests,
                total_wait_time_ms=self._stats.total_wait_time_ms,
            )
            return True
        self._stats = RateLimiterStats(
            total_requests=self._stats.total_requests + 1,
            allowed_requests=self._stats.allowed_requests,
            rejected_requests=self._stats.rejected_requests + 1,
            total_wait_time_ms=self._stats.total_wait_time_ms,
        )
        return False


class LeakyBucketRateLimiter:
    """Leaky bucket rate limiter with async support.

    Implements the leaky bucket algorithm for smooth, constant-rate
    processing.

    Attributes:
        rate: Processing rate in requests per second.
        capacity: Maximum bucket capacity.
    """

    __slots__ = ("_capacity", "_last_drip", "_level", "_rate", "_stats")

    def __init__(
        self,
        requests_per_second: float,
        max_burst: int = 10,
    ) -> None:
        self._rate = requests_per_second
        self._capacity = max_burst
        self._level = 0.0
        self._last_drip = time.monotonic()
        self._stats = RateLimiterStats()

    @property
    def stats(self) -> RateLimiterStats:
        """Get current statistics."""
        return self._stats

    def _drip(self) -> None:
        """Drip water from bucket based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_drip
        self._level = max(0.0, self._level - elapsed * self._rate)
        self._last_drip = now

    async def acquire(self, timeout: float | None = None) -> bool:
        """Acquire capacity in the bucket.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if capacity was acquired.

        Raises:
            RateLimitExceededError: If timeout is exceeded.
        """
        start = time.monotonic()
        self._stats = RateLimiterStats(
            total_requests=self._stats.total_requests + 1,
            allowed_requests=self._stats.allowed_requests,
            rejected_requests=self._stats.rejected_requests,
            total_wait_time_ms=self._stats.total_wait_time_ms,
        )

        while True:
            self._drip()

            if self._level < self._capacity:
                self._level += 1.0
                wait_time = (time.monotonic() - start) * 1000
                self._stats = RateLimiterStats(
                    total_requests=self._stats.total_requests,
                    allowed_requests=self._stats.allowed_requests + 1,
                    rejected_requests=self._stats.rejected_requests,
                    total_wait_time_ms=self._stats.total_wait_time_ms + wait_time,
                )
                return True

            if timeout is not None:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    self._stats = RateLimiterStats(
                        total_requests=self._stats.total_requests,
                        allowed_requests=self._stats.allowed_requests,
                        rejected_requests=self._stats.rejected_requests + 1,
                        total_wait_time_ms=self._stats.total_wait_time_ms,
                    )
                    raise RateLimitExceededError(
                        f"Leaky bucket overflow: capacity={self._capacity}",
                        retry_after=1.0 / self._rate,
                    )

            await _async_sleep(0.01)


# ------------------------------------------------------------------
# Circuit Breaker
# ------------------------------------------------------------------


class CircuitBreaker:
    """Circuit breaker pattern implementation.

    States:
        CLOSED: Normal operation, requests pass through.
        OPEN: Failures exceeded threshold, requests are blocked.
        HALF_OPEN: Testing if service has recovered.
    """

    __slots__ = (
        "_failure_count",
        "_failure_threshold",
        "_half_open_max",
        "_opened_at",
        "_recovery_timeout",
        "_state",
        "_success_count",
    )

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = DEFAULT_SHORT_TIMEOUT_SECONDS,
        half_open_max_requests: int = 3,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max_requests
        self._state: str = "CLOSED"
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        """Get current state."""
        if (
            self._state == "OPEN"
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._recovery_timeout
        ):
            self._state = "HALF_OPEN"
            self._success_count = 0
        return self._state

    def record_success(self) -> None:
        """Record a successful request."""
        if self._state == "HALF_OPEN":
            self._success_count += 1
            if self._success_count >= self._half_open_max:
                self._state = "CLOSED"
                self._failure_count = 0
        else:
            self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self) -> None:
        """Record a failed request."""
        self._failure_count += 1
        if self._state == "HALF_OPEN":
            self._state = "OPEN"
        elif self._failure_count >= self._failure_threshold:
            self._state = "OPEN"
            self._opened_at = time.monotonic()

    async def execute(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute a function with circuit breaker protection.

        Args:
            func: Function to execute.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result of the function.

        Raises:
            ChaosCircuitBreakerError: If circuit is open.
        """
        if self.state == "OPEN":
            raise ChaosCircuitBreakerError(
                "Circuit breaker is open",
                circuit_name="chaos",
            )

        try:
            result = func(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            self.record_success()
            return result
        except (RuntimeError, ValueError):
            self.record_failure()
            raise


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _async_sleep(seconds: float) -> None:
    """Sleep for given seconds using asyncio."""
    import asyncio

    await asyncio.sleep(seconds)


# ------------------------------------------------------------------
# Streaming Rate Limiter
# ------------------------------------------------------------------


class StreamingRateLimiter:
    """Rate limiter for streaming/chunked responses.

    Limits the rate of chunks or tokens in streaming scenarios.
    """

    __slots__ = ("_chunk_size", "_chunks_per_second", "_limiter")

    def __init__(
        self,
        chunks_per_second: float,
        chunk_size: int = 1,
    ) -> None:
        self._limiter = TokenBucketRateLimiter(chunks_per_second)
        self._chunk_size = chunk_size

    async def acquire_chunk(self) -> None:
        """Acquire permission to send a chunk."""
        for _ in range(self._chunk_size):
            await self._limiter.acquire()

    def acquire_chunk_batch(self, count: int) -> list[bool]:
        """Try to acquire multiple chunks without waiting.

        Returns:
            List of acquisition results.
        """
        return [self._limiter.try_acquire() for _ in range(count)]
