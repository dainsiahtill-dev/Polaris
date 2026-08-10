from __future__ import annotations

# mypy: disable-error-code="attr-defined,union-attr,arg-type,return,assignment,has-type,misc"
import asyncio
import contextlib
import logging
from typing import Any

from ._connection import RuntimeProjectionConnectionMixin
from ._local import RuntimeProjectionLocalMixin
from ._panels import RuntimeProjectionPanelsMixin
from ._runtime_v2 import RuntimeProjectionRuntimeV2Mixin
from ._taskboard import RuntimeProjectionTaskboardMixin

logger = logging.getLogger("observer.projection")


class RuntimeProjection(
    RuntimeProjectionTaskboardMixin,
    RuntimeProjectionConnectionMixin,
    RuntimeProjectionLocalMixin,
    RuntimeProjectionPanelsMixin,
    RuntimeProjectionRuntimeV2Mixin,
):
    """实时投影订阅系统（仅 WS runtime.v2 / JetStream）。"""

    def __init__(
        self,
        backend_url: str,
        token: str,
        workspace: str,
        transport: str = "ws",
        focus: str = "all",
    ) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.token = token
        self.workspace = self._normalize_workspace_value(workspace)
        self.transport = transport.lower()
        self.focus = focus.lower()
        self.ws_url = ""
        self._refresh_connection_urls()

        self.panels: dict[str, list[dict[str, Any]]] = {
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "taskboard_status": [],
            "code_diff": [],
            "realtime_events": [],
        }

        self.ws: Any | None = None
        self.connected = False
        self.transport_used: str = "none"
        self.connection_error: str = ""
        self._running = False
        self._task: asyncio.Task | None = None
        self._max_panel_items = 220
        self._max_llm_content_chars = 2400
        self._max_dialogue_chars = 1200
        self._runtime_v2_enabled = False
        self._runtime_v2_jetstream = False
        self._runtime_v2_client_id = ""
        self._runtime_v2_cursor = 0
        self._runtime_v2_last_acked_cursor = 0
        self._runtime_v2_tail = 200
        self._local_offsets: dict[str, int] = {}
        self._local_output_signatures: dict[str, str] = {}
        self._taskboard_has_non_empty_snapshot = False
        self._active_taskboard_task: dict[str, Any] | None = None
        self.runtime_root = self._resolve_runtime_root_path(workspace=self.workspace, runtime_root=None)

    async def _run_loop(self) -> None:
        """主运行循环（自动重连）。"""
        reconnect_backoff = 1.0
        while self._running:
            if not self.connected:
                ok = await self._connect()
                if not ok:
                    await asyncio.sleep(min(reconnect_backoff, 5.0))
                    reconnect_backoff = min(reconnect_backoff * 2.0, 5.0)
                    continue
                reconnect_backoff = 1.0
            if self.transport_used in {"ws", "ws.runtime_v2"}:
                await self._run_ws_listener()
            else:
                await asyncio.sleep(1.0)
                continue
            if self._running and not self.connected:
                await asyncio.sleep(min(reconnect_backoff, 5.0))
                reconnect_backoff = min(reconnect_backoff * 2.0, 5.0)

    async def start(self) -> None:
        """启动投影系统。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """停止投影系统。"""
        self._running = False

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self.ws:
            await self.ws.close()
            self.ws = None

        self.connected = False
        self._runtime_v2_enabled = False
        self._runtime_v2_jetstream = False
        self._runtime_v2_client_id = ""
        self._runtime_v2_cursor = 0
        self._runtime_v2_last_acked_cursor = 0
