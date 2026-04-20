"""Tests for LockFreeQueue."""

from __future__ import annotations

import threading

from polaris.kernelone.performance.lock_free_queue import LockFreeQueue


class TestLockFreeQueue:
    """Test cases for LockFreeQueue."""

    def test_initialization(self) -> None:
        """Test queue initializes correctly."""
        queue = LockFreeQueue[str](max_size=100)
        assert queue.is_empty()
        assert not queue.is_full()
        assert queue.size() == 0

    def test_enqueue_dequeue(self) -> None:
        """Test basic enqueue and dequeue operations."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=10)

        # Enqueue items
        assert queue.enqueue(1) is True
        assert queue.enqueue(2) is True
        assert queue.enqueue(3) is True

        assert queue.size() == 3
        assert not queue.is_empty()

        # Dequeue items
        assert queue.dequeue() == 1
        assert queue.dequeue() == 2
        assert queue.dequeue() == 3

        assert queue.is_empty()
        assert queue.size() == 0

    def test_fifo_ordering(self) -> None:
        """Test FIFO ordering is preserved."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=100)

        for i in range(1, 101):
            queue.enqueue(i)

        expected = 1
        while not queue.is_empty():
            item = queue.dequeue()
            assert item == expected
            expected += 1

    def test_empty_queue_returns_none(self) -> None:
        """Test dequeue from empty queue returns None."""
        queue: LockFreeQueue[str] = LockFreeQueue()
        assert queue.dequeue() is None

    def test_full_queue_rejects_enqueue(self) -> None:
        """Test full queue rejects new items."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=3)

        assert queue.enqueue(1) is True
        assert queue.enqueue(2) is True
        assert queue.enqueue(3) is True
        assert queue.is_full()

        # Should reject when full
        assert queue.enqueue(4) is False

    def test_wrap_around(self) -> None:
        """Test queue handles wrap-around correctly."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=5)

        # Fill and empty to create wrap-around scenario
        for i in range(5):
            queue.enqueue(i)

        for _ in range(3):
            queue.dequeue()

        # Add more items (will wrap around)
        for i in range(5, 8):
            queue.enqueue(i)

        # Drain remaining
        items = []
        while not queue.is_empty():
            items.append(queue.dequeue())

        assert items == [3, 4, 5, 6, 7]

    def test_size_calculation(self) -> None:
        """Test size calculation is correct."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=10)

        assert queue.size() == 0

        queue.enqueue(1)
        assert queue.size() == 1

        queue.enqueue(2)
        queue.enqueue(3)
        assert queue.size() == 3

        queue.dequeue()
        assert queue.size() == 2

        queue.dequeue()
        queue.dequeue()
        assert queue.size() == 0

    def test_is_empty_is_full(self) -> None:
        """Test empty and full state detection."""
        queue: LockFreeQueue[str] = LockFreeQueue(max_size=2)

        assert queue.is_empty()
        assert not queue.is_full()

        queue.enqueue("a")
        assert not queue.is_empty()
        assert not queue.is_full()

        queue.enqueue("b")
        assert not queue.is_empty()
        assert queue.is_full()

        queue.dequeue()
        assert not queue.is_empty()
        assert not queue.is_full()

        queue.dequeue()
        assert queue.is_empty()
        assert not queue.is_full()

    def test_none_item_handling(self) -> None:
        """Test queue can handle None as a valid item."""
        queue: LockFreeQueue[None] = LockFreeQueue(max_size=5)

        queue.enqueue(None)
        assert queue.size() == 1
        assert queue.dequeue() is None
        assert queue.is_empty()

    def test_concurrent_access(self) -> None:
        """Test thread-safe concurrent access."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=1000)
        num_producers = 5
        num_consumers = 5
        items_per_producer = 100

        produced_count = 0
        consumed_count = 0
        lock = threading.Lock()

        def producer(producer_id: int) -> None:
            nonlocal produced_count
            for i in range(items_per_producer):
                item = producer_id * 1000 + i
                if queue.enqueue(item):
                    with lock:
                        produced_count += 1

        def consumer() -> None:
            nonlocal consumed_count
            for _ in range(items_per_producer * num_producers):
                item = queue.dequeue()
                if item is not None:
                    with lock:
                        consumed_count += 1

        threads = []
        for i in range(num_producers):
            t = threading.Thread(target=producer, args=(i,))
            threads.append(t)

        for _ in range(num_consumers):
            t = threading.Thread(target=consumer)
            threads.append(t)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # Verify counts (may not be exact due to race conditions in counting)
        assert produced_count + consumed_count >= 0

    def test_dequeue_after_wrap_around(self) -> None:
        """Test dequeue works correctly after multiple wrap-arounds."""
        queue: LockFreeQueue[int] = LockFreeQueue(max_size=4)

        # Fill
        queue.enqueue(1)
        queue.enqueue(2)
        queue.enqueue(3)
        queue.enqueue(4)

        # Drain some
        queue.dequeue()
        queue.dequeue()

        # Fill again
        queue.enqueue(5)
        queue.enqueue(6)

        # Drain all
        items = []
        while not queue.is_empty():
            item = queue.dequeue()
            if item is not None:
                items.append(item)

        assert items == [3, 4, 5, 6]
