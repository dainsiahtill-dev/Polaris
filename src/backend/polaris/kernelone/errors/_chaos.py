"""Chaos-engineering and resilience marker errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class ChaosError(KernelOneError):
    """Chaos engineering error.

    Base class for chaos testing errors.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CHAOS_ERROR",
        chaos_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if chaos_type:
            self.details["chaos_type"] = chaos_type


class ChaosInjectionError(ChaosError):
    """Chaos injection failed."""

    def __init__(
        self,
        message: str,
        *,
        injection_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_INJECTION_ERROR",
            chaos_type=injection_type,
            **kwargs,
        )


class ChaosSkippedError(ChaosError):
    """Chaos injection was skipped."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_SKIPPED_ERROR",
            **kwargs,
        )
        if reason:
            self.details["reason"] = reason


class RateLimitExceededError(ChaosError):
    """Chaos rate limit exceeded."""

    def __init__(
        self,
        message: str,
        *,
        limit: int = 0,
        current: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_RATE_LIMIT_EXCEEDED_ERROR",
            chaos_type="rate_limiter",
            **kwargs,
        )
        self.limit = limit
        self.current = current
        if limit:
            self.details["limit"] = limit
        if current:
            self.details["current"] = current


class NetworkChaosError(ChaosError):
    """Network chaos error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="NETWORK_CHAOS_ERROR",
            chaos_type="network",
            **kwargs,
        )


class DeadlockDetectedError(ChaosError):
    """Deadlock detected."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="DEADLOCK_DETECTED_ERROR",
            chaos_type="deadlock",
            **kwargs,
        )


class LockTimeoutError(ChaosError):
    """Lock acquisition timeout."""

    def __init__(
        self,
        message: str,
        *,
        lock_name: str = "",
        timeout_seconds: float = 0,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="LOCK_TIMEOUT_ERROR",
            chaos_type="lock",
            **kwargs,
        )
        if lock_name:
            self.details["lock_name"] = lock_name
        if timeout_seconds:
            self.details["timeout_seconds"] = timeout_seconds


class ChaosCircuitBreakerError(ChaosError):
    """Chaos circuit breaker error."""

    def __init__(
        self,
        message: str,
        *,
        circuit_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CHAOS_CIRCUIT_BREAKER_ERROR",
            chaos_type="circuit_breaker",
            **kwargs,
        )
        if circuit_name:
            self.details["circuit_name"] = circuit_name


# ============================================================================
# Retry/Resilience Errors
# ============================================================================


class RetryableError(KernelOneError):
    """Error that can be safely retried."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs["retryable"] = True
        super().__init__(message, code="RETRYABLE_ERROR", **kwargs)


class NonRetryableError(KernelOneError):
    """Error that should not be retried."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs["retryable"] = False
        super().__init__(message, code="NON_RETRYABLE_ERROR", **kwargs)


class ShadowReplayError(KernelOneError):
    """Shadow replay error."""

    def __init__(
        self,
        message: str,
        *,
        replay_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="SHADOW_REPLAY_ERROR",
            **kwargs,
        )
        if replay_id:
            self.details["replay_id"] = replay_id
