"""Tests for PerformanceOptimizer."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from polaris.kernelone.performance.optimizer import (
    CacheStats,
    PerformanceMetrics,
    PerformanceOptimizer,
)


class TestPerformanceOptimizer:
    """Test cases for PerformanceOptimizer."""

    def test_initialization(self) -> None:
        """Test optimizer initializes with empty cache."""
        optimizer = PerformanceOptimizer()
        assert optimizer._cache == {}
        assert optimizer.get_cache_stats().hits == 0
        assert optimizer.get_cache_stats().misses == 0

    def test_cached_operation_sync(self) -> None:
        """Test synchronous cached operation."""
        optimizer = PerformanceOptimizer()
        call_count = 0

        def operation() -> int:
            nonlocal call_count
            call_count += 1
            return 42

        # First call - should execute operation
        result = asyncio.run(optimizer.cached_operation("key1", operation))
        assert result == 42
        assert call_count == 1

        # Second call - should return cached result
        result = asyncio.run(optimizer.cached_operation("key1", operation))
        assert result == 42
        assert call_count == 1  # Not called again

    def test_cached_operation_async(self) -> None:
        """Test asynchronous cached operation."""
        optimizer = PerformanceOptimizer()
        call_count = 0

        async def operation() -> int:
            nonlocal call_count
            call_count += 1
            return 42

        async def runner() -> Any:
            return await optimizer.cached_operation("async_key", operation)

        # First call
        result = asyncio.run(runner())
        assert result == 42
        assert call_count == 1

        # Second call - should use cache
        result = asyncio.run(runner())
        assert result == 42
        assert call_count == 1

    def test_cache_expiry(self) -> None:
        """Test cache expires after TTL."""
        optimizer = PerformanceOptimizer()
        call_count = 0

        def operation() -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        async def runner() -> Any:
            return await optimizer.cached_operation("expiry_key", operation, ttl_ms=50)

        # First call
        result = asyncio.run(runner())
        assert result == 1

        # Wait for cache to expire
        time.sleep(0.1)

        # Should execute again
        result = asyncio.run(runner())
        assert result == 2
        assert call_count == 2

    def test_cache_stats(self) -> None:
        """Test cache statistics tracking."""
        optimizer = PerformanceOptimizer()

        def operation() -> int:
            return 1

        async def runner() -> Any:
            return await optimizer.cached_operation("stats_key", operation)

        # First call - miss
        asyncio.run(runner())
        # Second call - hit
        asyncio.run(runner())
        # Third call - hit
        asyncio.run(runner())

        stats = optimizer.get_cache_stats()
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.hit_rate == pytest.approx(2 / 3)

    def test_clear_cache(self) -> None:
        """Test cache clearing."""
        optimizer = PerformanceOptimizer()

        def operation() -> int:
            return 1

        async def runner() -> Any:
            return await optimizer.cached_operation("clear_key", operation)

        asyncio.run(runner())
        asyncio.run(runner())

        assert optimizer.get_cache_stats().hits == 1
        optimizer.clear_cache()
        assert optimizer.get_cache_stats() == CacheStats()

    def test_measure_latency_sync(self) -> None:
        """Test latency measurement for sync operation."""
        optimizer = PerformanceOptimizer()

        def slow_operation() -> int:
            time.sleep(0.01)  # 10ms
            return 42

        result, latency = asyncio.run(optimizer.measure_latency(slow_operation))
        assert result == 42
        assert latency >= 10.0  # At least 10ms

    def test_measure_latency_async(self) -> None:
        """Test latency measurement for async operation."""
        optimizer = PerformanceOptimizer()

        async def slow_operation() -> int:
            await asyncio.sleep(0.01)  # 10ms
            return 42

        async def runner() -> tuple[int, float]:
            return await optimizer.measure_latency(slow_operation)

        result, latency = asyncio.run(runner())
        assert result == 42
        assert latency >= 10.0  # At least 10ms

    def test_get_metrics(self) -> None:
        """Test performance metrics collection."""
        optimizer = PerformanceOptimizer()

        def operation() -> int:
            return 1

        async def runner() -> Any:
            return await optimizer.cached_operation("metrics_key", operation)

        # Generate some load
        for _ in range(10):
            asyncio.run(runner())

        metrics = optimizer.get_metrics()
        assert isinstance(metrics, PerformanceMetrics)
        assert metrics.throughput_rps >= 0.0
        assert 0.0 <= metrics.error_rate <= 1.0
        assert metrics.timestamp is not None

    def test_different_keys_cache_separately(self) -> None:
        """Test different keys have separate cache entries."""
        optimizer = PerformanceOptimizer()
        call_count = 0

        def operation() -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        async def runner(key: str) -> Any:
            return await optimizer.cached_operation(key, operation)

        result1 = asyncio.run(runner("key_a"))
        result2 = asyncio.run(runner("key_b"))
        result3 = asyncio.run(runner("key_a"))  # Should hit cache

        assert result1 == 1
        assert result2 == 2
        assert result3 == 1  # Cached
        assert call_count == 2  # Only called twice
