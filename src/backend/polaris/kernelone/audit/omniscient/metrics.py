"""AuditMetricsCollector — Prometheus-compatible metrics for Omniscient Audit System.

Exposes metrics in Prometheus exposition format:
- audit_events_total{domain, event_type, priority}
- audit_events_latency_seconds{domain}
- audit_buffer_size
- audit_circuit_breaker_state
- audit_storm_level

Reference:
- Prometheus exposition format: https://prometheus.io/docs/instrumenting/exposition_formats/
- CloudEvents schema: https://cloudevents.io/

Usage:
    from polaris.kernelone.audit.omniscient.metrics import get_metrics_collector

    collector = get_metrics_collector()
    collector.record_event(domain="llm", event_type="llm_call", priority="info", latency_ms=150.0)
    print(collector.get_prometheus_format())
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.schemas.base import AuditEvent


class CircuitBreakerState(IntEnum):
    """Circuit breaker states for audit bus."""

    CLOSED = 0  # Normal operation
    OPEN = 1  # Failing fast
    HALF_OPEN = 2  # Testing recovery


class StormLevel(str, Enum):
    """Audit storm detection levels.

    [P1-AUDIT-002] Unified with storm_detector.py and high_availability.py.
    Uses string values for consistency with other audit components.
    """

    NORMAL = "normal"  # Normal event rate
    ELEVATED = "elevated"  # Elevated but manageable
    WARNING = "warning"  # High event rate
    CRITICAL = "critical"  # Storm in progress
    EMERGENCY = "emergency"  # Extreme rate, drop all non-error

    def __repr__(self) -> str:  # type: ignore[override]
        return f"StormLevel.{self.name}"


# Standard Prometheus histogram buckets for latency (seconds)
LATENCY_BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]


@dataclass
class HistogramBucket:
    """Single histogram bucket for latency tracking."""

    upper_bound: float
    count: int = 0


@dataclass
class EventMetrics:
    """Metrics for a specific domain/event_type/priority combination."""

    counter: int = 0
    total_latency_ms: float = 0.0
    latency_buckets: list[HistogramBucket] = field(
        default_factory=lambda: [HistogramBucket(b) for b in LATENCY_BUCKETS] + [HistogramBucket(float("inf"))]
    )


class AuditMetricsCollector:
    """Thread-safe metrics collector for Omniscient Audit System.

    Collects and exposes Prometheus-compatible metrics for audit events.

    Attributes:
        BUCKETS: Standard histogram bucket boundaries for latency.
    """

    BUCKETS: list[float] = LATENCY_BUCKETS

    def __init__(self) -> None:
        """Initialize the metrics collector."""
        self._events: dict[str, EventMetrics] = defaultdict(EventMetrics)
        self._buffer_size: int = 0
        self._circuit_breaker_state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._storm_level: StormLevel = StormLevel.NORMAL
        self._lock = RLock()
        self._start_time = time.time()

    def record_event(
        self,
        domain: str,
        event_type: str,
        priority: str,
        latency_ms: float = 0.0,
    ) -> None:
        """Record an audit event.

        Args:
            domain: Event domain (llm, tool, dialogue, context, task, system).
            event_type: Specific event type within domain.
            priority: Event priority (critical, error, warning, info, debug).
            latency_ms: Event processing latency in milliseconds.
        """
        key = self._make_key(domain, event_type, priority)
        with self._lock:
            metric = self._events[key]
            metric.counter += 1
            metric.total_latency_ms += latency_ms

            # Update histogram buckets
            latency_sec = latency_ms / 1000.0
            for bucket in metric.latency_buckets:
                if latency_sec <= bucket.upper_bound:
                    bucket.count += 1

    def record_event_from_audit(self, event: AuditEvent, latency_ms: float = 0.0) -> None:
        """Record an audit event from an AuditEvent instance.

        Args:
            event: The AuditEvent instance to record.
            latency_ms: Event processing latency in milliseconds.
        """
        domain = event.domain.value if hasattr(event.domain, "value") else str(event.domain)
        event_type = event.event_type
        # Convert priority to string safely
        priority_val = event.priority
        if hasattr(priority_val, "value"):
            priority = str(priority_val.value)
        elif isinstance(priority_val, int):
            priority = str(priority_val)
        else:
            priority = str(priority_val)
        self.record_event(domain=domain, event_type=event_type, priority=priority, latency_ms=latency_ms)

    def set_buffer_size(self, size: int) -> None:
        """Set the current audit buffer size.

        Args:
            size: Number of events in buffer.
        """
        with self._lock:
            self._buffer_size = size

    def set_circuit_breaker_state(self, state: CircuitBreakerState) -> None:
        """Set the circuit breaker state.

        Args:
            state: Current circuit breaker state.
        """
        with self._lock:
            self._circuit_breaker_state = state

    def set_storm_level(self, level: StormLevel) -> None:
        """Set the audit storm detection level.

        Args:
            level: Current storm level.
        """
        with self._lock:
            self._storm_level = level

    def _make_key(self, domain: str, event_type: str, priority: str) -> str:
        """Create a unique key for metric labeling.

        Args:
            domain: Event domain.
            event_type: Event type.
            priority: Event priority.

        Returns:
            Composite key string.
        """
        return f"{domain}:{event_type}:{priority}"

    def _parse_key(self, key: str) -> tuple[str, str, str]:
        """Parse a composite key into its components.

        Args:
            key: Composite key string.

        Returns:
            Tuple of (domain, event_type, priority).
        """
        parts = key.split(":")
        domain = parts[0] if len(parts) > 0 else ""
        event_type = parts[1] if len(parts) > 1 else ""
        priority = parts[2] if len(parts) > 2 else ""
        return domain, event_type, priority

    def get_prometheus_format(self) -> str:
        """Export metrics in Prometheus exposition format.

        Returns:
            Metrics formatted as Prometheus text exposition.
        """
        lines: list[str] = []

        # Header
        lines.append("# HELP audit_events_total Total audit events by domain, type, and priority")
        lines.append("# TYPE audit_events_total counter")

        with self._lock:
            for key, metric in self._events.items():
                domain, event_type, priority = self._parse_key(key)
                lines.append(
                    f'audit_events_total{{domain="{domain}",event_type="{event_type}",priority="{priority}"}} {metric.counter}'
                )

            # Latency histogram
            lines.append("")
            lines.append("# HELP audit_events_latency_seconds Audit event processing latency in seconds")
            lines.append("# TYPE audit_events_latency_seconds histogram")

            # Track which domains we've output for _sum/_count
            domain_latencies: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

            for key, metric in self._events.items():
                domain, event_type, priority = self._parse_key(key)
                domain_latencies[domain] = (
                    domain_latencies[domain][0] + metric.total_latency_ms / 1000.0,
                    domain_latencies[domain][1] + metric.counter,
                )

                for bucket in metric.latency_buckets:
                    bound = bucket.upper_bound
                    bound_label = "+Inf" if bound == float("inf") else str(bound)
                    lines.append(
                        f'audit_events_latency_seconds_bucket{{domain="{domain}",event_type="{event_type}",priority="{priority}",le="{bound_label}"}} {bucket.count}'
                    )
                lines.append(
                    f'audit_events_latency_seconds_count{{domain="{domain}",event_type="{event_type}",priority="{priority}"}} {metric.counter}'
                )
                if metric.counter > 0:
                    lines.append(
                        f'audit_events_latency_seconds_sum{{domain="{domain}",event_type="{event_type}",priority="{priority}"}} {metric.total_latency_ms / 1000.0}'
                    )

            # Domain-level latency summary
            lines.append("")
            lines.append("# HELP audit_events_latency_by_domain_seconds Latency aggregated by domain")
            lines.append("# TYPE audit_events_latency_by_domain_seconds histogram")
            for domain, (total_sec, count) in domain_latencies.items():
                if count > 0:
                    lines.append(f'audit_events_latency_by_domain_seconds{{domain="{domain}"}}_sum {total_sec}')
                    lines.append(f'audit_events_latency_by_domain_seconds{{domain="{domain}"}}_count {count}')

            # Buffer size gauge
            lines.append("")
            lines.append("# HELP audit_buffer_size Current audit event buffer size")
            lines.append("# TYPE audit_buffer_size gauge")
            lines.append(f"audit_buffer_size {self._buffer_size}")

            # Circuit breaker state gauge
            lines.append("")
            lines.append("# HELP audit_circuit_breaker_state Circuit breaker state (0=closed, 1=open, 2=half_open)")
            lines.append("# TYPE audit_circuit_breaker_state gauge")
            lines.append(f"audit_circuit_breaker_state {self._circuit_breaker_state.value}")

            # Storm level gauge
            lines.append("")
            lines.append(
                "# HELP audit_storm_level Audit storm detection level (0=normal, 1=elevated, 2=high, 3=critical)"
            )
            lines.append("# TYPE audit_storm_level gauge")
            lines.append(f"audit_storm_level {self._storm_level.value}")

            # Uptime
            lines.append("")
            lines.append("# HELP audit_uptime_seconds Process uptime in seconds")
            lines.append("# TYPE audit_uptime_seconds gauge")
            uptime = time.time() - self._start_time
            lines.append(f"audit_uptime_seconds {uptime}")

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._events.clear()
            self._buffer_size = 0
            self._circuit_breaker_state = CircuitBreakerState.CLOSED
            self._storm_level = StormLevel.NORMAL
            self._start_time = time.time()

    def get_summary(self) -> dict[str, int]:
        """Get a summary of total events by domain.

        Returns:
            Dictionary mapping domain to total event count.
        """
        summary: dict[str, int] = defaultdict(int)
        with self._lock:
            for key, metric in self._events.items():
                domain, _, _ = self._parse_key(key)
                summary[domain] += metric.counter
        return dict(summary)


__all__ = [
    "AuditMetricsCollector",
    "CircuitBreakerState",
    "StormLevel",
    "get_metrics_collector",
    "get_unified_prometheus_metrics",
]


# =============================================================================
# Convergence: Unified Prometheus metrics (AuditMetricsCollector + MetricsRecorder)
# =============================================================================


def get_unified_prometheus_metrics() -> str:
    """Get unified Prometheus metrics from both AuditMetricsCollector and MetricsRecorder.

    This function converges the dual metrics systems:
    - AuditMetricsCollector: audit-specific event counts, latency, storm levels
    - MetricsRecorder: general telemetry (tokens, turns, tool calls)

    Deduplication is performed by tracking seen metric names.

    Returns:
        Combined Prometheus-formatted metrics string.
    """
    from polaris.kernelone.telemetry.metrics import get_recorder

    lines: list[str] = []
    seen_metrics: set[str] = set()

    # 1. AuditMetricsCollector metrics
    collector = get_metrics_collector()
    audit_output = collector.get_prometheus_format()
    for line in audit_output.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append(stripped)
            continue
        # Extract metric name for deduplication
        if stripped.startswith("#"):
            lines.append(stripped)
            continue
        # Parse metric name (everything before { or space)
        space_idx = stripped.find(" ")
        brace_idx = stripped.find("{")
        end_idx = space_idx if space_idx != -1 else (brace_idx if brace_idx != -1 else len(stripped))
        metric_name = stripped[:end_idx]
        if metric_name not in seen_metrics:
            seen_metrics.add(metric_name)
            lines.append(stripped)

    # 2. MetricsRecorder general telemetry
    recorder = get_recorder()
    try:
        recorder_output = recorder.export_prometheus()
    except AttributeError:
        recorder_output = ""

    for line in recorder_output.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append(stripped)
            continue
        if stripped.startswith("#"):
            lines.append(stripped)
            continue
        space_idx = stripped.find(" ")
        brace_idx = stripped.find("{")
        end_idx = space_idx if space_idx != -1 else (brace_idx if brace_idx != -1 else len(stripped))
        metric_name = stripped[:end_idx]
        if metric_name not in seen_metrics:
            seen_metrics.add(metric_name)
            lines.append(stripped)

    return "\n".join(lines) + "\n"


# Global metrics collector instance
_metrics_collector: AuditMetricsCollector | None = None
_metrics_lock = RLock()


def get_metrics_collector() -> AuditMetricsCollector:
    """Get the global metrics collector instance.

    Returns:
        The singleton AuditMetricsCollector instance.
    """
    global _metrics_collector
    with _metrics_lock:
        if _metrics_collector is None:
            _metrics_collector = AuditMetricsCollector()
        return _metrics_collector
