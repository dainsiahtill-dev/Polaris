"""Bounded cache implementations for ContextOS.

Provides size-limited caches with both entry count and byte size bounds.
"""

from __future__ import annotations

import logging
import sys
from collections import OrderedDict
from typing import Any, Generic, Protocol, TypeVar

logger = logging.getLogger(__name__)

K = TypeVar("K", contravariant=True)
V = TypeVar("V")


class BoundedCache(Protocol[K, V]):
    """Protocol for caches with enforced size limits."""

    max_entries: int
    max_bytes: int

    def put(self, key: K, value: V) -> None:
        """Store a value in the cache, evicting if necessary."""
        ...

    def get(self, key: K) -> V | None:
        """Retrieve a value from the cache."""
        ...

    def evict_if_needed(self) -> None:
        """Evict entries until cache is within bounds."""
        ...

    def clear(self) -> None:
        """Clear all entries from the cache."""
        ...

    @property
    def current_bytes(self) -> int:
        """Current byte size of all cached values."""
        ...

    @property
    def current_entries(self) -> int:
        """Current number of entries in the cache."""
        ...

    def pop(self, key: K, default: V | None = None) -> V | None:
        """Remove and return a value from the cache."""
        ...


def _estimate_value_size(value: Any) -> int:
    """Estimate the memory size of a value in bytes.

    Uses sys.getsizeof for a rough estimate. For complex objects
    this may undercount, but serves as a reasonable heuristic.
    """
    try:
        return sys.getsizeof(value)
    except (TypeError, ValueError):
        return 1024  # Default fallback size


class LRUBoundedCache(Generic[K, V]):
    """LRU cache with both entry count and byte size limits.

    Eviction policy:
    1. First evict expired entries (if TTL is configured externally)
    2. Then evict least-recently-used entries until both
       max_entries and max_bytes constraints are satisfied.
    """

    def __init__(
        self,
        max_entries: int = 128,
        max_bytes: int = 10_000_000,
    ) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._cache: OrderedDict[K, V] = OrderedDict()
        self._current_bytes = 0

    def put(self, key: K, value: V) -> None:
        """Store a value in the cache.

        If the key already exists, updates the value and moves it
        to the end (most recently used). Then evicts if needed.
        """
        # Remove old value size if key exists
        if key in self._cache:
            old_value = self._cache[key]
            self._current_bytes -= _estimate_value_size(old_value)

        # Add new value
        self._cache[key] = value
        self._cache.move_to_end(key)
        self._current_bytes += _estimate_value_size(value)

        self.evict_if_needed()

    def get(self, key: K) -> V | None:
        """Retrieve a value and mark it as recently used."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def evict_if_needed(self) -> None:
        """Evict LRU entries until within bounds.

        Continues evicting until both entry count and byte size
        are within their respective limits.
        """
        while len(self._cache) > self.max_entries or self._current_bytes > self.max_bytes:
            if not self._cache:
                break
            # Pop the first (least recently used) item
            key, value = self._cache.popitem(last=False)
            self._current_bytes -= _estimate_value_size(value)
            logger.debug(
                "LRUBoundedCache evicted key=%s (entries=%d/%d, bytes=%d/%d)",
                key,
                len(self._cache),
                self.max_entries,
                self._current_bytes,
                self.max_bytes,
            )

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._cache.clear()
        self._current_bytes = 0

    @property
    def current_bytes(self) -> int:
        """Current estimated byte size of cached values."""
        return self._current_bytes

    @property
    def current_entries(self) -> int:
        """Current number of entries."""
        return len(self._cache)

    def pop(self, key: K, default: V | None = None) -> V | None:
        """Remove and return a value from the cache.

        Args:
            key: The key to remove.
            default: Value to return if key is not found.

        Returns:
            The removed value, or default if key was not present.
        """
        if key not in self._cache:
            return default
        value = self._cache.pop(key)
        self._current_bytes -= _estimate_value_size(value)
        return value
