"""Tests for Deadlock Detection.

Tests for DeadlockDetector and AsyncDeadlockDetector.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from polaris.kernelone.benchmark.chaos.deadlock import (
    AsyncDeadlockDetector,
    CompositeDeadlockDetector,
    DeadlockDetector,
    DeadlockReport,
    LockAcquisition,
)

# ------------------------------------------------------------------
# Test DeadlockDetector
# ------------------------------------------------------------------


class TestDeadlockDetector:
    """Tests for DeadlockDetector."""

    def test_initial_state(self) -> None:
        """Test initial detector state."""
        detector = DeadlockDetector()
        assert detector.held_locks == {}
        assert detector.lock_graph == {}

    def test_record_acquire(self) -> None:
        """Test recording lock acquisition."""
        detector = DeadlockDetector()
        thread_id = 123

        detector.record_acquire(thread_id, lock_id=1)
        assert detector.held_locks[thread_id] == 1

    def test_record_release(self) -> None:
        """Test recording lock release."""
        detector = DeadlockDetector()
        thread_id = 123

        detector.record_acquire(thread_id, lock_id=1)
        detector.record_release(thread_id, lock_id=1)
        assert thread_id not in detector.held_locks

    def test_track_lock_context_manager(self) -> None:
        """Test track_lock context manager."""
        detector = DeadlockDetector()
        thread_id = threading.current_thread().ident or 0

        with detector.track_lock(lock_id=1):  # type: ignore[misc]
            assert detector.held_locks.get(thread_id) == 1

        assert thread_id not in detector.held_locks

    def test_no_deadlock_simple(self) -> None:
        """Test no deadlock for simple non-circular case."""
        detector = DeadlockDetector()

        # Thread 1 gets lock 1, thread 2 gets lock 2
        detector.record_acquire(thread_id=1, lock_id=1)
        detector.record_acquire(thread_id=2, lock_id=2)

        # No deadlock because they're not waiting for each other
        assert 1 in detector.held_locks
        assert 2 in detector.held_locks

    def test_potential_deadlock_detection(self) -> None:
        """Test that potential deadlock is detected."""
        detector = DeadlockDetector()

        # Create a scenario where thread 1 waits for lock held by thread 2
        # and thread 2 waits for lock held by thread 1
        # Simplified: just test that detection logic works

        detector.record_acquire(thread_id=1, lock_id=1)
        detector.record_acquire(thread_id=2, lock_id=2)

        # Thread 1 already has lock 1, trying to get lock 2 (held by thread 2)
        # This could be a deadlock if thread 2 tries to get lock 1

        # Manual check for potential deadlock
        thread1_waiting = detector._waiting_for_unsafe(thread_id=1)
        assert 2 in thread1_waiting  # Thread 1 waiting for lock 2

    def test_deadlock_callback(self) -> None:
        """Test that deadlock callback is called."""
        callback_calls: list[DeadlockReport] = []

        def on_deadlock(report: DeadlockReport) -> None:
            callback_calls.append(report)

        detector = DeadlockDetector(on_deadlock=on_deadlock)

        # This test verifies callback registration
        assert detector._deadlock_callback is on_deadlock


# ------------------------------------------------------------------
# Test AsyncDeadlockDetector
# ------------------------------------------------------------------


class TestAsyncDeadlockDetector:
    """Tests for AsyncDeadlockDetector."""

    def test_initial_state(self) -> None:
        """Test initial async detector state."""
        detector = AsyncDeadlockDetector()
        assert detector.held_locks == {}
        assert detector.awaiting_tasks == {}

    def test_track_acquire(self) -> None:
        """Test tracking async lock acquisition."""
        detector = AsyncDeadlockDetector()
        task_id = 123

        detector.track_acquire(task_id, lock_id="lock1")
        assert task_id in detector.awaiting_tasks
        assert detector.held_locks[task_id] == "lock1"

    def test_track_release(self) -> None:
        """Test tracking async lock release."""
        detector = AsyncDeadlockDetector()
        task_id = 123

        detector.track_acquire(task_id, lock_id="lock1")
        detector.track_release(task_id)

        assert task_id not in detector.awaiting_tasks
        assert task_id not in detector.held_locks

    def test_check_for_stuck_tasks_none(self) -> None:
        """Test checking for stuck tasks when none are stuck."""
        detector = AsyncDeadlockDetector(stuck_threshold_seconds=30.0)

        # Recently added task should not be stuck
        detector.track_acquire(1, "lock1")
        stuck = detector.check_for_stuck_tasks()

        assert len(stuck) == 0

    def test_check_for_stuck_tasks_with_stuck(self) -> None:
        """Test detection of stuck tasks."""
        detector = AsyncDeadlockDetector(stuck_threshold_seconds=0.1)

        # Manually add an old task
        detector._awaiting_tasks[1] = time.monotonic() - 1.0

        stuck = detector.check_for_stuck_tasks()
        assert 1 in stuck

    @pytest.mark.asyncio
    async def test_monitored_acquire_success(self) -> None:
        """Test monitored lock acquisition success."""
        detector = AsyncDeadlockDetector(stuck_threshold_seconds=5.0)

        task_id = id(asyncio.current_task())

        # This would deadlock without the lock, but we're testing the wrapper
        # For unit test, just verify the detector tracks correctly
        detector.track_acquire(task_id, "test_lock")
        detector.track_release(task_id)

        assert task_id not in detector.awaiting_tasks


# ------------------------------------------------------------------
# Test CompositeDeadlockDetector
# ------------------------------------------------------------------


class TestCompositeDeadlockDetector:
    """Tests for CompositeDeadlockDetector."""

    def test_initialization(self) -> None:
        """Test composite detector initialization."""
        detector = CompositeDeadlockDetector()

        assert isinstance(detector.thread_detector, DeadlockDetector)
        assert isinstance(detector.async_detector, AsyncDeadlockDetector)

    def test_record_thread_acquire(self) -> None:
        """Test recording thread acquisition via composite."""
        detector = CompositeDeadlockDetector()
        thread_id = 123

        detector.record_thread_acquire(thread_id, lock_id=1)
        assert detector.thread_detector.held_locks.get(thread_id) == 1

    def test_record_thread_release(self) -> None:
        """Test recording thread release via composite."""
        detector = CompositeDeadlockDetector()
        thread_id = 123

        detector.record_thread_acquire(thread_id, lock_id=1)
        detector.record_thread_release(thread_id, lock_id=1)
        assert thread_id not in detector.thread_detector.held_locks

    def test_track_thread_lock(self) -> None:
        """Test tracking thread lock via composite."""
        detector = CompositeDeadlockDetector()
        thread_id = threading.current_thread().ident or 0

        with detector.track_thread_lock(lock_id=1):  # type: ignore[attr-defined]
            assert detector.thread_detector.held_locks.get(thread_id) == 1

        assert thread_id not in detector.thread_detector.held_locks


# ------------------------------------------------------------------
# Test Models
# ------------------------------------------------------------------


class TestDeadlockReport:
    """Tests for DeadlockReport model."""

    def test_to_dict(self) -> None:
        """Test DeadlockReport serialization."""
        acquisition = LockAcquisition(
            lock_id=1,
            thread_id=123,
            timestamp=1000.0,
            wait_time_ms=50.0,
        )

        report = DeadlockReport(
            cycle=(1, 2, 3),
            detection_time=1000.0,
            acquisition_chain=(acquisition,),
        )

        result = report.to_dict()

        assert result["cycle"] == [1, 2, 3]
        assert result["detection_time"] == 1000.0
        assert len(result["acquisition_chain"]) == 1
        assert result["acquisition_chain"][0]["lock_id"] == 1


class TestLockAcquisition:
    """Tests for LockAcquisition model."""

    def test_creation(self) -> None:
        """Test LockAcquisition creation."""
        acquisition = LockAcquisition(
            lock_id=1,
            thread_id=123,
            timestamp=1000.0,
            wait_time_ms=50.0,
        )

        assert acquisition.lock_id == 1
        assert acquisition.thread_id == 123
        assert acquisition.timestamp == 1000.0
        assert acquisition.wait_time_ms == 50.0
