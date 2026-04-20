"""Deadlock Detection for Chaos Testing.

This module provides deadlock detection capabilities for concurrent
operations, including lock graph analysis and cycle detection.

Example
-------
    detector = DeadlockDetector()
    with detector.track_lock(lock_id=1):
        with detector.track_lock(lock_id=2):
            # Operations under lock protection
            pass
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.errors import DeadlockDetectedError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LockAcquisition:
    """Record of a lock acquisition."""

    lock_id: int
    thread_id: int
    timestamp: float
    wait_time_ms: float


@dataclass(frozen=True, kw_only=True)
class DeadlockReport:
    """Report of detected deadlock."""

    cycle: tuple[int, ...]
    detection_time: float
    acquisition_chain: tuple[LockAcquisition, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "cycle": list(self.cycle),
            "detection_time": self.detection_time,
            "acquisition_chain": [
                {
                    "lock_id": a.lock_id,
                    "thread_id": a.thread_id,
                    "timestamp": a.timestamp,
                    "wait_time_ms": a.wait_time_ms,
                }
                for a in self.acquisition_chain
            ],
        }


# ------------------------------------------------------------------
# Thread-based Deadlock Detector
# ------------------------------------------------------------------


class DeadlockDetector:
    """Detects deadlocks using wait-for graph analysis.

    This detector tracks lock acquisitions across threads and
    detects cycles in the wait-for graph.

    Attributes:
        check_interval_ms: How often to check for deadlocks.
    """

    __slots__ = (
        "_acquisitions",
        "_check_interval_ms",
        "_deadlock_callback",
        "_held_locks",
        "_lock",
        "_lock_graph",
        "_monitor_thread",
        "_running",
    )

    def __init__(
        self,
        check_interval_ms: float = 100.0,
        on_deadlock: Callable[[DeadlockReport], None] | None = None,
    ) -> None:
        self._lock_graph: dict[int, set[int]] = {}
        self._held_locks: dict[int, int] = {}  # thread_id -> lock_id
        self._acquisitions: list[LockAcquisition] = []
        self._monitor_thread: threading.Thread | None = None
        self._running = False
        self._check_interval_ms = check_interval_ms
        self._lock = threading.Lock()
        self._deadlock_callback = on_deadlock

    @property
    def held_locks(self) -> dict[int, int]:
        """Get currently held locks."""
        with self._lock:
            return dict(self._held_locks)

    @property
    def lock_graph(self) -> dict[int, set[int]]:
        """Get current lock graph."""
        with self._lock:
            return {t: set(locks) for t, locks in self._lock_graph.items()}

    def record_acquire(self, thread_id: int, lock_id: int) -> None:
        """Record a lock acquisition.

        Args:
            thread_id: ID of the thread acquiring the lock.
            lock_id: ID of the lock being acquired.

        Raises:
            DeadlockDetectedError: If a deadlock is detected.
        """
        now = time.monotonic()

        with self._lock:
            # Initialize thread entry
            if thread_id not in self._lock_graph:
                self._lock_graph[thread_id] = set()

            # Build wait-for graph and check for cycles
            waiting_for = self._waiting_for_unsafe(thread_id)
            if lock_id in waiting_for:
                cycle = self._find_cycle_unsafe(thread_id, lock_id)
                report = DeadlockReport(
                    cycle=tuple(cycle),
                    detection_time=now,
                    acquisition_chain=tuple(self._acquisitions[-len(cycle) :]),
                )
                if self._deadlock_callback:
                    self._deadlock_callback(report)
                raise DeadlockDetectedError(
                    f"Deadlock detected: thread {thread_id} waiting for lock {lock_id}",
                    cycle=cycle,
                    involved_threads=list(self._lock_graph.keys()),
                )

            # Record acquisition
            self._held_locks[thread_id] = lock_id
            self._acquisitions.append(
                LockAcquisition(
                    lock_id=lock_id,
                    thread_id=thread_id,
                    timestamp=now,
                    wait_time_ms=0.0,
                )
            )

    def record_release(self, thread_id: int, lock_id: int) -> None:
        """Record a lock release.

        Args:
            thread_id: ID of the thread releasing the lock.
            lock_id: ID of the lock being released.
        """
        with self._lock:
            if self._held_locks.get(thread_id) == lock_id:
                del self._held_locks[thread_id]

    def _waiting_for_unsafe(self, thread_id: int) -> set[int]:
        """Build wait-for set (must hold lock)."""
        waiting = set()
        for other_thread, held_lock in self._held_locks.items():
            if other_thread != thread_id:
                waiting.add(held_lock)
        return waiting

    def _find_cycle_unsafe(self, start_thread: int, start_lock: int) -> list[int]:
        """Find cycle involving thread and lock (must hold lock)."""
        # Simple cycle: start_thread -> start_lock -> holder_thread -> ...
        cycle = [start_thread]
        for thread_id, lock_id in self._held_locks.items():
            if lock_id == start_lock:
                cycle.append(thread_id)
                break
        return cycle

    def start_monitoring(self) -> None:
        """Start background deadlock monitoring thread."""
        if self._running:
            return

        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
        )
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                self._check_deadlock()
            except DeadlockDetectedError:
                raise
            except (RuntimeError, ValueError) as e:
                logger.debug("Deadlock check error (continuing monitoring): %s", e)
            time.sleep(self._check_interval_ms / 1000.0)

    def _check_deadlock(self) -> None:
        """Check for deadlocks in the current lock graph."""
        with self._lock:
            if not self._held_locks:
                return

    @contextmanager
    def track_lock(
        self,
        lock_id: int,
        timeout: float | None = None,
    ) -> Iterator[None]:
        """Context manager for tracking lock acquisitions.

        Args:
            lock_id: ID of the lock to track.
            timeout: Optional timeout for acquisition.

        Yields:
            None when lock is acquired.

        Example
        -------
            with detector.track_lock(lock_id=1):
                # Lock is being tracked
                pass
        """
        thread_id = threading.current_thread().ident or 0

        try:
            self.record_acquire(thread_id, lock_id)
            yield
        finally:
            self.record_release(thread_id, lock_id)


# ------------------------------------------------------------------
# Async Lock Tracker
# ------------------------------------------------------------------


class AsyncDeadlockDetector:
    """Deadlock detector for asyncio-based code.

    Uses the event loop to detect async lock contention.
    """

    __slots__ = (
        "_awaiting_tasks",
        "_check_count",
        "_held_async_locks",
        "_stuck_threshold_s",
    )

    def __init__(self, stuck_threshold_seconds: float = 30.0) -> None:
        self._awaiting_tasks: dict[int, float] = {}  # task_id -> start_time
        self._held_async_locks: dict[int, str] = {}  # task_id -> lock_id
        self._check_count = 0
        self._stuck_threshold_s = stuck_threshold_seconds

    def track_acquire(self, task_id: int, lock_id: str) -> None:
        """Track async lock acquisition."""
        self._awaiting_tasks[task_id] = time.monotonic()
        self._held_async_locks[task_id] = lock_id

    def track_release(self, task_id: int) -> None:
        """Track async lock release."""
        self._awaiting_tasks.pop(task_id, None)
        self._held_async_locks.pop(task_id, None)

    def check_for_stuck_tasks(self) -> list[int]:
        """Check for stuck tasks.

        Returns:
            List of stuck task IDs.
        """
        now = time.monotonic()
        stuck = []

        for task_id, start_time in list(self._awaiting_tasks.items()):
            if now - start_time > self._stuck_threshold_s:
                stuck.append(task_id)

        self._check_count += 1
        return stuck

    async def monitored_acquire(
        self,
        lock: asyncio.Lock,
        task_id: int,
        lock_id: str,
    ) -> None:
        """Acquire a lock with monitoring.

        Args:
            lock: The asyncio.Lock to acquire.
            task_id: ID to track this acquisition.
            lock_id: String identifier for the lock.

        Raises:
            TimeoutError: If acquisition takes too long.
        """
        self.track_acquire(task_id, lock_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._stuck_threshold_s)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"Lock acquisition timed out for task {task_id}, lock {lock_id}") from e
        finally:
            self.track_release(task_id)

    @property
    def held_locks(self) -> dict[int, str]:
        """Get currently held async locks."""
        return dict(self._held_async_locks)

    @property
    def awaiting_tasks(self) -> dict[int, float]:
        """Get tasks awaiting locks."""
        return dict(self._awaiting_tasks)


# ------------------------------------------------------------------
# Composite Deadlock Detector
# ------------------------------------------------------------------


class CompositeDeadlockDetector:
    """Combines thread and async deadlock detection."""

    def __init__(self) -> None:
        self._thread_detector = DeadlockDetector()
        self._async_detector = AsyncDeadlockDetector()

    def record_thread_acquire(self, thread_id: int, lock_id: int) -> None:
        """Record thread-based lock acquisition."""
        self._thread_detector.record_acquire(thread_id, lock_id)

    def record_thread_release(self, thread_id: int, lock_id: int) -> None:
        """Record thread-based lock release."""
        self._thread_detector.record_release(thread_id, lock_id)

    @contextmanager
    def track_thread_lock(self, lock_id: int) -> Iterator[None]:
        """Track thread lock with automatic release."""
        with self._thread_detector.track_lock(lock_id):
            yield

    @property
    def thread_detector(self) -> DeadlockDetector:
        """Get thread-based detector."""
        return self._thread_detector

    @property
    def async_detector(self) -> AsyncDeadlockDetector:
        """Get async-based detector."""
        return self._async_detector
