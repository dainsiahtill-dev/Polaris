"""Exponential backoff utilities for KernelOne resilience layer.

Provides deterministic exponential-backoff computation and a configurable
:class:`BackoffController` that wraps the ClockPort protocol. This eliminates
direct ``time.sleep`` calls throughout the codebase, replacing them with
injectable clock-aware backoff so that unit tests never need to patch
``time.sleep`` directly.

Usage::

    from polaris.kernelone.common.clock import RealClock, MockClock
    from polaris.kernelone.resilience.backoff import BackoffController

    # Production
    bc = BackoffController(clock=RealClock())

    # Retry loop
    for attempt in range(max_retries):
        try:
            do_work()
            bc.on_success()
            break
        except (RuntimeError, ValueError):
            bc.on_failure()
            bc.sleep_until_ready()

    # Unit test
    mock = MockClock()
    bc = BackoffController(clock=mock)
    # ...exercise code...
    mock.advance(1.0)  # advance time to simulate sleep
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.common.clock import ClockPort

__all__ = ["BackoffController", "build_backoff_seconds"]


def build_backoff_seconds(
    *,
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    """Compute exponential backoff seconds with jitter for a single attempt.

    Args:
        attempt: 1-based retry attempt number.
        base_delay_seconds: Starting delay (e.g. 0.5).
        max_delay_seconds: Upper bound on returned value.

    Returns:
        Seconds to sleep, capped at *max_delay_seconds* and with up to 20%
        positive jitter to spread retry load.
    """
    exp_delay = base_delay_seconds * (2 ** max(0, attempt - 1))
    bounded = min(max_delay_seconds, max(base_delay_seconds, exp_delay))
    jitter = random.uniform(0.0, bounded * 0.2)
    return bounded + jitter


class BackoffController:
    """Stateful exponential-backoff controller with injectable clock.

    Tracks consecutive failures and produces deterministic (jittered) sleep
    durations on each :meth:`on_failure` call. A successful :meth:`on_success`
    resets the internal failure counter so backoff restarts from the beginning.

    All time operations are delegated to the injected :class:`ClockPort`, making
    this class fully testable without patching ``time`` at all.
    """

    __slots__ = (
        "_base_delay",
        "_clock",
        "_current_delay",
        "_failures",
        "_max_attempts",
        "_max_delay",
    )

    def __init__(
        self,
        clock: ClockPort,
        *,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 60.0,
        max_attempts: int = 10,
    ) -> None:
        """Initialize the backoff controller.

        Args:
            clock: Injected time provider (e.g. :class:`~.clock.RealClock`
                or :class:`~.clock.MockClock`).
            base_delay_seconds: Initial delay on the first failure.
            max_delay_seconds: Upper bound on any single sleep duration.
            max_attempts: Number of consecutive failures before the caller
                should treat this as exhausted (tracked externally via
                :attr:`failure_count`).
        """
        self._clock = clock
        self._base_delay = float(base_delay_seconds)
        self._max_delay = float(max_delay_seconds)
        self._max_attempts = int(max_attempts)
        self._failures: int = 0
        self._current_delay: float = self._base_delay

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def failure_count(self) -> int:
        """Number of consecutive failures since the last success."""
        return self._failures

    @property
    def current_delay(self) -> float:
        """Delay (in seconds) that will be used for the next sleep."""
        return self._current_delay

    @property
    def is_exhausted(self) -> bool:
        """True when *failure_count* has reached *max_attempts*."""
        return self._failures >= self._max_attempts

    def on_failure(self) -> float:
        """Record a failure and advance the backoff schedule.

        Returns:
            The delay (in seconds) the caller should sleep for before
            the next attempt.
        """
        self._failures += 1
        delay = min(self._current_delay, self._max_delay)
        self._current_delay = min(
            self._current_delay * 2.0,
            self._max_delay,
        )
        return delay

    def on_success(self) -> None:
        """Reset the backoff schedule after a successful operation."""
        self._failures = 0
        self._current_delay = self._base_delay

    def sleep_until_ready(self) -> None:
        """Block for the current backoff delay using the injected clock."""
        self._clock.sleep(self._current_delay)
