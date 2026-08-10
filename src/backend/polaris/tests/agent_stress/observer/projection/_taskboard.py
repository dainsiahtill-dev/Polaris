from __future__ import annotations

# Cross-mixin method calls resolve at RuntimeProjection composition time.
# mypy: disable-error-code="attr-defined,union-attr,arg-type,return,assignment,has-type,misc"
import json
import logging
import re
from typing import Any

from ._base import RuntimeProjectionBase

logger = logging.getLogger("observer.projection")


class RuntimeProjectionTaskboardMixin(RuntimeProjectionBase):
    """Domain mixin: taskboard methods for RuntimeProjection."""

    _TASKBOARD_BRIEF_PATTERN = re.compile(
        r"taskboard\s+total=(?P<total>\d+)\s+ready=(?P<ready>\d+)\s+"
        r"pending=(?P<pending>\d+)\s+in_progress=(?P<running>\d+)\s+"
        r"completed=(?P<completed>\d+)\s+failed=(?P<failed>\d+)\s+blocked=(?P<blocked>\d+)",
        flags=re.IGNORECASE,
    )

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
            counters[str(key).strip().lower()] = RuntimeProjectionTaskboardMixin._coerce_non_negative_int(value)
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
