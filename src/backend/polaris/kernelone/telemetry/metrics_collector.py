"""MetricsCollector for unified, thread-safe metrics collection.

Provides decorator-based metric capture (@timed, @counted, @gauge),
Prometheus-compatible export, and aggregated metrics storage.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all text operations use encoding="utf-8"
- Thread-safe: all metrics operations protected by locks
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# -----------------------------------------------------------------------------
# Metric Types
# -----------------------------------------------------------------------------


class MetricType(Enum):
    """Supported metric types for the collector."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


# -----------------------------------------------------------------------------
# Aggregated Metrics Storage
# -----------------------------------------------------------------------------


@dataclass
class AggregatedMetric:
    """Stores aggregated metric data with thread-safe access.

    Attributes:
        name: Metric name.
        metric_type: Type of metric (counter, gauge, histogram, timer).
        labels: Label key-value pairs.
        value: Current metric value.
        min_value: Minimum observed value (for histograms/timers).
        max_value: Maximum observed value (for histograms/timers).
        count: Number of observations.
        sum_value: Sum of all observed values (for histograms/timers).
        sum_squared: Sum of squared values (for stddev calculation).
        last_updated: Timestamp of last update.
    """

    name: str
    metric_type: MetricType
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    min_value: float = float("inf")
    max_value: float = float("-inf")
    count: int = 0
    sum_value: float = 0.0
    sum_squared: float = 0.0
    last_updated: str = field(default_factory=lambda: _utc_now().isoformat())

    def update(self, new_value: float) -> None:
        """Update metric with a new observation.

        Args:
            new_value: The new value to record.
        """
        self.value = new_value
        self.count += 1
        self.sum_value += new_value
        self.sum_squared += new_value * new_value
        self.min_value = min(self.min_value, new_value)
        self.max_value = max(self.max_value, new_value)
        self.last_updated = _utc_now().isoformat()

    def increment(self, amount: float = 1.0) -> None:
        """Increment metric by amount (for counters).

        Args:
            amount: Amount to increment by.
        """
        self.value += amount
        self.count += 1
        self.sum_value += amount
        self.sum_squared += amount * amount
        self.last_updated = _utc_now().isoformat()

    def set_value(self, new_value: float) -> None:
        """Set metric to a specific value (for gauges).

        Args:
            new_value: The value to set.
        """
        self.value = new_value
        self.last_updated = _utc_now().isoformat()

    @property
    def mean(self) -> float:
        """Calculate mean of observed values."""
        if self.count == 0:
            return 0.0
        return self.sum_value / self.count

    @property
    def std_dev(self) -> float:
        """Calculate population standard deviation of observed values.

        Returns 0.0 if count < 2 (need at least 2 samples for stddev).
        """
        if self.count < 2:
            return 0.0
        mean = self.sum_value / self.count
        variance = (self.sum_squared / self.count) - (mean * mean)
        if variance < 0:
            # Numerical precision edge case
            return 0.0
        return variance**0.5

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "type": self.metric_type.value,
            "labels": self.labels,
            "value": self.value,
            "min": self.min_value if self.min_value != float("inf") else None,
            "max": self.max_value if self.max_value != float("-inf") else None,
            "count": self.count,
            "sum": self.sum_value,
            "sum_squared": self.sum_squared,
            "mean": self.mean,
            "std_dev": self.std_dev,
            "last_updated": self.last_updated,
        }


# -----------------------------------------------------------------------------
# MetricsCollector
# -----------------------------------------------------------------------------


