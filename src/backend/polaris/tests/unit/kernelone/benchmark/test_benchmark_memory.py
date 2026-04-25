"""Unit tests for polaris.kernelone.benchmark.memory."""

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


class TestMemorySnapshot:
    def test_mb_properties(self) -> None:
        snap = MemorySnapshot(current_bytes=2 * 1024 * 1024, peak_bytes=4 * 1024 * 1024)
        assert snap.current_mb == 2.0
        assert snap.peak_mb == 4.0


class TestMemoryProfile:
    def test_get_delta_mb_no_baseline(self) -> None:
        profile = MemoryProfile(name="test")
        assert profile.get_delta_mb() == 0.0

    def test_to_result(self) -> None:
        profile = MemoryProfile(name="test")
        profile.baseline = MemorySnapshot(current_bytes=0, peak_bytes=0)
        profile.peak = MemorySnapshot(current_bytes=2 * 1024 * 1024, peak_bytes=2 * 1024 * 1024)
        profile.final = MemorySnapshot(current_bytes=1 * 1024 * 1024, peak_bytes=2 * 1024 * 1024)
        result = profile.to_result()
        assert result.memory_baseline_mb == 0.0
        assert result.memory_peak_mb == 2.0


class TestMemoryBenchmarker:
    def test_run_sync(self) -> None:
        bench = MemoryBenchmarker("test")

        def dummy() -> list[int]:
            return [0] * 1000

        result = bench.run_sync(dummy)
        assert result.memory_baseline_mb >= 0.0

    def test_run_async(self) -> None:
        bench = MemoryBenchmarker("test")

        async def dummy() -> list[int]:
            await asyncio.sleep(0)
            return [0] * 1000

        result = asyncio.run(bench.run_async(dummy))
        assert result.memory_baseline_mb >= 0.0

    def test_run_async_rejects_sync(self) -> None:
        bench = MemoryBenchmarker("test")

        def dummy() -> None:
            pass

        with pytest.raises(TypeError, match="run_sync"):
            asyncio.run(bench.run_async(dummy))

    def test_get_profile(self) -> None:
        bench = MemoryBenchmarker("test")

        def dummy() -> None:
            pass

        bench.run_sync(dummy)
        profile = bench.get_profile()
        assert profile.name == "test"


class TestMemoryTracker:
    def test_context_manager(self) -> None:
        with MemoryTracker("op") as tracker:
            data = [0] * 500
            assert tracker._active is True

        assert tracker._active is False
        assert tracker.get_delta_mb() >= 0.0
        assert tracker.get_peak_mb() >= 0.0
        result = tracker.get_result()
        assert result.memory_baseline_mb >= 0.0


class TestMemoryProfileDecorator:
    def test_sync_decorator(self) -> None:
        @memory_profile
        def dummy() -> None:
            data = [0] * 1000

        profile = dummy()
        assert isinstance(profile, MemoryProfile)
        assert profile.name == "dummy"

    def test_async_decorator(self) -> None:
        @async_memory_profile
        async def dummy() -> None:
            await asyncio.sleep(0)
            data = [0] * 1000

        profile = asyncio.run(dummy())
        assert isinstance(profile, MemoryProfile)
        assert profile.name == "dummy"

    def test_async_decorator_rejects_sync(self) -> None:
        @async_memory_profile
        def dummy() -> None:
            pass

        with pytest.raises(TypeError, match="memory_profile"):
            dummy()
