"""Tests for polaris.kernelone.benchmark.memory."""

from __future__ import annotations

import asyncio

import pytest

from polaris.kernelone.benchmark.memory import (
    MemoryBenchmarker,
    MemoryProfile,
    MemorySnapshot,
    MemoryTracker,
    async_memory_profile,
    memory_profile,
)
from polaris.kernelone.benchmark.models import MemoryBenchmarkResult


class TestMemorySnapshot:
    def test_properties(self) -> None:
        snap = MemorySnapshot(current_bytes=1024 * 1024, peak_bytes=2 * 1024 * 1024)
        assert snap.current_mb == 1.0
        assert snap.peak_mb == 2.0

    def test_zero(self) -> None:
        snap = MemorySnapshot(current_bytes=0, peak_bytes=0)
        assert snap.current_mb == 0.0
        assert snap.peak_mb == 0.0


class TestMemoryProfile:
    def test_get_delta_mb_with_baseline_and_final(self) -> None:
        profile = MemoryProfile(name="test")
        profile.baseline = MemorySnapshot(current_bytes=1024 * 1024, peak_bytes=1024 * 1024)
        profile.final = MemorySnapshot(current_bytes=2 * 1024 * 1024, peak_bytes=3 * 1024 * 1024)
        assert profile.get_delta_mb() == 1.0

    def test_get_delta_mb_fallback(self) -> None:
        profile = MemoryProfile(name="test", delta_mb=5.0)
        assert profile.get_delta_mb() == 5.0

    def test_to_result(self) -> None:
        profile = MemoryProfile(name="test")
        profile.baseline = MemorySnapshot(current_bytes=1024 * 1024, peak_bytes=1024 * 1024)
        profile.final = MemorySnapshot(current_bytes=2 * 1024 * 1024, peak_bytes=3 * 1024 * 1024)
        profile.allocations = 100
        result = profile.to_result()
        assert isinstance(result, MemoryBenchmarkResult)
        assert result.memory_baseline_mb == 1.0
        assert result.memory_delta_mb == 1.0
        assert result.memory_peak_mb == 3.0
        assert result.allocations_count == 100


class TestMemoryBenchmarker:
    def test_run_sync(self) -> None:
        bench = MemoryBenchmarker("test", track_gc=True, gc_before=True, gc_after=True)

        def op() -> None:
            data = [0] * 1000
            _ = len(data)

        result = bench.run_sync(op)
        assert isinstance(result, MemoryBenchmarkResult)
        assert result.memory_baseline_mb >= 0.0

    def test_run_sync_rejects_async(self) -> None:
        bench = MemoryBenchmarker("test")

        async def async_op() -> None:
            pass

        with pytest.raises(TypeError, match="run_async"):
            bench.run_sync(async_op)

    def test_run_async(self) -> None:
        bench = MemoryBenchmarker("test", track_gc=True, gc_before=True, gc_after=True)

        async def op() -> None:
            data = [0] * 1000
            _ = len(data)

        result = asyncio.get_event_loop().run_until_complete(bench.run_async(op))
        assert isinstance(result, MemoryBenchmarkResult)

    def test_run_async_rejects_sync(self) -> None:
        bench = MemoryBenchmarker("test")

        def sync_op() -> None:
            pass

        with pytest.raises(TypeError, match="run_sync"):
            asyncio.get_event_loop().run_until_complete(bench.run_async(sync_op))

    def test_get_profile(self) -> None:
        bench = MemoryBenchmarker("test")

        def op() -> None:
            pass

        bench.run_sync(op)
        profile = bench.get_profile()
        assert isinstance(profile, MemoryProfile)
        assert profile.name == "test"


class TestMemoryTracker:
    def test_context_manager(self) -> None:
        tracker = MemoryTracker("test")
        with tracker:
            data = [0] * 1000
            _ = len(data)
        assert not tracker._active
        assert tracker.get_delta_mb() >= 0.0
        assert tracker.get_peak_mb() >= 0.0
        result = tracker.get_result()
        assert isinstance(result, MemoryBenchmarkResult)

    def test_exception_in_context(self) -> None:
        tracker = MemoryTracker("test")
        with pytest.raises(ValueError):
            with tracker:
                raise ValueError("boom")
        assert not tracker._active


class TestMemoryProfileDecorator:
    def test_sync_decorator(self) -> None:
        @memory_profile
        def alloc() -> None:
            data = [0] * 1000
            _ = len(data)

        profile = alloc()
        assert isinstance(profile, MemoryProfile)
        assert profile.name == "alloc"

    def test_async_decorator(self) -> None:
        @async_memory_profile
        async def alloc() -> None:
            data = [0] * 1000
            _ = len(data)

        profile = asyncio.get_event_loop().run_until_complete(alloc())
        assert isinstance(profile, MemoryProfile)
        assert profile.name == "alloc"

    def test_async_decorator_rejects_sync(self) -> None:
        @async_memory_profile
        def sync_fn() -> None:
            pass

        with pytest.raises(TypeError, match="memory_profile"):
            asyncio.get_event_loop().run_until_complete(sync_fn())
