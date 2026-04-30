"""Performance Benchmark Fixtures and Decorators.

This module provides decorators and fixtures for measuring and validating
performance characteristics of KernelOne components.
"""

from __future__ import annotations

import asyncio
import gc
import time
import tracemalloc
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, TypeVar, cast

from polaris.kernelone.benchmark.models import (
    BenchmarkResult,
    BenchmarkStats,
    MemoryStats,
    ThroughputStats,
)

F = TypeVar("F", bound=Callable[..., Any])


class BenchmarkContext:
    """Context manager for collecting benchmark statistics.

    Example:
        with BenchmarkContext() as ctx:
            # perform operations
            ctx.record_latency(5.2)
            ctx.record_latency(3.8)
        result = ctx.get_stats()
    """

    def __init__(self, metric_name: str = "default") -> None:
        self.metric_name = metric_name
        self.latencies: list[float] = []
        self._start_time: float = 0.0

    def __enter__(self) -> BenchmarkContext:
        gc.collect()
        self._start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency measurement in milliseconds."""
        self.latencies.append(latency_ms)

    def start_timer(self) -> float:
        """Start a high-resolution timer.

        Returns:
            Start time for use with stop_timer.
        """
        return time.perf_counter()

    def stop_timer(self, start_time: float) -> float:
        """Stop a timer and return elapsed time in milliseconds.

        Args:
            start_time: Start time from start_timer().

        Returns:
            Elapsed time in milliseconds.
        """
        return (time.perf_counter() - start_time) * 1000.0

    def get_stats(self) -> BenchmarkStats:
        """Get statistics from collected measurements."""
        stats = BenchmarkStats(latencies=self.latencies)
        stats.compute_statistics()
        return stats

    def get_result(self) -> BenchmarkResult:
        """Get a structured BenchmarkResult."""
        stats = self.get_stats()
        return BenchmarkResult.from_stats(self.metric_name, stats)


def benchmark(
    warmup: int = 3,
    iterations: int = 100,
    percentile: bool = True,
    metric_name: str = "benchmark",
) -> Callable[[F], Callable[..., BenchmarkStats]]:
    """Performance benchmark decorator.

    Measures execution time and computes latency statistics including
    p50, p90, p99 percentiles.

    Args:
        warmup: Number of warmup iterations before measurement.
        iterations: Number of measurement iterations.
        percentile: Whether to compute percentile statistics.
        metric_name: Name for the benchmark metric.

    Returns:
        Decorated function that returns BenchmarkStats.

    Example:
        @benchmark(warmup=3, iterations=100)
        async def my_async_function():
            # function to benchmark
            pass

        stats = await my_async_function()
        print(f"p50: {stats.p50}ms, p90: {stats.p90}ms, p99: {stats.p99}ms")
    """

    def decorator(func: F) -> Callable[..., BenchmarkStats]:
        is_async = asyncio.iscoroutinefunction(func)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> BenchmarkStats:
            stats = BenchmarkStats(warmup_count=warmup, iterations=iterations)

            # Warmup phase
            for _ in range(warmup):
                if is_async:
                    await func(*args, **kwargs)
                else:
                    func(*args, **kwargs)

            # Measurement phase
            for _ in range(iterations):
                start = time.perf_counter()
                if is_async:
                    await func(*args, **kwargs)
                else:
                    func(*args, **kwargs)
                latency = (time.perf_counter() - start) * 1000.0
                stats.latencies.append(latency)

            # Compute statistics
            stats.compute_statistics()
            return stats

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> BenchmarkStats:
            stats = BenchmarkStats(warmup_count=warmup, iterations=iterations)

            # Warmup phase
            for _ in range(warmup):
                func(*args, **kwargs)

            # Measurement phase
            for _ in range(iterations):
                start = time.perf_counter()
                func(*args, **kwargs)
                latency = (time.perf_counter() - start) * 1000.0
                stats.latencies.append(latency)

            # Compute statistics
            stats.compute_statistics()
            return stats

        return cast("Callable[..., BenchmarkStats]", async_wrapper if is_async else sync_wrapper)

    return decorator


def memory_benchmark(func: F) -> Callable[..., dict[str, Any]]:
    """Memory benchmark decorator using tracemalloc.

    Measures memory allocation before and after function execution.

    Args:
        func: Function to benchmark.

    Returns:
        Decorated function that returns dict with result and memory stats.

    Example:
        @memory_benchmark
        def memory_intensive_function():
            # function to benchmark
            pass

        result = memory_intensive_function()
        print(f"Peak memory: {result['memory_peak_mb']} MB")
    """

    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        gc.collect()
        tracemalloc.start()

        try:
            baseline = tracemalloc.get_traced_memory()[0]

            if asyncio.iscoroutinefunction(func):
                raise TypeError("Use async_memory_benchmark for async functions")
            result = func(*args, **kwargs)

            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        delta = current - baseline

        return {
            "result": result,
            "memory_current_mb": current / 1024 / 1024,
            "memory_peak_mb": peak / 1024 / 1024,
            "memory_delta_mb": delta / 1024 / 1024,
            "memory_baseline_mb": baseline / 1024 / 1024,
        }

    return wrapper


def async_memory_benchmark(func: F) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Async memory benchmark decorator using tracemalloc.

    Args:
        func: Async function to benchmark.

    Returns:
        Decorated async function that returns dict with result and memory stats.
    """

    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        gc.collect()
        tracemalloc.start()

        try:
            baseline = tracemalloc.get_traced_memory()[0]

            if not asyncio.iscoroutinefunction(func):
                raise TypeError("Use memory_benchmark for sync functions")
            result = await func(*args, **kwargs)

            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        delta = current - baseline

        return {
            "result": result,
            "memory_current_mb": current / 1024 / 1024,
            "memory_peak_mb": peak / 1024 / 1024,
            "memory_delta_mb": delta / 1024 / 1024,
            "memory_baseline_mb": baseline / 1024 / 1024,
        }

    return wrapper


