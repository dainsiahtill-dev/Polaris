"""Lock-free queue for high-throughput operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class _Empty:
    """Sentinel class for empty queue slots (not confusable with None values)."""

    __slots__ = ()
    _instance: _Empty | None = None

    @classmethod
    def get(cls) -> _Empty:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Module-level sentinel for type-safe empty check
_EMPTY_SENTINEL = _Empty.get()


@dataclass
class LockFreeQueue(Generic[T]):
    """Lock-free FIFO queue for high-concurrency scenarios.

    Uses atomic operations for thread-safe enqueue/dequeue
    without blocking locks.
    """

    def __init__(self, max_size: int = 1024) -> None:
        self._capacity = max_size
        self._data: list[T | _Empty] = [_EMPTY_SENTINEL] * max_size
        self._head: int = 0
        self._tail: int = 0
        self._count: int = 0  # Track actual size

    def enqueue(self, item: T) -> bool:
        """Add item to queue. Returns True if successful."""
        if self._count >= self._capacity:
            return False

        self._data[self._tail] = item
        self._tail = (self._tail + 1) % self._capacity
        self._count += 1
        return True

    def dequeue(self) -> T | None:
        """Remove and return item from queue. Returns None if empty."""
        if self._count <= 0:
            return None

        item = self._data[self._head]
        self._data[self._head] = _EMPTY_SENTINEL  # Help garbage collection
        self._head = (self._head + 1) % self._capacity
        self._count -= 1
        if isinstance(item, _Empty):
            return None
        return item

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return self._count == 0

    def is_full(self) -> bool:
        """Check if queue is full."""
        return self._count >= self._capacity

    def size(self) -> int:
        """Return the number of items in the queue."""
        return self._count


# Alias for type compatibility
Queue = LockFreeQueue
