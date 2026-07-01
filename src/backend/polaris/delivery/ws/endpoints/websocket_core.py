"""Core WebSocket endpoint for runtime streaming.

This module contains the main runtime_websocket endpoint function
and connection lifecycle management.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from fastapi import Query, WebSocket, WebSocketDisconnect
from polaris.cells.audit.diagnosis.public.service import write_ws_connection_event
from polaris.cells.runtime.projection.public.service import (
    DEFAULT_WORKSPACE,
    resolve_workspace_runtime_context,
)
from polaris.delivery.ws.endpoints.helpers import normalize_roles
from polaris.delivery.ws.endpoints.models import WebSocketSendError, is_websocket_disconnect_runtime_error
from polaris.delivery.ws.endpoints.protocol import build_status_payload
from polaris.delivery.ws.endpoints.stream import send_json_safe

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState, Auth
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

logger = logging.getLogger(__name__)


async def runtime_websocket(
    websocket: WebSocket,
    roles: str | None = Query(None, description="Comma-separated roles: pm,director,qa"),
    workspace: str | None = Query(None, description="Workspace path override"),
) -> None:
    """Main runtime WebSocket endpoint.

    Provides runtime.v2 WebSocket access. Product realtime events are delivered
    exclusively through Nats-JetStream consumers; file-watch/process-local
    realtime adapters are not registered by this endpoint.
    """
    auth: Auth = websocket.app.state.auth
    state: AppState = websocket.app.state.app_state

    # Resolve workspace context
    connection_id = uuid.uuid4().hex
    query_workspace = str(workspace or "").strip()
    settings_workspace = str(getattr(state.settings, "workspace", "") or "").strip()
    configured_workspace = query_workspace or settings_workspace
    ramdisk_root = str(getattr(state.settings, "ramdisk_root", "") or "").strip()
    workspace_ctx = resolve_workspace_runtime_context(
        configured_workspace=configured_workspace,
        default_workspace=DEFAULT_WORKSPACE,
        ramdisk_root=ramdisk_root,
    )
    resolved_workspace = workspace_ctx.workspace
    cache_root = workspace_ctx.runtime_root
    workspace_details = {
        "workspace": resolved_workspace,
        "workspace_key": workspace_ctx.workspace_key,
        "runtime_root": workspace_ctx.runtime_root,
        "runtime_base": workspace_ctx.runtime_base,
        "workspace_source": workspace_ctx.source,
    }
    client = f"{getattr(websocket.client, 'host', '')}:{getattr(websocket.client, 'port', '')}".strip(":")
    ws_token = websocket.query_params.get("token")

    # Accept connection and log
    await websocket.accept()
    await _log_connection_event(
        resolved_workspace,
        cache_root,
        connection_id,
        "accepted",
        {
            "client": client,
            "roles_query": str(roles or "").strip(),
            "token_present": bool(ws_token),
            **workspace_details,
        },
    )

    # Handle workspace resolution logging
    if workspace_ctx.source != "settings":
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "workspace_resolved",
            {"client": client, "workspace_source": workspace_ctx.source, **workspace_details},
        )

    # Auth check
    if not auth.check(f"Bearer {ws_token}"):
        await _log_connection_event(
            resolved_workspace, cache_root, connection_id, "auth_rejected", {"client": client, **workspace_details}
        )
        await websocket.close(code=1008)
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "closed",
            {"close_code": 1008, "reason": "auth_rejected", "client": client, **workspace_details},
        )
        return

    # Initialize connection state
    roles_filter = normalize_roles(roles)
    tail_lines = 200
    v2_protocol: str | None = None
    v2_consumer_manager: JetStreamConsumerManager | None = None
    v2_client_id: str = ""
    v2_channels: list[str] = []
    v2_cursor: int = 0

    # Log open event
    await _log_connection_event(
        resolved_workspace,
        cache_root,
        connection_id,
        "open",
        {"client": client, "roles_filter": sorted(roles_filter), **workspace_details},
    )

    connection_state = getattr(websocket.app.state, "connection_state", None)
    if connection_state is not None:
        connection_state.active_connections = max(0, int(getattr(connection_state, "active_connections", 0))) + 1
        connection_state.total_connections = int(getattr(connection_state, "total_connections", 0)) + 1
        connection_state.last_connection_id = connection_id
        connection_state.last_event = "open"
        connection_state.last_error = ""
        connection_state.last_updated_at = time.time()

    # Create helper functions for main loop
    async def send_status(force: bool = False, last_sig: str = "") -> tuple[str, dict[str, Any]]:
        from polaris.delivery.ws.endpoints.helpers import status_signature

        payload = await build_status_payload(state, resolved_workspace, cache_root, roles_filter)
        sig = status_signature(payload)
        if (force or sig != last_sig) and not await send_json_safe(
            websocket, payload, connection_id, client, resolved_workspace
        ):
            raise WebSocketSendError("send_failed", "Failed to send status")
        return sig, payload

    # Run main loop (imported from websocket_loop.py)
    from polaris.delivery.ws.endpoints.websocket_loop import run_main_loop

    close_code: int | None = None
    close_reason = ""
    try:
        close_code, close_reason = await run_main_loop(
            websocket=websocket,
            state=state,
            resolved_workspace=resolved_workspace,
            cache_root=cache_root,
            roles_filter=roles_filter,
            connection_id=connection_id,
            client=client,
            tail_lines=tail_lines,
            v2_protocol=v2_protocol,
            v2_consumer_manager=v2_consumer_manager,
            v2_client_id=v2_client_id,
            v2_channels=v2_channels,
            v2_cursor=v2_cursor,
            send_status_func=send_status,
        )
    except WebSocketDisconnect as exc:
        close_code = getattr(exc, "code", None)
        close_reason = f"websocket_disconnect:{getattr(exc, 'reason', '')}"
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "disconnect",
            {"client": client, "close_code": close_code, **workspace_details},
        )
    except WebSocketSendError as exc:
        close_reason = f"{exc.error_type}:{exc.message}"
        if connection_state is not None:
            connection_state.last_event = "send_error"
            connection_state.last_error = close_reason
            connection_state.last_updated_at = time.time()
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "send_error",
            {"client": client, "error_type": exc.error_type, "error": exc.message, **workspace_details},
        )
    except (RuntimeError, ValueError) as exc:
        close_reason = f"{type(exc).__name__}:{exc!s}"
        if isinstance(exc, RuntimeError) and is_websocket_disconnect_runtime_error(exc):
            close_code = 1001
            close_reason = f"client_disconnect:{exc!s}"
            if connection_state is not None:
                connection_state.last_event = "disconnect"
                connection_state.last_error = ""
                connection_state.last_updated_at = time.time()
            await _log_connection_event(
                resolved_workspace,
                cache_root,
                connection_id,
                "disconnect",
                {"client": client, "close_code": close_code, "reason": str(exc), **workspace_details},
            )
            return
        if connection_state is not None:
            connection_state.last_event = "error"
            connection_state.last_error = close_reason
            connection_state.last_updated_at = time.time()
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "error",
            {"client": client, "error_type": type(exc).__name__, "error": str(exc), **workspace_details},
        )
    finally:
        if connection_state is not None:
            connection_state.active_connections = max(
                0,
                int(getattr(connection_state, "active_connections", 0)) - 1,
            )
            connection_state.last_event = "closed"
            connection_state.last_updated_at = time.time()

        # Cleanup — each step guarded so one failure doesn't block others.
        async def _safe_cleanup(coro: Any, label: str) -> None:
            try:
                await asyncio.shield(coro)
            except Exception as exc:  # noqa: BLE001
                logger.debug("WS cleanup %s failed for %s: %s", label, connection_id, exc)

        with suppress(Exception):
            await asyncio.shield(
                _log_connection_event(
                    resolved_workspace,
                    cache_root,
                    connection_id,
                    "closed",
                    {
                        "client": client,
                        "close_code": close_code,
                        "reason": close_reason,
                        "v2_protocol": v2_protocol,
                        **workspace_details,
                    },
                )
            )

        if v2_consumer_manager:
            await _safe_cleanup(v2_consumer_manager.disconnect(), "v2_consumer_disconnect")


async def _log_connection_event(
    workspace: str, cache_root: str, connection_id: str, event: str, details: dict[str, Any]
) -> None:
    """Helper to log connection events."""
    await write_ws_connection_event(
        workspace=workspace,
        cache_root=cache_root,
        endpoint="/v2/ws/runtime",
        connection_id=connection_id,
        event=event,
        details=details,
    )


__all__ = ["runtime_websocket"]
