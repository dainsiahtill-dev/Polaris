"""Tests for timeout utilities.

Tests cover:
- Normal execution with timeout protection
- Timeout scenarios
- Cancellation handling
- Optional timeout behavior
- Edge cases and boundary conditions
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from polaris.cells.llm.evaluation.internal.timeout import (
    DEFAULT_SUITE_TIMEOUT,
    TimeoutConfig,
    TimeoutResult,
    run_with_timeout,
    run_with_timeout_optional,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fast_event_loop():
    """Provide a fast event loop for testing."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# TimeoutConfig Tests
# =============================================================================


class TestTimeoutConfig:
    """Tests for TimeoutConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = TimeoutConfig()
        assert config.suite_timeout_sec == 300.0
        assert config.case_timeout_sec == 120.0
        assert config.default_suite_timeout == 300.0
        assert config.default_case_timeout == 120.0
        assert config.enable_timeout is True

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = TimeoutConfig(
            suite_timeout_sec=600.0,
            case_timeout_sec=180.0,
            default_suite_timeout=500.0,
            default_case_timeout=150.0,
            enable_timeout=False,
        )
        assert config.suite_timeout_sec == 600.0
        assert config.case_timeout_sec == 180.0
        assert config.enable_timeout is False

    def test_validation_positive_timeout(self) -> None:
        """Test that positive timeouts are valid."""
        config = TimeoutConfig(suite_timeout_sec=1.0, case_timeout_sec=0.5)
        assert config.suite_timeout_sec == 1.0

    def test_validation_zero_timeout_raises(self) -> None:
        """Test that zero timeout raises ValueError."""
        with pytest.raises(ValueError, match="suite_timeout_sec must be positive"):
            TimeoutConfig(suite_timeout_sec=0.0)

    def test_validation_negative_timeout_raises(self) -> None:
        """Test that negative timeout raises ValueError."""
        with pytest.raises(ValueError, match="case_timeout_sec must be positive"):
            TimeoutConfig(case_timeout_sec=-10.0)

    def test_from_options_full(self) -> None:
        """Test creating config from full options dict."""
        options: dict[str, Any] = {
            "suite_timeout_sec": 500.0,
            "case_timeout_sec": 200.0,
            "default_suite_timeout": 400.0,
            "default_case_timeout": 180.0,
            "enable_timeout": False,
        }
        config = TimeoutConfig.from_options(options)
        assert config.suite_timeout_sec == 500.0
        assert config.case_timeout_sec == 200.0
        assert config.enable_timeout is False

    def test_from_options_partial(self) -> None:
        """Test creating config from partial options dict."""
        options: dict[str, Any] = {"suite_timeout_sec": 400.0}
        config = TimeoutConfig.from_options(options)
        assert config.suite_timeout_sec == 400.0
        assert config.case_timeout_sec == 120.0  # default
        assert config.enable_timeout is True  # default

    def test_from_options_empty(self) -> None:
        """Test creating config from empty options dict."""
        config = TimeoutConfig.from_options({})
        assert config.suite_timeout_sec == 300.0  # default

    def test_from_options_with_invalid_types(self) -> None:
        """Test that invalid types use defaults."""
        options: dict[str, Any] = {
            "suite_timeout_sec": "invalid",  # type: ignore
            "enable_timeout": "yes",  # type: ignore
        }
        config = TimeoutConfig.from_options(options)
        assert config.suite_timeout_sec == 300.0  # default used
        assert config.enable_timeout is True  # default used


# =============================================================================
# TimeoutResult Tests
# =============================================================================


class TestTimeoutResult:
    """Tests for TimeoutResult dataclass."""

    def test_default_values(self) -> None:
        """Test default result values."""
        result = TimeoutResult()
        assert result.ok is False
        assert result.result is None
        assert result.error == ""
        assert result.timed_out is False
        assert result.elapsed_ms == 0

    def test_success_result(self) -> None:
        """Test success result values."""
        result = TimeoutResult(
            ok=True,
            result={"data": "test"},
            elapsed_ms=150,
        )
        assert result.ok is True
        assert result.result == {"data": "test"}
        assert result.timed_out is False
        assert result.elapsed_ms == 150

    def test_timeout_result(self) -> None:
        """Test timeout result values."""
        result = TimeoutResult(
            ok=False,
            error="operation timed out",
            timed_out=True,
            elapsed_ms=3000,
        )
        assert result.ok is False
        assert result.timed_out is True
        assert "timed out" in result.error

    def test_error_result(self) -> None:
        """Test error result values."""
        result = TimeoutResult(
            ok=False,
            error="something went wrong",
            timed_out=False,
            elapsed_ms=50,
        )
        assert result.ok is False
        assert result.timed_out is False
        assert result.error == "something went wrong"


# =============================================================================
# run_with_timeout Tests
# =============================================================================


class TestRunWithTimeout:
    """Tests for run_with_timeout function."""

    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        """Test successful coroutine execution within timeout."""

        async def quick_operation() -> str:
            return "success"

        result = await run_with_timeout(quick_operation(), 5.0, "test_op")
        assert result.ok is True
        assert result.result == "success"
        assert result.timed_out is False
        assert result.elapsed_ms < 1000

    @pytest.mark.asyncio
    async def test_timeout_occurs(self) -> None:
        """Test that timeout is properly detected."""
        long_delay = 0.1  # 100ms

        async def slow_operation() -> str:
            await asyncio.sleep(long_delay)
            return "should not complete"

        # Use timeout shorter than operation
        result = await run_with_timeout(slow_operation(), 0.05, "slow_op")
        assert result.ok is False
        assert result.timed_out is True
        assert "timed out" in result.error
        assert result.elapsed_ms >= 50

    @pytest.mark.asyncio
    async def test_exact_timeout_boundary(self) -> None:
        """Test behavior at exact timeout boundary."""
        # Use slightly larger delay to account for timing imprecision on Windows
        exact_delay = 0.01  # 10ms
        timeout_val = 0.05  # 50ms timeout (5x the delay)

        async def exact_operation() -> str:
            await asyncio.sleep(exact_delay)
            return "exact"

        # Timeout with generous margin
        result = await run_with_timeout(exact_operation(), timeout_val, "exact_op")
        # Should succeed since delay is well within timeout
        assert result.ok is True
        assert result.result == "exact"

    @pytest.mark.asyncio
    async def test_zero_timeout_raises(self) -> None:
        """Test that zero timeout raises ValueError."""

        async def dummy() -> str:
            return "dummy"

        with pytest.raises(ValueError, match="timeout_sec must be positive"):
            await run_with_timeout(dummy(), 0.0, "zero_op")

    @pytest.mark.asyncio
    async def test_negative_timeout_raises(self) -> None:
        """Test that negative timeout raises ValueError."""

        async def dummy() -> str:
            return "dummy"

        with pytest.raises(ValueError, match="timeout_sec must be positive"):
            await run_with_timeout(dummy(), -1.0, "negative_op")

    @pytest.mark.asyncio
    async def test_exception_handling(self) -> None:
        """Test that exceptions are properly caught and reported."""

        async def failing_operation() -> str:
            raise ValueError("test error")

        result = await run_with_timeout(failing_operation(), 5.0, "failing_op")
        assert result.ok is False
        assert result.timed_out is False
        assert "test error" in result.error

    @pytest.mark.asyncio
    async def test_exception_includes_operation_name(self) -> None:
        """Test that error message includes operation name."""

        async def failing_operation() -> str:
            raise RuntimeError("specific error")

        result = await run_with_timeout(failing_operation(), 5.0, "my_custom_operation")
        assert "my_custom_operation" in result.error

    @pytest.mark.asyncio
    async def test_cancelled_error_re_raised(self) -> None:
        """Test that CancelledError is properly re-raised."""
        operation_started = asyncio.Event()

        async def cancellable_operation() -> str:
            operation_started.set()
            await asyncio.sleep(10.0)  # Long sleep
            return "cancelled"

        async def cancel_after_start() -> None:
            await operation_started.wait()
            # Schedule cancellation
            asyncio.get_event_loop().call_later(0.01, lambda: None)

        # Start operation
        task = asyncio.create_task(cancellable_operation())
        await operation_started.wait()

        # Cancel the task
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await run_with_timeout(task, 5.0, "cancellable_op")


# =============================================================================
# run_with_timeout_optional Tests
# =============================================================================


class TestRunWithTimeoutOptional:
    """Tests for run_with_timeout_optional function."""

    @pytest.mark.asyncio
    async def test_with_valid_timeout(self) -> None:
        """Test with a valid positive timeout."""

        async def operation() -> str:
            return "success"

        result = await run_with_timeout_optional(operation(), 5.0, "test_op")
        assert result.ok is True
        assert result.result == "success"

    @pytest.mark.asyncio
    async def test_with_none_timeout(self) -> None:
        """Test with None timeout (no timeout protection)."""

        async def operation() -> str:
            await asyncio.sleep(0.01)
            return "success"

        result = await run_with_timeout_optional(operation(), None, "test_op")
        assert result.ok is True
        assert result.result == "success"

    @pytest.mark.asyncio
    async def test_with_zero_timeout(self) -> None:
        """Test with zero timeout (no timeout protection)."""

        async def operation() -> str:
            return "success"

        result = await run_with_timeout_optional(operation(), 0.0, "test_op")
        assert result.ok is True
        assert result.result == "success"

    @pytest.mark.asyncio
    async def test_with_negative_timeout(self) -> None:
        """Test with negative timeout (no timeout protection)."""

        async def operation() -> str:
            return "success"

        result = await run_with_timeout_optional(operation(), -5.0, "test_op")
        assert result.ok is True
        assert result.result == "success"

    @pytest.mark.asyncio
    async def test_timeout_works_when_specified(self) -> None:
        """Test that timeout actually works when specified."""

        async def slow_operation() -> str:
            await asyncio.sleep(0.2)
            return "slow"

        result = await run_with_timeout_optional(slow_operation(), 0.05, "slow_op")
        assert result.ok is False
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_exception_handling_no_timeout(self) -> None:
        """Test exception handling when timeout is disabled."""

        async def failing_operation() -> str:
            raise RuntimeError("test error")

        result = await run_with_timeout_optional(failing_operation(), None, "failing_op")
        assert result.ok is False
        assert "test error" in result.error

    @pytest.mark.asyncio
    async def test_cancelled_error_re_raised_no_timeout(self) -> None:
        """Test that CancelledError is re-raised even without timeout."""

        async def cancellable_operation() -> str:
            await asyncio.sleep(10.0)
            return "should not complete"

        task = asyncio.create_task(cancellable_operation())
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await run_with_timeout_optional(task, None, "cancellable_op")


# =============================================================================
# Default Configuration Tests
# =============================================================================


class TestDefaultConfiguration:
    """Tests for DEFAULT_SUITE_TIMEOUT."""

    def test_default_is_valid_config(self) -> None:
        """Test that default config is valid."""
        assert isinstance(DEFAULT_SUITE_TIMEOUT, TimeoutConfig)
        assert DEFAULT_SUITE_TIMEOUT.enable_timeout is True
        assert DEFAULT_SUITE_TIMEOUT.suite_timeout_sec > 0
        assert DEFAULT_SUITE_TIMEOUT.case_timeout_sec > 0

    def test_default_suite_timeout_value(self) -> None:
        """Test default suite timeout is 5 minutes."""
        assert DEFAULT_SUITE_TIMEOUT.suite_timeout_sec == 300.0

    def test_default_case_timeout_value(self) -> None:
        """Test default case timeout is 2 minutes."""
        assert DEFAULT_SUITE_TIMEOUT.case_timeout_sec == 120.0


# =============================================================================
# Integration-like Tests
# =============================================================================


class TestTimeoutIntegration:
    """Integration-style tests for timeout behavior."""

    @pytest.mark.asyncio
    async def test_multiple_operations_same_timeout(self) -> None:
        """Test running multiple operations with same timeout."""
        ops = [
            run_with_timeout(asyncio.sleep(0.01), 1.0, "op1"),
            run_with_timeout(asyncio.sleep(0.01), 1.0, "op2"),
            run_with_timeout(asyncio.sleep(0.01), 1.0, "op3"),
        ]

        results = await asyncio.gather(*ops)

        assert all(r.ok for r in results)
        assert all(r.result is None for r in results)  # sleep returns None

    @pytest.mark.asyncio
    async def test_mixed_success_and_timeout(self) -> None:
        """Test mix of successful and timed-out operations."""
        ops = [
            run_with_timeout(asyncio.sleep(0.01), 0.1, "fast"),
            run_with_timeout(asyncio.sleep(0.5), 0.05, "slow"),
            run_with_timeout(asyncio.sleep(0.01), 0.1, "fast2"),
        ]

        results = await asyncio.gather(*ops)

        assert results[0].ok is True
        assert results[1].timed_out is True
        assert results[2].ok is True

    @pytest.mark.asyncio
    async def test_sequential_timeout_checks(self) -> None:
        """Test sequential operations with different timeouts."""
        result1 = await run_with_timeout(asyncio.sleep(0.01), 0.1, "first")
        result2 = await run_with_timeout(asyncio.sleep(0.5), 0.1, "second")
        result3 = await run_with_timeout(asyncio.sleep(0.01), 0.1, "third")

        assert result1.ok is True
        assert result2.timed_out is True
        assert result3.ok is True
