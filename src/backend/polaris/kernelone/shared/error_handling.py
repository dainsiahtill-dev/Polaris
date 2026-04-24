"""Standardized error handling utilities for KernelOne.

This module provides unified exception handling patterns across the KernelOne
runtime, ensuring consistent logging, proper exception propagation, and
avoiding silent exception swallowing.

Example:
    >>> import logging
    >>> logger = logging.getLogger(__name__)
    >>>
    >>> @log_and_reraise(logger)
    ... async def my_async_func():
    ...     ...
    >>>
    >>> with exception_context(logger, "Failed to connect"):
    ...     await connection.connect()
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    ParamSpec,
    TypeVar,
    overload,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

P = ParamSpec("P")
T = TypeVar("T")
R = TypeVar("R")
# Type variable for async function return values (must be Awaitable)
T_async = TypeVar("T_async")

# Exceptions that should always be re-raised without suppression
# These are critical system-level exceptions that should not be caught
_SYSTEM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
    asyncio.CancelledError,
)


def _normalize_reraise(
    reraise: tuple[type[Exception], ...] | type[Exception],
) -> tuple[type[Exception], ...]:
    """Normalize reraise parameter to tuple."""
    if isinstance(reraise, type):
        return (reraise,)
    return reraise


@overload
def log_and_reraise(
    logger: logging.Logger,
    *,
    level: int = logging.ERROR,
    reraise: type[Exception] = Exception,
    message: str | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...


@overload
def log_and_reraise(
    logger: logging.Logger,
    *,
    level: int = logging.ERROR,
    reraise: tuple[type[Exception], ...] = (Exception,),
    message: str | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...


def log_and_reraise(
    logger: logging.Logger,
    *,
    level: int = logging.ERROR,
    reraise: tuple[type[Exception], ...] | type[Exception] = Exception,
    message: str | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """Decorator that logs exceptions and re-raises them.

    Use this for operations where failure should propagate to the caller
    but the exception should be logged for debugging purposes.

    Args:
        logger: Logger instance for recording exceptions.
        level: Logging level for exception messages (default: ERROR).
        reraise: Exception types to catch and re-raise (default: all Exceptions).
            Can be a single type or tuple of types.
        message: Custom error message. If None, uses "{func_name} failed".

    Returns:
        Decorated function that logs exceptions before re-raising.

    Example:
        @log_and_reraise(_logger)
        async def connect_to_service():
            await client.connect()
    """
    reraise_tuple = _normalize_reraise(reraise)

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        func_name = func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            except _SYSTEM_EXCEPTIONS:
                raise
            except reraise_tuple as exc:
                log_msg = message or f"{func_name} failed"
                logger.log(level, "%s: %s", log_msg, exc, exc_info=True)
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except _SYSTEM_EXCEPTIONS:
                raise
            except reraise_tuple as exc:
                log_msg = message or f"{func_name} failed"
                logger.log(level, "%s: %s", log_msg, exc, exc_info=True)
                raise

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _normalize_suppress(
    suppress: tuple[type[Exception], ...] | type[Exception],
) -> tuple[type[Exception], ...]:
    """Normalize suppress parameter to tuple."""
    if isinstance(suppress, type):
        return (suppress,)
    return suppress


@overload
def suppress_and_log(
    logger: logging.Logger,
    *,
    level: int = logging.WARNING,
    suppress: type[Exception] = Exception,
    default: Any = None,
    message: str = "Operation failed",
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...


@overload
def suppress_and_log(
    logger: logging.Logger,
    *,
    level: int = logging.WARNING,
    suppress: tuple[type[Exception], ...] = (Exception,),
    default: Any = None,
    message: str = "Operation failed",
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...


def suppress_and_log(
    logger: logging.Logger,
    *,
    level: int = logging.WARNING,
    suppress: tuple[type[Exception], ...] | type[Exception] = (Exception,),
    default: Any = None,
    message: str = "Operation failed",
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """Decorator that suppresses exceptions and logs them.

    Use this ONLY for non-critical operations where failure should not
    propagate and a default value can be safely returned.

    IMPORTANT: Avoid using this for operations that affect program correctness.
    Prefer log_and_reraise for operations where exceptions must be handled
    by the caller.

    Args:
        logger: Logger instance for recording suppressed exceptions.
        level: Logging level for exception messages (default: WARNING).
        suppress: Exception types to suppress (default: all Exceptions).
            Can be a single type or tuple of types.
        default: Default value to return when exception is suppressed.
        message: Prefix for the log message.

    Returns:
        Decorated function that suppresses exceptions and returns default.

    Example:
        @suppress_and_log(_logger, default=[])
        def get_optional_config():
            return load_config()  # Failure returns []
    """
    suppress_tuple = _normalize_suppress(suppress)

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        func_name = func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            except _SYSTEM_EXCEPTIONS:
                raise
            except suppress_tuple as exc:
                logger.log(
                    level,
                    "%s: %s - %s",
                    message,
                    func_name,
                    exc,
                    exc_info=True,
                )
                return default

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except _SYSTEM_EXCEPTIONS:
                raise
            except suppress_tuple as exc:
                logger.log(
                    level,
                    "%s: %s - %s",
                    message,
                    func_name,
                    exc,
                    exc_info=True,
                )
                return default

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


@contextmanager
def exception_context(
    logger: logging.Logger,
    message: str,
    *,
    level: int = logging.ERROR,
    reraise: type[Exception] | tuple[type[Exception], ...] = Exception,
) -> Generator[None, None, None]:
    """Context manager for wrapping operations that may fail.

    Logs the exception and re-raises it. Use this when you need to
    add context to an operation that may fail without changing the
    exception type.

    Args:
        logger: Logger instance for recording exceptions.
        message: Human-readable description of the operation context.
        level: Logging level for exception messages (default: ERROR).
        reraise: Exception types to catch and re-raise (default: all Exceptions).

    Yields:
        None - the context simply wraps the operation.

    Raises:
        The original exception if it matches the reraise criteria.

    Example:
        with exception_context(_logger, "Failed to establish connection"):
            await client.connect()
    """
    reraise_tuple = _normalize_reraise(reraise)
    try:
        yield
    except _SYSTEM_EXCEPTIONS:
        raise
    except reraise_tuple as exc:
        logger.log(level, "%s: %s", message, exc, exc_info=True)
        raise


@overload
def capture_exception(
    logger: logging.Logger,
    *,
    level: int = logging.ERROR,
    message: str | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...


@overload
def capture_exception(
    logger: logging.Logger,
    message: str,
    *,
    level: int = logging.ERROR,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]: ...


def capture_exception(
    logger: logging.Logger,
    message: str | None = None,
    *,
    level: int = logging.ERROR,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """Decorator that captures exceptions, logs them, and returns None.

    This is similar to suppress_and_log but provides more flexibility
    for custom logging messages.

    Args:
        logger: Logger instance for recording exceptions.
        level: Logging level for exception messages (default: ERROR).
        message: Custom log message. If None, uses function name.

    Returns:
        Decorated function that returns None on exception.

    Example:
        @capture_exception(_logger, message="Cache lookup failed")
        def get_cached_value(key):
            return cache.get(key)
    """

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        func_name = func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            except _SYSTEM_EXCEPTIONS:
                raise
            except (RuntimeError, OSError) as exc:
                log_msg = message or f"{func_name} failed"
                logger.log(level, "%s: %s", log_msg, exc, exc_info=True)
                return None

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except _SYSTEM_EXCEPTIONS:
                raise
            except (RuntimeError, OSError) as exc:
                log_msg = message or f"{func_name} failed"
                logger.log(level, "%s: %s", log_msg, exc, exc_info=True)
                return None

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
