from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class PerformanceMetrics:
    """Current performance metrics."""

    p50_latency_ms: float
    p99_latency_ms: float
    throughput_rps: float
    error_rate: float
    timestamp: str


@dataclass
class CacheStats:
    """Cache hit/miss statistics."""

    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0


class PerformanceOptimizer:
    """Optimizer for performance improvements."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry)
        self._cache_ttl_ms: float = 5000.0  # 5 second default TTL
        self._stats = CacheStats()
        self._request_counts: list[float] = []
        self._error_counts: list[float] = []
        self._latencies: list[float] = []
        self._window_size_ms: float = 60000.0  # 1 minute window

    async def cached_operation(
        self,
        key: str,
        operation: Callable[[], Awaitable[Any]] | Callable[[], Any],
        ttl_ms: float | None = None,
    ) -> Any:
        """Execute operation with caching.

        Returns cached result if available and not expired,
        otherwise executes operation and caches result.
        """
        now = time.monotonic() * 1000  # Convert to milliseconds
        effective_ttl = ttl_ms if ttl_ms is not None else self._cache_ttl_ms

        # Check cache
        if key in self._cache:
            value, expiry = self._cache[key]
            if now < expiry:
                self._stats.hits += 1
                self._update_hit_rate()
                return value
            else:
                # Expired entry
                del self._cache[key]

        # Cache miss - execute operation
        self._stats.misses += 1
        self._update_hit_rate()

        if inspect.iscoroutinefunction(operation):
            result = await operation()
        else:
            result = operation()

        # Store in cache
        expiry_time = now + effective_ttl
        self._cache[key] = (result, expiry_time)

        return result

    def _update_hit_rate(self) -> None:
        """Update cache hit rate statistics."""
        total = self._stats.hits + self._stats.misses
        if total > 0:
            self._stats.hit_rate = self._stats.hits / total

    def get_cache_stats(self) -> CacheStats:
        """Get cache hit/miss statistics."""
        return self._stats

    def clear_cache(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._stats = CacheStats()

    async def measure_latency(
        self,
        operation: Callable[[], Awaitable[Any]] | Callable[[], Any],
    ) -> tuple[Any, float]:
        """Measure operation latency in milliseconds."""
        start = time.perf_counter()
        if inspect.iscoroutinefunction(operation):
            result = await operation()
        else:
            result = operation()
        end = time.perf_counter()
        latency_ms = (end - start) * 1000

        self._record_latency(latency_ms)
        return result, latency_ms

    def _record_latency(self, latency_ms: float) -> None:
        """Record latency for metrics calculation."""
        now = time.monotonic() * 1000
        self._latencies.append(latency_ms)
        self._request_counts.append(now)

        # Clean old entries outside the window
        cutoff = now - self._window_size_ms
        self._latencies = self._latencies[len([x for x in self._request_counts if x < cutoff]) :]
        self._request_counts = [x for x in self._request_counts if x >= cutoff]

    def _record_error(self) -> None:
        """Record an error for error rate calculation."""
        now = time.monotonic() * 1000
        self._error_counts.append(now)

        # Clean old entries outside the window
        cutoff = now - self._window_size_ms
        self._error_counts = [x for x in self._error_counts if x >= cutoff]

    def record_error(self) -> None:
        """Public API to record an error for error rate calculation.

        Call this when you catch exceptions in operations wrapped by
        cached_operation or measure_latency.
        """
        self._record_error()

    def get_metrics(self) -> PerformanceMetrics:
        """Get current performance metrics."""
        # Calculate latency percentiles
        if self._latencies:
            sorted_latencies = sorted(self._latencies)
            n = len(sorted_latencies)
            p50_idx = int(n * 0.50)
            p99_idx = int(n * 0.99)
            p50 = sorted_latencies[min(p50_idx, n - 1)]
            p99 = sorted_latencies[min(p99_idx, n - 1)]
        else:
            p50 = 0.0
            p99 = 0.0

        # Calculate throughput (requests per second)
        window_duration_s = self._window_size_ms / 1000
        throughput = len(self._latencies) / window_duration_s if window_duration_s > 0 else 0.0

        # Calculate error rate
        total_requests = len(self._request_counts)
        error_count = len(self._error_counts)
        error_rate = error_count / total_requests if total_requests > 0 else 0.0

        return PerformanceMetrics(
            p50_latency_ms=p50,
            p99_latency_ms=p99,
            throughput_rps=throughput,
            error_rate=error_rate,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
