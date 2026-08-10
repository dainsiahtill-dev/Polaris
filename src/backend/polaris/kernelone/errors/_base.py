"""Base error category, root exceptions, and classification helpers.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Canonical error categories for KernelOne.

    This enum unifies error categorization across all subsystems:
    - LLM errors (provider, timeout, rate limit, etc.)
    - Kernel errors (invalid input, not found, etc.)
    - Orchestration errors (transient vs permanent failures)
    - Tool execution errors (authorization, validation, etc.)

    Usage:
        from polaris.kernelone.errors import ErrorCategory

        def handle_error(error: Exception) -> ErrorCategory:
            if isinstance(error, asyncio.TimeoutError):
                return ErrorCategory.SYSTEM_TIMEOUT
            ...
    """

    # --- LLM-related errors ---
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_RESPONSE = "invalid_response"
    JSON_PARSE = "json_parse"
    CONFIG_ERROR = "config_error"
    NETWORK_ERROR = "network_error"

    # --- Kernel/system errors ---
    UNKNOWN = "unknown"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    FAILED_PRECONDITION = "failed_precondition"
    ABORTED = "aborted"
    OUT_OF_RANGE = "out_of_range"
    UNIMPLEMENTED = "unimplemented"
    INTERNAL_ERROR = "internal_error"
    UNAVAILABLE = "unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    TRANSPORT_ERROR = "transport_error"

    # --- Transient errors (can be retried) ---
    TRANSIENT_NETWORK = "transient_network"
    TRANSIENT_RATE_LIMIT = "transient_rate_limit"
    TRANSIENT_RESOURCE = "transient_resource"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TEMPORARY_FAILURE = "temporary_failure"
    SYSTEM_TIMEOUT = "system_timeout"
    SYSTEM_CAPACITY = "system_capacity"
    SYSTEM_UNKNOWN = "system_unknown"

    # --- Permanent errors (retry won't help) ---
    PERMANENT_AUTH = "permanent_auth"
    PERMANENT_VALIDATION = "permanent_validation"
    PERMANENT_NOT_FOUND = "permanent_not_found"
    PERMANENT_CONFLICT = "permanent_conflict"

    # --- Authorization errors ---
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    INVALID_ARGUMENT = "invalid_argument"
    UNSUPPORTED_OPERATION = "unsupported_operation"

    # --- Workflow errors ---
    WORKFLOW_DEADLOCK = "workflow_deadlock"
    WORKFLOW_CANCELED = "workflow_canceled"


# ============================================================================
# Error Classification Helper
# ============================================================================


def _category_from_llm_exception(error: Exception) -> ErrorCategory | None:
    """Extract ErrorCategory from LLMError subclasses.

    Returns None if the exception is not an LLMError or has no category mapping.
    """
    try:
        from polaris.kernelone.llm.exceptions import (
            CircuitBreakerOpenError,
            ConfigMigrationError,
            ConfigurationError,
            ConfigValidationError,
            JSONParseError,
            LLMError,
            LLMTimeoutError,
            NetworkError,
            ProviderError,
            RateLimitError,
            ResponseParseError,
            ToolParseError,
        )

        if isinstance(error, LLMTimeoutError):
            return ErrorCategory.TIMEOUT
        if isinstance(error, RateLimitError):
            return ErrorCategory.RATE_LIMIT
        if isinstance(error, (NetworkError, CircuitBreakerOpenError)):
            return ErrorCategory.NETWORK_ERROR
        if isinstance(error, (ConfigurationError, ConfigMigrationError, ConfigValidationError)):
            return ErrorCategory.CONFIG_ERROR
        if isinstance(error, (JSONParseError, ResponseParseError, ToolParseError)):
            return ErrorCategory.JSON_PARSE
        if isinstance(error, ProviderError):
            return ErrorCategory.PROVIDER_ERROR
        if isinstance(error, LLMError):
            return ErrorCategory.UNKNOWN
    except ImportError:
        pass
    return None


