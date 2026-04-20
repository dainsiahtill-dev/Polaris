"""LLM-specific exceptions with unified kernelone.errors inheritance.

This module provides LLM-specific exceptions that inherit from the unified
kernelone.errors hierarchy. This enables consistent exception handling:

    except kernelone.errors.RateLimitError:
        # Now catches llm.exceptions.RateLimitError!

Migration (P1-NEW-016):
- All exceptions now inherit from kernelone.errors base classes
- LLMError is defined in errors.py and re-exported here for convenience (P0-NEW-004)
- Preserve LLM-specific attributes while leveraging kernelone.errors infrastructure

Usage:
    from polaris.kernelone.llm.exceptions import RateLimitError
    from polaris.kernelone.errors import RateLimitError as KernelOneRateLimitError

    e = RateLimitError()
    assert isinstance(e, KernelOneRateLimitError)  # True!

    try:
        ...
    except kernelone.errors.RateLimitError:
        # Catches both kernelone.errors and llm.exceptions RateLimitError
        pass
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

    from polaris.kernelone.errors import LLMError as _LLMErrorType


logger = logging.getLogger(__name__)


# ============================================================================
# Lazy import of LLMError from kernelone.errors to avoid circular dependency
# LLMError is defined in kernelone.errors but needs to be accessible here
# ============================================================================

_LLM_ERROR_LOADED = False
_LLM_ERROR: type[Exception] | None = None


def _get_llm_error() -> type[Exception]:
    """Lazily import LLMError from kernelone.errors to avoid circular import."""
    global _LLM_ERROR_LOADED, _LLM_ERROR
    if not _LLM_ERROR_LOADED:
        from polaris.kernelone.errors import LLMError as _LE

        _LLM_ERROR = _LE  # type: ignore[assignment]
        _LLM_ERROR_LOADED = True
    assert _LLM_ERROR is not None
    return _LLM_ERROR


# ============================================================================
# Lazy Import Helpers
# ============================================================================


def _get_kernelone_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import KernelOneError

    return KernelOneError


def _get_rate_limit_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import RateLimitError as _RateLimitError

    return _RateLimitError


def _get_authentication_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import AuthenticationError as _AuthenticationError

    return _AuthenticationError


def _get_timeout_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import TimeoutError as _TimeoutError

    return _TimeoutError


def _get_network_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import NetworkError as _NetworkError

    return _NetworkError


def _get_tool_execution_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import ToolExecutionError as _ToolExecutionError

    return _ToolExecutionError


def _get_budget_exceeded_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import BudgetExceededError as _BudgetExceededError

    return _BudgetExceededError


def _get_configuration_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import ConfigurationError as _ConfigurationError

    return _ConfigurationError


def _get_config_migration_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import ConfigMigrationError as _ConfigMigrationError

    return _ConfigMigrationError


def _get_config_validation_error() -> type:
    """Lazy import to avoid circular dependency."""
    from polaris.kernelone.errors import ConfigValidationError as _ConfigValidationError

    return _ConfigValidationError


# ============================================================================
# LLMError is lazily loaded from kernelone.errors to avoid circular dependency
# ============================================================================


# ============================================================================
# Parse Errors (Non-retryable by default)
# ============================================================================


class ToolParseError(_get_llm_error()):  # type: ignore[misc]
    """Tool call parsing failed.

    Raised when LLM output cannot be parsed into valid tool calls.
    This is typically a prompt or model output issue, not a runtime problem.

    Attributes:
        tool_name: The name of the tool being parsed (if known).
        parse_context: Additional context about what failed to parse.
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        parse_context: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, retryable=False, **kwargs)
        self.tool_name = tool_name
        self.parse_context = parse_context
        if tool_name:
            self.details["tool_name"] = tool_name
        if parse_context:
            self.details["parse_context"] = parse_context


class ResponseParseError(_get_llm_error()):  # type: ignore[misc]
    """LLM response parsing failed.

    Raised when LLM response cannot be parsed into expected structure.
    This is typically a model output format issue.

    Attributes:
        response_preview: First N characters of the problematic response.
        expected_format: What format was expected.
    """

    def __init__(
        self,
        message: str,
        *,
        response_preview: str = "",
        expected_format: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, retryable=False, **kwargs)
        self.response_preview = response_preview
        self.expected_format = expected_format
        if response_preview:
            self.details["response_preview"] = response_preview[:500]
        if expected_format:
            self.details["expected_format"] = expected_format


