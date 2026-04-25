"""Tests for Kernel Metrics Module.

Tests the MetricsCollector, Counter, Histogram, Gauge, and DeadLoopMetricsCollector classes.
"""

from __future__ import annotations

import threading

from polaris.cells.roles.kernel.internal.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    MetricsSnapshot,
    get_dead_loop_metrics,
    get_metrics_collector,
    record_cache_stats,
    reset_dead_loop_metrics,
)


class TestCounter:
    """Tests for Counter metric."""

    def test_counter_basic_increment(self) -> None:
        """Test basic counter increment."""
        counter = Counter("test_counter", "A test counter")
        counter.inc()
        assert counter.get() == 1

    def test_counter_increment_by_value(self) -> None:
        """Test counter increment by specific value."""
        counter = Counter("test_counter", "A test counter")
        counter.inc(5)
        assert counter.get() == 5

    def test_counter_multiple_increments(self) -> None:
        """Test multiple increments accumulate correctly."""
        counter = Counter("test_counter", "A test counter")
        counter.inc()
        counter.inc()
        counter.inc(5)
        assert counter.get() == 7

    def test_counter_with_labels(self) -> None:
        """Test counter with label dimensions."""
        counter = Counter("test_counter", "A test counter", labelnames=("level",))
        counter.labels(level="l1").inc()
        counter.labels(level="l2").inc()
        counter.labels(level="l1").inc()
        assert counter.labels(level="l1").get() == 2
        assert counter.labels(level="l2").get() == 1

    def test_counter_collect_format(self) -> None:
        """Test counter collect returns correct Prometheus format."""
        counter = Counter("test_counter", "A test counter", labelnames=("env",))
        counter.labels(env="prod").inc(10)
        result = counter.collect()
        assert result["name"] == "test_counter"
        assert result["type"] == "counter"
        assert result["values"][(("env", "prod"),)] == 10

    def test_counter_thread_safety(self) -> None:
        """Test counter is thread-safe for concurrent increments."""
        counter = Counter("test_counter", "A test counter")
        threads: list[threading.Thread] = []

        def increment_many() -> None:
            for _ in range(100):
                counter.inc()

        for _ in range(10):
            t = threading.Thread(target=increment_many)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert counter.get() == 1000


class TestHistogram:
    """Tests for Histogram metric."""

    def test_histogram_basic_observation(self) -> None:
        """Test basic histogram observation."""
        hist = Histogram("test_histogram", "A test histogram")
        hist.observe(0.5)
        hist.observe(1.0)
        hist.observe(1.5)
        stats = hist.get_stats()
        assert stats["count"] == 3
        assert stats["sum"] == 3.0

    def test_histogram_statistics(self) -> None:
        """Test histogram statistics calculation."""
        hist = Histogram("test_histogram", "A test histogram")
        for value in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
            hist.observe(value)
        stats = hist.get_stats()
        assert stats["count"] == 10
        assert stats["min"] == 0.1
        assert stats["max"] == 1.0
        assert 0.4 <= stats["p50"] <= 0.6
        assert 0.8 <= stats["p95"] <= 1.0

    def test_histogram_empty_stats(self) -> None:
        """Test empty histogram returns zero statistics."""
        hist = Histogram("test_histogram", "A test histogram")
        stats = hist.get_stats()
        assert stats["count"] == 0
        assert stats["sum"] == 0
        assert stats["avg"] == 0

    def test_histogram_custom_buckets(self) -> None:
        """Test histogram with custom buckets."""
        buckets = (0.1, 0.5, 1.0, 5.0)
        hist = Histogram("test_histogram", "A test histogram", buckets=buckets)
        result = hist.collect()
        assert result["buckets"] == buckets

    def test_histogram_max_samples(self) -> None:
        """Test histogram respects max_samples limit."""
        hist = Histogram("test_histogram", "A test histogram", max_samples=100)
        for i in range(200):
            hist.observe(float(i))
        stats = hist.get_stats()
        # Should not exceed max_samples
        assert stats["count"] == 200  # count is total observations
        assert len(hist._values) <= 100  # _values is reservoir

    def test_histogram_collect_format(self) -> None:
        """Test histogram collect returns correct format."""
        hist = Histogram("test_histogram", "A test histogram")
        hist.observe(1.0)
        result = hist.collect()
        assert result["name"] == "test_histogram"
        assert result["type"] == "histogram"
        assert "count" in result
        assert "sum" in result


