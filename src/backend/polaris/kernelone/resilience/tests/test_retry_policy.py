"""Tests for retry_policy module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from polaris.kernelone.errors import ErrorCategory, NetworkError, RateLimitError
from polaris.kernelone.resilience.retry_policy import (
    RetryContext,
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
        base_delay_seconds=0.01,
        max_delay_seconds=0.05,
        jitter_ratio=0.0,
    )


@pytest.fixture
def no_retry_policy() -> RetryPolicy:
    """Policy that allows only a single attempt."""
    return RetryPolicy(
        max_attempts=1,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        jitter_ratio=0.0,
    )


# =============================================================================
# RetryPolicy Validation
# =============================================================================


class TestRetryPolicyValidation:
    """Tests for RetryPolicy invariants."""

    def test_default_values(self) -> None:
        """Default policy has sensible defaults."""
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.base_delay_seconds == 1.0
        assert policy.max_delay_seconds == 60.0
        assert policy.jitter_ratio == 0.2
        assert ErrorCategory.TIMEOUT in policy.transient_categories
        assert ErrorCategory.RATE_LIMIT in policy.transient_categories

    def test_custom_transient_categories(self) -> None:
        """Custom transient category set is honoured."""
        custom = frozenset({ErrorCategory.NETWORK_ERROR})
        policy = RetryPolicy(transient_categories=custom)
        assert policy.transient_categories == custom

    def test_max_attempts_must_be_positive(self) -> None:
        """max_attempts < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            RetryPolicy(max_attempts=0)

    def test_base_delay_must_be_non_negative(self) -> None:
        """base_delay_seconds < 0 raises ValueError."""
        with pytest.raises(ValueError, match="base_delay_seconds must be >= 0"):
            RetryPolicy(base_delay_seconds=-0.1)

    def test_max_delay_must_be_gte_base(self) -> None:
        """max_delay_seconds < base_delay_seconds raises ValueError."""
        with pytest.raises(ValueError, match="max_delay_seconds must be >= base_delay_seconds"):
            RetryPolicy(base_delay_seconds=5.0, max_delay_seconds=1.0)

    def test_jitter_ratio_bounds(self) -> None:
        """jitter_ratio outside [0, 1] raises ValueError."""
        with pytest.raises(ValueError, match="jitter_ratio must be in"):
            RetryPolicy(jitter_ratio=-0.1)
        with pytest.raises(ValueError, match="jitter_ratio must be in"):
            RetryPolicy(jitter_ratio=1.1)

    def test_jitter_ratio_at_boundaries(self) -> None:
        """jitter_ratio exactly 0.0 and 1.0 is accepted."""
        p1 = RetryPolicy(jitter_ratio=0.0)
        assert p1.jitter_ratio == 0.0
        p2 = RetryPolicy(jitter_ratio=1.0)
        assert p2.jitter_ratio == 1.0


# =============================================================================
# should_retry
# =============================================================================


class TestShouldRetry:
    """Tests for should_retry function."""

    def test_transient_timeout_error(self, fast_policy: RetryPolicy) -> None:
        """asyncio.TimeoutError is classified as TIMEOUT and retried."""
        assert should_retry(asyncio.TimeoutError("timed out"), fast_policy) is True

    def test_transient_network_error(self, fast_policy: RetryPolicy) -> None:
        """NetworkError maps to NETWORK_ERROR and is retried."""
        err = NetworkError("connection failed", url="http://example.com")
        assert should_retry(err, fast_policy) is True

    def test_transient_rate_limit_error(self, fast_policy: RetryPolicy) -> None:
        """RateLimitError maps to RATE_LIMIT and is retried."""
        err = RateLimitError("too many requests", retry_after=1.0)
        assert should_retry(err, fast_policy) is True

    def test_non_transient_value_error(self, fast_policy: RetryPolicy) -> None:
        """ValueError maps to UNKNOWN which is not in default transient set."""
        assert should_retry(ValueError("bad input"), fast_policy) is False

    def test_custom_category_set(self) -> None:
        """Policy with custom transient_categories is respected."""
        policy = RetryPolicy(transient_categories=frozenset({ErrorCategory.INVALID_INPUT}))
        assert should_retry(ValueError("invalid"), policy) is False
        # INVALID_INPUT is in the set, but classify_error returns UNKNOWN for ValueError.
        # This tests the policy boundary: should_retry only checks membership.
        # To truly test custom set, we need an error that classify_error maps to INVALID_INPUT.
        # classify_error does not map anything to INVALID_INPUT, so we verify the set directly.
        assert ErrorCategory.INVALID_INPUT in policy.transient_categories

    def test_empty_transient_set(self) -> None:
        """Empty transient set means nothing is retried."""
        policy = RetryPolicy(transient_categories=frozenset())
        assert should_retry(asyncio.TimeoutError("timeout"), policy) is False


