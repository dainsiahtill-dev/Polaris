"""Tests for Prometheus Metrics (ContextOS 3.0 P2)."""

import pytest

from polaris.kernelone.context.context_os.metrics.collectors import MetricsCollector, MetricValue
from polaris.kernelone.context.context_os.metrics.exporters import MetricsExporter


class TestMetricValue:
    """Test MetricValue dataclass."""

    def test_create_value(self) -> None:
        value = MetricValue(
            name="test_metric",
            value=42.0,
            labels={"env": "test"},
        )
        assert value.name == "test_metric"
        assert value.value == 42.0

    def test_to_dict(self) -> None:
        value = MetricValue(
            name="test_metric",
            value=42.0,
            labels={"env": "test"},
        )
        d = value.to_dict()
        assert d["name"] == "test_metric"
        assert d["value"] == 42.0
        assert d["labels"] == {"env": "test"}


class TestMetricsCollector:
    """Test MetricsCollector class."""

    def test_create_collector(self) -> None:
        collector = MetricsCollector()
        assert len(collector._gauges) == 0
        assert len(collector._counters) == 0

    def test_record_content_store_entries(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_entries(100)
        assert collector._gauges["contextos_content_store_entries"] == 100.0

    def test_record_content_store_bytes(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_bytes(1024)
        assert collector._gauges["contextos_content_store_bytes"] == 1024.0

    def test_record_content_store_hit(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_hit()
        collector.record_content_store_hit()
        assert collector._counters["contextos_content_store_hits"] == 2

    def test_record_content_store_miss(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_miss()
        assert collector._counters["contextos_content_store_misses"] == 1

    def test_record_phase_transition(self) -> None:
        collector = MetricsCollector()
        collector.record_phase_transition("intake", "planning")
        assert collector._counters["contextos_phase_transitions_total"] == 1
        assert collector._counters["contextos_phase_transition_intake_to_planning"] == 1

    def test_record_phase_duration(self) -> None:
        collector = MetricsCollector()
        collector.record_phase_duration("intake", 1.5)
        collector.record_phase_duration("intake", 2.0)
        assert len(collector._histograms["contextos_phase_duration_intake"]) == 2

    def test_record_attention_score(self) -> None:
        collector = MetricsCollector()
        collector.record_attention_score(0.75)
        collector.record_attention_score(0.85)
        assert len(collector._histograms["contextos_attention_score_distribution"]) == 2

    def test_record_decision_log_entry(self) -> None:
        collector = MetricsCollector()
        collector.record_decision_log_entry("include_full")
        collector.record_decision_log_entry("exclude")
        assert collector._counters["contextos_decision_log_entries_total"] == 2
        assert collector._counters["contextos_decision_log_include_full"] == 1

    def test_record_budget_utilization(self) -> None:
        collector = MetricsCollector()
        collector.record_budget_utilization(0.75)
        assert len(collector._histograms["contextos_budget_utilization_ratio"]) == 1

    def test_record_budget_overrun(self) -> None:
        collector = MetricsCollector()
        collector.record_budget_overrun()
        assert collector._counters["contextos_budget_overruns"] == 1

    def test_collect(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_entries(100)
        collector.record_content_store_hit()
        collector.record_phase_transition("intake", "planning")
        collector.record_attention_score(0.75)

        metrics = collector.collect()
        assert "gauges" in metrics
        assert "counters" in metrics
        assert "histograms" in metrics
        assert metrics["gauges"]["contextos_content_store_entries"] == 100.0

    def test_reset(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_entries(100)
        collector.record_content_store_hit()

        collector.reset()
        assert len(collector._gauges) == 0
        assert len(collector._counters) == 0


class TestMetricsExporter:
    """Test MetricsExporter class."""

    def test_create_exporter(self) -> None:
        collector = MetricsCollector()
        exporter = MetricsExporter(collector)
        assert exporter._collector == collector

    def test_export_prometheus(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_entries(100)
        collector.record_content_store_hit()

        exporter = MetricsExporter(collector)
        prometheus_text = exporter.export_prometheus()

        assert "contextos_content_store_entries 100.0" in prometheus_text
        assert "contextos_content_store_hits 1" in prometheus_text
        assert "# TYPE" in prometheus_text

    def test_export_json(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_entries(100)

        exporter = MetricsExporter(collector)
        json_text = exporter.export_json()

        assert "contextos_content_store_entries" in json_text
        assert "100.0" in json_text

    def test_export_dict(self) -> None:
        collector = MetricsCollector()
        collector.record_content_store_entries(100)

        exporter = MetricsExporter(collector)
        metrics = exporter.export_dict()

        assert metrics["gauges"]["contextos_content_store_entries"] == 100.0
