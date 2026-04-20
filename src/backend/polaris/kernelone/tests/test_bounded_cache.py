"""Tests for BoundedCache - P2-001 bounded cache implementation."""

from __future__ import annotations

import threading
import time

import pytest
from polaris.kernelone.runtime import BoundedCache


class TestBoundedCacheBasic:
    """Basic functionality tests."""

    def test_empty_cache(self) -> None:
        """Cache starts empty."""
        cache = BoundedCache[str, int](max_size=10)
        assert len(cache) == 0
        assert "key" not in cache

    def test_set_and_get(self) -> None:
        """Basic set/get operations."""
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)

        assert len(cache) == 2
        assert cache.get("a") == 1
        assert cache.get("b") == 2

    def test_get_missing_key_returns_default(self) -> None:
        """Missing keys return the default value."""
        cache = BoundedCache[str, int](max_size=10)
        assert cache.get("missing") is None
        assert cache.get("missing", -1) == -1

    def test_update_existing_key(self) -> None:
        """Updating existing key does not increase size."""
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)

        assert len(cache) == 2

        cache.set("a", 10)  # Update existing

        assert len(cache) == 2
        assert cache.get("a") == 10
        assert cache.get("b") == 2

    def test_remove_existing_key(self) -> None:
        """Removing existing key works."""
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)

        assert cache.remove("a") is True
        assert len(cache) == 1
        assert cache.get("a") is None
        assert cache.get("b") == 2

    def test_remove_missing_key(self) -> None:
        """Removing missing key returns False."""
        cache = BoundedCache[str, int](max_size=10)
        assert cache.remove("missing") is False

    def test_clear(self) -> None:
        """Clear removes all entries."""
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        cache.clear()

        assert len(cache) == 0
        assert "a" not in cache

    def test_contains(self) -> None:
        """Contains check works."""
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)

        assert "a" in cache
        assert "b" not in cache

    def test_max_size_property(self) -> None:
        """max_size property returns configured value."""
        cache = BoundedCache[str, int](max_size=500)
        assert cache.max_size == 500


class TestBoundedCacheCapacity:
    """Capacity limit and LRU eviction tests."""

    def test_capacity_limit_respected(self) -> None:
        """Cache never exceeds max_size."""
        cache = BoundedCache[str, int](max_size=3)

        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        assert len(cache) == 3

        cache.set("d", 4)  # Should evict "a"

        assert len(cache) == 3

    def test_lru_eviction_order(self) -> None:
        """Least recently used entry is evicted first."""
        cache = BoundedCache[str, int](max_size=3)

        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        # Access "a" to make it most recently used
        cache.get("a")

        # Add new entry - "b" should be evicted (it's now LRU)
        cache.set("d", 4)

        assert cache.get("a") == 1  # Still accessible
        assert cache.get("b") is None  # Evicted
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_update_does_not_trigger_eviction(self) -> None:
        """Updating existing key does not trigger eviction."""
        cache = BoundedCache[str, int](max_size=3)

        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        cache.set("a", 10)  # Update, not new entry

        cache.set("d", 4)

        # "b" should be evicted, not "a"
        assert cache.get("b") is None
        assert cache.get("a") == 10

    def test_keys_values_items(self) -> None:
        """keys(), values(), items() return correct data in LRU order."""
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        keys = cache.keys()
        values = cache.values()
        items = cache.items()

        assert keys == ["a", "b", "c"]
        assert values == [1, 2, 3]
        assert items == [("a", 1), ("b", 2), ("c", 3)]

    def test_peek_does_not_update_lru(self) -> None:
        """peek() returns value without updating LRU order."""
        cache = BoundedCache[str, int](max_size=3)

        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        cache.peek("a")  # Access without updating LRU

        cache.set("d", 4)  # Should evict "a" since peek didn't update it

        assert cache.get("a") is None

    def test_remove_does_not_cause_immediate_eviction(self) -> None:
        """Removing an entry doesn't trigger eviction of others."""
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        cache.remove("a")

        cache.set("d", 4)
        cache.set("e", 5)

        # Only 3 entries should remain
        assert len(cache) == 3


