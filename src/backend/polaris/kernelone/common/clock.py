"""Time abstraction layer for KernelOne.

Provides ClockPort protocol for time/sleep abstraction, enabling testable code
that does not depend on real-time delays. Eliminates the need for
``patch("time.sleep")`` patterns in unit tests.

Usage::

    from polaris.kernelone.common.clock import ClockPort, RealClock, MockClock

    class MyService:
        def __init__(self, clock: ClockPort | None = None):
            self._clock: ClockPort | None = clock or RealClock()

        def do_work(self) -> None:
            start = self._clock.time()
            self._clock.sleep(1.0)
            elapsed = self._clock.time() - start

    # Production
    service = MyService()

    # Test with controlled time
    mock = MockClock()
    service = MyService(clock=mock)
    mock.advance(2.0)  # simulates 2 seconds passing
"""

from __future__ import annotations

import time
from typing import Protocol

__all__ = ["ClockPort", "MockClock", "RealClock"]


class ClockPort(Protocol):
    """Protocol for time abstraction.

    Allows injecting test doubles for time/sleep in tests,
    eliminating the need for ``patch("time.sleep")`` patterns.
    """

    def time(self) -> float:
        """Return the current time in seconds since epoch (like ``time.time()``)."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for approximately *seconds* (like ``time.sleep()``)."""
        ...


class RealClock:
    """Production clock backed by the real :mod:`time` module."""

    __slots__ = ()

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class MockClock:
    """Deterministic clock for unit tests.

    Simulates time passage without any real blocking.
    Time only advances when :meth:`advance` is called.

    Attributes:
        auto_sleep: If True, :meth:`sleep` advances the clock by the
            requested duration. If False (default), :meth:`sleep` is a no-op.
            Default is False so that callers must explicitly call
            :meth:`advance` to move time forward, which is the safest
            pattern for deterministic tests.
    """

    __slots__ = ("_auto_sleep", "_now", "_sleep_calls", "_time_calls")

    def __init__(self, initial_time: float = 0.0, *, auto_sleep: bool = False) -> None:
        self._now: float = initial_time
        self._sleep_calls: list[float] = []
        self._time_calls: list[float] = []
        self._auto_sleep: bool = auto_sleep

    # ------------------------------------------------------------------
    # ClockPort interface
    # ------------------------------------------------------------------

    def time(self) -> float:
        """Return the current simulated time."""
        self._time_calls.append(self._now)
        return self._now

    def sleep(self, seconds: float) -> None:
        """Record the requested sleep duration.

        Does NOT advance the clock unless ``auto_sleep=True``.
        Call :meth:`advance` to move time forward.
        """
        self._sleep_calls.append(seconds)
        if self._auto_sleep:
            self._now += seconds

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def sleep_calls(self) -> list[float]:
        """List of sleep durations requested so far (read-only view)."""
        return list(self._sleep_calls)

    @property
    def time_calls(self) -> list[float]:
        """List of time() return values recorded so far (read-only view)."""
        return list(self._time_calls)

    def advance(self, seconds: float) -> None:
        """Advance the simulated clock by *seconds*."""
        if seconds < 0:
            raise ValueError(f"advance() cannot accept negative value: {seconds!r}")
        self._now += seconds

    def set_time(self, seconds: float) -> None:
        """Set the simulated clock to an absolute value."""
        self._now = float(seconds)

    def reset(self) -> None:
        """Clear all recorded calls and reset time to 0."""
        self._now = 0.0
        self._sleep_calls.clear()
        self._time_calls.clear()

    @property
    def now(self) -> float:
        """Current simulated time (read-only)."""
        return self._now
