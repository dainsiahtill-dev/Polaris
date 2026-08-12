"""Private helpers for runtime projection service package."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from polaris.cells.runtime.projection.internal.io_helpers import (
    read_json,
    resolve_artifact_path,
)

from ._models import RuntimeProjection, logger


def _safe_int(value: Any) -> int:
    """Safely convert value to non-negative integer."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError) as exc:
        logger.warning("_safe_int: failed to convert %r: %s", value, exc)
        return 0


def _dedupe_text_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _ordered_string_union(*values: Any) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            items: Any = [value]
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            continue
        for item in items:
            text = str(item or "").strip()
            marker = text.casefold()
            if not text or marker in seen:
                continue
            seen.add(marker)
            rows.append(text)
    return rows


_TASK_OBSERVABLE_TEXT_MAX_CHARS = 2_048
_TASK_OBSERVABLE_LIST_MAX_ITEMS = 64
_TASK_OBSERVABLE_SUMMARY_SCHEMA = "runtime.task-observable-summary/1"
_TASK_OBSERVABLE_UNSUPPORTED = object()
_TASK_OBSERVABLE_FIELDS = frozenset(
    {
        "id",
        "task_id",
        "subject",
        "title",
        "goal",
        "summary",
        "description",
        "status",
        "state",
        "execution_state",
        "running",
        "done",
        "completed",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "claimed_at",
        "blocked_by",
        "blockedBy",
        "blocks",
        "dependencies",
        "depends_on",
        "owner",
        "assignee",
        "claimed_by",
        "last_claimed_by",
        "role_id",
        "priority",
        "priority_label",
        "tags",
        "estimated_hours",
        "progress_percent",
        "current_file",
        "session_id",
        "attempt",
        "claim_attempt",
        "resume_count",
        "resume_state",
        "resume_available",
        "lease_expires_at",
        "last_heartbeat_at",
        "event_type",
        "run_id",
        "factory_run_id",
        "fact_event_seq",
        "source",
        "error_category",
        "error_code",
        "error_message",
        "last_error",
        "failure_class",
        "failure_reason",
        "responsible_layer",
        "result_summary",
        "target_files",
        "scope_paths",
        "files",
        "acceptance",
        "acceptance_criteria",
        "execution_checklist",
        "steps",
        "evidence_refs",
        "receipt_refs",
        "context_refs",
        "handoff_ready",
        "blueprint_path",
        "runtime_blueprint_path",
    }
)
_TASK_OBSERVABLE_METADATA_FIELDS = frozenset(
    {
        "source",
        "status_source",
        "target_files",
        "scope_paths",
        "files",
        "dependencies",
        "depends_on",
        "blocked_by",
        "blockedBy",
        "acceptance",
        "acceptance_criteria",
        "execution_checklist",
        "steps",
        "evidence_refs",
        "receipt_refs",
        "context_refs",
        "running",
        "handoff_ready",
        "blueprint_path",
        "runtime_blueprint_path",
        "error_category",
        "error_code",
        "error_message",
        "last_error",
        "failure_class",
        "failure_reason",
        "responsible_layer",
    }
)
_TASK_OBSERVABLE_KEY_SUFFIXES = (
    "_id",
    "_ids",
    "_ref",
    "_refs",
    "_hash",
    "_hashes",
    "_count",
    "_counts",
    "_code",
)


def _is_task_observable_key(key: str, *, metadata: bool) -> bool:
    allowed = _TASK_OBSERVABLE_METADATA_FIELDS if metadata else _TASK_OBSERVABLE_FIELDS
    return key in allowed or key.endswith(_TASK_OBSERVABLE_KEY_SUFFIXES)


def _bounded_task_observable_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_TASK_OBSERVABLE_TEXT_MAX_CHARS]
    if isinstance(value, (list, tuple, set, frozenset)):
        source_values = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        bounded: list[Any] = []
        for item in source_values:
            if len(bounded) >= _TASK_OBSERVABLE_LIST_MAX_ITEMS:
                break
            projected = _bounded_task_observable_value(item)
            if projected is _TASK_OBSERVABLE_UNSUPPORTED or isinstance(projected, list):
                continue
            bounded.append(projected)
        return bounded
    return _TASK_OBSERVABLE_UNSUPPORTED


