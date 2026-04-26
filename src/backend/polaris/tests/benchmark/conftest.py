"""Benchmark test configuration and fixtures.

Performance baseline infrastructure using simple time.perf_counter() timing.
No external benchmark dependencies required.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

import pytest

# =============================================================================
# Performance Threshold Constants (milliseconds)
# =============================================================================


class LatencyThresholds:
    """Performance latency thresholds in milliseconds.

    These define acceptable upper bounds for various operations.
    Exceeding these thresholds indicates potential performance regression.
    """

    # TurnTransactionController single execution
    TURN_EXECUTE_P50_MS: float = 100.0
    TURN_EXECUTE_P95_MS: float = 500.0
    TURN_EXECUTE_P99_MS: float = 1000.0

    # LLM Provider mock call latency
    LLM_PROVIDER_P50_MS: float = 50.0
    LLM_PROVIDER_P95_MS: float = 200.0
    LLM_PROVIDER_P99_MS: float = 500.0

    # ContextOS read/write operations
    CONTEXT_OS_READ_P50_MS: float = 10.0
    CONTEXT_OS_READ_P95_MS: float = 50.0
    CONTEXT_OS_WRITE_P50_MS: float = 20.0
    CONTEXT_OS_WRITE_P95_MS: float = 100.0

    # State machine transitions
    STATE_MACHINE_TRANSITION_MS: float = 5.0

    # Ledger operations
    LEDGER_RECORD_MS: float = 10.0


# =============================================================================
# Benchmark Result Data Structures
# =============================================================================


@dataclass
class BenchmarkResult:
    """Result of a single benchmark measurement."""

    name: str
    iterations: int
    total_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    stddev_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "iterations": self.iterations,
            "total_ms": round(self.total_ms, 3),
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "stddev_ms": round(self.stddev_ms, 3),
            "metadata": self.metadata,
        }


@dataclass
class BenchmarkSuite:
    """Collection of benchmark results for a test run."""

    suite_name: str
    timestamp: str
    results: list[BenchmarkResult] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "suite_name": self.suite_name,
            "timestamp": self.timestamp,
            "environment": self.environment,
            "results": [r.to_dict() for r in self.results],
        }


# =============================================================================
# Timing Utilities
# =============================================================================


def percentile(values: list[float], p: float) -> float:
    """Calculate percentile from sorted values.

    Args:
        values: Sorted list of values.
        p: Percentile (0-100).

    Returns:
        The p-th percentile value.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[f]
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def calculate_stats(times_ms: list[float]) -> dict[str, Any]:
    """Calculate statistics from timing measurements.

    Args:
        times_ms: List of timing measurements in milliseconds.

    Returns:
        Dictionary with min, max, mean, p50, p95, p99, stddev.
    """
    if not times_ms:
        return {
            "min_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "stddev_ms": 0.0,
        }

    n = len(times_ms)
    mean = sum(times_ms) / n
    variance = sum((t - mean) ** 2 for t in times_ms) / n if n > 1 else 0.0

    return {
        "min_ms": min(times_ms),
        "max_ms": max(times_ms),
        "mean_ms": mean,
        "p50_ms": percentile(times_ms, 50),
        "p95_ms": percentile(times_ms, 95),
        "p99_ms": percentile(times_ms, 99),
        "stddev_ms": variance**0.5,
    }


@contextmanager
def benchmark_timer() -> Generator[list[float], None, None]:
    """Context manager that measures elapsed time.

    Usage:
        with benchmark_timer() as times:
            for _ in range(100):
                start = time.perf_counter()
                # ... operation ...
                times.append((time.perf_counter() - start) * 1000)
    """
    times: list[float] = []
    yield times


