from __future__ import annotations

# Cross-mixin method calls resolve at RuntimeProjection composition time.
# mypy: disable-error-code="attr-defined,union-attr,arg-type,return,assignment,has-type,misc"
import asyncio
import json
import logging
import re
from typing import Any

import websockets

from ._base import RuntimeProjectionBase

logger = logging.getLogger("observer.projection")


class RuntimeProjectionRuntimeV2Mixin(RuntimeProjectionBase):
    """Domain mixin: runtime_v2 methods for RuntimeProjection."""

    @staticmethod
    def _extract_projection_event_type(tags: Any) -> str:
        """Extract explicit projection event type from runtime.v2 tags."""
        if not isinstance(tags, list):
            return ""
        for item in tags:
            token = str(item or "").strip().lower()
            if token.startswith("projection_event:"):
                return token.split(":", 1)[1].strip()
        return ""

    @staticmethod
    def _extract_tool_name(content: str) -> str:
        """从文本中提取工具名（best-effort）。"""
        text = str(content or "").strip()
        if not text:
            return ""
        patterns = (
            r"tool[=:]\s*([a-zA-Z0-9_.:-]+)",
            r"([a-zA-Z_][a-zA-Z0-9_.:-]*)\(",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return str(match.group(1) or "").strip()
        return ""

    @staticmethod
    def _extract_runtime_v2_data(payload: dict[str, Any]) -> dict[str, Any]:
        """提取 runtime.v2 事件里的结构化原始数据。"""
        raw = payload.get("raw")
        raw = raw if isinstance(raw, dict) else {}
        data = raw.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _runtime_v2_metadata_maps(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """提取 metadata 与 extra_fields 视图。"""
        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        extra_fields = metadata.get("extra_fields")
        extra_fields = extra_fields if isinstance(extra_fields, dict) else {}
        return metadata, extra_fields

    @classmethod
    def _extract_runtime_v2_text(cls, payload: dict[str, Any], fallback: str) -> str:
        """从 runtime.v2 结构化数据中提取可展示文本。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        for candidate in (
            metadata.get("preview"),
            metadata.get("content_preview"),
            metadata.get("thinking_preview"),
            data.get("content"),
            data.get("message"),
            data.get("summary"),
            metadata.get("content"),
            metadata.get("message"),
            metadata.get("summary"),
            extra_fields.get("preview"),
            extra_fields.get("content"),
            extra_fields.get("message"),
            extra_fields.get("summary"),
            payload.get("message"),
            fallback,
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _extract_runtime_v2_tool_name(cls, payload: dict[str, Any], fallback: str) -> str:
        """从 runtime.v2 结构化数据中提取工具名。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        for candidate in (
            data.get("tool_name"),
            data.get("tool"),
            metadata.get("tool_name"),
            metadata.get("tool"),
            extra_fields.get("tool_name"),
            extra_fields.get("tool"),
        ):
            token = str(candidate or "").strip()
            if token:
                return token
        return cls._extract_tool_name(fallback)

    @classmethod
    def _extract_runtime_v2_tool_args(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """从 runtime.v2 结构化数据中提取工具参数。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        for candidate in (
            data.get("args"),
            metadata.get("args"),
            extra_fields.get("args"),
        ):
            if isinstance(candidate, dict):
                return candidate
        return {}

    @classmethod
    def _extract_runtime_v2_tool_result(cls, payload: dict[str, Any]) -> Any:
        """从 runtime.v2 结构化数据中提取工具结果。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        for candidate in (
            metadata.get("result_payload"),
            extra_fields.get("result_payload"),
            metadata.get("result"),
            extra_fields.get("result"),
            data.get("result"),
        ):
            if candidate is not None:
                return candidate
        return None

    @classmethod
    def _extract_runtime_v2_tool_success(cls, payload: dict[str, Any]) -> bool | None:
        """从 runtime.v2 结构化数据中提取工具执行状态。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        result_payload = cls._extract_runtime_v2_tool_result(payload)
        for candidate in (
            data.get("success"),
            metadata.get("success"),
            extra_fields.get("success"),
            result_payload.get("success") if isinstance(result_payload, dict) else None,
        ):
            if isinstance(candidate, bool):
                return candidate
        return None

    @classmethod
    def _extract_runtime_v2_task_id(cls, payload: dict[str, Any]) -> str:
        """从 runtime.v2 结构化数据中提取 task_id。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        for candidate in (
            data.get("task_id"),
            metadata.get("task_id"),
            extra_fields.get("task_id"),
        ):
            token = str(candidate or "").strip()
            if token:
                return token
        return ""

    @classmethod
    def _extract_runtime_v2_attempt(cls, payload: dict[str, Any]) -> int:
        """从 runtime.v2 结构化数据中提取尝试序号。"""
        data = cls._extract_runtime_v2_data(payload)
        metadata, extra_fields = cls._runtime_v2_metadata_maps(data)
        for candidate in (
            data.get("attempt"),
            data.get("iteration"),
            metadata.get("attempt"),
            metadata.get("iteration"),
            extra_fields.get("attempt"),
            extra_fields.get("iteration"),
        ):
            try:
                return max(0, int(candidate))
            except (TypeError, ValueError):
                continue
        return 0

    def _handle_runtime_v2_event(
        self,
        *,
        cursor: int,
        envelope: dict[str, Any],
    ) -> None:
        """处理 runtime.v2 的 JetStream 事件并映射到观察面板。"""
        safe_cursor = self._coerce_non_negative_int(cursor, default=0)
        if safe_cursor > self._runtime_v2_cursor:
            self._runtime_v2_cursor = safe_cursor

        payload = envelope.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        ts = str(envelope.get("ts") or "")
        channel = str(envelope.get("channel") or "").strip().lower()
        kind = str(envelope.get("kind") or "").strip().lower()
        actor = self._normalize_role_token(payload.get("actor"))
        message = str(payload.get("message") or "").strip()
        tags = payload.get("tags")
        raw = payload.get("raw")
        raw = raw if isinstance(raw, dict) else {}
        raw_stream_event = str(raw.get("stream_event") or raw.get("event_type") or "").strip().lower()

        if channel == "llm":
            explicit_projection_event = self._extract_projection_event_type(tags)
            if explicit_projection_event:
                event_type = explicit_projection_event
            elif raw_stream_event:
                event_type = raw_stream_event
            else:
                event_type = self._infer_llm_event_type_from_runtime_v2(kind, message, tags)
            content = self._extract_runtime_v2_text(payload, message or kind or "llm event")
            llm_item: dict[str, Any] = {
                "timestamp": ts,
                "channel": "llm",
                "role": actor,
                "event_type": event_type,
                "stream_key": f"runtime.v2:{actor}:{envelope.get('run_id') or ''!s}",
                "content": content[: self._max_llm_content_chars],
            }
            task_id = self._extract_runtime_v2_task_id(payload)
            if task_id:
                llm_item["task_id"] = task_id
            attempt = self._extract_runtime_v2_attempt(payload)
            if attempt > 0:
                llm_item["attempt"] = attempt
            if event_type in {"tool_call", "tool_result"}:
                tool_name = self._extract_runtime_v2_tool_name(payload, content)
                if tool_name:
                    llm_item["tool_name"] = tool_name
                tool_args = self._extract_runtime_v2_tool_args(payload)
                if tool_args:
                    llm_item["tool_args"] = tool_args
                if event_type == "tool_result":
                    success_hint = self._extract_runtime_v2_tool_success(payload)
                    if success_hint is None:
                        lower = content.lower()
                        if "failed" in lower or "error" in lower:
                            success_hint = False
                        elif "ok" in lower or "success" in lower:
                            success_hint = True
                    if success_hint is not None:
                        llm_item["tool_success"] = success_hint
                        llm_item["tool_status"] = "ok" if success_hint else "failed"
                    tool_result = self._extract_runtime_v2_tool_result(payload)
                    if tool_result is not None:
                        llm_item["tool_result_raw"] = tool_result
            self._push_llm_panel(llm_item)
            if event_type in {"tool_call", "tool_result", "error", "llm_failed"}:
                self._push_panel("tool_activity", llm_item)
            return

        runtime_payload = {
            "code": kind,
            "content": message,
            "actor": actor,
            "channel": channel,
            "refs": payload.get("refs"),
            "tags": payload.get("tags"),
        }
        taskboard_pushed = self._push_taskboard_from_payload(
            timestamp=ts,
            payload={"payload": payload, "raw": raw},
            source_prefix="runtime.v2",
        )
        if not taskboard_pushed:
            taskboard_pushed = self._push_taskboard_from_text(
                timestamp=ts,
                text=message or raw.get("summary") or raw.get("name"),
                source="runtime.v2.text",
            )
        if message:
            self._project_llm_lifecycle_from_runtime_payload(
                timestamp=ts,
                payload=runtime_payload,
                content=message,
            )
            self._push_panel(
                "realtime_events",
                {
                    "timestamp": ts,
                    "channel": channel or "runtime_event",
                    "content": message[:600],
                    "type": "runtime_event",
                    "kind": kind,
                    "cursor": safe_cursor,
                },
            )

    async def _run_ws_listener(self) -> None:
        """WebSocket 监听循环。"""
        if not self.ws:
            return

        try:
            async for message in self.ws:
                try:
                    parsed = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    await self._on_message(parsed)
        except websockets.exceptions.ConnectionClosed:
            logger.info("WS connection closed")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # Catch-all: unexpected error in WS listener loop
            self.connection_error = f"ws_listener_failed:{type(e).__name__}"
            logger.error("WS listener error: %s", e)
        finally:
            self.connected = False

    async def _on_message(self, msg: dict) -> None:
        """处理接收到的消息。"""
        msg_type = str(msg.get("type") or "").strip()
        protocol = str(msg.get("protocol") or "").strip()

        if msg_type.upper() == "EVENT" and protocol == "runtime.v2":
            event_payload = msg.get("event")
            event_payload = event_payload if isinstance(event_payload, dict) else {}
            event_channel = str(event_payload.get("channel") or "").strip().lower()
            if self.focus == "llm" and event_channel != "llm":
                cursor_only = self._coerce_non_negative_int(msg.get("cursor"), default=0)
                if cursor_only > 0:
                    await self._send_runtime_v2_ack(cursor_only)
                return
            cursor = self._coerce_non_negative_int(msg.get("cursor"), default=0)
            self._handle_runtime_v2_event(cursor=cursor, envelope=event_payload)
            if cursor > 0:
                await self._send_runtime_v2_ack(cursor)
            return

        if msg_type.upper() == "RESYNC_REQUIRED" and protocol == "runtime.v2":
            reason = str(msg.get("reason") or "")
            self._push_panel(
                "realtime_events",
                {
                    "timestamp": str(msg.get("timestamp") or ""),
                    "channel": "runtime.v2",
                    "content": f"resync required: {reason or 'events_dropped'}",
                    "type": "runtime_event",
                },
            )
            return

        if msg_type in {"PING", "PONG", "SUBSCRIBED", "UNSUBSCRIBED"}:
            return
        if self.focus == "llm" and msg_type not in {"status", "llm_stream"}:
            return

        if msg_type == "status":
            chain_status = {
                "pm": msg.get("pm_status"),
                "director": msg.get("director_status"),
            }
            if chain_status:
                self._push_panel(
                    "chain_status",
                    {
                        "timestamp": msg.get("timestamp", ""),
                        "status": chain_status,
                    },
                )

            taskboard_pushed = False
            snapshot_payload = msg.get("snapshot")
            if isinstance(snapshot_payload, str):
                snapshot_text = snapshot_payload.strip()
                if snapshot_text.startswith("{") or snapshot_text.startswith("["):
                    try:
                        parsed_snapshot = json.loads(snapshot_text)
                    except (ValueError, json.JSONDecodeError):
                        # ValueError/JSONDecodeError: malformed JSON
                        parsed_snapshot = {}
                    snapshot_payload = parsed_snapshot if isinstance(parsed_snapshot, dict) else {}
                else:
                    snapshot_payload = {}
            if not isinstance(snapshot_payload, dict):
                parent_payload = msg.get("payload")
                parent_payload = parent_payload if isinstance(parent_payload, dict) else {}
                nested_snapshot = parent_payload.get("snapshot")
                if isinstance(nested_snapshot, str):
                    nested_text = nested_snapshot.strip()
                    if nested_text.startswith("{") or nested_text.startswith("["):
                        try:
                            nested_snapshot = json.loads(nested_text)
                        except json.JSONDecodeError:
                            nested_snapshot = {}
                snapshot_payload = nested_snapshot if isinstance(nested_snapshot, dict) else {}
            if snapshot_payload:
                taskboard_pushed = self._push_snapshot_taskboard(
                    timestamp=str(msg.get("timestamp", "")),
                    tasks_payload=snapshot_payload.get("tasks"),
                    source="status.snapshot",
                )

            director_status = msg.get("director_status")
            director_status = director_status if isinstance(director_status, dict) else {}
            # 兼容两种数据结构：
            # 1. 扁平结构: director_status.tasks (后端 runtime_ws_status.py 实际返回)
            # 2. 嵌套结构: director_status.status.tasks (历史/其他来源)
            nested_status = director_status.get("status")
            nested_status = nested_status if isinstance(nested_status, dict) else {}
            tasks_payload = director_status.get("tasks") or nested_status.get("tasks")
            tasks_payload = tasks_payload if isinstance(tasks_payload, dict) else {}
            if (not taskboard_pushed) and tasks_payload:
                by_status = tasks_payload.get("by_status")
                by_status = by_status if isinstance(by_status, dict) else {}
                task_rows = tasks_payload.get("task_rows")
                if not isinstance(task_rows, list):
                    task_rows = tasks_payload.get("items")
                if not isinstance(task_rows, list):
                    task_rows = []

                total = self._coerce_non_negative_int(tasks_payload.get("total"), len(task_rows))
                ready_q = self._coerce_non_negative_int(tasks_payload.get("ready_queue_size"))
                ready_count = self._count_status_bucket(by_status, "READY")
                pending_count = self._count_status_bucket(by_status, "PENDING", "QUEUED")
                running_count = self._count_status_bucket(by_status, "RUNNING", "IN_PROGRESS", "CLAIMED")
                completed_count = self._count_status_bucket(by_status, "COMPLETED")
                failed_count = self._count_status_bucket(by_status, "FAILED")
                blocked_count = self._count_status_bucket(by_status, "BLOCKED")
                if pending_count <= 0 and ready_q > 0:
                    pending_count = ready_q
                items = self._normalize_taskboard_items(task_rows, default_status="pending", limit=16)
                if total <= 0:
                    total = len(items)
                summary = self._build_taskboard_summary(
                    total=total,
                    ready=ready_count,
                    pending=pending_count,
                    running=running_count,
                    completed=completed_count,
                    failed=failed_count,
                    blocked=blocked_count,
                )
                has_activity = self._has_non_empty_taskboard_snapshot(
                    total=total,
                    ready=ready_count,
                    pending=pending_count,
                    running=running_count,
                    completed=completed_count,
                    failed=failed_count,
                    blocked=blocked_count,
                    items=items,
                )
                self._push_taskboard_snapshot(
                    timestamp=str(msg.get("timestamp", "")),
                    summary=summary,
                    items=items,
                    source="status",
                    has_activity=has_activity,
                )

            if self._active_taskboard_task is not None and not self._latest_taskboard_has_running_item():
                self._overlay_active_taskboard_snapshot(
                    timestamp=str(msg.get("timestamp", "")),
                    source="status.active_task",
                )

        elif msg_type == "llm_stream":
            if self._runtime_v2_enabled:
                return
            item = self._normalize_llm_stream_item(msg)
            if item:
                self._push_llm_panel(item)
                if item.get("event_type") in {"tool_call", "tool_result", "error"}:
                    self._push_panel("tool_activity", item)

        elif msg_type == "dialogue_event":
            item = self._normalize_dialogue_item(msg)
            if item:
                self._push_panel("dialogue_stream", item)

        elif msg_type in ("process_stream", "runtime_event"):
            line = msg.get("line", "") or msg.get("event", {})
            timestamp = str(msg.get("timestamp", ""))
            payload = line if isinstance(line, dict) else {}
            content = line.get("content", "") or line.get("text", "") if isinstance(line, dict) else str(line)
            if payload:
                self._project_llm_lifecycle_from_runtime_payload(
                    timestamp=timestamp,
                    payload=payload,
                    content=content,
                )
            if content:
                self._push_panel(
                    "realtime_events",
                    {
                        "timestamp": timestamp,
                        "channel": msg.get("channel", ""),
                        "content": content[:600],
                    },
                )

        elif msg_type == "file_edit":
            event = msg.get("event")
            event = event if isinstance(event, dict) else {}
            patch_preview = self._compact_patch_preview(event.get("patch"))
            operation = str(event.get("operation") or "modify").strip().lower()
            if operation not in {"create", "modify", "delete"}:
                operation = "modify"
            added_lines = self._coerce_non_negative_int(event.get("added_lines"))
            deleted_lines = self._coerce_non_negative_int(event.get("deleted_lines"))
            modified_lines = self._coerce_non_negative_int(event.get("modified_lines"))
            self._push_panel(
                "code_diff",
                {
                    "timestamp": msg.get("timestamp", ""),
                    "file_path": str(event.get("file_path") or ""),
                    "operation": operation,
                    "patch": patch_preview,
                    "added_lines": added_lines,
                    "deleted_lines": deleted_lines,
                    "modified_lines": modified_lines,
                },
            )
            self._push_panel(
                "realtime_events",
                {
                    "timestamp": msg.get("timestamp", ""),
                    "type": "file_edit",
                    "file_path": event.get("file_path", ""),
                    "operation": operation,
                    "added_lines": added_lines,
                    "deleted_lines": deleted_lines,
                    "modified_lines": modified_lines,
                },
            )

        elif msg_type == "task_trace":
            event = msg.get("event", {})
            core_event = self._unwrap_task_trace_event(event)
            refs_payload = core_event.get("refs") if isinstance(core_event.get("refs"), dict) else {}
            taskboard_pushed = self._push_taskboard_from_payload(
                timestamp=str(msg.get("timestamp", "")),
                payload={"event": core_event, "refs": refs_payload},
                source_prefix="task_trace",
            )
            if not taskboard_pushed:
                self._push_taskboard_from_text(
                    timestamp=str(msg.get("timestamp", "")),
                    text=core_event.get("step_detail") or core_event.get("step_title") or "",
                    source="task_trace.text",
                )
            self._push_panel(
                "realtime_events",
                {
                    "timestamp": msg.get("timestamp", ""),
                    "type": "task_trace",
                    "event": event,
                },
            )
