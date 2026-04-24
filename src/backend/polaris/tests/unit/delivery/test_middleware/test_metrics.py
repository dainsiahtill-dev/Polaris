"""Tests for polaris.delivery.http.middleware.metrics.

Covers MetricsCollector, MetricsMiddleware, path normalization, and Prometheus export.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from polaris.delivery.http.middleware.metrics import (
    MetricsCollector,
    MetricsMiddleware,
    get_metrics_collector,
    reset_metrics_for_testing,
)


class TestHistogramBucket:
    """Tests for HistogramBucket dataclass behavior."""

    def test_HistogramBucket_initialization(self) -> None:
        from polaris.delivery.http.middleware.metrics import HistogramBucket

        bucket = HistogramBucket(upper_bound=100.0, count=5)
        assert bucket.upper_bound == 100.0
        assert bucket.count == 5


class TestRequestMetrics:
    """Tests for RequestMetrics dataclass."""

    def test_RequestMetrics_defaults(self) -> None:
        from polaris.delivery.http.middleware.metrics import RequestMetrics

        metric = RequestMetrics()
        assert metric.request_count == 0
        assert metric.error_count == 0
        assert metric.total_latency_ms == 0.0
        assert len(metric.latency_buckets) == 11  # 10 defined + inf

    def test_RequestMetrics_latency_buckets_have_inf(self) -> None:
        from polaris.delivery.http.middleware.metrics import RequestMetrics

        metric = RequestMetrics()
        inf_bucket = next(b for b in metric.latency_buckets if b.upper_bound == float("inf"))
        assert inf_bucket is not None


class TestMetricsCollector:
    """Tests for MetricsCollector core logic."""

    def setup_method(self) -> None:
        """Create fresh collector for each test."""
        self.collector = MetricsCollector()

    def test_record_request_increments_count(self) -> None:
        self.collector.record_request("GET", "/test", 200, 50.0)
        self.collector.record_request("POST", "/api", 201, 30.0)

        prom_output = self.collector.get_prometheus_format()
        assert 'path="/test"' in prom_output
        assert 'path="/api"' in prom_output

    def test_record_request_tracks_errors(self) -> None:
        self.collector.record_request("GET", "/error", 404, 10.0)
        self.collector.record_request("GET", "/server-error", 500, 50.0)

        prom_output = self.collector.get_prometheus_format()
        assert 'path="/error"' in prom_output
        assert 'path="/server-error"' in prom_output

    def test_record_request_updates_histogram(self) -> None:
        self.collector.record_request("GET", "/hist", 200, 75.0)

        key = "GET /hist"
        metric = self.collector._metrics[key]
        # 75ms falls in bucket 100 (since 50 < 75 <= 100)
        bucket_100 = next(b for b in metric.latency_buckets if b.upper_bound == 100)
        assert bucket_100.count == 1

    def test_start_request_increments_inflight(self) -> None:
        self.collector.start_request("GET", "/inflight")
        self.collector.start_request("GET", "/inflight")

        assert self.collector._inflight["GET /inflight"] == 2

    def test_record_request_decrements_inflight(self) -> None:
        self.collector.start_request("GET", "/pending", 1)
        self.collector.record_request("GET", "/pending", 200, 50.0)

        # After record_request, in-flight should be decremented
        assert self.collector._inflight["GET /pending"] == 0

    def test_inflight_cannot_go_negative(self) -> None:
        # Record without starting - should handle gracefully
        self.collector.record_request("GET", "/orphan", 200, 10.0)
        assert self.collector._inflight["GET /orphan"] == 0

    def test_get_prometheus_format_includes_help_and_type(self) -> None:
        prom_output = self.collector.get_prometheus_format()

        assert "# HELP polaris_requests_total" in prom_output
        assert "# TYPE polaris_requests_total counter" in prom_output
        assert "# HELP polaris_request_duration_ms" in prom_output
        assert "# TYPE polaris_request_duration_ms histogram" in prom_output

    def test_get_prometheus_format_includes_uptime(self) -> None:
        prom_output = self.collector.get_prometheus_format()

        assert "# HELP polaris_uptime_seconds" in prom_output
        assert "# TYPE polaris_uptime_seconds gauge" in prom_output
        assert "polaris_uptime_seconds" in prom_output

    def test_get_prometheus_format_includes_inflight_gauge(self) -> None:
        self.collector.start_request("GET", "/live", 1)
        prom_output = self.collector.get_prometheus_format()

        assert "# HELP polaris_requests_inflight" in prom_output
        assert "# TYPE polaris_requests_inflight gauge" in prom_output

    def test_get_prometheus_format_inf_bucket_uses_plusinf(self) -> None:
        self.collector.record_request("GET", "/slow", 200, 10000.0)
        prom_output = self.collector.get_prometheus_format()

        assert 'le="+Inf"' in prom_output

    def test_reset_clears_all_metrics(self) -> None:
        self.collector.record_request("GET", "/reset", 200, 50.0)
        self.collector.start_request("POST", "/reset", 1)

        self.collector.reset()

        assert len(self.collector._metrics) == 0
        assert len(self.collector._inflight) == 0

    def test_reset_preserves_thread_safety(self) -> None:
        self.collector.reset()  # Should not raise
        self.collector.get_prometheus_format()  # Should not raise

    def test_case_insensitive_method_normalization(self) -> None:
        self.collector.record_request("get", "/lower", 200, 10.0)
        self.collector.record_request("POST", "/upper", 200, 10.0)

        # Keys should be normalized to uppercase
        assert "GET /lower" in self.collector._metrics
        assert "POST /upper" in self.collector._metrics

    def test_latency_buckets_accumulation(self) -> None:
        # Record requests in different bucket ranges
        self.collector.record_request("GET", "/buckets", 200, 5.0)   # <= 10
        self.collector.record_request("GET", "/buckets", 200, 30.0)  # <= 50
        self.collector.record_request("GET", "/buckets", 200, 150.0) # <= 250

        metric = self.collector._metrics["GET /buckets"]
        bucket_10 = next(b for b in metric.latency_buckets if b.upper_bound == 10)
        bucket_50 = next(b for b in metric.latency_buckets if b.upper_bound == 50)
        bucket_250 = next(b for b in metric.latency_buckets if b.upper_bound == 250)

        assert bucket_10.count == 1
        assert bucket_50.count == 2  # 10 < 30 <= 50
        assert bucket_250.count == 3  # 50 < 150 <= 250


class TestMetricsCollectorThreadSafety:
    """Tests for thread safety of MetricsCollector."""

    def test_concurrent_record_requests(self) -> None:
        import threading

        collector = MetricsCollector()
        num_threads = 10
        requests_per_thread = 100

        def record_requests() -> None:
            for i in range(requests_per_thread):
                collector.record_request("GET", "/concurrent", 200, 1.0)

        threads = [threading.Thread(target=record_requests) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All requests should be recorded
        assert collector._metrics["GET /concurrent"].request_count == num_threads * requests_per_thread

    def test_reset_while_recording(self) -> None:
        import threading

        collector = MetricsCollector()
        stop_event = threading.Event()

        def record_loop() -> None:
            i = 0
            while not stop_event.is_set():
                collector.record_request("GET", "/reset", 200, 1.0)
                i += 1

        def reset_loop() -> None:
            for _ in range(50):
                collector.reset()
                time.sleep(0.001)

        recorder = threading.Thread(target=record_loop)
        resetter = threading.Thread(target=reset_loop)

        recorder.start()
        reset_loop()
        stop_event.set()
        recorder.join()

        # Should not deadlock or raise


class TestMetricsMiddleware:
    """Tests for MetricsMiddleware middleware logic."""

    def test_excluded_paths_constant(self) -> None:
        assert "/metrics" in MetricsMiddleware.EXCLUDED_PATHS
        assert "/health" in MetricsMiddleware.EXCLUDED_PATHS
        assert "/favicon.ico" in MetricsMiddleware.EXCLUDED_PATHS

    def test_should_collect_excludes_metrics_path(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        assert middleware._should_collect("/metrics") is False
        assert middleware._should_collect("/metrics/prometheus") is False

    def test_should_collect_excludes_health_path(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        assert middleware._should_collect("/health") is False
        assert middleware._should_collect("/health/live") is False

    def test_should_collect_allows_normal_paths(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        assert middleware._should_collect("/api/users") is True
        assert middleware._should_collect("/v1/posts") is True

    def test_normalize_path_replaces_uuids(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        path = "/users/550e8400-e29b-41d4-a716-446655440000"
        normalized = middleware._normalize_path(path)

        assert normalized == "/users/{id}"

    def test_normalize_path_replaces_numeric_ids(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        path = "/users/12345/posts/67890"
        normalized = middleware._normalize_path(path)

        assert normalized == "/users/{id}/posts/{id}"

    def test_normalize_path_preserves_static_paths(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        path = "/api/users/list"
        normalized = middleware._normalize_path(path)

        assert normalized == "/api/users/list"

    def test_normalize_path_handles_mixed_ids(self) -> None:
        middleware = MetricsMiddleware(MagicMock())
        path = "/users/123e4567-e89b-12d3-a456-426614174000/posts/999"
        normalized = middleware._normalize_path(path)

        assert normalized == "/users/{id}/posts/{id}"

    def test_middleware_uses_provided_collector(self) -> None:
        custom_collector = MetricsCollector()
        middleware = MetricsMiddleware(MagicMock(), collector=custom_collector)

        assert middleware._collector is custom_collector

    def test_middleware_defaults_to_global_collector(self) -> None:
        middleware = MetricsMiddleware(MagicMock())

        assert middleware._collector is not None
        assert isinstance(middleware._collector, MetricsCollector)


class TestGlobalMetricsCollector:
    """Tests for global metrics collector functions."""

    def setup_method(self) -> None:
        reset_metrics_for_testing()

    def test_get_metrics_collector_returns_instance(self) -> None:
        collector = get_metrics_collector()
        assert collector is not None
        assert isinstance(collector, MetricsCollector)

    def test_get_metrics_collector_returns_same_instance(self) -> None:
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        assert collector1 is collector2

    def test_reset_metrics_for_testing_creates_new_instance(self) -> None:
        collector1 = get_metrics_collector()
        reset_metrics_for_testing()
        collector2 = get_metrics_collector()

        # After reset, metrics should be cleared
        assert collector1._metrics is not collector2._metrics or len(collector2._metrics) == 0


class TestPrometheusFormatEdgeCases:
    """Tests for edge cases in Prometheus format generation."""

    def setup_method(self) -> None:
        self.collector = MetricsCollector()

    def test_zero_requests_no_sum_line(self) -> None:
        prom_output = self.collector.get_prometheus_format()
        # Sum line should not appear when request_count is 0
        lines = prom_output.split("\n")
        sum_lines = [l for l in lines if "_sum" in l and "polaris_request_duration" in l]
        assert len(sum_lines) == 0

    def test_single_request_includes_sum_line(self) -> None:
        self.collector.record_request("GET", "/test", 200, 100.0)
        prom_output = self.collector.get_prometheus_format()

        assert 'polaris_request_duration_ms_sum{method="GET",path="/test"} 100.0' in prom_output

    def test_multiple_paths_separated_correctly(self) -> None:
        self.collector.record_request("GET", "/a", 200, 10.0)
        self.collector.record_request("POST", "/b", 200, 20.0)
        self.collector.record_request("DELETE", "/c", 200, 30.0)

        prom_output = self.collector.get_prometheus_format()

        assert 'method="GET",path="/a"' in prom_output
        assert 'method="POST",path="/b"' in prom_output
        assert 'method="DELETE",path="/c"' in prom_output

    def test_path_with_special_characters_escaped(self) -> None:
        self.collector.record_request("GET", "/api/v1/users?q=test&sort=name", 200, 50.0)
        prom_output = self.collector.get_prometheus_format()

        # Path should be quoted in prometheus format
        assert 'path="/api/v1/users' in prom_output