class TestGauge:
    """Tests for Gauge metric."""

    def test_gauge_set_value(self) -> None:
        """Test gauge set operation."""
        gauge = Gauge("test_gauge", "A test gauge")
        gauge.set(42.0)
        assert gauge.get() == 42.0

    def test_gauge_increment(self) -> None:
        """Test gauge increment operation."""
        gauge = Gauge("test_gauge", "A test gauge")
        gauge.set(10.0)
        gauge.inc()
        assert gauge.get() == 11.0
        gauge.inc(5)
        assert gauge.get() == 16.0

    def test_gauge_decrement(self) -> None:
        """Test gauge decrement operation."""
        gauge = Gauge("test_gauge", "A test gauge")
        gauge.set(10.0)
        gauge.dec()
        assert gauge.get() == 9.0
        gauge.dec(3)
        assert gauge.get() == 6.0

    def test_gauge_collect_format(self) -> None:
        """Test gauge collect returns correct format."""
        gauge = Gauge("test_gauge", "A test gauge")
        gauge.set(99.0)
        result = gauge.collect()
        assert result["name"] == "test_gauge"
        assert result["type"] == "gauge"
        assert result["value"] == 99.0


class TestMetricsCollector:
    """Tests for MetricsCollector singleton."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        MetricsCollector.reset_for_testing()
        MetricsCollector.reset()

    def test_singleton_pattern(self) -> None:
        """Test MetricsCollector is a singleton."""
        collector1 = MetricsCollector.get_instance()
        collector2 = MetricsCollector.get_instance()
        assert collector1 is collector2

    def test_record_cache_hit(self) -> None:
        """Test recording cache hits."""
        collector = MetricsCollector.get_instance()
        collector.record_cache_hit("l1")
        collector.record_cache_hit("l1")
        collector.record_cache_hit("l2")
        # Verify via collect_all
        all_metrics = collector.collect_all()
        cache_hit_metric = next(m for m in all_metrics if m["name"] == "role_kernel_cache_hit")
        assert cache_hit_metric["values"][(("level", "l1"),)] == 2
        assert cache_hit_metric["values"][(("level", "l2"),)] == 1

    def test_record_cache_miss(self) -> None:
        """Test recording cache misses."""
        collector = MetricsCollector.get_instance()
        collector.record_cache_miss("l1")
        collector.record_cache_miss("l3")
        all_metrics = collector.collect_all()
        cache_miss_metric = next(m for m in all_metrics if m["name"] == "role_kernel_cache_miss")
        assert cache_miss_metric["values"][(("level", "l1"),)] == 1
        assert cache_miss_metric["values"][(("level", "l3"),)] == 1

    def test_record_llm_latency(self) -> None:
        """Test recording LLM latency."""
        collector = MetricsCollector.get_instance()
        collector.record_llm_latency(1.5)
        snapshot = collector.get_snapshot()
        assert snapshot.llm_stats["last_latency"] == 1.5

    def test_record_quality_score(self) -> None:
        """Test recording quality score."""
        collector = MetricsCollector.get_instance()
        collector.record_quality_score(0.95)
        snapshot = collector.get_snapshot()
        assert snapshot.quality_stats["last_score"] == 0.95

    def test_record_execution(self) -> None:
        """Test recording execution events."""
        collector = MetricsCollector.get_instance()
        collector.record_execution("coder", "success")
        collector.record_execution("coder", "success")
        collector.record_execution("coder", "failed")
        all_metrics = collector.collect_all()
        exec_metric = next(m for m in all_metrics if m["name"] == "role_kernel_execution_total")
        assert exec_metric["values"][(("role", "coder"), ("status", "success"))] == 2
        assert exec_metric["values"][(("role", "coder"), ("status", "failed"))] == 1

    def test_record_retry(self) -> None:
        """Test recording retry events."""
        collector = MetricsCollector.get_instance()
        collector.record_retry("coder", "timeout")
        collector.record_retry("coder", "rate_limit")
        all_metrics = collector.collect_all()
        retry_metric = next(m for m in all_metrics if m["name"] == "role_kernel_retry_total")
        # Labels are sorted by key, so ("reason", ...) comes before ("role", ...)
        assert retry_metric["values"][(("reason", "timeout"), ("role", "coder"))] == 1
        assert retry_metric["values"][(("reason", "rate_limit"), ("role", "coder"))] == 1

    def test_record_transaction_metrics(self) -> None:
        """Test recording Phase 7 transaction metrics."""
        collector = MetricsCollector.get_instance()
        metrics = {
            "transaction_kernel.violation_count": 2,
            "turn.single_batch_ratio": 0.95,
            "workflow.handoff_rate": 0.1,
            "kernel_guard.assert_fail_rate": 0.01,
            "speculative.hit_rate": 0.8,
            "speculative.false_positive_rate": 0.05,
        }
        collector.record_transaction_metrics(metrics)
        snapshot = collector.get_snapshot()
        assert "execution" in snapshot.to_dict()

    def test_get_snapshot(self) -> None:
        """Test getting metrics snapshot."""
        collector = MetricsCollector.get_instance()
        collector.record_cache_hit("l1")
        collector.record_llm_latency(2.0)
        collector.record_quality_score(0.85)
        snapshot = collector.get_snapshot()
        assert isinstance(snapshot, MetricsSnapshot)
        assert snapshot.timestamp > 0
        assert snapshot.cache_stats["l1_hits"] == 1
        assert snapshot.llm_stats["last_latency"] == 2.0
        assert snapshot.quality_stats["last_score"] == 0.85

    def test_get_prometheus_format(self) -> None:
        """Test Prometheus format export."""
        collector = MetricsCollector.get_instance()
        collector.record_cache_hit("l1")
        prom_output = collector.get_prometheus_format()
        assert "# HELP role_kernel_cache_hit" in prom_output
        assert "# TYPE role_kernel_cache_hit counter" in prom_output


class TestRecordCacheStats:
    """Tests for record_cache_stats convenience function."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        MetricsCollector.reset_for_testing()
        MetricsCollector.reset()

    def test_record_cache_stats_all_levels(self) -> None:
        """Test recording cache stats for all levels."""
        stats = {
            "l1_hits": 10,
            "l1_misses": 2,
            "l2_hits": 5,
            "l2_misses": 1,
            "l3_hits": 2,
            "l3_misses": 0,
        }
        record_cache_stats(stats)
        collector = get_metrics_collector()
        all_metrics = collector.collect_all()
        cache_hit_metric = next(m for m in all_metrics if m["name"] == "role_kernel_cache_hit")
        assert cache_hit_metric["values"][(("level", "l1"),)] == 10
        assert cache_hit_metric["values"][(("level", "l2"),)] == 5
        assert cache_hit_metric["values"][(("level", "l3"),)] == 2

    def test_record_cache_stats_empty(self) -> None:
        """Test recording empty cache stats."""
        record_cache_stats({})
        collector = get_metrics_collector()
        all_metrics = collector.collect_all()
        cache_hit_metric = next(m for m in all_metrics if m["name"] == "role_kernel_cache_hit")
        assert cache_hit_metric["values"] == {}


