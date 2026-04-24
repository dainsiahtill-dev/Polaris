"""Tests for circuit breaker implementation."""

from __future__ import annotations

import asyncio
import time
from typing import NoReturn

import pytest
from polaris.kernelone.llm.engine.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    calculate_backoff_with_jitter,
    is_retryable,
    retry_with_jitter,
)


class TestCircuitState:
    """Tests for circuit state transitions."""

    def test_initial_state_is_closed(self) -> None:
        """Circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    def test_config_defaults(self) -> None:
        """Test default configuration values."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60.0
        assert config.half_open_max_calls == 3
        assert config.success_threshold == 2
        assert config.window_seconds == 120.0

    def test_config_from_options(self) -> None:
        """Test configuration from options dict."""
        config = CircuitBreakerConfig.from_options(
            {
                "cb_failure_threshold": 10,
                "cb_recovery_timeout": 120.0,
                "cb_half_open_max_calls": 5,
                "cb_success_threshold": 3,
            }
        )
        assert config.failure_threshold == 10
        assert config.recovery_timeout == 120.0
        assert config.half_open_max_calls == 5
        assert config.success_threshold == 3


class TestCircuitBreakerClosedToOpen:
    """Tests for CLOSED -> OPEN transition."""

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self) -> None:
        """Circuit opens after reaching failure threshold."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(failure_threshold=3),
        )

        async def fail() -> NoReturn:
            raise ValueError("test error")

        # Fail until threshold
        for _i in range(3):
            with pytest.raises(ValueError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    @pytest.mark.asyncio
    async def test_closes_after_success_in_closed(self) -> None:
        """Failure count resets on success in CLOSED state."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(failure_threshold=3),
        )

        async def fail_once_then_succeed() -> str:
            if cb.failure_count < 1:
                raise ValueError("first failure")
            return "success"

        # First call fails
        with pytest.raises(ValueError):
            await cb.call(fail_once_then_succeed)

        assert cb.failure_count == 1

        # Second call succeeds, resets failure count
        result = await cb.call(fail_once_then_succeed)
        assert result == "success"
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerOpenToHalfOpen:
    """Tests for OPEN -> HALF_OPEN transition."""

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self) -> None:
        """Circuit transitions to HALF_OPEN after recovery timeout."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.1,  # 100ms
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("always fail")

        # Trigger OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout (with margin for timing variance)
        await asyncio.sleep(0.2)

        # Next call should transition to HALF_OPEN
        with pytest.raises(ValueError):
            await cb.call(fail)

        # State should be OPEN (failure in HALF_OPEN transitions back to OPEN)
        assert cb.state == CircuitState.OPEN
        assert cb.half_open_calls == 1  # Was briefly in HALF_OPEN before failure

    @pytest.mark.asyncio
    async def test_raises_open_error_before_timeout(self) -> None:
        """Circuit raises CircuitBreakerOpenError before timeout."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=10.0,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Trigger OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Immediate call should raise CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await cb.call(fail)

        assert exc_info.value.circuit_name == "test"
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerHalfOpenRecovery:
    """Tests for HALF_OPEN -> CLOSED recovery."""

    @pytest.mark.asyncio
    async def test_half_open_closes_after_success_threshold(self) -> None:
        """Circuit closes after reaching success threshold in HALF_OPEN."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                success_threshold=2,
            ),
        )

        success_count = 0

        async def succeed() -> str:
            nonlocal success_count
            success_count += 1
            return f"success_{success_count}"

        # Force OPEN state
        async def fail_always() -> NoReturn:
            raise ValueError("always fail")

        with pytest.raises(ValueError):
            await cb.call(fail_always)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # First call in HALF_OPEN
        result1 = await cb.call(succeed)
        assert result1 == "success_1"
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.half_open_successes == 1

        # Second call in HALF_OPEN - should close circuit
        result2 = await cb.call(succeed)
        assert result2 == "success_2"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_reopens_on_failure(self) -> None:
        """Circuit reopens immediately on failure in HALF_OPEN."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Force OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout (ensure enough time has passed)
        await asyncio.sleep(0.1)

        # Transition to HALF_OPEN and fail immediately -> back to OPEN
        with pytest.raises(ValueError):
            await cb.call(fail)

        # State is OPEN (failure in HALF_OPEN immediately reopens)
        assert cb.state == CircuitState.OPEN

        # Wait again for recovery timeout
        await asyncio.sleep(0.05)

        # Transition to HALF_OPEN and fail again
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerHalfOpenMaxCalls:
    """Tests for HALF_OPEN max calls limit."""

    @pytest.mark.asyncio
    async def test_half_open_rejects_over_max_calls(self) -> None:
        """Circuit rejects calls exceeding half_open_max_calls."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                half_open_max_calls=2,
                success_threshold=3,  # Prevent early transition to CLOSED
            ),
        )

        async def succeed() -> str:
            return "success"

        # Force OPEN state
        async def fail() -> NoReturn:
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await cb.call(fail)

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # First HALF_OPEN call succeeds
        result1 = await cb.call(succeed)
        assert result1 == "success"

        # Second HALF_OPEN call succeeds
        result2 = await cb.call(succeed)
        assert result2 == "success"

        # Third call should be rejected (exceeds half_open_max_calls=2)
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(succeed)


class TestCircuitBreakerStatus:
    """Tests for circuit breaker status/health check."""

    def test_get_status_returns_all_fields(self) -> None:
        """Status includes all relevant metrics."""
        cb = CircuitBreaker(
            name="test_breaker",
            config=CircuitBreakerConfig(failure_threshold=5),
        )

        status = cb.get_status()

        assert status["name"] == "test_breaker"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0
        assert status["success_count"] == 0
        assert status["half_open_calls"] == 0
        assert status["half_open_successes"] == 0
        assert "config" in status
        assert status["config"]["failure_threshold"] == 5

    def test_reset_clears_all_state(self) -> None:
        """Reset clears failure/success counts and returns to CLOSED."""
        cb = CircuitBreaker(name="test")

        # Manually set some state
        cb.failure_count = 10
        cb.success_count = 5
        cb.state = CircuitState.HALF_OPEN

        cb.reset()

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.last_failure_time is None
        assert cb.last_success_time is None


class TestJitter:
    """Tests for jitter calculation."""

    def test_calculate_backoff_with_jitter_exponential(self) -> None:
        """Jitter increases exponentially with attempt."""
        delays = [calculate_backoff_with_jitter(i, base_delay=1.0, max_delay=100.0) for i in range(5)]

        # Each delay should be roughly 2x the previous (with jitter)
        for i in range(1, len(delays)):
            # Delay should be within reasonable range
            assert delays[i] > 0
            # Base is 1.0 * 2^i, allow 50% tolerance for jitter
            expected_min = 1.0 * (2**i) * 0.5
            expected_max = 1.0 * (2**i) * 1.5
            assert delays[i] >= expected_min
            assert delays[i] <= expected_max

    def test_calculate_backoff_with_jitter_respects_max(self) -> None:
        """Jitter is capped at max_delay."""
        delay = calculate_backoff_with_jitter(100, base_delay=1.0, max_delay=10.0)
        assert delay <= 11.0  # max_delay + max jitter

    def test_calculate_backoff_with_jitter_has_variance(self) -> None:
        """Jitter introduces variance between calls."""
        delays = [calculate_backoff_with_jitter(3, base_delay=1.0, max_delay=100.0) for _ in range(100)]
        unique_delays = set(delays)
        # With 100 samples, should have multiple unique values
        assert len(unique_delays) > 1

    def test_calculate_backoff_with_jitter_custom_percent(self) -> None:
        """Custom jitter percentage is applied."""
        delay_small_jitter = calculate_backoff_with_jitter(5, base_delay=1.0, max_delay=100.0, jitter_percent=0.01)
        delay_large_jitter = calculate_backoff_with_jitter(5, base_delay=1.0, max_delay=100.0, jitter_percent=0.5)
        # Large jitter should be more variable (not deterministic test, but sanity check)
        base_value = 1.0 * (2**5)  # 32
        assert abs(delay_small_jitter - base_value) < base_value * 0.02  # ~1% variance
        assert abs(delay_large_jitter - base_value) < base_value * 0.6  # ~50% variance


class TestRetryWithJitter:
    """Tests for retry_with_jitter function."""

    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        """Retries function on failure up to max_retries."""
        attempts = 0

        async def flaky_function() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("transient error")
            return "success"

        result = await retry_with_jitter(flaky_function, max_retries=3, base_delay=0.01)

        assert result == "success"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Raises last exception after exhausting retries."""
        attempts = 0

        async def always_fails() -> NoReturn:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("permanent error")

        with pytest.raises(RuntimeError, match="permanent error"):
            await retry_with_jitter(always_fails, max_retries=2, base_delay=0.01)

        # Should have tried 3 times (1 original + 2 retries)
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_fast_fail_on_non_retryable(self) -> None:
        """Fast fails on non-retryable errors."""
        attempts = 0

        async def auth_error() -> NoReturn:
            nonlocal attempts
            attempts += 1
            raise Exception("HTTP 401 Unauthorized")

        with pytest.raises(Exception, match="401"):
            await retry_with_jitter(auth_error, max_retries=3, base_delay=0.01)

        # Should only attempt once (fast fail)
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self) -> None:
        """Returns immediately on success."""
        attempts = 0

        async def succeed() -> str:
            nonlocal attempts
            attempts += 1
            return "immediate success"

        result = await retry_with_jitter(succeed, max_retries=3)

        assert result == "immediate success"
        assert attempts == 1


