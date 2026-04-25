"""Tests for polaris.kernelone.benchmark.fixtures."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.benchmark.fixtures import (
    BenchmarkContext,
    async_memory_benchmark,
    benchmark,
    memory_benchmark,
    throughput_benchmark,
)
from polaris.kernelone.benchmark.models import BenchmarkResult, BenchmarkStats, ThroughputStats


class TestBenchmarkContext:
    def test_record_latency(self) -> None:
        ctx = BenchmarkContext("test")
        with ctx:
            ctx.record_latency(5.0)
            ctx.record_latency(10.0)
        stats = ctx.get_stats()
        assert isinstance(stats, BenchmarkStats)
        assert stats.latencies == [5.0, 10.0]

    def test_timer(self) -> None:
        ctx = BenchmarkContext("test")
        with ctx:
            start = ctx.start_timer()
            elapsed = ctx.stop_timer(start)
        assert elapsed >= 0.0

    def test_get_result(self) -> None:
        ctx = BenchmarkContext("my_metric")
        with ctx:
            ctx.record_latency(5.0)
        result = ctx.get_result()
        assert isinstance(result, BenchmarkResult)
        assert result.metric_name == "my_metric"


class TestBenchmarkDecorator:
    def test_sync_function(self) -> None:
        @benchmark(warmup=1, iterations=3, metric_name="sync_bench")
        def slow_op() -> None:
            pass

        stats = slow_op()
        assert isinstance(stats, BenchmarkStats)
        assert stats.iterations == 3
        assert stats.warmup_count == 1

    def test_async_function(self) -> None:
        @benchmark(warmup=1, iterations=3, metric_name="async_bench")
        async def slow_op() -> None:
            pass

        stats = asyncio.run(slow_op())
        assert isinstance(stats, BenchmarkStats)
        assert stats.iterations == 3


class TestMemoryBenchmarkDecorator:
    def test_sync_function(self) -> None:
        @memory_benchmark
        def alloc() -> None:
            data = [0] * 1000
            _ = len(data)

        result = alloc()
        assert "memory_peak_mb" in result
        assert "memory_delta_mb" in result

    def test_async_function_rejected(self) -> None:
        @memory_benchmark
        async def alloc() -> None:
            pass

        with pytest.raises(TypeError, match="async_memory_benchmark"):
            asyncio.run(alloc())


class TestAsyncMemoryBenchmarkDecorator:
    def test_async_function(self) -> None:
        @async_memory_benchmark
        async def alloc() -> None:
            data = [0] * 1000
            _ = len(data)

        result = asyncio.run(alloc())
        assert "memory_peak_mb" in result

    def test_sync_function_rejected(self) -> None:
        @async_memory_benchmark
        def alloc() -> None:
            pass

        with pytest.raises(TypeError, match="memory_benchmark"):
            asyncio.run(alloc())


class TestThroughputBenchmarkDecorator:
    def test_sync_function(self) -> None:
        @throughput_benchmark(warmup=1, target_duration_ms=50.0)
        def process() -> None:
            pass

        stats = process()
        assert isinstance(stats, ThroughputStats)
        assert stats.total_operations > 0

    def test_async_function(self) -> None:
        @throughput_benchmark(warmup=1, target_duration_ms=50.0)
        async def process() -> None:
            pass

        stats = asyncio.run(process())
        assert isinstance(stats, ThroughputStats)
