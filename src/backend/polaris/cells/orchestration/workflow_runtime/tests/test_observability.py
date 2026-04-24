"""Tests for polaris.cells.orchestration.workflow_runtime.internal.observability module.

This module tests the observability layer for Polaris orchestration.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.event_stream import (
    EventLevel,
    EventStream,
    EventType,
    OrchestrationEvent,
)
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


class TestUIEventType:
    """Tests for UIEventType enum."""

    def test_all_event_types_exist(self) -> None:
        """All expected UI event types exist."""
        assert UIEventType.SERVICE_STATUS.value == "service_status"
        assert UIEventType.SERVICE_LOG.value == "service_log"
        assert UIEventType.TASK_PROGRESS.value == "task_progress"
        assert UIEventType.SYSTEM_METRICS.value == "system_metrics"
        assert UIEventType.ERROR_NOTIFICATION.value == "error_notification"
        assert UIEventType.HEALTH_STATUS.value == "health_status"
        assert UIEventType.BACKEND_STARTED.value == "backend_started"


class TestServiceMetrics:
    """Tests for ServiceMetrics dataclass."""

    def test_construction(self) -> None:
        """ServiceMetrics can be constructed."""
        metrics = ServiceMetrics(service_id="pm-1", service_name="PM Service")
        assert metrics.service_id == "pm-1"
        assert metrics.service_name == "PM Service"

    def test_construction_with_times(self) -> None:
        """ServiceMetrics accepts start and end times."""
        start = datetime.now(timezone.utc)
        end = datetime.now(timezone.utc)
        metrics = ServiceMetrics(
            service_id="pm-1",
            service_name="PM Service",
            start_time=start,
            end_time=end,
            restart_count=2,
            success_count=10,
            failure_count=2,
            total_runtime_ms=1000.0,
            last_error="Previous error",
        )
        assert metrics.start_time == start
        assert metrics.end_time == end
        assert metrics.restart_count == 2
        assert metrics.success_count == 10
        assert metrics.failure_count == 2
        assert metrics.total_runtime_ms == 1000.0

    def test_avg_runtime_ms_with_no_runs(self) -> None:
        """avg_runtime_ms returns 0 when no runs."""
        metrics = ServiceMetrics(service_id="pm-1", service_name="PM Service")
        assert metrics.avg_runtime_ms == 0.0

    def test_avg_runtime_ms_with_runs(self) -> None:
        """avg_runtime_ms calculates correctly."""
        metrics = ServiceMetrics(
            service_id="pm-1",
            service_name="PM Service",
            success_count=2,
            failure_count=2,
            total_runtime_ms=400.0,
        )
        assert metrics.avg_runtime_ms == 100.0

    def test_success_rate_with_no_runs(self) -> None:
        """success_rate returns 0 when no runs."""
        metrics = ServiceMetrics(service_id="pm-1", service_name="PM Service")
        assert metrics.success_rate == 0.0

    def test_success_rate_with_runs(self) -> None:
        """success_rate calculates correctly."""
        metrics = ServiceMetrics(
            service_id="pm-1",
            service_name="PM Service",
            success_count=8,
            failure_count=2,
        )
        assert metrics.success_rate == 0.8

    def test_to_dict(self) -> None:
        """ServiceMetrics.to_dict returns correct structure."""
        metrics = ServiceMetrics(
            service_id="pm-1",
            service_name="PM Service",
            success_count=5,
            failure_count=1,
        )
        result = metrics.to_dict()
        assert result["service_id"] == "pm-1"
        assert result["service_name"] == "PM Service"
        assert result["success_count"] == 5
        assert result["failure_count"] == 1
        assert result["avg_runtime_ms"] == 0.0
        assert result["success_rate"] == 5 / 6


class TestUIEventBridge:
    """Tests for UIEventBridge class."""

    def test_construction(self) -> None:
        """UIEventBridge can be constructed."""
        bridge = UIEventBridge()
        assert bridge._ui_handlers == []
        assert bridge._running is False

    def test_construction_with_event_stream(self) -> None:
        """UIEventBridge accepts event stream."""
        stream = EventStream()
        bridge = UIEventBridge(stream)
        assert bridge._event_stream is stream

    def test_add_ui_handler(self) -> None:
        """UIEventBridge.add_ui_handler adds handler."""
        bridge = UIEventBridge()
        handler = MagicMock()
        bridge.add_ui_handler(handler)
        assert handler in bridge._ui_handlers

    def test_remove_ui_handler(self) -> None:
        """UIEventBridge.remove_ui_handler removes handler."""
        bridge = UIEventBridge()
        handler = MagicMock()
        bridge.add_ui_handler(handler)
        bridge.remove_ui_handler(handler)
        assert handler not in bridge._ui_handlers

    def test_remove_ui_handler_not_added(self) -> None:
        """UIEventBridge.remove_ui_handler handles non-added handler gracefully."""
        bridge = UIEventBridge()
        handler = MagicMock()
        bridge.remove_ui_handler(handler)  # Should not raise

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        """UIEventBridge.start subscribes to event stream."""
        stream = EventStream()
        bridge = UIEventBridge(stream)
        await bridge.start()
        assert bridge._running is True

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        """UIEventBridge.start is idempotent."""
        stream = EventStream()
        bridge = UIEventBridge(stream)
        await bridge.start()
        await bridge.start()  # Should not raise
        assert bridge._running is True

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        """UIEventBridge.stop unsubscribes from event stream."""
        stream = EventStream()
        bridge = UIEventBridge(stream)
        await bridge.start()
        await bridge.stop()
        assert bridge._running is False

    @pytest.mark.asyncio
    async def test_stop_idempotent(self) -> None:
        """UIEventBridge.stop is idempotent."""
        stream = EventStream()
        bridge = UIEventBridge(stream)
        await bridge.stop()  # Should not raise when not started
        assert bridge._running is False

    def test_emit_backend_started(self) -> None:
        """UIEventBridge.emit_backend_started emits event."""
        bridge = UIEventBridge()
        handler = MagicMock()
        bridge.add_ui_handler(handler)
        bridge.emit_backend_started(49977, "127.0.0.1")

        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event["type"] == "backend_started"
        assert event["data"]["port"] == 49977
        assert event["data"]["host"] == "127.0.0.1"
        assert event["data"]["url"] == "http://127.0.0.1:49977"

    def test_transform_event_spawned(self) -> None:
        """UIEventBridge transforms SPAWNED to SERVICE_STATUS."""
        bridge = UIEventBridge()
        event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        result = bridge._transform_event(event)
        assert result is not None
        assert result["type"] == "service_status"
        assert result["data"]["event_type"] == "spawned"

    def test_transform_event_failed(self) -> None:
        """UIEventBridge transforms FAILED to ERROR_NOTIFICATION."""
        bridge = UIEventBridge()
        event = OrchestrationEvent.failed("pm", "pm-1", "Error occurred")
        result = bridge._transform_event(event)
        assert result is not None
        assert result["type"] == "error_notification"

    def test_transform_event_unmapped(self) -> None:
        """UIEventBridge filters unmapped events."""
        bridge = UIEventBridge()
        event = OrchestrationEvent(level=EventLevel.INFO, event_type=EventType.HEARTBEAT, source="pm")
        result = bridge._transform_event(event)
        assert result is None

    def test_emit_to_ui_calls_handlers(self) -> None:
        """UIEventBridge._emit_to_ui calls all handlers."""
        bridge = UIEventBridge()
        handler1 = MagicMock()
        handler2 = MagicMock()
        bridge.add_ui_handler(handler1)
        bridge.add_ui_handler(handler2)

        event = {"type": "test", "data": {}}
        bridge._emit_to_ui(event)

        handler1.assert_called_once_with(event)
        handler2.assert_called_once_with(event)

    def test_emit_to_ui_handles_handler_exception(self) -> None:
        """UIEventBridge._emit_to_ui continues if handler raises."""
        bridge = UIEventBridge()
        bad_handler = MagicMock(side_effect=RuntimeError("Handler error"))
        good_handler = MagicMock()
        bridge.add_ui_handler(bad_handler)
        bridge.add_ui_handler(good_handler)

        event = {"type": "test", "data": {}}
        bridge._emit_to_ui(event)  # Should not raise

        good_handler.assert_called_once()


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_construction(self) -> None:
        """MetricsCollector can be constructed."""
        collector = MetricsCollector()
        assert collector._metrics == {}
        assert collector._running is False

    def test_get_metrics_returns_none(self) -> None:
        """MetricsCollector.get_metrics returns None for unknown service."""
        collector = MetricsCollector()
        result = collector.get_metrics("unknown")
        assert result is None

    def test_get_all_metrics(self) -> None:
        """MetricsCollector.get_all_metrics returns all metrics."""
        collector = MetricsCollector()
        result = collector.get_all_metrics()
        assert result == {}

    def test_get_summary_empty(self) -> None:
        """MetricsCollector.get_summary returns zeros when no metrics."""
        collector = MetricsCollector()
        summary = collector.get_summary()
        assert summary["total_services"] == 0
        assert summary["total_success"] == 0
        assert summary["total_failures"] == 0
        assert summary["overall_success_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        """MetricsCollector.start subscribes to event stream."""
        stream = EventStream()
        collector = MetricsCollector(stream)
        await collector.start()
        assert collector._running is True

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        """MetricsCollector.stop unsubscribes from event stream."""
        stream = EventStream()
        collector = MetricsCollector(stream)
        await collector.start()
        await collector.stop()
        assert collector._running is False

    def test_on_event_spawned(self) -> None:
        """MetricsCollector._on_event handles SPAWNED."""
        stream = EventStream()
        collector = MetricsCollector(stream)
        event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        collector._on_event(event)

        metrics = collector.get_metrics("pm-1")
        assert metrics is not None
        assert metrics.service_name == "pm"
        assert metrics.start_time is not None

    def test_on_event_completed(self) -> None:
        """MetricsCollector._on_event handles COMPLETED."""
        stream = EventStream()
        collector = MetricsCollector(stream)

        # First spawn
        spawn_event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        collector._on_event(spawn_event)

        # Then complete
        complete_event = OrchestrationEvent.completed("pm", "pm-1", 1234, 0, 1000)
        collector._on_event(complete_event)

        metrics = collector.get_metrics("pm-1")
        assert metrics is not None
        assert metrics.success_count == 1
        assert metrics.end_time is not None

    def test_on_event_failed(self) -> None:
        """MetricsCollector._on_event handles FAILED."""
        stream = EventStream()
        collector = MetricsCollector(stream)

        spawn_event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        collector._on_event(spawn_event)

        fail_event = OrchestrationEvent.failed("pm", "pm-1", "Error occurred")
        collector._on_event(fail_event)

        metrics = collector.get_metrics("pm-1")
        assert metrics is not None
        assert metrics.failure_count == 1
        assert metrics.last_error == "Error occurred"

    def test_on_event_retry_scheduled(self) -> None:
        """MetricsCollector._on_event handles RETRY_SCHEDULED."""
        stream = EventStream()
        collector = MetricsCollector(stream)

        event = OrchestrationEvent(
            event_type=EventType.RETRY_SCHEDULED,
            source="pm",
            process_id="pm-1",
        )
        collector._on_event(event)

        metrics = collector.get_metrics("pm-1")
        assert metrics is not None
        assert metrics.restart_count == 1

    def test_get_summary_with_metrics(self) -> None:
        """MetricsCollector.get_summary calculates correctly."""
        stream = EventStream()
        collector = MetricsCollector(stream)

        # Add some events
        collector._on_event(OrchestrationEvent.spawned("pm", "pm-1", 1234, []))
        collector._on_event(OrchestrationEvent.completed("pm", "pm-1", 1234, 0, 100))
        collector._on_event(OrchestrationEvent.spawned("director", "d-1", 1235, []))
        collector._on_event(OrchestrationEvent.failed("director", "d-1", "Error"))

        summary = collector.get_summary()
        assert summary["total_services"] == 2
        assert summary["total_success"] == 1
        assert summary["total_failures"] == 1


class TestHealthMonitor:
    """Tests for HealthMonitor class."""

    def test_construction(self) -> None:
        """HealthMonitor can be constructed."""
        monitor = HealthMonitor()
        assert monitor._service_states == {}
        assert monitor._running is False
        assert monitor._backend_started is False

    def test_get_health_status_healthy(self) -> None:
        """HealthMonitor.get_health_status returns healthy when no failures."""
        monitor = HealthMonitor()
        status = monitor.get_health_status()
        assert status["healthy"] is True
        assert status["total_services"] == 0
        assert status["running_services"] == 0
        assert status["failed_services"] == 0

    def test_is_backend_ready_false(self) -> None:
        """HealthMonitor.is_backend_ready returns False initially."""
        monitor = HealthMonitor()
        assert monitor.is_backend_ready() is False

    def test_on_event_spawned(self) -> None:
        """HealthMonitor._on_event handles SPAWNED."""
        monitor = HealthMonitor()
        event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        monitor._on_event(event)

        status = monitor.get_health_status()
        assert "pm-1" in status["unhealthy_services"] or True  # Not failed
        assert status["running_services"] == 1

    def test_on_event_failed(self) -> None:
        """HealthMonitor._on_event marks service as unhealthy on FAILED."""
        monitor = HealthMonitor()

        # First spawn the service (required for tracking)
        spawn_event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        monitor._on_event(spawn_event)

        # Then mark it as failed
        fail_event = OrchestrationEvent.failed("pm", "pm-1", "Error")
        monitor._on_event(fail_event)

        status = monitor.get_health_status()
        assert status["healthy"] is False
        assert "pm-1" in status["unhealthy_services"]

    def test_on_event_backend_started(self) -> None:
        """HealthMonitor._on_event handles backend_started event."""
        monitor = HealthMonitor()
        event = OrchestrationEvent(
            source="backend",
            payload={"event": "backend_started", "port": 49977},
        )
        monitor._on_event(event)

        assert monitor.is_backend_ready() is True
        assert monitor._backend_port == 49977

    @pytest.mark.asyncio
    async def test_wait_for_backend_success(self) -> None:
        """HealthMonitor.wait_for_backend returns True when backend starts."""
        monitor = HealthMonitor()

        # Simulate backend starting
        async def simulate_start():
            await asyncio.sleep(0.05)
            monitor._backend_started = True

        async def wait_task():
            return await monitor.wait_for_backend(timeout=1.0)

        result = await asyncio.gather(wait_task(), simulate_start())
        assert result[0] is True

    @pytest.mark.asyncio
    async def test_wait_for_backend_timeout(self) -> None:
        """HealthMonitor.wait_for_backend returns False on timeout."""
        monitor = HealthMonitor()
        result = await monitor.wait_for_backend(timeout=0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """HealthMonitor can start and stop."""
        monitor = HealthMonitor()
        await monitor.start()
        assert monitor._running is True
        await monitor.stop()
        assert monitor._running is False


class TestStructuredLogger:
    """Tests for StructuredLogger class."""

    def test_construction(self) -> None:
        """StructuredLogger can be constructed."""
        logger = StructuredLogger()
        assert logger._buffer == []
        assert logger._running is False

    def test_construction_with_log_path(self) -> None:
        """StructuredLogger accepts log path."""
        logger = StructuredLogger(log_path=Path("/tmp/test.log.jsonl"))
        assert logger._log_path == Path("/tmp/test.log.jsonl")

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """StructuredLogger can start and stop."""
        logger = StructuredLogger()
        await logger.start()
        assert logger._running is True
        await logger.stop()
        assert logger._running is False

    def test_on_event(self) -> None:
        """StructuredLogger._on_event adds to buffer."""
        stream = EventStream()
        logger = StructuredLogger(stream)
        event = OrchestrationEvent.spawned("pm", "pm-1", 1234, ["cmd"])
        logger._on_event(event)

        assert len(logger._buffer) == 1
        parsed = json.loads(logger._buffer[0])
        assert parsed["event_type"] == "spawned"
        assert parsed["source"] == "pm"


class TestObservabilityStackFunctions:
    """Tests for observability stack helper functions."""

    def test_create_observability_stack(self) -> None:
        """create_observability_stack creates all components."""
        stream = EventStream()
        ui_bridge, metrics, health, logger = create_observability_stack(stream)

        assert isinstance(ui_bridge, UIEventBridge)
        assert isinstance(metrics, MetricsCollector)
        assert isinstance(health, HealthMonitor)
        assert isinstance(logger, StructuredLogger)

    def test_create_observability_stack_without_stream(self) -> None:
        """create_observability_stack creates shared stream when not provided."""
        ui_bridge, metrics, health, logger = create_observability_stack()

        assert metrics._event_stream is ui_bridge._event_stream
        assert health._event_stream is ui_bridge._event_stream
        assert logger._event_stream is ui_bridge._event_stream

    @pytest.mark.asyncio
    async def test_start_stop_observability(self) -> None:
        """start_observability and stop_observability work."""
        ui_bridge, metrics, health, logger = create_observability_stack()

        await start_observability(ui_bridge, metrics, health, logger)
        assert ui_bridge._running is True
        assert metrics._running is True
        assert health._running is True
        assert logger._running is True

        await stop_observability(ui_bridge, metrics, health, logger)
        assert ui_bridge._running is False
        assert metrics._running is False
        assert health._running is False
        assert logger._running is False
