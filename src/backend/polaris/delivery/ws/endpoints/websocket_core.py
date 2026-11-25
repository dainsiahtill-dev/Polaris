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
from pathlib import Path
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

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState, Auth
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

logger = logging.getLogger(__name__)


class WorkspaceBindingError(RuntimeError):
    """WebSocket 的 workspace 绑定无法确定或相互冲突。"""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def resolve_ws_workspace_binding(
    *,
    settings_workspace: str,
    query_workspace: str,
    ramdisk_root: str,
    default_workspace: str,
) -> tuple[Any, str, bool]:
    """决定 /v2/ws/runtime 用哪个 workspace 投影，绝不静默回落 cwd。

    旧版先拿 settings_workspace 去 resolve；为空时
    resolve_workspace_runtime_context 会回落到 default_workspace（= 进程 cwd，
    通常是主仓 src/backend），于是 owner 查询拿主仓路径去校验
    KERNELONE_RUNTIME_ROOT（指向真实 workspace）→ fail-closed →
    director_status.running=false + projection_error，前端"运行实况"整片空白。
    实测主实例与 audit 实例同时中招。CLAUDE.md 明文禁止这种静默回退：
    "前端 API 与 /v2/ws/runtime 必须使用该 workspace 绑定，禁止静默回退到
     默认 backend、默认 workspace 或主仓 runtime"。

    判定：进程绑定优先；进程未绑定则客户端绑定是唯一权威；两者都有且不一致
    则拒绝；两者都无则拒绝。
    """

    requested_workspace = str(Path(query_workspace).expanduser().resolve()) if query_workspace else ""
    if settings_workspace:
        settings_ctx = resolve_workspace_runtime_context(
            configured_workspace=settings_workspace,
            default_workspace=default_workspace,
            ramdisk_root=ramdisk_root,
        )
        # 只比归一化路径：resolve 对方会先撞它自己的 runtime-root 校验，
        # 把"绑定冲突"误报成"root 不匹配"。
        if requested_workspace and requested_workspace != settings_ctx.workspace:
            raise WorkspaceBindingError(4401, "workspace binding conflict")
        # 第三个返回值是"是否冲突"，不是"是否带了 query" —— 调用方据此发 1008。
        return settings_ctx, requested_workspace, False
    if requested_workspace:
        # 进程未绑定：客户端绑定是唯一权威，绝不回落 cwd。
        query_ctx = resolve_workspace_runtime_context(
            configured_workspace=query_workspace,
            default_workspace=default_workspace,
            ramdisk_root=ramdisk_root,
        )
        return query_ctx, requested_workspace, False
    raise WorkspaceBindingError(4400, "workspace binding required")


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
    # 直接调用（非经 FastAPI）时 workspace 可能是 Query 哨兵对象；只有真正的
    # 字符串才算客户端绑定。
    query_workspace = workspace.strip() if isinstance(workspace, str) else ""
    settings_workspace = str(getattr(state.settings, "workspace", "") or "").strip()
    ramdisk_root = str(getattr(state.settings, "ramdisk_root", "") or "").strip()
    try:
        workspace_ctx, requested_workspace, workspace_binding_mismatch = resolve_ws_workspace_binding(
            settings_workspace=settings_workspace,
            query_workspace=query_workspace,
            ramdisk_root=ramdisk_root,
            default_workspace=DEFAULT_WORKSPACE,
        )
    except WorkspaceBindingError as error:
        await websocket.close(code=error.code, reason=error.reason)
        return
    resolved_workspace = workspace_ctx.workspace
    # DirectorService 是单例，可能仍绑在启动时的 workspace 上。HTTP 依赖会在每个
    # 请求前按请求 workspace rebind，WS 路径没有这一步 —— 于是 owner 查询拿旧绑定
    # （往往是进程 cwd）去校验 KERNELONE_RUNTIME_ROOT → fail-closed →
    # director_status.running=false + projection_error，前端"运行实况"全空。
    try:
        from polaris.cells.director.execution.public import rebind_director_service

        await rebind_director_service(resolved_workspace)
    except Exception as exc:  # noqa: BLE001 - rebind 失败不该拖垮连接，状态会如实报错
        logger.warning("ws director rebind failed for %s: %s", resolved_workspace, exc)
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

    if workspace_binding_mismatch:
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "workspace_rejected",
            {
                "client": client,
                "requested_workspace": requested_workspace,
                "reason": "workspace_binding_mismatch",
                **workspace_details,
            },
        )
        await websocket.close(code=1008)
        await _log_connection_event(
            resolved_workspace,
            cache_root,
            connection_id,
            "closed",
            {
                "close_code": 1008,
                "reason": "workspace_binding_mismatch",
                "client": client,
                **workspace_details,
            },
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
    async def build_status() -> tuple[str, dict[str, Any]]:
        from polaris.delivery.ws.endpoints.helpers import status_signature

        payload = await build_status_payload(state, resolved_workspace, cache_root, roles_filter)
        sig = status_signature(payload)
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
            build_status_func=build_status,
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
