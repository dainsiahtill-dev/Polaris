"""Business metrics for ``runtime.task_market`` — Prometheus-format counters, latency histograms, and queue depth gauges."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any

# Default histogram buckets (ms) matching the existing MetricsMiddleware pattern.
_DEFAULT_BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, float("inf"))


class TaskMarketMetrics:
    """Thread-safe business metrics for ``runtime.task_market``.

    Metrics exposed in Prometheus text format via ``get_prometheus_metrics()``:

    Counters:
        ``task_market_operations_total{operation,stage,ok}``
        ``task_market_outbox_relay_sent_total``
        ``task_market_outbox_relay_failed_total``

    Histograms:
        ``task_market_operation_duration_ms_bucket{operation,le}``

    Gauges:
        ``task_market_queue_depth{stage}``
        ``task_market_consumer_poll_total{role}``

    Enabled via ``POLARIS_TASK_MARKET_METRICS_ENABLED`` (default: ``true``).
    """

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is not None:
            self._enabled = enabled
        else:
            raw = str(os.environ.get("POLARIS_TASK_MARKET_METRICS_ENABLED", "true") or "true").strip().lower()
            self._enabled = raw not in ("false", "0", "no", "off")

        # Counter: (operation, stage, ok) -> count
        self._operation_counts: dict[tuple[str, str, str], int] = {}
        # Latency: operation -> list of durations (ms)
        self._latencies: dict[str, list[float]] = defaultdict(list)
        # Gauge: stage -> count
        self._queue_depths: dict[str, int] = {}
        # Outbox relay counters
        self._outbox_sent: int = 0
        self._outbox_failed: int = 0
        # Consumer poll counters: role -> count
        self._consumer_poll_counts: dict[str, int] = {}
        # Consumer poll latency: role -> list of durations (ms)
        self._consumer_poll_latencies: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---- Recording ----------------------------------------------------------

    def record_operation(
        self,
        operation: str,
        duration_ms: float,
        *,
        stage: str = "",
        ok: bool = True,
    ) -> None:
        """Record an operation invocation with duration."""
        if not self._enabled:
            return
        key = (operation, stage, str(ok).lower())
        with self._lock:
            self._operation_counts[key] = self._operation_counts.get(key, 0) + 1
            self._latencies[operation].append(max(0.0, float(duration_ms)))

    def record_outbox_relay(self, sent: int, failed: int) -> None:
        """Record outbox relay batch results."""
        if not self._enabled:
            return
        with self._lock:
            self._outbox_sent += max(0, int(sent))
            self._outbox_failed += max(0, int(failed))

    def set_queue_depth(self, stage: str, count: int) -> None:
        """Set the current queue depth for a stage."""
        if not self._enabled:
            return
        with self._lock:
            self._queue_depths[stage] = max(0, int(count))

    def record_consumer_poll(
        self,
        role: str,
        duration_ms: float,
    ) -> None:
        """Record a consumer poll cycle."""
        if not self._enabled:
            return
        with self._lock:
            self._consumer_poll_counts[role] = self._consumer_poll_counts.get(role, 0) + 1
            self._consumer_poll_latencies[role].append(max(0.0, float(duration_ms)))

    # ---- Prometheus Export --------------------------------------------------

    def get_prometheus_metrics(self) -> str:
        """Return all task_market metrics in Prometheus text exposition format."""
        if not self._enabled:
            return ""
        with self._lock:
            lines: list[str] = []

            # -- Counters: task_market_operations_total
            lines.append("# HELP task_market_operations_total Total task market operations")
            lines.append("# TYPE task_market_operations_total counter")
            for (op, stage, ok_val), count in sorted(self._operation_counts.items()):
                lines.append(f'task_market_operations_total{{operation="{op}",stage="{stage}",ok="{ok_val}"}} {count}')

            # -- Histograms: task_market_operation_duration_ms
            lines.append("")
            lines.append("# HELP task_market_operation_duration_ms Operation duration in milliseconds")
            lines.append("# TYPE task_market_operation_duration_ms histogram")
            for op in sorted(self._latencies.keys()):
                durations = self._latencies[op]
                count = len(durations)
                total = sum(durations)
                for bound in _DEFAULT_BUCKETS:
                    bucket_count = sum(1 for d in durations if d <= bound)
                    le_val = "+Inf" if bound == float("inf") else str(int(bound))
                    lines.append(
                        f'task_market_operation_duration_ms_bucket{{operation="{op}",le="{le_val}"}} {bucket_count}'
                    )
                lines.append(f'task_market_operation_duration_ms_count{{operation="{op}"}} {count}')
                if count > 0:
                    lines.append(f'task_market_operation_duration_ms_sum{{operation="{op}"}} {total:.3f}')

            # -- Counters: outbox relay
            lines.append("")
            lines.append("# HELP task_market_outbox_relay_sent_total Total outbox messages sent")
            lines.append("# TYPE task_market_outbox_relay_sent_total counter")
            lines.append(f"task_market_outbox_relay_sent_total {self._outbox_sent}")
            lines.append("# HELP task_market_outbox_relay_failed_total Total outbox messages failed")
            lines.append("# TYPE task_market_outbox_relay_failed_total counter")
            lines.append(f"task_market_outbox_relay_failed_total {self._outbox_failed}")

            # -- Gauges: queue depth
            lines.append("")
            lines.append("# HELP task_market_queue_depth Current queue depth by stage")
            lines.append("# TYPE task_market_queue_depth gauge")
            for stage in sorted(self._queue_depths.keys()):
                lines.append(f'task_market_queue_depth{{stage="{stage}"}} {self._queue_depths[stage]}')

            # -- Counters: consumer poll
            lines.append("")
            lines.append("# HELP task_market_consumer_poll_total Total consumer poll cycles")
            lines.append("# TYPE task_market_consumer_poll_total counter")
            for role in sorted(self._consumer_poll_counts.keys()):
                lines.append(f'task_market_consumer_poll_total{{role="{role}"}} {self._consumer_poll_counts[role]}')

            # -- Histograms: consumer poll latency
            lines.append("")
            lines.append("# HELP task_market_consumer_poll_duration_ms Consumer poll duration in milliseconds")
            lines.append("# TYPE task_market_consumer_poll_duration_ms histogram")
            for role in sorted(self._consumer_poll_latencies.keys()):
                durations = self._consumer_poll_latencies[role]
                count = len(durations)
                total = sum(durations)
                for bound in _DEFAULT_BUCKETS:
                    bucket_count = sum(1 for d in durations if d <= bound)
                    le_val = "+Inf" if bound == float("inf") else str(int(bound))
                    lines.append(
                        f'task_market_consumer_poll_duration_ms_bucket{{role="{role}",le="{le_val}"}} {bucket_count}'
                    )
                lines.append(f'task_market_consumer_poll_duration_ms_count{{role="{role}"}} {count}')
                if count > 0:
                    lines.append(f'task_market_consumer_poll_duration_ms_sum{{role="{role}"}} {total:.3f}')

            return "\n".join(lines) + "\n"

    # ---- Reset (for testing) -----------------------------------------------

    def reset(self) -> None:
        """Reset all metrics for test isolation."""
        with self._lock:
            self._operation_counts.clear()
            self._latencies.clear()
            self._queue_depths.clear()
            self._outbox_sent = 0
            self._outbox_failed = 0
            self._consumer_poll_counts.clear()
            self._consumer_poll_latencies.clear()

    # ---- Convenience helpers ------------------------------------------------

    def time_operation(self, operation: str, *, stage: str = ""):
        """Context manager / decorator helper that times an operation.

        Usage::

            metrics = TaskMarketMetrics.get_instance()
            with metrics.time_operation("publish", stage=cmd.stage) as timer:
                ...  # do work
            # timer.duration_ms is available after the block
        """
        return _OperationTimer(self, operation, stage=stage)


class _OperationTimer:
    """Context manager for timing a task_market operation."""

    __slots__ = ("_metrics", "_operation", "_stage", "_t0", "duration_ms")

    def __init__(self, metrics: TaskMarketMetrics, operation: str, *, stage: str = "") -> None:
        self._metrics = metrics
        self._operation = operation
        self._stage = stage
        self.duration_ms: float = 0.0

    def __enter__(self) -> _OperationTimer:
        self._t0 = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        self.duration_ms = (time.monotonic() - self._t0) * 1000.0
        ok = exc_type is None
        self._metrics.record_operation(
            self._operation,
            self.duration_ms,
            stage=self._stage,
            ok=ok,
        )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics_singleton: TaskMarketMetrics | None = None


def get_task_market_metrics() -> TaskMarketMetrics:
    """Return the global TaskMarketMetrics singleton."""
    global _metrics_singleton
    if _metrics_singleton is not None:
        return _metrics_singleton
    with _metrics_lock:
        if _metrics_singleton is None:
            _metrics_singleton = TaskMarketMetrics()
        return _metrics_singleton


def reset_task_market_metrics_for_testing() -> TaskMarketMetrics:
    """Create a fresh singleton for test isolation."""
    global _metrics_singleton
    with _metrics_lock:
        _metrics_singleton = TaskMarketMetrics(enabled=True)
    return _metrics_singleton


__all__ = [
    "TaskMarketMetrics",
    "get_task_market_metrics",
    "reset_task_market_metrics_for_testing",
]
