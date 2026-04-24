"""Observability layer for Polaris orchestration.

This module provides EventStream integration for UI real-time status panels,
structured logging, and metrics collection as part of Phase 5 refactoring.

Architecture:
    - EventStream: Core event bus (orchestration/event_stream.py)
    - UIEventBridge: Bridges orchestration events to UI/WebSocket
    - MetricsCollector: Aggregates runtime metrics
    - HealthMonitor: Service health checks
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from polaris.cells.orchestration.workflow_runtime.internal.event_stream import (
    EventStream,
    EventType,
    OrchestrationEvent,
)
from polaris.kernelone.constants import DEFAULT_TELEMETRY_BUFFER_SIZE
from polaris.kernelone.fs.text_ops import open_text_log_append, write_text_atomic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_logger = logging.getLogger(__name__)


class UIEventType(Enum):
    """UI-facing event types for real-time status panels."""

    SERVICE_STATUS = "service_status"
    SERVICE_LOG = "service_log"
    TASK_PROGRESS = "task_progress"
    SYSTEM_METRICS = "system_metrics"
    ERROR_NOTIFICATION = "error_notification"
    HEALTH_STATUS = "health_status"
    BACKEND_STARTED = "backend_started"


@dataclass
class ServiceMetrics:
    """Metrics for a single service."""

    service_id: str
    service_name: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    restart_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_runtime_ms: float = 0.0
    last_error: str | None = None

    @property
    def avg_runtime_ms(self) -> float:
        """Calculate average runtime."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.total_runtime_ms / total

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "service_id": self.service_id,
            "service_name": self.service_name,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "restart_count": self.restart_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "avg_runtime_ms": self.avg_runtime_ms,
            "success_rate": self.success_rate,
            "last_error": self.last_error,
        }


class UIEventBridge:
    """Bridge orchestration events to UI/WebSocket consumers.

    This class subscribes to EventStream and transforms orchestration events
    into UI-friendly formats for real-time status panels.

    Example:
        >>> bridge = UIEventBridge(event_stream)
        >>> bridge.add_ui_handler(lambda event: websocket.send(json.dumps(event)))
        >>> await bridge.start()
    """

    def __init__(self, event_stream: EventStream | None = None) -> None:
        """Initialize UI event bridge.

        Args:
            event_stream: Event stream to subscribe to (creates new if None)
        """
        self._event_stream = event_stream or EventStream()
        self._ui_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._subscription_callback: Callable[[OrchestrationEvent], None] | None = None
        self._running = False

    def add_ui_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Add a UI event handler.

        Args:
            handler: Callback function that receives UI events
        """
        self._ui_handlers.append(handler)

    def remove_ui_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Remove a UI event handler."""
        if handler in self._ui_handlers:
            self._ui_handlers.remove(handler)

    async def start(self) -> None:
        """Start bridging events to UI."""
        if self._running:
            return

        self._subscription_callback = self._on_event
        self._event_stream.subscribe(self._subscription_callback)
        self._running = True

    async def stop(self) -> None:
        """Stop bridging events."""
        if not self._running:
            return

        if self._subscription_callback is not None:
            self._event_stream.unsubscribe(self._subscription_callback)
            self._subscription_callback = None

        self._running = False

    def _on_event(self, event: OrchestrationEvent) -> None:
        """Process orchestration event and emit UI events."""
        ui_event = self._transform_event(event)
        if ui_event:
            self._emit_to_ui(ui_event)

    def _transform_event(self, event: OrchestrationEvent) -> dict[str, Any] | None:
        """Transform orchestration event to UI event.

        Args:
            event: Raw orchestration event

        Returns:
            UI-friendly event dict or None if should be filtered
        """
        # Map EventType to UIEventType
        type_map = {
            EventType.SPAWNED: UIEventType.SERVICE_STATUS,
            EventType.COMPLETED: UIEventType.SERVICE_STATUS,
            EventType.FAILED: UIEventType.ERROR_NOTIFICATION,
            EventType.TERMINATED: UIEventType.SERVICE_STATUS,
            EventType.RETRY_SCHEDULED: UIEventType.SERVICE_STATUS,
            EventType.RETRY_EXHAUSTED: UIEventType.ERROR_NOTIFICATION,
        }

        ui_type = type_map.get(event.event_type)
        if not ui_type:
            return None

        return {
            "type": ui_type.value,
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "level": event.level.value,
            "data": {
                "event_type": event.event_type.value,
                "service_id": event.process_id,
                "payload": event.payload,
            },
        }

    def _emit_to_ui(self, event: dict[str, Any]) -> None:
        """Emit event to all UI handlers."""
        for handler in self._ui_handlers:
            try:
                handler(event)
            except (RuntimeError, ValueError) as exc:
                # Don't let UI handler failures break event flow
                _logger.debug("UI handler raised (non-critical): %s", exc)

    def emit_backend_started(self, port: int, host: str = "127.0.0.1") -> None:
        """Emit backend_started event for Electron integration.

        Args:
            port: Backend port
            host: Backend host
        """
        event = {
            "type": UIEventType.BACKEND_STARTED.value,
            "timestamp": datetime.now().isoformat(),
            "source": "backend",
            "level": "info",
            "data": {
                "port": port,
                "host": host,
                "url": f"http://{host}:{port}",
            },
        }
        self._emit_to_ui(event)