class TestIsRetryable:
    """Tests for HTTP status code retry decision."""

    def test_non_retryable_auth_errors(self) -> None:
        """401 and 403 are not retryable."""
        assert is_retryable(401) is False
        assert is_retryable(403) is False

    def test_non_retryable_client_errors(self) -> None:
        """400 and 422 are not retryable."""
        assert is_retryable(400) is False
        assert is_retryable(422) is False

    def test_retryable_rate_limit(self) -> None:
        """429 is retryable."""
        assert is_retryable(429) is True

    def test_retryable_server_errors(self) -> None:
        """5xx errors are retryable."""
        assert is_retryable(500) is True
        assert is_retryable(502) is True
        assert is_retryable(503) is True
        assert is_retryable(504) is True

    def test_retryable_on_none(self) -> None:
        """None status defaults to retryable."""
        assert is_retryable(None) is True

    def test_non_retryable_success_codes(self) -> None:
        """2xx success codes are not retryable."""
        assert is_retryable(200) is False
        assert is_retryable(201) is False


class TestCircuitBreakerRegistry:
    """Tests for circuit breaker registry."""

    @pytest.mark.asyncio
    async def test_get_or_create_returns_same_instance(self) -> None:
        """Same name returns same breaker instance."""
        from polaris.kernelone.llm.engine.resilience import get_circuit_breaker_registry

        registry = get_circuit_breaker_registry()
        breaker1 = await registry.get_or_create("test")
        breaker2 = await registry.get_or_create("test")

        assert breaker1 is breaker2

    @pytest.mark.asyncio
    async def test_get_or_create_different_names(self) -> None:
        """Different names return different breakers."""
        from polaris.kernelone.llm.engine.resilience import get_circuit_breaker_registry

        registry = get_circuit_breaker_registry()
        breaker1 = await registry.get_or_create("test1")
        breaker2 = await registry.get_or_create("test2")

        assert breaker1 is not breaker2
        assert breaker1.name == "test1"
        assert breaker2.name == "test2"

    def test_get_all_status(self) -> None:
        """Get status of all registered breakers."""
        from polaris.kernelone.llm.engine.resilience import get_circuit_breaker_registry

        registry = get_circuit_breaker_registry()
        registry._breakers.clear()  # Reset for test isolation

        # Create breakers
        cb1 = CircuitBreaker(name="breaker1")
        cb2 = CircuitBreaker(name="breaker2")
        registry._breakers["breaker1"] = cb1
        registry._breakers["breaker2"] = cb2

        status = registry.get_all_status()

        assert "breaker1" in status
        assert "breaker2" in status
        assert status["breaker1"]["state"] == "closed"
        assert status["breaker2"]["state"] == "closed"

    def test_remove_existing(self) -> None:
        """Remove returns True for existing breaker."""
        from polaris.kernelone.llm.engine.resilience import get_circuit_breaker_registry

        registry = get_circuit_breaker_registry()
        registry._breakers.clear()
        registry._breakers["test"] = CircuitBreaker(name="test")

        result = registry.remove("test")

        assert result is True
        assert "test" not in registry._breakers

    def test_remove_nonexistent(self) -> None:
        """Remove returns False for nonexistent breaker."""
        from polaris.kernelone.llm.engine.resilience import get_circuit_breaker_registry

        registry = get_circuit_breaker_registry()
        registry._breakers.clear()

        result = registry.remove("nonexistent")

        assert result is False