class TestDeadLoopMetricsCollector:
    """Tests for DeadLoopMetricsCollector."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        reset_dead_loop_metrics()

    def test_singleton_dead_loop_metrics(self) -> None:
        """Test get_dead_loop_metrics returns singleton."""
        dm1 = get_dead_loop_metrics()
        dm2 = get_dead_loop_metrics()
        assert dm1 is dm2

    def test_record_circuit_breaker(self) -> None:
        """Test recording circuit breaker events."""
        dm = get_dead_loop_metrics()
        dm.record_circuit_breaker("same_tool", "Read", {"file": "test.py"})
        dm.record_circuit_breaker("stagnation")
        recent = dm.get_recent_breakers()
        assert len(recent) == 2
        assert recent[0]["breaker_type"] == "same_tool"
        assert recent[1]["breaker_type"] == "stagnation"

    def test_record_intent_switch(self) -> None:
        """Test recording intent switch events."""
        dm = get_dead_loop_metrics()
        dm.record_intent_switch("read_file", "write_file")

    def test_record_thinking_violation(self) -> None:
        """Test recording thinking tag violations."""
        dm = get_dead_loop_metrics()
        dm.record_thinking_violation("missing_close_tag")
        dm.record_thinking_violation("invalid_format")

    def test_record_emergency_compaction(self) -> None:
        """Test recording emergency compaction events."""
        dm = get_dead_loop_metrics()
        dm.record_emergency_compaction(100)

    def test_record_read_only_streak(self) -> None:
        """Test recording read-only streak lengths."""
        dm = get_dead_loop_metrics()
        dm.record_read_only_streak(5)
        dm.record_read_only_streak(10)

    def test_record_tool_call(self) -> None:
        """Test recording tool calls by category."""
        dm = get_dead_loop_metrics()
        dm.record_tool_call(is_read_only=True)
        dm.record_tool_call(is_read_only=False)
        dm.record_tool_call(is_read_only=True)

    def test_get_recent_breakers_with_limit(self) -> None:
        """Test getting recent breakers with limit."""
        dm = get_dead_loop_metrics()
        for i in range(20):
            dm.record_circuit_breaker(f"type_{i % 5}")
        recent = dm.get_recent_breakers(limit=5)
        assert len(recent) == 5

    def test_get_recent_breakers_empty(self) -> None:
        """Test getting recent breakers when none recorded."""
        dm = get_dead_loop_metrics()
        recent = dm.get_recent_breakers()
        assert recent == []


class TestMetricsSnapshot:
    """Tests for MetricsSnapshot dataclass."""

    def test_snapshot_to_dict(self) -> None:
        """Test snapshot serialization to dict."""
        snapshot = MetricsSnapshot(
            timestamp=1234567890.0,
            cache_stats={"l1_hits": 10},
            llm_stats={"last_latency": 1.5},
            quality_stats={"last_score": 0.9},
            execution_stats={"uptime": 3600.0},
        )
        result = snapshot.to_dict()
        assert result["timestamp"] == 1234567890.0
        assert result["cache"]["l1_hits"] == 10
        assert result["llm"]["last_latency"] == 1.5
        assert result["quality"]["last_score"] == 0.9
        assert result["execution"]["uptime"] == 3600.0

    def test_snapshot_default_values(self) -> None:
        """Test snapshot default values."""
        snapshot = MetricsSnapshot()
        assert snapshot.timestamp > 0
        assert snapshot.cache_stats == {}
        assert snapshot.llm_stats == {}
        assert snapshot.quality_stats == {}
        assert snapshot.execution_stats == {}


class TestMetricsCollectorReset:
    """Tests for MetricsCollector reset functionality."""

    def setup_method(self) -> None:
        """Reset before each test."""
        MetricsCollector.reset_for_testing()
        MetricsCollector.reset()

    def test_reset_clears_all_metrics(self) -> None:
        """Test that reset clears all metric values."""
        collector = MetricsCollector.get_instance()
        collector.record_cache_hit("l1")
        collector.record_llm_latency(5.0)
        MetricsCollector.reset()
        # After reset, new collector should have zero values
        collector2 = MetricsCollector()
        assert collector2.get_snapshot().cache_stats["l1_hits"] == 0

    def test_reset_allows_new_instance_after_reset_for_testing(self) -> None:
        """Test reset_for_testing allows new singleton."""
        collector1 = MetricsCollector.get_instance()
        MetricsCollector.reset_for_testing()
        collector2 = MetricsCollector.get_instance()
        # Should be different instances after reset
        assert collector1 is not collector2
