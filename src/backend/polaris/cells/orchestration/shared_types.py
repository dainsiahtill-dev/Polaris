"""Shared domain types for orchestration cells.

This module is the single source of truth for error classification types that are
consumed by both `pm_dispatch` and `workflow_runtime`.  Neither cell may import
these types from the other cell; both must import from here.

Dependency rule (enforced by tests/test_orchestration_import_fence.py):
  polaris.cells.orchestration.shared_types
    <- polaris.cells.orchestration.pm_dispatch.*
    <- polaris.cells.orchestration.workflow_runtime.*
  (no reverse edges allowed)

Note:
    ErrorCategory has been moved to polaris.kernelone.errors.
    Please update imports to use: from polaris.kernelone.errors import ErrorCategory
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeAlias

from polaris.kernelone.errors import ErrorCategory as _CanonicalErrorCategory

logger = logging.getLogger(__name__)


# Re-export for backward compatibility with deprecation warning
def __getattr__(name: str):
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


@dataclass
class ErrorRecord:
    """Record of an error occurrence."""

    category: ErrorCategory
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0


@dataclass
class RecoveryRecommendation:
    """Recommendation for error recovery."""

    can_retry: bool
    retry_delay_seconds: float
    max_retries: int
    strategy: str  # "immediate", "backoff", "manual", "abort"
    reason: str


class ErrorClassifier:
    """Classify errors and determine recovery strategies.

    This class is intentionally dependency-free so it can live in shared_types
    without pulling in either pm_dispatch or workflow_runtime internals.
    """

    _ERROR_PATTERNS: dict[ErrorCategory, list[str]] = {
        ErrorCategory.TRANSIENT_NETWORK: [
            "connection refused",
            "connection reset",
            "broken pipe",
            "network is unreachable",
            "temporary failure",
            "try again",
        ],
        ErrorCategory.TRANSIENT_RATE_LIMIT: [
            "rate limit",
            "too many requests",
            "429",
            "throttled",
        ],
        ErrorCategory.TRANSIENT_RESOURCE: [
            "resource temporarily unavailable",
            "out of memory",
            "disk full",
        ],
        ErrorCategory.PERMANENT_AUTH: [
            "unauthorized",
            "forbidden",
            "invalid token",
            "authentication failed",
            "permission denied",
        ],
        ErrorCategory.PERMANENT_VALIDATION: [
            "invalid argument",
            "validation failed",
            "bad request",
            "malformed",
        ],
        ErrorCategory.PERMANENT_NOT_FOUND: [
            "not found",
            "does not exist",
            "no such",
        ],
        ErrorCategory.SYSTEM_TIMEOUT: [
            "timeout",
            "timed out",
            "deadline exceeded",
        ],
        ErrorCategory.WORKFLOW_DEADLOCK: [
            "dependency graph cannot converge",
            "deadlock detected",
            "circular dependency",
        ],
    }

    _RECOVERY_STRATEGIES: dict[ErrorCategory, RecoveryRecommendation] = {
        ErrorCategory.TRANSIENT_NETWORK: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=1.0,
            max_retries=3,
            strategy="backoff",
            reason="Network issues are usually transient",
        ),
        ErrorCategory.TRANSIENT_RATE_LIMIT: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=5.0,
            max_retries=5,
            strategy="backoff",
            reason="Rate limits require backoff",
        ),
        ErrorCategory.TRANSIENT_RESOURCE: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=10.0,
            max_retries=3,
            strategy="backoff",
            reason="Resource constraints may resolve",
        ),
        ErrorCategory.PERMANENT_AUTH: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="Authentication errors require credential update",
        ),
        ErrorCategory.PERMANENT_VALIDATION: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="Validation errors require input correction",
        ),
        ErrorCategory.PERMANENT_NOT_FOUND: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="abort",
            reason="Resource not found, retry won't help",
        ),
        ErrorCategory.PERMANENT_CONFLICT: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="State conflict requires manual resolution",
        ),
        ErrorCategory.SYSTEM_TIMEOUT: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=2.0,
            max_retries=2,
            strategy="backoff",
            reason="Timeouts may be transient, limited retries",
        ),
        ErrorCategory.SYSTEM_CAPACITY: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=30.0,
            max_retries=3,
            strategy="backoff",
            reason="System overloaded, longer backoff",
        ),
        ErrorCategory.SYSTEM_UNKNOWN: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=5.0,
            max_retries=2,
            strategy="backoff",
            reason="Unknown errors, limited retries",
        ),
        ErrorCategory.WORKFLOW_DEADLOCK: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="Deadlock requires dependency graph review",
        ),
        ErrorCategory.WORKFLOW_CANCELED: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="abort",
            reason="Explicitly canceled by user",
        ),
    }

    @classmethod
    def classify(cls, error: Exception) -> ErrorCategory:
        """Classify an error based on its type and message."""
        error_str = f"{type(error).__name__}: {error!s}".lower()

        for category, patterns in cls._ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern in error_str:
                    return category

        if isinstance(error, TimeoutError):
            return ErrorCategory.SYSTEM_TIMEOUT
        if isinstance(error, PermissionError):
            return ErrorCategory.PERMANENT_AUTH
        if isinstance(error, FileNotFoundError):
            return ErrorCategory.PERMANENT_NOT_FOUND
        if isinstance(error, ValueError):
            return ErrorCategory.PERMANENT_VALIDATION

        return ErrorCategory.SYSTEM_UNKNOWN

    @classmethod
    def get_recovery_recommendation(cls, category: ErrorCategory) -> RecoveryRecommendation:
        """Get recovery recommendation for an error category."""
        return cls._RECOVERY_STRATEGIES.get(
            category,
            RecoveryRecommendation(
                can_retry=False,
                retry_delay_seconds=0.0,
                max_retries=0,
                strategy="abort",
                reason="Unknown error type",
            ),
        )

    @classmethod
    def analyze(cls, error: Exception) -> tuple[ErrorCategory, RecoveryRecommendation]:
        """Full analysis: classify and recommend."""
        category = cls.classify(error)
        recommendation = cls.get_recovery_recommendation(category)
        return category, recommendation

    @classmethod
    def classify_from_message(cls, message: str) -> tuple[ErrorCategory, RecoveryRecommendation]:
        """Classify from a message string and return recommendation."""

        class _TemporaryError(Exception):
            pass

        temp_error = _TemporaryError(message)
        category = cls.classify(temp_error)
        recommendation = cls.get_recovery_recommendation(category)
        return category, recommendation


__all__ = [
    "ErrorCategory",
    "ErrorClassifier",
    "ErrorRecord",
    "RecoveryRecommendation",
]
