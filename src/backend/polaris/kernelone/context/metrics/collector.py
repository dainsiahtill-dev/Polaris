"""Metrics collector with instance-variable-based metrics.

This module provides ContextOS runtime metrics collection using dynamic
instance variables rather than hardcoded values.

Usage:
    from polaris.kernelone.context.metrics.collector import MetricsCollector

    collector = MetricsCollector()
    collector.increment_receipt_write_total()
    collector.increment_receipt_write_failures()
    rate = collector.receipt_write_failure_rate
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = ["MetricsCollector", "MetricsSnapshot"]


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Immutable snapshot of metrics at a point in time."""

    receipt_write_total: int = 0
    receipt_write_failures: int = 0
    receipt_write_failure_rate: float = 0.0
    sqlite_write_p95_ms: float = 0.0
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_write_total": self.receipt_write_total,
            "receipt_write_failures": self.receipt_write_failures,
            "receipt_write_failure_rate": round(self.receipt_write_failure_rate, 4),
            "sqlite_write_p95_ms": round(self.sqlite_write_p95_ms, 2),
            "collected_at": self.collected_at,
        }


class MetricsCollector:
    """ContextOS metrics collector using dynamic instance variables.

    This collector tracks metrics via instance variables that are updated
    by the runtime, rather than hardcoded values or polling external sources.

    Metrics tracked:
    - receipt_write_total: Total number of receipt write attempts
    - receipt_write_failures: Number of failed receipt write attempts
    - sqlite_write_p95_ms: p95 latency for SQLite write operations (instance variable)

    Usage:
        collector = MetricsCollector()
        collector.increment_receipt_write_total()
        collector.increment_receipt_write_failures()
        rate = collector.receipt_write_failure_rate  # Dynamic calculation
    """

    def __init__(self) -> None:
        self._receipt_write_total: int = 0
        self._receipt_write_failures: int = 0
        self._sqlite_write_p95_ms: float = 0.0
        self._sqlite_write_latencies: list[float] = []
        self._lock = threading.Lock()

    # ─────────────────────────────────────────────────────────────────
    # Receipt write metrics (dynamic instance variables)
    # ─────────────────────────────────────────────────────────────────

    @property
    def receipt_write_total(self) -> int:
        """Total number of receipt write attempts."""
        return self._receipt_write_total

    @property
    def receipt_write_failures(self) -> int:
        """Number of failed receipt write attempts."""
        return self._receipt_write_failures

    @property
    def receipt_write_failure_rate(self) -> float:
        """Failure rate for receipt writes (dynamically calculated).

        Returns:
            Ratio of failures to total attempts, or 0.0 if no attempts.
        """
        total = self._receipt_write_total
        if total == 0:
            return 0.0
        return self._receipt_write_failures / total

    @property
    def sqlite_write_p95_ms(self) -> float:
        """p95 latency for SQLite write operations in milliseconds.

        Returns:
            The p95 write latency from collected samples, or 0.0 if no samples.
        """
        return self._sqlite_write_p95_ms

    # ─────────────────────────────────────────────────────────────────
    # Counters
    # ─────────────────────────────────────────────────────────────────

    def increment_receipt_write_total(self, count: int = 1) -> None:
        """Increment the total receipt write counter.

        Args:
            count: Number to add to the counter (default 1).
        """
        with self._lock:
            self._receipt_write_total += count

    def increment_receipt_write_failures(self, count: int = 1) -> None:
        """Increment the receipt write failures counter.

        Args:
            count: Number to add to the failures counter (default 1).
        """
        with self._lock:
            self._receipt_write_failures += count

    def record_sqlite_write_latency(self, latency_ms: float) -> None:
        """Record a SQLite write latency sample.

        The p95 is recalculated after each sample is added.
        Latencies beyond 100ms are tracked as potential failures.

        Args:
            latency_ms: Write latency in milliseconds.
        """
        with self._lock:
            self._sqlite_write_latencies.append(latency_ms)
            # Keep only last 1000 samples to bound memory
            if len(self._sqlite_write_latencies) > 1000:
                self._sqlite_write_latencies = self._sqlite_write_latencies[-1000:]
            self._recalculate_p95()

    def _recalculate_p95(self) -> None:
        """Recalculate p95 from collected latencies (must hold lock)."""
        if not self._sqlite_write_latencies:
            self._sqlite_write_p95_ms = 0.0
            return
        sorted_latencies = sorted(self._sqlite_write_latencies)
        n = len(sorted_latencies)
        p95_index = int((n - 1) * 0.95)
        p95_index = min(max(p95_index, 0), n - 1)
        self._sqlite_write_p95_ms = sorted_latencies[p95_index]

    # ─────────────────────────────────────────────────────────────────
    # Snapshot
    # ─────────────────────────────────────────────────────────────────

    def snapshot(self) -> MetricsSnapshot:
        """Get an immutable snapshot of current metrics.

        Returns:
            MetricsSnapshot with current metric values.
        """
        with self._lock:
            return MetricsSnapshot(
                receipt_write_total=self._receipt_write_total,
                receipt_write_failures=self._receipt_write_failures,
                receipt_write_failure_rate=(self._receipt_write_failures / max(1, self._receipt_write_total)),
                sqlite_write_p95_ms=self._sqlite_write_p95_ms,
            )

    # ─────────────────────────────────────────────────────────────────
    # Reset (for testing)
    # ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset all metrics to initial state (for testing)."""
        with self._lock:
            self._receipt_write_total = 0
            self._receipt_write_failures = 0
            self._sqlite_write_p95_ms = 0.0
            self._sqlite_write_latencies.clear()
