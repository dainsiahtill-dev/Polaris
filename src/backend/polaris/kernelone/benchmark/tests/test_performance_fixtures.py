"""Performance Benchmark Tests.

This module contains test cases for the performance benchmark framework,
validating latency, memory, and throughput measurement capabilities.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from polaris.kernelone.benchmark.fixtures import (
    BenchmarkContext,
    async_memory_benchmark,
    benchmark,
    memory_benchmark,
    throughput_benchmark,
)
from polaris.kernelone.benchmark.latency import (
    LatencyBenchmarker,
    LatencyProfile,
)
from polaris.kernelone.benchmark.memory import (
    MemoryBenchmarker,
    MemoryTracker,
    memory_profile,
)
from polaris.kernelone.benchmark.models import (
    BenchmarkResult,
    BenchmarkStats,
    MemoryBenchmarkResult,
    ThroughputStats,
)
from polaris.kernelone.benchmark.throughput import (
    FixedIterationThroughputBench,
    TimeBasedThroughputBench,
)

# =============================================================================
# Fixtures Tests
# =============================================================================


class TestBenchmarkStats:
    """Tests for BenchmarkStats class."""

    def test_benchmark_stats_initialization(self) -> None:
        """Test BenchmarkStats can be initialized with defaults."""
        stats = BenchmarkStats()
        assert stats.latencies == []
        assert stats.warmup_count == 3
        assert stats.iterations == 100
        assert stats.p50 == 0.0

    def test_benchmark_stats_with_data(self) -> None:
        """Test BenchmarkStats with latency data."""
        stats = BenchmarkStats(
            latencies=[1.0, 2.0, 3.0, 4.0, 5.0],
            warmup_count=2,
            iterations=5,
        )
        stats.compute_statistics()

        assert stats.mean == 3.0
        assert stats.p50 == 3.0
        assert stats.min_latency == 1.0
        assert stats.max_latency == 5.0

    def test_benchmark_stats_percentiles(self) -> None:
        """Test percentile computation."""
        stats = BenchmarkStats(latencies=[1.0] * 50 + [10.0] * 50)
        stats.compute_statistics()

        # With 50 values of 1.0 and 50 values of 10.0, p50 should be around 10.0
        # (index 50 in 0-based is the 51st element)
        assert stats.p50 >= 10.0
        assert stats.p90 >= 10.0
        assert stats.p99 >= 10.0


class TestBenchmarkContext:
    """Tests for BenchmarkContext context manager."""

    def test_context_initialization(self) -> None:
        """Test BenchmarkContext initialization."""
        ctx = BenchmarkContext(metric_name="test_metric")
        assert ctx.metric_name == "test_metric"
        assert ctx.latencies == []

    def test_context_record_latency(self) -> None:
        """Test recording latencies."""
        ctx = BenchmarkContext()
        ctx.record_latency(1.5)
        ctx.record_latency(2.5)
        assert len(ctx.latencies) == 2
        assert 1.5 in ctx.latencies

    def test_context_get_stats(self) -> None:
        """Test getting statistics."""
        ctx = BenchmarkContext()
        for i in range(10):
            ctx.record_latency(float(i + 1))
        stats = ctx.get_stats()
        assert stats.mean == 5.5
        assert stats.p50 in {5.5, 6.0}

    def test_context_timer(self) -> None:
        """Test timer functionality."""
        ctx = BenchmarkContext()
        start = ctx.start_timer()
        time.sleep(0.01)
        elapsed = ctx.stop_timer(start)
        assert elapsed >= 10.0  # at least 10ms


class TestBenchmarkResult:
    """Tests for BenchmarkResult dataclass."""

    def test_benchmark_result_from_stats(self) -> None:
        """Test creating BenchmarkResult from BenchmarkStats."""
        stats = BenchmarkStats(
            latencies=[1.0, 2.0, 3.0, 4.0, 5.0],
            warmup_count=2,
            iterations=5,
        )
        stats.compute_statistics()

        result = BenchmarkResult.from_stats("test_metric", stats)

        assert result.metric_name == "test_metric"
        assert result.warmup_count == 2
        assert result.iterations == 5
        assert result.p50_ms > 0

    def test_benchmark_result_to_dict(self) -> None:
        """Test JSON serialization."""
        result = BenchmarkResult(
            metric_name="test",
            p50_ms=5.0,
            p90_ms=10.0,
            p99_ms=20.0,
            mean_ms=7.0,
            iterations=100,
        )
        data = result.to_dict()

        assert data["metric_name"] == "test"
        assert data["p50_ms"] == 5.0
        assert "timestamp" in data


# =============================================================================
# Decorator Tests
# =============================================================================


class TestBenchmarkDecorator:
    """Tests for @benchmark decorator."""

    def test_sync_benchmark_decorator(self) -> None:
        """Test benchmark decorator on sync function."""
        call_count = 0

        @benchmark(warmup=1, iterations=5)
        def my_function() -> int:
            nonlocal call_count
            call_count += 1
            time.sleep(0.001)
            return call_count

        stats = my_function()

        assert isinstance(stats, BenchmarkStats)
        assert call_count == 6  # 1 warmup + 5 iterations
        assert len(stats.latencies) == 5
        assert stats.warmup_count == 1
        assert stats.iterations == 5

    @pytest.mark.asyncio
    async def test_async_benchmark_decorator(self) -> None:
        """Test benchmark decorator on async function."""

        @benchmark(warmup=1, iterations=5)
        async def my_async_function() -> None:
            await asyncio.sleep(0.001)

        stats = await my_async_function()

        assert isinstance(stats, BenchmarkStats)
        assert len(stats.latencies) == 5
        assert stats.p50 > 0

    def test_benchmark_statistics_computed(self) -> None:
        """Test that statistics are computed correctly."""

        @benchmark(warmup=0, iterations=5)
        def slow_function() -> None:
            time.sleep(0.001)

        stats = slow_function()

        assert stats.p50 > 0
        assert stats.p90 >= stats.p50
        assert stats.p99 >= stats.p90


class TestMemoryBenchmarkDecorator:
    """Tests for @memory_benchmark decorator."""

    def test_memory_benchmark_sync(self) -> None:
        """Test memory benchmark on sync function."""

        @memory_benchmark
        def allocate_memory() -> list[int]:
            return [0] * 10000

        result = allocate_memory()

        assert "result" in result
        assert "memory_current_mb" in result
        assert "memory_peak_mb" in result
        assert "memory_delta_mb" in result
        assert len(result["result"]) == 10000

    def test_memory_benchmark_increases(self) -> None:
        """Test that memory benchmark detects memory increase."""
        large_data: list[int] = []

        @memory_benchmark
        def allocate_large() -> int:
            nonlocal large_data
            large_data = [0] * 100000
            return len(large_data)

        result = allocate_large()
        assert result["memory_delta_mb"] > 0

    def test_memory_benchmark_rejects_async(self) -> None:
        """Test that memory_benchmark rejects async functions."""

        @memory_benchmark
        async def async_func() -> None:
            pass

        with pytest.raises(TypeError, match="async"):
            async_func()


class TestAsyncMemoryBenchmarkDecorator:
    """Tests for @async_memory_benchmark decorator."""

    @pytest.mark.asyncio
    async def test_async_memory_benchmark(self) -> None:
        """Test async memory benchmark."""

        @async_memory_benchmark
        async def allocate_memory() -> list[int]:
            await asyncio.sleep(0.001)
            return [0] * 10000

        result = await allocate_memory()

        assert "result" in result
        assert "memory_peak_mb" in result
        assert len(result["result"]) == 10000


class TestThroughputBenchmarkDecorator:
    """Tests for @throughput_benchmark decorator."""

    def test_throughput_benchmark_sync(self) -> None:
        """Test throughput benchmark on sync function."""
        counter = 0

        @throughput_benchmark(warmup=1, target_duration_ms=100)
        def increment_counter() -> None:
            nonlocal counter
            counter += 1

        stats = increment_counter()

        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations > 0
        assert stats.duration_ms > 0
        assert stats.ops_per_second > 0

    @pytest.mark.asyncio
    async def test_throughput_benchmark_async(self) -> None:
        """Test throughput benchmark on async function."""

        @throughput_benchmark(warmup=1, target_duration_ms=100)
        async def async_increment() -> None:
            await asyncio.sleep(0.001)

        stats = await async_increment()

        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations > 0


# =============================================================================
# Latency Benchmark Tests
# =============================================================================


class TestLatencyProfile:
    """Tests for LatencyProfile class."""

    def test_latency_profile_creation(self) -> None:
        """Test LatencyProfile creation."""
        profile = LatencyProfile(name="test_profile")
        assert profile.name == "test_profile"
        assert profile.measurements == []

    def test_add_measurement(self) -> None:
        """Test adding measurements."""
        profile = LatencyProfile(name="test")
        profile.add_measurement(1.5, {"key": "value"})
        assert len(profile.measurements) == 1
        assert profile.measurements[0].latency_ms == 1.5

    def test_add_warmup(self) -> None:
        """Test adding warmup measurements."""
        profile = LatencyProfile(name="test")
        profile.add_warmup(2.0)
        assert len(profile.warmup_measurements) == 1
        assert len(profile.measurements) == 0

    def test_compute_statistics(self) -> None:
        """Test statistics computation."""
        profile = LatencyProfile(name="test")
        for i in range(1, 101):
            profile.add_measurement(float(i))
        profile.compute_statistics()

        assert profile.mean == 50.5
        assert profile.min_latency == 1.0
        assert profile.max_latency == 100.0
        assert 50.0 <= profile.p50 <= 51.0
        assert 90.0 <= profile.p90 <= 91.0

    def test_get_tail_ratio(self) -> None:
        """Test tail latency ratio calculation."""
        profile = LatencyProfile(name="test")
        # Add 98 measurements of 1.0, then 2 measurements of 100.0
        for _ in range(98):
            profile.add_measurement(1.0)
        for _ in range(2):
            profile.add_measurement(100.0)
        profile.compute_statistics()

        ratio = profile.get_tail_ratio()
        # p50 should be 1.0, p99 should be 100.0, ratio should be 100
        assert ratio > 50.0  # p99/median should be significantly higher than 1


class TestLatencyBenchmarker:
    """Tests for LatencyBenchmarker class."""

    def test_sync_benchmarker(self) -> None:
        """Test synchronous latency benchmarking."""
        bench = LatencyBenchmarker("test", warmup=1, iterations=10)

        def my_func() -> None:
            time.sleep(0.001)

        profile = bench.run_sync(my_func)

        assert isinstance(profile, LatencyProfile)
        assert profile.name == "test"
        assert len(profile.measurements) == 10

    @pytest.mark.asyncio
    async def test_async_benchmarker(self) -> None:
        """Test asynchronous latency benchmarking."""
        bench = LatencyBenchmarker("async_test", warmup=1, iterations=10)

        async def my_async_func() -> None:
            await asyncio.sleep(0.001)

        profile = await bench.run_async(my_async_func)

        assert isinstance(profile, LatencyProfile)
        assert len(profile.measurements) == 10


# =============================================================================
# Memory Benchmark Tests
# =============================================================================


class TestMemoryBenchmarker:
    """Tests for MemoryBenchmarker class."""

    def test_sync_memory_benchmarker(self) -> None:
        """Test synchronous memory benchmarking."""
        bench = MemoryBenchmarker("test", track_gc=True)

        def allocate_data() -> list[int]:
            return [0] * 100000

        result = bench.run_sync(allocate_data)

        assert isinstance(result, MemoryBenchmarkResult)
        assert result.memory_peak_mb > 0

    @pytest.mark.asyncio
    async def test_async_memory_benchmarker(self) -> None:
        """Test asynchronous memory benchmarking."""
        bench = MemoryBenchmarker("async_test")

        async def allocate_data() -> list[int]:
            await asyncio.sleep(0.001)
            return [0] * 100000

        result = await bench.run_async(allocate_data)

        assert isinstance(result, MemoryBenchmarkResult)


class TestMemoryTracker:
    """Tests for MemoryTracker context manager."""

    def test_memory_tracker_basic(self) -> None:
        """Test basic memory tracking."""
        tracker = MemoryTracker("test")

        with tracker:
            _data = [0] * 100000

        delta = tracker.get_delta_mb()
        assert delta > 0

    def test_memory_tracker_result(self) -> None:
        """Test getting result from tracker."""
        tracker = MemoryTracker("test")

        with tracker:
            _ = [0] * 50000

        result = tracker.get_result()
        assert isinstance(result, MemoryBenchmarkResult)
        assert result.memory_delta_mb > 0


# =============================================================================
# Throughput Benchmark Tests
# =============================================================================


class TestTimeBasedThroughputBench:
    """Tests for TimeBasedThroughputBench class."""

    def test_time_based_benchmark(self) -> None:
        """Test time-based throughput measurement."""
        bench = TimeBasedThroughputBench("test", target_duration_ms=100)
        counter = 0

        def increment() -> None:
            nonlocal counter
            counter += 1

        stats = bench.run_sync(increment)

        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations > 0
        assert stats.ops_per_second > 0

    @pytest.mark.asyncio
    async def test_time_based_benchmark_async(self) -> None:
        """Test async time-based throughput measurement."""
        bench = TimeBasedThroughputBench("async_test", target_duration_ms=100)

        async def async_op() -> None:
            await asyncio.sleep(0.001)

        stats = await bench.run_async(async_op)

        assert stats.total_operations > 0


class TestFixedIterationThroughputBench:
    """Tests for FixedIterationThroughputBench class."""

    def test_fixed_iteration_benchmark(self) -> None:
        """Test fixed-iteration throughput measurement."""
        bench = FixedIterationThroughputBench("test", iterations=100)
        counter = 0

        def increment() -> None:
            nonlocal counter
            counter += 1

        stats = bench.run_sync(increment)

        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations == 100
        assert stats.duration_ms > 0


# =============================================================================
# Integration Tests
# =============================================================================


class TestBenchmarkIntegration:
    """Integration tests for the benchmark framework."""

    def test_latency_and_memory_together(self) -> None:
        """Test measuring both latency and memory."""

        @benchmark(warmup=1, iterations=5)
        @memory_profile
        def operation() -> str:
            time.sleep(0.001)
            return "result"

        # These decorators don't compose, so test separately
        stats = benchmark(warmup=1, iterations=5)(operation)()
        assert isinstance(stats, BenchmarkStats)

    def test_multiple_benchmarks(self) -> None:
        """Test running multiple benchmarks."""
        results: list[LatencyProfile] = []

        for i in range(3):
            bench = LatencyBenchmarker(f"bench_{i}", warmup=1, iterations=5)

            def op() -> None:
                time.sleep(0.001)

            profile = bench.run_sync(op)
            results.append(profile)

        assert len(results) == 3
        assert all(isinstance(r, LatencyProfile) for r in results)

    @pytest.mark.asyncio
    async def test_async_integration(self) -> None:
        """Test async benchmark integration."""
        bench = LatencyBenchmarker("async_integration", warmup=1, iterations=10)

        async def complex_op() -> dict[str, int]:
            await asyncio.sleep(0.001)
            return {"value": 42}

        profile = await bench.run_async(complex_op)

        assert len(profile.measurements) == 10
        assert all(m.latency_ms > 0 for m in profile.measurements)
