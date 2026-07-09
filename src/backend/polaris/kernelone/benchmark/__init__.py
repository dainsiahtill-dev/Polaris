"""Unified Benchmark Framework for Polaris.

This module provides a unified benchmark system that consolidates
Agentic, Strategy, and Context benchmark capabilities into a single,
coherent architecture.

Performance Benchmark Framework
--------------------------------
Specialized benchmarking for performance characteristics:
- Latency measurement (p50, p90, p95, p99 percentiles)
- Memory allocation tracking (tracemalloc integration)
- Throughput measurement (ops/s, ops/min)

ContextOS Benchmarks
--------------------
Additional specialized benchmarking for ContextOS reliability:
- Long session compression verification
- Context desynchronization detection
- Incorrect truncation detection
- Context loss prevention
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Audit Metrics Benchmark
from polaris.kernelone.benchmark.audit_metrics import (
    AlertMetrics,
    AuditAlertFiringValidator,
    AuditMetricsBenchmarker,
    AuditStorageTierValidator,
    AuditStormDetectionValidator,
    AuditThroughputValidator,
    StormDetectionMetrics,
    ThroughputMetrics,
    get_audit_benchmark_cases,
    get_validator,
)
from polaris.kernelone.benchmark.fixtures import (
    BenchmarkContext,
    async_memory_benchmark,
    benchmark,
    benchmark as benchmark_decorator,
    memory_benchmark,
    throughput_benchmark,
)

if TYPE_CHECKING:
    from polaris.kernelone.benchmark.holographic import (
        HolographicRunResult,
        HolographicSuiteResult,
        RunStatus,
        run_case,
        run_holographic_suite,
    )
    from polaris.kernelone.benchmark.holographic_models import (
        CaseReadiness,
        HolographicCase,
    )
    from polaris.kernelone.benchmark.holographic_registry import (
        HOLOGRAPHIC_CASES,
        case_ids,
        list_holographic_cases,
        ready_case_ids,
    )
from polaris.kernelone.benchmark.latency import (
    LatencyBenchmarker,
    LatencyMeasurement,
    LatencyProfile,
    measure_latency,
    measure_latency_async,
)
from polaris.kernelone.benchmark.memory import (
    MemoryBenchmarker,
    MemoryProfile,
    MemorySnapshot,
    MemoryTracker,
    async_memory_profile,
    memory_profile,
)

# Performance Benchmark Framework
from polaris.kernelone.benchmark.models import (
    BenchmarkResult,
    BenchmarkStats,
    LatencyBenchmarkResult,
    MemoryBenchmarkResult,
    MemoryStats,
    ThroughputStats,
)
from polaris.kernelone.benchmark.throughput import (
    FixedIterationThroughputBench,
    ThroughputBenchmarker,
    ThroughputMeasurement,
    ThroughputProfile,
    TimeBasedThroughputBench,
    throughput,
)

# Unified Evaluation Framework
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    BenchmarkMode,
    BudgetConditions,
    JudgeCheck,
    JudgeConfig,
    ObservedBenchmarkRun,
    ToolArgumentRule,
    ToolCallObservation,
    UnifiedBenchmarkCase,
    UnifiedJudgeVerdict,
)
from polaris.kernelone.benchmark.unified_runner import (
    BenchmarkRunResult,
    BenchmarkSuiteResult,
    UnifiedBenchmarkRunner,
)

_LAZY_EXPORT_MODULES = {
    "HolographicRunResult": "polaris.kernelone.benchmark.holographic",
    "HolographicSuiteResult": "polaris.kernelone.benchmark.holographic",
    "RunStatus": "polaris.kernelone.benchmark.holographic",
    "run_case": "polaris.kernelone.benchmark.holographic",
    "run_holographic_suite": "polaris.kernelone.benchmark.holographic",
    "CaseReadiness": "polaris.kernelone.benchmark.holographic_models",
    "HolographicCase": "polaris.kernelone.benchmark.holographic_models",
    "HOLOGRAPHIC_CASES": "polaris.kernelone.benchmark.holographic_registry",
    "case_ids": "polaris.kernelone.benchmark.holographic_registry",
    "list_holographic_cases": "polaris.kernelone.benchmark.holographic_registry",
    "ready_case_ids": "polaris.kernelone.benchmark.holographic_registry",
}


def __getattr__(name: str) -> Any:
    """Lazily expose optional benchmark suites without importing their deps."""
    module_name = _LAZY_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "HOLOGRAPHIC_CASES",
    "AlertMetrics",
    "AuditAlertFiringValidator",
    # Audit Metrics Benchmark
    "AuditMetricsBenchmarker",
    "AuditStorageTierValidator",
    "AuditStormDetectionValidator",
    "AuditThroughputValidator",
    "BenchmarkContext",
    "BenchmarkMode",
    # Performance Benchmark Models
    "BenchmarkResult",
    "BenchmarkRunResult",
    "BenchmarkStats",
    "BenchmarkSuiteResult",
    "BudgetConditions",
    "CaseReadiness",
    # Throughput Benchmarking
    "FixedIterationThroughputBench",
    "HolographicCase",
    "HolographicRunResult",
    "HolographicSuiteResult",
    "JudgeCheck",
    "JudgeConfig",
    "LatencyBenchmarkResult",
    # Latency Benchmarking
    "LatencyBenchmarker",
    "LatencyMeasurement",
    "LatencyProfile",
    "MemoryBenchmarkResult",
    "MemoryBenchmarker",
    "MemoryProfile",
    "MemorySnapshot",
    "MemoryStats",
    "MemoryTracker",
    "ObservedBenchmarkRun",
    "RunStatus",
    "StormDetectionMetrics",
    "ThroughputBenchmarker",
    "ThroughputMeasurement",
    "ThroughputMetrics",
    "ThroughputProfile",
    "ThroughputStats",
    "TimeBasedThroughputBench",
    "ToolArgumentRule",
    "ToolCallObservation",
    # Unified Evaluation Framework
    "UnifiedBenchmarkCase",
    "UnifiedBenchmarkRunner",
    "UnifiedJudge",
    "UnifiedJudgeVerdict",
    # Performance Benchmark Fixtures
    "async_memory_benchmark",
    # Memory Benchmarking
    "async_memory_profile",
    "benchmark",
    "case_ids",
    "get_audit_benchmark_cases",
    "get_validator",
    "list_holographic_cases",
    "measure_latency",
    "measure_latency_async",
    "memory_benchmark",
    "memory_profile",
    "ready_case_ids",
    "run_case",
    "run_holographic_suite",
    "throughput",
    "throughput_benchmark",
]
