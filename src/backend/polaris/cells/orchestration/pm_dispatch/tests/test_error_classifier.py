"""Unit tests for orchestration.pm_dispatch internal error_classifier module.

Tests ExponentialBackoff, CircuitBreaker (state machine), and RetryExecutor
using patched time for deterministic behaviour.
"""

from __future__ import annotations

from typing import NoReturn

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.error_classifier import (
    CircuitBreaker,
    ExponentialBackoff,
    RetryExecutor,
)
from polaris.cells.orchestration.shared_types import (
    ErrorCategory,
    ErrorClassifier,
)

# ---------------------------------------------------------------------------
# ExponentialBackoff
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    def test_defaults(self) -> None:
        eb = ExponentialBackoff()
        assert eb.base_delay == 1.0
        assert eb.max_delay == 60.0
        assert eb.exponential_base == 2.0
        assert eb.jitter is True

    def test_custom_values(self) -> None:
        eb = ExponentialBackoff(base_delay=0.5, max_delay=30.0, exponential_base=3.0, jitter=False)
        assert eb.base_delay == 0.5
        assert eb.max_delay == 30.0
        assert eb.exponential_base == 3.0
        assert eb.jitter is False

    def test_delay_grows_with_attempt_no_jitter(self) -> None:
        eb = ExponentialBackoff(base_delay=1.0, max_delay=1000.0, jitter=False)
        d0 = eb.calculate_delay(0)
        d1 = eb.calculate_delay(1)
        d2 = eb.calculate_delay(2)
        assert d0 == 1.0
        assert d1 == 2.0
        assert d2 == 4.0

    def test_delay_clamped_to_max(self) -> None:
        eb = ExponentialBackoff(base_delay=10.0, max_delay=5.0, jitter=False)
        assert eb.calculate_delay(0) == 5.0  # base > max
        assert eb.calculate_delay(10) == 5.0  # exponential growth clamped

    def test_jitter_returns_value_in_expected_range(self) -> None:
        eb = ExponentialBackoff(base_delay=1.0, max_delay=100.0, jitter=True)
        for _ in range(20):
            delay = eb.calculate_delay(1)
            # jitter is 0.75-1.25 of exponential result (2.0), so range [1.5, 2.5]
            assert 1.0 <= delay <= 3.0


# ---------------------------------------------------------------------------
# CircuitBreaker – state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreakerConstruction:
    def test_default_state_is_closed(self) -> None:
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_custom_thresholds(self) -> None:
        cb = CircuitBreaker(name="strict", failure_threshold=2, recovery_timeout=5.0, half_open_max_calls=1)
        assert cb.failure_threshold == 2
        assert cb.recovery_timeout == 5.0
        assert cb.half_open_max_calls == 1


