"""Tests for standardized error handling utilities."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.shared.error_handling import (
    capture_exception,
    exception_context,
    log_and_reraise,
    suppress_and_log,
)


class TestLogAndReraise:
    """Tests for log_and_reraise decorator."""

    def test_sync_function_success(self) -> None:
        """Verify successful sync function returns value."""
        logger = MagicMock()

        @log_and_reraise(logger)
        def successful_func() -> int:
            return 42

        result = successful_func()
        assert result == 42
        logger.log.assert_not_called()

    def test_sync_function_exception_reraised(self) -> None:
        """Verify exception is logged and re-raised."""
        logger = MagicMock()

        @log_and_reraise(logger)
        def failing_func() -> int:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            failing_func()

        logger.log.assert_called_once()
        call_args = logger.log.call_args
        assert call_args[0][0] == logging.ERROR
        # call_args[0][1] is format string, call_args[0][2] is the actual message
        assert "failed" in str(call_args[0][2])
        assert call_args[1]["exc_info"] is True

    def test_sync_function_custom_message(self) -> None:
        """Verify custom message is used in logging."""
        logger = MagicMock()

        @log_and_reraise(logger, message="Custom error message")
        def failing_func() -> int:
            raise RuntimeError("inner error")

        with pytest.raises(RuntimeError):
            failing_func()

        call_args = logger.log.call_args
        # call_args[0][1] is format string, call_args[0][2] is the actual message
        assert "Custom error message" in str(call_args[0][2])

    def test_sync_function_keyboard_interrupt_not_caught(self) -> None:
        """Verify KeyboardInterrupt is not caught."""
        logger = MagicMock()

        @log_and_reraise(logger)
        def interrupt_func() -> int:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            interrupt_func()

        logger.log.assert_not_called()

    def test_async_function_success(self) -> None:
        """Verify successful async function returns value."""
        logger = MagicMock()

        @log_and_reraise(logger)
        async def async_successful() -> int:
            return 100

        result = asyncio.run(async_successful())
        assert result == 100
        logger.log.assert_not_called()

    def test_async_function_exception_reraised(self) -> None:
        """Verify async exception is logged and re-raised."""
        logger = MagicMock()

        @log_and_reraise(logger)
        async def async_failing() -> int:
            raise TypeError("async error")

        with pytest.raises(TypeError, match="async error"):
            asyncio.run(async_failing())

        logger.log.assert_called_once()
        call_args = logger.log.call_args
        assert call_args[0][0] == logging.ERROR
        assert call_args[1]["exc_info"] is True

    def test_async_function_cancelled_error_propagates(self) -> None:
        """Verify CancelledError propagates without logging."""
        logger = MagicMock()

        @log_and_reraise(logger)
        async def cancelled_func() -> int:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(cancelled_func())

        logger.log.assert_not_called()


class TestSuppressAndLog:
    """Tests for suppress_and_log decorator."""

    def test_sync_function_success(self) -> None:
        """Verify successful sync function returns value."""
        logger = MagicMock()

        @suppress_and_log(logger)
        def successful_func() -> list[str]:
            return ["a", "b"]

        result = successful_func()
        assert result == ["a", "b"]
        logger.log.assert_not_called()

    def test_suppress_and_log_returns_none(self) -> None:
        """Verify suppressed exception returns None."""
        logger = MagicMock()

        @suppress_and_log(logger, suppress=(ValueError,))
        def failing_func() -> int:
            raise ValueError("suppressed")

        result = failing_func()
        assert result is None
        logger.log.assert_called_once()
        call_args = logger.log.call_args
        assert call_args[0][0] == logging.WARNING
        assert "suppressed" in str(call_args[0])

    def test_suppress_and_log_custom_default(self) -> None:
        """Verify custom default value is returned."""
        logger = MagicMock()

        @suppress_and_log(logger, default=-1, suppress=(ValueError,))
        def failing_func() -> int:
            raise ValueError("error")

        result = failing_func()
        assert result == -1

    def test_suppress_and_log_non_matching_exception(self) -> None:
        """Verify non-matching exceptions are not suppressed."""
        logger = MagicMock()

        @suppress_and_log(logger, suppress=(ValueError,))
        def wrong_exception() -> int:
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            wrong_exception()

        logger.log.assert_not_called()

    def test_async_suppress_and_log(self) -> None:
        """Verify async function with suppressed exception."""
        logger = MagicMock()

        @suppress_and_log(logger, suppress=(RuntimeError,))
        async def async_failing() -> int:
            raise RuntimeError("async suppressed")

        result = asyncio.run(async_failing())
        assert result is None
        logger.log.assert_called_once()


class TestExceptionContext:
    """Tests for exception_context context manager."""

    def test_successful_block(self) -> None:
        """Verify successful block completes without logging."""
        logger = MagicMock()

        with exception_context(logger, "Test context"):
            x = 1 + 1

        assert x == 2
        logger.log.assert_not_called()

    def test_exception_logged_and_reraised(self) -> None:
        """Verify exception is logged and re-raised."""
        logger = MagicMock()

        with pytest.raises(RuntimeError), exception_context(logger, "operation failed"):
            raise RuntimeError("inner error")

        logger.log.assert_called_once()
        call_args = logger.log.call_args
        assert call_args[0][0] == logging.ERROR
        # call_args[0][1] is format string, call_args[0][2] is the actual message
        assert "operation failed" in str(call_args[0][2])
        assert call_args[1]["exc_info"] is True

    def test_custom_level(self) -> None:
        """Verify custom logging level is used."""
        logger = MagicMock()

        with pytest.raises(RuntimeError), exception_context(logger, "warning context", level=logging.WARNING):
            raise RuntimeError("error")

        call_args = logger.log.call_args
        assert call_args[0][0] == logging.WARNING

    def test_keyboard_interrupt_not_caught(self) -> None:
        """Verify KeyboardInterrupt is not caught."""
        logger = MagicMock()

        with pytest.raises(KeyboardInterrupt), exception_context(logger, "should not log"):
            raise KeyboardInterrupt

        logger.log.assert_not_called()


class TestCaptureException:
    """Tests for capture_exception decorator."""

    def test_success_returns_value(self) -> None:
        """Verify successful function returns value."""
        logger = MagicMock()

        @capture_exception(logger)
        def successful_func() -> dict[str, int]:
            return {"count": 5}

        result = successful_func()
        assert result == {"count": 5}
        logger.log.assert_not_called()

    def test_exception_returns_none(self) -> None:
        """Verify exception returns None."""
        logger = MagicMock()

        @capture_exception(logger)
        def failing_func() -> list:
            raise OSError("file not found")

        result = failing_func()
        assert result is None
        logger.log.assert_called_once()
        call_args = logger.log.call_args
        assert call_args[0][0] == logging.ERROR
        assert call_args[1]["exc_info"] is True

    def test_custom_message(self) -> None:
        """Verify custom message is used."""
        logger = MagicMock()

        @capture_exception(logger, message="Cache lookup failed")
        def cache_func() -> str:
            raise ConnectionError("network error")

        result = cache_func()
        assert result is None
        call_args = logger.log.call_args
        # call_args[0][1] is format string, call_args[0][2] is the actual message
        assert "Cache lookup failed" in str(call_args[0][2])

    def test_async_capture_exception(self) -> None:
        """Verify async function with captured exception."""
        logger = MagicMock()

        @capture_exception(logger)
        async def async_failing() -> int:
            raise TimeoutError("request timeout")

        result = asyncio.run(async_failing())
        assert result is None
        logger.log.assert_called_once()


class TestSystemExceptionPropagation:
    """Tests to verify system exceptions propagate correctly."""

    def test_system_exit_not_caught(self) -> None:
        """Verify SystemExit is not caught by any decorator."""
        logger = MagicMock()

        @suppress_and_log(logger)
        def system_exit_func() -> int:
            raise SystemExit(0)

        with pytest.raises(SystemExit):
            system_exit_func()

        logger.log.assert_not_called()

    def test_keyboard_interrupt_not_suppressed(self) -> None:
        """Verify KeyboardInterrupt is not suppressed."""
        logger = MagicMock()

        @suppress_and_log(logger)
        def interrupt_func() -> int:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            interrupt_func()

        logger.log.assert_not_called()