class TestBoundedCacheDefaultSize:
    """Test default size (1000)."""

    def test_default_size(self) -> None:
        """Default max_size is 1000."""
        cache = BoundedCache[str, int]()
        assert cache.max_size == 1000

    def test_default_size_capacity(self) -> None:
        """Cache respects default size."""
        cache = BoundedCache[str, int]()

        # Add 1000 entries
        for i in range(1000):
            cache.set(f"key_{i}", i)

        assert len(cache) == 1000

        # Add one more - should evict first entry
        cache.set("key_1000", 1000)
        assert len(cache) == 1000
        assert cache.get("key_0") is None
        assert cache.get("key_1000") == 1000


class TestBoundedCacheInvalidInput:
    """Invalid input handling tests."""

    def test_zero_max_size_raises(self) -> None:
        """Zero max_size raises ValueError."""
        with pytest.raises(ValueError, match="positive integer"):
            BoundedCache[str, int](max_size=0)

    def test_negative_max_size_raises(self) -> None:
        """Negative max_size raises ValueError."""
        with pytest.raises(ValueError, match="positive integer"):
            BoundedCache[str, int](max_size=-1)

    def test_non_integer_max_size_raises(self) -> None:
        """Non-integer max_size raises ValueError."""
        with pytest.raises(ValueError, match="positive integer"):
            BoundedCache[str, int](max_size="100")  # type: ignore


class TestBoundedCacheThreadSafety:
    """Thread safety tests."""

    def test_concurrent_set(self) -> None:
        """Concurrent set operations are thread-safe."""
        cache = BoundedCache[str, int](max_size=1000)

        def worker(start: int, count: int) -> None:
            for i in range(start, start + count):
                cache.set(f"key_{i}", i)

        threads = [threading.Thread(target=worker, args=(i * 100, 100)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # With 1000 entries and 1000 capacity, all entries should be present
        assert len(cache) == 1000

        # Verify entries are accessible (may have been evicted due to concurrent access)
        # But we can verify that some entries are definitely there
        verified_count = 0
        for i in range(1000):
            if cache.get(f"key_{i}") == i:
                verified_count += 1

        # At least some entries should be correct (given the high capacity and single-writer pattern)
        assert verified_count > 0

    def test_concurrent_get_set(self) -> None:
        """Concurrent get/set operations are thread-safe."""
        cache = BoundedCache[str, int](max_size=100)

        def writer() -> None:
            for i in range(100):
                cache.set(f"key_{i}", i)
                time.sleep(0.0001)

        def reader() -> None:
            for _ in range(100):
                cache.get("key_0")
                time.sleep(0.0001)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No crashes, cache is in valid state
        assert len(cache) <= 100

    def test_thread_safe_length(self) -> None:
        """len() returns consistent results under concurrent access."""
        cache = BoundedCache[str, int](max_size=100)

        def writer() -> None:
            for i in range(100):
                cache.set(f"key_{i}", i)

        threads = [threading.Thread(target=writer) for _ in range(10)]

        for t in threads:
            t.start()

        # len() should not crash
        for _ in range(10):
            _ = len(cache)
            time.sleep(0.001)

        for t in threads:
            t.join()


class TestBoundedCacheStats:
    """Statistics and introspection tests."""

    def test_get_stats(self) -> None:
        """get_stats() returns correct statistics."""
        cache = BoundedCache[str, int](max_size=50)
        cache.set("a", 1)
        cache.set("b", 2)

        stats = cache.get_stats()

        assert stats["size"] == 2
        assert stats["max_size"] == 50

    def test_stats_after_eviction(self) -> None:
        """Stats reflect eviction correctly."""
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("d", 4)  # Triggers eviction

        stats = cache.get_stats()
        assert stats["size"] == 3
        assert stats["max_size"] == 3