class MetricsCollector:
    """Collect and aggregate runtime metrics from EventStream.

    Example:
        >>> collector = MetricsCollector(event_stream)
        >>> await collector.start()
        >>> # ... run services ...
        >>> metrics = collector.get_metrics("pm")
        >>> print(f"Success rate: {metrics.success_rate:.1%}")
    """

    def __init__(self, event_stream: EventStream | None = None) -> None:
        """Initialize metrics collector.

        Args:
            event_stream: Event stream to subscribe to
        """
        self._event_stream = event_stream or EventStream()
        self._metrics: dict[str, ServiceMetrics] = {}
        self._subscription_callback: Callable[[OrchestrationEvent], None] | None = None
        self._running = False
        self._start_times: dict[str, datetime] = {}

    async def start(self) -> None:
        """Start collecting metrics."""
        if self._running:
            return

        self._subscription_callback = self._on_event
        self._event_stream.subscribe(self._subscription_callback)
        self._running = True

    async def stop(self) -> None:
        """Stop collecting metrics."""
        if not self._running:
            return

        if self._subscription_callback is not None:
            self._event_stream.unsubscribe(self._subscription_callback)
            self._subscription_callback = None

        self._running = False

    def _on_event(self, event: OrchestrationEvent) -> None:
        """Process event for metrics."""
        service_id = event.process_id or event.source
        service_name = event.source

        # Get or create metrics
        if service_id not in self._metrics:
            self._metrics[service_id] = ServiceMetrics(
                service_id=service_id,
                service_name=service_name,
            )

        metrics = self._metrics[service_id]

        # Update based on event type
        if event.event_type == EventType.SPAWNED:
            self._start_times[service_id] = event.timestamp
            metrics.start_time = event.timestamp

        elif event.event_type == EventType.COMPLETED:
            metrics.end_time = event.timestamp
            metrics.success_count += 1

            # Calculate runtime
            if service_id in self._start_times:
                duration = (event.timestamp - self._start_times[service_id]).total_seconds()
                metrics.total_runtime_ms += duration * 1000
                del self._start_times[service_id]

        elif event.event_type == EventType.FAILED:
            metrics.end_time = event.timestamp
            metrics.failure_count += 1
            if event.payload.get("error"):
                metrics.last_error = event.payload["error"]

            if service_id in self._start_times:
                del self._start_times[service_id]

        elif event.event_type == EventType.RETRY_SCHEDULED:
            metrics.restart_count += 1

    def get_metrics(self, service_id: str) -> ServiceMetrics | None:
        """Get metrics for a service.

        Args:
            service_id: Service ID

        Returns:
            Service metrics or None
        """
        return self._metrics.get(service_id)

    def get_all_metrics(self) -> dict[str, ServiceMetrics]:
        """Get all collected metrics."""
        return dict(self._metrics)

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics."""
        if not self._metrics:
            return {
                "total_services": 0,
                "total_success": 0,
                "total_failures": 0,
                "overall_success_rate": 0.0,
            }

        total_success = sum(m.success_count for m in self._metrics.values())
        total_failures = sum(m.failure_count for m in self._metrics.values())
        total = total_success + total_failures

        return {
            "total_services": len(self._metrics),
            "total_success": total_success,
            "total_failures": total_failures,
            "total_restarts": sum(m.restart_count for m in self._metrics.values()),
            "overall_success_rate": total_success / total if total > 0 else 0.0,
        }

    def export_json(self, path: Path) -> None:
        """Export metrics to JSON file.

        Args:
            path: Output file path
        """
        data = {
            "exported_at": datetime.now().isoformat(),
            "summary": self.get_summary(),
            "services": {sid: m.to_dict() for sid, m in self._metrics.items()},
        }

        write_text_atomic(str(path), json.dumps(data, indent=2, ensure_ascii=False) + "\n")


class HealthMonitor:
    """Monitor health of services and backend.

    Provides health checks for Electron startup protocol stability.

    Example:
        >>> monitor = HealthMonitor(event_stream)
        >>> await monitor.start()
        >>> status = monitor.get_health_status()
        >>> if not status["healthy"]:
        ...     print(f"Unhealthy services: {status['unhealthy_services']}")
    """

    def __init__(self, event_stream: EventStream | None = None) -> None:
        """Initialize health monitor.

        Args:
            event_stream: Event stream to subscribe to
        """
        self._event_stream = event_stream or EventStream()
        self._service_states: dict[str, dict[str, Any]] = {}
        self._subscription_callback: Callable[[OrchestrationEvent], None] | None = None
        self._running = False
        self._backend_started = False
        self._backend_port: int | None = None

    async def start(self) -> None:
        """Start health monitoring."""
        if self._running:
            return

        self._subscription_callback = self._on_event
        self._event_stream.subscribe(self._subscription_callback)
        self._running = True

    async def stop(self) -> None:
        """Stop health monitoring."""
        if not self._running:
            return

        if self._subscription_callback is not None:
            self._event_stream.unsubscribe(self._subscription_callback)
            self._subscription_callback = None

        self._running = False

    def _on_event(self, event: OrchestrationEvent) -> None:
        """Process event for health status."""
        service_id = event.process_id or event.source

        if event.event_type == EventType.SPAWNED:
            self._service_states[service_id] = {
                "state": "running",
                "started_at": event.timestamp.isoformat(),
                "pid": event.payload.get("pid"),
            }

        elif event.event_type == EventType.COMPLETED:
            if service_id in self._service_states:
                self._service_states[service_id]["state"] = "completed"
                self._service_states[service_id]["completed_at"] = event.timestamp.isoformat()

        elif event.event_type == EventType.FAILED:
            if service_id in self._service_states:
                self._service_states[service_id]["state"] = "failed"
                self._service_states[service_id]["error"] = event.payload.get("error")

        elif event.event_type == EventType.TERMINATED and service_id in self._service_states:
            self._service_states[service_id]["state"] = "terminated"

        # Check for backend_started event (Electron protocol)
        if event.source == "backend" and event.payload.get("event") == "backend_started":
            self._backend_started = True
            self._backend_port = event.payload.get("port")

    def get_health_status(self) -> dict[str, Any]:
        """Get current health status.

        Returns:
            Health status dictionary
        """
        running = [s for s in self._service_states.values() if s["state"] == "running"]
        failed = [s for s in self._service_states.values() if s["state"] == "failed"]

        return {
            "healthy": len(failed) == 0,
            "timestamp": datetime.now().isoformat(),
            "total_services": len(self._service_states),
            "running_services": len(running),
            "failed_services": len(failed),
            "unhealthy_services": [sid for sid, s in self._service_states.items() if s["state"] == "failed"],
            "backend_ready": self._backend_started,
            "backend_port": self._backend_port,
        }

    async def wait_for_backend(self, timeout: float = 30.0) -> bool:
        """Wait for backend to be ready (Electron protocol stability).

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if backend started, False on timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._backend_started:
                return True
            await asyncio.sleep(0.1)
        return False

    def is_backend_ready(self) -> bool:
        """Check if backend is ready."""
        return self._backend_started


