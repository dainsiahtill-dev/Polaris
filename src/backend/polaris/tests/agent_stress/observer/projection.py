"""实时投影系统。

WebSocket runtime.v2(JetStream) 连接与事件归一化。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots

logger = logging.getLogger("observer.projection")


class RuntimeProjection:
    """实时投影订阅系统（仅 WS runtime.v2 / JetStream）。"""

    _TASKBOARD_BRIEF_PATTERN = re.compile(
        r"taskboard\s+total=(?P<total>\d+)\s+ready=(?P<ready>\d+)\s+"
        r"pending=(?P<pending>\d+)\s+in_progress=(?P<running>\d+)\s+"
        r"completed=(?P<completed>\d+)\s+failed=(?P<failed>\d+)\s+blocked=(?P<blocked>\d+)",
        flags=re.IGNORECASE,
    )

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

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int = 0) -> int:
        """安全转换为非负整数。"""
        try:
            return max(0, int(value))
        except (ValueError, TypeError):
            # ValueError: invalid string for int conversion
            # TypeError: wrong type passed to int()
            return max(0, int(default))

    @staticmethod
    def _coerce_bool(value: Any) -> bool | None:
        """宽松布尔解析。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        token = str(value or "").strip().lower()
        if not token:
            return None
        if token in {"1", "true", "yes", "y", "on", "pass", "passed", "success", "ok"}:
            return True
        if token in {"0", "false", "no", "n", "off", "fail", "failed", "error"}:
            return False
        return None

    @staticmethod
    def _normalize_task_status_token(value: Any) -> str:
        """统一任务状态 token。"""
        token = str(value or "").strip().lower()
        if not token:
            return "pending"
        alias_map = {
            "queued": "pending",
            "todo": "pending",
            "new": "pending",
            "open": "pending",
            "claimed": "in_progress",
            "running": "in_progress",
            "executing": "in_progress",
            "in-progress": "in_progress",
            "in progress": "in_progress",
            "done": "completed",
            "success": "completed",
            "passed": "completed",
            "pass": "completed",
            "error": "failed",
            "fail": "failed",
            "cancelled": "failed",
            "canceled": "failed",
            "timed_out": "failed",
            "timeout": "failed",
            "stalled": "blocked",
        }
        return alias_map.get(token, token)

    @classmethod
    def _count_status_bucket(cls, bucket: dict[str, Any], *tokens: str) -> int:
        """按多候选 token 汇总数量。"""
        total = 0
        if not isinstance(bucket, dict):
            return total
        for token in tokens:
            for key in (token, token.upper(), token.lower()):
                if key not in bucket:
                    continue
                total += cls._coerce_non_negative_int(bucket.get(key))
                break
        return total

    @staticmethod
    def _normalize_qa_state(value: Any) -> str:
        """归一化 QA 状态 token。"""
        token = str(value or "").strip().lower()
        if token in {"pending", "passed", "failed", "rework", "exhausted"}:
            return token
        return ""

    @classmethod
    def _infer_qa_state_from_row(cls, row: dict[str, Any], status: str) -> str:
        """从任务行推断 QA 状态（兼容缺失 qa_state 字段）。"""
        explicit = cls._normalize_qa_state(row.get("qa_state"))
        if explicit:
            return explicit

        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        adapter_result = metadata.get("adapter_result") if isinstance(metadata.get("adapter_result"), dict) else {}
        result_payload = row.get("result") if isinstance(row.get("result"), dict) else {}

        qa_required = bool(adapter_result.get("qa_required_for_final_verdict"))
        qa_passed = cls._coerce_bool(adapter_result.get("qa_passed"))
        if qa_passed is None:
            qa_passed = cls._coerce_bool(result_payload.get("qa_passed"))

        if status == "failed" and bool(metadata.get("qa_rework_exhausted")):
            return "exhausted"
        if bool(metadata.get("qa_rework_requested")):
            return "rework"

        if qa_required:
            if qa_passed is True:
                return "passed"
            if qa_passed is False:
                return "failed" if status == "completed" else "rework"
            if status == "completed":
                return "pending"

        return ""

    @classmethod
    def _normalize_taskboard_item(
        cls,
        row: dict[str, Any],
        *,
        default_status: str = "pending",
    ) -> dict[str, Any]:
        """标准化 TaskBoard 单行。"""
        task_id = str(row.get("id") or row.get("task_id") or "").strip()
        subject = str(row.get("subject") or row.get("title") or row.get("name") or "").strip()
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        status = cls._normalize_task_status_token(row.get("status") or row.get("state") or default_status)
        qa_state = cls._infer_qa_state_from_row(row, status)
        projection = metadata.get("projection") if isinstance(metadata.get("projection"), dict) else {}
        resume_state = (
            str(
                row.get("resume_state")
                or metadata.get("resume_state")
                or (
                    metadata.get("runtime_execution", {}).get("resume_state")
                    if isinstance(metadata.get("runtime_execution"), dict)
                    else ""
                )
                or ""
            )
            .strip()
            .lower()
        )
        execution_backend = str(row.get("execution_backend") or metadata.get("execution_backend") or "").strip().lower()
        projection_scenario = (
            str(
                row.get("projection_scenario")
                or metadata.get("projection_scenario")
                or projection.get("scenario_id")
                or ""
            )
            .strip()
            .lower()
        )
        return {
            "id": task_id,
            "subject": subject,
            "status": status,
            "qa_state": qa_state,
            "resume_state": resume_state,
            "execution_backend": execution_backend,
            "projection_scenario": projection_scenario,
        }

    @classmethod
    def _normalize_taskboard_items(
        cls,
        rows: Any,
        *,
        default_status: str = "pending",
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        """标准化 TaskBoard 列表。"""
        normalized: list[dict[str, Any]] = []
        if not isinstance(rows, list):
            return normalized
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append(
                cls._normalize_taskboard_item(
                    row,
                    default_status=default_status,
                )
            )
            if len(normalized) >= max(1, int(limit)):
                break
        return normalized

    @staticmethod
    def _taskboard_item_identity(item: dict[str, Any]) -> str:
        """Return a stable identity key for a normalized taskboard item."""
        task_id = str(item.get("id") or item.get("task_id") or "").strip()
        if task_id:
            return f"id:{task_id}"
        subject = str(item.get("subject") or item.get("title") or "").strip().lower()
        if subject:
            return f"subject:{subject}"
        return ""

    @classmethod
    def _is_running_task_status(cls, status: Any) -> bool:
        """Whether a task status token should be rendered as actively executing."""
        return cls._normalize_task_status_token(status) == "in_progress"

    @classmethod
    def _is_terminal_task_status(cls, status: Any) -> bool:
        """Whether a task status token is terminal for active-task tracking."""
        return cls._normalize_task_status_token(status) in {"completed", "failed", "blocked"}

    @classmethod
    def _merge_taskboard_item(
        cls,
        primary: dict[str, Any],
        secondary: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge duplicate taskboard items while preserving the higher-signal row."""
        merged = dict(primary)
        secondary_normalized = cls._normalize_taskboard_item(secondary, default_status="pending")
        if (
            cls._is_running_task_status(secondary_normalized.get("status"))
            and not cls._is_running_task_status(merged.get("status"))
        ) or (
            cls._is_terminal_task_status(secondary_normalized.get("status"))
            and not cls._is_running_task_status(merged.get("status"))
        ):
            merged["status"] = secondary_normalized.get("status", merged.get("status"))

        for key in ("subject", "qa_state", "resume_state", "execution_backend", "projection_scenario"):
            if not str(merged.get(key) or "").strip() and str(secondary_normalized.get(key) or "").strip():
                merged[key] = secondary_normalized.get(key)
        return merged

    @classmethod
    def _dedupe_taskboard_items(
        cls,
        items: list[dict[str, Any]],
        *,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        """Deduplicate taskboard rows by task id/subject while preserving priority order."""
        deduped: list[dict[str, Any]] = []
        index_by_key: dict[str, int] = {}
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            item = cls._normalize_taskboard_item(raw_item, default_status="pending")
            key = cls._taskboard_item_identity(item)
            if key and key in index_by_key:
                existing_index = index_by_key[key]
                deduped[existing_index] = cls._merge_taskboard_item(deduped[existing_index], item)
                continue
            if key:
                index_by_key[key] = len(deduped)
            deduped.append(item)
            if len(deduped) >= max(1, int(limit)):
                break
        return deduped

    @staticmethod
    def _parse_taskboard_summary(summary: Any) -> dict[str, int]:
        """Parse `total=... ready=...` counters from a rendered taskboard summary."""
        text = str(summary or "").strip()
        if not text:
            return {}
        matches = re.findall(
            r"\b(total|ready|pending|running|completed|failed|blocked)=(\d+)\b",
            text,
            flags=re.IGNORECASE,
        )
        if not matches:
            return {}
        counters: dict[str, int] = {}
        for key, value in matches:
            counters[str(key).strip().lower()] = RuntimeProjection._coerce_non_negative_int(value)
        return counters

    @classmethod
    def _payload_indicates_running_task(cls, payload: Any) -> bool:
        """Infer whether a payload describes an actively executing task."""
        if not isinstance(payload, dict):
            return False
        text = " ".join(
            str(payload.get(key) or "").strip().lower()
            for key in ("status", "state", "phase", "code", "message", "event", "step_title", "step_detail")
            if str(payload.get(key) or "").strip()
        )
        if not text:
            return False
        if any(token in text for token in ("completed", "failed", "blocked", "cancelled", "canceled")):
            return False
        return any(
            token in text
            for token in (
                "claimed",
                "in_progress",
                "running",
                "executing",
                "execute_start",
                "execution_backend.selected",
                "task_selected",
            )
        )

    @classmethod
    def _extract_taskboard_focus_task(cls, payload: Any) -> tuple[dict[str, Any] | None, bool]:
        """Extract the current task reference carried alongside taskboard events."""
        if not isinstance(payload, dict):
            return None, False

        stack: list[dict[str, Any]] = [payload]
        visited: set[int] = set()
        active_hint = False
        while stack:
            node = stack.pop()
            node_id = id(node)
            if node_id in visited:
                continue
            visited.add(node_id)

            active_hint = active_hint or cls._payload_indicates_running_task(node)
            task_ref = node.get("taskboard_task")
            if isinstance(task_ref, dict):
                normalized = cls._normalize_taskboard_item(task_ref, default_status="pending")
                if cls._taskboard_item_identity(normalized):
                    if cls._is_running_task_status(normalized.get("status")):
                        active_hint = True
                    return normalized, active_hint

            for key in ("refs", "payload", "event", "raw", "data", "output", "meta"):
                child = node.get(key)
                if isinstance(child, dict):
                    stack.append(child)
        return None, active_hint

    def _remember_active_taskboard_task(
        self,
        task: dict[str, Any] | None,
        *,
        running_hint: bool = False,
    ) -> None:
        """Track the last active task so stale snapshots can be repaired in the observer."""
        if not isinstance(task, dict):
            return
        normalized = self._normalize_taskboard_item(task, default_status="pending")
        if running_hint and not self._is_terminal_task_status(normalized.get("status")):
            normalized["status"] = "in_progress"
        if self._is_running_task_status(normalized.get("status")):
            self._active_taskboard_task = normalized
            return
        if self._is_terminal_task_status(normalized.get("status")):
            current = self._active_taskboard_task if isinstance(self._active_taskboard_task, dict) else {}
            current_key = self._taskboard_item_identity(current)
            normalized_key = self._taskboard_item_identity(normalized)
            if current_key and current_key == normalized_key:
                self._active_taskboard_task = None

    def _latest_taskboard_has_running_item(self) -> bool:
        """Whether the latest taskboard panel already exposes a running task."""
        rows = self.panels.get("taskboard_status") or []
        if not rows:
            return False
        latest = rows[-1] if isinstance(rows[-1], dict) else {}
        items = latest.get("items")
        items = items if isinstance(items, list) else []
        if any(self._is_running_task_status(item.get("status")) for item in items if isinstance(item, dict)):
            return True
        counts = self._parse_taskboard_summary(latest.get("summary"))
        return int(counts.get("running") or 0) > 0

    def _overlay_active_taskboard_snapshot(
        self,
        *,
        timestamp: str,
        source: str,
        focus_task: dict[str, Any] | None = None,
        running_hint: bool = False,
    ) -> bool:
        """Overlay the active task onto the latest taskboard snapshot to keep the UI realtime."""
        candidate = focus_task if isinstance(focus_task, dict) else self._active_taskboard_task
        if not isinstance(candidate, dict):
            return False

        merged_focus = self._normalize_taskboard_item(candidate, default_status="pending")
        if running_hint and not self._is_terminal_task_status(merged_focus.get("status")):
            merged_focus["status"] = "in_progress"
        if not self._taskboard_item_identity(merged_focus):
            return False

        rows = self.panels.get("taskboard_status") or []
        latest = rows[-1] if rows and isinstance(rows[-1], dict) else {}
        items = latest.get("items")
        items = items if isinstance(items, list) else []
        merged_items = self._dedupe_taskboard_items(
            [merged_focus, *items]
            if self._is_running_task_status(merged_focus.get("status"))
            else [*items, merged_focus],
            limit=16,
        )
        counts = self._parse_taskboard_summary(latest.get("summary"))
        summary_counts = {
            "total": self._coerce_non_negative_int(counts.get("total"), len(merged_items)),
            "ready": self._coerce_non_negative_int(counts.get("ready")),
            "pending": self._coerce_non_negative_int(counts.get("pending")),
            "running": self._coerce_non_negative_int(counts.get("running")),
            "completed": self._coerce_non_negative_int(counts.get("completed")),
            "failed": self._coerce_non_negative_int(counts.get("failed")),
            "blocked": self._coerce_non_negative_int(counts.get("blocked")),
        }
        if self._is_running_task_status(merged_focus.get("status")):
            summary_counts["running"] = max(summary_counts["running"], 1)
        summary_counts["total"] = max(summary_counts["total"], len(merged_items))
        summary = self._build_taskboard_summary(
            total=summary_counts["total"],
            ready=summary_counts["ready"],
            pending=summary_counts["pending"],
            running=summary_counts["running"],
            completed=summary_counts["completed"],
            failed=summary_counts["failed"],
            blocked=summary_counts["blocked"],
        )
        has_activity = self._has_non_empty_taskboard_snapshot(
            total=summary_counts["total"],
            ready=summary_counts["ready"],
            pending=summary_counts["pending"],
            running=summary_counts["running"],
            completed=summary_counts["completed"],
            failed=summary_counts["failed"],
            blocked=summary_counts["blocked"],
            items=merged_items,
        )
        self._push_taskboard_snapshot(
            timestamp=str(timestamp or ""),
            summary=summary,
            items=merged_items,
            source=str(source or "taskboard.active"),
            has_activity=has_activity,
        )
        return True

    @classmethod
    def _extract_snapshot_task_rows(cls, tasks_payload: Any) -> list[dict[str, Any]]:
        """Extract task rows from ``snapshot.tasks`` payload."""
        if isinstance(tasks_payload, str):
            text = str(tasks_payload).strip()
            if text.startswith("{") or text.startswith("["):
                try:
                    parsed = json.loads(text)
                except (ValueError, json.JSONDecodeError):
                    # ValueError: invalid JSON structure
                    # json.JSONDecodeError: actual decode failure
                    parsed = None
                if parsed is not None:
                    return cls._extract_snapshot_task_rows(parsed)
        if isinstance(tasks_payload, list):
            return [dict(item) for item in tasks_payload if isinstance(item, dict)]
        if isinstance(tasks_payload, dict):
            rows: list[dict[str, Any]] = []
            for task_id, item in tasks_payload.items():
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                if not str(row.get("id") or "").strip():
                    row["id"] = str(task_id or "").strip()
                rows.append(row)
            return rows
        return []

    @staticmethod
    def _build_taskboard_summary(
        *,
        total: int,
        ready: int,
        pending: int,
        running: int,
        completed: int,
        failed: int,
        blocked: int,
    ) -> str:
        """构建 TaskBoard 摘要文本。"""
        return (
            f"total={max(0, int(total))} "
            f"ready={max(0, int(ready))} "
            f"pending={max(0, int(pending))} "
            f"running={max(0, int(running))} "
            f"completed={max(0, int(completed))} "
            f"failed={max(0, int(failed))} "
            f"blocked={max(0, int(blocked))}"
        )

    @staticmethod
    def _has_non_empty_taskboard_snapshot(
        *,
        total: int,
        ready: int,
        pending: int,
        running: int,
        completed: int,
        failed: int,
        blocked: int,
        items: list[dict[str, Any]],
    ) -> bool:
        if items:
            return True
        return any(value > 0 for value in (total, ready, pending, running, completed, failed, blocked))

    def _push_taskboard_snapshot(
        self,
        *,
        timestamp: str,
        summary: str,
        items: list[dict[str, Any]],
        source: str,
        has_activity: bool,
    ) -> None:
        """Push taskboard snapshot with anti-flicker guard for empty updates."""
        if has_activity:
            self._taskboard_has_non_empty_snapshot = True
        elif self._taskboard_has_non_empty_snapshot:
            return
        self._push_panel(
            "taskboard_status",
            {
                "timestamp": str(timestamp or ""),
                "summary": str(summary or ""),
                "items": items[:16],
                "source": str(source or "status"),
            },
        )

    def _push_snapshot_taskboard(
        self,
        *,
        timestamp: str,
        tasks_payload: Any,
        source: str,
    ) -> bool:
        """Build and push Taskboard panel from ``snapshot.tasks``."""
        rows = self._extract_snapshot_task_rows(tasks_payload)
        if not rows:
            return False
        items = self._dedupe_taskboard_items(
            self._normalize_taskboard_items(rows, default_status="pending", limit=16),
            limit=16,
        )

        total = len(items)
        ready = sum(1 for item in items if str(item.get("status") or "").strip().lower() == "ready")
        pending = sum(1 for item in items if str(item.get("status") or "").strip().lower() == "pending")
        running = sum(
            1
            for item in items
            if str(item.get("status") or "").strip().lower() in {"in_progress", "running", "claimed"}
        )
        completed = sum(1 for item in items if str(item.get("status") or "").strip().lower() == "completed")
        failed = sum(1 for item in items if str(item.get("status") or "").strip().lower() == "failed")
        blocked = sum(1 for item in items if str(item.get("status") or "").strip().lower() == "blocked")

        summary = self._build_taskboard_summary(
            total=total,
            ready=ready,
            pending=pending,
            running=running,
            completed=completed,
            failed=failed,
            blocked=blocked,
        )
        has_activity = self._has_non_empty_taskboard_snapshot(
            total=total,
            ready=ready,
            pending=pending,
            running=running,
            completed=completed,
            failed=failed,
            blocked=blocked,
            items=items,
        )
        self._push_taskboard_snapshot(
            timestamp=str(timestamp or ""),
            summary=summary,
            items=items,
            source=source,
            has_activity=has_activity,
        )
        return True

    @classmethod
    def _extract_taskboard_snapshot_candidates(
        cls,
        payload: Any,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Extract taskboard snapshots from nested runtime payloads."""
        if not isinstance(payload, dict):
            return []

        candidates: list[tuple[str, dict[str, Any]]] = []
        stack: list[tuple[str, dict[str, Any]]] = [("root", payload)]
        visited: set[int] = set()

        while stack:
            path, node = stack.pop()
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)

            counts = node.get("counts")
            samples = node.get("samples")
            if isinstance(counts, dict) or isinstance(samples, dict):
                candidates.append((path, node))

            for key in (
                "taskboard",
                "taskboard_before",
                "taskboard_after_claim",
                "taskboard_after",
                "taskboard_before_claim",
                "refs",
                "payload",
                "event",
                "raw",
                "data",
                "output",
                "meta",
            ):
                child = node.get(key)
                if isinstance(child, dict):
                    stack.append((f"{path}.{key}", child))

        return candidates

    def _push_taskboard_from_payload(
        self,
        *,
        timestamp: str,
        payload: Any,
        source_prefix: str,
    ) -> bool:
        """Push taskboard rows extracted from nested payload snapshots."""
        focus_task, running_hint = self._extract_taskboard_focus_task(payload)
        if focus_task is not None:
            self._remember_active_taskboard_task(focus_task, running_hint=running_hint)
        snapshots = self._extract_taskboard_snapshot_candidates(payload)
        pushed = False
        for path, snapshot in snapshots:
            before_len = len(self.panels.get("taskboard_status", []))
            source = f"{source_prefix}.{path}".strip(".")
            self._push_local_taskboard_snapshot(
                timestamp=str(timestamp or ""),
                snapshot=snapshot,
                source=source,
            )
            after_len = len(self.panels.get("taskboard_status", []))
            if after_len > before_len:
                pushed = True
        if focus_task is not None and self._overlay_active_taskboard_snapshot(
            timestamp=str(timestamp or ""),
            source=f"{source_prefix}.focus_task",
            focus_task=focus_task,
            running_hint=running_hint,
        ):
            pushed = True
        return pushed

    @classmethod
    def _extract_taskboard_counts_from_text(cls, text: Any) -> dict[str, int] | None:
        """Extract `TaskBoard total=...` counters from free-form text."""
        token = str(text or "").strip()
        if not token:
            return None
        match = cls._TASKBOARD_BRIEF_PATTERN.search(token)
        if match is None:
            return None
        try:
            return {
                "total": cls._coerce_non_negative_int(match.group("total")),
                "ready": cls._coerce_non_negative_int(match.group("ready")),
                "pending": cls._coerce_non_negative_int(match.group("pending")),
                "running": cls._coerce_non_negative_int(match.group("running")),
                "completed": cls._coerce_non_negative_int(match.group("completed")),
                "failed": cls._coerce_non_negative_int(match.group("failed")),
                "blocked": cls._coerce_non_negative_int(match.group("blocked")),
            }
        except (IndexError, TypeError):
            # IndexError: group name doesn't exist in regex
            # TypeError: wrong type passed to group()
            return None

    def _push_taskboard_from_text(
        self,
        *,
        timestamp: str,
        text: Any,
        source: str,
    ) -> bool:
        """Push taskboard summary parsed from textual taskboard briefs."""
        counts = self._extract_taskboard_counts_from_text(text)
        if not counts:
            return False

        summary = self._build_taskboard_summary(
            total=counts["total"],
            ready=counts["ready"],
            pending=counts["pending"],
            running=counts["running"],
            completed=counts["completed"],
            failed=counts["failed"],
            blocked=counts["blocked"],
        )
        has_activity = self._has_non_empty_taskboard_snapshot(
            total=counts["total"],
            ready=counts["ready"],
            pending=counts["pending"],
            running=counts["running"],
            completed=counts["completed"],
            failed=counts["failed"],
            blocked=counts["blocked"],
            items=[],
        )
        before_len = len(self.panels.get("taskboard_status", []))
        self._push_taskboard_snapshot(
            timestamp=str(timestamp or ""),
            summary=summary,
            items=[],
            source=str(source or "taskboard.text"),
            has_activity=has_activity,
        )
        return len(self.panels.get("taskboard_status", [])) > before_len

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
        except websockets.exceptions.ConnectionClosed:
            # ConnectionClosed: WS disconnected, not an error in connect flow
            self.connection_error = "ws_connect_failed:connection_closed"
            logger.debug("WS connect error: %s", e)
        except Exception as e:
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

    def _push_local_taskboard_snapshot(self, *, timestamp: str, snapshot: Any, source: str) -> None:
        """将 adapter_debug 中的 taskboard 快照写入 taskboard 面板。"""
        payload = snapshot if isinstance(snapshot, dict) else {}
        if not payload:
            return

        counts = payload.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        samples = payload.get("samples")
        samples = samples if isinstance(samples, dict) else {}

        total = self._coerce_non_negative_int(counts.get("total"))
        ready = self._coerce_non_negative_int(counts.get("ready"))
        pending = self._coerce_non_negative_int(counts.get("pending"))
        in_progress = self._coerce_non_negative_int(counts.get("in_progress"))
        completed = self._coerce_non_negative_int(counts.get("completed"))
        failed = self._coerce_non_negative_int(counts.get("failed"))
        blocked = self._coerce_non_negative_int(counts.get("blocked"))

        items: list[dict[str, Any]] = []
        for bucket in ("in_progress", "ready", "pending", "completed", "failed", "blocked"):
            rows = samples.get(bucket)
            if not isinstance(rows, list):
                continue
            items.extend(
                self._normalize_taskboard_items(
                    rows,
                    default_status=bucket,
                    limit=6,
                )
            )
        items = self._dedupe_taskboard_items(items, limit=16)

        if total <= 0:
            total = len(items)
        summary = self._build_taskboard_summary(
            total=total,
            ready=ready,
            pending=pending,
            running=in_progress,
            completed=completed,
            failed=failed,
            blocked=blocked,
        )
        has_activity = self._has_non_empty_taskboard_snapshot(
            total=total,
            ready=ready,
            pending=pending,
            running=in_progress,
            completed=completed,
            failed=failed,
            blocked=blocked,
            items=items,
        )
        self._push_taskboard_snapshot(
            timestamp=str(timestamp or ""),
            summary=summary,
            items=items,
            source=str(source or "local_taskboard"),
            has_activity=has_activity,
        )

    def _push_panel(self, panel_name: str, payload: dict[str, Any]) -> None:
        """向面板添加数据。"""
        rows = self.panels.setdefault(panel_name, [])
        rows.append(payload)
        if len(rows) > self._max_panel_items:
            del rows[: len(rows) - self._max_panel_items]

    def _push_llm_panel(self, payload: dict[str, Any]) -> None:
        """向 LLM 面板添加数据（合并连续的 chunk）。"""
        rows = self.panels.setdefault("llm_reasoning", [])
        event_type = str(payload.get("event_type") or "")
        stream_key = str(payload.get("stream_key") or "")

        if event_type in {"thinking_chunk", "content_chunk"} and rows:
            last = rows[-1]
            if str(last.get("event_type") or "") == event_type and str(last.get("stream_key") or "") == stream_key:
                merged_content = str(last.get("content") or "") + str(payload.get("content") or "")
                merged = dict(last)
                merged.update(payload)
                merged["content"] = merged_content[-self._max_llm_content_chars :]
                rows[-1] = merged
                return

        rows.append(payload)
        if len(rows) > self._max_panel_items:
            del rows[: len(rows) - self._max_panel_items]

    @staticmethod
    def _safe_json_compact(value: Any, max_chars: int = 220) -> str:
        """将值压缩为紧凑的 JSON 字符串。"""
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (ValueError, TypeError):
            # ValueError/TypeError: non-serializable object passed
            text = str(value)
        text = str(text).replace("\n", " ").strip()
        if len(text) > max_chars:
            return f"{text[:max_chars]}..."
        return text

    @staticmethod
    def _compact_text_preview(value: Any, max_chars: int = 240, preserve_lines: bool = False) -> str:
        """压缩文本预览。"""
        raw_text = str(value or "").strip()
        if not raw_text:
            return ""

        if preserve_lines:
            lines = raw_text.split("\n")
            result_lines = []
            total_chars = 0
            for line in lines:
                if total_chars + len(line) > max_chars - 3:
                    remaining = max_chars - total_chars - 3
                    if remaining > 0:
                        result_lines.append(line[:remaining] + "...")
                    else:
                        result_lines.append("...")
                    break
                result_lines.append(line)
                total_chars += len(line) + 1
            return "\n".join(result_lines)
        else:
            text = " ".join(raw_text.split()).strip()
            if len(text) > max_chars:
                return f"{text[:max_chars]}..."
            return text

    @staticmethod
    def _compact_patch_preview(value: Any, *, max_lines: int = 180, max_chars: int = 12000) -> str:
        """压缩 patch 文本，保留 diff 行结构。"""
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text:
            return ""

        lines = text.split("\n")
        truncated_by_lines = False
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated_by_lines = True
        compact = "\n".join(lines)

        truncated_by_chars = False
        if max_chars > 0 and len(compact) > max_chars:
            compact = compact[:max_chars]
            truncated_by_chars = True

        if truncated_by_lines or truncated_by_chars:
            compact = f"{compact}\n... [diff truncated]"
        return compact

    def _normalize_llm_stream_item(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """归一化 LLM 流事件。"""
        raw_event = msg.get("event")
        payload = raw_event if isinstance(raw_event, dict) else {}
        line = msg.get("line", "")

        if not payload and isinstance(line, str):
            text = line.strip()
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    # Malformed JSON, ignore and use empty payload
                    payload = {}

        event_raw = payload.get("raw")
        event_raw = event_raw if isinstance(event_raw, dict) else {}
        event_type = str(event_raw.get("stream_event") or event_raw.get("event") or payload.get("event") or "").strip()
        timestamp = str(payload.get("ts") or msg.get("timestamp") or "")
        channel = str(msg.get("channel") or payload.get("channel") or "llm")
        role = str(payload.get("actor") or event_raw.get("role") or "")
        stream_key = f"{channel}:{role or 'unknown'}"

        content = ""
        if event_type in {"thinking_chunk", "content_chunk"}:
            content = str(event_raw.get("content") or payload.get("message") or "")
        elif event_type == "tool_call":
            tool = str(event_raw.get("tool") or "unknown")
            args = event_raw.get("args") or {}
            args_text = self._safe_json_compact(args)
            content = f"{tool}({args_text})"

            # 保存完整的工具调用详情供后续展示
            return {
                "timestamp": timestamp,
                "channel": channel,
                "role": role,
                "event_type": event_type or "llm_stream",
                "stream_key": stream_key,
                "content": content,
                "tool_name": tool,
                "tool_args": args,
            }
        elif event_type == "tool_result":
            tool = str(event_raw.get("tool") or "unknown")
            success = event_raw.get("success")
            result_payload = event_raw.get("result") if isinstance(event_raw.get("result"), dict) else {}
            if success is None and isinstance(result_payload, dict):
                success = result_payload.get("success")
            if success is True:
                status = "ok"
            elif success is False:
                status = "failed"
            else:
                status = "ok" if not str(result_payload.get("error") or "").strip() else "failed"
            detail = ""
            if isinstance(result_payload, dict):
                detail = str(result_payload.get("error") or result_payload.get("message") or "")
                if not detail:
                    detail = self._safe_json_compact(result_payload, max_chars=160)
            if detail:
                detail_text = self._compact_text_preview(detail, max_chars=240)
                if status == "failed":
                    content = f"{tool} -> {status}\nreason: {detail_text}"
                else:
                    content = f"{tool} -> {status} ({detail_text})"
            else:
                content = f"{tool} -> {status}"

            # 保存完整的工具结果详情供后续展示
            full_result = event_raw.get("result")
            return {
                "timestamp": timestamp,
                "channel": channel,
                "role": role,
                "event_type": event_type or "llm_stream",
                "stream_key": stream_key,
                "content": content,
                "tool_name": tool,
                "tool_status": status,
                "tool_success": success,
                "tool_result_raw": full_result,
                "tool_args": event_raw.get("args"),
            }
        elif event_type == "error":
            content = str(event_raw.get("error") or payload.get("message") or "")
        else:
            content = str(payload.get("message") or payload.get("content") or line or "").strip()

        content = str(content or "").strip()
        if not content:
            return None

        if len(content) > self._max_llm_content_chars:
            content = content[-self._max_llm_content_chars :]

        return {
            "timestamp": timestamp,
            "channel": channel,
            "role": role,
            "event_type": event_type or "llm_stream",
            "stream_key": stream_key,
            "content": content,
        }

    def _normalize_dialogue_item(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """归一化对话事件。"""
        raw_event = msg.get("event")
        payload = raw_event if isinstance(raw_event, dict) else {}
        line = msg.get("line", "")

        if not payload and isinstance(line, str):
            text = line.strip()
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    # Malformed JSON, ignore and use empty payload
                    payload = {}

        timestamp = str(payload.get("ts") or payload.get("timestamp") or msg.get("timestamp") or "")
        speaker = str(
            payload.get("speaker") or payload.get("actor") or payload.get("role") or payload.get("source") or "unknown"
        ).strip()
        dialogue_type = str(payload.get("type") or payload.get("kind") or "dialogue").strip().lower()
        text = str(
            payload.get("text")
            or payload.get("summary")
            or payload.get("content")
            or payload.get("message")
            or line
            or ""
        ).strip()
        if not text:
            if payload:
                text = self._safe_json_compact(payload, max_chars=self._max_dialogue_chars)
            else:
                return None
        if len(text) > self._max_dialogue_chars:
            text = text[: self._max_dialogue_chars] + "..."
        return {
            "timestamp": timestamp,
            "speaker": speaker,
            "dialogue_type": dialogue_type,
            "content": text,
        }

    @staticmethod
    def _infer_role_from_runtime_payload(payload: dict[str, Any], content: str) -> str:
        """Infer role from runtime payload and message text."""
        role = ""
        code = str(payload.get("code") or payload.get("event_code") or "").strip().lower()
        if code and "." in code:
            candidate = code.split(".", 1)[0]
            if candidate in {"architect", "pm", "director", "qa", "chief_engineer"}:
                role = candidate
        if role:
            return role
        content_lower = str(content or "").strip().lower()
        for candidate in ("architect", "pm", "director", "qa", "chief_engineer"):
            if candidate in content_lower:
                return candidate
        return "unknown"

    def _push_llm_lifecycle_hint(
        self,
        *,
        timestamp: str,
        role: str,
        event_type: str,
        content: str,
    ) -> None:
        """Push synthetic LLM lifecycle event to reasoning panel."""
        normalized_role = str(role or "unknown").strip().lower() or "unknown"
        normalized_type = str(event_type or "").strip().lower()
        if normalized_type not in {"llm_waiting", "llm_completed", "llm_failed"}:
            return
        payload = {
            "timestamp": str(timestamp or ""),
            "channel": "runtime_event",
            "role": normalized_role,
            "event_type": normalized_type,
            "stream_key": f"runtime_hint:{normalized_role}",
            "content": str(content or "")[: self._max_llm_content_chars],
        }
        self._push_llm_panel(payload)

    def _project_llm_lifecycle_from_runtime_payload(
        self,
        *,
        timestamp: str,
        payload: dict[str, Any],
        content: str,
    ) -> None:
        """Map runtime/task-trace events into LLM lifecycle visualization hints."""
        detail = " ".join(
            [
                str(payload.get("code") or ""),
                str(payload.get("step_title") or ""),
                str(payload.get("step_detail") or ""),
                str(payload.get("reason") or ""),
                str(content or ""),
            ]
        ).strip()
        detail_lower = detail.lower()
        if "llm" not in detail_lower and "first_call" not in detail_lower and "retry_call" not in detail_lower:
            return

        role = self._infer_role_from_runtime_payload(payload, detail)
        if any(
            marker in detail_lower
            for marker in (
                ".started",
                "call started",
                "waiting for first llm response",
                "retrying",
                "force-write retry started",
            )
        ):
            self._push_llm_lifecycle_hint(
                timestamp=timestamp,
                role=role,
                event_type="llm_waiting",
                content=detail,
            )
            return

        if any(
            marker in detail_lower
            for marker in (
                "timeout",
                "failed",
                "format_validation_failed",
                "llm_error",
                "no_writable_output_after_retry",
            )
        ):
            self._push_llm_lifecycle_hint(
                timestamp=timestamp,
                role=role,
                event_type="llm_failed",
                content=detail,
            )
            return

        if any(
            marker in detail_lower
            for marker in (
                "tools.first_round.summary",
                "tools.retry_round.summary",
                "tools.force_retry_round.summary",
                "response",
                "summary",
            )
        ):
            self._push_llm_lifecycle_hint(
                timestamp=timestamp,
                role=role,
                event_type="llm_completed",
                content=detail,
            )

    @staticmethod
    def _normalize_role_token(value: Any) -> str:
        """归一化角色标识。"""
        token = str(value or "").strip().lower()
        mapping = {
            "architect": "architect",
            "pm": "pm",
            "director": "director",
            "qa": "qa",
            "chief engineer": "chief_engineer",
            "chief_engineer": "chief_engineer",
        }
        return mapping.get(token, token or "unknown")

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
    def _infer_llm_event_type_from_runtime_v2(kind: str, content: str, tags: Any = None) -> str:
        """根据 runtime.v2 kind/content 推断 LLM 事件类型。"""
        projection_event = RuntimeProjection._extract_projection_event_type(tags)
        if projection_event:
            return projection_event

        kind_token = str(kind or "").strip().lower()
        content_token = str(content or "").strip().lower()

        if "llm_waiting" in kind_token:
            return "llm_waiting"
        if "llm_completed" in kind_token:
            return "llm_completed"
        if "llm_failed" in kind_token:
            return "llm_failed"
        if "thinking" in kind_token:
            return "thinking_chunk"
        if "tool.call" in kind_token or "tool_call" in kind_token:
            return "tool_call"
        if "tool.result" in kind_token or "tool_result" in kind_token:
            return "tool_result"
        if "error" in kind_token or "failed" in kind_token:
            return "llm_failed" if "llm" in kind_token else "error"
        if "content" in kind_token or "response" in kind_token:
            return "content_chunk"

        if "llm_waiting" in content_token or "thinking" in content_token:
            return "llm_waiting"
        if "llm_completed" in content_token:
            return "llm_completed"
        if "llm_failed" in content_token:
            return "llm_failed"
        if "tool_call" in content_token:
            return "tool_call"
        if "tool_result" in content_token:
            return "tool_result"
        if "error" in content_token or "failed" in content_token:
            return "error"
        return "content_preview"

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
        except Exception as e:
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

    def get_panels(self) -> dict[str, list]:
        """获取所有面板数据。"""
        return {
            "chain_status": list(self.panels.get("chain_status", [])),
            "llm_reasoning": list(self.panels.get("llm_reasoning", [])),
            "dialogue_stream": list(self.panels.get("dialogue_stream", [])),
            "tool_activity": list(self.panels.get("tool_activity", [])),
            "taskboard_status": list(self.panels.get("taskboard_status", [])),
            "code_diff": list(self.panels.get("code_diff", [])),
            "realtime_events": list(self.panels.get("realtime_events", [])),
        }

    def _poll_local_projection_once(self) -> None:
        """本地轮询已下线，防止出现伪实时回放。"""
        raise RuntimeError("Local polling has been removed. Use WS runtime.v2 (JetStream) push only.")
