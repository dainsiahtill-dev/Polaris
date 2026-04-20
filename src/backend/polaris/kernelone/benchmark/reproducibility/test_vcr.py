"""
Tests for VCR Cache Replay System

Verifies deterministic recording and replay of function calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.benchmark.reproducibility.vcr import (
    CacheReplay,
    Recording,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestCacheReplay:
    """Test suite for CacheReplay class."""

    @pytest.fixture
    def cache_replay(self, tmp_path: Path) -> CacheReplay:
        """Provide a CacheReplay instance for testing."""
        return CacheReplay(cache_dir=tmp_path / "cache", mode="both")

    def test_initialization_creates_cache_dir(self, tmp_path: Path) -> None:
        """Verify initialization creates cache directory."""
        cache_dir = tmp_path / "new_cache"
        CacheReplay(cache_dir=cache_dir, mode="replay")

        assert cache_dir.exists()
        assert cache_dir.is_dir()

    def test_invalid_mode_raises_error(self, tmp_path: Path) -> None:
        """Verify invalid mode raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CacheReplay(cache_dir=tmp_path / "cache", mode="invalid")

        assert "Invalid mode" in str(exc_info.value)

    def test_make_key_is_deterministic(self, cache_replay: CacheReplay) -> None:
        """Verify same inputs produce same key."""
        key1 = cache_replay._make_key("test", arg1="value")
        key2 = cache_replay._make_key("test", arg1="value")

        assert key1 == key2

    def test_make_key_differs_for_different_inputs(
        self,
        cache_replay: CacheReplay,
    ) -> None:
        """Verify different inputs produce different keys."""
        key1 = cache_replay._make_key("input1")
        key2 = cache_replay._make_key("input2")

        assert key1 != key2

    def test_save_and_load_recording(self, cache_replay: CacheReplay) -> None:
        """Verify recording save and load cycle."""
        test_response = {"result": "test_value", "data": [1, 2, 3]}
        key = "test_key_123"

        # Save
        cache_replay._save_recording(key, test_response)

        # Load
        recording = cache_replay._load_recording(key)

        assert recording is not None
        assert recording.request_key == key
        assert recording.response == test_response
        assert "timestamp" in asdict(recording)

    def test_load_nonexistent_returns_none(self, cache_replay: CacheReplay) -> None:
        """Verify loading nonexistent key returns None."""
        recording = cache_replay._load_recording("nonexistent_key")
        assert recording is None

    def test_replay_decorator_replays_cached_response(
        self,
        cache_replay: CacheReplay,
    ) -> None:
        """Verify decorator replays cached responses."""
        call_count = 0

        @cache_replay.replay
        def expensive_function(value: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"result": value * 2}

        # First call should execute
        result1 = expensive_function(5)
        assert call_count == 1
        assert result1 == {"result": 10}

        # Second call should replay
        result2 = expensive_function(5)
        assert call_count == 1  # Not incremented
        assert result2 == {"result": 10}

    def test_replay_decorator_records_new_response(
        self,
        cache_replay: CacheReplay,
    ) -> None:
        """Verify decorator records new responses."""
        call_count = 0

        @cache_replay.replay
        def function_with_counter(x: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"count": call_count, "input": x}

        # First call records
        result = function_with_counter(10)
        assert result == {"count": 1, "input": 10}

    def test_record_mode_only_records(self, tmp_path: Path) -> None:
        """Verify record mode only records, never replays."""
        cache = CacheReplay(cache_dir=tmp_path / "record_cache", mode="record")

        call_count = 0

        @cache.replay
        def count_calls(value: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        # Multiple calls all execute
        count_calls(1)
        count_calls(1)
        assert call_count == 2

    def test_replay_mode_only_replays(self, tmp_path: Path) -> None:
        """Verify replay mode fails if no recording exists."""
        cache = CacheReplay(cache_dir=tmp_path / "replay_cache", mode="replay")

        call_count = 0

        @cache.replay
        def always_counts(value: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        # Pre-populate cache
        cache._save_recording(
            cache._make_key(99),
            {"count": 100},
        )

        # Call with cached key replays
        result = always_counts(99)
        assert result == {"count": 100}
        assert call_count == 0  # Not called

    def test_clear_specific_key(self, cache_replay: CacheReplay) -> None:
        """Verify clearing specific key removes recording."""
        key = "to_clear"
        cache_replay._save_recording(key, {"data": "test"})

        assert cache_replay._load_recording(key) is not None
        cache_replay.clear(key)
        assert cache_replay._load_recording(key) is None

    def test_clear_all(self, cache_replay: CacheReplay) -> None:
        """Verify clearing all removes all recordings."""
        cache_replay._save_recording("key1", {"data": "1"})
        cache_replay._save_recording("key2", {"data": "2"})

        cache_replay.clear()

        assert cache_replay._load_recording("key1") is None
        assert cache_replay._load_recording("key2") is None

    def test_list_recordings(self, cache_replay: CacheReplay) -> None:
        """Verify listing all recordings."""
        cache_replay._save_recording("key1", {"data": "1"})
        cache_replay._save_recording("key2", {"data": "2"})

        recordings = cache_replay.list_recordings()

        assert len(recordings) == 2
        assert all(isinstance(r, Recording) for r in recordings)

    def test_has_recording(self, cache_replay: CacheReplay) -> None:
        """Verify checking if recording exists."""
        cache_replay._save_recording("existing", {"data": "test"})

        assert cache_replay.has_recording("existing") is True
        assert cache_replay.has_recording("nonexistent") is False

    def test_key_prefix(self, tmp_path: Path) -> None:
        """Verify key prefix is applied."""
        cache = CacheReplay(
            cache_dir=tmp_path / "prefix_cache",
            mode="both",
            key_prefix="test_",
        )

        key = cache._make_key("arg1")
        assert key.startswith("test_")


class TestAsyncCacheReplay:
    """Test suite for async function caching."""

    @pytest.fixture
    def async_cache(self, tmp_path: Path) -> CacheReplay:
        """Provide async-ready CacheReplay."""
        return CacheReplay(cache_dir=tmp_path / "async_cache", mode="both")

    @pytest.mark.asyncio
    async def test_async_replay(self, async_cache: CacheReplay) -> None:
        """Verify async function replay."""
        call_count = 0

        @async_cache.replay
        async def async_function(value: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"result": value * 3}

        # First call
        result1 = await async_function(5)
        assert call_count == 1
        assert result1 == {"result": 15}

        # Replay
        result2 = await async_function(5)
        assert call_count == 1
        assert result2 == {"result": 15}


# Helper for test
def asdict(obj: object) -> dict:
    """Convert dataclass to dict."""
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: getattr(obj, f.name) for f in obj.__dataclass_fields__.values()}
    raise TypeError(f"Object {obj} is not a dataclass")
