"""Cell-local Shangshuling registry port for PM dispatch.

This module keeps the PM dispatch Cell self-contained.  It provides a
lightweight registry backed by the Cell-owned runtime/dispatch state path,
avoiding any dependency on ``polaris.delivery.*``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
    ShangshulingPort,
    normalize_task_status,
)
from polaris.cells.runtime.state_owner.public.service import write_json_atomic
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.fs.text_ops import read_file_safe
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.traceability.internal.safety import (
    safe_find_node,
    safe_link,
    safe_register_node,
)
from polaris.kernelone.utils import _now

logger = logging.getLogger(__name__)

_REGISTRY_REL_PATH = "runtime/state/dispatch/shangshuling.registry.json"
_HISTORY_REL_PATH = "runtime/state/dispatch/shangshuling.history.jsonl"
_ACTIVE_STATUSES = {"todo", "in_progress", "review", "needs_continue"}
_TERMINAL_STATUSES = {"done", "failed", "blocked"}


def _now_iso() -> str:
    return _now()


def _registry_path(workspace_full: str) -> str:
    return resolve_runtime_path(workspace_full, _REGISTRY_REL_PATH)


def _history_path(workspace_full: str) -> str:
    return resolve_runtime_path(workspace_full, _HISTORY_REL_PATH)


def _load_registry(workspace_full: str) -> dict[str, Any]:
    raw = read_file_safe(_registry_path(workspace_full))
    if not raw.strip():
        return {
            "version": 1,
            "workspace": str(workspace_full or "").strip(),
            "updated_at": "",
            "tasks": [],
        }
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Failed to parse shangshuling registry; using empty fallback", exc_info=True)
        return {
            "version": 1,
            "workspace": str(workspace_full or "").strip(),
            "updated_at": "",
            "tasks": [],
        }
    if not isinstance(payload, dict):
        return {
            "version": 1,
            "workspace": str(workspace_full or "").strip(),
            "updated_at": "",
            "tasks": [],
        }
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        payload["tasks"] = []
    return payload


def _save_registry(workspace_full: str, registry: dict[str, Any]) -> None:
    registry = dict(registry)
    registry["workspace"] = str(workspace_full or "").strip()
    registry["updated_at"] = _now_iso()
    write_json_atomic(_registry_path(workspace_full), registry)


def _task_identity(task: dict[str, Any]) -> str:
    token = str(task.get("id") or task.get("legacy_id") or "").strip()
    if token:
        return token
    metadata = task.get("metadata")
    if isinstance(metadata, dict):
        token = str(metadata.get("legacy_id") or "").strip()
    return token


def _priority_value(task: dict[str, Any]) -> int:
    raw = task.get("priority")
    if raw is None:
        return 5  # default medium priority
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        token = str(raw).strip().lower()
        aliases = {"critical": 0, "high": 1, "medium": 5, "low": 9}
        return aliases.get(token, 5)


def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(task)
    normalized["id"] = _task_identity(normalized)
    normalized["status"] = normalize_task_status(normalized.get("status"))
    normalized["priority"] = _priority_value(normalized)
    normalized.setdefault("metadata", {})
    if not isinstance(normalized["metadata"], dict):
        normalized["metadata"] = {}
    return normalized


class LocalShangshulingPort(ShangshulingPort):
    """Cell-local ShangshulingPort implementation backed by runtime/dispatch."""

    def sync_tasks_to_shangshuling(
        self,
        workspace_full: str,
        tasks: list[dict[str, Any]],
    ) -> int:
        if not isinstance(tasks, list) or not tasks:
            return 0

        registry = _load_registry(workspace_full)
        existing = registry.get("tasks")
        task_map: dict[str, dict[str, Any]] = {}
        if isinstance(existing, list):
            for item in existing:
                if not isinstance(item, dict):
                    continue
                task_id = _task_identity(item)
                if task_id:
                    task_map[task_id] = dict(item)

        synced = 0
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = _task_identity(task)
            if not task_id:
                continue
            task_map[task_id] = _normalize_task(task)
            synced += 1

        registry["tasks"] = list(task_map.values())
        _save_registry(workspace_full, registry)
        return synced

    def get_shangshuling_ready_tasks(
        self,
        workspace_full: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        registry = _load_registry(workspace_full)
        raw_tasks = registry.get("tasks")
        tasks = [dict(item) for item in raw_tasks if isinstance(item, dict)] if isinstance(raw_tasks, list) else []
        ready_tasks = [task for task in tasks if normalize_task_status(task.get("status")) not in _TERMINAL_STATUSES]
        ready_tasks.sort(key=lambda item: (_priority_value(item), _task_identity(item)))
        if limit > 0:
            return ready_tasks[:limit]
        return ready_tasks

    def record_shangshuling_task_completion(
        self,
        workspace_full: str,
        task_id: str,
        success: bool,
        metadata: dict[str, Any],
    ) -> bool:
        registry = _load_registry(workspace_full)
        raw_tasks = registry.get("tasks")
        tasks = [dict(item) for item in raw_tasks if isinstance(item, dict)] if isinstance(raw_tasks, list) else []

        updated = False
        normalized_status = "done" if success else "failed"
        task_token = str(task_id or "").strip()
        for item in tasks:
            if _task_identity(item) != task_token and str(item.get("legacy_id") or "").strip() != task_token:
                metadata_raw = item.get("metadata")
                metadata_payload = metadata_raw if isinstance(metadata_raw, dict) else {}
                if str(metadata_payload.get("legacy_id") or "").strip() != task_token:
                    continue
            item["status"] = normalized_status
            item["updated_at"] = _now_iso()
            if isinstance(metadata, dict) and metadata:
                item_metadata_raw = item.get("metadata")
                item_metadata = item_metadata_raw if isinstance(item_metadata_raw, dict) else {}
                item["metadata"] = {**item_metadata, **metadata}
            updated = True
            break

        if not updated:
            return False

        registry["tasks"] = tasks
        _save_registry(workspace_full, registry)
        return True

    def archive_task_history(
        self,
        workspace_full: str,
        cache_root_full: str,
        run_id: str,
        iteration: int,
        normalized: dict[str, Any],
        director_result: Any,
        timestamp: str,
        trace_service: Any = None,
    ) -> None:
        record = {
            "timestamp": str(timestamp or "").strip() or _now_iso(),
            "workspace": str(workspace_full or "").strip(),
            "cache_root": str(cache_root_full or "").strip(),
            "run_id": str(run_id or "").strip(),
            "iteration": int(iteration or 0),
            "normalized": normalized if isinstance(normalized, dict) else {},
            "director_result": director_result if isinstance(director_result, dict) else {},
        }
        append_jsonl(_history_path(workspace_full), record)

        # Redundant traceability link verification: ensure doc -> task links exist
        if trace_service is not None:
            doc_node = safe_find_node(trace_service, run_id, "doc")
            if doc_node is None:
                doc_node = safe_register_node(
                    trace_service,
                    node_kind="doc",
                    role="pm",
                    external_id=run_id,
                    content=json.dumps(normalized, ensure_ascii=False)[:1024],
                )
            tasks = normalized.get("tasks") if isinstance(normalized, dict) else []
            for task in tasks if isinstance(tasks, list) else []:
                task_id = str(task.get("id") or "").strip()
                if not task_id:
                    continue
                task_node = safe_find_node(trace_service, task_id, "task")
                if task_node is None:
                    task_node = safe_register_node(
                        trace_service,
                        node_kind="task",
                        role="pm",
                        external_id=task_id,
                        content=json.dumps(task, ensure_ascii=False)[:1024],
                    )
                if doc_node is not None and task_node is not None:
                    safe_link(trace_service, doc_node, task_node, "derives_from")


def get_shangshuling_port() -> ShangshulingPort:
    """Return the cell-local Shangshuling port."""
    return LocalShangshulingPort()