def throughput_benchmark(
    warmup: int = 2,
    target_duration_ms: float = 1000.0,
) -> Callable[[F], Callable[..., ThroughputStats]]:
    """Throughput benchmark decorator.

    Measures how many operations can be performed within a target duration.

    Args:
        warmup: Number of warmup iterations.
        target_duration_ms: Target duration for measurement in milliseconds.

    Returns:
        Decorated function that returns ThroughputStats.

    Example:
        @throughput_benchmark(warmup=2, target_duration_ms=1000)
        async def process_item(item):
            # operation to measure
            pass
    """

    def decorator(func: F) -> Callable[..., ThroughputStats]:
        is_async = asyncio.iscoroutinefunction(func)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> ThroughputStats:
            stats = ThroughputStats()

            # Warmup phase
            for _ in range(warmup):
                await func(*args, **kwargs)

            # Measurement phase
            start = time.perf_counter()
            deadline = start + (target_duration_ms / 1000.0)

            while time.perf_counter() < deadline:
                await func(*args, **kwargs)
                stats.total_operations += 1

            stats.duration_ms = (time.perf_counter() - start) * 1000.0
            stats.compute()
            return stats

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> ThroughputStats:
            stats = ThroughputStats()

            # Warmup phase
            for _ in range(warmup):
                func(*args, **kwargs)

            # Measurement phase
            start = time.perf_counter()
            deadline = start + (target_duration_ms / 1000.0)

            while time.perf_counter() < deadline:
                func(*args, **kwargs)
                stats.total_operations += 1

            stats.duration_ms = (time.perf_counter() - start) * 1000.0
            stats.compute()
            return stats

        return cast("Callable[..., ThroughputStats]", async_wrapper if is_async else sync_wrapper)

    return decorator


# Pytest Fixtures — guarded so pytest remains a test-only dependency.

try:
    import pytest

    def pytest_configure(config: Any) -> None:
        """Configure pytest with custom markers."""
        config.addinivalue_line("markers", "benchmark: mark test as a benchmark")

    @pytest.fixture
    def benchmark_stats() -> BenchmarkStats:
        """Pytest fixture providing a fresh BenchmarkStats instance."""
        return BenchmarkStats()

    @pytest.fixture
    def benchmark_context() -> Callable[[str], BenchmarkContext]:
        """Pytest fixture providing a BenchmarkContext factory.

        Returns:
            Factory function that creates BenchmarkContext instances.
        """
        return lambda name: BenchmarkContext(metric_name=name)

    @pytest.fixture
    def memory_context() -> Callable[[], MemoryStats]:
        """Pytest fixture providing a MemoryStats factory.

        Returns:
            Factory function that creates MemoryStats instances.
        """
        return MemoryStats

except ImportError:
    pass
