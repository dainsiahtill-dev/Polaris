"""Tests for polaris.kernelone.llm.engine.telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from polaris.kernelone.llm.engine.telemetry import (
    MetricsAggregator,
    TelemetryCollector,
    TelemetryEvent,
    create_telemetry_collector,
)


class TestTelemetryEvent:
    def test_to_dict_basic(self) -> None:
        event = TelemetryEvent(
            event_id="e1",
            event_type="invoke_start",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        d = event.to_dict()
        assert d["event_id"] == "e1"
        assert d["event_type"] == "invoke_start"
        assert d["timestamp"] == "2026-04-24T10:00:00Z"
        assert d["trace_id"] == "t1"

    def test_to_dict_omits_none(self) -> None:
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        d = event.to_dict()
        assert "task_type" not in d
        assert "latency_ms" not in d

    def test_to_dict_includes_optional(self) -> None:
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
            task_type="generation",
            role="pm",
            latency_ms=100,
            tokens={"prompt": 10, "completion": 20},
            error_category="timeout",
            error_message="timed out",
            metadata={"key": "value"},
        )
        d = event.to_dict()
        assert d["task_type"] == "generation"
        assert d["role"] == "pm"
        assert d["latency_ms"] == 100
        assert d["tokens"] == {"prompt": 10, "completion": 20}
        assert d["error_category"] == "timeout"
        assert d["error_message"] == "timed out"
        assert d["metadata"] == {"key": "value"}

    def test_to_json_line(self) -> None:
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        line = event.to_json_line()
        assert line.endswith("\n")
        parsed = json.loads(line)
        assert parsed["event_id"] == "e1"


class TestTelemetryCollector:
    def test_disabled_does_not_emit(self) -> None:
        collector = TelemetryCollector(enabled=False)
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        assert collector.get_events() == []

    def test_emit_adds_to_buffer(self) -> None:
        collector = TelemetryCollector(enabled=True)
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        assert len(collector.get_events()) == 1

    def test_emit_notifies_listener(self) -> None:
        collector = TelemetryCollector(enabled=True)
        received: list[TelemetryEvent] = []
        collector.add_listener(lambda e: received.append(e))
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        assert len(received) == 1
        assert received[0].event_id == "e1"

    def test_listener_exception_does_not_break(self) -> None:
        collector = TelemetryCollector(enabled=True)
        collector.add_listener(lambda e: (_ for _ in ()).throw(ValueError("boom")))
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        # Should not raise
        collector.emit(event)
        assert len(collector.get_events()) == 1

    def test_remove_listener(self) -> None:
        collector = TelemetryCollector(enabled=True)
        received: list[TelemetryEvent] = []
        listener = lambda e: received.append(e)  # noqa: E731
        collector.add_listener(listener)
        collector.remove_listener(listener)
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        assert len(received) == 0

    def test_buffer_size_limit(self) -> None:
        collector = TelemetryCollector(enabled=True)
        collector._buffer_size = 3
        for i in range(5):
            event = TelemetryEvent(
                event_id=f"e{i}",
                event_type="test",
                timestamp="2026-04-24T10:00:00Z",
                trace_id="t1",
            )
            collector.emit(event)
        events = collector.get_events()
        assert len(events) == 3
        assert events[0].event_id == "e2"
        assert events[-1].event_id == "e4"

    def test_get_events_filter_by_trace_id(self) -> None:
        collector = TelemetryCollector(enabled=True)
        for trace_id in ("t1", "t2", "t1"):
            event = TelemetryEvent(
                event_id=f"e-{trace_id}",
                event_type="test",
                timestamp="2026-04-24T10:00:00Z",
                trace_id=trace_id,
            )
            collector.emit(event)
        t1_events = collector.get_events(trace_id="t1")
        assert len(t1_events) == 2

    def test_flush_clears_buffer(self) -> None:
        collector = TelemetryCollector(enabled=True)
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        collector.flush()
        assert collector.get_events() == []

    def test_persist_event_writes_file(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        collector = TelemetryCollector(enabled=True, events_file=events_file)
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        assert events_file.exists()
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_id"] == "e1"

    def test_persist_event_creates_parent_dirs(self, tmp_path: Path) -> None:
        events_file = tmp_path / "nested" / "dir" / "events.jsonl"
        collector = TelemetryCollector(enabled=True, events_file=events_file)
        event = TelemetryEvent(
            event_id="e1",
            event_type="test",
            timestamp="2026-04-24T10:00:00Z",
            trace_id="t1",
        )
        collector.emit(event)
        assert events_file.exists()

    def test_persist_event_failure_does_not_break(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        collector = TelemetryCollector(enabled=True, events_file=events_file)
        # Make parent a file so mkdir fails
        events_file.parent.mkdir(parents=True, exist_ok=True)
        # This shouldn't happen in practice, but test resilience
        with patch.object(events_file, "parent", tmp_path / "not_a_dir"):
            event = TelemetryEvent(
                event_id="e1",
                event_type="test",
                timestamp="2026-04-24T10:00:00Z",
                trace_id="t1",
            )
            # Should not raise
            collector.emit(event)


class TestMetricsAggregator:
    def test_empty_stats(self) -> None:
        agg = MetricsAggregator()
        stats = agg.get_stats()
        assert stats["total_requests"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["avg_latency_ms"] == 0
        assert stats["p95_latency_ms"] == 0
        assert stats["p99_latency_ms"] == 0
        assert stats["avg_tokens"] == 0
        assert stats["error_breakdown"] == {}

    def test_record_success(self) -> None:
        agg = MetricsAggregator()
        agg.record_request(latency_ms=100, tokens=50, success=True)
        stats = agg.get_stats()
        assert stats["total_requests"] == 1
        assert stats["success_rate"] == 1.0
        assert stats["avg_latency_ms"] == 100
        assert stats["avg_tokens"] == 50

    def test_record_failure(self) -> None:
        agg = MetricsAggregator()
        agg.record_request(latency_ms=200, tokens=30, success=False, error_category="timeout")
        stats = agg.get_stats()
        assert stats["total_requests"] == 1
        assert stats["success_rate"] == 0.0
        assert stats["error_breakdown"] == {"timeout": 1}

    def test_multiple_requests(self) -> None:
        agg = MetricsAggregator()
        agg.record_request(latency_ms=100, tokens=10, success=True)
        agg.record_request(latency_ms=200, tokens=20, success=True)
        agg.record_request(latency_ms=300, tokens=30, success=False, error_category="rate_limit")
        stats = agg.get_stats()
        assert stats["total_requests"] == 3
        assert stats["success_rate"] == 2 / 3
        assert stats["avg_latency_ms"] == 200
        assert stats["avg_tokens"] == 20
        assert stats["error_breakdown"] == {"rate_limit": 1}

    def test_window_size_limit(self) -> None:
        agg = MetricsAggregator(window_size=3)
        for i in range(5):
            agg.record_request(latency_ms=i * 100, tokens=i * 10, success=True)
        stats = agg.get_stats()
        # Only last 3 should be kept
        assert stats["avg_latency_ms"] == 300

    def test_percentiles(self) -> None:
        agg = MetricsAggregator()
        for i in range(100):
            agg.record_request(latency_ms=i + 1, tokens=10, success=True)
        stats = agg.get_stats()
        assert stats["p95_latency_ms"] == 95
        assert stats["p99_latency_ms"] == 99


class TestCreateTelemetryCollector:
    def test_with_workspace(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        collector = create_telemetry_collector(workspace=workspace)
        assert collector.enabled is True
        assert collector.events_file is not None
        assert collector.events_file.parent.name == "telemetry"

    def test_with_events_dir(self, tmp_path: Path) -> None:
        events_dir = tmp_path / "custom"
        collector = create_telemetry_collector(events_dir=events_dir)
        assert collector.events_file is not None
        assert collector.events_file.parent == events_dir

    def test_no_workspace_no_dir(self) -> None:
        collector = create_telemetry_collector()
        assert collector.events_file is None
