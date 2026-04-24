"""Self-healing executor with automatic retry and alternative strategy support.

This module provides the :class:`SelfHealingExecutor` that wraps operations
with automatic retry logic, failure classification, and fallback to alternative
strategies when the primary approach fails.

Failure Classification:
    - TRANSIENT: Temporary failures (network timeout, rate limit) that can be retried
    - PERMANENT: Permanent failures (validation error, auth failure) that need strategy change
    - UNKNOWN: Unclassified failures treated as potentially transient

Usage::

    from polaris.kernelone.resilience.self_healing import (
        SelfHealingExecutor,
        RetryStrategy,
        AlternativeStrategy,
        HealingResult,
    )

    executor = SelfHealingExecutor(
        retry_strategy=RetryStrategy(max_attempts=3),
        alternatives=[
            AlternativeStrategy(
                name="fallback",
                description="Use cached result",
                execute=fetch_from_cache,
            )
        ],
    )

    result = await executor.execute(primary_func, *args, **kwargs)
    if result.success:
        use(result.final_result)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, TypeVar

from polaris.kernelone.errors import ErrorCategory, KernelOneError

__all__ = [
    "AlternativeStrategy",
    "FailureType",
    "HealingResult",
    "RetryStrategy",
    "SelfHealingExecutor",
]

T = TypeVar("T")


class FailureType(Enum):
    """Classification of failure types for retry decisions.

    Attributes:
        TRANSIENT: Temporary failure that may succeed on retry.
            Examples: network timeout, rate limit, temporary unavailable.
        PERMANENT: Failure that will not be resolved by retry.
            Examples: validation error, authentication failure, not found.
        UNKNOWN: Unclassified failure; treated as potentially transient.
    """

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RetryStrategy:
    """Configuration for retry behavior.

    Attributes:
        max_attempts: Maximum number of retry attempts (default 3).
        base_delay: Initial delay in seconds before first retry (default 1.0).
        exponential_base: Multiplier for delay on each attempt (default 2.0).
        max_delay: Maximum delay cap in seconds (default 30.0).
        jitter: Whether to add random jitter to delays (default True).
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    exponential_base: float = 2.0
    max_delay: float = 30.0
    jitter: bool = True


@dataclass
class AlternativeStrategy:
    """A fallback strategy to try when primary strategy exhausts retries.

    Attributes:
        name: Unique identifier for this strategy.
        description: Human-readable description of what this strategy does.
        execute: Async callable to execute as fallback.
    """

    name: str
    description: str
    execute: Callable[..., Any]


@dataclass(frozen=True)
class HealingResult:
    """Result of a self-healing execution attempt.

    Attributes:
        success: Whether the execution ultimately succeeded.
        final_result: The result value if successful, None otherwise.
        attempts: Total number of attempts made (retries + alternatives).
        strategies_tried: Tuple of strategy names that were attempted.
        final_error: Error message if all strategies failed.
    """

    success: bool
    final_result: Any | None
    attempts: int
    strategies_tried: tuple[str, ...]
    final_error: str | None


# Transient error categories that are safe to retry
_TRANSIENT_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.TRANSIENT_NETWORK,
        ErrorCategory.TRANSIENT_RATE_LIMIT,
        ErrorCategory.TRANSIENT_RESOURCE,
        ErrorCategory.SERVICE_UNAVAILABLE,
        ErrorCategory.TEMPORARY_FAILURE,
        ErrorCategory.SYSTEM_TIMEOUT,
        ErrorCategory.SYSTEM_CAPACITY,
        ErrorCategory.SYSTEM_UNKNOWN,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.TIMEOUT,
    }
)

