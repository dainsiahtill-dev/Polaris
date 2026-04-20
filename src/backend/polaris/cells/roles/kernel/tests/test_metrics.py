"""Tests for metrics module."""

from __future__ import annotations

import os

from polaris.cells.roles.kernel.internal.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    get_metrics_collector,
    record_cache_stats,
)


class TestCounter:
    """Tests for Counter metric."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset()

    def test_counter_initial_value(self) -> None:
        """Counter starts at zero."""
        counter = Counter("test_counter", "Test counter")
        assert counter.get() == 0

    def test_counter_increment(self) -> None:
        """Counter increments correctly."""
        counter = Counter("test_counter", "Test counter")
        counter.inc()
        assert counter.get() == 1

    def test_counter_increment_with_value(self) -> None:
        """Counter increments by specified value."""
        counter = Counter("test_counter", "Test counter")
        counter.inc(5)
        assert counter.get() == 5

    def test_counter_with_labels(self) -> None:
        """Counter works with labels."""
        counter = Counter("test_counter", "Test counter", labelnames=("level",))
        counter.labels(level="l1").inc()
        counter.labels(level="l2").inc()
        counter.labels(level="l2").inc()

        # Keys are stored as tuple(sorted(label_items)) format
        assert counter.get((("level", "l1"),)) == 1
        assert counter.get((("level", "l2"),)) == 2
        assert counter.get((("level", "l3"),)) == 0

    def test_counter_collect(self) -> None:
        """Counter collects metrics correctly."""
        counter = Counter("test_counter", "Test counter", labelnames=("level",))
        counter.labels(level="l1").inc()

        collected = counter.collect()
        assert collected["name"] == "test_counter"
        assert collected["type"] == "counter"
        # Keys are stored as tuple(sorted(label_items)) format
        assert collected["values"][(("level", "l1"),)] == 1


class TestHistogram:
    """Tests for Histogram metric."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset()

    def test_histogram_initial(self) -> None:
        """Histogram starts empty."""
        histogram = Histogram("test_histogram", "Test histogram")
        stats = histogram.get_stats()
        assert stats["count"] == 0
        assert stats["sum"] == 0

    def test_histogram_observe(self) -> None:
        """Histogram records observations."""
        histogram = Histogram("test_histogram", "Test histogram")
        histogram.observe(1.0)
        histogram.observe(2.0)
        histogram.observe(3.0)

        stats = histogram.get_stats()
        assert stats["count"] == 3
        assert stats["sum"] == 6.0
        assert stats["avg"] == 2.0
        assert stats["min"] == 1.0
        assert stats["max"] == 3.0

    def test_histogram_percentiles(self) -> None:
        """Histogram calculates percentiles."""
        histogram = Histogram("test_histogram", "Test histogram")
        for i in range(100):
            histogram.observe(float(i))

        stats = histogram.get_stats()
        # p50 should be around 49-50
        assert 45 <= stats["p50"] <= 55
        # p95 should be around 94-95
        assert 90 <= stats["p95"] <= 99
        # p99 should be around 98-99
        assert 95 <= stats["p99"] <= 99


class TestGauge:
    """Tests for Gauge metric."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset()

    def test_gauge_initial(self) -> None:
        """Gauge starts at zero."""
        gauge = Gauge("test_gauge", "Test gauge")
        assert gauge.get() == 0

    def test_gauge_set(self) -> None:
        """Gauge sets value correctly."""
        gauge = Gauge("test_gauge", "Test gauge")
        gauge.set(42.0)
        assert gauge.get() == 42.0

    def test_gauge_inc(self) -> None:
        """Gauge increments."""
        gauge = Gauge("test_gauge", "Test gauge")
        gauge.inc()
        assert gauge.get() == 1

    def test_gauge_dec(self) -> None:
        """Gauge decrements."""
        gauge = Gauge("test_gauge", "Test gauge")
        gauge.set(10)
        gauge.dec(3)
        assert gauge.get() == 7


class TestMetricsCollector:
    """Tests for MetricsCollector singleton."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset()

    def test_singleton(self) -> None:
        """MetricsCollector is a singleton."""
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        assert collector1 is collector2

    def test_record_cache_hit(self) -> None:
        """Collector records cache hits."""
        collector = get_metrics_collector()
        collector.record_cache_hit("l1")
        collector.record_cache_hit("l1")
        collector.record_cache_hit("l2")

        snapshot = collector.get_snapshot()
        assert snapshot.cache_stats["l1_hits"] == 2
        assert snapshot.cache_stats["l2_hits"] == 1

    def test_record_cache_miss(self) -> None:
        """Collector records cache misses."""
        collector = get_metrics_collector()
        collector.record_cache_miss("l1")

        snapshot = collector.get_snapshot()
        assert snapshot.cache_stats["l1_misses"] == 1

    def test_record_llm_latency(self) -> None:
        """Collector records LLM latency."""
        collector = get_metrics_collector()
        collector.record_llm_latency(1.5)

        snapshot = collector.get_snapshot()
        assert snapshot.llm_stats["last_latency"] == 1.5

    def test_record_quality_score(self) -> None:
        """Collector records quality score."""
        collector = get_metrics_collector()
        collector.record_quality_score(85.5)

        snapshot = collector.get_snapshot()
        assert snapshot.quality_stats["last_score"] == 85.5

    def test_record_execution(self) -> None:
        """Collector records execution."""
        collector = get_metrics_collector()
        collector.record_execution("pm", "success")
        collector.record_execution("director", "validation_failed")

        # Verify through collect_all
        all_metrics = collector.collect_all()
        exec_metric = next(m for m in all_metrics if m["name"] == "role_kernel_execution_total")
        # Keys are stored as tuple(sorted(label_items)) format
        assert exec_metric["values"][(("role", "pm"), ("status", "success"))] == 1
        assert exec_metric["values"][(("role", "director"), ("status", "validation_failed"))] == 1

    def test_record_retry(self) -> None:
        """Collector records retries."""
        collector = get_metrics_collector()
        collector.record_retry("pm", "validation_failed")
        collector.record_retry("pm", "validation_failed")

        all_metrics = collector.collect_all()
        retry_metric = next(m for m in all_metrics if m["name"] == "role_kernel_retry_total")
        # Keys are stored as tuple(sorted(label_items)) format, sorted by label name
        assert retry_metric["values"][(("reason", "validation_failed"), ("role", "pm"))] == 2


