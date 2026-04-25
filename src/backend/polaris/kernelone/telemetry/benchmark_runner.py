"""BenchmarkRunner for performance testing with async support.

Provides a framework for running performance benchmarks with:
- Synchronous and async benchmark support
- Baseline comparison and regression detection
- Statistical analysis (mean, median, stddev, percentiles)
- HTML report generation

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text I/O uses encoding="utf-8"
- Thread-safe: benchmark results protected by locks
"""

from __future__ import annotations

import asyncio
import functools
import html
import json
import logging
import statistics
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# -----------------------------------------------------------------------------
# Benchmark Result Types
# -----------------------------------------------------------------------------


@dataclass
class BenchmarkSample:
    """A single benchmark measurement sample.

    Attributes:
        name: Name of the benchmark.
        duration_seconds: Execution duration in seconds.
        iterations: Number of iterations performed.
        timestamp: When the sample was taken.
        metadata: Additional metadata about the run.
    """

    name: str
    duration_seconds: float
    iterations: int = 1
    timestamp: str = field(default_factory=lambda: _utc_now().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ops_per_second(self) -> float:
        """Calculate operations per second."""
        if self.duration_seconds == 0:
            return 0.0
        return self.iterations / self.duration_seconds

    @property
    def avg_per_op(self) -> float:
        """Average time per operation in milliseconds."""
        if self.iterations == 0:
            return 0.0
        return (self.duration_seconds / self.iterations) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "duration_seconds": self.duration_seconds,
            "iterations": self.iterations,
            "timestamp": self.timestamp,
            "ops_per_second": self.ops_per_second,
            "avg_per_op_ms": self.avg_per_op,
            "metadata": self.metadata,
        }


@dataclass
class BenchmarkResult:
    """Statistical results from a benchmark run.

    Attributes:
        name: Benchmark name.
        samples: List of measurement samples.
        baseline: Optional baseline result for comparison.
        warmup_samples: Number of warmup iterations.
    """

    name: str
    samples: list[BenchmarkSample] = field(default_factory=list)
    baseline: BenchmarkSample | None = None
    warmup_samples: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def add_sample(self, sample: BenchmarkSample) -> None:
        """Add a measurement sample (thread-safe)."""
        with self._lock:
            self.samples.append(sample)

    @property
    def count(self) -> int:
        """Number of samples."""
        with self._lock:
            return len(self.samples)

    def _get_durations(self) -> list[float]:
        """Get list of durations from samples."""
        with self._lock:
            return [s.duration_seconds for s in self.samples]

    @property
    def mean(self) -> float:
        """Mean duration across samples."""
        durations = self._get_durations()
        if not durations:
            return 0.0
        return statistics.mean(durations)

    @property
    def median(self) -> float:
        """Median duration across samples."""
        durations = self._get_durations()
        if not durations:
            return 0.0
        return statistics.median(durations)

    @property
    def stdev(self) -> float:
        """Standard deviation of durations."""
        durations = self._get_durations()
        if len(durations) < 2:
            return 0.0
        return statistics.stdev(durations)

    @property
    def min(self) -> float:
        """Minimum duration."""
        durations = self._get_durations()
        return min(durations) if durations else 0.0

    @property
    def max(self) -> float:
        """Maximum duration."""
        durations = self._get_durations()
        return max(durations) if durations else 0.0

    def percentile(self, p: float) -> float:
        """Calculate percentile (0-100) of durations.

        Args:
            p: Percentile to calculate (0-100).

        Returns:
            Percentile value.
        """
        durations = sorted(self._get_durations())
        if not durations:
            return 0.0
        if p <= 0:
            return durations[0]
        if p >= 100:
            return durations[-1]
        idx = (p / 100) * (len(durations) - 1)
        lower = int(idx)
        upper = lower + 1
        if upper >= len(durations):
            return durations[-1]
        fraction = idx - lower
        return durations[lower] * (1 - fraction) + durations[upper] * fraction

    @property
    def p50(self) -> float:
        """50th percentile (median)."""
        return self.percentile(50)

    @property
    def p95(self) -> float:
        """95th percentile."""
        return self.percentile(95)

    @property
    def p99(self) -> float:
        """99th percentile."""
        return self.percentile(99)

    @property
    def total_duration(self) -> float:
        """Total duration of all samples."""
        return sum(self._get_durations())

    @property
    def regression_detected(self) -> bool:
        """Check if performance regression detected vs baseline."""
        if self.baseline is None:
            return False
        # Consider regression if mean increased by more than 10%
        return self.mean > self.baseline.duration_seconds * 1.1

    @property
    def improvement_ratio(self) -> float | None:
        """Calculate improvement ratio vs baseline.

        Returns:
            Ratio of baseline to current mean (>1 means improvement).
            None if no baseline available.
        """
        if self.baseline is None or self.baseline.duration_seconds == 0:
            return None
        return self.baseline.duration_seconds / self.mean

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        with self._lock:
            return {
                "name": self.name,
                "count": len(self.samples),
                "warmup_samples": self.warmup_samples,
                "mean": self.mean,
                "median": self.median,
                "stdev": self.stdev,
                "min": self.min,
                "max": self.max,
                "p50": self.p50,
                "p95": self.p95,
                "p99": self.p99,
                "total_duration": self.total_duration,
                "regression_detected": self.regression_detected,
                "improvement_ratio": self.improvement_ratio,
                "baseline": self.baseline.to_dict() if self.baseline else None,
                "samples": [
                    {
                        "duration_seconds": s.duration_seconds,
                        "iterations": s.iterations,
                        "timestamp": s.timestamp,
                        "ops_per_second": s.ops_per_second,
                        "avg_per_op_ms": s.avg_per_op,
                    }
                    for s in self.samples
                ],
            }