def classify_error(error: Exception) -> ErrorCategory:
    """Canonical error classifier for the KernelOne LLM subsystem.

    Single source of truth used by both ``executor`` and ``resilience``.
    First checks if the error is an LLMError subclass, then falls back
    to keyword-based classification.

    Args:
        error: The exception to classify.

    Returns:
        The appropriate ErrorCategory for the exception.
    """
    import asyncio

    category = _category_from_llm_exception(error)
    if category is not None:
        return category

    # Type-based checks for common transient exceptions
    if isinstance(error, asyncio.TimeoutError):
        return ErrorCategory.TIMEOUT
    if isinstance(error, ConnectionError):
        return ErrorCategory.NETWORK_ERROR

    error_str = str(error).lower()

    if "timeout" in error_str or "timed out" in error_str:
        return ErrorCategory.TIMEOUT
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return ErrorCategory.RATE_LIMIT
    if "connection" in error_str or "network" in error_str:
        return ErrorCategory.NETWORK_ERROR
    if "config" in error_str or "configuration" in error_str:
        return ErrorCategory.CONFIG_ERROR
    if "json" in error_str or "parse" in error_str:
        return ErrorCategory.JSON_PARSE
    if "invalid" in error_str or "response" in error_str:
        return ErrorCategory.INVALID_RESPONSE

    return ErrorCategory.UNKNOWN


# ============================================================================
# Root Base Exception
# ============================================================================


class KernelOneError(Exception):
    """Base exception for all KernelOne and Cell errors.

    This is the single root exception for all KernelOne subsystems and Cells,
    providing a unified error hierarchy for catching and handling.

    Intent Separation (P1-014):
        此异常与 Kernel Cell 服务层异常（polaris.cells.roles.kernel.internal.services.contracts.KernelError）
        意图分离：
        - KernelOneError: KernelOne 运行时基础层异常（配置、事件、审计、基础设施）
        - KernelError: Kernel Cell 服务层业务异常（LLM调用、工具执行、策略控制）
        两者处于不同抽象层次，不存在继承关系。

    Attributes:
        message: Human-readable error description.
        code: Machine-readable error code (e.g., "CONFIG_LOAD_FAILED").
        cause: The original exception that triggered this error, if any.
        details: Additional context for debugging and classification.
        retryable: Whether the error is safe to retry without changes.

    Example:
        try:
            await some_kernel_operation()
        except KernelOneError as e:
            logger.error(f"KernelOne error [{e.code}]: {e.message}")
            if e.retryable:
                await retry_operation()
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "KERNEL_ERROR",
        cause: Exception | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause
        self._message = message
        self.details = details or {}
        self.retryable = retryable

    def __str__(self) -> str:
        return self._message

    def to_dict(self) -> dict[str, Any]:
        """Serialize exception to a JSON-compatible dictionary."""
        result: dict[str, Any] = {
            "type": self.__class__.__name__,
            "code": self.code,
            "message": self._message,
            "retryable": self.retryable,
        }
        if self.__cause__ is not None:
            result["cause"] = {
                "type": type(self.__cause__).__name__,
                "message": str(self.__cause__),
            }
        if self.details:
            result["details"] = self.details
        return result


# ============================================================================
# LLM Errors (Base for LLM-specific exceptions)
# ============================================================================


class LLMError(KernelOneError):
    """Base exception for all LLM module errors.

    Inherits from KernelOneError to provide unified exception hierarchy (P0-NEW-004 fix).
    This enables catching all KernelOne errors with:
        except KernelOneError:  # catches LLMError too!

    This is the base for LLM-specific parse errors (ToolParseError, ResponseParseError, JSONParseError).
    Subclasses should define more specific error codes.

    Attributes:
        message: Human-readable error description.
        cause: The original exception that triggered this error, if any.
        retryable: Whether the error is safe to retry without changes.
        details: Additional context for debugging and classification.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: Exception | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="LLM_ERROR", cause=cause, details=details, retryable=retryable)

    def to_dict(self) -> dict[str, Any]:
        """Serialize exception to a JSON-compatible dictionary."""
        return super().to_dict()
