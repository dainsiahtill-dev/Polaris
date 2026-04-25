"""Shared LLM error category definitions.

This module exists to keep ``engine`` and ``toolkit`` contracts aligned
without introducing package import cycles.

Note:
    This module is deprecated. Please import ErrorCategory from
    polaris.kernelone.errors instead.
"""

from __future__ import annotations

import warnings
from typing import Any, TypeAlias

from polaris.kernelone.errors import ErrorCategory as _CanonicalErrorCategory

# Re-export for backward compatibility with deprecation warning
__all__ = ["ErrorCategory", "_category_from_exception", "classify_error"]


def __getattr__(name: str) -> Any:
    """Provide deprecation warnings for direct module imports."""
    if name == "ErrorCategory":
        warnings.warn(
            "ErrorCategory has been moved to polaris.kernelone.errors. "
            "Please update imports to use: from polaris.kernelone.errors import ErrorCategory",
            DeprecationWarning,
            stacklevel=2,
        )
        return _CanonicalErrorCategory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# For type checking and runtime compatibility
ErrorCategory: TypeAlias = _CanonicalErrorCategory


def _category_from_exception(error: Exception) -> ErrorCategory | None:
    """Extract ErrorCategory from LLMError subclasses.

    Returns None if the exception is not an LLMError or has no category mapping.
    """
    # Avoid circular import at runtime
    from . import exceptions as exc

    if isinstance(error, exc.LLMTimeoutError):
        return ErrorCategory.TIMEOUT
    if isinstance(error, exc.RateLimitError):
        return ErrorCategory.RATE_LIMIT
    if isinstance(error, (exc.NetworkError, exc.CircuitBreakerOpenError)):
        return ErrorCategory.NETWORK_ERROR
    if isinstance(error, (exc.ConfigurationError, exc.ConfigMigrationError, exc.ConfigValidationError)):
        return ErrorCategory.CONFIG_ERROR
    if isinstance(error, (exc.JSONParseError, exc.ResponseParseError, exc.ToolParseError)):
        return ErrorCategory.JSON_PARSE
    if isinstance(error, exc.ProviderError):
        return ErrorCategory.PROVIDER_ERROR
    if isinstance(error, exc.LLMError):
        # Default for LLMError subclasses not explicitly handled
        return ErrorCategory.UNKNOWN
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
    # Try to extract category from LLMError hierarchy first
    category = _category_from_exception(error)
    if category is not None:
        return category

    # Fall back to keyword-based classification for non-LLMError exceptions
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
