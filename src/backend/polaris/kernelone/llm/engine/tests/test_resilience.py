"""Tests for resilience policy - is_retryable_by_category, backoff jitter, and related utilities.

Covers:
- is_retryable_by_category() fast-fail for semantic errors
- is_retryable_by_category() retryable for transport-layer errors
- calculate_backoff_with_jitter() boundary conditions
- retry_with_jitter() basic retry behavior
- ResilienceManager initialization and retry configuration
"""

from __future__ import annotations

from typing import NoReturn

import pytest
from polaris.kernelone.llm.engine.resilience import (
    ErrorCategory,
    ResilienceManager,
    RetryConfig,
    TimeoutConfig,
    calculate_backoff_with_jitter,
    is_retryable,
    retry_with_jitter,
)


class TestIsRetryableByCategory:
    """Tests for ResilienceManager.is_retryable_by_category()."""

    def test_semantic_errors_fast_fail(self) -> None:
        """INVALID_RESPONSE, JSON_PARSE, CONFIG_ERROR must not be retried."""
        rm = ResilienceManager(
            timeout_config=TimeoutConfig(),
            retry_config=RetryConfig(),
        )
        assert rm.is_retryable_by_category(ErrorCategory.INVALID_RESPONSE) is False
        assert rm.is_retryable_by_category(ErrorCategory.JSON_PARSE) is False
        assert rm.is_retryable_by_category(ErrorCategory.CONFIG_ERROR) is False

    def test_transport_errors_retryable(self) -> None:
        """TIMEOUT, RATE_LIMIT, NETWORK_ERROR are retryable by default."""
        rm = ResilienceManager(
            timeout_config=TimeoutConfig(),
            retry_config=RetryConfig(),
        )
        assert rm.is_retryable_by_category(ErrorCategory.TIMEOUT) is True
        assert rm.is_retryable_by_category(ErrorCategory.RATE_LIMIT) is True
        assert rm.is_retryable_by_category(ErrorCategory.NETWORK_ERROR) is True

    def test_provider_error_not_retryable_by_default(self) -> None:
        """PROVIDER_ERROR is not in default retryable_errors, returns False."""
        rm = ResilienceManager(
            timeout_config=TimeoutConfig(),
            retry_config=RetryConfig(),
        )
        assert rm.is_retryable_by_category(ErrorCategory.PROVIDER_ERROR) is False

    def test_unknown_not_retryable(self) -> None:
        """UNKNOWN category is not in retryable_errors, returns False."""
        rm = ResilienceManager(
            timeout_config=TimeoutConfig(),
            retry_config=RetryConfig(),
        )
        assert rm.is_retryable_by_category(ErrorCategory.UNKNOWN) is False

    def test_custom_retryable_errors(self) -> None:
        """Custom retryable_errors list must be respected."""
        rm = ResilienceManager(
            timeout_config=TimeoutConfig(),
            retry_config=RetryConfig(
                retryable_errors=[
                    ErrorCategory.TIMEOUT,
                    ErrorCategory.RATE_LIMIT,
                    ErrorCategory.PROVIDER_ERROR,  # Added
                ],
            ),
        )
        assert rm.is_retryable_by_category(ErrorCategory.PROVIDER_ERROR) is True
        assert rm.is_retryable_by_category(ErrorCategory.NETWORK_ERROR) is False  # Not in custom list


class TestIsRetryable:
    """Tests for the standalone is_retryable(status_code) function."""

    def test_unauthorized_not_retryable(self) -> None:
        """401 and 403 are never retryable."""
        assert is_retryable(401) is False
        assert is_retryable(403) is False

    def test_client_errors_not_retryable(self) -> None:
        """400 and 422 are never retryable."""
        assert is_retryable(400) is False
        assert is_retryable(422) is False

    def test_rate_limit_retryable(self) -> None:
        """429 is retryable."""
        assert is_retryable(429) is True

    def test_server_errors_retryable(self) -> None:
        """5xx errors are retryable."""
        assert is_retryable(500) is True
        assert is_retryable(502) is True
        assert is_retryable(503) is True
        assert is_retryable(504) is True

    def test_none_is_retryable(self) -> None:
        """Unknown status code (None) defaults to retryable."""
        assert is_retryable(None) is True


