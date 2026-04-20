"""Tests for metrics collector."""

from __future__ import annotations

from polaris.kernelone.observability.metrics import (
    MetricsCollector,
    MetricType,
)


class TestMetricsCollector:
    """Test cases for MetricsCollector."""

    def test_init(self) -> None:
        """Test metrics collector initialization."""
        collector = MetricsCollector("test-service")
        assert collector._service_name == "test-service"

    def test_inc_counter(self) -> None:
        """Test incrementing a counter."""
        collector = MetricsCollector("test-service")
        collector.inc_counter("requests")
        collector.inc_counter("requests")
        collector.inc_counter("requests")
        points = collector.get_metrics()
        counter_points = [p for p in points if p.name == "requests"]
        assert len(counter_points) == 3
        assert counter_points[-1].value == 3.0

    def test_inc_counter_with_labels(self) -> None:
        """Test incrementing a counter with labels."""
        collector = MetricsCollector("test-service")
        collector.inc_counter("requests", labels={"method": "GET"})
        collector.inc_counter("requests", labels={"method": "POST"})
        points = collector.get_metrics()
        assert len(points) == 2

    def test_set_gauge(self) -> None:
        """Test setting a gauge."""
        collector = MetricsCollector("test-service")
        collector.set_gauge("cpu_usage", 75.5)
        collector.set_gauge("cpu_usage", 80.0)
        points = collector.get_metrics()
        gauge_points = [p for p in points if p.name == "cpu_usage"]
        assert len(gauge_points) == 2
        assert gauge_points[-1].value == 80.0

    def test_set_gauge_with_labels(self) -> None:
        """Test setting a gauge with labels."""
        collector = MetricsCollector("test-service")
        collector.set_gauge("memory_usage", 1024.0, labels={"host": "server1"})
        points = collector.get_metrics()
        assert len(points) == 1
        assert points[0].labels == {"host": "server1"}

    def test_observe_histogram(self) -> None:
        """Test observing histogram values."""
        collector = MetricsCollector("test-service")
        collector.observe_histogram("request_duration", 0.1)
        collector.observe_histogram("request_duration", 0.2)
        collector.observe_histogram("request_duration", 0.3)
        points = collector.get_metrics()
        histogram_points = [p for p in points if p.name == "request_duration"]
        assert len(histogram_points) == 3

    def test_observe_histogram_with_labels(self) -> None:
        """Test observing histogram values with labels."""
        collector = MetricsCollector("test-service")
        collector.observe_histogram("request_duration", 0.1, labels={"endpoint": "/api"})
        points = collector.get_metrics()
        assert len(points) == 1
        assert points[0].labels == {"endpoint": "/api"}

    def test_get_metrics(self) -> None:
        """Test getting all collected metrics."""
        collector = MetricsCollector("test-service")
        collector.inc_counter("requests")
        collector.set_gauge("cpu", 50.0)
        collector.observe_histogram("latency", 0.05)
        metrics = collector.get_metrics()
        assert len(metrics) == 3

    def test_export_prometheus(self) -> None:
        """Test exporting metrics in Prometheus format."""
        collector = MetricsCollector("test-service")
        collector.inc_counter("requests_total", labels={"method": "GET"})
        collector.set_gauge("cpu_percent", 75.5)
        exporter_output = collector.export_prometheus()
        assert "requests_total" in exporter_output
        assert "cpu_percent" in exporter_output
        assert 'method="GET"' in exporter_output

    def test_metric_point_type(self) -> None:
        """Test MetricPoint has correct type."""
        collector = MetricsCollector("test-service")
        collector.inc_counter("test_counter")
        points = collector.get_metrics()
        assert points[0].metric_type == MetricType.COUNTER
        collector.set_gauge("test_gauge", 1.0)
        gauge_points = [p for p in collector.get_metrics() if p.name == "test_gauge"]
        assert gauge_points[-1].metric_type == MetricType.GAUGE
        collector.observe_histogram("test_histogram", 1.0)
        histogram_points = [p for p in collector.get_metrics() if p.name == "test_histogram"]
        assert histogram_points[-1].metric_type == MetricType.HISTOGRAM
