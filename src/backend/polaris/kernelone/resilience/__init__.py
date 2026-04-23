"""KernelOne resilience utilities."""

from __future__ import annotations

from polaris.kernelone.resilience import backoff
from polaris.kernelone.resilience.retry_policy import (
    RetryContext,
    RetryPolicy,
    compute_delay,
    execute_with_retry,
    should_retry,
)
from polaris.kernelone.resilience.self_healing import (
    AlternativeStrategy,
    FailureType,
    HealingResult,
    RetryStrategy,
    SelfHealingExecutor,
)

BackoffController = backoff.BackoffController
build_backoff_seconds = backoff.build_backoff_seconds

__all__ = [
    "AlternativeStrategy",
    "BackoffController",
    "FailureType",
    "HealingResult",
    "RetryContext",
    "RetryPolicy",
    "RetryStrategy",
    "SelfHealingExecutor",
    "backoff",
    "build_backoff_seconds",
    "compute_delay",
    "execute_with_retry",
    "should_retry",
]
