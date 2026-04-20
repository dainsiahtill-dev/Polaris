"""Pytest Configuration for Performance Benchmark Tests.

This module provides pytest fixtures and configuration for
benchmark testing in the KernelOne framework.
"""

from __future__ import annotations

import gc
import time
import tracemalloc
from typing import TYPE_CHECKING, Any

import pytest
from polaris.kernelone.benchmark.fixtures import BenchmarkContext, BenchmarkStats
from polaris.kernelone.benchmark.models import BenchmarkResult, MemoryStats, ThroughputStats

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


def pytest_configure(config: Any) -> None:
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line(
        "markers",
        "benchmark: mark test as a benchmark (excludes from normal test runs)",
    )
    config.addinivalue_line(
        "markers",
        "slow_benchmark: mark test as a slow benchmark",
    )
    config.addinivalue_line(
        "markers",
        "memory_benchmark: mark test as a memory benchmark",
    )
    config.addinivalue_line(
        "markers",
        "latency_benchmark: mark test as a latency benchmark",
    )
    config.addinivalue_line(
        "markers",
        "throughput_benchmark: mark test as a throughput benchmark",
    )


# Benchmark Fixtures


@pytest.fixture
def benchmark_stats() -> Generator[BenchmarkStats, None, None]:
    """Provide a fresh BenchmarkStats instance for each test."""
    yield BenchmarkStats()
    gc.collect()


@pytest.fixture
def benchmark_context() -> Callable[[str], BenchmarkContext]:
    """Factory fixture for creating BenchmarkContext instances.

    Returns:
        A factory function that creates BenchmarkContext with a given name.

    Example:
        def test_something(benchmark_context):
            ctx = benchmark_context("my_benchmark")
            with ctx:
                # perform operations
                ctx.record_latency(5.2)
            result = ctx.get_result()
            assert result.p90_ms < 10.0
    """
    return lambda name: BenchmarkContext(metric_name=name)


@pytest.fixture
def memory_stats() -> Generator[MemoryStats, None, None]:
    """Provide a fresh MemoryStats instance for each test."""
    yield MemoryStats()
    gc.collect()


@pytest.fixture
def throughput_stats() -> Generator[ThroughputStats, None, None]:
    """Provide a fresh ThroughputStats instance for each test."""
    yield ThroughputStats()


@pytest.fixture
def baseline_memory() -> Generator[dict[str, float], None, None]:
    """Capture baseline memory state before a test.

    Yields:
        Dictionary with baseline memory metrics in MB.

    Example:
        def test_memory_usage(baseline_memory):
            # allocate memory
            data = allocate_large_data()
            # check memory increase
            current, peak = tracemalloc.get_traced_memory()
            increase_mb = (current - baseline_memory['current'] * 1024 * 1024) / 1024 / 1024
            assert increase_mb < 100  # less than 100MB increase
    """
    gc.collect()
    gc.collect()

    tracemalloc.start()
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    baseline = {
        "current_mb": current / 1024 / 1024,
        "baseline_bytes": current,
    }

    yield baseline

    gc.collect()


# Benchmark Configuration Fixtures


@pytest.fixture
def benchmark_config() -> dict[str, Any]:
    """Default benchmark configuration.

    Returns:
        Dictionary with benchmark settings.
    """
    return {
        "warmup_iterations": 3,
        "measurement_iterations": 100,
        "target_duration_ms": 1000.0,
        "gc_between_iterations": False,
        "track_memory": True,
        "percentiles": [50, 90, 95, 99],
    }


@pytest.fixture
def fast_benchmark_config() -> dict[str, Any]:
    """Fast benchmark configuration for CI.

    Returns:
        Dictionary with fast benchmark settings.
    """
    return {
        "warmup_iterations": 1,
        "measurement_iterations": 10,
        "target_duration_ms": 100.0,
        "gc_between_iterations": True,
        "track_memory": True,
        "percentiles": [50, 90, 99],
    }


# Performance Threshold Fixtures


@pytest.fixture
def latency_thresholds() -> dict[str, float]:
    """Default latency thresholds in milliseconds.

    Returns:
        Dictionary with p50, p90, p99 thresholds.
    """
    return {
        "p50_ms": 10.0,
        "p90_ms": 50.0,
        "p99_ms": 100.0,
    }


@pytest.fixture
def memory_thresholds() -> dict[str, float]:
    """Default memory thresholds in megabytes.

    Returns:
        Dictionary with memory thresholds.
    """
    return {
        "delta_mb": 50.0,
        "peak_mb": 200.0,
    }


@pytest.fixture
def throughput_thresholds() -> dict[str, float]:
    """Default throughput thresholds.

    Returns:
        Dictionary with throughput thresholds (ops/s).
    """
    return {
        "min_ops_per_second": 100.0,
        "max_latency_ms": 10.0,
    }


# Helper Fixtures


@pytest.fixture
def perf_counter() -> Callable[[], float]:
    """Provide a high-resolution timer.

    Returns:
        Function that returns current time in seconds with high resolution.
    """
    return time.perf_counter


@pytest.fixture
def memory_tracker() -> Callable[[], dict[str, Any]]:
    """Factory for tracking memory within a test.

    Returns:
        Factory function that creates a memory tracking context.

    Example:
        def test_memory(benchmark, memory_tracker):
            tracker = memory_tracker()
            # ... perform operations ...
            delta_mb = tracker['delta_bytes'] / 1024 / 1024
            assert delta_mb < 10.0
    """
    return lambda: {
        "active": False,
        "start_bytes": 0,
        "end_bytes": 0,
        "peak_bytes": 0,
        "start": time.perf_counter,
    }


# Parametrization Helpers


def benchmark_params(*names: str) -> list[tuple[str, int, int]]:
    """Generate benchmark test parameters.

    Args:
        *names: Names of benchmarks to generate params for.

    Returns:
        List of tuples (name, warmup, iterations).
    """
    return [(name, 3, 100) for name in names]


# Skipping utilities


def skip_if_slow_benchmark(reason: str = "Slow benchmark skipped") -> pytest.MarkDecorator:
    """Skip marker for slow benchmarks."""
    return pytest.mark.skip(reason=reason)


# Report generation helpers


def generate_benchmark_report(results: list[BenchmarkResult]) -> dict[str, Any]:
    """Generate a benchmark report from results.

    Args:
        results: List of BenchmarkResult instances.

    Returns:
        Dictionary containing the formatted report.
    """
    return {
        "summary": {
            "total_benchmarks": len(results),
            "timestamp": time.time(),
        },
        "results": [r.to_dict() for r in results],
    }
