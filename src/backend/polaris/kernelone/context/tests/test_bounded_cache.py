"""Tests for BoundedCache and LRUBoundedCache implementations."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os.bounded_cache import LRUBoundedCache


class TestLRUBoundedCacheEntryLimit:
    """Tests for entry count limiting."""

    def test_put_exceeding_max_entries_evicts_oldest(self) -> None:
        """When putting more than max_entries, oldest entries are evicted."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=3, max_bytes=1_000_000)

        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.put("d", 4)

        assert cache.get("a") is None  # Evicted (oldest)
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get("d") == 4
        assert cache.current_entries == 3

    def test_get_updates_lru_order(self) -> None:
        """Accessing an entry moves it to most-recently-used position."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=3, max_bytes=1_000_000)

        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        # Access 'a' to make it most recently used
        cache.get("a")

        # Now 'b' is the oldest
        cache.put("d", 4)

        assert cache.get("a") == 1  # Should still exist
        assert cache.get("b") is None  # Evicted
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_put_existing_key_updates_value(self) -> None:
        """Putting to an existing key updates the value and LRU order."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=3, max_bytes=1_000_000)

        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)

        assert cache.get("a") == 10
        assert cache.current_entries == 2


class TestLRUBoundedCacheByteLimit:
    """Tests for byte size limiting."""

    def test_put_exceeding_max_bytes_evicts(self) -> None:
        """When total size exceeds max_bytes, entries are evicted."""
        cache: LRUBoundedCache[str, bytes] = LRUBoundedCache(max_entries=100, max_bytes=500)

        # Each bytes object has overhead; use small values
        cache.put("a", b"x" * 100)
        cache.put("b", b"x" * 100)
        cache.put("c", b"x" * 100)
        cache.put("d", b"x" * 100)
        cache.put("e", b"x" * 100)

        # Total should be around 500+ bytes; adding one more should trigger eviction
        cache.put("f", b"x" * 100)

        # At least one entry should have been evicted
        assert cache.current_entries <= 5
        assert cache.current_bytes <= cache.max_bytes

    def test_large_single_value_triggers_eviction(self) -> None:
        """A single large value can trigger eviction of multiple entries."""
        cache: LRUBoundedCache[str, bytes] = LRUBoundedCache(max_entries=100, max_bytes=500)

        cache.put("a", b"x" * 100)
        cache.put("b", b"x" * 100)
        cache.put("c", b"x" * 100)

        # This large value should evict all previous entries
        cache.put("big", b"x" * 400)

        assert cache.current_entries <= 2
        assert cache.current_bytes <= cache.max_bytes


class TestLRUBoundedCacheClear:
    """Tests for cache clearing."""

    def test_clear_removes_all_entries(self) -> None:
        """Clear should remove all entries and reset byte count."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=10, max_bytes=1_000_000)

        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        cache.clear()

        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None
        assert cache.current_entries == 0
        assert cache.current_bytes == 0

    def test_clear_on_empty_cache(self) -> None:
        """Clear on an empty cache should not raise."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=10, max_bytes=1_000_000)

        cache.clear()

        assert cache.current_entries == 0
        assert cache.current_bytes == 0


class TestLRUBoundedCacheProperties:
    """Tests for cache property accessors."""

    def test_current_entries_reflects_actual_count(self) -> None:
        """current_entries should match the number of stored items."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=10, max_bytes=1_000_000)

        assert cache.current_entries == 0

        cache.put("a", 1)
        assert cache.current_entries == 1

        cache.put("b", 2)
        assert cache.current_entries == 2

        cache.get("a")
        assert cache.current_entries == 2

    def test_current_bytes_is_non_negative(self) -> None:
        """current_bytes should always be non-negative."""
        cache: LRUBoundedCache[str, int] = LRUBoundedCache(max_entries=10, max_bytes=1_000_000)

        assert cache.current_bytes >= 0

        cache.put("a", 1)
        assert cache.current_bytes > 0

        cache.clear()
        assert cache.current_bytes == 0
