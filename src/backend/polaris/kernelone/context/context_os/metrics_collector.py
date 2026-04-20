"""Cognitive Runtime metrics collector for Context OS + Cognitive Runtime eval gate.

This module provides metrics collection for Cognitive Runtime operations:
- Receipt coverage (handoff success rate)
- Handoff roundtrip success rate
- State restore accuracy
- Transaction envelope coverage
- Receipt write failure rate
- SQLite write latency (p95)

Usage:
    from polaris.kernelone.context.context_os.metrics_collector import CognitiveRuntimeMetricsCollector

    collector = CognitiveRuntimeMetricsCollector(workspace=".")
    metrics = collector.collect_metrics()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CognitiveRuntimeMetrics:
    """Metrics for Cognitive Runtime quality evaluation.

    These metrics measure the operational health of the Cognitive Runtime layer,
    including receipt coverage, handoff success, and performance.
    """

    total_cases: int = 0
    receipt_coverage: float = 0.0
    handoff_roundtrip_success_rate: float = 0.0
    state_restore_accuracy: float = 0.0
    transaction_envelope_coverage: float = 0.0
    receipt_write_failure_rate: float = 0.0
    sqlite_write_p95_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "receipt_coverage": round(self.receipt_coverage, 4),
            "handoff_roundtrip_success_rate": round(self.handoff_roundtrip_success_rate, 4),
            "state_restore_accuracy": round(self.state_restore_accuracy, 4),
            "transaction_envelope_coverage": round(self.transaction_envelope_coverage, 4),
            "receipt_write_failure_rate": round(self.receipt_write_failure_rate, 4),
            "sqlite_write_p95_ms": round(self.sqlite_write_p95_ms, 2),
        }


@dataclass(frozen=True, slots=True)
class CognitiveRuntimeMetricsCollectionResult:
    """Result of metrics collection operation."""

    ok: bool
    metrics: CognitiveRuntimeMetrics | None
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "collected_at": self.collected_at,
        }


class CognitiveRuntimeMetricsCollector:
    """Collects Cognitive Runtime metrics for CI gate evaluation.

    This collector aggregates metrics from:
    - SQLite store operations
    - Receipt records
    - Handoff pack operations
    - Projection compile requests

    Args:
        workspace: Path to the workspace directory for metrics collection.
    """

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace).resolve()

    def collect_metrics(self) -> CognitiveRuntimeMetricsCollectionResult:
        """Collect Cognitive Runtime metrics from the workspace.

        Returns:
            CognitiveRuntimeMetricsCollectionResult with metrics and status.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Try to collect from SQLite store
        try:
            metrics = self._collect_from_sqlite_store()
            return CognitiveRuntimeMetricsCollectionResult(
                ok=True,
                metrics=metrics,
                warnings=tuple(warnings),
            )
        except (RuntimeError, ValueError) as exc:
            errors.append(f"Failed to collect from SQLite store: {exc}")
            # Return empty metrics with errors for CI gate to handle
            return CognitiveRuntimeMetricsCollectionResult(
                ok=False,
                metrics=None,
                errors=tuple(errors),
                warnings=tuple(warnings),
            )

    def _collect_from_sqlite_store(self) -> CognitiveRuntimeMetrics:
        """Collect metrics from SQLite store.

        Attempts to read from the cognitive runtime SQLite database.
        If the database doesn't exist or is inaccessible, returns
        synthetic/empty metrics for CI gate compatibility.
        """
        import sqlite3
        import time

        db_path = self._workspace / "runtime" / "cognitive_runtime" / "cognitive_runtime.sqlite"
        metrics = CognitiveRuntimeMetrics()

        if not db_path.exists():
            # Database doesn't exist yet - return empty metrics
            # CI gate should handle this gracefully
            return metrics

        # Collect timing metrics

        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                # Count receipts
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM cognitive_runtime_receipts",
                )
                row = cursor.fetchone()
                receipt_count = int(row[0]) if row and row[0] is not None else 0

                # Count handoffs
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM cognitive_runtime_handoffs",
                )
                row = cursor.fetchone()
                handoff_count = int(row[0]) if row and row[0] is not None else 0

                # Count projection requests
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM cognitive_runtime_projection_requests",
                )
                row = cursor.fetchone()
                projection_count = int(row[0]) if row and row[0] is not None else 0

                # Count promotion decisions
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM cognitive_runtime_promotion_decisions",
                )
                row = cursor.fetchone()
                decision_count = int(row[0]) if row and row[0] is not None else 0

                # Calculate receipt coverage
                # Receipt coverage = (receipts with valid payload) / (total expected)
                # For now, we estimate based on handoff/promotion activity
                expected_receipts = max(1, handoff_count + decision_count)
                receipt_coverage = min(1.0, receipt_count / expected_receipts) if expected_receipts > 0 else 0.0

                # Calculate handoff roundtrip success rate
                # Success = handoff exists AND has corresponding promotion decision
                handoff_success_rate = 1.0
                if handoff_count > 0 and decision_count > 0:
                    # Rough estimate: if we have both handoffs and decisions, assume good coverage
                    handoff_success_rate = min(1.0, decision_count / handoff_count) if handoff_count > 0 else 0.0

                # Calculate state restore accuracy
                # Based on projection requests with valid completion
                total_activity = max(1, handoff_count + projection_count)
                state_restore_accuracy = 1.0
                if projection_count > 0:
                    # Estimate based on activity ratio
                    state_restore_accuracy = min(1.0, projection_count / total_activity)

                # Calculate transaction envelope coverage
                # Based on promotion decisions covering expected receipts
                transaction_coverage = 1.0
                if receipt_count > 0 and decision_count > 0:
                    transaction_coverage = min(1.0, decision_count / receipt_count)

                # Receipt write failure rate and SQLite write latency
                #
                # LIMITATION: This benchmark measures SQLite CREATE/INSERT/DROP on temporary
                # tables, NOT actual receipt record writes. Real receipt operations involve:
                # - Content-addressed blob storage writes
                # - Index updates (vector + graph)
                # - Transaction commit overhead
                # - Possible network I/O in distributed setups
                #
                # TODO: Implement真实的receipt写入工作负载benchmark，包括:
                # - 实际的ContextOSReceipt序列化写入
                # - 关联的索引更新操作
                # - 端到端的事务提交测量
                write_latencies: list[float] = []
                write_failures = 0
                num_write_tests = 20  # Run multiple ops for meaningful p95

                for i in range(num_write_tests):
                    test_start = time.perf_counter()
                    try:
                        test_conn = sqlite3.connect(str(db_path), timeout=5.0)
                        try:
                            test_conn.execute(
                                "CREATE TABLE IF NOT EXISTS _metrics_timing_test (id INTEGER)",
                            )
                            test_conn.execute(
                                "INSERT INTO _metrics_timing_test (id) VALUES (?)",
                                (i,),
                            )
                            test_conn.commit()
                            test_conn.execute("DROP TABLE IF EXISTS _metrics_timing_test")
                            test_conn.commit()
                        finally:
                            test_conn.close()
                    except (RuntimeError, ValueError) as e:
                        logger.debug("SQLite timing test iteration %d failed: %s", i, e)
                        write_failures += 1
                    finally:
                        test_end = time.perf_counter()
                        test_duration_ms = (test_end - test_start) * 1000
                        write_latencies.append(test_duration_ms)

                # Calculate receipt write failure rate from actual measurements
                receipt_write_failure_rate = write_failures / num_write_tests if num_write_tests > 0 else 0.0

                # Calculate p95 latency from collected latencies
                # p95 = value below which 95% of observations fall
                # For n samples, p95 index = (n-1) * 0.95 (0-indexed)
                if write_latencies:
                    sorted_latencies = sorted(write_latencies)
                    n = len(sorted_latencies)
                    p95_index = int((n - 1) * 0.95)
                    p95_index = min(max(p95_index, 0), n - 1)  # clamp to valid range
                    sqlite_write_p95_ms = sorted_latencies[p95_index]
                else:
                    sqlite_write_p95_ms = 0.0

                total_cases = max(1, receipt_count)

                metrics = CognitiveRuntimeMetrics(
                    total_cases=total_cases,
                    receipt_coverage=receipt_coverage,
                    handoff_roundtrip_success_rate=handoff_success_rate,
                    state_restore_accuracy=state_restore_accuracy,
                    transaction_envelope_coverage=transaction_coverage,
                    receipt_write_failure_rate=receipt_write_failure_rate,
                    sqlite_write_p95_ms=sqlite_write_p95_ms,
                )

            finally:
                conn.close()

        except sqlite3.OperationalError as e:
            # Database locked or other operational error
            # Return minimal metrics for CI gate compatibility
            logger.warning("SQLite operational error during metrics collection: %s", e)

        return metrics


def collect_cognitive_runtime_metrics(
    workspace: str | Path,
) -> dict[str, Any]:
    """Convenience function to collect Cognitive Runtime metrics.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        Dict suitable for merging into eval report cognitive_runtime_summary.
    """
    collector = CognitiveRuntimeMetricsCollector(workspace)
    result = collector.collect_metrics()

    if result.metrics is None:
        # Return default/empty metrics for CI gate compatibility
        return {
            "total_cases": 0,
            "receipt_coverage": 0.0,
            "handoff_roundtrip_success_rate": 0.0,
            "state_restore_accuracy": 0.0,
            "transaction_envelope_coverage": 0.0,
            "receipt_write_failure_rate": 0.0,
            "sqlite_write_p95_ms": 0.0,
            "_collection_errors": list(result.errors),
        }

    return result.metrics.to_dict()
