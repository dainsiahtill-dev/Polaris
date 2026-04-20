"""Metrics collection system for Polaris."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from polaris.kernelone.constants import MAX_METRIC_POINTS


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass(frozen=True)
class MetricPoint:
    """A single metric data point."""

    name: str
    value: float
    metric_type: MetricType
    timestamp_ms: float
    labels: dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """Collects and exports metrics."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._points: list[MetricPoint] = []

    def inc_counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        key = self._make_key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value
        point = MetricPoint(
            name=name,
            value=self._counters[key],
            metric_type=MetricType.COUNTER,
            timestamp_ms=time.time() * 1000,
            labels=labels or {},
        )
        self._points.append(point)
        self._prune_points()

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge metric."""
        key = self._make_key(name, labels)
        self._gauges[key] = value
        point = MetricPoint(
            name=name,
            value=value,
            metric_type=MetricType.GAUGE,
            timestamp_ms=time.time() * 1000,
            labels=labels or {},
        )
        self._points.append(point)
        self._prune_points()

    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Observe a histogram value."""
        key = self._make_key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)
        point = MetricPoint(
            name=name,
            value=value,
            metric_type=MetricType.HISTOGRAM,
            timestamp_ms=time.time() * 1000,
            labels=labels or {},
        )
        self._points.append(point)
        self._prune_points()

    def _prune_points(self) -> None:
        """Remove oldest points when limit is exceeded."""
        if len(self._points) > MAX_METRIC_POINTS:
            excess = len(self._points) - MAX_METRIC_POINTS
            self._points = self._points[excess:]

    def get_metrics(self) -> list[MetricPoint]:
        """Get all collected metrics."""
        return list(self._points)

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines: list[str] = []
        for key, value in self._counters.items():
            name, labels = self._parse_key(key)
            labels_str = self._format_labels(labels)
            lines.append(f"{name}{labels_str} {value}")
        for key, value in self._gauges.items():
            name, labels = self._parse_key(key)
            labels_str = self._format_labels(labels)
            lines.append(f"{name}{labels_str} {value}")
        for key, values in self._histograms.items():
            name, labels = self._parse_key(key)
            labels_str = self._format_labels(labels)
            for v in values:
                lines.append(f"{name}{labels_str} {v}")
        return "\n".join(lines)

    def _make_key(self, name: str, labels: dict[str, str] | None) -> str:
        label_str = ",".join(f"{k}={v}" for k, v in sorted((labels or {}).items()))
        return f"{name}{{{label_str}}}"

    def _parse_key(self, key: str) -> tuple[str, dict[str, str]]:
        if "{" in key:
            name = key[: key.index("{")]
            label_str = key[key.index("{") + 1 : key.index("}")]
            labels = {}
            for pair in label_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    labels[k] = v
            return name, labels
        return key, {}

    def _format_labels(self, labels: dict[str, str]) -> str:
        if not labels:
            return ""
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{{{label_str}}}"
