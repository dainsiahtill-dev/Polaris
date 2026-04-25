"""Tests for polaris.kernelone.benchmark.latency."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from polaris.kernelone.benchmark.latency import (
    LatencyBenchmarker,
    LatencyMeasurement,
    LatencyProfile,
    measure_latency,
    measure_latency_async,
)
from polaris.kernelone.benchmark.models import LatencyBenchmarkResult


class TestLatencyMeasurement:
    def test_fields(self) -> None:
        m = LatencyMeasurement(latency_ms=5.0, metadata={"key": "val"})
        assert m.latency_ms == 5.0
        assert m.metadata == {"key": "val"}
        assert m.timestamp > 0


class TestLatencyProfile:
    def test_add_measurement(self) -> None:
        profile = LatencyProfile(name="test")
        profile.add_measurement(10.0, metadata={"run": 1})
        assert len(profile.measurements) == 1
        assert profile.measurements[0].latency_ms == 10.0

    def test_add_warmup(self) -> None:
        profile = LatencyProfile(name="test")
        profile.add_warmup(5.0)
        assert len(profile.warmup_measurements) == 1
        assert profile.warmup_measurements[0].latency_ms == 5.0

    def test_compute_statistics(self) -> None:
        profile = LatencyProfile(name="test")
        for i in range(1, 101):
            profile.add_measurement(float(i))
        profile.compute_statistics()
        assert profile.p50 == 50.0
        assert profile.p90 == 90.0
        assert profile.p95 == 95.0
        assert profile.p99 == 99.0
        assert profile.mean == 50.5
        assert profile.min_latency == 1.0
        assert profile.max_latency == 100.0
        assert profile.std_dev > 0

    def test_compute_statistics_empty(self) -> None:
        profile = LatencyProfile(name="test")
        profile.compute_statistics()
        assert profile.p50 == 0.0

    def test_to_result(self) -> None:
        profile = LatencyProfile(name="test")
        for i in range(1, 101):
            profile.add_measurement(float(i))
        profile.compute_statistics()
        result = profile.to_result()
        assert isinstance(result, LatencyBenchmarkResult)
        assert result.metric_name == "test"
        assert result.p50_ms == 50.0

    def test_get_tail_ratio(self) -> None:
        profile = LatencyProfile(name="test")
        profile.p50 = 10.0
        profile.p99 = 50.0
        assert profile.get_tail_ratio() == 5.0

    def test_get_tail_ratio_zero(self) -> None:
        profile = LatencyProfile(name="test")
        assert profile.get_tail_ratio() == 0.0

    def test_get_coefficient_of_variation(self) -> None:
        profile = LatencyProfile(name="test")
        profile.mean = 10.0
        profile.std_dev = 5.0
        assert profile.get_coefficient_of_variation() == 0.5

    def test_get_coefficient_of_variation_zero(self) -> None:
        profile = LatencyProfile(name="test")
        assert profile.get_coefficient_of_variation() == 0.0


class TestLatencyBenchmarker:
    def test_run_sync(self) -> None:
        bench = LatencyBenchmarker("sync_op", warmup=1, iterations=5)

        def op() -> None:
            pass

        profile = bench.run_sync(op)
        assert len(profile.measurements) == 5
        assert profile.name == "sync_op"

    def test_run_sync_rejects_async(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=1)

        async def async_op() -> None:
            pass

        with pytest.raises(TypeError, match="run_async"):
            bench.run_sync(async_op)

    def test_run_async(self) -> None:
        bench = LatencyBenchmarker("async_op", warmup=1, iterations=5)

        async def op() -> None:
            pass

        profile = asyncio.get_event_loop().run_until_complete(bench.run_async(op))
        assert len(profile.measurements) == 5

    def test_run_async_accepts_sync(self) -> None:
        bench = LatencyBenchmarker("mixed", warmup=1, iterations=5)

        def sync_op() -> None:
            pass

        profile = asyncio.get_event_loop().run_until_complete(bench.run_async(sync_op))
        assert len(profile.measurements) == 5

    def test_get_result(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=3)

        def op() -> None:
            pass

        bench.run_sync(op)
        result = bench.get_result()
        assert isinstance(result, LatencyBenchmarkResult)
        assert result.metric_name == "test"

    def test_get_stats(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=3)

        def op() -> None:
            pass

        bench.run_sync(op)
        stats = bench.get_stats()
        assert stats.iterations == 3
        assert len(stats.latencies) == 3

    def test_gc_between_iterations(self) -> None:
        bench = LatencyBenchmarker("test", warmup=0, iterations=2, gc_between_iterations=True)

        def op() -> None:
            pass

        bench.run_sync(op)
        assert len(bench.profile.measurements) == 2


class TestMeasureLatencyDecorator:
    def test_sync_decorator(self) -> None:
        @measure_latency
        def slow_op() -> None:
            pass

        latency = slow_op()
        assert latency >= 0.0

    def test_async_decorator(self) -> None:
        @measure_latency_async
        async def slow_op() -> None:
            pass

        latency = asyncio.get_event_loop().run_until_complete(slow_op())
        assert latency >= 0.0
