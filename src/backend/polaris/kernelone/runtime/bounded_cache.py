"""BoundedCache - LRU cache with configurable capacity limit.

This module provides a thread-safe, bounded cache implementation for preventing
unbounded memory growth in runtime components.

Usage::

    from polaris.kernelone.runtime import BoundedCache

    cache = BoundedCache[str, ProcessHandle](max_size=1000)
    cache.set("exec-123", handle)
    handle = cache.get("exec-123")
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Generic, TypeVar

__all__ = ["BoundedCache"]

logger = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(Generic[K, V]):
    """LRU-bounded cache with thread-safe operations.

    Provides O(1) get/set operations with automatic LRU eviction when capacity
    is exceeded. Internally uses OrderedDict to track access order.

    Args:
        max_size: Maximum number of entries. Must be positive. Defaults to 1000.

    Raises:
        ValueError: If max_size is not a positive integer.

    Example::

        cache = BoundedCache[str, int](max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)

        # "a" is evicted when we add "d"
        cache.set("d", 4)
        assert cache.get("a") is None
        assert cache.get("d") == 4

        # Accessing "b" updates its position in LRU order
        _ = cache.get("b")
        cache.set("e", 5)
        # "c" is evicted (LRU), not "b"
        assert cache.get("c") is None
        assert cache.get("b") == 2
    """

    def __init__(self, max_size: int = 1000) -> None:
        if not isinstance(max_size, int) or max_size <= 0:
            raise ValueError(f"max_size must be a positive integer, got {max_size!r}")
        self._max_size = max_size
        self._data: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def max_size(self) -> int:
        """Return the maximum cache size."""
        return self._max_size

    def __len__(self) -> int:
        """Return the current number of entries."""
        with self._lock:
            return len(self._data)

    def __contains__(self, key: K) -> bool:
        """Check if key exists in cache (does not update LRU order)."""
        with self._lock:
            return key in self._data

    def set(self, key: K, value: V) -> None:
        """Set a value, updating LRU order and evicting if necessary.

        Args:
            key: Cache key.
            value: Value to store.
        """
        with self._lock:
            if key in self._data:
                # Move to end (most recently used) and update value
                self._data.move_to_end(key)
                self._data[key] = value
                return

            # Evict LRU entries if at capacity
            while len(self._data) >= self._max_size:
                evicted_key, _ = self._data.popitem(last=False)
                logger.debug(
                    "BoundedCache: evicted LRU entry key=%r (cap=%d)",
                    evicted_key,
                    self._max_size,
                )

            self._data[key] = value

    def get(self, key: K, default: V | None = None) -> V | None:
        """Get a value by key, updating its LRU position.

        Args:
            key: Cache key to look up.
            default: Default value if key not found. Defaults to None.

        Returns:
            The cached value, or default if key not found.
        """
        with self._lock:
            if key not in self._data:
                return default

            # Move to end (most recently used)
            self._data.move_to_end(key)
            return self._data[key]

    def peek(self, key: K, default: V | None = None) -> V | None:
        """Get a value without updating its LRU position.

        Args:
            key: Cache key to look up.
            default: Default value if key not found. Defaults to None.

        Returns:
            The cached value, or default if key not found.
        """
        with self._lock:
            return self._data.get(key, default)

    def remove(self, key: K) -> bool:
        """Remove an entry from the cache.

        Args:
            key: Cache key to remove.

        Returns:
            True if key was removed, False if key was not present.
        """
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._data.clear()

    def get_stats(self) -> dict[str, int]:
        """Return cache statistics.

        Returns:
            Dict with 'size' (current entries) and 'max_size' (capacity).
        """
        with self._lock:
            return {
                "size": len(self._data),
                "max_size": self._max_size,
            }

    def keys(self) -> list[K]:
        """Return all keys in LRU order (oldest to newest)."""
        with self._lock:
            return list(self._data.keys())

    def values(self) -> list[V]:
        """Return all values in LRU order."""
        with self._lock:
            return list(self._data.values())

    def items(self) -> list[tuple[K, V]]:
        """Return all (key, value) pairs in LRU order."""
        with self._lock:
            return list(self._data.items())