class MetricsCollector:
    """Thread-safe unified metrics collector with Prometheus export.

    Provides centralized metrics collection with support for:
    - Counters, Gauges, Histograms, Timers
    - Label-based metric scoping
    - Decorator-based metric capture
    - Prometheus-compatible text format export

    Usage::

        collector = MetricsCollector()

        # Define metrics
        collector.define("http_requests", MetricType.COUNTER, ["method", "path"])
        collector.define("response_latency", MetricType.HISTOGRAM, ["endpoint"])

        # Use decorators
        @collector.timed("operation_duration")
        def my_operation():
            pass

        # Or record manually
        collector.increment("http_requests", {"method": "GET", "path": "/api"})
        collector.observe("response_latency", 0.123, {"endpoint": "/api/users"})

        # Export
        prometheus_output = collector.export_prometheus()
    """

    def __init__(self) -> None:
        self._metrics: dict[str, AggregatedMetric] = {}
        self._definitions: dict[str, tuple[MetricType, list[str]]] = {}
        self._lock = threading.RLock()
        self._async_lock: asyncio.Lock | None = None

    def _get_async_lock(self) -> asyncio.Lock:
        """Get or create async lock (lazy initialization for sync contexts).

        Uses double-checked locking with the existing RLock to ensure
        atomic creation of the asyncio.Lock instance.
        """
        if self._async_lock is None:
            with self._lock:  # Use existing RLock for protection
                if self._async_lock is None:  # Double-check
                    self._async_lock = asyncio.Lock()
        return self._async_lock

    def define(
        self,
        name: str,
        metric_type: MetricType,
        label_names: list[str] | None = None,
    ) -> None:
        """Define a metric with optional labels.

        Args:
            name: Metric name (e.g., "http_requests_total").
            metric_type: Type of metric.
            label_names: List of label names for this metric.
        """
        with self._lock:
            key = self._make_key(name, {})
            self._definitions[key] = (metric_type, label_names or [])
            logger.debug(
                "Defined metric %s of type %s with labels %s",
                name,
                metric_type.value,
                label_names,
            )

    def _make_key(self, name: str, labels: dict[str, str]) -> str:
        """Create a unique key for a metric with labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def _get_or_create_metric(
        self,
        name: str,
        metric_type: MetricType,
        labels: dict[str, str],
    ) -> AggregatedMetric:
        """Get existing or create new metric storage."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = AggregatedMetric(
                    name=name,
                    metric_type=metric_type,
                    labels=dict(labels),
                )
            return self._metrics[key]

    def increment(self, name: str, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        """Increment a counter metric.

        Args:
            name: Metric name.
            labels: Optional label values.
            amount: Amount to increment by.
        """
        labels = labels or {}
        metric = self._get_or_create_metric(name, MetricType.COUNTER, labels)
        with self._lock:
            metric.increment(amount)

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge metric to a specific value.

        Args:
            name: Metric name.
            value: Value to set.
            labels: Optional label values.
        """
        labels = labels or {}
        metric = self._get_or_create_metric(name, MetricType.GAUGE, labels)
        with self._lock:
            metric.set_value(value)

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record an observation for a histogram/timer metric.

        Args:
            name: Metric name.
            value: Observed value.
            labels: Optional label values.
        """
        labels = labels or {}
        metric = self._get_or_create_metric(name, MetricType.HISTOGRAM, labels)
        with self._lock:
            metric.update(value)

    def gauge(self, name: str, labels: dict[str, str] | None = None) -> Callable[[F], F]:
        """Decorator to record gauge value after function execution.

        The function's return value becomes the gauge value.

        Args:
            name: Metric name.
            labels: Optional label values.

        Returns:
            Decorated function.
        """

        def decorator(func: F) -> F:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = func(*args, **kwargs)
                if result is not None:
                    self.set(name, float(result), labels)
                return result

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = await func(*args, **kwargs)
                if result is not None:
                    self.set(name, float(result), labels)
                return result

            if asyncio.iscoroutinefunction(func):
                return async_wrapper  # type: ignore[return-value]
            return sync_wrapper  # type: ignore[return-value]

        return decorator

    def counted(
        self,
        name: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> Callable[[F], F]:
        """Decorator to count function invocations.

        Args:
            name: Metric name. Defaults to function name.
            labels: Optional label values.

        Returns:
            Decorated function.
        """
        metric_name = name

        def decorator(func: F) -> F:
            nonlocal metric_name
            if metric_name is None:
                metric_name = func.__name__

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                self.increment(metric_name, labels)  # type: ignore[arg-type]
                return func(*args, **kwargs)

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                self.increment(metric_name, labels)  # type: ignore[arg-type]
                return await func(*args, **kwargs)

            if asyncio.iscoroutinefunction(func):
                return async_wrapper  # type: ignore[return-value]
            return sync_wrapper  # type: ignore[return-value]

        return decorator

    def timed(
        self,
        name: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> Callable[[F], F]:
        """Decorator to measure function execution time.

        Records execution duration as a histogram observation.

        Args:
            name: Metric name. Defaults to function name.
            labels: Optional label values.

        Returns:
            Decorated function.
        """
        metric_name = name

        def decorator(func: F) -> F:
            nonlocal metric_name
            if metric_name is None:
                metric_name = func.__name__

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    return func(*args, **kwargs)
                finally:
                    duration = time.perf_counter() - start
                    self.observe(metric_name, duration, labels)  # type: ignore[arg-type]

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    return await func(*args, **kwargs)
                finally:
                    duration = time.perf_counter() - start
                    self.observe(metric_name, duration, labels)  # type: ignore[arg-type]

            if asyncio.iscoroutinefunction(func):
                return async_wrapper  # type: ignore[return-value]
            return sync_wrapper  # type: ignore[return-value]

        return decorator

    def get(self, name: str, labels: dict[str, str] | None = None) -> AggregatedMetric | None:
        """Get current state of a metric.

        Args:
            name: Metric name.
            labels: Optional label values.

        Returns:
            AggregatedMetric if found, None otherwise.
        """
        key = self._make_key(name, labels or {})
        with self._lock:
            return self._metrics.get(key)

    def get_all(self) -> dict[str, AggregatedMetric]:
        """Get all registered metrics.

        Returns:
            Dictionary of all metrics.
        """
        with self._lock:
            return dict(self._metrics)

    def reset(self, name: str | None = None, labels: dict[str, str] | None = None) -> None:
        """Reset metrics.

        Args:
            name: Specific metric name to reset. If None, reset all.
            labels: Specific labels to reset. Only used if name is provided.
        """
        with self._lock:
            if name is None:
                self._metrics.clear()
                logger.info("Reset all metrics")
            else:
                key = self._make_key(name, labels or {})
                if key in self._metrics:
                    del self._metrics[key]
                    logger.info("Reset metric %s", name)

    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text format.

        Returns:
            Prometheus-formatted metrics string.
        """
        with self._lock:
            lines: list[str] = []

            for _key, metric in sorted(self._metrics.items()):
                metric_labels = metric.labels
                labels_str = self._format_labels(metric_labels)

                # TYPE comment
                if metric.metric_type == MetricType.COUNTER:
                    lines.append(f"# TYPE {metric.name} counter")
                elif metric.metric_type == MetricType.GAUGE:
                    lines.append(f"# TYPE {metric.name} gauge")
                elif metric.metric_type in (MetricType.HISTOGRAM, MetricType.TIMER):
                    lines.append(f"# TYPE {metric.name} histogram")

                # HELP comment
                lines.append(f"# HELP {metric.name} {metric.metric_type.value}")

                # Value
                if metric.metric_type in (MetricType.HISTOGRAM, MetricType.TIMER):
                    # For histograms, export bucket structure
                    bucket_count = metric.count
                    bucket_labels = dict(metric_labels, le="+Inf")
                    bucket_labels_str = self._format_labels(bucket_labels)
                    lines.append(f"{metric.name}_bucket{bucket_labels_str} {bucket_count}")
                    lines.append(f"{metric.name}_sum{labels_str} {metric.sum_value}")
                    lines.append(f"{metric.name}_count{labels_str} {metric.count}")
                else:
                    lines.append(f"{metric.name}{labels_str} {metric.value}")

            return "\n".join(lines) + "\n"

    def _format_labels(self, labels: dict[str, str]) -> str:
        """Format labels for Prometheus output."""
        if not labels:
            return ""
        label_parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(label_parts) + "}"

    def export_dict(self) -> dict[str, Any]:
        """Export all metrics as a dictionary.

        Returns:
            Dictionary with all metrics data.
        """
        with self._lock:
            return {
                "timestamp": _utc_now().isoformat(),
                "metrics": {k: v.to_dict() for k, v in sorted(self._metrics.items())},
            }

    @property
    def size(self) -> int:
        """Number of registered metrics."""
        with self._lock:
            return len(self._metrics)


# -----------------------------------------------------------------------------
# Global Default Collector
# -----------------------------------------------------------------------------

_default_collector: MetricsCollector | None = None


def get_collector() -> MetricsCollector:
    """Get the default global MetricsCollector instance.

    Returns:
        Global MetricsCollector singleton.
    """
    global _default_collector
    if _default_collector is None:
        _default_collector = MetricsCollector()
    return _default_collector


# -----------------------------------------------------------------------------
# Decorator Shortcuts (using default collector)
# -----------------------------------------------------------------------------


def timed(name: str | None = None, labels: dict[str, str] | None = None) -> Callable[[F], F]:
    """Decorator shortcut using default collector.

    Args:
        name: Metric name.
        labels: Optional label values.

    Returns:
        Decorated function.
    """
    return get_collector().timed(name, labels)


def counted(name: str | None = None, labels: dict[str, str] | None = None) -> Callable[[F], F]:
    """Decorator shortcut using default collector.

    Args:
        name: Metric name.
        labels: Optional label values.

    Returns:
        Decorated function.
    """
    return get_collector().counted(name, labels)


def gauge(name: str, labels: dict[str, str] | None = None) -> Callable[[F], F]:
    """Decorator shortcut using default collector.

    Args:
        name: Metric name.
        labels: Optional label values.

    Returns:
        Decorated function.
    """
    return get_collector().gauge(name, labels)
