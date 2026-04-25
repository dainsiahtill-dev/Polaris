"""Tests for polaris.kernelone.benchmark.models."""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.kernelone.benchmark.models import (
    BenchmarkResult,
    BenchmarkStats,
    LatencyBenchmarkResult,
    MemoryBenchmarkResult,
    MemoryStats,
    ThroughputStats,
)


class TestBenchmarkStats:
    def test_defaults(self) -> None:
        stats = BenchmarkStats()
        assert stats.latencies == []
        assert stats.warmup_count == 3
        assert stats.iterations == 100

    def test_compute_statistics(self) -> None:
        stats = BenchmarkStats(latencies=[1.0, 2.0, 3.0, 4.0, 5.0])
        stats.compute_statistics()
        assert stats.p50 == 3.0
        assert stats.p90 == 5.0
        assert stats.p99 == 5.0
        assert stats.mean == 3.0
        assert stats.min_latency == 1.0
        assert stats.max_latency == 5.0
        assert stats.std_dev > 0

    def test_compute_statistics_empty(self) -> None:
        stats = BenchmarkStats()
        stats.compute_statistics()
        assert stats.p50 == 0.0
        assert stats.mean == 0.0

    def test_compute_statistics_single_value(self) -> None:
        stats = BenchmarkStats(latencies=[5.0])
        stats.compute_statistics()
        assert stats.std_dev == 0.0


class TestMemoryStats:
    def test_to_dict(self) -> None:
        stats = MemoryStats(current_bytes=1024, peak_bytes=2048, current_mb=1.0, peak_mb=2.0, allocations=10)
        d = stats.to_dict()
        assert d["current_bytes"] == 1024
        assert d["peak_bytes"] == 2048
        assert d["current_mb"] == 1.0
        assert d["peak_mb"] == 2.0
        assert d["allocations"] == 10


class TestThroughputStats:
    def test_compute(self) -> None:
        stats = ThroughputStats(total_operations=100, duration_ms=1000.0)
        stats.compute()
        assert stats.ops_per_second == 100.0
        assert stats.ops_per_minute == 6000.0
        assert stats.avg_latency_ms == 10.0

    def test_compute_zero_duration(self) -> None:
        stats = ThroughputStats(total_operations=100, duration_ms=0.0)
        stats.compute()
        assert stats.ops_per_second == 0.0
        assert stats.avg_latency_ms == 0.0

    def test_compute_zero_operations(self) -> None:
        stats = ThroughputStats(total_operations=0, duration_ms=1000.0)
        stats.compute()
        assert stats.ops_per_second == 0.0
        assert stats.avg_latency_ms == 0.0


class TestBenchmarkResult:
    def test_defaults(self) -> None:
        result = BenchmarkResult(metric_name="test")
        assert result.metric_name == "test"
        assert result.p50_ms == 0.0
        assert result.timestamp != ""

    def test_from_stats(self) -> None:
        stats = BenchmarkStats(latencies=[1.0, 2.0, 3.0])
        stats.compute_statistics()
        result = BenchmarkResult.from_stats("my_metric", stats)
        assert result.metric_name == "my_metric"
        assert result.p50_ms == 2.0
        assert result.iterations == 100  # default
        assert result.warmup_count == 3  # default

    def test_to_dict(self) -> None:
        result = BenchmarkResult(
            metric_name="test",
            p50_ms=1.0,
            p90_ms=2.0,
            p99_ms=3.0,
            mean_ms=2.0,
            std_dev_ms=0.5,
            min_ms=1.0,
            max_ms=3.0,
            iterations=10,
            warmup_count=1,
        )
        d = result.to_dict()
        assert d["metric_name"] == "test"
        assert d["p50_ms"] == 1.0
        assert d["iterations"] == 10
        assert d["warmup_count"] == 1
        assert "timestamp" in d


class TestLatencyBenchmarkResult:
    def test_defaults(self) -> None:
        result = LatencyBenchmarkResult()
        assert result.metric_name == ""
        assert result.tail_latency_ratio == 0.0

    def test_compute_tail_ratio(self) -> None:
        result = LatencyBenchmarkResult(median_ms=10.0, percentile_99_ms=50.0)
        result.compute_tail_ratio()
        assert result.tail_latency_ratio == 5.0

    def test_compute_tail_ratio_zero_median(self) -> None:
        result = LatencyBenchmarkResult(median_ms=0.0, percentile_99_ms=50.0)
        result.compute_tail_ratio()
        assert result.tail_latency_ratio == 0.0


class TestMemoryBenchmarkResult:
    def test_defaults(self) -> None:
        result = MemoryBenchmarkResult()
        assert result.memory_baseline_mb == 0.0
        assert result.memory_delta_mb == 0.0
        assert result.allocations_count == 0
        assert result.gc_collections == ()

    def test_to_dict(self) -> None:
        result = MemoryBenchmarkResult(
            memory_baseline_mb=1.0,
            memory_delta_mb=2.0,
            memory_peak_mb=5.0,
            allocations_count=100,
            deallocations_count=50,
            gc_collections=(1, 0, 0),
        )
        d = result.to_dict()
        assert d["memory_baseline_mb"] == 1.0
        assert d["memory_delta_mb"] == 2.0
        assert d["memory_peak_mb"] == 5.0
        assert d["allocations_count"] == 100
        assert d["deallocations_count"] == 50
        assert d["gc_collections"] == (1, 0, 0)
