"""Tests for workflow_runtime internal observability module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.event_stream import EventStream
from polaris.cells.orchestration.workflow_runtime.internal.observability import (
    HealthMonitor,
    MetricsCollector,
    ServiceMetrics,
    StructuredLogger,
    UIEventBridge,
    UIEventType,
    create_observability_stack,
    start_observability,
    stop_observability,
)


class TestServiceMetrics:
    def test_avg_runtime_no_data(self) -> None:
        m = ServiceMetrics(service_id="s1", service_name="test")
        assert m.avg_runtime_ms == 0.0

    def test_success_rate_no_data(self) -> None:
        m = ServiceMetrics(service_id="s1", service_name="test")
        assert m.success_rate == 0.0

    def test_success_rate_calculation(self) -> None:
        m = ServiceMetrics(service_id="s1", service_name="test", success_count=3, failure_count=1)
        assert m.success_rate == 0.75

    def test_to_dict(self) -> None:
        m = ServiceMetrics(service_id="s1", service_name="test")
        d = m.to_dict()
        assert d["service_id"] == "s1"
        assert d["success_rate"] == 0.0


class TestUIEventBridge:
    @pytest.fixture
    def bridge(self) -> UIEventBridge:
        return UIEventBridge(event_stream=EventStream())

    @pytest.mark.asyncio
    async def test_start_stop(self, bridge: UIEventBridge) -> None:
        await bridge.start()
        assert bridge._running is True
        await bridge.stop()
        assert bridge._running is False

    def test_add_remove_handler(self, bridge: UIEventBridge) -> None:
        handler = MagicMock()
        bridge.add_ui_handler(handler)
        assert handler in bridge._ui_handlers
        bridge.remove_ui_handler(handler)
        assert handler not in bridge._ui_handlers

    def test_transform_event_maps_types(self, bridge: UIEventBridge) -> None:
        from polaris.cells.orchestration.workflow_runtime.internal.event_stream import (
            EventLevel,
            EventType,
            OrchestrationEvent,
        )

        event = OrchestrationEvent(
            event_type=EventType.FAILED,
            source="pm",
            level=EventLevel.ERROR,
            process_id="p1",
        )
        ui_event = bridge._transform_event(event)
        assert ui_event is not None
        assert ui_event["type"] == UIEventType.ERROR_NOTIFICATION.value

    def test_transform_event_filters_unknown(self, bridge: UIEventBridge) -> None:
        from polaris.cells.orchestration.workflow_runtime.internal.event_stream import (
            EventType,
            OrchestrationEvent,
        )

        event = OrchestrationEvent(event_type=EventType.HEARTBEAT, source="pm")
        assert bridge._transform_event(event) is None

    def test_emit_backend_started(self, bridge: UIEventBridge) -> None:
        handler = MagicMock()
        bridge.add_ui_handler(handler)
        bridge.emit_backend_started(49977)
        handler.assert_called_once()
        call_args = handler.call_args[0][0]
        assert call_args["type"] == UIEventType.BACKEND_STARTED.value
        assert call_args["data"]["port"] == 49977


class TestMetricsCollector:
    @pytest.fixture
    def collector(self) -> MetricsCollector:
        return MetricsCollector(event_stream=EventStream())

    @pytest.mark.asyncio
    async def test_start_stop(self, collector: MetricsCollector) -> None:
        await collector.start()
        assert collector._running is True
        await collector.stop()
        assert collector._running is False

    def test_get_metrics_missing(self, collector: MetricsCollector) -> None:
        assert collector.get_metrics("missing") is None

    def test_get_summary_empty(self, collector: MetricsCollector) -> None:
        assert collector.get_summary()["total_services"] == 0

    def test_export_json(self, collector: MetricsCollector, tmp_path: Path) -> None:

        collector._metrics["s1"] = ServiceMetrics(
            service_id="s1",
            service_name="test",
            success_count=1,
        )
        path = tmp_path / "metrics.json"
        collector.export_json(path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "s1" in content


class TestHealthMonitor:
    @pytest.fixture
    def monitor(self) -> HealthMonitor:
        return HealthMonitor(event_stream=EventStream())

    @pytest.mark.asyncio
    async def test_start_stop(self, monitor: HealthMonitor) -> None:
        await monitor.start()
        assert monitor._running is True
        await monitor.stop()
        assert monitor._running is False

    def test_get_health_status_empty(self, monitor: HealthMonitor) -> None:
        status = monitor.get_health_status()
        assert status["healthy"] is True
        assert status["total_services"] == 0

    def test_is_backend_ready(self, monitor: HealthMonitor) -> None:
        assert monitor.is_backend_ready() is False

    @pytest.mark.asyncio
    async def test_wait_for_backend_timeout(self, monitor: HealthMonitor) -> None:
        result = await monitor.wait_for_backend(timeout=0.05)
        assert result is False


class TestStructuredLogger:
    @pytest.fixture
    def logger(self, tmp_path: Path) -> StructuredLogger:
        return StructuredLogger(event_stream=EventStream(), log_path=tmp_path / "log.jsonl")

    @pytest.mark.asyncio
    async def test_start_stop(self, logger: StructuredLogger) -> None:
        await logger.start()
        assert logger._running is True
        await logger.stop()
        assert logger._running is False


class TestConvenienceFunctions:
    def test_create_observability_stack(self) -> None:
        stack = create_observability_stack()
        assert len(stack) == 4

    @pytest.mark.asyncio
    async def test_start_stop_observability(self) -> None:
        stack = create_observability_stack()
        await start_observability(*stack)
        await stop_observability(*stack)
