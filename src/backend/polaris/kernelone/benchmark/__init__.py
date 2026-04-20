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
from polaris.kernelone.benchmark.holographic_runner import (
    HolographicRunResult,
    HolographicSuiteResult,
    RunStatus,
    run_case,
    run_holographic_suite,
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

__all__ = [
    "AlertMetrics",
    "AuditAlertFiringValidator",
    # Audit Metrics Benchmark
    "AuditMetricsBenchmarker",
    "AuditStorageTierValidator",
    "AuditStormDetectionValidator",
    "AuditThroughputValidator",
    "BenchmarkContext",
    "BenchmarkMode",
    "CaseReadiness",
    # Performance Benchmark Models
    "BenchmarkResult",
    "BenchmarkRunResult",
    "BenchmarkStats",
    "BenchmarkSuiteResult",
    "BudgetConditions",
    # Throughput Benchmarking
    "FixedIterationThroughputBench",
    "HOLOGRAPHIC_CASES",
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
    "RunStatus",
    # Performance Benchmark Fixtures
    "async_memory_benchmark",
    "case_ids",
    # Memory Benchmarking
    "async_memory_profile",
    "benchmark",
    "get_audit_benchmark_cases",
    "get_validator",
    "measure_latency",
    "measure_latency_async",
    "memory_benchmark",
    "memory_profile",
    "list_holographic_cases",
    "ready_case_ids",
    "run_case",
    "run_holographic_suite",
    "throughput",
    "throughput_benchmark",
]