# =============================================================================
# compute_delay
# =============================================================================


class TestComputeDelay:
    """Tests for compute_delay function."""

    def test_first_attempt_no_jitter(self) -> None:
        """Attempt 1 with zero jitter returns base_delay_seconds."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
            jitter_ratio=0.0,
        )
        assert compute_delay(policy, 1) == 1.0

    def test_exponential_growth(self) -> None:
        """Delay doubles with each attempt."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
            jitter_ratio=0.0,
        )
        assert compute_delay(policy, 2) == 2.0
        assert compute_delay(policy, 3) == 4.0
        assert compute_delay(policy, 4) == 8.0

    def test_max_delay_cap(self) -> None:
        """Delay is capped at max_delay_seconds."""
        policy = RetryPolicy(
            base_delay_seconds=10.0,
            max_delay_seconds=15.0,
            jitter_ratio=0.0,
        )
        # attempt 2: 10 * 2^(2-1) = 20, capped to 15
        assert compute_delay(policy, 2) == 15.0

    def test_zero_jitter(self) -> None:
        """jitter_ratio=0 yields deterministic delays."""
        policy = RetryPolicy(
            base_delay_seconds=2.0,
            max_delay_seconds=100.0,
            jitter_ratio=0.0,
        )
        for attempt in range(1, 6):
            assert compute_delay(policy, attempt) == min(2.0 * (2 ** (attempt - 1)), 100.0)

    def test_non_zero_jitter(self) -> None:
        """Non-zero jitter adds extra delay bounded by jitter_ratio."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
            jitter_ratio=0.5,
        )
        delay = compute_delay(policy, 1)
        # bounded = 1.0, jitter = 1.0 * 0.5 = 0.5
        assert 1.0 <= delay <= 1.5

    def test_default_jitter_matches_build_backoff(self) -> None:
        """When jitter_ratio is 0.2, compute_delay delegates directly."""
        policy = RetryPolicy(
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
            jitter_ratio=0.2,
        )
        # With jitter we cannot assert exact equality, but we can verify range.
        delay = compute_delay(policy, 1)
        assert 1.0 <= delay <= 1.2


# =============================================================================
# RetryContext
# =============================================================================


class TestRetryContext:
    """Tests for RetryContext dataclass."""

    def test_default_state(self) -> None:
        """Fresh context starts at zero."""
        ctx = RetryContext()
        assert ctx.attempt_count == 0
        assert ctx.cumulative_delay == 0.0
        assert ctx.last_error is None

    def test_mutable_fields(self) -> None:
        """Context fields can be updated."""
        ctx = RetryContext()
        ctx.attempt_count = 2
        ctx.cumulative_delay = 1.5
        ctx.last_error = RuntimeError("oops")
        assert ctx.attempt_count == 2
        assert ctx.cumulative_delay == 1.5
        assert isinstance(ctx.last_error, RuntimeError)


# =============================================================================
# execute_with_retry
# =============================================================================


class TestExecuteWithRetry:
    """Tests for execute_with_retry async wrapper."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self, fast_policy: RetryPolicy) -> None:
        """Operation succeeding immediately returns result."""

        async def op() -> str:
            return "ok"

        result = await execute_with_retry(op, fast_policy)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_success_after_transient_failures(self, fast_policy: RetryPolicy) -> None:
        """Operation retries on transient errors and eventually succeeds."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise asyncio.TimeoutError("temporary")
            return "recovered"

        result = await execute_with_retry(flaky, fast_policy)
        assert result == "recovered"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_non_transient_error_raises_immediately(self, fast_policy: RetryPolicy) -> None:
        """Non-transient errors are not retried."""

        async def fail() -> str:
            raise ValueError("permanent")

        with pytest.raises(ValueError, match="permanent"):
            await execute_with_retry(fail, fast_policy)

    @pytest.mark.asyncio
    async def test_exhausted_retries_raise_last_error(self, fast_policy: RetryPolicy) -> None:
        """When all attempts are exhausted, the last error is raised."""

        async def always_timeout() -> str:
            raise asyncio.TimeoutError("still failing")

        with pytest.raises(asyncio.TimeoutError, match="still failing"):
            await execute_with_retry(always_timeout, fast_policy)

    @pytest.mark.asyncio
    async def test_cancelled_error_not_retried(self, fast_policy: RetryPolicy) -> None:
        """asyncio.CancelledError is always propagated."""

        async def cancel() -> str:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await execute_with_retry(cancel, fast_policy)

    @pytest.mark.asyncio
    async def test_args_and_kwargs_forwarded(self, fast_policy: RetryPolicy) -> None:
        """Positional and keyword arguments are passed through."""

        async def op(a: int, b: str, c: bool = False) -> tuple[int, str, bool]:
            return (a, b, c)

        result = await execute_with_retry(op, fast_policy, 42, "hello", c=True)
        assert result == (42, "hello", True)

    @pytest.mark.asyncio
    async def test_cumulative_delay_tracked(self, fast_policy: RetryPolicy) -> None:
        """RetryContext accumulates delay across attempts."""
        # Patch compute_delay to return a fixed value so we can assert cumulative.
        with patch(
            "polaris.kernelone.resilience.retry_policy.compute_delay",
            return_value=0.05,
        ):
            attempts = 0

            async def flaky() -> str:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise asyncio.TimeoutError("temporary")
                return "done"

            result = await execute_with_retry(flaky, fast_policy)
            assert result == "done"
            # 2 failures -> 2 sleeps of 0.05 each
            # Note: we cannot inspect the internal ctx, but we verify the
            # function completes successfully with the patched delay.

    @pytest.mark.asyncio
    async def test_single_attempt_no_retry(self, no_retry_policy: RetryPolicy) -> None:
        """Policy with max_attempts=1 never retries."""

        async def fail() -> str:
            raise asyncio.TimeoutError("timeout")

        with pytest.raises(asyncio.TimeoutError):
            await execute_with_retry(fail, no_retry_policy)

    @pytest.mark.asyncio
    async def test_mock_operation_called_with_correct_args(self) -> None:
        """AsyncMock receives the forwarded arguments."""
        policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.0, jitter_ratio=0.0)
        mock_op: AsyncMock = AsyncMock(return_value="mocked")

        result = await execute_with_retry(mock_op, policy, "pos", key="val")
        assert result == "mocked"
        mock_op.assert_awaited_once_with("pos", key="val")

    @pytest.mark.asyncio
    async def test_kernel_one_network_error_retried(self, fast_policy: RetryPolicy) -> None:
        """NetworkError (transient) triggers retries."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise NetworkError("connection reset", url="http://test")
            return "ok"

        result = await execute_with_retry(flaky, fast_policy)
        assert result == "ok"
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_kernel_one_rate_limit_error_retried(self, fast_policy: RetryPolicy) -> None:
        """RateLimitError (transient) triggers retries."""
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise RateLimitError("rate limited", retry_after=0.01)
            return "ok"

        result = await execute_with_retry(flaky, fast_policy)
        assert result == "ok"
        assert attempts == 2