def _project_task_observable_fields(
    source: dict[str, Any],
    *,
    metadata: bool,
) -> tuple[dict[str, Any], set[str]]:
    projected: dict[str, Any] = {}
    retained_keys: set[str] = set()
    for raw_key, value in source.items():
        key = str(raw_key or "").strip()
        if not key or not _is_task_observable_key(key, metadata=metadata):
            continue
        bounded = _bounded_task_observable_value(value)
        if bounded is _TASK_OBSERVABLE_UNSUPPORTED:
            continue
        projected[key] = bounded
        retained_keys.add(key)
        if isinstance(value, (list, tuple, set, frozenset)):
            projected.setdefault(f"{key}_count", len(value))
    return projected, retained_keys


def _project_task_error_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    raw_error = source.get("error")
    if not isinstance(raw_error, dict):
        return
    for target_key, source_keys in (
        ("error_code", ("code", "error_code", "category")),
        ("error_message", ("message", "error_message", "detail")),
    ):
        if target_key in target:
            continue
        for source_key in source_keys:
            bounded = _bounded_task_observable_value(raw_error.get(source_key))
            if bounded is _TASK_OBSERVABLE_UNSUPPORTED or bounded is None or bounded == "":
                continue
            target[target_key] = bounded
            break


def _project_observable_task_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return bounded transport summary without mutating TaskRuntime evidence."""

    summary, retained_row_keys = _project_task_observable_fields(row, metadata=False)
    _project_task_error_fields(summary, row)

    raw_metadata = row.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    metadata_summary, retained_metadata_keys = _project_task_observable_fields(metadata, metadata=True)
    _project_task_error_fields(metadata_summary, metadata)
    if metadata_summary:
        summary["metadata"] = metadata_summary

    summary["observable_summary"] = {
        "schema_version": _TASK_OBSERVABLE_SUMMARY_SCHEMA,
        "source_field_count": len(row),
        "omitted_field_count": max(0, len(row) - len(retained_row_keys) - (1 if "metadata" in row else 0)),
        "source_metadata_field_count": len(metadata),
        "omitted_metadata_field_count": max(0, len(metadata) - len(retained_metadata_keys)),
    }
    return summary


def _is_pm_contract_path(path: str) -> bool:
    """Return True for PM task contract paths emitted by runtime storage."""

    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if resolved.name != "pm_tasks.contract.json":
        return False
    if resolved.parent.name != "contracts":
        return False
    parts = [part.lower() for part in resolved.parts]
    return "projects" in parts and "runtime" in parts


def _runtime_root_from_pm_contract_path(path: str) -> str:
    if not _is_pm_contract_path(path):
        return ""
    try:
        return str(Path(path).expanduser().resolve().parent.parent)
    except (OSError, RuntimeError, ValueError):
        return ""


def _candidate_runtime_roots(projection: RuntimeProjection, resolved_cache_root: str) -> list[str]:
    roots = [str(resolved_cache_root or "").strip()]
    pm_contract_path = ""
    if isinstance(projection.pm_local, dict):
        pm_contract_path = str(projection.pm_local.get("contract_path") or "").strip()
    inferred_root = _runtime_root_from_pm_contract_path(pm_contract_path)
    if inferred_root:
        roots.append(inferred_root)
    return _dedupe_text_values(roots)


def _resolve_runtime_artifact_candidates(workspace: str, runtime_roots: list[str], rel_path: str) -> list[str]:
    candidates: list[str] = []
    if not workspace:
        return candidates
    for runtime_root in runtime_roots:
        try:
            candidates.append(resolve_artifact_path(workspace, runtime_root, rel_path))
        except (RuntimeError, ValueError) as exc:
            logger.debug(
                "runtime artifact candidate rejected: workspace=%r root=%r rel=%r error=%s",
                workspace,
                runtime_root,
                rel_path,
                exc,
            )
    return _dedupe_text_values(candidates)


def _read_first_json_candidate(paths: list[str]) -> dict[str, Any]:
    for path in paths:
        payload = read_json(str(path))
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _workspace_artifact_candidates(workspace: str, rel_paths: list[str]) -> list[str]:
    candidates: list[str] = []
    if not workspace:
        return candidates
    for rel_path in rel_paths:
        try:
            candidates.append(resolve_artifact_path(workspace, "", rel_path))
        except (RuntimeError, ValueError) as exc:
            logger.debug(
                "workspace artifact candidate rejected: workspace=%r rel=%r error=%s",
                workspace,
                rel_path,
                exc,
            )
    return _dedupe_text_values(candidates)


def _read_factory_latest_plan_tasks(workspace: str) -> list[dict[str, Any]]:
    payload = _read_first_json_candidate(
        _workspace_artifact_candidates(
            workspace,
            ["workspace/plans/latest.plan.json"],
        )
    )
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    return [dict(item) for item in raw_tasks if isinstance(item, dict)]


def _read_factory_blueprints_by_task(workspace: str) -> dict[str, dict[str, Any]]:
    candidates = _workspace_artifact_candidates(
        workspace,
        ["workspace/blueprints/latest.review.json"],
    )
    if not candidates:
        return {}
    blueprint_dir = Path(candidates[0]).parent
    try:
        paths = sorted(blueprint_dir.glob("ce_*.json"))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("Failed to scan factory blueprint dir %s: %s", blueprint_dir, exc)
        return {}

    by_task: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = read_json(str(path))
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("task_id") or payload.get("taskId") or "").strip()
        if not task_id:
            continue
        by_task[task_id] = {
            "blueprint_id": str(payload.get("blueprint_id") or payload.get("blueprintId") or path.stem).strip(),
            "runtime_blueprint_path": str(path),
            "blueprint_summary": str(payload.get("summary") or payload.get("objective") or "").strip(),
            "blueprint_status": str(payload.get("status") or "").strip(),
            "handoff_ready": bool(payload.get("handoff_ready")),
            "target_files": payload.get("target_files"),
            "scope_paths": payload.get("scope_paths"),
        }
    return by_task


def _enrich_tasks_with_factory_blueprints(tasks: list[dict[str, Any]], workspace: str) -> list[dict[str, Any]]:
    if not tasks:
        return tasks
    blueprints_by_task = _read_factory_blueprints_by_task(workspace)
    if not blueprints_by_task:
        return tasks

    enriched: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or task.get("task_id") or task.get("taskId") or "").strip()
        blueprint = blueprints_by_task.get(task_id)
        if not blueprint:
            enriched.append(task)
            continue
        merged = dict(task)
        for key, value in blueprint.items():
            if value in (None, "", []):
                continue
            if key in {"target_files", "scope_paths"}:
                merged[key] = _ordered_string_union(merged.get(key), value)
                continue
            merged.setdefault(key, value)
        enriched.append(merged)
    return enriched


def _read_first_text_candidate(paths: list[str]) -> tuple[str, float | None]:
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                return f.read(), os.path.getmtime(path)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "build_snapshot_payload_from_projection: failed to read artifact at %r: %s",
                path,
                exc,
            )
    return "", None


def _state_token(payload: dict[str, Any] | None) -> str:
    """Extract state token from payload."""
    if not isinstance(payload, dict):
        return ""
    # Check top-level state first
    token = str(payload.get("state") or "").strip().upper()
    if token:
        return token
    # Check nested status.state
    nested = payload.get("status")
    if isinstance(nested, dict):
        return str(nested.get("state") or "").strip().upper()
    return ""


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _task_totals(payload: dict[str, Any] | None) -> tuple[int, int]:
    """Get (total, active) task counts from payload."""
    if not isinstance(payload, dict):
        return (0, 0)
    # Check both top-level tasks and nested status.tasks
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        status = payload.get("status")
        if isinstance(status, dict):
            tasks = status.get("tasks")
    if not isinstance(tasks, dict):
        return (0, 0)
    total = _safe_int(tasks.get("total"))
    by_status = tasks.get("by_status")
    if not isinstance(by_status, dict):
        return (total, 0)
    active = (
        _safe_int(by_status.get("IN_PROGRESS"))
        + _safe_int(by_status.get("RUNNING"))
        + _safe_int(by_status.get("CLAIMED"))
    )
    return (total, active)


def _workflow_has_live_rows(payload: dict[str, Any] | None) -> bool:
    """Check if workflow has live task rows."""
    if not isinstance(payload, dict):
        return False
    # Check both top-level tasks and nested status.tasks
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        # Check nested in status
        status = payload.get("status")
        if isinstance(status, dict):
            tasks = status.get("tasks")
    if not isinstance(tasks, dict):
        return False
    rows = tasks.get("task_rows")
    if not isinstance(rows, list):
        return False
    live_tokens = {"RUNNING", "IN_PROGRESS", "CLAIMED", "COMPLETED", "FAILED", "BLOCKED"}
    for item in rows:
        if not isinstance(item, dict):
            continue
        token = str(item.get("status") or item.get("state") or "").strip().upper()
        if token in live_tokens:
            return True
    return False


def _parse_engine_updated_at(value: Any) -> float | None:
    """Parse engine status updated_at to epoch timestamp."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        if "T" in normalized:
            return datetime.fromisoformat(normalized).timestamp()
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("_parse_timestamp: failed to parse %r: %s", text, exc)
        return None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _timestamp_sort_value(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    parsed = _parse_engine_updated_at(str(value))
    return parsed if parsed is not None else 0.0


def _datetime_attr_isoformat(value: Any, attr: str) -> str | None:
    timestamp = getattr(value, attr, None)
    return timestamp.isoformat() if isinstance(timestamp, datetime) else None


def _workspace_tokens_match(left: str, right: str) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    try:
        return os.path.normcase(os.path.abspath(left_text)) == os.path.normcase(os.path.abspath(right_text))
    except (OSError, RuntimeError, ValueError):
        return False


def _is_director_orchestration_snapshot(snapshot: Any, workspace: str) -> bool:
    run_id = str(getattr(snapshot, "run_id", "") or "").strip()
    snapshot_workspace = str(getattr(snapshot, "workspace", "") or "").strip()
    if not run_id.startswith("director-"):
        return False
    return _workspace_tokens_match(snapshot_workspace, workspace)


def _active_orchestration_state(status_token: str) -> tuple[str, bool]:
    token = str(status_token or "").strip().lower()
    if token == "pending":
        return "QUEUED", True
    if token == "running":
        return "RUNNING", True
    if token == "retrying":
        return "BUSY", True
    if token == "blocked":
        return "BLOCKED", False
    return token.upper() if token else "IDLE", False


def _director_status_for_runtime_task_row(row: dict[str, Any]) -> str:
    status_token = str(row.get("status") or "").strip().lower()
    if status_token == "in_progress":
        return "IN_PROGRESS"
    if status_token:
        return status_token.upper()
    return "PENDING"


def _orchestration_task_rows(snapshot: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    tasks = getattr(snapshot, "tasks", None)
    if not isinstance(tasks, dict):
        return rows, by_status

    run_id = str(getattr(snapshot, "run_id", "") or "").strip()
    for fallback_id, task in tasks.items():
        payload = task.to_dict() if hasattr(task, "to_dict") else {}
        if not isinstance(payload, dict):
            payload = {}

        task_id = str(payload.get("task_id") or getattr(task, "task_id", fallback_id) or fallback_id).strip()
        if not task_id:
            continue
        status_token = _enum_value(payload.get("status") or getattr(task, "status", "pending")).upper()
        if not status_token:
            status_token = "PENDING"
        by_status[status_token] = by_status.get(status_token, 0) + 1

        role_id = str(payload.get("role_id") or getattr(task, "role_id", "director") or "director").strip()
        rows.append(
            {
                "id": task_id,
                "task_id": task_id,
                "subject": task_id,
                "description": str(payload.get("error_message") or "").strip(),
                "status": status_token,
                "priority": "MEDIUM",
                "claimed_by": None,
                "role_id": role_id,
                "progress_percent": payload.get("progress_percent", 0.0),
                "current_file": payload.get("current_file"),
                "error_category": payload.get("error_category"),
                "error_message": payload.get("error_message"),
                "metadata": {
                    "orchestration_run_id": run_id,
                    "workflow_task_id": task_id,
                    "role_id": role_id,
                },
            }
        )

    return rows, by_status
