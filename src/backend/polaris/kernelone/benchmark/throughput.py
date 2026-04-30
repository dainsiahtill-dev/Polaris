"""Throughput Benchmark Module.

This module provides tools for measuring and analyzing throughput
characteristics of KernelOne components.
"""

from __future__ import annotations

import asyncio
import gc
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar, cast

from polaris.kernelone.benchmark.models import ThroughputStats

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ThroughputMeasurement:
    """Single throughput measurement with metadata."""

    operations: int
    duration_ms: float
    ops_per_second: float = 0.0
    ops_per_minute: float = 0.0
    avg_latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Compute derived metrics."""
        if self.duration_ms > 0:
            duration_s = self.duration_ms / 1000.0
            self.ops_per_second = self.operations / duration_s
            self.ops_per_minute = self.operations / duration_s * 60.0
            self.avg_latency_ms = self.duration_ms / self.operations if self.operations > 0 else 0.0


@dataclass
class ThroughputProfile:
    """Comprehensive throughput profile with multiple measurements.

    Attributes:
        name: Name of the throughput profile.
        measurements: List of throughput measurements.
        total_operations: Sum of all operations.
        total_duration_ms: Sum of all durations.
        avg_ops_per_second: Average operations per second.
        min_ops_per_second: Minimum operations per second.
        max_ops_per_second: Maximum operations per second.
    """

    name: str
    measurements: list[ThroughputMeasurement] = field(default_factory=list)
    total_operations: int = 0
    total_duration_ms: float = 0.0
    avg_ops_per_second: float = 0.0
    min_ops_per_second: float = 0.0
    max_ops_per_second: float = 0.0

    def add_measurement(self, operations: int, duration_ms: float, metadata: dict[str, Any] | None = None) -> None:
        """Add a throughput measurement.

        Args:
            operations: Number of operations completed.
            duration_ms: Duration in milliseconds.
            metadata: Optional metadata for the measurement.
        """
        measurement = ThroughputMeasurement(
            operations=operations,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )
        self.measurements.append(measurement)
        self._recompute_aggregates()

    def _recompute_aggregates(self) -> None:
        """Recompute aggregate statistics."""
        if not self.measurements:
            return

        ops_rates = [m.ops_per_second for m in self.measurements if m.ops_per_second > 0]

        self.total_operations = sum(m.operations for m in self.measurements)
        self.total_duration_ms = sum(m.duration_ms for m in self.measurements)
        self.avg_ops_per_second = sum(ops_rates) / len(ops_rates) if ops_rates else 0.0
        self.min_ops_per_second = min(ops_rates) if ops_rates else 0.0
        self.max_ops_per_second = max(ops_rates) if ops_rates else 0.0

    def to_stats(self) -> ThroughputStats:
        """Convert to ThroughputStats."""
        stats = ThroughputStats(
            total_operations=self.total_operations,
            duration_ms=self.total_duration_ms,
        )
        stats.compute()
        return stats

    def get_throughput_stability(self) -> float:
        """Calculate throughput stability (coefficient of variation).

        Returns:
            Coefficient of variation of ops/s. Lower is more stable.
        """
        if self.avg_ops_per_second > 0 and self.min_ops_per_second > 0:
            range_ratio = (self.max_ops_per_second - self.min_ops_per_second) / self.avg_ops_per_second
            return range_ratio
        return 0.0


class ThroughputBenchmarker:
    """Throughput benchmarking tool.

    Measures how many operations can be performed within a target duration
    or how quickly a fixed number of operations completes.

    Example:
        # Time-based measurement
        bench = TimeBasedThroughputBench("op", target_duration_ms=1000)
        stats = bench.run_sync(my_function)

        # Fixed-iteration measurement
        bench = FixedIterationThroughputBench("op", iterations=1000)
        stats = bench.run_sync(my_function)
    """

    def __init__(
        self,
        name: str,
        warmup: int = 2,
        gc_between_runs: bool = False,
    ) -> None:
        """Initialize the throughput benchmarker.

        Args:
            name: Name of the benchmark.
            warmup: Number of warmup iterations.
            gc_between_runs: Whether to run GC between measurement runs.
        """
        self.name = name
        self.warmup = warmup
        self.gc_between_runs = gc_between_runs
        self.profile = ThroughputProfile(name=name)


class TimeBasedThroughputBench(ThroughputBenchmarker):
    """Time-based throughput benchmark.

    Measures how many operations complete within a target duration.

    Example:
        bench = TimeBasedThroughputBench("process_items", target_duration_ms=1000)
        stats = bench.run_sync(process_item, item)
        print(f"Throughput: {stats.ops_per_second:.2f} ops/s")
    """

    def __init__(
        self,
        name: str,
        target_duration_ms: float = 1000.0,
        warmup: int = 2,
        gc_between_runs: bool = False,
    ) -> None:
        """Initialize time-based throughput benchmark.

        Args:
            name: Name of the benchmark.
            target_duration_ms: Target duration for measurement.
            warmup: Number of warmup iterations.
            gc_between_runs: Whether to run GC between runs.
        """
        super().__init__(name=name, warmup=warmup, gc_between_runs=gc_between_runs)
        self.target_duration_ms = target_duration_ms

    def run_sync(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> ThroughputStats:
        """Run time-based throughput benchmark synchronously.

        Args:
            func: Sync function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            ThroughputStats with measurement results.
        """
        if asyncio.iscoroutinefunction(func):
            raise TypeError("Use run_async for async functions")

        # Warmup phase
        for _ in range(self.warmup):
            func(*args, **kwargs)

        # Measurement phase
        if self.gc_between_runs:
            gc.collect()

        operations = 0
        start = time.perf_counter()
        deadline = start + (self.target_duration_ms / 1000.0)

        while time.perf_counter() < deadline:
            func(*args, **kwargs)
            operations += 1

        duration_ms = (time.perf_counter() - start) * 1000.0

        stats = ThroughputStats(total_operations=operations, duration_ms=duration_ms)
        stats.compute()

        self.profile.add_measurement(operations=operations, duration_ms=duration_ms)

        return stats

    async def run_async(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> ThroughputStats:
        """Run time-based throughput benchmark asynchronously.

        Args:
            func: Async function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            ThroughputStats with measurement results.
        """
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("Use run_sync for sync functions")

        # Warmup phase
        for _ in range(self.warmup):
            await func(*args, **kwargs)

        # Measurement phase
        if self.gc_between_runs:
            gc.collect()

        operations = 0
        start = time.perf_counter()
        deadline = start + (self.target_duration_ms / 1000.0)

        while time.perf_counter() < deadline:
            await func(*args, **kwargs)
            operations += 1

        duration_ms = (time.perf_counter() - start) * 1000.0

        stats = ThroughputStats(total_operations=operations, duration_ms=duration_ms)
        stats.compute()

        self.profile.add_measurement(operations=operations, duration_ms=duration_ms)

        return stats


class FixedIterationThroughputBench(ThroughputBenchmarker):
    """Fixed-iteration throughput benchmark.

    Measures how quickly a fixed number of operations completes.

    Example:
        bench = FixedIterationThroughputBench("process_items", iterations=1000)
        stats = bench.run_sync(process_item, item)
        print(f"Duration: {stats.duration_ms:.2f} ms")
    """

    def __init__(
        self,
        name: str,
        iterations: int = 1000,
        warmup: int = 2,
        gc_between_runs: bool = False,
    ) -> None:
        """Initialize fixed-iteration throughput benchmark.

        Args:
            name: Name of the benchmark.
            iterations: Number of operations to measure.
            warmup: Number of warmup iterations.
            gc_between_runs: Whether to run GC between runs.
        """
        super().__init__(name=name, warmup=warmup, gc_between_runs=gc_between_runs)
        self.iterations = iterations

    def run_sync(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> ThroughputStats:
        """Run fixed-iteration throughput benchmark synchronously.

        Args:
            func: Sync function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            ThroughputStats with measurement results.
        """
        if asyncio.iscoroutinefunction(func):
            raise TypeError("Use run_async for async functions")

        # Warmup phase
        for _ in range(self.warmup):
            func(*args, **kwargs)

        # Measurement phase
        if self.gc_between_runs:
            gc.collect()

        start = time.perf_counter()

        for _ in range(self.iterations):
            func(*args, **kwargs)

        duration_ms = (time.perf_counter() - start) * 1000.0

        stats = ThroughputStats(total_operations=self.iterations, duration_ms=duration_ms)
        stats.compute()

        self.profile.add_measurement(operations=self.iterations, duration_ms=duration_ms)

        return stats

    async def run_async(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> ThroughputStats:
        """Run fixed-iteration throughput benchmark asynchronously.

        Args:
            func: Async function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            ThroughputStats with measurement results.
        """
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("Use run_sync for sync functions")

        # Warmup phase
        for _ in range(self.warmup):
            await func(*args, **kwargs)

        # Measurement phase
        if self.gc_between_runs:
            gc.collect()

        start = time.perf_counter()

        for _ in range(self.iterations):
            await func(*args, **kwargs)

        duration_ms = (time.perf_counter() - start) * 1000.0

        stats = ThroughputStats(total_operations=self.iterations, duration_ms=duration_ms)
        stats.compute()

        self.profile.add_measurement(operations=self.iterations, duration_ms=duration_ms)

        return stats


def throughput(func: F) -> Callable[..., ThroughputStats]:
    """Decorator for simple throughput measurement.

    Measures how many operations complete in the target duration.

    Args:
        func: Function to benchmark.

    Returns:
        Decorated function that returns ThroughputStats.

    Example:
        @throughput
        def process_item(item):
            pass

        stats = process_item(target_duration_ms=1000)
    """

    @wraps(func)
    def wrapper(*args: Any, target_duration_ms: float = 1000.0, **kwargs: Any) -> ThroughputStats:
        bench = TimeBasedThroughputBench(
            name=getattr(func, "__name__", "anonymous"),
            target_duration_ms=target_duration_ms,
        )

        if asyncio.iscoroutinefunction(func):

            async def run_benchmark() -> ThroughputStats:
                return await bench.run_async(func, *args, **kwargs)

            return asyncio.run(run_benchmark())
        else:
            return bench.run_sync(func, *args, **kwargs)

    return cast(Callable[..., ThroughputStats], wrapper)
