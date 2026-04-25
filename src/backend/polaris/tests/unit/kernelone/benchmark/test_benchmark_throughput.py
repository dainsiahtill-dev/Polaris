"""Unit tests for polaris.kernelone.benchmark.throughput."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.benchmark.throughput import (
    FixedIterationThroughputBench,
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

    def test_zero_duration(self) -> None:
        m = ThroughputMeasurement(operations=10, duration_ms=0.0)
        assert m.ops_per_second == 0.0


class TestThroughputProfile:
    def test_add_measurement(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        assert len(profile.measurements) == 1
        assert profile.total_operations == 100

    def test_get_throughput_stability(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        profile.add_measurement(operations=200, duration_ms=1000.0)
        stability = profile.get_throughput_stability()
        assert stability > 0.0

    def test_to_stats(self) -> None:
        profile = ThroughputProfile(name="test")
        profile.add_measurement(operations=100, duration_ms=1000.0)
        stats = profile.to_stats()
        assert stats.total_operations == 100
        assert stats.ops_per_second == 100.0


class TestTimeBasedThroughputBench:
    def test_run_sync(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=1)

        def dummy() -> None:
            pass

        stats = bench.run_sync(dummy)
        assert stats.total_operations > 0
        assert stats.duration_ms >= 50.0

    def test_run_sync_rejects_async(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0)

        async def dummy() -> None:
            pass

        with pytest.raises(TypeError, match="run_async"):
            bench.run_sync(dummy)

    def test_run_async(self) -> None:
        bench = TimeBasedThroughputBench("test", target_duration_ms=50.0, warmup=1)

        async def dummy() -> None:
            await asyncio.sleep(0)

        stats = asyncio.run(bench.run_async(dummy))
        assert stats.total_operations > 0


class TestFixedIterationThroughputBench:
    def test_run_sync(self) -> None:
        bench = FixedIterationThroughputBench("test", iterations=50, warmup=1)

        def dummy() -> None:
            pass

        stats = bench.run_sync(dummy)
        assert stats.total_operations == 50
        assert stats.duration_ms > 0.0

    def test_run_async(self) -> None:
        bench = FixedIterationThroughputBench("test", iterations=50, warmup=1)

        async def dummy() -> None:
            await asyncio.sleep(0)

        stats = asyncio.run(bench.run_async(dummy))
        assert stats.total_operations == 50


class TestThroughputDecorator:
    def test_sync_function(self) -> None:
        @throughput
        def dummy() -> None:
            pass

        stats = dummy(target_duration_ms=50.0)
        assert stats.total_operations > 0
