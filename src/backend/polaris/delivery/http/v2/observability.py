"""Observability API routes for V2 API.

Provides endpoints for:
- Real-time service status
- Metrics collection
- Health monitoring
- Event streaming (WebSocket)

Part of Phase 5 refactoring: Compatibility reinforcement and observability.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from polaris.cells.orchestration.workflow_runtime.public.service import (
    EventStream,
    HealthMonitor,
    MetricsCollector,
    RuntimeOrchestrator,
    StructuredLogger,
    UIEventBridge,
    create_observability_stack,
    start_observability,
)
from polaris.delivery.http.dependencies import get_workspace, require_auth
from polaris.kernelone.storage import resolve_runtime_path
from starlette.websockets import WebSocketState

if TYPE_CHECKING:
    from pathlib import Path

router = APIRouter(prefix="/observability", tags=["observability"])

# Global observability stack (initialized on first use)
_observability_initialized = False
_ui_bridge: UIEventBridge | None = None
_metrics_collector: MetricsCollector | None = None
_health_monitor: HealthMonitor | None = None
_structured_logger: StructuredLogger | None = None


async def _ensure_observability(workspace: Path) -> None:
    """Ensure observability stack is initialized."""
    global _observability_initialized, _ui_bridge, _metrics_collector, _health_monitor, _structured_logger

    if _observability_initialized:
        return

    # Create shared event stream
    event_stream = EventStream()

    # Create observability stack
    log_path = Path(resolve_runtime_path(str(workspace), "runtime/orchestration.log.jsonl"))
    _ui_bridge, _metrics_collector, _health_monitor, _structured_logger = create_observability_stack(
        event_stream=event_stream,
        log_path=log_path,
    )

    # Start all components
    await start_observability(_ui_bridge, _metrics_collector, _health_monitor, _structured_logger)

    _observability_initialized = True


@router.get("/status", dependencies=[Depends(require_auth)])
async def get_observability_status(
    workspace: Path = Depends(get_workspace),
) -> dict[str, Any]:
    """Get current observability status.

    Returns:
        Status information including health, active services, and metrics summary.
    """
    await _ensure_observability(workspace)

    health_status = _health_monitor.get_health_status() if _health_monitor else {}
    return {
        "healthy": health_status.get("healthy", False),
        "backend_ready": _health_monitor.is_backend_ready() if _health_monitor else False,
        "metrics_summary": _metrics_collector.get_summary() if _metrics_collector else {},
        "health_status": health_status,
    }


@router.get("/services", dependencies=[Depends(require_auth)])
async def list_services(
    workspace: Path = Depends(get_workspace),
) -> dict[str, Any]:
    """List all services tracked by observability.

    Returns:
        List of services with their current state and metrics.
    """
    await _ensure_observability(workspace)

    orchestrator = RuntimeOrchestrator()
    active_services = orchestrator.list_active()

    services = []
    for svc in active_services:
        metrics = _metrics_collector.get_metrics(svc.id) if _metrics_collector else None
        services.append(
            {
                "id": svc.id,
                "name": svc.definition.name,
                "state": svc.state.value,
                "running": svc.is_running,
                "metrics": metrics.to_dict() if metrics else None,
            }
        )

    return {
        "services": services,
        "total": len(services),
        "running": sum(1 for s in services if s["running"]),
    }


@router.get("/services/{service_id}", dependencies=[Depends(require_auth)])
async def get_service_details(
    service_id: str,
    workspace: Path = Depends(get_workspace),
) -> dict[str, Any]:
    """Get detailed information about a specific service.

    Args:
        service_id: Unique service identifier

    Returns:
        Service details including metrics and state.
    """
    await _ensure_observability(workspace)

    if not _metrics_collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")

    metrics = _metrics_collector.get_metrics(service_id)
    if not metrics:
        raise HTTPException(status_code=404, detail=f"Service {service_id} not found")

    return {
        "service_id": service_id,
        "metrics": metrics.to_dict(),
    }


@router.get("/metrics", dependencies=[Depends(require_auth)])
async def get_metrics(
    workspace: Path = Depends(get_workspace),
) -> dict[str, Any]:
    """Get aggregated metrics for all services.

    Returns:
        Summary metrics and per-service metrics.
    """
    await _ensure_observability(workspace)

    if not _metrics_collector:
        return {
            "summary": {},
            "services": {},
        }

    return {
        "summary": _metrics_collector.get_summary(),
        "services": {sid: m.to_dict() for sid, m in _metrics_collector.get_all_metrics().items()},
    }


@router.get("/health", dependencies=[Depends(require_auth)])
async def get_health(
    workspace: Path = Depends(get_workspace),
) -> dict[str, Any]:
    """Get health status.

    Returns:
        Health status including service states and backend readiness.
    """
    await _ensure_observability(workspace)

    if not _health_monitor:
        return {"healthy": False, "status": "unavailable"}

    return _health_monitor.get_health_status()


@router.get("/health/backend", dependencies=[Depends(require_auth)])
async def get_backend_health(
    workspace: Path = Depends(get_workspace),
) -> dict[str, Any]:
    """Check backend health specifically.

    Returns:
        Backend health status.
    """
    await _ensure_observability(workspace)

    if not _health_monitor:
        return {
            "ready": False,
            "status": "unavailable",
        }

    return {
        "ready": _health_monitor.is_backend_ready(),
        "status": _health_monitor.get_health_status(),
    }


@router.websocket("/ws/events")
async def observability_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time observability events.

    Streams UI events to connected clients for real-time status panels.
    """
    # WebSocket auth: validate bearer token before accepting
    auth = getattr(websocket.app.state, "auth", None)
    if auth is not None:
        auth_header = websocket.headers.get("authorization", "")
        if not auth.check(auth_header):
            await websocket.close(code=4001, reason="unauthorized")
            return
    await websocket.accept()

    # Create local event bridge for this connection
    event_stream = EventStream()
    ui_bridge = UIEventBridge(event_stream)

    # Handler to send events to WebSocket
    async def send_event(event: dict[str, Any]) -> None:
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(event)
        except (OSError, RuntimeError) as exc:
            logging.getLogger("polaris.observability.ws").debug(
                "WebSocket send failed (connection may be closed): %s", exc
            )

    # Add handler - wrap async function in sync wrapper for the bridge
    def sync_handler(event: dict[str, Any]) -> None:
        asyncio.create_task(send_event(event))

    ui_bridge.add_ui_handler(sync_handler)
    await ui_bridge.start()

    try:
        # Keep connection alive and handle client messages
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )
                # Handle ping/heartbeat
                data = json.loads(message)
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": data.get("timestamp")})
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "keepalive"})
    except WebSocketDisconnect:
        pass
    except (OSError, RuntimeError) as exc:
        logging.getLogger("polaris.observability.ws").warning("WebSocket handler ended unexpectedly: %s", exc)
    finally:
        await ui_bridge.stop()


@router.post("/metrics/export", dependencies=[Depends(require_auth)])
async def export_metrics(
    workspace: Path = Depends(get_workspace),
) -> dict[str, str]:
    """Export metrics to JSON file.

    Returns:
        Path to exported file.
    """
    await _ensure_observability(workspace)

    if not _metrics_collector:
        raise HTTPException(status_code=503, detail="Metrics collector not available")

    export_path = Path(resolve_runtime_path(str(workspace), "runtime/metrics_export.json"))
    _metrics_collector.export_json(export_path)

    return {
        "exported_to": str(export_path),
        "status": "success",
    }