class JSONParseError(ResponseParseError):
    """JSON parsing failed.

    A specialized ResponseParseError for JSON decoding failures.
    """

    def __init__(
        self,
        message: str,
        *,
        json_error: json.JSONDecodeError | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            expected_format="JSON",
            **kwargs,
        )
        if json_error is not None:
            self.details["json_error"] = {
                "message": str(json_error),
                "pos": json_error.pos,
                "lineno": json_error.lineno,
                "colno": json_error.colno,
            }


# ============================================================================
# Resilience/Communication Errors (Retryable by default)
# These inherit directly from kernelone.errors to enable proper catch behavior
# ============================================================================


class RateLimitError(_get_rate_limit_error()):  # type: ignore[misc]
    """Rate limit exceeded.

    Raised when API rate limits are hit.
    Caller should implement backoff and retry.

    Attributes:
        retry_after: Seconds to wait before retrying.
        limit_type: Type of limit hit (requests, tokens, etc.).
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: float | None = None,
        limit_type: str = "requests",
        **kwargs,
    ) -> None:
        super().__init__(message, retry_after=retry_after, limit_type=limit_type, retryable=True, **kwargs)
        self.retry_after = retry_after
        self.limit_type = limit_type


class AuthenticationError(_get_authentication_error()):  # type: ignore[misc]
    """API authentication failed.

    Raised when API key is invalid, expired, or missing.

    Attributes:
        provider: The provider that failed authentication.
    """

    def __init__(
        self,
        message: str = "",
        *,
        provider: str = "",
        **kwargs,
    ) -> None:
        full_message = message or f"Authentication failed for provider: {provider}"
        super().__init__(full_message, provider=provider, retryable=False, **kwargs)
        self.provider = provider


class LLMTimeoutError(_get_timeout_error()):  # type: ignore[misc]
    """Operation timed out.

    Raised when an LLM call or tool execution exceeds its timeout.

    Note:
        This class is named LLMTimeoutError to avoid shadowing Python's
        built-in TimeoutError.

    Attributes:
        timeout_seconds: The configured timeout that was exceeded.
        operation: What was being performed when timeout occurred.
    """

    def __init__(
        self,
        message: str = "",
        *,
        timeout_seconds: float | None = None,
        operation: str = "",
        **kwargs,
    ) -> None:
        full_message = message or (
            f"Operation timed out after {timeout_seconds}s" if timeout_seconds else "Operation timed out"
        )
        super().__init__(full_message, timeout_seconds=timeout_seconds, operation=operation, retryable=True, **kwargs)
        self.timeout_seconds = timeout_seconds
        self.operation = operation


# ============================================================================
# Execution Errors (Retryable by default)
# ============================================================================


class ToolExecutionError(_get_tool_execution_error()):  # type: ignore[misc]
    """Tool execution failed.

    Raised when a tool call fails during execution.
    This can include file system errors, subprocess failures, etc.

    Attributes:
        tool_name: The name of the tool that failed.
        exit_code: Exit code if the error came from a subprocess.
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        exit_code: int | None = None,
        retryable: bool = True,
        **kwargs,
    ) -> None:
        # ToolExecutionError is retryable by default
        super().__init__(message, tool_name=tool_name, exit_code=exit_code, retryable=retryable, **kwargs)
        self.tool_name = tool_name
        self.exit_code = exit_code


class BudgetExceededError(_get_budget_exceeded_error()):  # type: ignore[misc]
    """Tool execution would exceed available context budget.

    Attributes:
        tool: Name of the tool that triggered the error.
        file: File path that caused the budget exceedance.
        line_count: Number of lines in the file.
        limit: The hard limit that was exceeded.
        suggestion: Recommended alternative tool or approach.
    """

    def __init__(
        self,
        message: str,
        *,
        tool: str = "read_file",
        file: str | None = None,
        line_count: int = 0,
        limit: int = 2000,
        suggestion: str | None = None,
        **kwargs,
    ) -> None:
        # BudgetExceededError is always retryable
        kwargs.setdefault("retryable", True)
        suggestion = suggestion or (
            f"Use repo_read_slice with {{'file': '{file}', 'start': 1, 'end': 200}} "
            f"for targeted reading instead of reading the entire file."
            if file
            else "Use repo_read_slice for targeted line-range reading."
        )
        super().__init__(
            message,
            tool_name=tool,
            limit=limit,
            requested=line_count,
            current=0,
            suggestion=suggestion,
            retryable=True,
        )
        self.tool = tool
        self.file = file
        self.line_count = line_count
        self.limit = limit
        self.suggestion = suggestion


# ============================================================================
# Configuration Errors (Non-retryable)
# ============================================================================


