"""Polaris AI Platform - Resilience Strategies

统一弹性策略：超时、重试、降级、截断修复、熔断器。

IMPORTANT: This module contains an async CircuitBreaker implementation.
For sync provider operations, see polaris/infrastructure/llm/providers/provider_helpers.py.

CircuitBreaker Intentional Separation:
1. AsyncCircuitBreaker (this module, llm/engine/resilience.py):
   - For async LLM engine calls
   - Full HALF_OPEN state management with asyncio.Lock
   - Integrates with ResilienceManager for retry/timeout

2. SyncCircuitBreaker (llm/providers/provider_helpers.py):
   - For sync provider HTTP operations
   - Simplified state machine with threading.RLock
   - Independent implementation optimized for blocking I/O

These are intentionally separate implementations optimized for their
respective execution models. Do NOT try to unify them.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

from polaris.kernelone.constants import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS,
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
    CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
    CIRCUIT_BREAKER_WINDOW_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    RETRY_JITTER_MAX,
    RETRY_JITTER_MIN,
)
from polaris.kernelone.errors import CircuitBreakerOpenError, NonRetryableError, RetryableError

from ..error_categories import classify_error as _classify_error_fn
from .contracts import AIResponse, ErrorCategory, Usage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

logger = logging.getLogger(__name__)
T = TypeVar("T")


# =============================================================================
# Error Category Extensions for HTTP Status Codes
# =============================================================================

# HTTP status codes that should NOT be retried
UNAUTHORIZED_ERRORS: frozenset[int] = frozenset({401, 403})
CLIENT_ERRORS: frozenset[int] = frozenset({400, 422})

# HTTP status codes that SHOULD be retried
RATE_LIMIT_ERRORS: frozenset[int] = frozenset({429})
SERVER_ERRORS: frozenset[int] = frozenset({500, 502, 503, 504})


def is_retryable(status_code: int | None) -> bool:
    """Determine if an HTTP status code is retryable.

    Implements fast-fail detection:
    - 401/403: Never retry (fix credentials first)
    - 400/422: Never retry (request is malformed, retry won't help)
    - 429: Retry (rate limited, wait and retry)
    - 500+: Retry (server-side issues may be transient)

    Args:
        status_code: HTTP status code to evaluate.

    Returns:
        True if the error is retryable, False for fast-fail.
    """
    if status_code is None:
        return True  # Unknown errors default to retryable

    if status_code in UNAUTHORIZED_ERRORS:
        return False  # Never retry auth errors

    if status_code in CLIENT_ERRORS:
        return False  # Never retry client errors

    if status_code in RATE_LIMIT_ERRORS:
        return True  # Rate limited, retry with backoff

    # 2xx success codes shouldn't reach here, but treat as non-retryable
    return status_code >= 500  # Server errors may be transient


def extract_status_code_from_error(error: Exception) -> int | None:
    """Extract HTTP status code from exception if available.

    Args:
        error: Exception to inspect.

    Returns:
        Status code if found, None otherwise.
    """
    error_str = str(error)

    # Check for common status code patterns in error messages
    for code in (401, 403, 429, 500, 502, 503, 504):
        if str(code) in error_str:
            return code

    # Check for response objects with status_code attribute
    if hasattr(error, "status_code"):
        return int(error.status_code)

    return None


# =============================================================================
# Jitter Calculation
# =============================================================================


def calculate_backoff_with_jitter(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_percent: float = 0.1,
) -> float:
    """Calculate exponential backoff with jitter.

    Prevents thundering herd by adding random jitter to backoff delays.

    Args:
        attempt: Current attempt number (0-indexed).
        base_delay: Base delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter_percent: Jitter as percentage of delay (0.1 = 10%).

    Returns:
        Delay in seconds with jitter applied.
    """
    delay = min(base_delay * (2**attempt), max_delay)
    jitter = random.uniform(0, delay * jitter_percent)
    return delay + jitter


async def retry_with_jitter(
    func: Callable[[], Awaitable[T]],
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_percent: float = 0.1,
) -> T:
    """Retry an async function with exponential backoff and jitter.

    Args:
        func: Async function to retry.
        max_retries: Maximum number of retry attempts.
        base_delay: Base delay between retries.
        max_delay: Maximum delay cap.
        jitter_percent: Jitter as percentage of delay.

    Returns:
        Result from successful function call.

    Raises:
        The last exception if all retries fail.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except asyncio.CancelledError:
            # CancelledError must be re-raised, not swallowed
            logger.debug("Retry attempt %d cancelled", attempt + 1)
            raise
        except (RuntimeError, ConnectionError) as e:
            last_error = e

            # Fast-fail for non-retryable errors
            status_code = extract_status_code_from_error(e)
            if not is_retryable(status_code):
                logger.debug("Non-retryable error (status=%s), failing fast", status_code)
                raise

            if attempt < max_retries:
                delay = calculate_backoff_with_jitter(attempt, base_delay, max_delay, jitter_percent)
                logger.debug("Retry attempt %d failed, waiting %.2fs", attempt + 1, delay)
                await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Retry exhausted without error")  # Should not reach here


# =============================================================================
# Circuit Breaker
# =============================================================================


class CircuitState(Enum):
    """Circuit breaker states.

    CLOSED: Normal operation, requests pass through.
    OPEN: Circuit is tripped, requests fail fast.
    HALF_OPEN: Testing if service has recovered.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior.

    Attributes:
        failure_threshold: Number of consecutive failures before opening.
        recovery_timeout: Seconds to wait before attempting reset.
        half_open_max_calls: Max test requests in half-open state.
        success_threshold: Successes needed in half-open to close circuit.
        window_seconds: Sliding window for failure counting.
    """

    failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD
    recovery_timeout: float = CIRCUIT_BREAKER_RECOVERY_TIMEOUT
    half_open_max_calls: int = CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS
    success_threshold: int = CIRCUIT_BREAKER_SUCCESS_THRESHOLD
    window_seconds: float = CIRCUIT_BREAKER_WINDOW_SECONDS

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> CircuitBreakerConfig:
        return cls(
            failure_threshold=int(options.get("cb_failure_threshold", 5)),
            recovery_timeout=float(options.get("cb_recovery_timeout", 60.0)),
            half_open_max_calls=int(options.get("cb_half_open_max_calls", 3)),
            success_threshold=int(options.get("cb_success_threshold", 2)),
            window_seconds=float(options.get("cb_window_seconds", 120.0)),
        )


class CircuitBreaker:
    """Circuit breaker implementation with HALF_OPEN state.

    State transitions:
        CLOSED -> OPEN: When failures exceed threshold
        OPEN -> HALF_OPEN: After recovery timeout
        HALF_OPEN -> CLOSED: When successes exceed threshold
        HALF_OPEN -> OPEN: When a failure occurs
    """

    def __init__(
        self,
        name: str = "default",
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float | None = None
        self.last_success_time: float | None = None
        self.half_open_calls = 0
        self.half_open_successes = 0

        # Thread-safe counters via simple locking
        self._lock = asyncio.Lock()

    async def call(self, func: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: T) -> T:
        """Execute a function through the circuit breaker.

        Args:
            func: Async function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            Result from the function.

        Raises:
            CircuitBreakerOpenError: If circuit is open and not ready to test.
        """
        async with self._lock:
            # Check state and decide action (all within lock to prevent TOCTOU)
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._transition_to_half_open()
                else:
                    raise CircuitBreakerOpenError(
                        circuit_name=self.name,
                        retry_after=self._time_until_reset(),
                    )

            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        circuit_name=self.name,
                        retry_after=self._time_until_reset(),
                    )
                self.half_open_calls += 1

        # Execute function outside lock
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except asyncio.CancelledError:
            # CancelledError must be re-raised, not swallowed
            logger.info("Circuit breaker '%s' call cancelled", self.name)
            raise
        except BaseException:
            # Intentionally broad: circuit breaker must count ANY failure
            # (including KeyboardInterrupt, SystemExit) to properly track
            # health. We immediately re-raise after recording.
            await self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        return time.monotonic() - self.last_failure_time >= self.config.recovery_timeout

    def _time_until_reset(self) -> float | None:
        """Calculate seconds until reset can be attempted."""
        if self.last_failure_time is None:
            return 0.0
        elapsed = time.monotonic() - self.last_failure_time
        remaining = self.config.recovery_timeout - elapsed
        return max(0.0, remaining) if remaining > 0 else None

    def _transition_to_half_open(self) -> None:
        """Transition from OPEN to HALF_OPEN state."""
        self.state = CircuitState.HALF_OPEN
        self.half_open_calls = 0
        self.half_open_successes = 0
        self.failure_count = 0  # Reset failure count for new recovery cycle
        logger.info("Circuit breaker '%s' transitioning OPEN -> HALF_OPEN", self.name)

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            self.last_success_time = time.monotonic()

            if self.state == CircuitState.HALF_OPEN:
                self.half_open_successes += 1
                self.success_count += 1

                if self.half_open_successes >= self.config.success_threshold:
                    self._transition_to_closed()
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success in closed state
                self.failure_count = 0

    async def _on_failure(self) -> None:
        """Handle failed call."""
        async with self._lock:
            self.last_failure_time = time.monotonic()
            self.failure_count += 1

            if self.state == CircuitState.HALF_OPEN:
                # Any failure in half-open immediately opens the circuit
                self._transition_to_open()
            elif self.state == CircuitState.CLOSED and self.failure_count >= self.config.failure_threshold:
                self._transition_to_open()

    def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        self.state = CircuitState.OPEN
        self.success_count = 0
        self.last_failure_time = time.monotonic()
        logger.warning(
            "Circuit breaker '%s' transitioning CLOSED/HALF_OPEN -> OPEN (failures: %d)",
            self.name,
            self.failure_count,
        )

    def _transition_to_closed(self) -> None:
        """Transition to CLOSED state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        logger.info("Circuit breaker '%s' transitioning HALF_OPEN -> CLOSED", self.name)

    def get_status(self) -> dict[str, Any]:
        """Get circuit breaker status for health checks.

        Returns:
            Dictionary with current state and metrics.
        """
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "half_open_calls": self.half_open_calls,
            "half_open_successes": self.half_open_successes,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "half_open_max_calls": self.config.half_open_max_calls,
                "success_threshold": self.config.success_threshold,
            },
        }

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.last_success_time = None
        self.half_open_calls = 0
        self.half_open_successes = 0
        logger.info("Circuit breaker '%s' manually reset", self.name)


class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers.

    Provides centralized access to circuit breakers by name.
    """

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreaker:
        """Get an existing circuit breaker or create a new one.

        Args:
            name: Unique name for the circuit breaker.
            config: Optional configuration for new breakers.

        Returns:
            Circuit breaker instance.
        """
        async with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name=name, config=config)
            return self._breakers[name]

    def get_all_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all registered circuit breakers.

        Returns:
            Dictionary mapping breaker names to their status.
        """
        return {name: breaker.get_status() for name, breaker in self._breakers.items()}

    async def reset_all(self) -> None:
        """Reset all circuit breakers to CLOSED state."""
        async with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()

    def remove(self, name: str) -> bool:
        """Remove a circuit breaker from the registry.

        Args:
            name: Name of the circuit breaker to remove.

        Returns:
            True if removed, False if not found.
        """
        if name in self._breakers:
            del self._breakers[name]
            return True
        return False

    def clear(self) -> None:
        """Remove all circuit breakers from the registry."""
        self._breakers.clear()


# Global registry instance
_global_registry: CircuitBreakerRegistry | None = None


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Get the global circuit breaker registry.

    Returns:
        Global registry instance.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = CircuitBreakerRegistry()
    return _global_registry


# =============================================================================
# Multi-Provider Fallback
# =============================================================================


@dataclass(frozen=True)
class ProviderEndpoint:
    """Single provider endpoint in a fallback chain."""

    name: str
    invoke: Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class FallbackExecutionResult:
    """Result metadata for a fallback-chain invocation."""

    provider: str
    attempts: int
    fallback_used: bool
    total_latency_ms: float
    value: Any


class MultiProviderFallbackManager:
    """Execute providers in priority order with automatic fallback.

    Designed for provider-level failover scenarios such as:
    - primary provider rate-limited (429)
    - transient provider/network errors
    """

    def __init__(
        self,
        providers: list[ProviderEndpoint],
        *,
        fallback_status_codes: set[int] | None = None,
        fallback_on_exception_types: tuple[type[Exception], ...] = (Exception,),
    ) -> None:
        if not providers:
            raise ValueError("providers cannot be empty")
        self._providers = list(providers)
        self._fallback_status_codes = fallback_status_codes or {429, 500, 502, 503, 504}
        self._fallback_on_exception_types = fallback_on_exception_types

    async def invoke(self, *args: Any, **kwargs: Any) -> FallbackExecutionResult:
        started_ns = time.perf_counter_ns()
        fallback_used = False
        last_error: Exception | None = None

        for attempts, endpoint in enumerate(self._providers, start=1):
            index = attempts - 1
            try:
                value = await endpoint.invoke(*args, **kwargs)
                return FallbackExecutionResult(
                    provider=endpoint.name,
                    attempts=attempts,
                    fallback_used=fallback_used,
                    total_latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000.0,
                    value=value,
                )
            except asyncio.CancelledError:
                raise
            except self._fallback_on_exception_types as exc:
                last_error = exc
                is_last = index == len(self._providers) - 1
                if is_last or not self._should_fallback(exc):
                    raise
                fallback_used = True
                logger.info(
                    "Fallback provider switch: %s -> %s (error=%s)",
                    endpoint.name,
                    self._providers[index + 1].name,
                    type(exc).__name__,
                )

        if last_error is not None:
            raise last_error
        raise RuntimeError("Fallback chain exhausted without result")

    def _should_fallback(self, error: Exception) -> bool:
        status_code = extract_status_code_from_error(error)
        if status_code is None:
            return True
        return status_code in self._fallback_status_codes


# =============================================================================
# Timeout Configuration
# =============================================================================


@dataclass
class TimeoutConfig:
    """超时配置"""

    request_timeout: float = 60.0  # 单次请求超时
    total_timeout: float = DEFAULT_OPERATION_TIMEOUT_SECONDS  # 总超时（含重试）
    connect_timeout: float = 10.0  # 连接超时

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> TimeoutConfig:
        return cls(
            request_timeout=float(options.get("timeout", 60.0) or 60.0),
            total_timeout=float(
                options.get("total_timeout", DEFAULT_OPERATION_TIMEOUT_SECONDS) or DEFAULT_OPERATION_TIMEOUT_SECONDS
            ),
            connect_timeout=float(options.get("connect_timeout", 10.0) or 10.0),
        )


# =============================================================================
# Retry Configuration
# =============================================================================


@dataclass
class RetryConfig:
    """重试配置

    平台层配置：仅重试传输类错误（TIMEOUT, NETWORK_ERROR, RATE_LIMIT）。
    语义类错误（INVALID_RESPONSE, JSON_PARSE, CONFIG_ERROR）不在平台层重试。
    """

    max_attempts: int = 2  # 默认 2 次尝试 = 1 次重试
    base_delay: float = 1.0  # 基础延迟（秒）
    max_delay: float = 30.0  # 最大延迟（秒）
    exponential_base: float = 2.0  # 指数退避基数
    jitter: bool = True  # 是否添加抖动
    # 仅重试传输类错误
    retryable_errors: list[ErrorCategory] = field(
        default_factory=lambda: [
            ErrorCategory.TIMEOUT,
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.NETWORK_ERROR,
        ]
    )
    # 是否仅做传输层重试（不解码响应内容）
    transport_only: bool = True

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> RetryConfig:
        import os

        # 支持环境变量配置
        env_val = os.environ.get("KERNELONE_PLATFORM_RETRY_MAX") or os.environ.get("KERNELONE_PLATFORM_RETRY_MAX", "1")
        platform_retry_max = int(env_val) if env_val else 1

        # 兼容旧配置键
        max_retries = options.get("max_retries", platform_retry_max)
        if max_retries is None:
            max_retries = platform_retry_max

        return cls(
            max_attempts=int(max_retries) + 1,
            base_delay=float(options.get("retry_delay") or 1.0),
            max_delay=float(options.get("max_retry_delay") or 30.0),
            exponential_base=float(options.get("exponential_base") or 2.0),
            jitter=bool(options.get("jitter", True)),
            transport_only=options.get("platform_transport_only", True),
        )


# =============================================================================
# Truncation Configuration
# =============================================================================


@dataclass
class TruncationConfig:
    """截断修复配置"""

    enabled: bool = True  # 是否启用修复
    max_repair_tokens: int = 1200  # 修复时最大 tokens
    repair_temperature: float = 0.0  # 修复时温度
    max_repair_attempts: int = 1  # 最大修复尝试次数


# =============================================================================
# Resilience Manager
# =============================================================================


class ResilienceManager:
    """弹性策略管理器

    提供统一的超时、重试、降级、截断修复能力。
    """

    def __init__(
        self,
        timeout_config: TimeoutConfig | None = None,
        retry_config: RetryConfig | None = None,
        truncation_config: TruncationConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.timeout_config = timeout_config or TimeoutConfig()
        self.retry_config = retry_config or RetryConfig()
        self.truncation_config = truncation_config or TruncationConfig()
        self.circuit_breaker = circuit_breaker

    async def execute_with_resilience(
        self,
        operation: Callable[[], Coroutine[Any, Any, AIResponse]],
        operation_name: str = "llm_invoke",
    ) -> AIResponse:
        """带弹性策略执行操作

        包含重试、超时处理、错误分类。
        返回的 AIResponse 包含 platform_retry_count 和 platform_retry_exhausted 字段。
        """
        start_time = time.monotonic()
        last_error: str | None = None
        last_category = ErrorCategory.UNKNOWN
        platform_retry_count = 0

        for attempt in range(1, self.retry_config.max_attempts + 1):
            elapsed = time.monotonic() - start_time
            if elapsed >= self.timeout_config.total_timeout:
                return AIResponse.failure(
                    error=f"Total timeout exceeded ({self.timeout_config.total_timeout}s)",
                    category=ErrorCategory.TIMEOUT,
                    latency_ms=int(elapsed * 1000),
                    platform_retry_count=platform_retry_count,
                    platform_retry_exhausted=True,
                    last_transport_error=last_error,
                )

            try:
                remaining_timeout = self.timeout_config.request_timeout
                if remaining_timeout <= 0:
                    remaining_timeout = 60.0

                # Execute with optional circuit breaker
                if self.circuit_breaker:
                    response = await self.circuit_breaker.call(
                        self._execute_operation_with_timeout,
                        operation,
                        remaining_timeout,
                    )
                else:
                    response = await self._execute_operation_with_timeout(operation, remaining_timeout)

                if response.ok:
                    # 成功返回，检查是否有过重试
                    response.platform_retry_count = platform_retry_count
                    return response

                # 记录错误用于重试决策
                last_error = response.error
                last_category = response.error_category or ErrorCategory.UNKNOWN

                # 记录重试次数
                platform_retry_count = attempt - 1

                # 判断是否可重试（仅传输类错误）
                if not self._is_retryable(last_category):
                    # 不可重试的错误，立即返回
                    response.platform_retry_count = platform_retry_count
                    response.platform_retry_exhausted = attempt >= self.retry_config.max_attempts
                    response.last_transport_error = last_error
                    return response

            except asyncio.CancelledError:
                # CancelledError must be re-raised, not swallowed or retried.
                # Re-raise to let the coroutine cancellation propagate properly.
                raise
            except CircuitBreakerOpenError as e:
                last_error = f"Circuit breaker open: {e.circuit_name}"
                last_category = ErrorCategory.PROVIDER_ERROR
                platform_retry_count = attempt - 1
                # Wait for circuit breaker to recover before retrying
                cb_config = self.circuit_breaker.config if self.circuit_breaker else None
                recovery_timeout = cb_config.recovery_timeout if cb_config else 60.0
                retry_after = e.retry_after or recovery_timeout
                await asyncio.sleep(min(retry_after, self.timeout_config.total_timeout / 2))
            except asyncio.TimeoutError:
                # TimeoutError means the operation did not complete.
                # Return a fresh failure response immediately; do NOT fall through
                # with a stale response from a previous iteration.
                elapsed = time.monotonic() - start_time
                return AIResponse.failure(
                    error=f"Request timeout ({self.timeout_config.request_timeout}s)",
                    category=ErrorCategory.TIMEOUT,
                    latency_ms=int(elapsed * 1000),
                    platform_retry_count=attempt - 1,
                    platform_retry_exhausted=True,
                    last_transport_error=f"Request timeout ({self.timeout_config.request_timeout}s)",
                )
            except (RuntimeError, ConnectionError) as exc:
                # All other exceptions: classify and decide whether to retry.
                # Return a FRESH failure response rather than a stale `response`
                # from a previous iteration that may have had ok=True.
                elapsed = time.monotonic() - start_time
                last_error = str(exc)
                last_category = self._classify_error(exc)
                platform_retry_count = attempt - 1
                # If non-retryable, return immediately with a fresh failure response
                if not self._is_retryable(last_category):
                    return AIResponse.failure(
                        error=last_error,
                        category=last_category,
                        latency_ms=int(elapsed * 1000),
                        platform_retry_count=platform_retry_count,
                        platform_retry_exhausted=True,
                        last_transport_error=last_error,
                    )
                # For retryable errors, fall through to the retry loop below

            # 检查是否还有重试机会
            if attempt < self.retry_config.max_attempts:
                delay = self._calculate_delay(attempt)
                logger.debug(
                    "[%s] Attempt %d failed (%s), retrying in %.2fs",
                    operation_name,
                    attempt,
                    last_category.value,
                    delay,
                )
                await asyncio.sleep(delay)

        # 所有尝试失败
        elapsed = time.monotonic() - start_time
        return AIResponse.failure(
            error=f"All {self.retry_config.max_attempts} attempts failed. Last: {last_error}",
            category=last_category,
            latency_ms=int(elapsed * 1000),
            platform_retry_count=platform_retry_count,
            platform_retry_exhausted=True,
            last_transport_error=last_error,
        )

    async def _execute_operation_with_timeout(
        self,
        operation: Callable[[], Coroutine[Any, Any, AIResponse]],
        timeout: float,
    ) -> AIResponse:
        """Execute operation with timeout."""
        return await asyncio.wait_for(operation(), timeout=timeout)

    def _is_retryable(self, category: ErrorCategory) -> bool:
        """判断错误是否可重试（基于 ErrorCategory）"""
        return self.is_retryable_by_category(category)

    def is_retryable_by_category(self, category: ErrorCategory) -> bool:
        """基于 ErrorCategory 判断是否可重试。

        语义层错误（INVALID_RESPONSE、JSON_PARSE、CONFIG_ERROR）默认 fast-fail，
        不进入重试循环。传输层错误（TIMEOUT、RATE_LIMIT、NETWORK_ERROR）
        通过 RetryConfig.retryable_errors 判断。
        """
        # 语义层错误：fast-fail，不重试
        if category in (
            ErrorCategory.INVALID_RESPONSE,
            ErrorCategory.JSON_PARSE,
            ErrorCategory.CONFIG_ERROR,
        ):
            return False
        # 传输层错误：通过 RetryConfig.retryable_errors 判断
        return category in self.retry_config.retryable_errors

    def _calculate_delay(self, attempt: int) -> float:
        """计算重试延迟（指数退避 + 抖动）"""
        delay = self.retry_config.base_delay * (self.retry_config.exponential_base ** (attempt - 1))
        delay = min(delay, self.retry_config.max_delay)

        if self.retry_config.jitter:
            # 添加 0-30% 的抖动
            jitter_factor = RETRY_JITTER_MIN + (random.random() * (RETRY_JITTER_MAX - RETRY_JITTER_MIN))
            delay *= jitter_factor

        return delay

    def _classify_error(self, error: Exception) -> ErrorCategory:
        """Delegate to the canonical error classifier in error_categories."""
        return _classify_error_fn(error)

    def should_attempt_repair(self, output: str) -> bool:
        """判断是否需要截断修复"""
        if not self.truncation_config.enabled:
            return False

        body = str(output or "").strip()
        if not body or "{" not in body:
            return False

        # JSON 不完整检测
        if body.count("{") > body.count("}"):
            return True
        return bool(body.endswith((",", ":", "[", "{", '"')))

    def build_repair_prompt(self, truncated_output: str, required_keys: list[str] | None = None) -> str:
        """构建截断修复提示词"""
        keys_text = ""
        if required_keys:
            keys_text = f"Required keys: {', '.join(required_keys)}.\n"

        return (
            "Convert the following text into valid JSON only.\n"
            f"{keys_text}"
            "No markdown, no extra text.\n\n"
            f"{truncated_output[:6000]}"
        )

    def create_fallback_response(
        self,
        task_type: str,
        context: dict[str, Any] | None = None,
    ) -> AIResponse:
        """创建降级响应"""
        context = context or {}

        # 根据任务类型提供不同的降级内容
        fallback_content = self._generate_fallback_content(task_type, context)

        return AIResponse.success(
            output=json.dumps(fallback_content),
            structured=fallback_content,
            usage=Usage.estimate("", json.dumps(fallback_content)),
            latency_ms=0,
        )

    def _generate_fallback_content(
        self,
        task_type: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a generic provider-unavailable fallback payload.

        KernelOne does not own Polaris business semantics.  Callers that
        need domain-specific fallback text must inject a ``fallback_template``
        key into ``context`` or handle it in the Cell / application layer.
        """
        template = context.get("fallback_template")
        if isinstance(template, dict):
            template["fallback"] = True
            template["fallback_reason"] = "provider_unavailable"
            return template

        return {
            "fallback": True,
            "fallback_reason": "provider_unavailable",
            "task_type": str(task_type or ""),
        }

    def _generate_dialogue_fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Deprecated - use _generate_fallback_content instead.

        Retained for backward compatibility; returns generic fallback.
        """
        return self._generate_fallback_content("dialogue", context)

    def _generate_interview_fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Deprecated - use _generate_fallback_content instead.

        Retained for backward compatibility; returns generic fallback.
        """
        return self._generate_fallback_content("interview", context)

    def get_circuit_breaker_status(self) -> dict[str, Any] | None:
        """Get circuit breaker status if configured.

        Returns:
            Circuit breaker status dict or None if not configured.
        """
        if self.circuit_breaker:
            return self.circuit_breaker.get_status()
        return None


