"""Latency Benchmark Module.

This module provides specialized tools for measuring and analyzing
latency characteristics of KernelOne components.
"""

from __future__ import annotations

import asyncio
import gc
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar

from polaris.kernelone.benchmark.models import BenchmarkStats, LatencyBenchmarkResult

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class LatencyMeasurement:
    """Individual latency measurement with metadata."""

    latency_ms: float
    timestamp: float = field(default_factory=time.perf_counter)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LatencyProfile:
    """Comprehensive latency profile with detailed statistics.

    Attributes:
        name: Name of the latency profile.
        measurements: List of individual measurements.
        warmup_measurements: Warmup phase measurements (discarded from stats).
        p50: 50th percentile latency.
        p90: 90th percentile latency.
        p95: 95th percentile latency.
        p99: 99th percentile latency.
        mean: Mean latency.
        median: Median latency.
        std_dev: Standard deviation.
        min_latency: Minimum latency.
        max_latency: Maximum latency.
    """

    name: str
    measurements: list[LatencyMeasurement] = field(default_factory=list)
    warmup_measurements: list[LatencyMeasurement] = field(default_factory=list)
    p50: float = 0.0
    p90: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    std_dev: float = 0.0
    min_latency: float = 0.0
    max_latency: float = 0.0

    def add_measurement(self, latency_ms: float, metadata: dict[str, Any] | None = None) -> None:
        """Add a measurement to the profile.

        Args:
            latency_ms: Latency in milliseconds.
            metadata: Optional metadata for the measurement.
        """
        measurement = LatencyMeasurement(latency_ms=latency_ms, metadata=metadata or {})
        self.measurements.append(measurement)

    def add_warmup(self, latency_ms: float) -> None:
        """Add a warmup measurement (not included in final statistics).

        Args:
            latency_ms: Warmup latency in milliseconds.
        """
        self.warmup_measurements.append(LatencyMeasurement(latency_ms=latency_ms))

    def compute_statistics(self) -> None:
        """Compute all statistical measures from measurements."""
        if not self.measurements:
            return

        latencies = sorted([m.latency_ms for m in self.measurements])
        n = len(latencies)

        self.p50 = latencies[int(n * 0.50)]
        self.p90 = latencies[int(n * 0.90)]
        self.p95 = latencies[int(n * 0.95)]
        self.p99 = latencies[int(n * 0.99)]
        self.mean = statistics.mean(latencies)
        self.median = statistics.median(latencies)
        self.std_dev = statistics.stdev(latencies) if n > 1 else 0.0
        self.min_latency = min(latencies)
        self.max_latency = max(latencies)

    def to_result(self) -> LatencyBenchmarkResult:
        """Convert to LatencyBenchmarkResult."""
        result = LatencyBenchmarkResult(
            metric_name=self.name,
            p50_ms=self.p50,
            p90_ms=self.p90,
            percentile_95_ms=self.p95,
            p99_ms=self.p99,
            mean_ms=self.mean,
            std_dev_ms=self.std_dev,
            min_ms=self.min_latency,
            max_ms=self.max_latency,
        )
        result.median_ms = self.median
        result.percentile_95_ms = self.p95
        result.compute_tail_ratio()
        return result

    def get_tail_ratio(self) -> float:
        """Get tail latency ratio (p99/p50).

        Returns:
            Ratio of p99 to p50 latency. Values > 2.0 indicate significant tail latency.
        """
        if self.p50 > 0:
            return self.p99 / self.p50
        return 0.0

    def get_coefficient_of_variation(self) -> float:
        """Get coefficient of variation (std_dev/mean).

        Returns:
            Coefficient of variation. Values > 0.5 indicate high variability.
        """
        if self.mean > 0:
            return self.std_dev / self.mean
        return 0.0


class LatencyBenchmarker:
    """Advanced latency benchmarking tool with warmup support.

    Example:
        async def benchmark_operation():
            bench = LatencyBenchmarker("my_operation", warmup=3, iterations=100)
            await bench.run(my_async_function)
            result = bench.get_result()
            print(f"p90: {result.p90_ms}ms")
    """

    def __init__(
        self,
        name: str,
        warmup: int = 3,
        iterations: int = 100,
        gc_between_iterations: bool = False,
    ) -> None:
        """Initialize the latency benchmarker.

        Args:
            name: Name of the benchmark.
            warmup: Number of warmup iterations.
            iterations: Number of measurement iterations.
            gc_between_iterations: Whether to run GC between iterations.
        """
        self.name = name
        self.warmup = warmup
        self.iterations = iterations
        self.gc_between_iterations = gc_between_iterations
        self.profile = LatencyProfile(name=name)

    async def run_async(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> LatencyProfile:
        """Run async benchmark.

        Args:
            func: Async function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            LatencyProfile with collected measurements.
        """
        is_async = asyncio.iscoroutinefunction(func)

        # Warmup phase
        for _ in range(self.warmup):
            if is_async:
                await func(*args, **kwargs)
            else:
                func(*args, **kwargs)

        # Measurement phase
        for _ in range(self.iterations):
            if self.gc_between_iterations:
                gc.collect()

            start = time.perf_counter()
            if is_async:
                await func(*args, **kwargs)
            else:
                func(*args, **kwargs)
            latency = (time.perf_counter() - start) * 1000.0

            self.profile.add_measurement(latency)

        self.profile.compute_statistics()
        return self.profile

    def run_sync(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> LatencyProfile:
        """Run sync benchmark.

        Args:
            func: Sync function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            LatencyProfile with collected measurements.
        """
        if asyncio.iscoroutinefunction(func):
            raise TypeError("Use run_async for async functions")

        # Warmup phase
        for _ in range(self.warmup):
            func(*args, **kwargs)

        # Measurement phase
        for _ in range(self.iterations):
            if self.gc_between_iterations:
                gc.collect()

            start = time.perf_counter()
            func(*args, **kwargs)
            latency = (time.perf_counter() - start) * 1000.0

            self.profile.add_measurement(latency)

        self.profile.compute_statistics()
        return self.profile

    def get_result(self) -> LatencyBenchmarkResult:
        """Get structured benchmark result."""
        return self.profile.to_result()

    def get_stats(self) -> BenchmarkStats:
        """Get raw BenchmarkStats."""
        latencies = [m.latency_ms for m in self.profile.measurements]
        stats = BenchmarkStats(latencies=latencies, warmup_count=self.warmup, iterations=self.iterations)
        stats.compute_statistics()
        return stats


def measure_latency(func: Callable[..., Any]) -> Callable[..., float]:
    """Decorator to measure single invocation latency.

    Args:
        func: Function to measure.

    Returns:
        Decorated function that returns latency in milliseconds.

    Example:
        @measure_latency
        def my_function():
            pass

        latency = my_function()  # returns latency in ms
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> float:
        start = time.perf_counter()
        func(*args, **kwargs)
        return (time.perf_counter() - start) * 1000.0

    return wrapper


async def measure_latency_async(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to measure single async invocation latency.

    Args:
        func: Async function to measure.

    Returns:
        Decorated async function that returns latency in milliseconds.

    Example:
        @measure_latency_async
        async def my_async_function():
            pass

        latency = await my_async_function()  # returns latency in ms
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> float:
        start = time.perf_counter()
        await func(*args, **kwargs)
        return (time.perf_counter() - start) * 1000.0

    return wrapper  # type: ignore[return-value]