def run_benchmark(
    name: str,
    func: Any,
    iterations: int = 100,
    warmup: int = 5,
    **kwargs: Any,
) -> BenchmarkResult:
    """Run a benchmark function multiple times and collect statistics.

    Args:
        name: Benchmark name.
        func: Callable to benchmark (should return None or be timed internally).
        iterations: Number of iterations to run.
        warmup: Number of warmup iterations (not counted).
        **kwargs: Additional arguments passed to func.

    Returns:
        BenchmarkResult with timing statistics.
    """
    for _ in range(warmup):
        func(**kwargs)

    times_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        func(**kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        times_ms.append(elapsed_ms)

    stats = calculate_stats(times_ms)
    total_ms = sum(times_ms)

    return BenchmarkResult(
        name=name,
        iterations=iterations,
        total_ms=total_ms,
        **stats,
    )


def run_async_benchmark(
    name: str,
    func: Any,
    iterations: int = 100,
    warmup: int = 5,
    **kwargs: Any,
) -> BenchmarkResult:
    """Run an async benchmark function multiple times.

    Args:
        name: Benchmark name.
        func: Async callable to benchmark.
        iterations: Number of iterations to run.
        warmup: Number of warmup iterations.
        **kwargs: Additional arguments passed to func.

    Returns:
        BenchmarkResult with timing statistics.
    """
    import asyncio

    async def _run() -> list[float]:
        for _ in range(warmup):
            await func(**kwargs)

        times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            await func(**kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times.append(elapsed_ms)
        return times

    loop = asyncio.new_event_loop()
    try:
        times_ms = loop.run_until_complete(_run())
    finally:
        loop.close()

    stats = calculate_stats(times_ms)
    total_ms = sum(times_ms)

    return BenchmarkResult(
        name=name,
        iterations=iterations,
        total_ms=total_ms,
        **stats,
    )


# =============================================================================
# Baseline Storage
# =============================================================================

BASELINES_DIR = Path(__file__).parent / "baselines"


def save_baseline(suite: BenchmarkSuite, filename: str | None = None) -> Path:
    """Save benchmark suite results to baselines directory.

    Args:
        suite: Benchmark suite to save.
        filename: Optional filename (defaults to suite_name + .json).

    Returns:
        Path to saved file.
    """
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"{suite.suite_name}.json"
    filepath = BASELINES_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite.to_dict(), f, indent=2, ensure_ascii=False)
    return filepath


def load_baseline(filename: str) -> dict[str, Any]:
    """Load baseline results from file.

    Args:
        filename: Baseline filename (relative to baselines/).

    Returns:
        Loaded baseline data.
    """
    filepath = BASELINES_DIR / filename
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def compare_with_baseline(
    current: BenchmarkResult,
    baseline_data: dict[str, Any],
    tolerance_pct: float = 20.0,
) -> dict[str, Any]:
    """Compare current benchmark result with baseline.

    Args:
        current: Current benchmark result.
        baseline_data: Loaded baseline data.
        tolerance_pct: Acceptable regression tolerance percentage.

    Returns:
        Comparison report with pass/fail status.
    """
    baseline_results = {r["name"]: r for r in baseline_data.get("results", [])}
    baseline = baseline_results.get(current.name)

    if baseline is None:
        return {
            "status": "NEW",
            "message": f"No baseline found for {current.name}",
        }

    baseline_p95 = baseline.get("p95_ms", 0.0)
    current_p95 = current.p95_ms
    regression_pct = ((current_p95 - baseline_p95) / baseline_p95 * 100) if baseline_p95 > 0 else 0.0

    return {
        "status": "PASS" if regression_pct <= tolerance_pct else "FAIL",
        "baseline_p95_ms": round(baseline_p95, 3),
        "current_p95_ms": round(current_p95, 3),
        "regression_pct": round(regression_pct, 2),
        "tolerance_pct": tolerance_pct,
    }


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture
def latency_thresholds() -> type[LatencyThresholds]:
    """Provide latency threshold constants."""
    return LatencyThresholds


@pytest.fixture
def benchmark_collector() -> list[BenchmarkResult]:
    """Collect benchmark results during a test session."""
    return []


@pytest.fixture
def save_benchmarks(benchmark_collector: list[BenchmarkResult]) -> Any:
    """Fixture to save collected benchmarks to a file."""

    def _save(suite_name: str) -> Path:
        import datetime

        suite = BenchmarkSuite(
            suite_name=suite_name,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            results=benchmark_collector,
        )
        return save_baseline(suite)

    return _save
