from __future__ import annotations

# Cross-mixin method calls resolve at RuntimeProjection composition time.
# mypy: disable-error-code="attr-defined,union-attr,arg-type,return,assignment,has-type,misc"
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._base import RuntimeProjectionBase

logger = logging.getLogger("observer.projection")


class RuntimeProjectionLocalMixin(RuntimeProjectionBase):
    """Domain mixin: local methods for RuntimeProjection."""

    def _iter_local_projection_logs(self) -> list[tuple[str, str, Path]]:
        """发现可用于本地投影兜底的角色日志文件。"""
        discovered: list[tuple[str, str, Path]] = []
        seen_paths: set[str] = set()

        for runtime_root in self._collect_runtime_roots():
            roles_root = runtime_root / "roles"
            if not roles_root.is_dir():
                continue

            try:
                role_dirs = sorted(
                    (item for item in roles_root.iterdir() if item.is_dir()),
                    key=lambda item: item.name.lower(),
                )
            except OSError:
                continue

            for role_dir in role_dirs:
                role = str(role_dir.name or "").strip().lower() or "unknown"
                logs_dir = role_dir / "logs"
                if not logs_dir.is_dir():
                    continue

                for pattern, source in (
                    ("adapter_debug_*.jsonl", "adapter_debug"),
                    ("events_*.jsonl", "role_events"),
                ):
                    try:
                        files = sorted(logs_dir.glob(pattern))
                    except OSError:
                        continue

                    for path in files:
                        if not path.is_file():
                            continue
                        try:
                            key = str(path.resolve())
                        except (OSError, RuntimeError):
                            # OSError: path resolution failed
                            # RuntimeError: invalid path
                            key = str(path)
                        if key in seen_paths:
                            continue
                        seen_paths.add(key)
                        discovered.append((role, source, path))

        return discovered

    def _iter_local_projection_outputs(self) -> list[tuple[str, Path]]:
        """发现可用于本地投影兜底的角色输出文件。"""
        discovered: list[tuple[str, Path]] = []
        seen_paths: set[str] = set()
        for runtime_root in self._collect_runtime_roots():
            roles_root = runtime_root / "roles"
            if not roles_root.is_dir():
                continue
            try:
                role_dirs = sorted(
                    (item for item in roles_root.iterdir() if item.is_dir()),
                    key=lambda item: item.name.lower(),
                )
            except OSError:
                continue

            for role_dir in role_dirs:
                role = str(role_dir.name or "").strip().lower() or "unknown"
                outputs_dir = role_dir / "outputs"
                if not outputs_dir.is_dir():
                    continue
                try:
                    files = sorted(outputs_dir.glob("*.json"))
                except OSError:
                    continue
                for path in files:
                    if not path.is_file():
                        continue
                    try:
                        key = str(path.resolve())
                    except (OSError, RuntimeError):
                        # OSError: path resolution failed
                        # RuntimeError: invalid path
                        key = str(path)
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    discovered.append((role, path))
        return discovered

    @staticmethod
    def _utc_timestamp_from_mtime_ns(value: int) -> str:
        """将文件 mtime(ns) 转为 UTC ISO 字符串。"""
        try:
            return datetime.fromtimestamp(float(value) / 1_000_000_000, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            # ValueError: value out of range for timestamp
            # OSError: timestamp too large
            return ""

    def _poll_local_output_projection_once(self) -> None:
        """从角色输出文件投影 LLM 内容（兜底通道）。"""
        output_sources = self._iter_local_projection_outputs()
        if not output_sources:
            return

        for role, path in output_sources:
            try:
                stat = path.stat()
            except OSError:
                continue
            path_key = str(path)
            signature = f"{stat.st_mtime_ns}:{stat.st_size}"
            if self._local_output_signatures.get(path_key) == signature:
                continue

            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._local_output_signatures[path_key] = signature
                continue
            self._local_output_signatures[path_key] = signature
            if not isinstance(payload, dict):
                continue

            task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
            timestamp = str(payload.get("timestamp") or "").strip() or self._utc_timestamp_from_mtime_ns(
                stat.st_mtime_ns
            )
            content = str(payload.get("content") or payload.get("response") or payload.get("output") or "").strip()
            if content:
                self._push_local_llm_row(
                    timestamp=timestamp,
                    role=role,
                    task_id=task_id,
                    event_type="content_preview",
                    content=self._compact_text_preview(
                        content,
                        max_chars=min(self._max_llm_content_chars, 1800),
                        preserve_lines=True,
                    ),
                )

            error = str(payload.get("result_error") or payload.get("error") or "").strip()
            if error:
                self._push_local_llm_row(
                    timestamp=timestamp,
                    role=role,
                    task_id=task_id,
                    event_type="error",
                    content=error,
                )

            self._push_panel(
                "realtime_events",
                {
                    "timestamp": timestamp,
                    "channel": "local_role_output",
                    "content": f"[{role}] output snapshot: {path.name}",
                },
            )

    def _push_local_llm_row(
        self,
        *,
        timestamp: str,
        role: str,
        task_id: str,
        event_type: str,
        content: str,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        tool_status: str = "",
        tool_success: bool | None = None,
        tool_result_raw: Any = None,
    ) -> None:
        """将本地角色日志记录归一化为 LLM 推理行。"""
        payload: dict[str, Any] = {
            "timestamp": str(timestamp or ""),
            "channel": "local_role_log",
            "role": str(role or "unknown"),
            "event_type": str(event_type or "local_llm"),
            "stream_key": f"local:{role}:{task_id or 'na'}",
            "content": str(content or "")[: self._max_llm_content_chars],
        }
        # 添加工具相关元数据
        if tool_name:
            payload["tool_name"] = tool_name
        if tool_args:
            payload["tool_args"] = tool_args
        if tool_status:
            payload["tool_status"] = tool_status
        if tool_success is not None:
            payload["tool_success"] = tool_success
        if tool_result_raw is not None:
            payload["tool_result_raw"] = tool_result_raw

        self._push_llm_panel(payload)
        if payload["event_type"] in {"tool_call", "tool_result", "error"}:
            self._push_panel("tool_activity", payload)

    def _consume_local_log_record(self, role: str, source: str, record: dict[str, Any]) -> None:
        """解析本地角色日志记录并投影到面板。"""
        timestamp = str(record.get("timestamp") or "")
        event_name = str(record.get("event") or record.get("type") or "").strip().lower()
        task_id = str(record.get("task_id") or record.get("id") or "")
        payload = record.get("payload")
        payload = payload if isinstance(payload, dict) else {}

        if source == "adapter_debug":
            if event_name.startswith("taskboard_"):
                self._push_local_taskboard_snapshot(
                    timestamp=timestamp,
                    snapshot=payload.get("taskboard"),
                    source=event_name,
                )
                self._push_local_taskboard_snapshot(
                    timestamp=timestamp,
                    snapshot=payload.get("taskboard_after_claim"),
                    source=f"{event_name}_after_claim",
                )
                self._push_local_taskboard_snapshot(
                    timestamp=timestamp,
                    snapshot=payload.get("taskboard_before"),
                    source=f"{event_name}_before",
                )

            if event_name in {"first_llm_response", "sparse_output_retry_llm_response"}:
                success = payload.get("success")
                content_len = payload.get("content_len")
                validation_score = payload.get("validation_score")
                raw_error = str(payload.get("raw_error") or "").strip()
                summary = (
                    f"{event_name} success={success} content_len={content_len} validation_score={validation_score}"
                )
                if raw_error:
                    summary = f"{summary} error={raw_error}"
                self._push_local_llm_row(
                    timestamp=timestamp,
                    role=role,
                    task_id=task_id,
                    event_type="local_llm",
                    content=summary,
                )
                return

            if event_name in {"first_tool_results", "sparse_output_retry_tool_results"}:
                items = payload.get("items")
                if not isinstance(items, list):
                    items = []
                if not items:
                    count = payload.get("count")
                    self._push_local_llm_row(
                        timestamp=timestamp,
                        role=role,
                        task_id=task_id,
                        event_type="tool_result",
                        content=f"{event_name} count={count or 0}",
                    )
                    return
                for item in items[:8]:
                    if not isinstance(item, dict):
                        continue
                    tool = str(item.get("tool") or "unknown")
                    success = item.get("success")
                    status = "ok" if success is True else ("failed" if success is False else "unknown")
                    error = str(item.get("error") or "").strip()
                    content = f"{tool} -> {status}" if not error else f"{tool} -> {status} ({error})"

                    # 构建完整的工具结果数据
                    result_raw = {"success": success} if success is not None else {}
                    if error:
                        result_raw["error"] = error
                    # 尝试提取更多结果字段
                    for key in ("result", "output", "data", "items", "files", "content"):
                        if key in item and item[key] is not None:
                            result_raw[key] = item[key]

                    self._push_local_llm_row(
                        timestamp=timestamp,
                        role=role,
                        task_id=task_id,
                        event_type="tool_result",
                        content=content,
                        tool_name=tool,
                        tool_status=status,
                        tool_success=success if success is not None else (not error),
                        tool_result_raw=result_raw if result_raw else None,
                    )
                return

            if event_name in {
                "taskboard_task_selected",
                "taskboard_claimed",
                "execute_start",
                "execute_failed",
                "sparse_output_detected",
            }:
                detail = self._safe_json_compact(payload, max_chars=260)
                self._push_panel(
                    "realtime_events",
                    {
                        "timestamp": timestamp,
                        "channel": "local_role_log",
                        "content": f"[{role}] {event_name}: {detail}",
                    },
                )
                return

        if source == "role_events" and event_name == "turn_completed":
            data = record.get("data")
            data = data if isinstance(data, dict) else {}

            thinking_preview = str(data.get("thinking_preview") or "").strip()
            if thinking_preview:
                self._push_local_llm_row(
                    timestamp=timestamp,
                    role=role,
                    task_id=task_id,
                    event_type="thinking_chunk",
                    content=self._compact_text_preview(
                        thinking_preview,
                        max_chars=self._max_llm_content_chars,
                        preserve_lines=True,
                    ),
                )

            content_preview = str(data.get("content_preview") or "").strip()
            if content_preview:
                self._push_local_llm_row(
                    timestamp=timestamp,
                    role=role,
                    task_id=task_id,
                    event_type="content_chunk",
                    content=self._compact_text_preview(
                        content_preview,
                        max_chars=self._max_llm_content_chars,
                        preserve_lines=True,
                    ),
                )

            tool_details = data.get("tool_details")
            if isinstance(tool_details, list):
                for item in tool_details[:8]:
                    if not isinstance(item, dict):
                        continue
                    tool_name = str(item.get("tool") or "unknown")
                    success = item.get("success")
                    status = "ok" if success is True else ("failed" if success is False else "unknown")
                    error = str(item.get("error") or "").strip()
                    detail = f"{tool_name} -> {status}" if not error else f"{tool_name} -> {status} ({error})"

                    # 构建完整的工具结果数据
                    result_raw = {"success": success} if success is not None else {}
                    if error:
                        result_raw["error"] = error
                    # 尝试提取更多结果字段
                    for key in ("result", "output", "data", "items", "files", "content"):
                        if key in item and item[key] is not None:
                            result_raw[key] = item[key]

                    self._push_local_llm_row(
                        timestamp=timestamp,
                        role=role,
                        task_id=task_id,
                        event_type="tool_result",
                        content=detail,
                        tool_name=tool_name,
                        tool_status=status,
                        tool_success=success if success is not None else (not error),
                        tool_result_raw=result_raw if result_raw else None,
                    )

            self._push_panel(
                "realtime_events",
                {
                    "timestamp": timestamp,
                    "channel": "local_role_log",
                    "content": (
                        f"[{role}] turn_completed tool_calls={data.get('has_tool_calls')} "
                        f"tool_results={data.get('tool_results_count')}"
                    ),
                },
            )

    def _poll_local_projection_once(self) -> None:
        """本地轮询已下线，防止出现伪实时回放。"""
        raise RuntimeError("Local polling has been removed. Use WS runtime.v2 (JetStream) push only.")
