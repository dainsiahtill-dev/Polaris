"""Metrics primitives for KernelOne telemetry/ subsystem.

Provides counter, gauge, histogram, and timer instruments that are
independent of any specific metrics backend (Prometheus, StatsD, etc.).

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Metric Instruments
# -----------------------------------------------------------------------------


@dataclass
class Counter:
    """Monotonically increasing integer counter.

    Usage::

        http_requests = Counter("kernelone_http_requests_total", labels={"method": "GET"})
        http_requests.inc()           # +1
        http_requests.inc(amount=5)   # +5
    """

    name: str
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    _value: int = field(default=0, init=False)

    def inc(self, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError(f"Counter.inc amount must be non-negative, got {amount}")
        self._value += amount

    def get(self) -> int:
        return self._value

    def reset(self) -> None:
        self._value = 0


@dataclass
class Gauge:
    """Value that can go up or down.

    Usage::

        active_sessions = Gauge("kernelone_active_sessions")
        active_sessions.set(10)
        active_sessions.inc()    # +1
        active_sessions.dec(2)  # -2
    """

    name: str
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    _value: float = field(default=0.0, init=False)

    def set(self, value: float) -> None:
        self._value = float(value)

    def inc(self, amount: float = 1.0) -> None:
        self._value += float(amount)

    def dec(self, amount: float = 1.0) -> None:
        self._value -= float(amount)

    def get(self) -> float:
        return self._value


@dataclass
class Histogram:
    """Distribution of values over time.

    Usage::

        http_duration = Histogram(
            "kernelone_http_request_duration_seconds",
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        )
        http_duration.observe(0.123)
    """

    name: str
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    _values: list[float] = field(default_factory=list, init=False)

    def observe(self, value: float) -> None:
        if value < 0:
            raise ValueError(f"Histogram.observe value must be non-negative, got {value}")
        self._values.append(float(value))

    def get(self) -> dict[str, Any]:
        if not self._values:
            return {"count": 0, "sum": 0.0, "buckets": dict.fromkeys(self.buckets, 0)}
        total = len(self._values)
        s = sum(self._values)
        cumulative = 0
        bucket_counts: dict[str, int] = {}
        for b in self.buckets:
            cumulative += sum(1 for v in self._values if v <= b)
            bucket_counts[f"le_{b}"] = cumulative
        return {"count": total, "sum": s, "buckets": bucket_counts}

    def reset(self) -> None:
        self._values.clear()


@dataclass
class Timer:
    """Wall-clock timer for measuring elapsed time.

    Usage::

        with Timer() as timer:
            await do_work()
        elapsed_ms = timer.elapsed_ms

        # Or with a target threshold:
        timer = Timer(threshold_seconds=5.0)
        with timer:
            await long_work()
        if timer.exceeded:
            logger.warning("Operation exceeded threshold")
    """

    name: str
    threshold_seconds: float | None = None
    _start: float | None = field(default=None, init=False)
    _stop: float | None = field(default=None, init=False)

    def __enter__(self) -> Timer:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._stop = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self._start is None:
            return 0.0
        stop = self._stop if self._stop is not None else time.monotonic()
        return stop - self._start

    @property
    def elapsed_ms(self) -> int:
        return int(self.elapsed_seconds * 1000)

    @property
    def exceeded(self) -> bool:
        if self.threshold_seconds is None:
            return False
        return self.elapsed_seconds > self.threshold_seconds


# -----------------------------------------------------------------------------
# MetricsRecorder
# ----------------------------------------------------------------------------_


class MetricsRecorder:
    """Central metrics registry for KernelOne.

    Provides a singleton-style registry of named instruments and
    exposes a /metrics endpoint payload compatible with Prometheus.

    Usage::

        recorder = MetricsRecorder()

        # Define instruments
        recorder.define_counter("kernelone_http_requests_total", labels={"method", "path"})
        recorder.define_histogram("kernelone_http_request_duration_seconds")

        # Use
        recorder.get_counter("kernelone_http_requests_total", {"method": "GET"}).inc()

        # Export Prometheus format
        payload = recorder.export_prometheus()
    """

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------------------
    # Definition
    # -------------------------------------------------------------------------

    def define_counter(
        self,
        name: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> None:
        key = self._make_key(name, dict.fromkeys(labels or [], ""))
        self._counters[key] = Counter(
            name=name,
            description=description,
            labels=dict.fromkeys(labels or [], ""),
        )

    def define_gauge(
        self,
        name: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> None:
        key = self._make_key(name, dict.fromkeys(labels or [], ""))
        self._gauges[key] = Gauge(
            name=name,
            description=description,
            labels=dict.fromkeys(labels or [], ""),
        )

    def define_histogram(
        self,
        name: str,
        description: str = "",
        labels: list[str] | None = None,
        buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    ) -> None:
        key = self._make_key(name, dict.fromkeys(labels or [], ""))
        self._histograms[key] = Histogram(
            name=name,
            description=description,
            labels=dict.fromkeys(labels or [], ""),
            buckets=buckets,
        )

    # -------------------------------------------------------------------------
    # Accessors
    # -------------------------------------------------------------------------

    def get_counter(
        self,
        name: str,
        label_values: dict[str, str] | None = None,
    ) -> Counter:
        key = self._make_key(name, label_values)
        counter = self._counters.get(key)
        if counter is not None:
            return counter
        # Auto-register on first use
        counter = Counter(name=name, labels=dict(label_values) if label_values else {})
        self._counters[key] = counter
        return counter

    def get_gauge(
        self,
        name: str,
        label_values: dict[str, str] | None = None,
    ) -> Gauge:
        key = self._make_key(name, label_values)
        gauge = self._gauges.get(key)
        if gauge is not None:
            return gauge
        gauge = Gauge(name=name, labels=dict(label_values) if label_values else {})
        self._gauges[key] = gauge
        return gauge

    def get_histogram(
        self,
        name: str,
        label_values: dict[str, str] | None = None,
    ) -> Histogram:
        key = self._make_key(name, label_values)
        hist = self._histograms.get(key)
        if hist is not None:
            return hist
        hist = Histogram(name=name, labels=dict(label_values) if label_values else {})
        self._histograms[key] = hist
        return hist

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines: list[str] = []
        for counter in self._counters.values():
            labels = self._format_labels(counter.labels)
            lines.append(f"# TYPE {counter.name} counter")
            if counter.description:
                lines.append(f"# HELP {counter.name} {counter.description}")
            lines.append(f"{counter.name}{labels} {counter.get()}")
        for gauge in self._gauges.values():
            labels = self._format_labels(gauge.labels)
            lines.append(f"# TYPE {gauge.name} gauge")
            if gauge.description:
                lines.append(f"# HELP {gauge.name} {gauge.description}")
            lines.append(f"{gauge.name}{labels} {gauge.get()}")
        for hist in self._histograms.values():
            labels = self._format_labels(hist.labels)
            lines.append(f"# TYPE {hist.name} histogram")
            if hist.description:
                lines.append(f"# HELP {hist.name} {hist.description}")
            bucket_data = hist.get()
            for b, count in bucket_data["buckets"].items():
                b_labels = dict(hist.labels, le=str(b))
                lines.append(f"{hist.name}_bucket{self._format_labels(b_labels)} {count}")
            lines.append(f"{hist.name}_sum{labels} {bucket_data['sum']}")
            lines.append(f"{hist.name}_count{labels} {bucket_data['count']}")
        return "\n".join(lines) + "\n"

    def export_dict(self) -> dict[str, Any]:
        """Export all metrics as a plain dict."""
        return {
            "timestamp": _utc_now().isoformat(),
            "counters": {c.name: c.get() for c in self._counters.values()},
            "gauges": {g.name: g.get() for g in self._gauges.values()},
            "histograms": {h.name: h.get() for h in self._histograms.values()},
        }

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _make_key(
        self,
        name: str,
        label_values: dict[str, str] | None,
    ) -> str:
        if not label_values:
            return name
        pairs = ",".join(f"{k}={v}" for k, v in sorted(label_values.items()))
        return f"{name}{{{pairs}}}"

    def _format_labels(self, labels: dict[str, str]) -> str:
        if not labels:
            return ""
        pairs = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{{{pairs}}}"


# Global default recorder
_default_recorder: MetricsRecorder | None = None


def get_recorder() -> MetricsRecorder:
    """Get the default global MetricsRecorder instance."""
    global _default_recorder
    if _default_recorder is None:
        _default_recorder = MetricsRecorder()
    return _default_recorder


# -----------------------------------------------------------------------------
# Business Metrics (PR-11)
# -----------------------------------------------------------------------------


@dataclass
class BusinessMetrics:
    """Business metrics for Polaris operations.

    These metrics track end-to-end business outcomes rather than
    technical infrastructure metrics.
    """

    # Agent E2E success rate (percentage)
    agent_e2e_success_rate: float = 0.0

    # Cognitive evolution drift rate per week (percentage)
    cognitive_drift_rate_per_week: float = 0.0

    # Context projection latency p95 (milliseconds)
    context_projection_latency_p95_ms: float = 0.0

    # Fallback success rate (percentage)
    fallback_success_rate: float = 0.0

    # HITL timeout rate (percentage)
    hitl_timeout_rate: float = 0.0

    # Semantic boundary violation rate (percentage)
    semantic_boundary_violation_rate: float = 0.0


# Metric name constants
METRIC_AGENT_E2E_SUCCESS = "agent_e2e_success_rate"
METRIC_COGNITIVE_DRIFT = "cognitive_drift_rate_per_week"
METRIC_CONTEXT_LATENCY_P95 = "context_projection_latency_p95_ms"
METRIC_FALLBACK_SUCCESS = "fallback_success_rate"
METRIC_HITL_TIMEOUT = "hitl_timeout_rate"
METRIC_SEMANTIC_BOUNDARY = "semantic_boundary_violation_rate"

# Global metrics storage for business metrics
_business_metrics = BusinessMetrics()


def record_metric(metric_name: str, value: float, tags: dict[str, str] | None = None) -> None:
    """Record a business metric value.

    Args:
        metric_name: Name of the metric (use METRIC_* constants).
        value: Metric value to record.
        tags: Optional tags for metric categorization.

    Example::

        record_metric(METRIC_CONTEXT_LATENCY_P95, 125.5, {"role": "director"})
    """
    # Update the global BusinessMetrics instance
    if metric_name == METRIC_AGENT_E2E_SUCCESS:
        _business_metrics.agent_e2e_success_rate = value
    elif metric_name == METRIC_COGNITIVE_DRIFT:
        _business_metrics.cognitive_drift_rate_per_week = value
    elif metric_name == METRIC_CONTEXT_LATENCY_P95:
        _business_metrics.context_projection_latency_p95_ms = value
    elif metric_name == METRIC_FALLBACK_SUCCESS:
        _business_metrics.fallback_success_rate = value
    elif metric_name == METRIC_HITL_TIMEOUT:
        _business_metrics.hitl_timeout_rate = value
    elif metric_name == METRIC_SEMANTIC_BOUNDARY:
        _business_metrics.semantic_boundary_violation_rate = value
    else:
        logger.warning("Unknown metric_name: %s", metric_name)

    # Also record in the MetricsRecorder for export
    recorder = get_recorder()
    if tags is None:
        tags = {}
    recorder.get_histogram(metric_name, tags).observe(value)


def get_business_metrics() -> BusinessMetrics:
    """Get the current business metrics snapshot."""
    return _business_metrics
