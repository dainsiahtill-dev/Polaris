from __future__ import annotations

# Cross-mixin method calls resolve at RuntimeProjection composition time.
# mypy: disable-error-code="attr-defined,union-attr,arg-type,return,assignment,has-type,misc"
import asyncio
import json
import logging
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import websockets
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots

from ._base import RuntimeProjectionBase

logger = logging.getLogger("observer.projection")


class RuntimeProjectionConnectionMixin(RuntimeProjectionBase):
    """Domain mixin: connection methods for RuntimeProjection."""

    @staticmethod
    def _unwrap_task_trace_event(event: Any) -> dict[str, Any]:
        """Unwrap nested `task_trace` envelopes emitted by message fanout."""
        current = event if isinstance(event, dict) else {}
        for _ in range(3):
            nested = current.get("event")
            if not isinstance(nested, dict):
                break
            current_type = str(current.get("type") or "").strip().lower()
            if current_type and current_type != "task_trace":
                break
            current = nested
        return current if isinstance(current, dict) else {}

    @staticmethod
    def _normalize_workspace_value(value: str) -> str:
        """归一化工作区路径字符串。"""
        raw = str(value or "").strip().strip('"')
        if not raw:
            return ""
        try:
            return str(Path(raw).resolve())
        except OSError:
            # OSError: path resolution failed (permissions, symlinks, etc.)
            return raw

    @staticmethod
    def _resolve_runtime_root_path(*, workspace: str, runtime_root: str | None) -> Path:
        """解析运行时根目录路径。"""
        if runtime_root:
            try:
                return Path(str(runtime_root)).resolve()
            except OSError:
                # OSError: path resolution failed
                return Path(str(runtime_root))
        try:
            workspace_path = Path(workspace).resolve()
        except OSError:
            workspace_path = Path(workspace)
        return Path(resolve_runtime_path(str(workspace_path), "runtime"))

    @staticmethod
    def _sanitize_token(token: str) -> str:
        """脱敏显示 token。"""
        if not token:
            return ""
        if len(token) <= 4:
            return "****"
        return f"{token[0]}****{token[-1]}"

    def _refresh_connection_urls(self) -> None:
        """根据当前 workspace/token 重新生成 WS URL。"""
        self.ws_url = self._build_ws_url()

    def _build_ws_url(self) -> str:
        """从 HTTP backend URL 构建 runtime.v2 WebSocket URL。"""
        parsed = urllib.parse.urlparse(self.backend_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        query: dict[str, str] = {"protocol": "runtime.v2"}
        if self.workspace:
            query["workspace"] = self.workspace
        if self.token:
            query["token"] = self.token
        query_text = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        return f"{ws_scheme}://{parsed.netloc}/v2/ws/runtime?{query_text}"

    async def _connect_ws(self) -> bool:
        """建立 WebSocket 连接。"""
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(self.ws_url, ping_interval=30),
                timeout=10.0,
            )
            subscribed = await self._subscribe_runtime_v2()
            if not subscribed:
                if self.ws is not None:
                    try:
                        await self.ws.close()
                    except websockets.exceptions.ConnectionClosed:
                        # ConnectionClosed: WebSocket already closed, ignore
                        pass
                    except OSError:
                        # OSError: other close errors
                        pass
                self.ws = None
                self.connected = False
                return False
            self.connected = True
            self.connection_error = ""
            return True
        except websockets.exceptions.ConnectionClosed as e:
            # ConnectionClosed: WS disconnected, not an error in connect flow
            self.connection_error = "ws_connect_failed:connection_closed"
            logger.debug("WS connect error: %s", e)
        except Exception as e:  # noqa: BLE001
            # Catch-all for unexpected errors during connect.
            self.connection_error = f"ws_connect_failed:{type(e).__name__}"
            logger.debug("WS connect error: %s", e)
            self.connected = False
            return False

    async def _connect(self) -> bool:
        """建立连接（严格 WS runtime.v2 / JetStream 推送）。"""
        if await self._connect_ws():
            self.transport_used = "ws.runtime_v2"
            logger.info("Projection connected via WS runtime.v2 (JetStream)")
            return True
        self.transport_used = "none"
        return False

    def _derive_workspace_key(self) -> str:
        """从 workspace 路径推导 workspace_key。"""
        raw = str(self.workspace or "").strip()
        if not raw:
            return "default"
        try:
            return str(resolve_storage_roots(raw).workspace_key or "").strip() or "default"
        except (OSError, ValueError):
            # OSError/ValueError: resolve_storage_roots() failure
            try:
                return Path(raw).resolve().name or "default"
            except (OSError, ValueError):
                return Path(raw).name or "default"

    async def _send_subscribe(self, channels: list[str]) -> bool:
        """发送 runtime.v2 SUBSCRIBE 请求。"""
        if not self.ws:
            return False

        normalized_workspace = self._derive_workspace_key()
        message = {
            "type": "SUBSCRIBE",
            "protocol": "runtime.v2",
            "client_id": f"observer-{uuid.uuid4().hex[:10]}",
            "channels": channels,
            "cursor": int(self._runtime_v2_cursor or 0),
            "tail": int(self._runtime_v2_tail),
            "workspace": normalized_workspace,
        }
        try:
            await self.ws.send(json.dumps(message, ensure_ascii=False))
            self._runtime_v2_client_id = str(message["client_id"])
            return True
        except websockets.exceptions.ConnectionClosed:
            self.connection_error = "runtime_v2_subscribe_send_failed:connection_closed"
            logger.debug("runtime.v2 SUBSCRIBE send failed: connection closed")
            return False
        except OSError as exc:
            # OSError: network send errors
            self.connection_error = f"runtime_v2_subscribe_send_failed:{type(exc).__name__}"
            logger.debug("runtime.v2 SUBSCRIBE send failed: %s", exc)
            return False

    async def _subscribe_runtime_v2(self) -> bool:
        """激活 runtime.v2 协议并校验 JetStream 可用。"""
        if not self.ws:
            self.connection_error = "runtime_v2_subscribe_failed:no_socket"
            return False

        if not await self._send_subscribe(["*"]):
            return False

        deadline = asyncio.get_running_loop().time() + 6.0
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.connection_error = "runtime_v2_subscribe_timeout"
                return False
            try:
                raw_message = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                self.connection_error = "runtime_v2_subscribe_recv_timeout"
                logger.debug("runtime.v2 SUBSCRIBE recv timed out")
                return False
            except websockets.exceptions.ConnectionClosed:
                self.connection_error = "runtime_v2_subscribe_recv_connection_closed"
                logger.debug("runtime.v2 SUBSCRIBE recv: connection closed")
                return False
            except OSError as exc:
                self.connection_error = f"runtime_v2_subscribe_recv_failed:{type(exc).__name__}"
                logger.debug("runtime.v2 SUBSCRIBE recv failed: %s", exc)
                return False

            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue

            msg_type = str(message.get("type") or "").strip().upper()
            protocol = str(message.get("protocol") or "").strip()
            if msg_type == "SUBSCRIBED" and protocol == "runtime.v2":
                payload = message.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                jetstream_ok = bool(payload.get("jetstream") is True)
                if not jetstream_ok:
                    self.connection_error = "runtime_v2_subscribed_without_jetstream"
                    return False
                self._runtime_v2_enabled = True
                self._runtime_v2_jetstream = True
                self._runtime_v2_client_id = str(payload.get("client_id") or self._runtime_v2_client_id)
                self._runtime_v2_cursor = self._coerce_non_negative_int(payload.get("cursor"), default=0)
                self._runtime_v2_last_acked_cursor = self._runtime_v2_cursor
                return True

            await self._on_message(message)

    async def _send_runtime_v2_ack(self, cursor: int) -> None:
        """向 runtime.v2 服务端确认游标，避免 JetStream 积压。"""
        safe_cursor = self._coerce_non_negative_int(cursor, default=0)
        if safe_cursor <= 0:
            return
        if safe_cursor <= self._runtime_v2_last_acked_cursor:
            return
        if not self.ws or not self._runtime_v2_enabled:
            return

        ack_payload = {
            "type": "ACK",
            "protocol": "runtime.v2",
            "cursor": safe_cursor,
        }
        try:
            await self.ws.send(json.dumps(ack_payload, ensure_ascii=False))
            self._runtime_v2_last_acked_cursor = safe_cursor
        except OSError as exc:
            # OSError: network send errors
            self.connection_error = f"runtime_v2_ack_failed:{type(exc).__name__}"
            logger.debug("runtime.v2 ACK failed: cursor=%s error=%s", safe_cursor, exc)

    async def retarget_workspace(self, new_workspace: str) -> bool:
        """切换工作空间。"""
        normalized_workspace = self._normalize_workspace_value(new_workspace)
        if not normalized_workspace or self.workspace == normalized_workspace:
            return False

        previous_workspace = self.workspace
        if self.ws:
            try:
                await self.ws.close()
            except websockets.exceptions.ConnectionClosed:
                # ConnectionClosed: already closed, ignore
                pass
            except OSError as e:
                # OSError: close errors (connection lost, etc.)
                logger.debug("WS close error during workspace retarget: %s", e)
        self.ws = None

        self.connected = False
        self.transport_used = "none"
        self.connection_error = ""
        self._runtime_v2_enabled = False
        self._runtime_v2_jetstream = False
        self._runtime_v2_client_id = ""
        self._runtime_v2_cursor = 0
        self._runtime_v2_last_acked_cursor = 0
        self.workspace = normalized_workspace
        self._refresh_connection_urls()
        self.runtime_root = self._resolve_runtime_root_path(workspace=self.workspace, runtime_root=None)
        self._local_offsets.clear()
        self._local_output_signatures.clear()

        # 保留最近 taskboard 快照，避免 workspace 切换时面板瞬间清空。
        cached_taskboard = list(self.panels.get("taskboard_status", []))[-3:]
        for key in self.panels:
            self.panels[key].clear()
        if cached_taskboard:
            self.panels["taskboard_status"].extend(cached_taskboard)
        self._taskboard_has_non_empty_snapshot = bool(cached_taskboard)
        self._active_taskboard_task = None
        self._push_panel(
            "realtime_events",
            {
                "channel": "projection",
                "content": (f"workspace switched: {previous_workspace or '(unknown)'} -> {self.workspace}"),
            },
        )

        return True

    def retarget_runtime_root(self, new_runtime_root: str) -> bool:
        """切换运行时根目录。"""
        candidate = str(new_runtime_root or "").strip().strip("'\"")
        if not candidate:
            return False
        resolved = self._resolve_runtime_root_path(workspace=self.workspace, runtime_root=candidate)
        if resolved == self.runtime_root:
            return False
        self.runtime_root = resolved
        self._local_offsets.clear()
        self._local_output_signatures.clear()
        return True

    def _collect_runtime_roots(self) -> list[Path]:
        """收集所有可能的运行时根目录。"""
        roots: list[Path] = []
        default_root = self._resolve_runtime_root_path(workspace=self.workspace, runtime_root=None)
        for candidate in (self.runtime_root, default_root):
            if not isinstance(candidate, Path):
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                # OSError: path resolution failed
                resolved = candidate
            if resolved in roots:
                continue
            roots.append(resolved)
        return roots

    async def probe_connection(self, timeout: float = 10.0) -> dict[str, Any]:
        """执行一次性连接探针，不启动后台循环。"""
        try:
            connected = await asyncio.wait_for(self._connect(), timeout=max(0.5, float(timeout or 0.0)))
            return {
                "ok": bool(connected and self.connected and self._runtime_v2_jetstream),
                "connected": bool(self.connected),
                "transport": str(self.transport_used or "none"),
                "runtime_v2": bool(self._runtime_v2_enabled),
                "jetstream": bool(self._runtime_v2_jetstream),
                "connection_error": str(self.connection_error or ""),
                "ws_url": str(self.ws_url or ""),
            }
        finally:
            await self.stop()