def with_resilience(
    timeout_config: TimeoutConfig | None = None,
    retry_config: RetryConfig | None = None,
) -> Callable[[Callable[..., Coroutine[Any, Any, AIResponse]]], Callable[..., Coroutine[Any, Any, AIResponse]]]:
    """弹性策略装饰器"""

    manager = ResilienceManager(timeout_config=timeout_config, retry_config=retry_config)

    def decorator(
        func: Callable[..., Coroutine[Any, Any, AIResponse]],
    ) -> Callable[..., Coroutine[Any, Any, AIResponse]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> AIResponse:
            return await manager.execute_with_resilience(
                lambda: func(*args, **kwargs),
                operation_name=func.__name__,
            )

        return wrapper

    return decorator


# =============================================================================
# Public API Exports
# =============================================================================

__all__ = [
    "CLIENT_ERRORS",
    "RATE_LIMIT_ERRORS",
    "SERVER_ERRORS",
    # HTTP error sets
    "UNAUTHORIZED_ERRORS",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpenError",
    "CircuitBreakerRegistry",
    # Circuit breaker
    "CircuitState",
    # Manager
    "FallbackExecutionResult",
    "MultiProviderFallbackManager",
    "NonRetryableError",
    "ProviderEndpoint",
    "ResilienceManager",
    "RetryConfig",
    "RetryableError",
    # Config classes
    "TimeoutConfig",
    "TruncationConfig",
    # Jitter utilities
    "calculate_backoff_with_jitter",
    "extract_status_code_from_error",
    "get_circuit_breaker_registry",
    # Retry decision
    "is_retryable",
    "retry_with_jitter",
    "with_resilience",
]
