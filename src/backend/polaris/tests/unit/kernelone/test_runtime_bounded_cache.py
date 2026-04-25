"""Tests for polaris.kernelone.runtime.bounded_cache."""

from __future__ import annotations

import pytest
from polaris.kernelone.runtime.bounded_cache import BoundedCache


class TestBoundedCacheInit:
    def test_default_size(self) -> None:
        cache = BoundedCache[str, int]()
        assert cache.max_size == 1000

    def test_custom_size(self) -> None:
        cache = BoundedCache[str, int](max_size=50)
        assert cache.max_size == 50

    def test_zero_size_raises(self) -> None:
        with pytest.raises(ValueError):
            BoundedCache[str, int](max_size=0)

    def test_negative_size_raises(self) -> None:
        with pytest.raises(ValueError):
            BoundedCache[str, int](max_size=-1)

    def test_non_int_size_raises(self) -> None:
        with pytest.raises(ValueError):
            BoundedCache[str, int](max_size="100")  # type: ignore[arg-type]


class TestBoundedCacheSetGet:
    def test_set_and_get(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        assert cache.get("a") == 1

    def test_get_missing_returns_none(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        assert cache.get("missing") is None

    def test_get_missing_with_default(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        assert cache.get("missing", default=42) == 42

    def test_update_existing_key(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("a", 2)
        assert cache.get("a") == 2


class TestBoundedCacheLRUEviction:
    def test_eviction_at_capacity(self) -> None:
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.set("d", 4)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_access_updates_lru_order(self) -> None:
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # Access "a" to make it most recently used
        cache.get("a")
        cache.set("d", 4)
        # "b" should be evicted (LRU), not "a"
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3
        assert cache.get("d") == 4


class TestBoundedCachePeek:
    def test_peek_returns_value(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        assert cache.peek("a") == 1

    def test_peek_does_not_update_lru(self) -> None:
        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.peek("a")
        cache.set("d", 4)
        # "a" should still be evicted because peek doesn't update LRU
        assert cache.get("a") is None


class TestBoundedCacheRemove:
    def test_remove_existing(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        assert cache.remove("a") is True
        assert cache.get("a") is None

    def test_remove_missing(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        assert cache.remove("missing") is False


class TestBoundedCacheClear:
    def test_clear_removes_all(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache) == 0


class TestBoundedCacheContains:
    def test_contains_existing(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        assert "a" in cache

    def test_contains_missing(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        assert "a" not in cache


class TestBoundedCacheLen:
    def test_len_increments(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        assert len(cache) == 0
        cache.set("a", 1)
        assert len(cache) == 1
        cache.set("b", 2)
        assert len(cache) == 2

    def test_len_respects_capacity(self) -> None:
        cache = BoundedCache[str, int](max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert len(cache) == 2


class TestBoundedCacheStats:
    def test_stats(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        stats = cache.get_stats()
        assert stats["size"] == 1
        assert stats["max_size"] == 10


class TestBoundedCacheKeysValuesItems:
    def test_keys_in_lru_order(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")  # Move "a" to end
        assert cache.keys() == ["b", "a"]

    def test_values_in_lru_order(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.values() == [1, 2]

    def test_items_in_lru_order(self) -> None:
        cache = BoundedCache[str, int](max_size=10)
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.items() == [("a", 1), ("b", 2)]


class TestBoundedCacheThreadSafety:
    def test_concurrent_access_does_not_crash(self) -> None:
        import threading

        cache = BoundedCache[int, int](max_size=100)
        errors = []

        def writer() -> None:
            try:
                for i in range(200):
                    cache.set(i % 50, i)
            except Exception as e:
                errors.append(e)

        def reader() -> None:
            try:
                for i in range(200):
                    cache.get(i % 50)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)] + [
            threading.Thread(target=reader) for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache) <= 100
