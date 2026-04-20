"""Backward-compatibility shim for error classification types.

The canonical implementations of `ErrorCategory`, `ErrorClassifier`,
`ErrorRecord`, and `RecoveryRecommendation` now live in:

    polaris.cells.orchestration.shared_types

This module re-exports those types verbatim so that existing importers
(tests, other internal modules) continue to work without modification.

`ExponentialBackoff`, `CircuitBreaker`, and `RetryExecutor` remain here
because they are pm_dispatch-internal utilities with no cross-cell exposure.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from typing import TYPE_CHECKING, Any, TypeVar

# Re-export canonical cross-cell types from neutral shared location.
from polaris.cells.orchestration.shared_types import (
    ErrorCategory,
    ErrorClassifier,
    ErrorRecord,
    RecoveryRecommendation,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ExponentialBackoff:
    """Exponential backoff with jitter for retry delays."""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ) -> None:
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given retry attempt."""
        delay = self.base_delay * (self.exponential_base**attempt)
        delay = min(delay, self.max_delay)
        if self.jitter:
            jitter_factor = 0.75 + random.random() * 0.5
            delay *= jitter_factor
        return delay


class CircuitBreaker:
    """Circuit breaker pattern for preventing cascade failures.

    States:
    - CLOSED: Normal operation, requests pass through.
    - OPEN: Failure threshold exceeded, requests fail fast.
    - HALF_OPEN: Testing if service has recovered.
    """

    class State(enum.Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = self.State.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitBreaker.State:
        """Get current circuit state, transitioning OPEN -> HALF_OPEN if ready."""
        if self._state == self.State.OPEN and self._last_failure_time is not None:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = self.State.HALF_OPEN
                self._half_open_calls = 0
                logger.info("Circuit %s entering HALF_OPEN state", self.name)
        return self._state

    def can_execute(self) -> bool:
        """Return True if a call is allowed through the circuit breaker."""
        state = self.state
        if state == self.State.CLOSED:
            return True
        if state == self.State.OPEN:
            logger.warning("Circuit %s is OPEN, failing fast", self.name)
            return False
        # HALF_OPEN: allow a limited probe burst
        if self._half_open_calls < self.half_open_max_calls:
            self._half_open_calls += 1
            return True
        return False

    def record_success(self) -> None:
        """Record a successful execution."""
        if self._state == self.State.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max_calls:
                logger.info("Circuit %s closing (recovered)", self.name)
                self._reset()
        else:
            self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self) -> None:
        """Record a failed execution."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._state == self.State.HALF_OPEN:
            logger.warning("Circuit %s opening (failure in HALF_OPEN)", self.name)
            self._state = self.State.OPEN
        elif self._failure_count >= self.failure_threshold and self._state == self.State.CLOSED:
            logger.warning("Circuit %s opening (%d failures)", self.name, self._failure_count)
            self._state = self.State.OPEN

    def _reset(self) -> None:
        self._state = self.State.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._half_open_calls = 0


class RetryExecutor:
    """Execute coroutines with retry logic based on error classification."""

    def __init__(
        self,
        name: str,
        circuit_breaker: CircuitBreaker | None = None,
        on_retry: Callable[[int, Exception, float], None] | None = None,
    ) -> None:
        self.name = name
        self.circuit_breaker = circuit_breaker
        self.on_retry = on_retry

    async def execute(
        self,
        coro: Callable[[], Coroutine[Any, Any, T]],
        max_retries: int | None = None,
        base_delay: float = 1.0,
    ) -> T:
        """Execute a coroutine with retry logic."""
        attempt = 0

        while True:
            if self.circuit_breaker and not self.circuit_breaker.can_execute():
                raise RuntimeError(f"Circuit breaker {self.circuit_breaker.name} is OPEN")

            try:
                result = await coro()
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()
                return result
            except Exception as exc:
                attempt += 1
                category, recommendation = ErrorClassifier.analyze(exc)
                effective_max = max_retries if max_retries is not None else recommendation.max_retries
                if not recommendation.can_retry or attempt > effective_max:
                    logger.error(
                        "[%s] Non-retryable error or max retries exceeded: %s",
                        self.name,
                        exc,
                    )
                    if self.circuit_breaker:
                        self.circuit_breaker.record_failure()
                    raise

                backoff = ExponentialBackoff(base_delay=base_delay)
                delay = max(
                    backoff.calculate_delay(attempt - 1),
                    recommendation.retry_delay_seconds,
                )
                logger.warning(
                    "[%s] Attempt %d failed (%s): %s. Retrying in %.2fs...",
                    self.name,
                    attempt,
                    category.value,
                    exc,
                    delay,
                )
                if self.on_retry:
                    self.on_retry(attempt, exc, delay)
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()
                await asyncio.sleep(delay)


__all__ = [
    "CircuitBreaker",
    "ErrorCategory",
    "ErrorClassifier",
    "ErrorRecord",
    "ExponentialBackoff",
    "RecoveryRecommendation",
    "RetryExecutor",
]
