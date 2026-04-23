"""Retry policy module that auto-binds Polaris ErrorCategory to retry decisions.

This module provides a lightweight, policy-driven retry layer on top of the
existing :func:`~polaris.kernelone.errors.classify_error` and
:func:`~polaris.kernelone.resilience.backoff.build_backoff_seconds`
primitives.  It is intentionally decoupled from :class:`BackoffController` and
:class:`SelfHealingExecutor` so that callers can choose the abstraction level
that fits their use-case.

Usage::

    from polaris.kernelone.resilience.retry_policy import (
        RetryPolicy,
        RetryContext,
        should_retry,
        compute_delay,
        execute_with_retry,
    )

    policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.5)

    result = await execute_with_retry(
        some_async_operation,
        policy,
        "arg1",
        keyword="value",
    )
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from polaris.kernelone.errors import ErrorCategory, classify_error
from polaris.kernelone.resilience.backoff import build_backoff_seconds

__all__ = [
    "RetryContext",
    "RetryPolicy",
    "compute_delay",
    "execute_with_retry",
    "should_retry",
]

T = TypeVar("T")

# Default set of categories considered safe to retry.  Callers may override
# via :attr:`RetryPolicy.transient_categories`.
_DEFAULT_TRANSIENT_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.NETWORK_ERROR,
        ErrorCategory.TRANSIENT_NETWORK,
        ErrorCategory.TRANSIENT_RATE_LIMIT,
        ErrorCategory.TRANSIENT_RESOURCE,
        ErrorCategory.SERVICE_UNAVAILABLE,
        ErrorCategory.TEMPORARY_FAILURE,
        ErrorCategory.SYSTEM_TIMEOUT,
        ErrorCategory.SYSTEM_CAPACITY,
        ErrorCategory.SYSTEM_UNKNOWN,
        ErrorCategory.UNAVAILABLE,
        ErrorCategory.DEADLINE_EXCEEDED,
        ErrorCategory.TRANSPORT_ERROR,
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable retry configuration.

    Attributes:
        max_attempts: Maximum number of attempts (including the first).
            Must be >= 1.
        base_delay_seconds: Starting delay for the first retry (attempt 1).
        max_delay_seconds: Upper bound on any single computed delay.
        jitter_ratio: Maximum fraction of the computed delay to add as
            random jitter (e.g. 0.2 means up to 20% extra).
        transient_categories: Set of :class:`ErrorCategory` values that
            should be retried.  Defaults to a broad transient set.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    jitter_ratio: float = 0.2
    transient_categories: frozenset[ErrorCategory] = field(default_factory=lambda: _DEFAULT_TRANSIENT_CATEGORIES)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if not (0.0 <= self.jitter_ratio <= 1.0):
            raise ValueError("jitter_ratio must be in [0.0, 1.0]")


@dataclass
class RetryContext:
    """Mutable state tracking for an ongoing retry sequence.

    Attributes:
        attempt_count: Number of attempts made so far (starts at 0).
        cumulative_delay: Total seconds spent sleeping across all retries.
        last_error: The most recent exception that triggered a retry.
    """

    attempt_count: int = 0
    cumulative_delay: float = 0.0
    last_error: Exception | None = None


def should_retry(error: Exception, policy: RetryPolicy) -> bool:
    """Determine whether *error* qualifies for a retry under *policy*.

    Uses :func:`polaris.kernelone.errors.classify_error` to map the exception
    to an :class:`ErrorCategory`, then checks membership in
    :attr:`RetryPolicy.transient_categories`.

    Args:
        error: The exception to evaluate.
        policy: The retry policy governing the decision.

    Returns:
        ``True`` if the error category is in the policy's transient set.
    """
    category = classify_error(error)
    return category in policy.transient_categories


def compute_delay(policy: RetryPolicy, attempt: int) -> float:
    """Compute the delay (in seconds) before the next retry attempt.

    Delegates to :func:`build_backoff_seconds` and enforces the policy's
    ``jitter_ratio`` by scaling the built-in 20% jitter cap proportionally.
    When ``jitter_ratio`` is exactly ``0.2`` the behaviour is identical to
    the underlying utility.

    Args:
        policy: The retry policy providing bounds.
        attempt: 1-based retry attempt number.

    Returns:
        Seconds to wait before the next attempt.
    """
    raw = build_backoff_seconds(
        attempt=attempt,
        base_delay_seconds=policy.base_delay_seconds,
        max_delay_seconds=policy.max_delay_seconds,
    )
    # build_backoff_seconds already applies up to 20% jitter.  If the policy
    # requests a different ratio we scale the jitter component accordingly.
    if policy.jitter_ratio == 0.2:
        return raw

    # Reverse-engineer the bounded exponential component (no jitter).
    exp_delay = policy.base_delay_seconds * (2 ** max(0, attempt - 1))
    bounded = min(policy.max_delay_seconds, max(policy.base_delay_seconds, exp_delay))

    # The raw value is bounded + up to 0.2*bounded jitter.
    # Recompute with the policy's jitter_ratio.
    jitter = bounded * policy.jitter_ratio
    return bounded + jitter


async def execute_with_retry(
    operation: Callable[..., Awaitable[T]],
    policy: RetryPolicy,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute an async operation with automatic retry on transient errors.

    The function attempts *operation* up to *policy.max_attempts* times.
    Between attempts it sleeps for the computed delay.  If the final attempt
    fails, the last exception is re-raised.

    Args:
        operation: Async callable to execute.
        policy: Retry configuration.
        *args: Positional arguments forwarded to *operation*.
        **kwargs: Keyword arguments forwarded to *operation*.

    Returns:
        The result of *operation* on success.

    Raises:
        Exception: The last exception raised if all attempts are exhausted.
    """
    ctx = RetryContext()
    last_error: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        ctx.attempt_count = attempt
        try:
            return await operation(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            ctx.last_error = exc

            if not should_retry(exc, policy):
                raise

            if attempt < policy.max_attempts:
                delay = compute_delay(policy, attempt)
                ctx.cumulative_delay += delay
                await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error

    # Defensive fallback — should never be reached because the loop above
    # either returns or raises, but mypy needs a guaranteed raise path.
    raise RuntimeError("execute_with_retry exhausted all attempts without an error")