class StructuredLogger:
    """Structured JSON logger for orchestration events.

    Outputs JSONL format for log aggregation and analysis.

    Example:
        >>> logger = StructuredLogger(event_stream, "runtime/orchestration.log.jsonl")
        >>> await logger.start()
    """

    def __init__(
        self,
        event_stream: EventStream | None = None,
        log_path: Path | None = None,
    ) -> None:
        """Initialize structured logger.

        Args:
            event_stream: Event stream to subscribe to
            log_path: Path to log file (JSONL format)
        """
        self._event_stream = event_stream or EventStream()
        self._log_path = log_path
        self._subscription_callback: Callable[[OrchestrationEvent], None] | None = None
        self._running = False
        self._buffer: list[str] = []
        self._flush_interval = 1.0  # seconds
        self._max_buffer_size = DEFAULT_TELEMETRY_BUFFER_SIZE

    async def start(self) -> None:
        """Start logging."""
        if self._running:
            return

        self._subscription_callback = self._on_event
        self._event_stream.subscribe(self._subscription_callback)
        self._running = True

        # Start flush task
        _ = asyncio.create_task(self._flush_loop())  # noqa: RUF006

    async def stop(self) -> None:
        """Stop logging and flush remaining events."""
        if not self._running:
            return

        if self._subscription_callback is not None:
            self._event_stream.unsubscribe(self._subscription_callback)
            self._subscription_callback = None

        self._running = False
        await self._flush()

    def _on_event(self, event: OrchestrationEvent) -> None:
        """Process event for logging."""
        record = {
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type.value,
            "level": event.level.value,
            "source": event.source,
            "process_id": event.process_id,
            "pid": event.pid,
            "payload": event.payload,
        }

        self._buffer.append(json.dumps(record, ensure_ascii=False))

        # Flush if buffer is full
        if len(self._buffer) >= self._max_buffer_size:
            _ = asyncio.create_task(self._flush())  # noqa: RUF006

    async def _flush_loop(self) -> None:
        """Periodic flush task."""
        while self._running:
            await asyncio.sleep(self._flush_interval)
            if self._buffer:
                await self._flush()

    async def _flush(self) -> None:
        """Flush buffer to file."""
        if not self._buffer or not self._log_path:
            return

        try:
            # Ensure directory exists
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

            # Append to file using KernelOne text_ops
            handle = open_text_log_append(str(self._log_path))
            try:
                for line in self._buffer:
                    handle.write(line + "\n")
            finally:
                handle.close()

            self._buffer.clear()
        except (RuntimeError, ValueError) as exc:
            # Don't let logging failures break orchestration
            _logger.debug("buffer flush failed (non-critical): %s", exc)