class TestThunderingHerdPrevention:
    """Tests verifying thundering herd prevention."""

    @pytest.mark.asyncio
    async def test_jitter_prevents_synchronized_retries(self) -> None:
        """Multiple concurrent retries should have different delays."""
        import asyncio

        start_times: list[float] = []

        async def timed_operation() -> NoReturn:
            start_times.append(time.monotonic())
            # Simulate work
            await asyncio.sleep(0.01)
            raise RuntimeError("fail")

        # Launch multiple concurrent retries
        tasks = []
        for _ in range(10):
            task = asyncio.create_task(retry_with_jitter(timed_operation, max_retries=1, base_delay=0.1))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should fail
        assert all(isinstance(r, RuntimeError) for r in results)

        # Calculate time spans between start times
        if len(start_times) >= 2:
            start_times.sort()
            time_diffs = [start_times[i + 1] - start_times[i] for i in range(len(start_times) - 1)]
            # At least some time differences should be non-zero due to jitter
            nonzero_diffs = [d for d in time_diffs if d > 0.001]
            # With proper jitter, we expect some spread
            assert len(nonzero_diffs) > 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_prevents_thundering_herd(self) -> None:
        """Open circuit prevents all requests from hitting service."""
        from polaris.kernelone.llm.engine.resilience import get_circuit_breaker_registry

        registry = get_circuit_breaker_registry()
        breaker = await registry.get_or_create("thunder_test")
        breaker.reset()
        breaker.state = CircuitState.OPEN
        breaker.last_failure_time = time.monotonic()  # Prevent immediate reset
        breaker.config.recovery_timeout = 10.0  # Long timeout

        call_count = 0

        async def count_calls() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        # Try many concurrent calls
        tasks = []
        for _ in range(100):
            try:
                task = asyncio.create_task(breaker.call(count_calls))
                tasks.append(task)
            except CircuitBreakerOpenError:
                pass

        # Collect results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Should have many CircuitBreakerOpenError
        open_errors = sum(1 for r in results if isinstance(r, CircuitBreakerOpenError))
        # Most calls should be rejected
        assert open_errors > 0
        # Function should not be called at all
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_half_open_limits_concurrent_requests(self) -> None:
        """Half-open state limits concurrent test requests."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.001,
                half_open_max_calls=2,
                success_threshold=3,  # Prevent early transition to CLOSED
            ),
        )

        # Force OPEN -> HALF_OPEN
        cb.state = CircuitState.OPEN
        cb.last_failure_time = time.monotonic() - 0.1  # Already past timeout

        call_count = 0

        async def track_call() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        # First two calls should succeed
        await cb.call(track_call)
        await cb.call(track_call)
        assert call_count == 2
        assert cb.half_open_calls == 2

        # Third call should be rejected
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(track_call)

        assert call_count == 2  # No new call


class TestResilienceManagerWithCircuitBreaker:
    """Tests for ResilienceManager integration with circuit breaker."""

    @pytest.mark.asyncio
    async def test_resilience_manager_accepts_circuit_breaker(self) -> None:
        """ResilienceManager can be configured with a circuit breaker."""
        from polaris.kernelone.llm.engine.resilience import (
            CircuitBreaker,
            ResilienceManager,
        )

        cb = CircuitBreaker(name="test")
        manager = ResilienceManager(circuit_breaker=cb)

        assert manager.circuit_breaker is cb

    @pytest.mark.asyncio
    async def test_get_circuit_breaker_status(self) -> None:
        """ResilienceManager exposes circuit breaker status."""
        from polaris.kernelone.llm.engine.resilience import (
            CircuitBreaker,
            ResilienceManager,
        )

        cb = CircuitBreaker(name="test_status")
        manager = ResilienceManager(circuit_breaker=cb)

        status = manager.get_circuit_breaker_status()

        assert status is not None
        assert status["name"] == "test_status"
        assert status["state"] == "closed"

    @pytest.mark.asyncio
    async def test_get_circuit_breaker_status_when_none(self) -> None:
        """Returns None when no circuit breaker configured."""
        from polaris.kernelone.llm.engine.resilience import ResilienceManager

        manager = ResilienceManager()
        status = manager.get_circuit_breaker_status()

        assert status is None
