"""状态追踪服务

包含 TaskBoard 状态追踪、文件指纹收集、调试事件记录等功能。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.fs.text_ops import open_text_log_append
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_string(*values: Any) -> str:
    for value in values:
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _final_request_audit_projection(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _mapping(payload.get("metadata"))
    data = _mapping(payload.get("data"))
    data_metadata = _mapping(data.get("metadata"))
    audit_refs = _mapping(payload.get("audit_refs"))
    final_request_evidence = _mapping(
        payload.get("final_request_evidence")
        or data.get("final_request_evidence")
        or metadata.get("final_request_evidence")
        or data_metadata.get("final_request_evidence")
    )
    final_audit = _mapping(
        payload.get("final_request_context_audit")
        or data.get("final_request_context_audit")
        or metadata.get("final_request_context_audit")
        or data_metadata.get("final_request_context_audit")
    )
    projection: dict[str, Any] = {}
    context_snapshot_ref = _first_string(
        payload.get("context_snapshot_ref"),
        data.get("context_snapshot_ref"),
        metadata.get("context_snapshot_ref"),
        data_metadata.get("context_snapshot_ref"),
        audit_refs.get("context_snapshot_ref"),
        final_request_evidence.get("context_snapshot_ref"),
    )
    if context_snapshot_ref:
        projection["context_snapshot_ref"] = context_snapshot_ref
    if final_audit:
        projection["final_request_context_audit"] = final_audit
        projection["final_request_context_audit_present"] = True
        projection["final_request_context_audit_hash"] = _stable_hash(final_audit)
    elif final_request_evidence.get("final_request_context_audit_present") is not None:
        projection["final_request_context_audit_present"] = bool(
            final_request_evidence.get("final_request_context_audit_present")
        )
    for key in (
        "final_request_context_audit_hash",
        "final_request_evidence_hash",
        "final_request_evidence_authority_hash",
    ):
        value = _first_string(payload.get(key), data.get(key), audit_refs.get(key), final_request_evidence.get(key))
        if value:
            projection[key] = value
    if final_request_evidence:
        projection["final_request_evidence"] = final_request_evidence
    return projection


class DirectorStateTracker:
    """Director 状态追踪服务。

    提供 TaskBoard 状态追踪、文件指纹收集、调试事件记录等功能。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    # -------------------------------------------------------------------------
    # 文件指纹收集
    # -------------------------------------------------------------------------

    def collect_workspace_code_files(self) -> dict[str, str]:
        """收集工作区代码文件快照指纹

        Returns:
            字典: {文件相对路径: 文件指纹}
        """
        root = Path(self.workspace).resolve()
        if not root.exists() or not root.is_dir():
            return {}
        ignored_roots = {
            ".polaris",
            "stress_reports",
            ".git",
            ".pytest_cache",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
        }
        results: dict[str, str] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if not rel.parts:
                continue
            if rel.parts[0] in ignored_roots:
                continue
            if any(part in ignored_roots for part in rel.parts):
                continue
            from .helpers import is_project_code_file

            if is_project_code_file(path.suffix):
                rel_path = rel.as_posix()
                stat_info = path.stat()
                size = int(getattr(stat_info, "st_size", 0) or 0)
                content_hash = self._build_file_content_fingerprint(path, size)
                results[rel_path] = f"{size}:{content_hash}"
        return results

    @staticmethod
    def _build_file_content_fingerprint(path: Path, size: int) -> str:
        """构建文件内容指纹"""
        try:
            if size <= 2 * 1024 * 1024:
                with path.open("rb") as handle:
                    return hashlib.sha1(handle.read()).hexdigest()[:16]
            with path.open("rb") as handle:
                head = handle.read(64 * 1024)
                if size > 128 * 1024:
                    handle.seek(max(0, size - 64 * 1024))
                tail = handle.read(64 * 1024)
            return hashlib.sha1(head + tail).hexdigest()[:16]
        except OSError:
            return "unreadable"

    # -------------------------------------------------------------------------
    # 调试事件记录
    # -------------------------------------------------------------------------

    def append_debug_event(
        self,
        task_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Append adapter-level debug trace for diagnosing dispatch stalls/failures."""
        try:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            metadata_dir = get_workspace_metadata_dir_name()
            log_path = (
                Path(self.workspace)
                / metadata_dir
                / "runtime"
                / "roles"
                / "director"
                / "logs"
                / f"adapter_debug_{day}.jsonl"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": str(task_id or "").strip(),
                "event": str(event or "").strip(),
                "payload": payload if isinstance(payload, dict) else {},
            }
            with open_text_log_append(str(log_path)) as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            return

    def append_director_llm_event(
        self,
        task_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Append Director runtime event evidence to the canonical LLM event stream."""
        try:
            now = datetime.now(timezone.utc)
            event_path = resolve_runtime_path(self.workspace, "runtime/events/director.llm.events.jsonl")
            record = {
                "ts_epoch": now.timestamp(),
                "timestamp": now.isoformat(),
                "event_id": uuid.uuid4().hex,
                "name": str(event or "").strip() or "director_event",
                "role": "director",
                "task_id": str(task_id or "").strip(),
                "payload": payload if isinstance(payload, dict) else {},
            }
            if isinstance(payload, dict):
                record.update(_final_request_audit_projection(payload))
            append_jsonl(str(event_path), record, buffered=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return

    # -------------------------------------------------------------------------
    # TaskBoard 快照构建
    # -------------------------------------------------------------------------

    def build_taskboard_observation_snapshot(
        self,
        task_runtime: Any,
        *,
        sample_limit: int = 5,
    ) -> dict[str, Any]:
        """Build a taskboard snapshot for LLM context."""
        counts = {
            "total": 0,
            "ready": 0,
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
        }
        samples: dict[str, list[dict[str, str]]] = {
            "ready": [],
            "pending": [],
            "in_progress": [],
            "completed": [],
            "failed": [],
            "blocked": [],
        }
        task_rows = task_runtime.list_observable_task_rows()
        get_observable_stats = getattr(task_runtime, "get_observable_task_row_stats", None)
        stats = get_observable_stats() if callable(get_observable_stats) else {}
        if isinstance(stats, dict):
            for key in counts:
                raw = stats.get(key)
                try:
                    counts[key] = max(0, int(raw)) if raw is not None else counts[key]
                except (TypeError, ValueError):
                    continue
        self._collect_taskboard_samples(task_runtime, task_rows, counts, samples, sample_limit)
        list_ready_task_rows = getattr(task_runtime, "list_ready_task_rows", None)
        ready_entries = list_ready_task_rows() if callable(list_ready_task_rows) else []
        self._add_ready_samples(ready_entries, samples, sample_limit)
        if counts["total"] <= 0 and task_rows:
            counts["total"] = len(task_rows)
        if counts["ready"] <= 0 and ready_entries:
            counts["ready"] = len(ready_entries)
        return {"counts": counts, "samples": samples}

    def _collect_taskboard_samples(
        self,
        task_runtime: Any,
        entries: list,
        counts: dict,
        samples: dict,
        sample_limit: int,
    ) -> None:
        """Collect task samples from entries."""
        from .helpers import coerce_task_record

        for entry in entries:
            record = coerce_task_record(entry)
            status = str(record.get("status") or "").strip().lower()
            task_id = str(record.get("id") or "").strip()
            subject = str(record.get("subject") or record.get("title") or "").strip()
            _raw_metadata = record.get("metadata")
            metadata: dict[str, Any] = _raw_metadata if isinstance(_raw_metadata, dict) else {}
            _raw_adapter_result = metadata.get("adapter_result") if isinstance(metadata, dict) else None
            adapter_result: dict[str, Any] = _raw_adapter_result if isinstance(_raw_adapter_result, dict) else {}
            qa_state = self._derive_qa_state(adapter_result, metadata, status)
            sample = {
                "id": task_id or "?",
                "subject": subject[:120],
                "qa_state": qa_state,
                "claimed_by": str(record.get("claimed_by") or metadata.get("claimed_by") or "").strip(),
                "execution_backend": str(record.get("execution_backend") or metadata.get("execution_backend") or "")
                .strip()
                .lower(),
                "resume_state": str(record.get("resume_state") or metadata.get("resume_state") or "").strip(),
                "session_id": str(record.get("session_id") or "").strip(),
                "workflow_run_id": str(record.get("workflow_run_id") or metadata.get("workflow_run_id") or "").strip(),
            }
            if status in samples and len(samples[status]) < max(1, int(sample_limit)):
                samples[status].append(sample)

    @staticmethod
    def _derive_qa_state(
        adapter_result: dict,
        metadata: dict,
        status: str,
    ) -> str:
        """Derive QA state from adapter_result and metadata."""
        qa_state = ""
        if isinstance(adapter_result, dict):
            qa_required = bool(adapter_result.get("qa_required_for_final_verdict"))
            qa_passed = adapter_result.get("qa_passed")
            if qa_required:
                if qa_passed is True:
                    qa_state = "passed"
                elif qa_passed is False:
                    qa_state = "failed" if status == "completed" else "rework"
                elif qa_passed is None:
                    qa_state = "pending" if status == "completed" else ""
        if not qa_state and bool(metadata.get("qa_rework_requested")):
            qa_state = "rework"
        if status == "failed" and bool(metadata.get("qa_rework_exhausted")):
            qa_state = "exhausted"
        return qa_state

    def _add_ready_samples(
        self,
        ready_entries: list,
        samples: dict,
        sample_limit: int,
    ) -> None:
        """Add ready task samples."""
        from .helpers import coerce_task_record

        seen_ids = {
            str(item.get("id") or "").strip()
            for rows in samples.values()
            if isinstance(rows, list)
            for item in rows
            if isinstance(item, dict)
        }
        for entry in ready_entries:
            record = coerce_task_record(entry)
            task_id = str(record.get("id") or "").strip()
            subject = str(record.get("subject") or record.get("title") or "").strip()
            if task_id and task_id in seen_ids:
                continue
            sample = {"id": task_id or "?", "subject": subject[:120]}
            if len(samples["ready"]) < max(1, int(sample_limit)):
                samples["ready"].append(sample)
                if task_id:
                    seen_ids.add(task_id)

    # -------------------------------------------------------------------------
    # TaskBoard Task Reference
    # -------------------------------------------------------------------------

    def build_taskboard_task_ref(self, task_id: str, get_task_fn: Any) -> dict[str, Any]:
        """Build a structured taskboard row reference for realtime observers."""
        task = get_task_fn(task_id)
        if not isinstance(task, dict):
            return {}
        metadata_raw = task.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        runtime_execution_raw = metadata.get("runtime_execution")
        runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
        projection_raw = metadata.get("projection")
        projection: dict[str, Any] = projection_raw if isinstance(projection_raw, dict) else {}
        return {
            "id": str(task.get("id") or "").strip(),
            "subject": str(task.get("subject") or task.get("title") or "").strip(),
            "status": str(task.get("status") or "").strip().lower(),
            "raw_status": str(task.get("raw_status") or "").strip().lower(),
            "qa_state": str(task.get("qa_state") or "").strip().lower(),
            "claimed_by": str(task.get("claimed_by") or metadata.get("claimed_by") or "").strip(),
            "execution_backend": str(task.get("execution_backend") or metadata.get("execution_backend") or "")
            .strip()
            .lower(),
            "execution_backend_source": str(metadata.get("execution_backend_source") or "").strip(),
            "resume_state": str(task.get("resume_state") or metadata.get("resume_state") or "").strip().lower(),
            "session_id": str(task.get("session_id") or runtime_execution.get("session_id") or "").strip(),
            "workflow_run_id": str(task.get("workflow_run_id") or metadata.get("workflow_run_id") or "").strip(),
            "projection_scenario": str(projection.get("scenario_id") or "").strip().lower(),
            "projection_experiment_id": str(projection.get("experiment_id") or "").strip(),
        }

    # -------------------------------------------------------------------------
    # Progress Update
    # -------------------------------------------------------------------------

    def mark_rework_round_started(
        self,
        task_id: str,
        get_task_fn: Any,
        update_board_task_fn: Any,
    ) -> None:
        """Clear QA rework request marker when Director starts a new attempt."""
        board_task = get_task_fn(task_id)
        if not isinstance(board_task, dict):
            return
        metadata = board_task.get("metadata") if isinstance(board_task.get("metadata"), dict) else {}
        if not isinstance(metadata, dict):
            return
        if not bool(metadata.get("qa_rework_requested")):
            return
        update_board_task_fn(
            task_id,
            metadata={
                "qa_rework_requested": False,
                "qa_rework_active": True,
                "qa_rework_last_started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # -------------------------------------------------------------------------
    # 任务描述清理
    # -------------------------------------------------------------------------

    @staticmethod
    def sanitize_task_description(raw_text: str, *, max_chars: int = 280) -> str:
        """清理和截断任务描述文本"""
        text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            return ""

        cleaned_lines: list[str] = []
        for raw_line in text.split("\n"):
            line = str(raw_line or "").strip()
            if not line:
                continue
            line = re.sub(r"^#+\s*", "", line)
            line = re.sub(r"^[\-\*\d\.\)\(]+\s*", "", line)
            if not line:
                continue
            if line.startswith("```"):
                continue
            cleaned_lines.append(line)
            if len(cleaned_lines) >= 6:
                break

        merged = " ".join(cleaned_lines).strip()
        if len(merged) > max_chars:
            merged = merged[:max_chars].rstrip() + "..."
        return merged
