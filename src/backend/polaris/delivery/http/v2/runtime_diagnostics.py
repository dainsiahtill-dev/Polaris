"""Runtime diagnostics v2 delivery route."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.middleware.rate_limit import get_rate_limit_diagnostics
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.infrastructure.messaging.nats.client import get_default_client_snapshot
from polaris.infrastructure.messaging.nats.server_runtime import get_managed_nats_runtime_snapshot
from pydantic import BaseModel, Field

router = APIRouter(prefix="/runtime", tags=["runtime-diagnostics"])


class RuntimeDiagnosticSection(BaseModel):
    """One runtime diagnostic section."""

    state: str
    ok: bool | None
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class RuntimeDiagnosticsResponse(BaseModel):
    """Runtime diagnostics payload consumed by the desktop diagnostics panel."""

    schema_version: str = "runtime_diagnostics.v1"
    generated_at: str
    workspace: str
    nats: RuntimeDiagnosticSection
    websocket: RuntimeDiagnosticSection
    rate_limit: RuntimeDiagnosticSection


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nats_section(nats_config: Any, server_snapshot: dict[str, Any]) -> RuntimeDiagnosticSection:
    enabled = bool(getattr(nats_config, "enabled", True))
    required = bool(getattr(nats_config, "required", True))
    client_snapshot = get_default_client_snapshot()
    connected = bool(client_snapshot.get("is_connected"))
    tcp_reachable = bool(server_snapshot.get("tcp_reachable"))

    if not enabled:
        state = "disabled"
        ok = True
    elif connected:
        state = "connected"
        ok = True
    elif tcp_reachable:
        state = "server_reachable"
        ok = True
    elif required:
        state = "required_disconnected"
        ok = False
    else:
        state = "optional_disconnected"
        ok = True

    evidence = [
        str(path)
        for path in (
            server_snapshot.get("stdout_log_path"),
            server_snapshot.get("stderr_log_path"),
        )
        if path
    ]
    return RuntimeDiagnosticSection(
        state=state,
        ok=ok,
        details={
            "enabled": enabled,
            "required": required,
            "client": client_snapshot,
            "managed_server": server_snapshot,
        },
        evidence=evidence,
    )


def _websocket_section(request: Request) -> RuntimeDiagnosticSection:
    connection_state = getattr(request.app.state, "connection_state", None)
    active = int(getattr(connection_state, "active_connections", 0) or 0) if connection_state is not None else 0
    total = int(getattr(connection_state, "total_connections", 0) or 0) if connection_state is not None else 0
    last_error = str(getattr(connection_state, "last_error", "") or "") if connection_state is not None else ""
    state = "active" if active > 0 else "idle"
    if last_error:
        state = "last_error"

    return RuntimeDiagnosticSection(
        state=state,
        ok=not bool(last_error),
        details={
            "endpoint": "/v2/ws/runtime",
            "active_connections": active,
            "total_connections": total,
            "last_connection_id": str(getattr(connection_state, "last_connection_id", "") or "")
            if connection_state is not None
            else "",
            "last_event": str(getattr(connection_state, "last_event", "") or "")
            if connection_state is not None
            else "",
            "last_error": last_error,
            "last_updated_at": float(getattr(connection_state, "last_updated_at", 0.0) or 0.0)
            if connection_state is not None
            else 0.0,
            "channels": sorted(getattr(connection_state, "channels", set()) or [])
            if connection_state is not None
            else [],
            "want_status": bool(getattr(connection_state, "want_status", False))
            if connection_state is not None
            else False,
            "tail_channels": sorted((getattr(connection_state, "tail_state", {}) or {}).keys())
            if connection_state is not None
            else [],
        },
        evidence=["KernelAuditRuntime: ws.connection events", "runtimeSocketManager frontend state"],
    )


def _rate_limit_section() -> RuntimeDiagnosticSection:
    diagnostics = get_rate_limit_diagnostics()
    store = diagnostics.get("store") if isinstance(diagnostics.get("store"), dict) else {}
    blocked_count = int(store.get("blocked_count", 0) or 0)
    enabled = bool(diagnostics.get("enabled"))
    if not enabled:
        state = "disabled"
    elif blocked_count > 0:
        state = "blocking"
    else:
        state = "active"

    return RuntimeDiagnosticSection(
        state=state,
        ok=blocked_count == 0,
        details=diagnostics,
        evidence=["RateLimitMiddleware token bucket snapshot"],
    )


@router.get(
    "/diagnostics",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeDiagnosticsResponse,
)
async def get_runtime_diagnostics(request: Request) -> RuntimeDiagnosticsResponse:
    """Return a side-effect-free diagnostics snapshot for desktop troubleshooting."""

    state = get_state(request)
    nats_config = getattr(state.settings, "nats", None)
    nats_url = str(getattr(nats_config, "url", "") or "")
    server_snapshot = await asyncio.to_thread(get_managed_nats_runtime_snapshot, nats_url)
    return RuntimeDiagnosticsResponse(
        generated_at=_utc_now(),
        workspace=str(getattr(state.settings, "workspace", "") or getattr(state.settings, "workspace_path", "") or ""),
        nats=_nats_section(nats_config, server_snapshot),
        websocket=_websocket_section(request),
        rate_limit=_rate_limit_section(),
    )


__all__ = ["router"]
