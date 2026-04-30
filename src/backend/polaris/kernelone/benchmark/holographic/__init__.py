"""Holographic benchmark runner package.

Re-exports all public APIs from the split modules to preserve backward
compatibility for existing imports.
"""

from __future__ import annotations

from polaris.kernelone.benchmark.holographic.config import (
    HolographicRunResult,
    HolographicSuiteResult,
    RunStatus,
)
from polaris.kernelone.benchmark.holographic.reports import format_suite_report
from polaris.kernelone.benchmark.holographic.runner import (
    EXECUTORS,
    TempfileWorkspace,
    run_case,
    run_holographic_suite,
)
from polaris.kernelone.benchmark.holographic.stats import (
    _boundary_retention,
    _chunk_ranges_fixed_80,
    _chunk_ranges_from_semantic,
    _contains_redacted,
    _evaluate_thresholds,
    _now_iso,
    _perf_ms,
    _python_block_ranges,
    _seed_random,
    _serialized_json,
    _token_similarity,
)

__all__ = [
    "EXECUTORS",
    "HolographicRunResult",
    "HolographicSuiteResult",
    "RunStatus",
    "TempfileWorkspace",
    "_boundary_retention",
    "_chunk_ranges_fixed_80",
    "_chunk_ranges_from_semantic",
    "_contains_redacted",
    "_evaluate_thresholds",
    "_now_iso",
    "_perf_ms",
    "_python_block_ranges",
    "_seed_random",
    "_serialized_json",
    "_token_similarity",
    "format_suite_report",
    "run_case",
    "run_holographic_suite",
]
