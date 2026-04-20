"""Timeout utilities for evaluation suite execution.

Provides robust timeout handling for async operations with proper
cancellation semantics.

✅ MIGRATION COMPLETED (2026-04-09): Independent module established, follows kernelone patterns.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class TimeoutConfig:
    """Configuration for timeout behavior.

    Attributes:
        suite_timeout_sec: Timeout for each individual suite execution.
        case_timeout_sec: Timeout for each individual test case within a suite.
        default_suite_timeout: Default timeout when not specified (300 seconds).
        default_case_timeout: Default timeout for cases within suites (120 seconds).
        enable_timeout: Whether timeout enforcement is enabled.
    """

    suite_timeout_sec: float = 300.0
    case_timeout_sec: float = 120.0
    default_suite_timeout: float = 300.0
    default_case_timeout: float = 120.0
    enable_timeout: bool = True

    def __post_init__(self) -> None:
        """Validate timeout values after initialization."""
        if self.enable_timeout:
            if self.suite_timeout_sec <= 0:
                raise ValueError("suite_timeout_sec must be positive when timeout is enabled")
            if self.case_timeout_sec <= 0:
                raise ValueError("case_timeout_sec must be positive when timeout is enabled")
            if self.default_suite_timeout <= 0:
                raise ValueError("default_suite_timeout must be positive when timeout is enabled")
            if self.default_case_timeout <= 0:
                raise ValueError("default_case_timeout must be positive when timeout is enabled")

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> TimeoutConfig:
        """Create TimeoutConfig from options dictionary.

        Args:
            options: Dictionary containing timeout configuration.

        Returns:
            TimeoutConfig instance with values from options or defaults.
        """

        # Parse timeout values with fallback to defaults for invalid inputs
        def parse_float(key: str, default: float) -> float:
            val = options.get(key)
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        return cls(
            suite_timeout_sec=parse_float("suite_timeout_sec", 300.0),
            case_timeout_sec=parse_float("case_timeout_sec", 120.0),
            default_suite_timeout=parse_float("default_suite_timeout", 300.0),
            default_case_timeout=parse_float("default_case_timeout", 120.0),
            enable_timeout=bool(options.get("enable_timeout", True)),
        )


@dataclass
class TimeoutResult:
    """Result of a timeout-protected operation.

    Attributes:
        ok: Whether the operation completed successfully.
        result: The result value if successful.
        error: Error message if failed or timed out.
        timed_out: Whether the operation timed out.
        elapsed_ms: Time elapsed in milliseconds.
    """

    ok: bool = False
    result: Any = None
    error: str = ""
    timed_out: bool = False
    elapsed_ms: int = 0


async def run_with_timeout(
    coro: Awaitable[T],
    timeout_sec: float,
    operation_name: str = "operation",
) -> TimeoutResult:
    """Run a coroutine with timeout protection.

    Properly distinguishes between TimeoutError and CancelledError,
    ensuring correct semantics for both cases.

    Args:
        coro: The coroutine to execute.
        timeout_sec: Timeout in seconds. Must be positive.
        operation_name: Name of the operation for logging purposes.

    Returns:
        TimeoutResult containing the result or error information.

    Raises:
        ValueError: If timeout_sec is not positive.
    """
    if timeout_sec <= 0:
        raise ValueError(f"timeout_sec must be positive, got {timeout_sec}")

    result: TimeoutResult = TimeoutResult()
    start = asyncio.get_event_loop().time()

    try:
        result.result = await asyncio.wait_for(coro, timeout=timeout_sec)
        result.ok = True
        elapsed = asyncio.get_event_loop().time() - start
        result.elapsed_ms = int(elapsed * 1000)
        return result

    except asyncio.TimeoutError:
        elapsed = asyncio.get_event_loop().time() - start
        result.elapsed_ms = int(elapsed * 1000)
        result.timed_out = True
        result.error = f"{operation_name} timed out after {timeout_sec}s"
        logger.warning("[timeout] %s: %s", operation_name, result.error)
        return result

    except asyncio.CancelledError:
        elapsed = asyncio.get_event_loop().time() - start
        result.elapsed_ms = int(elapsed * 1000)
        result.error = f"{operation_name} was cancelled"
        result.ok = False
        logger.info("[timeout] %s: %s (elapsed: %dms)", operation_name, result.error, result.elapsed_ms)
        raise  # Re-raise CancelledError - do not swallow it

    except (RuntimeError, ValueError) as exc:  # pylint: disable=broad-except
        elapsed = asyncio.get_event_loop().time() - start
        result.elapsed_ms = int(elapsed * 1000)
        result.error = f"{operation_name} failed: {exc!s}"
        result.ok = False
        logger.error("[timeout] %s: %s (elapsed: %dms)", operation_name, result.error, result.elapsed_ms)
        return result


async def run_with_timeout_optional(
    coro: Awaitable[T],
    timeout_sec: float | None,
    operation_name: str = "operation",
) -> TimeoutResult:
    """Run a coroutine with optional timeout protection.

    If timeout_sec is None or non-positive, runs without timeout protection.

    Args:
        coro: The coroutine to execute.
        timeout_sec: Timeout in seconds, or None to disable timeout.
        operation_name: Name of the operation for logging purposes.

    Returns:
        TimeoutResult containing the result or error information.
    """
    if timeout_sec is None or timeout_sec <= 0:
        # Run without timeout protection
        result: TimeoutResult = TimeoutResult()
        start = asyncio.get_event_loop().time()

        try:
            result.result = await coro
            result.ok = True
            elapsed = asyncio.get_event_loop().time() - start
            result.elapsed_ms = int(elapsed * 1000)
            return result

        except asyncio.CancelledError:
            raise  # Re-raise CancelledError

        except (RuntimeError, ValueError) as exc:  # pylint: disable=broad-except
            elapsed = asyncio.get_event_loop().time() - start
            result.elapsed_ms = int(elapsed * 1000)
            result.error = f"{operation_name} failed: {exc!s}"
            result.ok = False
            return result

    return await run_with_timeout(coro, timeout_sec, operation_name)


# Default timeout configuration for evaluation suites
DEFAULT_SUITE_TIMEOUT = TimeoutConfig(
    suite_timeout_sec=300.0,
    case_timeout_sec=120.0,
    default_suite_timeout=300.0,
    default_case_timeout=120.0,
    enable_timeout=True,
)


__all__ = [
    "DEFAULT_SUITE_TIMEOUT",
    "TimeoutConfig",
    "TimeoutResult",
    "run_with_timeout",
    "run_with_timeout_optional",
]