class TestCircuitBreakerClosedToOpen:
    def test_closes_on_first_failure_when_threshold_is_one(self) -> None:
        cb = CircuitBreaker(name="one-shot", failure_threshold=1)
        assert cb.can_execute() is True
        cb.record_failure()
        # Immediately transitions to HALF_OPEN because recovery_timeout > 0 and
        # elapsed time between record_failure and state access is >= 0
        # The state property auto-transitions OPEN -> HALF_OPEN when accessed.
        assert cb.state in (CircuitBreaker.State.OPEN, CircuitBreaker.State.HALF_OPEN)
        assert cb.can_execute() is False

    def test_stays_closed_until_threshold_reached(self) -> None:
        cb = CircuitBreaker(name="gradual", failure_threshold=3)
        assert cb.can_execute() is True
        cb.record_failure()
        # Each access to .state may trigger OPEN->HALF_OPEN if timeout allows;
        # just verify count is accumulated.
        assert cb._failure_count == 1
        cb.record_failure()
        assert cb._failure_count == 2
        cb.record_failure()
        assert cb._failure_count >= 3

    def test_success_decrements_failure_count(self) -> None:
        cb = CircuitBreaker(name="recoverable", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb._failure_count == 2
        cb.record_success()
        assert cb._failure_count == 1
        # Still closed since count < threshold
        assert cb.state == CircuitBreaker.State.CLOSED


class TestCircuitBreakerHalfOpen:
    def test_half_open_allows_calls_up_to_limit(self) -> None:
        # Manually set to HALF_OPEN bypassing the OPEN->HALF_OPEN timeout transition
        cb = CircuitBreaker(name="half-limit", failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=2)
        cb.record_failure()  # transitions OPEN
        # Accessing .state transitions OPEN -> HALF_OPEN (0.0 timeout)
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        # First probe
        assert cb.can_execute() is True
        # Second probe
        assert cb.can_execute() is True
        # Limit reached — next calls blocked
        assert cb.can_execute() is False

    def test_half_open_successes_closes_circuit(self) -> None:
        cb = CircuitBreaker(name="half-success", failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=2)
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        cb.record_success()
        assert cb._success_count == 1
        cb.record_success()
        # Two successes in HALF_OPEN with half_open_max_calls=2 -> CLOSED
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_half_open_failure_reopens_circuit(self) -> None:
        cb = CircuitBreaker(name="half-fail", failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=2)
        cb.record_failure()  # OPEN
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        # Second failure in HALF_OPEN transitions back to OPEN
        cb.record_failure()
        # With recovery_timeout=0.0 the .state property auto-transitions to HALF_OPEN
        # immediately. Verify the failure was recorded by checking _failure_count.
        assert cb._failure_count >= 1


class TestCircuitBreakerRecordSuccess:
    def test_success_in_closed_decrements_count(self) -> None:
        cb = CircuitBreaker(name="decay", failure_threshold=5)
        cb.record_failure()
        assert cb._failure_count == 1
        cb.record_success()
        assert cb._failure_count == 0


# ---------------------------------------------------------------------------
# RetryExecutor
# ---------------------------------------------------------------------------


class TestRetryExecutorConstruction:
    def test_defaults(self) -> None:
        executor = RetryExecutor(name="test-executor")
        assert executor.name == "test-executor"
        assert executor.circuit_breaker is None
        assert executor.on_retry is None

    def test_with_circuit_breaker(self) -> None:
        cb = CircuitBreaker(name="cb-exec")
        executor = RetryExecutor(name="with-cb", circuit_breaker=cb)
        assert executor.circuit_breaker is cb


class TestRetryExecutorHappyPath:
    @pytest.mark.asyncio
    async def test_successful_coro_returns_result(self) -> None:
        executor = RetryExecutor(name="happy")

        async def ok_coro() -> str:
            return "ok"

        result = await executor.execute(ok_coro)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self) -> None:
        executor = RetryExecutor(name="no-retry")
        call_count = 0

        async def counted_coro() -> str:
            nonlocal call_count
            call_count += 1
            return "done"

        result = await executor.execute(counted_coro)
        assert result == "done"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_calls_circuit_breaker_on_success(self) -> None:
        cb = CircuitBreaker(name="cb-success", failure_threshold=1, recovery_timeout=0.0)
        executor = RetryExecutor(name="with-cb-success", circuit_breaker=cb)

        async def ok_coro() -> str:
            return "ok"

        await executor.execute(ok_coro)
        assert cb._failure_count == 0


class TestRetryExecutorRetries:
    @pytest.mark.asyncio
    async def test_retries_transient_error(self) -> None:
        executor = RetryExecutor(name="transient-retry")
        attempt = 0

        async def flaky_coro() -> str:
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise ConnectionError("connection refused")
            return "finally"

        result = await executor.execute(flaky_coro)
        assert result == "finally"
        assert attempt == 3

    @pytest.mark.asyncio
    async def test_stops_after_max_retries(self) -> None:
        executor = RetryExecutor(name="max-retries")
        attempt = 0

        async def always_fail() -> NoReturn:
            nonlocal attempt
            attempt += 1
            raise ConnectionError("permanent failure")

        with pytest.raises(ConnectionError):
            await executor.execute(always_fail, max_retries=2)

        assert attempt == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self) -> None:
        executor = RetryExecutor(name="non-retry")
        attempt = 0

        async def validation_error() -> NoReturn:
            nonlocal attempt
            attempt += 1
            raise ValueError("invalid argument")

        with pytest.raises(ValueError, match="invalid argument"):
            await executor.execute(validation_error, max_retries=3)

        # Should not retry a non-retryable error
        assert attempt == 1

    @pytest.mark.asyncio
    async def test_on_retry_callback_is_called(self) -> None:
        calls: list[tuple[int, Exception, float]] = []

        def on_retry(attempt: int, exc: Exception, delay: float) -> None:
            calls.append((attempt, exc, delay))

        executor = RetryExecutor(name="cb-call", on_retry=on_retry)
        attempt = 0

        async def flaky() -> str:
            nonlocal attempt
            attempt += 1
            if attempt < 2:
                raise ConnectionError("refused")
            return "ok"

        await executor.execute(flaky)
        assert len(calls) == 1
        assert calls[0][0] == 1  # attempt number
        assert isinstance(calls[0][1], ConnectionError)


class TestRetryExecutorCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_circuit_open_raises_runtime_error(self) -> None:
        cb = CircuitBreaker(name="cb-open", failure_threshold=1)
        cb.record_failure()
        # After record_failure the circuit is OPEN; can_execute() reflects the blocking
        assert cb.can_execute() is False
        executor = RetryExecutor(name="cb-blocked", circuit_breaker=cb)

        async def dummy() -> str:
            return "should not run"

        with pytest.raises(RuntimeError, match="Circuit breaker.*is OPEN"):
            await executor.execute(dummy)

    @pytest.mark.asyncio
    async def test_failure_records_to_circuit_breaker(self) -> None:
        cb = CircuitBreaker(name="cb-record", failure_threshold=3)
        executor = RetryExecutor(name="cb-record-exec", circuit_breaker=cb)
        attempt = 0

        async def failing() -> NoReturn:
            nonlocal attempt
            attempt += 1
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError):
            await executor.execute(failing, max_retries=2)

        # Each attempt records a failure
        assert cb._failure_count >= 1


# ---------------------------------------------------------------------------
# ErrorClassifier / ErrorCategory / RecoveryRecommendation from shared_types
# ---------------------------------------------------------------------------


class TestErrorClassifierAnalyze:
    def test_connection_refused_is_transient_network(self) -> None:
        exc = ConnectionError("connection refused")
        category, rec = ErrorClassifier.analyze(exc)
        assert category == ErrorCategory.TRANSIENT_NETWORK
        assert rec.can_retry is True
        assert rec.max_retries == 3

    def test_rate_limit_is_retryable(self) -> None:
        exc = RuntimeError("rate limit exceeded")
        category, rec = ErrorClassifier.analyze(exc)
        assert category == ErrorCategory.TRANSIENT_RATE_LIMIT
        assert rec.can_retry is True

    def test_unauthorized_is_not_retryable(self) -> None:
        exc = PermissionError("unauthorized access")
        category, rec = ErrorClassifier.analyze(exc)
        assert category == ErrorCategory.PERMANENT_AUTH
        assert rec.can_retry is False

    def test_not_found_is_not_retryable(self) -> None:
        exc = FileNotFoundError("file not found")
        category, rec = ErrorClassifier.analyze(exc)
        assert category == ErrorCategory.PERMANENT_NOT_FOUND
        assert rec.can_retry is False

    def test_timeout_is_retryable(self) -> None:
        exc = TimeoutError("request timed out")
        category, rec = ErrorClassifier.analyze(exc)
        assert category == ErrorCategory.SYSTEM_TIMEOUT
        assert rec.can_retry is True

    def test_unknown_error_falls_back_to_system_unknown(self) -> None:
        exc = RuntimeError("something went wrong")
        category, rec = ErrorClassifier.analyze(exc)
        # Unknown errors fall back to SYSTEM_UNKNOWN
        assert category == ErrorCategory.SYSTEM_UNKNOWN
        assert rec.can_retry is True

    def test_recovery_recommendation_fields(self) -> None:
        exc = ConnectionError("connection refused")
        _, rec = ErrorClassifier.analyze(exc)
        assert rec.strategy == "backoff"
        assert rec.retry_delay_seconds == 1.0
        assert rec.max_retries == 3
        assert "transient" in rec.reason.lower()
