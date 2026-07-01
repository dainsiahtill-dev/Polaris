"""Shared domain types for orchestration cells.

This module owns orchestration-local error classification helpers consumed by
both `pm_dispatch` and `workflow_runtime`.  Neither cell may import these helper
types from the other cell; both must import from here.

Dependency rule (enforced by tests/test_orchestration_import_fence.py):
  polaris.cells.orchestration.shared_types
    <- polaris.cells.orchestration.pm_dispatch.*
    <- polaris.cells.orchestration.workflow_runtime.*
  (no reverse edges allowed)

Error category enum ownership lives in ``polaris.kernelone.errors``.  Callers
that need the enum must import it from that canonical owner directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.errors import ErrorCategory as KernelErrorCategory

logger = logging.getLogger(__name__)


@dataclass
class ErrorRecord:
    """Record of an error occurrence."""

    category: KernelErrorCategory
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

    _ERROR_PATTERNS: dict[KernelErrorCategory, list[str]] = {
        KernelErrorCategory.TRANSIENT_NETWORK: [
            "connection refused",
            "connection reset",
            "broken pipe",
            "network is unreachable",
            "temporary failure",
            "try again",
        ],
        KernelErrorCategory.TRANSIENT_RATE_LIMIT: [
            "rate limit",
            "too many requests",
            "429",
            "throttled",
        ],
        KernelErrorCategory.TRANSIENT_RESOURCE: [
            "resource temporarily unavailable",
            "out of memory",
            "disk full",
        ],
        KernelErrorCategory.PERMANENT_AUTH: [
            "unauthorized",
            "forbidden",
            "invalid token",
            "authentication failed",
            "permission denied",
        ],
        KernelErrorCategory.PERMANENT_VALIDATION: [
            "invalid argument",
            "validation failed",
            "bad request",
            "malformed",
        ],
        KernelErrorCategory.PERMANENT_NOT_FOUND: [
            "not found",
            "does not exist",
            "no such",
        ],
        KernelErrorCategory.SYSTEM_TIMEOUT: [
            "timeout",
            "timed out",
            "deadline exceeded",
        ],
        KernelErrorCategory.WORKFLOW_DEADLOCK: [
            "dependency graph cannot converge",
            "deadlock detected",
            "circular dependency",
        ],
    }

    _RECOVERY_STRATEGIES: dict[KernelErrorCategory, RecoveryRecommendation] = {
        KernelErrorCategory.TRANSIENT_NETWORK: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=1.0,
            max_retries=3,
            strategy="backoff",
            reason="Network issues are usually transient",
        ),
        KernelErrorCategory.TRANSIENT_RATE_LIMIT: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=5.0,
            max_retries=5,
            strategy="backoff",
            reason="Rate limits require backoff",
        ),
        KernelErrorCategory.TRANSIENT_RESOURCE: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=10.0,
            max_retries=3,
            strategy="backoff",
            reason="Resource constraints may resolve",
        ),
        KernelErrorCategory.PERMANENT_AUTH: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="Authentication errors require credential update",
        ),
        KernelErrorCategory.PERMANENT_VALIDATION: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="Validation errors require input correction",
        ),
        KernelErrorCategory.PERMANENT_NOT_FOUND: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="abort",
            reason="Resource not found, retry won't help",
        ),
        KernelErrorCategory.PERMANENT_CONFLICT: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="State conflict requires manual resolution",
        ),
        KernelErrorCategory.SYSTEM_TIMEOUT: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=2.0,
            max_retries=2,
            strategy="backoff",
            reason="Timeouts may be transient, limited retries",
        ),
        KernelErrorCategory.SYSTEM_CAPACITY: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=30.0,
            max_retries=3,
            strategy="backoff",
            reason="System overloaded, longer backoff",
        ),
        KernelErrorCategory.SYSTEM_UNKNOWN: RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=5.0,
            max_retries=2,
            strategy="backoff",
            reason="Unknown errors, limited retries",
        ),
        KernelErrorCategory.WORKFLOW_DEADLOCK: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="manual",
            reason="Deadlock requires dependency graph review",
        ),
        KernelErrorCategory.WORKFLOW_CANCELED: RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="abort",
            reason="Explicitly canceled by user",
        ),
    }

    @classmethod
    def classify(cls, error: Exception) -> KernelErrorCategory:
        """Classify an error based on its type and message."""
        error_str = f"{type(error).__name__}: {error!s}".lower()

        for category, patterns in cls._ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern in error_str:
                    return category

        if isinstance(error, TimeoutError):
            return KernelErrorCategory.SYSTEM_TIMEOUT
        if isinstance(error, PermissionError):
            return KernelErrorCategory.PERMANENT_AUTH
        if isinstance(error, FileNotFoundError):
            return KernelErrorCategory.PERMANENT_NOT_FOUND
        if isinstance(error, ValueError):
            return KernelErrorCategory.PERMANENT_VALIDATION

        return KernelErrorCategory.SYSTEM_UNKNOWN

    @classmethod
    def get_recovery_recommendation(cls, category: KernelErrorCategory) -> RecoveryRecommendation:
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
    def analyze(cls, error: Exception) -> tuple[KernelErrorCategory, RecoveryRecommendation]:
        """Full analysis: classify and recommend."""
        category = cls.classify(error)
        recommendation = cls.get_recovery_recommendation(category)
        return category, recommendation

    @classmethod
    def classify_from_message(cls, message: str) -> tuple[KernelErrorCategory, RecoveryRecommendation]:
        """Classify from a message string and return recommendation."""

        class _TemporaryError(Exception):
            pass

        temp_error = _TemporaryError(message)
        category = cls.classify(temp_error)
        recommendation = cls.get_recovery_recommendation(category)
        return category, recommendation


__all__ = [
    "ErrorClassifier",
    "ErrorRecord",
    "RecoveryRecommendation",
]
