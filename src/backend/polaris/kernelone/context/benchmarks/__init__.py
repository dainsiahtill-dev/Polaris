"""ContextOS Benchmark Framework — baseline validators for State-First Context OS.

.. deprecated::
    This package is deprecated. Use ``polaris.kernelone.benchmark.unified_models``
    for new benchmark case definitions and ``polaris.kernelone.benchmark.unified_runner``
    for execution. The canonical benchmark framework is now
    ``polaris/kernelone/benchmark/``.

    This package is retained for backward compatibility with existing
    ContextOS benchmark validators and will be removed in a future release.

Provides quality validators for detecting:
- Context loss (null/zero tokens)
- Unbounded token growth (long sessions)
- Turn/token desynchronization
- Incorrect truncation / over-compaction
"""

from __future__ import annotations

from polaris.kernelone.context.benchmarks.fixtures import (
    BenchmarkCase,
    BudgetConditions,
    load_all_fixtures,
    load_fixture,
)
from polaris.kernelone.context.benchmarks.validators import (
    ContextOSBenchmarkValidator,
    ContextOSDesynchronizationValidator,
    ContextOSIncorrectTruncationValidator,
    ContextOSLongSessionValidator,
    ContextOSLossValidator,
    ContextOSValidator,
    FixtureAwareBenchmarkValidator,
    ValidatorResult,
    ValidatorViolation,
)

__all__ = [
    "BenchmarkCase",
    "BudgetConditions",
    "ContextOSBenchmarkValidator",
    "ContextOSDesynchronizationValidator",
    "ContextOSIncorrectTruncationValidator",
    "ContextOSLongSessionValidator",
    "ContextOSLossValidator",
    "ContextOSValidator",
    "FixtureAwareBenchmarkValidator",
    "ValidatorResult",
    "ValidatorViolation",
    "load_all_fixtures",
    "load_fixture",
]