# -----------------------------------------------------------------------------
# BenchmarkRunner
# -----------------------------------------------------------------------------


class BenchmarkRunner:
    """Performance benchmark runner with async support.

    Provides a framework for running performance tests with:
    - Synchronous and async function support
    - Configurable iterations and warmup
    - Baseline comparison
    - HTML report generation

    Usage::

        runner = BenchmarkRunner()

        # Define a benchmark
        @runner.benchmark(name="json_parsing", iterations=100, warmup=10)
        def parse_json():
            return json.loads('{"key": "value"}')

        # Run all benchmarks
        results = runner.run_all()

        # Generate HTML report
        report_path = runner.generate_html_report(results)
    """

    def __init__(self) -> None:
        self._benchmarks: list[tuple[str, Callable[..., Any], dict[str, Any]]] = []
        self._results: dict[str, BenchmarkResult] = {}
        self._baselines: dict[str, BenchmarkSample] = {}
        self._lock = threading.Lock()

    def benchmark(
        self,
        name: str | None = None,
        iterations: int = 10,
        warmup: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Callable[[F], F]:
        """Decorator to register a function as a benchmark.

        Args:
            name: Benchmark name. Defaults to function name.
            iterations: Number of iterations to run.
            warmup: Number of warmup iterations.
            metadata: Additional metadata to store.

        Returns:
            Decorated function (unchanged).
        """

        def decorator(func: F) -> F:
            nonlocal name
            if name is None:
                name = func.__name__
            with self._lock:
                self._benchmarks.append(
                    (name, func, {"iterations": iterations, "warmup": warmup, "metadata": metadata or {}})
                )
            return func

        return decorator

    def register(
        self,
        name: str,
        func: Callable[..., Any],
        iterations: int = 10,
        warmup: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a benchmark function programmatically.

        Args:
            name: Benchmark name.
            func: Function to benchmark.
            iterations: Number of iterations.
            warmup: Number of warmup iterations.
            metadata: Additional metadata.
        """
        with self._lock:
            self._benchmarks.append(
                (name, func, {"iterations": iterations, "warmup": warmup, "metadata": metadata or {}})
            )

    def set_baseline(self, name: str, sample: BenchmarkSample) -> None:
        """Set a baseline sample for comparison.

        Args:
            name: Benchmark name.
            sample: Baseline sample data.
        """
        with self._lock:
            self._baselines[name] = sample

    def _run_sync_benchmark(
        self,
        name: str,
        func: Callable[..., Any],
        iterations: int,
        warmup: int,
        metadata: dict[str, Any],
    ) -> BenchmarkResult:
        """Run a synchronous benchmark."""
        result = BenchmarkResult(name=name)
        if name in self._baselines:
            result.baseline = self._baselines[name]

        # Warmup phase
        for _ in range(warmup):
            try:
                func()
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.warning("Benchmark %s warmup error: %s", name, e)

        # Measurement phase
        for i in range(iterations):
            start = time.perf_counter()
            try:
                func()
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.warning("Benchmark %s iteration %d error: %s", name, i, e)
                continue
            duration = time.perf_counter() - start
            result.add_sample(
                BenchmarkSample(
                    name=name,
                    duration_seconds=duration,
                    iterations=1,
                    metadata=metadata,
                )
            )

        result.warmup_samples = warmup
        return result

    async def _run_async_benchmark(
        self,
        name: str,
        func: Callable[..., Any],
        iterations: int,
        warmup: int,
        metadata: dict[str, Any],
    ) -> BenchmarkResult:
        """Run an async benchmark."""
        result = BenchmarkResult(name=name)
        if name in self._baselines:
            result.baseline = self._baselines[name]

        # Warmup phase
        for _ in range(warmup):
            try:
                if asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.warning("Benchmark %s warmup error: %s", name, e)

        # Measurement phase
        for i in range(iterations):
            start = time.perf_counter()
            try:
                if asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.warning("Benchmark %s iteration %d error: %s", name, i, e)
                continue
            duration = time.perf_counter() - start
            result.add_sample(
                BenchmarkSample(
                    name=name,
                    duration_seconds=duration,
                    iterations=1,
                    metadata=metadata,
                )
            )

        result.warmup_samples = warmup
        return result

    def run(self, name: str) -> BenchmarkResult | None:
        """Run a specific benchmark by name.

        Args:
            name: Benchmark name.

        Returns:
            Benchmark result or None if not found.
        """
        with self._lock:
            benchmarks_copy = list(self._benchmarks)

        for bench_name, func, params in benchmarks_copy:
            if bench_name == name:
                if asyncio.iscoroutinefunction(func):
                    return asyncio.run(
                        self._run_async_benchmark(
                            bench_name, func, params["iterations"], params["warmup"], params["metadata"]
                        )
                    )
                return self._run_sync_benchmark(
                    bench_name, func, params["iterations"], params["warmup"], params["metadata"]
                )
        return None

    def run_all(self) -> dict[str, BenchmarkResult]:
        """Run all registered benchmarks.

        Returns:
            Dictionary of results keyed by benchmark name.
        """
        results: dict[str, BenchmarkResult] = {}

        with self._lock:
            benchmarks_copy = list(self._benchmarks)

        for bench_name, func, params in benchmarks_copy:
            logger.info("Running benchmark: %s", bench_name)
            if asyncio.iscoroutinefunction(func):
                result = asyncio.run(
                    self._run_async_benchmark(
                        bench_name, func, params["iterations"], params["warmup"], params["metadata"]
                    )
                )
            else:
                result = self._run_sync_benchmark(
                    bench_name, func, params["iterations"], params["warmup"], params["metadata"]
                )
            results[bench_name] = result
            self._results[bench_name] = result

        return results

    async def run_all_async(self) -> dict[str, BenchmarkResult]:
        """Run all registered benchmarks asynchronously.

        Returns:
            Dictionary of results keyed by benchmark name.
        """
        results: dict[str, BenchmarkResult] = {}

        with self._lock:
            benchmarks_copy = list(self._benchmarks)

        for bench_name, func, params in benchmarks_copy:
            logger.info("Running benchmark: %s", bench_name)
            if asyncio.iscoroutinefunction(func):
                result = await self._run_async_benchmark(
                    bench_name, func, params["iterations"], params["warmup"], params["metadata"]
                )
            else:
                # Capture loop variables via partial to avoid late binding issues
                loop = asyncio.get_event_loop()
                sync_func = functools.partial(
                    self._run_sync_benchmark,
                    bench_name,
                    func,
                    params["iterations"],
                    params["warmup"],
                    params["metadata"],
                )
                result = await loop.run_in_executor(None, sync_func)
            results[bench_name] = result
            self._results[bench_name] = result

        return results

    def get_result(self, name: str) -> BenchmarkResult | None:
        """Get cached result for a benchmark.

        Args:
            name: Benchmark name.

        Returns:
            Cached result or None.
        """
        with self._lock:
            return self._results.get(name)

    def get_all_results(self) -> dict[str, BenchmarkResult]:
        """Get all cached results."""
        with self._lock:
            return dict(self._results)

    def generate_html_report(
        self,
        results: dict[str, BenchmarkResult] | None = None,
        output_path: str | Path | None = None,
        title: str = "Performance Benchmark Report",
    ) -> Path:
        """Generate an HTML report from benchmark results.

        Args:
            results: Results to include. Defaults to all cached results.
            output_path: Output file path. If None, creates temp file.
            title: Report title.

        Returns:
            Path to generated report.
        """
        if results is None:
            results = self.get_all_results()

        if output_path is None:
            # Replace colons with underscores for Windows compatibility
            timestamp_str = _utc_now().isoformat().replace(":", "-").replace("+", "-")
            output_path = Path(tempfile.gettempdir()) / f"benchmark_report_{timestamp_str}.html"
        output_path = Path(output_path)

        html_content = self._build_html_report(results, title)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("Generated benchmark report: %s", output_path)
        return output_path

    def _build_html_report(self, results: dict[str, BenchmarkResult], title: str) -> str:
        """Build HTML report content."""
        timestamp = _utc_now().isoformat()

        # Build summary table rows
        summary_rows = []
        for name, result in sorted(results.items()):
            regression_class = ""
            if result.regression_detected:
                regression_class = 'class="regression"'
            elif result.improvement_ratio and result.improvement_ratio > 1.1:
                regression_class = 'class="improvement"'

            summary_rows.append(f"""
                <tr {regression_class}>
                    <td>{html.escape(name)}</td>
                    <td>{result.count}</td>
                    <td>{result.mean * 1000:.3f} ms</td>
                    <td>{result.median * 1000:.3f} ms</td>
                    <td>{result.stdev * 1000:.3f} ms</td>
                    <td>{result.p50 * 1000:.3f} ms</td>
                    <td>{result.p95 * 1000:.3f} ms</td>
                    <td>{result.p99 * 1000:.3f} ms</td>
                    <td>{"YES" if result.regression_detected else "No"}</td>
                </tr>
            """)

        # Build detail sections
        detail_sections = []
        for name, result in sorted(results.items()):
            sample_rows = []
            for i, sample in enumerate(result.samples[:20], 1):  # Limit to 20 samples per table
                sample_rows.append(f"""
                    <tr>
                        <td>{i}</td>
                        <td>{sample.duration_seconds * 1000:.3f} ms</td>
                        <td>{sample.ops_per_second:.2f} ops/s</td>
                        <td>{sample.avg_per_op:.3f} ms/op</td>
                    </tr>
                """)

            baseline_info = ""
            if result.baseline:
                improvement = result.improvement_ratio if result.improvement_ratio is not None else 0.0
                baseline_info = f"""
                    <div class="baseline-info">
                        <h4>Baseline</h4>
                        <p>Duration: {result.baseline.duration_seconds * 1000:.3f} ms</p>
                        <p>Improvement Ratio: {improvement:.2f}x</p>
                    </div>
                """

            detail_sections.append(f"""
                <div class="benchmark-detail">
                    <h3>{html.escape(name)}</h3>
                    {baseline_info}
                    <table class="sample-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Duration</th>
                                <th>Ops/sec</th>
                                <th>Avg/op</th>
                            </tr>
                        </thead>
                        <tbody>
                            {"".join(sample_rows)}
                        </tbody>
                    </table>
                </div>
            """)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 10px;
        }}
        .meta {{
            color: #666;
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background: #007bff;
            color: white;
        }}
        tr:hover {{
            background: #f8f9fa;
        }}
        .regression {{
            background: #ffe6e6 !important;
            color: #dc3545;
        }}
        .improvement {{
            background: #e6ffe6 !important;
            color: #28a745;
        }}
        .benchmark-detail {{
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .baseline-info {{
            background: #e7f3ff;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 10px;
        }}
        .sample-table {{
            font-size: 0.9em;
        }}
        .sample-table th {{
            background: #6c757d;
        }}
    </style>
</head>
<body>
    <h1>{html.escape(title)}</h1>
    <div class="meta">
        <p>Generated: {html.escape(timestamp)}</p>
        <p>Total benchmarks: {len(results)}</p>
    </div>

    <h2>Summary</h2>
    <table>
        <thead>
            <tr>
                <th>Benchmark</th>
                <th>Samples</th>
                <th>Mean</th>
                <th>Median</th>
                <th>Stdev</th>
                <th>p50</th>
                <th>p95</th>
                <th>p99</th>
                <th>Regression</th>
            </tr>
        </thead>
        <tbody>
            {"".join(summary_rows)}
        </tbody>
    </table>

    <h2>Details</h2>
    {"".join(detail_sections)}
</body>
</html>
"""

    def export_json(self, results: dict[str, BenchmarkResult] | None = None) -> str:
        """Export results as JSON.

        Args:
            results: Results to export. Defaults to all cached.

        Returns:
            JSON string representation.
        """
        if results is None:
            results = self.get_all_results()
        return json.dumps({name: r.to_dict() for name, r in results.items()}, indent=2)

    def reset(self) -> None:
        """Clear all cached results and baselines."""
        with self._lock:
            self._results.clear()
            self._baselines.clear()


# -----------------------------------------------------------------------------
# Convenience Functions
# -----------------------------------------------------------------------------

_default_runner: BenchmarkRunner | None = None


def get_runner() -> BenchmarkRunner:
    """Get the default global BenchmarkRunner instance.

    Returns:
        Global BenchmarkRunner singleton.
    """
    global _default_runner
    if _default_runner is None:
        _default_runner = BenchmarkRunner()
    return _default_runner


def benchmark(
    name: str | None = None,
    iterations: int = 10,
    warmup: int = 0,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Decorator shortcut using default runner.

    Args:
        name: Benchmark name.
        iterations: Number of iterations.
        warmup: Warmup iterations.
        metadata: Additional metadata.

    Returns:
        Decorated function.
    """
    return get_runner().benchmark(name, iterations, warmup, metadata)
