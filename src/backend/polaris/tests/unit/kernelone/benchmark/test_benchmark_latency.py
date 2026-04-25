"""Unit tests for polaris.kernelone.benchmark.latency."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.benchmark.latency import (
    LatencyBenchmarker,
    LatencyMeasurement,
    LatencyProfile,
    measure_latency,
    measure_latency_async,
)


class TestLatencyMeasurement:
    def test_fields(self) -> None:
        m = LatencyMeasurement(latency_ms=10.0, metadata={"key": "val"})
        assert m.latency_ms == 10.0
        assert m.metadata == {"key": "val"}


class TestLatencyProfile:
    def test_add_measurement(self) -> None:
        profile = LatencyProfile(name="test")
        profile.add_measurement(10.0, metadata={"k": "v"})
        assert len(profile.measurements) == 1
        assert profile.measurements[0].latency_ms == 10.0

    def test_add_warmup(self) -> None:
        profile = LatencyProfile(name="test")
        profile.add_warmup(5.0)
        assert len(profile.warmup_measurements) == 1

    def test_compute_statistics(self) -> None:
        profile = LatencyProfile(name="test")
        for i in range(1, 11):
            profile.add_measurement(float(i))
        profile.compute_statistics()
        # int(10 * 0.50) = 5 -> index 5 = 6.0 (0-indexed)
        assert profile.p50 == 6.0
        assert profile.mean == 5.5
        assert profile.min_latency == 1.0
        assert profile.max_latency == 10.0

    def test_compute_statistics_empty(self) -> None:
        profile = LatencyProfile(name="test")
        profile.compute_statistics()
        assert profile.p50 == 0.0

    def test_to_result(self) -> None:
        profile = LatencyProfile(name="test")
        for i in range(1, 11):
            profile.add_measurement(float(i))
        profile.compute_statistics()
        result = profile.to_result()
        assert result.metric_name == "test"
        assert result.p50_ms == 6.0

    def test_get_tail_ratio(self) -> None:
        profile = LatencyProfile(name="test")
        profile.p50 = 10.0
        profile.p99 = 50.0
        assert profile.get_tail_ratio() == 5.0

    def test_get_coefficient_of_variation(self) -> None:
        profile = LatencyProfile(name="test")
        profile.mean = 10.0
        profile.std_dev = 5.0
        assert profile.get_coefficient_of_variation() == 0.5


class TestLatencyBenchmarker:
    def test_run_sync(self) -> None:
        bench = LatencyBenchmarker("sync_test", warmup=1, iterations=5)

        def dummy() -> None:
            pass

        profile = bench.run_sync(dummy)
        assert len(profile.measurements) == 5
        assert profile.name == "sync_test"

    def test_run_sync_rejects_async(self) -> None:
        bench = LatencyBenchmarker("test")

        async def dummy() -> None:
            pass

        with pytest.raises(TypeError, match="run_async"):
            bench.run_sync(dummy)

    def test_run_async(self) -> None:
        bench = LatencyBenchmarker("async_test", warmup=1, iterations=5)

        async def dummy() -> None:
            await asyncio.sleep(0)

        profile = asyncio.run(bench.run_async(dummy))
        assert len(profile.measurements) == 5

    def test_run_async_accepts_sync(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=3)

        def dummy() -> None:
            pass

        profile = asyncio.run(bench.run_async(dummy))
        assert isinstance(profile, LatencyProfile)
        assert len(profile.measurements) == 3

    def test_get_result(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=3)

        def dummy() -> None:
            pass

        bench.run_sync(dummy)
        result = bench.get_result()
        assert result.metric_name == "test"

    def test_get_stats(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=3)

        def dummy() -> None:
            pass

        bench.run_sync(dummy)
        stats = bench.get_stats()
        assert len(stats.latencies) == 3


class TestMeasureLatencyDecorator:
    def test_sync_decorator(self) -> None:
        @measure_latency
        def dummy() -> None:
            pass

        latency = dummy()
        assert latency >= 0.0

    def test_async_decorator(self) -> None:
        async def dummy() -> None:
            await asyncio.sleep(0)

        decorated = asyncio.run(measure_latency_async(dummy))
        latency = asyncio.run(decorated())
        assert latency >= 0.0