# Convenience functions for integration


def create_observability_stack(
    event_stream: EventStream | None = None,
    log_path: Path | None = None,
) -> tuple[UIEventBridge, MetricsCollector, HealthMonitor, StructuredLogger]:
    """Create complete observability stack.

    Args:
        event_stream: Shared event stream (creates new if None)
        log_path: Path for structured logging

    Returns:
        Tuple of (ui_bridge, metrics_collector, health_monitor, logger)
    """
    stream = event_stream or EventStream()

    ui_bridge = UIEventBridge(stream)
    metrics = MetricsCollector(stream)
    health = HealthMonitor(stream)
    logger = StructuredLogger(stream, log_path)

    return ui_bridge, metrics, health, logger


async def start_observability(
    ui_bridge: UIEventBridge,
    metrics: MetricsCollector,
    health: HealthMonitor,
    logger: StructuredLogger,
) -> None:
    """Start all observability components."""
    await ui_bridge.start()
    await metrics.start()
    await health.start()
    await logger.start()


async def stop_observability(
    ui_bridge: UIEventBridge,
    metrics: MetricsCollector,
    health: HealthMonitor,
    logger: StructuredLogger,
) -> None:
    """Stop all observability components."""
    await ui_bridge.stop()
    await metrics.stop()
    await health.stop()
    await logger.stop()
