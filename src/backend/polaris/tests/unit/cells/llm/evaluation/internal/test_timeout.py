"""Unit tests for polaris.cells.llm.evaluation.internal.timeout."""

from __future__ import annotations

import asyncio

import pytest
from polaris.cells.llm.evaluation.internal.timeout import (
    DEFAULT_SUITE_TIMEOUT,
    TimeoutConfig,
    TimeoutResult,
    run_with_timeout,
    run_with_timeout_optional,
)


class TestTimeoutConfig:
    """Tests for TimeoutConfig dataclass."""

    def test_defaults(self) -> None:
        config = TimeoutConfig()
        assert config.suite_timeout_sec == 300.0
        assert config.case_timeout_sec == 120.0
        assert config.enable_timeout is True

    def test_from_options(self) -> None:
        config = TimeoutConfig.from_options(
            {
                "suite_timeout_sec": "60",
                "case_timeout_sec": "30",
                "enable_timeout": False,
            }
        )
        assert config.suite_timeout_sec == 60.0
        assert config.case_timeout_sec == 30.0
        assert config.enable_timeout is False

    def test_from_options_invalid_values(self) -> None:
        config = TimeoutConfig.from_options(
            {
                "suite_timeout_sec": "not_a_number",
                "case_timeout_sec": "also_bad",
            }
        )
        assert config.suite_timeout_sec == 300.0
        assert config.case_timeout_sec == 120.0

    def test_post_init_positive(self) -> None:
        config = TimeoutConfig(suite_timeout_sec=10.0, case_timeout_sec=5.0)
        assert config.suite_timeout_sec == 10.0

    def test_post_init_invalid_suite(self) -> None:
        with pytest.raises(ValueError, match="suite_timeout_sec must be positive"):
            TimeoutConfig(suite_timeout_sec=0.0)

    def test_post_init_invalid_case(self) -> None:
        with pytest.raises(ValueError, match="case_timeout_sec must be positive"):
            TimeoutConfig(case_timeout_sec=-1.0)

    def test_post_init_disabled_no_validation(self) -> None:
        config = TimeoutConfig(
            suite_timeout_sec=0.0,
            case_timeout_sec=-1.0,
            enable_timeout=False,
        )
        assert config.suite_timeout_sec == 0.0


class TestTimeoutResult:
    """Tests for TimeoutResult dataclass."""

    def test_defaults(self) -> None:
        result = TimeoutResult()
        assert result.ok is False
        assert result.result is None
        assert result.error == ""
        assert result.timed_out is False
        assert result.elapsed_ms == 0


class TestRunWithTimeout:
    """Tests for run_with_timeout function."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        async def coro() -> str:
            return "done"

        result = await run_with_timeout(coro(), timeout_sec=5.0, operation_name="test")
        assert result.ok is True
        assert result.result == "done"
        assert result.timed_out is False
        assert result.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        async def slow() -> None:
            await asyncio.sleep(10)

        result = await run_with_timeout(slow(), timeout_sec=0.01, operation_name="slow_op")
        assert result.ok is False
        assert result.timed_out is True
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_cancelled_error(self) -> None:
        async def coro() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(run_with_timeout(coro(), timeout_sec=1.0, operation_name="cancel"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_value_error(self) -> None:
        async def bad() -> None:
            raise ValueError("boom")

        result = await run_with_timeout(bad(), timeout_sec=5.0, operation_name="bad_op")
        assert result.ok is False
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_invalid_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_sec must be positive"):
            await run_with_timeout(asyncio.sleep(0), timeout_sec=0)


class TestRunWithTimeoutOptional:
    """Tests for run_with_timeout_optional function."""

    @pytest.mark.asyncio
    async def test_with_timeout(self) -> None:
        async def coro() -> str:
            return "done"

        result = await run_with_timeout_optional(coro(), timeout_sec=5.0)
        assert result.ok is True
        assert result.result == "done"

    @pytest.mark.asyncio
    async def test_no_timeout(self) -> None:
        async def coro() -> str:
            return "done"

        result = await run_with_timeout_optional(coro(), timeout_sec=None)
        assert result.ok is True
        assert result.result == "done"

    @pytest.mark.asyncio
    async def test_zero_timeout(self) -> None:
        async def coro() -> str:
            return "done"

        result = await run_with_timeout_optional(coro(), timeout_sec=0)
        assert result.ok is True
        assert result.result == "done"

    @pytest.mark.asyncio
    async def test_cancelled_error_no_timeout(self) -> None:
        async def coro() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(
            run_with_timeout_optional(coro(), timeout_sec=None),
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestDefaultSuiteTimeout:
    """Tests for DEFAULT_SUITE_TIMEOUT constant."""

    def test_values(self) -> None:
        assert DEFAULT_SUITE_TIMEOUT.suite_timeout_sec == 300.0
        assert DEFAULT_SUITE_TIMEOUT.case_timeout_sec == 120.0
        assert DEFAULT_SUITE_TIMEOUT.enable_timeout is True