class ConfigurationError(_get_configuration_error()):  # type: ignore[misc]
    """Configuration error.

    Raised when LLM configuration is invalid or missing required fields.

    Attributes:
        field: The configuration field that is invalid.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, field=field, retryable=False, **kwargs)
        self.field = field


class ConfigMigrationError(_get_config_migration_error(), ConfigurationError):  # type: ignore[misc]
    """Configuration migration failed.

    Raised when upgrading or migrating configuration format fails.
    """

    def __init__(
        self,
        message: str,
        *,
        from_version: str = "",
        to_version: str = "",
        **kwargs,
    ) -> None:
        # Note: retryable=False is set by ConfigurationError via setdefault
        super().__init__(message, **kwargs)
        self.from_version = from_version
        self.to_version = to_version
        if from_version:
            self.details["from_version"] = from_version
        if to_version:
            self.details["to_version"] = to_version


class ConfigValidationError(_get_config_validation_error(), ConfigurationError):  # type: ignore[misc]
    """Configuration validation failed.

    Raised when configuration values fail validation checks.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str = "",
        validation_errors: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(message, field=field, retryable=False, **kwargs)
        self.validation_errors = validation_errors or []
        if self.validation_errors:
            self.details["validation_errors"] = self.validation_errors


# ============================================================================
# Provider Errors
# ============================================================================


