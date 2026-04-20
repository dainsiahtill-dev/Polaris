"""Memory Benchmark Module.

This module provides tools for measuring and analyzing memory usage
characteristics of KernelOne components.
"""

from __future__ import annotations

import asyncio
import gc
import sys
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar

from polaris.kernelone.benchmark.models import MemoryBenchmarkResult

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class MemorySnapshot:
    """Snapshot of memory state at a point in time."""

    current_bytes: int
    peak_bytes: int
    timestamp: float = field(default_factory=lambda: 0.0)

    @property
    def current_mb(self) -> float:
        """Current memory in megabytes."""
        return self.current_bytes / 1024 / 1024

    @property
    def peak_mb(self) -> float:
        """Peak memory in megabytes."""
        return self.peak_bytes / 1024 / 1024


@dataclass
class MemoryProfile:
    """Comprehensive memory usage profile.

    Attributes:
        name: Name of the memory profile.
        baseline: Baseline memory snapshot before operation.
        peak: Peak memory snapshot during operation.
        final: Final memory snapshot after operation.
        delta_bytes: Memory change in bytes.
        allocations: Number of allocations.
        deallocations: Number of deallocations.
    """

    name: str
    baseline: MemorySnapshot | None = None
    peak: MemorySnapshot | None = None
    final: MemorySnapshot | None = None
    delta_bytes: int = 0
    delta_mb: float = 0.0
    allocations: int = 0
    deallocations: int = 0
    gc_collections: tuple[int, ...] = field(default_factory=lambda: (0, 0, 0))

    def get_delta_mb(self) -> float:
        """Get memory delta in megabytes."""
        if self.final and self.baseline:
            return (self.final.current_bytes - self.baseline.current_bytes) / 1024 / 1024
        return self.delta_mb

    def to_result(self) -> MemoryBenchmarkResult:
        """Convert to MemoryBenchmarkResult."""
        return MemoryBenchmarkResult(
            memory_baseline_mb=self.baseline.current_mb if self.baseline else 0.0,
            memory_delta_mb=self.get_delta_mb(),
            memory_peak_mb=self.peak.current_mb if self.peak else 0.0,
            allocations_count=self.allocations,
            deallocations_count=self.deallocations,
            gc_collections=self.gc_collections,
        )


