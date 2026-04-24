"""Tests for polaris.kernelone.context.context_os.bounded_cache."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os.bounded_cache import LRUBoundedCache, _estimate_value_size


class TestEstimateValueSize:
    def test_string(self) -> None:
        size = _estimate_value_size("hello")
        assert size > 0

    def test_dict(self) -> None:
        size = _estimate_value_size({"key": "value"})
        assert size > 0

    def test_fallback_for_unsupported(self) -> None:
        class Weird:
            __slots__ = ()

            def __sizeof__(self) -> int:
                raise TypeError("unsupported")

        size = _estimate_value_size(Weird())
        assert size == 1024


class TestLRUBoundedCache:
    @pytest.fixture
    def cache(self) -> LRUBoundedCache[str, str]:
        return LRUBoundedCache(max_entries=3, max_bytes=1_000_000)

    def test_put_and_get(self, cache: LRUBoundedCache[str, str]) -> None:
        cache.put("a", "apple")
        assert cache.get("a") == "apple"

    def test_get_missing(self, cache: LRUBoundedCache[str, str]) -> None:
        assert cache.get("missing") is None

    def test_eviction_by_entries(self, cache: LRUBoundedCache[str, str]) -> None:
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        cache.put("d", "4")
        assert cache.get("a") is None
        assert cache.get("d") == "4"

    def test_clear(self, cache: LRUBoundedCache[str, str]) -> None:
        cache.put("a", "1")
        cache.clear()
        assert cache.get("a") is None
        assert cache.current_entries == 0
        assert cache.current_bytes == 0

    def test_pop_existing(self, cache: LRUBoundedCache[str, str]) -> None:
        cache.put("a", "1")
        assert cache.pop("a") == "1"
        assert cache.get("a") is None

    def test_pop_missing(self, cache: LRUBoundedCache[str, str]) -> None:
        assert cache.pop("missing") is None

    def test_pop_with_default(self, cache: LRUBoundedCache[str, str]) -> None:
        assert cache.pop("missing", "default") == "default"

    def test_lru_order(self, cache: LRUBoundedCache[str, str]) -> None:
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")
        cache.get("a")
        cache.put("d", "4")
        assert cache.get("a") == "1"
        assert cache.get("b") is None