class TestRecordCacheStats:
    """Tests for record_cache_stats convenience function."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset()

    def test_record_cache_stats(self) -> None:
        """record_cache_stats works correctly."""
        record_cache_stats(
            {
                "l1_hits": 5,
                "l2_hits": 3,
                "l3_hits": 2,
                "l1_misses": 1,
                "l2_misses": 0,
                "l3_misses": 0,
            }
        )

        snapshot = get_metrics_collector().get_snapshot()
        assert snapshot.cache_stats["l1_hits"] == 5
        assert snapshot.cache_stats["l2_hits"] == 3
        assert snapshot.cache_stats["l3_hits"] == 2
        assert snapshot.cache_stats["l1_misses"] == 1


class TestTransactionMetrics:
    """Tests for Phase 7 transaction monitoring metrics."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset()

    def test_record_transaction_metrics(self) -> None:
        """Collector records Phase 7 transaction metrics."""
        collector = get_metrics_collector()
        collector.record_transaction_metrics(
            {
                "transaction_kernel.violation_count": 2.0,
                "turn.single_batch_ratio": 1.0,
                "workflow.handoff_rate": 0.0,
                "kernel_guard.assert_fail_rate": 0.5,
                "speculative.hit_rate": 0.75,
                "speculative.false_positive_rate": 0.25,
            }
        )

        all_metrics = collector.collect_all()
        names = {m["name"] for m in all_metrics}
        assert "transaction_kernel_violation_count_total" in names
        assert "turn_single_batch_ratio" in names
        assert "workflow_handoff_rate" in names
        assert "kernel_guard_assert_fail_rate" in names
        assert "speculative_hit_rate" in names
        assert "speculative_false_positive_rate" in names

        violation_metric = next(m for m in all_metrics if m["name"] == "transaction_kernel_violation_count_total")
        assert violation_metric["values"][()] == 2.0

        handoff_metric = next(m for m in all_metrics if m["name"] == "workflow_handoff_rate")
        assert handoff_metric["value"] == 0.0

    def test_get_prometheus_format(self) -> None:
        """Prometheus text export includes transaction metrics."""
        collector = get_metrics_collector()
        collector.record_transaction_metrics(
            {
                "transaction_kernel.violation_count": 1.0,
                "turn.single_batch_ratio": 1.0,
                "workflow.handoff_rate": 0.0,
                "kernel_guard.assert_fail_rate": 0.0,
                "speculative.hit_rate": 0.0,
                "speculative.false_positive_rate": 0.0,
            }
        )

        text = collector.get_prometheus_format()
        assert "transaction_kernel_violation_count_total" in text
        assert "turn_single_batch_ratio" in text
        assert "workflow_handoff_rate" in text
        assert "kernel_guard_assert_fail_rate" in text
        assert "speculative_hit_rate" in text
        assert "speculative_false_positive_rate" in text


class TestQualityThresholdEnvVar:
    """Tests for POLARIS_QUALITY_THRESHOLD environment variable."""

    def setup_method(self) -> None:
        """Reset environment before each test."""
        self._original_env = os.environ.get("POLARIS_QUALITY_THRESHOLD")
        # Reset the singleton for each test
        MetricsCollector.reset()

    def teardown_method(self) -> None:
        """Restore environment after each test."""
        if self._original_env is None:
            os.environ.pop("POLARIS_QUALITY_THRESHOLD", None)
        else:
            os.environ["POLARIS_QUALITY_THRESHOLD"] = self._original_env

    def test_default_threshold(self) -> None:
        """Default threshold is 60.0."""
        os.environ.pop("POLARIS_QUALITY_THRESHOLD", None)
        from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker

        checker = QualityChecker()
        assert checker.quality_threshold == 60.0

    def test_custom_threshold(self) -> None:
        """Custom threshold from environment variable."""
        os.environ["POLARIS_QUALITY_THRESHOLD"] = "75.5"
        from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker

        checker = QualityChecker()
        assert checker.quality_threshold == 75.5

    def test_threshold_clamping(self) -> None:
        """Threshold is clamped to [0, 100]."""
        os.environ["POLARIS_QUALITY_THRESHOLD"] = "150"
        from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker

        checker = QualityChecker()
        assert checker.quality_threshold == 100.0

        os.environ["POLARIS_QUALITY_THRESHOLD"] = "-10"
        checker2 = QualityChecker()
        assert checker2.quality_threshold == 0.0

    def test_invalid_threshold_falls_back(self) -> None:
        """Invalid threshold falls back to default."""
        os.environ["POLARIS_QUALITY_THRESHOLD"] = "invalid"
        from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker

        checker = QualityChecker()
        assert checker.quality_threshold == 60.0

    def test_set_threshold_runtime(self) -> None:
        """Threshold can be set at runtime."""
        from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker

        checker = QualityChecker()
        checker.set_quality_threshold(80.0)
        assert checker.quality_threshold == 80.0

        # Verify clamping
        checker.set_quality_threshold(150.0)
        assert checker.quality_threshold == 100.0