class ProviderError(_get_llm_error()):  # type: ignore[misc]
    """Generic provider error.

    Raised when an LLM provider returns an unexpected error.
    The retryable flag should be set based on the specific error type.

    Note:
        This is an LLM-specific error that doesn't have a direct equivalent
        in kernelone.errors. It inherits from LLMError -> KernelOneError.

    Attributes:
        provider: The provider that produced the error.
        provider_code: Provider-specific error code if available.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        provider_code: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, **kwargs)
        self.provider = provider
        self.provider_code = provider_code
        if provider:
            self.details["provider"] = provider
        if provider_code:
            self.details["provider_code"] = provider_code


class NetworkError(_get_network_error()):  # type: ignore[misc]
    """Network connectivity error.

    Raised when network requests fail due to connectivity issues.
    """

    def __init__(
        self,
        message: str = "Network connectivity error",
        *,
        url: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, url=url, retryable=True, **kwargs)


# ============================================================================
# Re-exports from kernelone.errors for backward compatibility
# ============================================================================

from polaris.kernelone.errors import CircuitBreakerOpenError  # noqa: E402

# ============================================================================
# Context Managers for Standardized Error Handling
# ============================================================================


@contextmanager
def tool_execution_context(
    tool_name: str,
    *,
    reraise_llm_errors: bool = True,
) -> Generator[None, None, None]:
    """Context manager for standardized tool execution error handling.

    Wraps tool execution with consistent error classification and conversion.

    Args:
        tool_name: Name of the tool being executed.
        reraise_llm_errors: Whether to reraise LLMError exceptions or wrap them.

    Yields:
        None

    Raises:
        ToolParseError: JSON decode failures in tool arguments.
        ToolExecutionError: File not found, permission errors, unexpected errors.
        BudgetExceededError: Context budget exceeded during tool execution.
    """
    try:
        yield
    except json.JSONDecodeError as e:
        raise ToolParseError(
            f"Invalid JSON in tool {tool_name}: {e}",
            tool_name=tool_name,
            cause=e,
        ) from e
    except FileNotFoundError as e:
        raise ToolExecutionError(
            f"File not found in tool {tool_name}: {e}",
            tool_name=tool_name,
            cause=e,
            retryable=False,
        ) from e
    except PermissionError as e:
        raise ToolExecutionError(
            f"Permission denied in tool {tool_name}: {e}",
            tool_name=tool_name,
            cause=e,
            retryable=False,
        ) from e
    except OSError as e:
        raise ToolExecutionError(
            f"OS error in tool {tool_name}: {e}",
            tool_name=tool_name,
            cause=e,
            retryable=False,
        ) from e
    except _LLMErrorType as e:
        if reraise_llm_errors:
            raise
        # Wrap in ToolExecutionError if not reraising, preserving the cause chain
        raise ToolExecutionError(
            f"LLM error in tool {tool_name}: {e}",
            tool_name=tool_name,
            cause=e,
        ) from e
    except (RuntimeError, ValueError) as e:
        raise ToolExecutionError(
            f"Unexpected error in tool {tool_name}: {e}",
            tool_name=tool_name,
            cause=e,
        ) from e


@contextmanager
def json_parsing_context(
    context: str = "JSON parsing",
    *,
    max_preview: int = 200,
) -> Generator[None, None, None]:
    """Context manager for JSON parsing error handling.

    Provides structured error handling for JSON decode failures.

    Args:
        context: Description of what JSON was being parsed.
        max_preview: Maximum characters to include in error preview.

    Yields:
        None

    Raises:
        JSONParseError: When JSON decoding fails.
    """
    try:
        yield
    except json.JSONDecodeError as e:
        raise JSONParseError(
            f"{context} failed: {e.msg} at position {e.pos}",
            json_error=e,
        ) from e
    except ValueError as e:
        # Sometimes JSON parsing raises ValueError instead of JSONDecodeError
        raise JSONParseError(
            f"{context} failed: {e}",
            cause=e,
        ) from e


@contextmanager
def config_loading_context(
    config_path: str,
) -> Generator[None, None, None]:
    """Context manager for configuration loading error handling.

    Args:
        config_path: Path to the configuration file being loaded.

    Yields:
        None

    Raises:
        ConfigValidationError: When configuration validation fails.
        ConfigMigrationError: When configuration migration fails.
    """
    try:
        yield
    except json.JSONDecodeError as e:
        raise ConfigurationError(
            f"Invalid JSON in config {config_path}: {e}",
            field="file",
        ) from e
    except ValueError as e:
        if "migration" in str(e).lower():
            raise ConfigMigrationError(
                str(e),
                field=config_path,
            ) from e
        raise ConfigurationError(
            str(e),
            field=config_path,
        ) from e
    except RuntimeError as e:
        raise ConfigurationError(
            f"Failed to load config {config_path}: {e}",
            field=config_path,
            cause=e,
        ) from e


# ============================================================================
# Error Classification Utilities
# ============================================================================


def is_retryable(error: Exception) -> bool:
    """Determine if an error is safe to retry.

    Args:
        error: The exception to evaluate.

    Returns:
        True if the error is retryable, False otherwise.
    """
    if isinstance(error, _LLMErrorType):
        return error.retryable  # type: ignore[attr-defined]

    # Default classifications for common exceptions
    error_str = str(error).lower()

    if "timeout" in error_str or "timed out" in error_str:
        return True
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return True
    if "connection" in error_str or "network" in error_str:
        return True
    if "auth" in error_str or "api key" in error_str or "unauthorized" in error_str:
        return False
    return not ("permission" in error_str or "access denied" in error_str)


def wrap_tool_result_error(
    result: dict[str, Any],
    tool_name: str,
) -> _LLMErrorType:
    """Convert a tool result error dict back to an exception.

    Used for re-raising tool errors as proper exceptions.

    Args:
        result: The error result dict from tool execution.
        tool_name: Name of the tool that produced the error.

    Returns:
        Appropriate LLMError subclass.
    """
    error_msg = str(result.get("error", "Unknown error"))
    error_type = str(result.get("error_type", ""))

    if "BUDGET_EXCEEDED" in error_type or "budget" in error_msg.lower():
        return BudgetExceededError(
            error_msg,
            tool=result.get("tool", tool_name),
            file=result.get("file"),
            line_count=result.get("line_count", 0),
            limit=result.get("limit", 2000),
            suggestion=result.get("suggestion"),
        )

    if "JSON" in error_type or "parse" in error_msg.lower():
        return ToolParseError(error_msg, tool_name=tool_name)

    return ToolExecutionError(error_msg, tool_name=tool_name)


# ============================================================================
# Backward Compatibility Aliases
# ============================================================================

# Keep old name as alias for migration period
__all__ = [
    "AuthenticationError",
    "BudgetExceededError",
    # Resilience errors (re-exported from kernelone.errors for backward compat)
    "CircuitBreakerOpenError",
    "ConfigMigrationError",
    "ConfigValidationError",
    # Configuration errors
    "ConfigurationError",
    "JSONParseError",
    # Exception classes
    "LLMError",
    "LLMTimeoutError",  # Preferred name (avoids shadowing built-in)
    "NetworkError",
    # Provider errors
    "ProviderError",
    "RateLimitError",
    "ResponseParseError",
    "TimeoutError",  # Backward compatibility alias
    # Execution errors
    "ToolExecutionError",
    # Parse errors
    "ToolParseError",
    "config_loading_context",
    # Utilities
    "is_retryable",
    "json_parsing_context",
    # Context managers
    "tool_execution_context",
    "wrap_tool_result_error",
]


# ============================================================================
# Lazy module-level attribute access for LLMError
# ============================================================================


def __getattr__(name: str) -> type:
    """Lazily provide LLMError when accessed as a module attribute."""
    if name == "LLMError":
        return _get_llm_error()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Ensure LLMError is available for isinstance checks and type hints at runtime
# This is loaded on first access to any attribute in the module
LLMError = _get_llm_error()
