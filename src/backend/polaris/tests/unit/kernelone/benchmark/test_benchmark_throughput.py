"""Tests for polaris.kernelone.benchmark.throughput."""

from __future__ import annotations

import asyncio

import pytest

from polaris.kernelone.benchmark.models import ThroughputStats
from polaris.kernelone.benchmark.throughput import (
    FixedIterationThroughputBench,
    ThroughputBenchmarker,
    ThroughputMeasurement,
    ThroughputProfile,
    TimeBasedThroughputBench,
    throughput,
)


class TestThroughputMeasurement:
    def test_post_init(self) -> None:
        m = ThroughputMeasurement(operations=100, duration_ms=1000.0)
        assert m.ops_per_second == 100.0
        assert m.ops_per_minute == 6000.0
        assert m.avg_latency_ms == 10.0

    def test_post_init_zero_duration(self) -> None:
        m = ThroughputMeasurement(operations=100, duration_ms=0.0)
        assert m.ops_per_second == 0.0
        assert m.avg_latency_ms == 0.0

    def test_post_init_zero_operations(self) -> None:
        m = ThroughputMeasurement(operations=0, duration_ms=1000.0)
        assert m.ops_per_second == 0.0
        assert m.avg_latency_ms == 0.0


class TestThroughputProfile:
    def test_add_measurement(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        assert len(profile.measurements) == 1
        assert profile.total_operations == 100

    def test_add_multiple(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        profile.add_measurement(operations=200, duration_ms=1000.0)
        assert profile.total_operations == 300
        assert profile.avg_ops_per_second == 150.0
        assert profile.min_ops_per_second == 100.0
        assert profile.max_ops_per_second == 200.0

    def test_to_stats(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        stats = profile.to_stats()
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations == 100

    def test_get_throughput_stability(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        profile.add_measurement(operations=200, duration_ms=1000.0)
        stability = profile.get_throughput_stability()
        assert stability > 0

    def test_get_throughput_stability_zero(self) -> None:
        profile = ThroughputProfile(name="test")
        assert profile.get_throughput_stability() == 0.0


class TestThroughputBenchmarker:
    def test_init(self) -> None:
        bench = ThroughputBenchmarker("test", warmup=3, gc_between_runs=True)
        assert bench.name == "test"
        assert bench.warmup == 3
        assert bench.gc_between_runs is True


class TestTimeBasedThroughputBench:
    def test_run_sync(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=1)

        def op() -> None:
            pass

        stats = bench.run_sync(op)
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations > 0
        assert stats.duration_ms >= 50.0

    def test_run_sync_rejects_async(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=0)

        async def async_op() -> None:
            pass

        with pytest.raises(TypeError, match="run_async"):
            bench.run_sync(async_op)

    def test_run_async(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=1)

        async def op() -> None:
            pass

        stats = asyncio.get_event_loop().run_until_complete(bench.run_async(op))
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations > 0

    def test_run_async_rejects_sync(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=0)

        def sync_op() -> None:
            pass

        with pytest.raises(TypeError, match="run_sync"):
            asyncio.get_event_loop().run_until_complete(bench.run_async(sync_op))

    def test_gc_between_runs(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=1, gc_between_runs=True)

        def op() -> None:
            pass

        stats = bench.run_sync(op)
        assert stats.total_operations >= 0


class TestFixedIterationThroughputBench:
    def test_run_sync(self) -> None:
        bench = FixedIterationThroughputBench("test", iterations=10, warmup=1)

        def op() -> None:
            pass

        stats = bench.run_sync(op)
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations == 10

    def test_run_async(self) -> None:
        bench = FixedIterationThroughputBench("test", iterations=10, warmup=1)

        async def op() -> None:
            pass

        stats = asyncio.get_event_loop().run_until_complete(bench.run_async(op))
        assert stats.total_operations == 10


class TestThroughputDecorator:
    def test_sync_function(self) -> None:
        @throughput
        def process() -> None:
            pass

        stats = process(target_duration_ms=50.0)
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations >= 0

    def test_async_function(self) -> None:
        @throughput
        async def process() -> None:
            pass

        stats = process(target_duration_ms=50.0)
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations >= 0