# Permanent error categories that should not be retried
_PERMANENT_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.PERMANENT_AUTH,
        ErrorCategory.PERMANENT_VALIDATION,
        ErrorCategory.PERMANENT_NOT_FOUND,
        ErrorCategory.PERMANENT_CONFLICT,
        ErrorCategory.INVALID_INPUT,
        ErrorCategory.NOT_FOUND,
        ErrorCategory.ALREADY_EXISTS,
        ErrorCategory.PERMISSION_DENIED,
        ErrorCategory.FAILED_PRECONDITION,
        ErrorCategory.UNIMPLEMENTED,
        ErrorCategory.INVALID_ARGUMENT,
        ErrorCategory.AUTHORIZATION,
        ErrorCategory.VALIDATION,
    }
)

# Error types that indicate transient failures
_TRANSIENT_ERROR_TYPES: tuple[type[Exception], ...] = (
    asyncio.TimeoutError,
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    BrokenPipeError,
    TimeoutError,
)

# Error types that indicate permanent failures
_PERMANENT_ERROR_TYPES: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    NotImplementedError,
    PermissionError,
    FileNotFoundError,
)


class SelfHealingExecutor:
    """Executor with automatic retry and alternative strategy fallback.

    The executor attempts to run a primary function with retry logic. If all
    retries are exhausted, it tries each registered alternative strategy in
    order. If all strategies fail, a :class:`HealingResult` with ``success=False``
    is returned.

    The failure classification uses multiple signals:
        1. :class:`KernelOneError.retryable` attribute (explicit flag)
        2. :class:`ErrorCategory` mapping to transient/permanent
        3. Exception type matching for built-in errors

    Example::

        async def primary_fetch(url: str) -> dict:
            return await http.get(url)

        async def fallback_cache(url: str) -> dict:
            return cache.get(url)

        executor = SelfHealingExecutor(
            retry_strategy=RetryStrategy(max_attempts=3, base_delay=0.5),
            alternatives=[
                AlternativeStrategy("cache", "Fallback to cache", fallback_cache)
            ],
        )

        result = await executor.execute(primary_fetch, "https://api.example.com/data")
    """

    __slots__ = ("_alternatives", "_retry")

    def __init__(
        self,
        retry_strategy: RetryStrategy,
        alternatives: list[AlternativeStrategy] | None = None,
    ) -> None:
        """Initialize the self-healing executor.

        Args:
            retry_strategy: Configuration for retry behavior.
            alternatives: Optional list of fallback strategies to try
                after retries are exhausted.
        """
        self._retry = retry_strategy
        self._alternatives = alternatives or []

    async def execute(
        self,
        primary_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> HealingResult:
        """Execute a function with automatic retry and fallback.

        Args:
            primary_func: The main async function to execute.
            *args: Positional arguments passed to primary_func.
            **kwargs: Keyword arguments passed to primary_func.

        Returns:
            A :class:`HealingResult` indicating success/failure and details.
        """
        strategies_tried: list[str] = []

        # Phase 1: Try primary strategy with retries
        result = await self._try_primary(primary_func, args, kwargs, strategies_tried)
        if result is not None:
            return result

        # Phase 2: Try alternative strategies
        result = await self._try_alternatives(args, kwargs, strategies_tried)
        if result is not None:
            return result

        # Phase 3: All strategies exhausted
        return HealingResult(
            success=False,
            final_result=None,
            attempts=len(strategies_tried),
            strategies_tried=tuple(strategies_tried),
            final_error="All strategies exhausted",
        )

    async def _try_primary(
        self,
        primary_func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        strategies_tried: list[str],
    ) -> HealingResult | None:
        """Attempt primary function with retry logic.

        Returns:
            HealingResult if successful, None if should continue to alternatives.
        """
        last_error: Exception | None = None

        for attempt in range(self._retry.max_attempts):
            try:
                result = await primary_func(*args, **kwargs)
                return HealingResult(
                    success=True,
                    final_result=result,
                    attempts=attempt + 1,
                    strategies_tried=tuple(strategies_tried),
                    final_error=None,
                )
            except asyncio.CancelledError:
                raise
            except (RuntimeError, OSError) as e:
                last_error = e
                failure_type = self._classify_failure(e)

                if failure_type == FailureType.PERMANENT:
                    # Don't retry permanent failures - break to skip retries
                    # but still try alternatives (alternatives can potentially recover)
                    strategies_tried.append(f"primary_permanent_{attempt + 1}")
                    break

                if attempt < self._retry.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    strategies_tried.append(f"retry_{attempt + 1}")
                    await asyncio.sleep(delay)

        # If we have last_error from primary, record it
        if last_error is not None:
            strategies_tried.append("primary_exhausted")

        return None

    async def _try_alternatives(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        strategies_tried: list[str],
    ) -> HealingResult | None:
        """Try alternative strategies in order.

        Returns:
            HealingResult if successful, None if all alternatives failed.
        """
        for alt in self._alternatives:
            try:
                strategies_tried.append(alt.name)
                result = await alt.execute(*args, **kwargs)
                return HealingResult(
                    success=True,
                    final_result=result,
                    attempts=len(strategies_tried),
                    strategies_tried=tuple(strategies_tried),
                    final_error=None,
                )
            except asyncio.CancelledError:
                raise
            except (RuntimeError, OSError):
                continue

        return None

    def _classify_failure(self, error: Exception) -> FailureType:
        """Classify an exception into transient or permanent failure.

        Classification is based on multiple signals in priority order:
            1. :class:`KernelOneError.retryable` attribute (explicit flag)
            2. :class:`ErrorCategory` enum mapping
            3. Exception type matching

        Args:
            error: The exception to classify.

        Returns:
            :attr:`FailureType.TRANSIENT` if retryable,
            :attr:`FailureType.PERMANENT` if not,
            :attr:`FailureType.UNKNOWN` if unclassifiable.
        """
        # Check KernelOneError.retryable attribute first (explicit flag)
        if isinstance(error, KernelOneError):
            if error.retryable:
                return FailureType.TRANSIENT
            return FailureType.PERMANENT

        # Check ErrorCategory in details (for wrapped errors)
        if isinstance(error, KernelOneError) and error.details:
            category_str = error.details.get("error_category", "")
            try:
                category = ErrorCategory(category_str)
                if category in _TRANSIENT_CATEGORIES:
                    return FailureType.TRANSIENT
                if category in _PERMANENT_CATEGORIES:
                    return FailureType.PERMANENT
            except ValueError:
                pass

        # Check exception type directly
        if isinstance(error, _TRANSIENT_ERROR_TYPES):
            return FailureType.TRANSIENT

        if isinstance(error, _PERMANENT_ERROR_TYPES):
            return FailureType.PERMANENT

        # Check for common transient indicators in message
        error_msg = str(error).lower()
        transient_keywords = (
            "timeout",
            "timed out",
            "connection",
            "network",
            "unavailable",
            "rate limit",
            "temporarily",
            "retry",
            "reset",
            "refused",
            "broken pipe",
        )
        for keyword in transient_keywords:
            if keyword in error_msg:
                return FailureType.TRANSIENT

        # Check for permanent indicators
        permanent_keywords = (
            "not found",
            "invalid",
            "denied",
            "unauthorized",
            "forbidden",
            "already exists",
            "validation",
            "malformed",
            "illegal",
        )
        for keyword in permanent_keywords:
            if keyword in error_msg:
                return FailureType.PERMANENT

        return FailureType.UNKNOWN

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay before next retry attempt.

        Uses exponential backoff with optional jitter.

        Args:
            attempt: 0-based attempt number.

        Returns:
            Delay in seconds before next retry.
        """
        delay = self._retry.base_delay * (self._retry.exponential_base**attempt)
        delay = min(delay, self._retry.max_delay)

        if self._retry.jitter:
            # Add up to 20% random jitter
            jitter_range = delay * 0.2
            delay += random.uniform(0.0, jitter_range)

        return delay
