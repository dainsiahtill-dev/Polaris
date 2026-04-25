"""Unit tests for polaris.kernelone.benchmark.models."""

from __future__ import annotations

from polaris.kernelone.benchmark.models import (
    BenchmarkResult,
    BenchmarkStats,
    LatencyBenchmarkResult,
    MemoryBenchmarkResult,
    MemoryStats,
    ThroughputStats,
)


class TestBenchmarkStats:
    def test_empty_latencies(self) -> None:
        stats = BenchmarkStats()
        stats.compute_statistics()
        assert stats.p50 == 0.0

    def test_basic_percentiles(self) -> None:
        stats = BenchmarkStats(latencies=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        stats.compute_statistics()
        assert stats.p50 == 5.0
        assert stats.p90 == 9.0
        assert stats.p99 == 10.0
        assert stats.mean == 5.5
        assert stats.min_latency == 1.0
        assert stats.max_latency == 10.0

    def test_single_element_std_dev(self) -> None:
        stats = BenchmarkStats(latencies=[5.0])
        stats.compute_statistics()
        assert stats.std_dev == 0.0


class TestMemoryStats:
    def test_to_dict(self) -> None:
        stats = MemoryStats(current_bytes=1024 * 1024, peak_bytes=2 * 1024 * 1024, allocations=5)
        d = stats.to_dict()
        assert d["current_mb"] == 1.0
        assert d["peak_mb"] == 2.0
        assert d["allocations"] == 5


class TestThroughputStats:
    def test_compute(self) -> None:
        stats = ThroughputStats(total_operations=100, duration_ms=1000.0)
        stats.compute()
        assert stats.ops_per_second == 100.0
        assert stats.ops_per_minute == 6000.0
        assert stats.avg_latency_ms == 10.0

    def test_compute_zero_duration(self) -> None:
        stats = ThroughputStats(total_operations=10, duration_ms=0.0)
        stats.compute()
        assert stats.ops_per_second == 0.0

    def test_compute_zero_operations(self) -> None:
        stats = ThroughputStats(total_operations=0, duration_ms=1000.0)
        stats.compute()
        assert stats.avg_latency_ms == 0.0


class TestBenchmarkResult:
    def test_from_stats(self) -> None:
        stats = BenchmarkStats(latencies=[1.0, 2.0, 3.0])
        stats.compute_statistics()
        result = BenchmarkResult.from_stats("latency_test", stats)
        assert result.metric_name == "latency_test"
        assert result.p50_ms == 2.0
        assert result.iterations == 100  # default

    def test_to_dict(self) -> None:
        result = BenchmarkResult(metric_name="m", p50_ms=1.0, iterations=10)
        d = result.to_dict()
        assert d["metric_name"] == "m"
        assert d["iterations"] == 10
        assert isinstance(d["timestamp"], str)


class TestLatencyBenchmarkResult:
    def test_compute_tail_ratio(self) -> None:
        result = LatencyBenchmarkResult(median_ms=10.0, percentile_99_ms=50.0)
        result.compute_tail_ratio()
        assert result.tail_latency_ratio == 5.0

    def test_compute_tail_ratio_zero_median(self) -> None:
        result = LatencyBenchmarkResult(median_ms=0.0, percentile_99_ms=10.0)
        result.compute_tail_ratio()
        assert result.tail_latency_ratio == 0.0


class TestMemoryBenchmarkResult:
    def test_to_dict(self) -> None:
        result = MemoryBenchmarkResult(
            memory_baseline_mb=10.0,
            memory_delta_mb=5.0,
            memory_peak_mb=20.0,
            allocations_count=100,
            deallocations_count=50,
            gc_collections=(1, 0, 0),
        )
        d = result.to_dict()
        assert d["memory_baseline_mb"] == 10.0
        assert d["allocations_count"] == 100
        assert d["gc_collections"] == [1, 0, 0]