class TestCalculateBackoffWithJitter:
    """Tests for calculate_backoff_with_jitter()."""

    def test_attempt_zero_gives_base_delay(self) -> None:
        """Attempt 0 must return base_delay (with jitter)."""
        for _ in range(5):
            delay = calculate_backoff_with_jitter(
                attempt=0,
                base_delay=1.0,
                max_delay=60.0,
                jitter_percent=0.0,  # No jitter for deterministic test
            )
            assert delay == 1.0

    def test_attempt_one_gives_double_base(self) -> None:
        """Attempt 1 must return 2 * base_delay."""
        for _ in range(5):
            delay = calculate_backoff_with_jitter(
                attempt=1,
                base_delay=1.0,
                max_delay=60.0,
                jitter_percent=0.0,
            )
            assert delay == 2.0

    def test_attempt_two_gives_quadruple_base(self) -> None:
        """Attempt 2 must return 4 * base_delay."""
        for _ in range(5):
            delay = calculate_backoff_with_jitter(
                attempt=2,
                base_delay=1.0,
                max_delay=60.0,
                jitter_percent=0.0,
            )
            assert delay == 4.0

    def test_max_delay_observed(self) -> None:
        """Delay must not exceed max_delay."""
        for _ in range(10):
            delay = calculate_backoff_with_jitter(
                attempt=100,  # Would be huge without cap
                base_delay=1.0,
                max_delay=5.0,
                jitter_percent=0.1,
            )
            assert delay <= 5.5  # 5.0 + 10% jitter

    def test_jitter_in_range(self) -> None:
        """Jitter must be between 0 and jitter_percent * delay."""
        base_delay = 10.0
        max_delay = 100.0
        jitter_percent = 0.1

        for _ in range(20):
            delay = calculate_backoff_with_jitter(
                attempt=1,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter_percent=jitter_percent,
            )
            # Without jitter: 2 * 10 = 20
            # With jitter: 20 + random(0, 2) = [20, 22]
            assert delay >= 20.0
            assert delay <= 22.0


class TestRetryWithJitter:
    """Tests for retry_with_jitter()."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self) -> None:
        """Function that succeeds on first try must not retry."""
        call_count = 0

        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_with_jitter(succeed, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_until_success(self) -> None:
        """Must retry until function succeeds."""
        call_count = 0

        async def fail_twice_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error")
            return "ok"

        result = await retry_with_jitter(fail_twice_then_succeed, max_retries=5, base_delay=0.01)
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Must raise after exhausting max_retries."""
        call_count = 0

        async def always_fail() -> NoReturn:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("permanent error")

        with pytest.raises(RuntimeError, match="permanent error"):
            await retry_with_jitter(always_fail, max_retries=3, base_delay=0.01)

        # max_retries=3 means 3 retries AFTER initial call: range(4) = [0,1,2,3] = 4 calls
        assert call_count == 4


class TestResilienceManagerInit:
    """Tests for ResilienceManager initialization."""

    def test_default_initialization(self) -> None:
        """Must initialize with default config values."""
        rm = ResilienceManager()
        assert rm.timeout_config.request_timeout == 60.0
        assert rm.timeout_config.total_timeout == 300.0
        assert rm.retry_config.max_attempts == 2
        assert rm.retry_config.base_delay == 1.0

    def test_custom_initialization(self) -> None:
        """Must accept custom config objects."""
        tc = TimeoutConfig(request_timeout=30.0, total_timeout=120.0)
        rc = RetryConfig(max_attempts=5, base_delay=2.0)

        rm = ResilienceManager(timeout_config=tc, retry_config=rc)

        assert rm.timeout_config.request_timeout == 30.0
        assert rm.timeout_config.total_timeout == 120.0
        assert rm.retry_config.max_attempts == 5
        assert rm.retry_config.base_delay == 2.0

    def test_retry_config_default_retryable_errors(self) -> None:
        """RetryConfig must default to transport-layer errors only."""
        rc = RetryConfig()
        assert ErrorCategory.TIMEOUT in rc.retryable_errors
        assert ErrorCategory.RATE_LIMIT in rc.retryable_errors
        assert ErrorCategory.NETWORK_ERROR in rc.retryable_errors
        assert ErrorCategory.INVALID_RESPONSE not in rc.retryable_errors
        assert ErrorCategory.JSON_PARSE not in rc.retryable_errors
        assert ErrorCategory.CONFIG_ERROR not in rc.retryable_errors