class MemoryBenchmarker:
    """Memory benchmarking tool with detailed tracking.

    Example:
        bench = MemoryBenchmarker("my_operation")
        result = bench.run_sync(my_function)
        print(f"Delta: {result.memory_delta_mb} MB")
    """

    def __init__(
        self,
        name: str,
        track_gc: bool = True,
        gc_before: bool = True,
        gc_after: bool = True,
    ) -> None:
        """Initialize the memory benchmarker.

        Args:
            name: Name of the benchmark.
            track_gc: Whether to track garbage collection.
            gc_before: Whether to run GC before measurement.
            gc_after: Whether to run GC after measurement.
        """
        self.name = name
        self.track_gc = track_gc
        self.gc_before = gc_before
        self.gc_after = gc_after
        self.profile = MemoryProfile(name=name)

    def _take_snapshot(self, current: int, peak: int) -> MemorySnapshot:
        """Create a memory snapshot."""
        return MemorySnapshot(current_bytes=current, peak_bytes=peak)

    def run_sync(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> MemoryBenchmarkResult:
        """Run memory benchmark synchronously.

        Args:
            func: Sync function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            MemoryBenchmarkResult with memory measurements.
        """
        if self.gc_before:
            gc.collect()

        gc_before_state = gc.get_count() if self.track_gc else (0, 0, 0)

        tracemalloc.start()
        baseline_current, baseline_peak = tracemalloc.get_traced_memory()
        self.profile.baseline = self._take_snapshot(baseline_current, baseline_peak)

        func(*args, **kwargs)

        final_current, final_peak = tracemalloc.get_traced_memory()
        _peak_current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        if self.gc_after:
            gc.collect()

        gc_after_state = gc.get_count() if self.track_gc else (0, 0, 0)

        self.profile.final = self._take_snapshot(final_current, final_peak)
        self.profile.peak = self._take_snapshot(final_peak, final_peak)
        self.profile.delta_bytes = final_current - baseline_current
        self.profile.delta_mb = self.profile.get_delta_mb()

        if self.track_gc:
            self.profile.gc_collections = tuple(
                after - before for after, before in zip(gc_after_state, gc_before_state, strict=True)
            )

        self.profile.allocations = sys.getallocatedblocks()

        return self.profile.to_result()

    async def run_async(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> MemoryBenchmarkResult:
        """Run memory benchmark asynchronously.

        Args:
            func: Async function to benchmark.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.

        Returns:
            MemoryBenchmarkResult with memory measurements.
        """
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("Use run_sync for sync functions")

        if self.gc_before:
            gc.collect()

        gc_before_state = gc.get_count() if self.track_gc else (0, 0, 0)

        tracemalloc.start()
        baseline_current, baseline_peak = tracemalloc.get_traced_memory()
        self.profile.baseline = self._take_snapshot(baseline_current, baseline_peak)

        await func(*args, **kwargs)

        final_current, final_peak = tracemalloc.get_traced_memory()
        _peak_current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        if self.gc_after:
            gc.collect()

        gc_after_state = gc.get_count() if self.track_gc else (0, 0, 0)

        self.profile.final = self._take_snapshot(final_current, final_peak)
        self.profile.peak = self._take_snapshot(final_peak, final_peak)
        self.profile.delta_bytes = final_current - baseline_current
        self.profile.delta_mb = self.profile.get_delta_mb()

        if self.track_gc:
            self.profile.gc_collections = tuple(
                after - before for after, before in zip(gc_after_state, gc_before_state, strict=True)
            )

        self.profile.allocations = sys.getallocatedblocks()

        return self.profile.to_result()

    def get_profile(self) -> MemoryProfile:
        """Get the full memory profile."""
        return self.profile


class MemoryTracker:
    """Context manager for tracking memory within a code block.

    Example:
        with MemoryTracker("operation") as tracker:
            # perform operations
            allocate_memory()
        print(tracker.get_delta_mb())
    """

    def __init__(self, name: str = "tracked") -> None:
        """Initialize the memory tracker.

        Args:
            name: Name for the tracked operation.
        """
        self.name = name
        self.profile = MemoryProfile(name=name)
        self._active: bool = False

    def __enter__(self) -> MemoryTracker:
        """Start tracking memory."""
        gc.collect()
        gc.collect()  # Double collect for cleaner baseline
        self.gc_before = gc.get_count()
        tracemalloc.start()
        baseline_current, _ = tracemalloc.get_traced_memory()
        self.profile.baseline = self._take_snapshot(baseline_current, baseline_current)
        self._active = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Stop tracking memory."""
        gc.collect()
        final_current, final_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        gc_after = gc.get_count()

        self.profile.final = self._take_snapshot(final_current, final_peak)
        self.profile.peak = self._take_snapshot(final_peak, final_peak)
        self.profile.delta_bytes = final_current - (self.profile.baseline.current_bytes if self.profile.baseline else 0)
        self.profile.gc_collections = tuple(
            after - before for after, before in zip(gc_after, self.gc_before, strict=True)
        )
        self._active = False

    def _take_snapshot(self, current: int, peak: int) -> MemorySnapshot:
        """Create a memory snapshot."""
        return MemorySnapshot(current_bytes=current, peak_bytes=peak)

    def get_delta_mb(self) -> float:
        """Get memory delta in megabytes."""
        return self.profile.get_delta_mb()

    def get_peak_mb(self) -> float:
        """Get peak memory in megabytes."""
        return self.profile.peak.current_mb if self.profile.peak else 0.0

    def get_result(self) -> MemoryBenchmarkResult:
        """Get the benchmark result."""
        return self.profile.to_result()


def memory_profile(func: F) -> Callable[..., MemoryProfile]:
    """Decorator to profile memory usage of a function.

    Args:
        func: Function to profile.

    Returns:
        Decorated function that returns MemoryProfile.

    Example:
        @memory_profile
        def memory_intensive():
            data = [0] * 1000000
            return len(data)

        profile = memory_intensive()
        print(f"Delta: {profile.get_delta_mb():.3f} MB")
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> MemoryProfile:
        name = getattr(func, "__name__", "anonymous")
        tracker = MemoryTracker(name)

        with tracker:
            func(*args, **kwargs)

        return tracker.profile

    return wrapper  # type: ignore[return-value]


def async_memory_profile(func: F) -> Callable[..., MemoryProfile]:
    """Decorator to profile memory usage of an async function.

    Args:
        func: Async function to profile.

    Returns:
        Decorated async function that returns MemoryProfile.
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> MemoryProfile:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("Use memory_profile for sync functions")

        name = getattr(func, "__name__", "anonymous")
        tracker = MemoryTracker(name)

        with tracker:
            await func(*args, **kwargs)

        return tracker.profile

    return wrapper  # type: ignore[return-value]
