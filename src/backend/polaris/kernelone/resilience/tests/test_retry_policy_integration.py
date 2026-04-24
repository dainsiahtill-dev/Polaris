"""Integration tests for RetryPolicy with Error Classification.

These tests verify that RetryPolicy correctly integrates with the
:class:`polaris.kernelone.errors.classify_error` function to make
retry decisions based on error categories.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from polaris.kernelone.errors import (
    ErrorCategory,
    NetworkError,
    RateLimitError,
    ValidationError,
)
from polaris.kernelone.resilience.retry_policy import (
    RetryPolicy,
    compute_delay,
    execute_with_retry,
    should_retry,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fast_policy() -> RetryPolicy:
    """Retry policy with very short delays for speedy tests."""
    return RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.001,
        max_delay_seconds=0.01,
        jitter_ratio=0.0,
    )


@pytest.fixture
def custom_transient_policy() -> RetryPolicy:
    """Policy with custom transient categories for threshold tests."""
    return RetryPolicy(
        max_attempts=4,
        base_delay_seconds=0.001,
        max_delay_seconds=0.01,
        jitter_ratio=0.0,
        transient_categories=frozenset(
            {
                ErrorCategory.TIMEOUT,
                ErrorCategory.RATE_LIMIT,
                ErrorCategory.NETWORK_ERROR,
                ErrorCategory.TRANSIENT_NETWORK,
                ErrorCategory.SERVICE_UNAVAILABLE,
            }
        ),
    )


# =============================================================================
# Integration: Transient Errors Trigger Retry
# =============================================================================


class TestTransientErrorsTriggerRetry:
    """Verify that transient errors (RateLimitError, TimeoutError, NetworkError)
    are classified correctly and trigger retry behavior."""

    @pytest.mark.asyncio
    async def test_rate_limit_error_triggers_retry(self, custom_transient_policy: RetryPolicy) -> None:
        """RateLimitError (RATE_LIMIT category) should trigger retry."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RateLimitError("rate limited", retry_after=0.001)
            return "success"

        result = await execute_with_retry(flaky, custom_transient_policy)
        assert result == "success"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_timeout_error_triggers_retry(self, custom_transient_policy: RetryPolicy) -> None:
        """asyncio.TimeoutError (TIMEOUT category) should trigger retry."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise asyncio.TimeoutError("operation timed out")
            return "recovered"

        result = await execute_with_retry(flaky, custom_transient_policy)
        assert result == "recovered"
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_network_error_triggers_retry(self, custom_transient_policy: RetryPolicy) -> None:
        """NetworkError (NETWORK_ERROR category) should trigger retry."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise NetworkError("connection refused", url="http://example.com")
            return "connected"

        result = await execute_with_retry(flaky, custom_transient_policy)
        assert result == "connected"
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_transient_network_error_triggers_retry(self, fast_policy: RetryPolicy) -> None:
        """Transient network error should trigger retry via keyword classification."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConnectionError("temporary network issue")
            return "restored"

        result = await execute_with_retry(flaky, fast_policy)
        assert result == "restored"
        assert attempts == 2

    def test_should_retry_rate_limit_error(self, custom_transient_policy: RetryPolicy) -> None:
        """should_retry returns True for RateLimitError."""
        err = RateLimitError("rate limited", retry_after=1.0)
        assert should_retry(err, custom_transient_policy) is True

    def test_should_retry_timeout_error(self, custom_transient_policy: RetryPolicy) -> None:
        """should_retry returns True for asyncio.TimeoutError."""
        err = asyncio.TimeoutError("timed out")
        assert should_retry(err, custom_transient_policy) is True

    def test_should_retry_network_error(self, custom_transient_policy: RetryPolicy) -> None:
        """should_retry returns True for NetworkError."""
        err = NetworkError("connection failed", url="http://test")
        assert should_retry(err, custom_transient_policy) is True


# =============================================================================
# Integration: Permanent Errors Do NOT Trigger Retry
# =============================================================================


class TestPermanentErrorsNoRetry:
    """Verify that permanent errors (ValidationError, PermissionError)
    are classified correctly and do NOT trigger retry behavior."""

    @pytest.mark.asyncio
    async def test_validation_error_does_not_retry(self, custom_transient_policy: RetryPolicy) -> None:
        """ValidationError should not trigger retry - it is not transient."""
        attempts = 0

        async def fail() -> str:
            nonlocal attempts
            attempts += 1
            raise ValidationError("invalid input", field="name", value="")

        with pytest.raises(ValidationError, match="invalid input"):
            await execute_with_retry(fail, custom_transient_policy)

        # Should have been called exactly once - no retries
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_permission_error_does_not_retry(self, custom_transient_policy: RetryPolicy) -> None:
        """PermissionError should not trigger retry - it is not transient."""
        from polaris.kernelone.errors import PermissionError as KernelOnePermissionError

        attempts = 0

        async def fail() -> str:
            nonlocal attempts
            attempts += 1
            raise KernelOnePermissionError(
                "access denied",
                permission_name="write_file",
            )

        with pytest.raises(KernelOnePermissionError):
            await execute_with_retry(fail, custom_transient_policy)

        # Should have been called exactly once - no retries
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_value_error_does_not_retry(self, custom_transient_policy: RetryPolicy) -> None:
        """ValueError (UNKNOWN category) should not trigger retry."""
        attempts = 0

        async def fail() -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("bad value")

        with pytest.raises(ValueError):
            await execute_with_retry(fail, custom_transient_policy)

        assert attempts == 1

    def test_should_retry_validation_error(self, custom_transient_policy: RetryPolicy) -> None:
        """should_retry returns False for ValidationError."""
        err = ValidationError("invalid input", field="name")
        assert should_retry(err, custom_transient_policy) is False

    def test_should_retry_permission_error(self, custom_transient_policy: RetryPolicy) -> None:
        """should_retry returns False for KernelOnePermissionError."""
        from polaris.kernelone.errors import PermissionError as KernelOnePermissionError

        err = KernelOnePermissionError("access denied")
        assert should_retry(err, custom_transient_policy) is False


# =============================================================================
# Integration: execute_with_retry calls build_backoff_seconds
# =============================================================================


class TestExecuteWithRetryBackoffIntegration:
    """Verify that execute_with_retry calls build_backoff_seconds correctly."""

    @pytest.mark.asyncio
    async def test_delays_increase_exponentially(self) -> None:
        """Delays should follow exponential backoff pattern."""
        policy = RetryPolicy(
            max_attempts=4,
            base_delay_seconds=0.01,
            max_delay_seconds=1.0,
            jitter_ratio=0.0,
        )

        delays: list[float] = []

        async def flaky() -> str:
            raise asyncio.TimeoutError("timeout")

        # Patch compute_delay to capture delays
        original_compute = compute_delay

        async def mock_sleep(delay: float) -> None:
            delays.append(delay)
            # Don't actually sleep

        with (
            patch(
                "polaris.kernelone.resilience.retry_policy.compute_delay",
                side_effect=original_compute,
            ) as mock_compute,
            patch.object(asyncio, "sleep", mock_sleep),
            pytest.raises(asyncio.TimeoutError),
        ):
            mock_compute.side_effect = lambda p, a: original_compute(p, a)
            await execute_with_retry(flaky, policy)

        # Verify exponential growth: 0.01, 0.02, 0.04 (for 3 retries)
        assert len(delays) == 3
        assert delays[0] == pytest.approx(0.01, rel=0.01)
        assert delays[1] == pytest.approx(0.02, rel=0.01)
        assert delays[2] == pytest.approx(0.04, rel=0.01)

    @pytest.mark.asyncio
    async def test_compute_delay_called_with_correct_attempt(self) -> None:
        """build_backoff_seconds should be called with incrementing attempt numbers."""
        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=0.01,
            max_delay_seconds=0.1,
            jitter_ratio=0.0,
        )

        attempts_seen: list[int] = []

        async def flaky() -> str:
            raise asyncio.TimeoutError("timeout")

        original_compute = compute_delay

        def mock_compute(p: RetryPolicy, attempt: int) -> float:
            attempts_seen.append(attempt)
            return original_compute(p, attempt)

        with (
            patch("polaris.kernelone.resilience.retry_policy.compute_delay", mock_compute),
            patch.object(asyncio, "sleep"),
            pytest.raises(asyncio.TimeoutError),
        ):
            await execute_with_retry(flaky, policy)

        # Should see attempts 1, 2 (not 3 because max_attempts=3 means only 2 retries)
        assert attempts_seen == [1, 2]


# =============================================================================
# Integration: RetryPolicy Validation
# =============================================================================


class TestRetryPolicyValidationIntegration:
    """Integration tests for RetryPolicy validation with error classification."""

    def test_invalid_max_attempts_rejected(self) -> None:
        """max_attempts < 1 raises ValueError - prevents infinite loops."""
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryPolicy(max_attempts=0)

    def test_invalid_base_delay_rejected(self) -> None:
        """base_delay_seconds < 0 raises ValueError."""
        with pytest.raises(ValueError, match="base_delay_seconds must be >= 0"):
            RetryPolicy(base_delay_seconds=-1.0)

    def test_invalid_max_delay_rejected(self) -> None:
        """max_delay_seconds < base_delay_seconds raises ValueError."""
        with pytest.raises(
            ValueError,
            match="max_delay_seconds must be >= base_delay_seconds",
        ):
            RetryPolicy(base_delay_seconds=5.0, max_delay_seconds=1.0)

    def test_invalid_jitter_ratio_rejected(self) -> None:
        """jitter_ratio outside [0, 1] raises ValueError."""
        with pytest.raises(ValueError, match="jitter_ratio must be in"):
            RetryPolicy(jitter_ratio=-0.1)
        with pytest.raises(ValueError, match="jitter_ratio must be in"):
            RetryPolicy(jitter_ratio=1.5)

    def test_empty_transient_categories_means_no_retries(self) -> None:
        """Empty transient set prevents all retries."""
        policy = RetryPolicy(transient_categories=frozenset())
        assert should_retry(asyncio.TimeoutError("timeout"), policy) is False

    def test_full_transient_categories_enables_retries(self) -> None:
        """Full transient set enables retries for all transient errors."""
        policy = RetryPolicy(
            transient_categories=frozenset(
                {
                    ErrorCategory.TIMEOUT,
                    ErrorCategory.RATE_LIMIT,
                    ErrorCategory.NETWORK_ERROR,
                    ErrorCategory.SERVICE_UNAVAILABLE,
                }
            ),
        )
        assert should_retry(asyncio.TimeoutError("timeout"), policy) is True
        assert should_retry(RateLimitError("rate limited"), policy) is True
        assert should_retry(NetworkError("network error"), policy) is True


# =============================================================================
# Integration: RetryContext State Tracking
# =============================================================================


class TestRetryContextStateTracking:
    """Verify RetryContext correctly tracks retry state."""

    @pytest.mark.asyncio
    async def test_context_tracks_attempt_count(self, fast_policy: RetryPolicy) -> None:
        """Context.attempt_count should increment on each try."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise asyncio.TimeoutError("temporary")
            return "done"

        result = await execute_with_retry(flaky, fast_policy)
        assert result == "done"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_context_tracks_last_error(self, fast_policy: RetryPolicy) -> None:
        """Context.last_error should be updated on each failure."""

        async def flaky() -> str:
            raise asyncio.TimeoutError("attempt failed")

        # After exhaustion, last error should be the TimeoutError
        with pytest.raises(asyncio.TimeoutError) as exc_info:
            await execute_with_retry(flaky, fast_policy)

        assert "attempt failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_max_attempts_limit_respected(self, fast_policy: RetryPolicy) -> None:
        """Should not exceed max_attempts even with transient errors."""
        attempts = 0

        async def always_fail() -> str:
            nonlocal attempts
            attempts += 1
            raise asyncio.TimeoutError("persistent failure")

        with pytest.raises(asyncio.TimeoutError):
            await execute_with_retry(always_fail, fast_policy)

        # max_attempts=3 means 3 total attempts
        assert attempts == 3
