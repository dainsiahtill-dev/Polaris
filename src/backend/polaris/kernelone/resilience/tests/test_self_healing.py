"""Tests for SelfHealingExecutor."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.errors import (
    KernelOneError,
    NetworkError,
    RateLimitError,
    ToolExecutionError,
)
from polaris.kernelone.resilience.self_healing import (
    AlternativeStrategy,
    FailureType,
    HealingResult,
    RetryStrategy,
    SelfHealingExecutor,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def retry_strategy() -> RetryStrategy:
    """Standard retry strategy for tests."""
    return RetryStrategy(
        max_attempts=3,
        base_delay=0.01,
        exponential_base=2.0,
        max_delay=0.1,
        jitter=False,
    )


@pytest.fixture
def executor(retry_strategy: RetryStrategy) -> SelfHealingExecutor:
    """Create executor with standard retry strategy."""
    return SelfHealingExecutor(retry_strategy=retry_strategy)


# =============================================================================
# Test: Successful Execution
# =============================================================================


@pytest.mark.asyncio
async def test_execute_success_first_try(executor: SelfHealingExecutor) -> None:
    """Execution succeeds on first try without any retries."""

    async def succeed() -> str:
        return "success"

    result = await executor.execute(succeed)

    assert result.success is True
    assert result.final_result == "success"
    assert result.attempts == 1
    assert result.strategies_tried == ()
    assert result.final_error is None


@pytest.mark.asyncio
async def test_execute_success_after_retries(retry_strategy: RetryStrategy) -> None:
    """Execution succeeds after transient failures are retried."""

    executor = SelfHealingExecutor(retry_strategy=retry_strategy)
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise asyncio.TimeoutError("temporary failure")
        return "recovered"

    result = await executor.execute(flaky)

    assert result.success is True
    assert result.final_result == "recovered"
    assert result.attempts == 3


# =============================================================================
# Test: Permanent Failure (no retry)
# =============================================================================


@pytest.mark.asyncio
async def test_execute_permanent_failure_no_retry(
    retry_strategy: RetryStrategy,
) -> None:
    """Permanent failures are not retried."""
    executor = SelfHealingExecutor(retry_strategy=retry_strategy)

    async def always_fail() -> str:
        raise ValueError("invalid input")

    result = await executor.execute(always_fail)

    assert result.success is False
    assert result.final_result is None
    assert result.final_error == "All strategies exhausted"


# =============================================================================
# Test: Alternative Strategies
# =============================================================================


@pytest.mark.asyncio
async def test_execute_alternative_after_primary_fails(
    retry_strategy: RetryStrategy,
) -> None:
    """Alternative strategy is used after primary exhausts retries."""

    async def fallback_func() -> str:
        return "fallback_result"

    alternatives = [
        AlternativeStrategy(
            name="fallback",
            description="Fallback method",
            execute=fallback_func,
        )
    ]
    executor = SelfHealingExecutor(
        retry_strategy=retry_strategy,
        alternatives=alternatives,
    )

    async def always_fail() -> str:
        raise ConnectionError("connection refused")

    result = await executor.execute(always_fail)

    assert result.success is True
    assert result.final_result == "fallback_result"
    assert "fallback" in result.strategies_tried


@pytest.mark.asyncio
async def test_execute_multiple_alternatives(
    retry_strategy: RetryStrategy,
) -> None:
    """Multiple alternatives are tried in order."""

    async def alt1_fail() -> str:
        raise ConnectionError("failed")

    async def alt2_success() -> str:
        return "alt2_result"

    alternatives = [
        AlternativeStrategy(
            name="alt1",
            description="First alternative",
            execute=alt1_fail,
        ),
        AlternativeStrategy(
            name="alt2",
            description="Second alternative",
            execute=alt2_success,
        ),
    ]
    executor = SelfHealingExecutor(
        retry_strategy=retry_strategy,
        alternatives=alternatives,
    )

    async def primary_fail() -> str:
        raise ValueError("permanent")

    result = await executor.execute(primary_fail)

    assert result.success is True
    assert result.final_result == "alt2_result"
    # Primary permanent failure is recorded before alternatives
    assert "primary_permanent_1" in result.strategies_tried
    assert "alt1" in result.strategies_tried
    assert "alt2" in result.strategies_tried


@pytest.mark.asyncio
async def test_execute_all_alternatives_fail(retry_strategy: RetryStrategy) -> None:
    """Result is failure when all alternatives also fail."""

    async def alt1_fail() -> str:
        raise RuntimeError("alt1 failed")

    async def alt2_fail() -> str:
        raise RuntimeError("alt2 failed")

    alternatives = [
        AlternativeStrategy(
            name="alt1",
            description="First alternative",
            execute=alt1_fail,
        ),
        AlternativeStrategy(
            name="alt2",
            description="Second alternative",
            execute=alt2_fail,
        ),
    ]
    executor = SelfHealingExecutor(
        retry_strategy=retry_strategy,
        alternatives=alternatives,
    )

    async def primary_fail() -> str:
        raise ValueError("permanent")

    result = await executor.execute(primary_fail)

    assert result.success is False
    assert result.final_result is None
    assert result.final_error == "All strategies exhausted"
    assert result.strategies_tried == ("primary_permanent_1", "primary_exhausted", "alt1", "alt2")


# =============================================================================
# Test: Failure Classification
# =============================================================================


class TestFailureClassification:
    """Tests for _classify_failure method."""

    def test_transient_timeout_error(self, executor: SelfHealingExecutor) -> None:
        """asyncio.TimeoutError is classified as TRANSIENT."""
        result = executor._classify_failure(asyncio.TimeoutError("timed out"))
        assert result == FailureType.TRANSIENT

    def test_transient_connection_error(self, executor: SelfHealingExecutor) -> None:
        """ConnectionError is classified as TRANSIENT."""
        result = executor._classify_failure(ConnectionError("connection failed"))
        assert result == FailureType.TRANSIENT

    def test_permanent_value_error(self, executor: SelfHealingExecutor) -> None:
        """ValueError is classified as PERMANENT."""
        result = executor._classify_failure(ValueError("invalid value"))
        assert result == FailureType.PERMANENT

    def test_permanent_key_error(self, executor: SelfHealingExecutor) -> None:
        """KeyError is classified as PERMANENT."""
        result = executor._classify_failure(KeyError("missing key"))
        assert result == FailureType.PERMANENT

    def test_kernel_one_error_retryable_true(
        self,
        executor: SelfHealingExecutor,
    ) -> None:
        """KernelOneError with retryable=True is TRANSIENT."""
        error = ToolExecutionError("tool failed", retryable=True)
        result = executor._classify_failure(error)
        assert result == FailureType.TRANSIENT

    def test_kernel_one_error_retryable_false(
        self,
        executor: SelfHealingExecutor,
    ) -> None:
        """KernelOneError with retryable=False is PERMANENT."""
        error = ToolExecutionError("tool failed", retryable=False)
        result = executor._classify_failure(error)
        assert result == FailureType.PERMANENT

    def test_unknown_error_type(self, executor: SelfHealingExecutor) -> None:
        """RuntimeError (not in lists) is UNKNOWN."""
        result = executor._classify_failure(RuntimeError("weird error"))
        assert result == FailureType.UNKNOWN

    def test_transient_keyword_in_message(self, executor: SelfHealingExecutor) -> None:
        """Error message with 'timeout' is TRANSIENT."""
        result = executor._classify_failure(RuntimeError("operation timeout"))
        assert result == FailureType.TRANSIENT

    def test_transient_keyword_connection(self, executor: SelfHealingExecutor) -> None:
        """Error message with 'connection' is TRANSIENT."""
        result = executor._classify_failure(RuntimeError("connection reset"))
        assert result == FailureType.TRANSIENT

    def test_permanent_keyword_not_found(self, executor: SelfHealingExecutor) -> None:
        """Error message with 'not found' is PERMANENT."""
        result = executor._classify_failure(RuntimeError("resource not found"))
        assert result == FailureType.PERMANENT

    def test_permanent_keyword_invalid(self, executor: SelfHealingExecutor) -> None:
        """Error message with 'invalid' is PERMANENT."""
        result = executor._classify_failure(RuntimeError("invalid parameter"))
        assert result == FailureType.PERMANENT

    def test_network_error_transient(self, executor: SelfHealingExecutor) -> None:
        """NetworkError with retryable=True is TRANSIENT."""
        error = NetworkError("connection failed", url="http://example.com")
        result = executor._classify_failure(error)
        assert result == FailureType.TRANSIENT

    def test_rate_limit_error_transient(self, executor: SelfHealingExecutor) -> None:
        """RateLimitError is TRANSIENT."""
        error = RateLimitError("rate limited", retry_after=1.0)
        result = executor._classify_failure(error)
        assert result == FailureType.TRANSIENT


# =============================================================================
# Test: Retry Strategy Configuration
# =============================================================================


class TestRetryStrategy:
    """Tests for RetryStrategy dataclass."""

    def test_default_values(self) -> None:
        """Default retry strategy has sensible values."""
        strategy = RetryStrategy()
        assert strategy.max_attempts == 3
        assert strategy.base_delay == 1.0
        assert strategy.exponential_base == 2.0
        assert strategy.max_delay == 30.0
        assert strategy.jitter is True

    def test_custom_values(self) -> None:
        """Custom retry strategy values are preserved."""
        strategy = RetryStrategy(
            max_attempts=5,
            base_delay=0.5,
            exponential_base=3.0,
            max_delay=60.0,
            jitter=False,
        )
        assert strategy.max_attempts == 5
        assert strategy.base_delay == 0.5
        assert strategy.exponential_base == 3.0
        assert strategy.max_delay == 60.0
        assert strategy.jitter is False


# =============================================================================
# Test: Delay Calculation
# =============================================================================


class TestDelayCalculation:
    """Tests for _calculate_delay method."""

    def test_exponential_backoff_no_jitter(self) -> None:
        """Delay grows exponentially without jitter."""
        strategy = RetryStrategy(
            base_delay=1.0,
            exponential_base=2.0,
            max_delay=100.0,
            jitter=False,
        )
        executor = SelfHealingExecutor(retry_strategy=strategy)

        # Attempt 0: 1.0 * 2^0 = 1.0
        assert executor._calculate_delay(0) == 1.0
        # Attempt 1: 1.0 * 2^1 = 2.0
        assert executor._calculate_delay(1) == 2.0
        # Attempt 2: 1.0 * 2^2 = 4.0
        assert executor._calculate_delay(2) == 4.0

    def test_max_delay_cap(self) -> None:
        """Delay is capped at max_delay."""
        strategy = RetryStrategy(
            base_delay=10.0,
            exponential_base=2.0,
            max_delay=15.0,
            jitter=False,
        )
        executor = SelfHealingExecutor(retry_strategy=strategy)

        # 10.0 * 2^2 = 40.0, capped to 15.0
        assert executor._calculate_delay(2) == 15.0

    def test_jitter_adds_randomness(self) -> None:
        """Jitter adds random value between 0 and 20% of delay."""
        strategy = RetryStrategy(
            base_delay=1.0,
            exponential_base=2.0,
            max_delay=100.0,
            jitter=True,
        )
        executor = SelfHealingExecutor(retry_strategy=strategy)

        base_delay = 1.0
        results = [executor._calculate_delay(0) for _ in range(10)]

        # All results should be between base_delay and base_delay * 1.2
        for result in results:
            assert base_delay <= result <= base_delay * 1.2


# =============================================================================
# Test: HealingResult Dataclass
# =============================================================================


class TestHealingResult:
    """Tests for HealingResult dataclass."""

    def test_success_result(self) -> None:
        """Success result has correct fields."""
        result = HealingResult(
            success=True,
            final_result={"key": "value"},
            attempts=2,
            strategies_tried=("retry_1", "retry_2"),
            final_error=None,
        )
        assert result.success is True
        assert result.final_result == {"key": "value"}
        assert result.attempts == 2
        assert result.strategies_tried == ("retry_1", "retry_2")
        assert result.final_error is None

    def test_failure_result(self) -> None:
        """Failure result has correct fields."""
        result = HealingResult(
            success=False,
            final_result=None,
            attempts=5,
            strategies_tried=("retry_1", "retry_2", "retry_3", "alt1", "alt2"),
            final_error="All strategies exhausted",
        )
        assert result.success is False
        assert result.final_result is None
        assert result.attempts == 5
        assert len(result.strategies_tried) == 5
        assert result.final_error == "All strategies exhausted"


# =============================================================================
# Test: AlternativeStrategy Dataclass
# =============================================================================


class TestAlternativeStrategy:
    """Tests for AlternativeStrategy dataclass."""

    def test_alternative_strategy_fields(self) -> None:
        """AlternativeStrategy stores all fields correctly."""

        async def fallback() -> str:
            return "fallback"

        strategy = AlternativeStrategy(
            name="my_fallback",
            description="My fallback strategy",
            execute=fallback,
        )
        assert strategy.name == "my_fallback"
        assert strategy.description == "My fallback strategy"
        assert strategy.execute is fallback


# =============================================================================
# Test: Error Integration with KernelOneError
# =============================================================================


@pytest.mark.asyncio
async def test_kernel_one_error_with_error_category(
    retry_strategy: RetryStrategy,
) -> None:
    """KernelOneError with TRANSIENT ErrorCategory triggers retry."""
    executor = SelfHealingExecutor(retry_strategy=retry_strategy)
    attempts = 0

    async def flaky_kernel_error() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            # Create error with TRANSIENT category via details
            error = KernelOneError(
                "temp failure",
                code="TEST_ERROR",
                retryable=True,
            )
            raise error
        return "recovered"

    result = await executor.execute(flaky_kernel_error)

    assert result.success is True
    assert result.final_result == "recovered"


@pytest.mark.asyncio
async def test_execute_with_args_and_kwargs(
    retry_strategy: RetryStrategy,
) -> None:
    """Arguments are correctly passed to the function."""

    async def func_with_args(a: int, b: str, c: bool = True) -> tuple[int, str, bool]:
        return (a, b, c)

    executor = SelfHealingExecutor(retry_strategy=retry_strategy)
    result = await executor.execute(func_with_args, 42, "hello", c=False)

    assert result.success is True
    assert result.final_result == (42, "hello", False)
