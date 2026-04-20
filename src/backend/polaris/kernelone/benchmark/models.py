"""Performance Benchmark Models.

This module provides data models for storing and serializing
performance benchmark results.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class BenchmarkStats:
    """Statistics container for benchmark measurements.

    Attributes:
        latencies: List of measured latencies in milliseconds.
        warmup_count: Number of warmup iterations performed.
        iterations: Number of measurement iterations.
        p50: 50th percentile latency (median).
        p90: 90th percentile latency.
        p99: 99th percentile latency.
        mean: Mean latency.
        std_dev: Standard deviation of latencies.
        min_latency: Minimum latency.
        max_latency: Maximum latency.
    """

    latencies: list[float] = field(default_factory=list)
    warmup_count: int = 3
    iterations: int = 100
    p50: float = 0.0
    p90: float = 0.0
    p99: float = 0.0
    mean: float = 0.0
    std_dev: float = 0.0
    min_latency: float = 0.0
    max_latency: float = 0.0

    def compute_statistics(self) -> None:
        """Compute percentile and summary statistics from latencies.

        This method should be called after collecting all latency measurements.
        """
        if not self.latencies:
            return

        sorted_latencies = sorted(self.latencies)
        n = len(sorted_latencies)

        self.p50 = sorted_latencies[int(n * 0.50)]
        self.p90 = sorted_latencies[int(n * 0.90)]
        self.p99 = sorted_latencies[int(n * 0.99)]
        self.mean = statistics.mean(sorted_latencies)
        self.std_dev = statistics.stdev(sorted_latencies) if n > 1 else 0.0
        self.min_latency = min(sorted_latencies)
        self.max_latency = max(sorted_latencies)


@dataclass
class MemoryStats:
    """Memory usage statistics.

    Attributes:
        current_bytes: Current memory allocation in bytes.
        peak_bytes: Peak memory allocation in bytes.
        current_mb: Current memory in megabytes.
        peak_mb: Peak memory in megabytes.
        allocations: Number of allocations recorded.
    """

    current_bytes: int = 0
    peak_bytes: int = 0
    current_mb: float = 0.0
    peak_mb: float = 0.0
    allocations: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "current_bytes": self.current_bytes,
            "peak_bytes": self.peak_bytes,
            "current_mb": round(self.current_mb, 3),
            "peak_mb": round(self.peak_mb, 3),
            "allocations": self.allocations,
        }


@dataclass
class ThroughputStats:
    """Throughput measurement statistics.

    Attributes:
        total_operations: Total number of operations performed.
        duration_ms: Total duration in milliseconds.
        ops_per_second: Operations per second.
        ops_per_minute: Operations per minute.
        avg_latency_ms: Average latency per operation in milliseconds.
    """

    total_operations: int = 0
    duration_ms: float = 0.0
    ops_per_second: float = 0.0
    ops_per_minute: float = 0.0
    avg_latency_ms: float = 0.0

    def compute(self) -> None:
        """Compute derived throughput metrics."""
        if self.duration_ms > 0:
            duration_s = self.duration_ms / 1000.0
            self.ops_per_second = self.total_operations / duration_s
            self.ops_per_minute = self.total_operations / duration_s * 60.0
            self.avg_latency_ms = self.duration_ms / self.total_operations if self.total_operations > 0 else 0.0


@dataclass
class BenchmarkResult:
    """Structured benchmark result for serialization.

    Attributes:
        metric_name: Name of the metric being measured.
        p50_ms: 50th percentile latency in milliseconds.
        p90_ms: 90th percentile latency in milliseconds.
        p99_ms: 99th percentile latency in milliseconds.
        mean_ms: Mean latency in milliseconds.
        std_dev_ms: Standard deviation in milliseconds.
        min_ms: Minimum latency in milliseconds.
        max_ms: Maximum latency in milliseconds.
        iterations: Number of iterations performed.
        warmup_count: Number of warmup iterations.
        timestamp: ISO format timestamp of when benchmark was run.
    """

    metric_name: str
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p99_ms: float = 0.0
    mean_ms: float = 0.0
    std_dev_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    iterations: int = 0
    warmup_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_stats(cls, metric_name: str, stats: BenchmarkStats) -> BenchmarkResult:
        """Create a BenchmarkResult from a BenchmarkStats object.

        Args:
            metric_name: Name of the metric.
            stats: BenchmarkStats with collected measurements.

        Returns:
            BenchmarkResult with computed statistics.
        """
        return cls(
            metric_name=metric_name,
            p50_ms=stats.p50,
            p90_ms=stats.p90,
            p99_ms=stats.p99,
            mean_ms=stats.mean,
            std_dev_ms=stats.std_dev,
            min_ms=stats.min_latency,
            max_ms=stats.max_latency,
            iterations=stats.iterations,
            warmup_count=stats.warmup_count,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "metric_name": self.metric_name,
            "p50_ms": round(self.p50_ms, 3),
            "p90_ms": round(self.p90_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "std_dev_ms": round(self.std_dev_ms, 3),
            "min_ms": round(self.min_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "iterations": self.iterations,
            "warmup_count": self.warmup_count,
            "timestamp": self.timestamp,
        }


@dataclass
class LatencyBenchmarkResult:
    """Latency-specific benchmark result.

    This extends BenchmarkResult with additional latency-specific fields.
    """

    metric_name: str = ""
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p99_ms: float = 0.0
    mean_ms: float = 0.0
    std_dev_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    iterations: int = 0
    warmup_count: int = 0
    timestamp: str = ""
    median_ms: float = 0.0
    percentile_95_ms: float = 0.0
    percentile_99_ms: float = 0.0
    tail_latency_ratio: float = 0.0  # p99/p50 ratio

    def compute_tail_ratio(self) -> None:
        """Compute tail latency ratio (p99/p50)."""
        if self.median_ms > 0:
            self.tail_latency_ratio = self.percentile_99_ms / self.median_ms


@dataclass
class MemoryBenchmarkResult:
    """Memory-specific benchmark result."""

    memory_baseline_mb: float = 0.0
    memory_delta_mb: float = 0.0
    memory_peak_mb: float = 0.0
    allocations_count: int = 0
    deallocations_count: int = 0
    gc_collections: tuple[int, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "memory_baseline_mb": round(self.memory_baseline_mb, 3),
            "memory_delta_mb": round(self.memory_delta_mb, 3),
            "memory_peak_mb": round(self.memory_peak_mb, 3),
            "allocations_count": self.allocations_count,
            "deallocations_count": self.deallocations_count,
            "gc_collections": self.gc_collections,
        }
