"""Pure leaf helpers for the Director v2 router.

This module holds the side-effect-free data-transform helpers extracted from
``polaris.delivery.http.v2.director`` during the lossless module split. None of
these functions reference monkeypatchable runtime collaborators (projection
service, task market, blueprint persistence, artifact paths, emitters, ...); they
operate purely on plain dicts / objects and the declarative Pydantic models.

The canonical router module re-exports every name defined here so that
``director.<name>`` continues to resolve for existing callers and tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.delivery.http.v2.director_models import (
    DirectorDiagnosticsLLMSection,
    DirectorDiagnosticsStatusSection,
    DirectorDiagnosticsTaskSection,
    DirectorDiagnosticsWorkerSection,
    DirectorOrchestrationResponse,
    TaskResponse,
)


def _state_token(payload: dict[str, Any]) -> str:
    state = str(payload.get("state") or "").strip().upper()
    if state:
        return state
    nested_status = payload.get("status")
    if isinstance(nested_status, dict):
        nested_state = str(nested_status.get("state") or "").strip().upper()
        if nested_state:
            return nested_state
    if bool(payload.get("running")):
        return "RUNNING"
    return "IDLE"


def _flatten_director_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    local_payload = payload if isinstance(payload, dict) else {}
    state_token = _state_token(local_payload)
    running = bool(local_payload.get("running")) or state_token == "RUNNING"
    flattened = dict(local_payload)
    flattened["running"] = running
    flattened["state"] = state_token
    flattened.setdefault("status", local_payload.get("status") or {"state": state_token})
    flattened.setdefault("source", str(local_payload.get("source") or "none"))
    return flattened


def _director_tasks_queued(result: Any, requested_task_ids: list[str]) -> int:
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        queued = metadata.get("tasks_queued")
        if isinstance(queued, int) and not isinstance(queued, bool) and queued >= 0:
            return queued
        if isinstance(queued, str) and queued.strip().isdigit():
            return int(queued.strip())
        task_ids = metadata.get("task_ids")
        if isinstance(task_ids, list):
            return len([item for item in task_ids if str(item).strip()])
    return len(requested_task_ids)


def _director_run_task_ids_from_diagnostics(
    diagnostics: Any,
    explicit_task_ids: list[str],
) -> tuple[list[str], str]:
    """Resolve Director run task IDs from explicit request or diagnostics."""

    normalized_explicit = [str(item).strip() for item in explicit_task_ids if str(item).strip()]
    if normalized_explicit:
        return normalized_explicit, "explicit_request"

    task_section = getattr(diagnostics, "tasks", None)
    candidate_sources = (
        ("diagnostics_blueprint_ready", getattr(task_section, "blueprint_ready_task_ids", None)),
        ("diagnostics_ready", getattr(task_section, "ready_task_ids", None)),
    )
    seen: set[str] = set()
    selected: list[str] = []
    selected_sources: list[str] = []
    for source, values in candidate_sources:
        if not isinstance(values, list):
            continue
        added_from_source = False
        for item in values:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            selected.append(token)
            added_from_source = True
        if added_from_source:
            selected_sources.append(source)
    if selected:
        if len(selected_sources) == 1:
            return selected, selected_sources[0]
        return selected, "diagnostics_mixed_ready"
    return [], "none"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text_or_none(value)
        if text:
            return text
    return None


def _projection_source_for_task_rows(projection: Any) -> str:
    """Return the best available provenance label for task projection rows."""

    workflow_archive = getattr(projection, "workflow_archive", None)
    if isinstance(workflow_archive, dict) and workflow_archive:
        return "workflow_archive"

    director_merged = getattr(projection, "director_merged", None)
    if isinstance(director_merged, dict) and director_merged:
        return "director_merged"

    director_local = getattr(projection, "director_local", None)
    if isinstance(director_local, dict) and director_local:
        return "director_local"

    return "runtime_projection"


def _with_task_projection_source(row: dict[str, Any], *, fallback_source: str) -> dict[str, Any]:
    normalized = dict(row)
    metadata = dict(_as_dict(normalized.get("metadata")))
    projection_source = _first_text(
        metadata.get("projection_source"),
        normalized.get("projection_source"),
        metadata.get("materialized_by"),
        normalized.get("materialized_by"),
        metadata.get("task_market_source"),
        normalized.get("task_market_source"),
        metadata.get("director_task_source"),
        normalized.get("director_task_source"),
        metadata.get("source"),
        normalized.get("source"),
        fallback_source,
    )
    if projection_source and not _text_or_none(metadata.get("projection_source")):
        metadata["projection_source"] = projection_source
    normalized["metadata"] = metadata
    return normalized


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple, set)):
        scalar_text = _text_or_none(value)
        return [scalar_text] if scalar_text else []

    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            item_text = _first_text(
                item.get("description"),
                item.get("text"),
                item.get("title"),
                item.get("name"),
                item.get("path"),
                item.get("file"),
                item.get("id"),
                item.get("value"),
            )
        else:
            item_text = _text_or_none(item)
        if item_text and item_text not in seen:
            seen.add(item_text)
            items.append(item_text)
    return items


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        items = _string_list(value)
        if items:
            return items
    return []


def _normalize_task_status_token(value: Any) -> str:
    token = str(value or "").strip().upper().replace("-", "_")
    aliases = {
        "": "PENDING",
        "TODO": "PENDING",
        "TO_DO": "PENDING",
        "QUEUED": "PENDING",
        "READY": "PENDING",
        "PENDING": "PENDING",
        "PENDING_EXEC": "PENDING",
        "PENDING_DESIGN": "PENDING",
        "PENDING_QA": "PENDING",
        "CLAIMED": "CLAIMED",
        "IN_PROGRESS": "RUNNING",
        "IN_EXECUTION": "RUNNING",
        "IN_DESIGN": "RUNNING",
        "IN_QA": "RUNNING",
        "RUNNING": "RUNNING",
        "EXECUTING": "RUNNING",
        "ACTIVE": "RUNNING",
        "BLOCKED": "BLOCKED",
        "FAILED": "FAILED",
        "ERROR": "FAILED",
        "TIMEOUT": "FAILED",
        "TIMED_OUT": "FAILED",
        "COMPLETED": "COMPLETED",
        "DONE": "COMPLETED",
        "SUCCESS": "COMPLETED",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
    }
    return aliases.get(token, token or "PENDING")


def _task_row_from_object(task: Any) -> dict[str, Any]:
    if isinstance(task, dict):
        return _with_task_projection_source(task, fallback_source="director_local")

    result = getattr(task, "result", None)
    result_payload = result.to_dict() if result and hasattr(result, "to_dict") else result
    metadata = getattr(task, "metadata", None)
    status_value = getattr(getattr(task, "status", None), "name", None) or getattr(task, "status", None)
    priority_value = getattr(getattr(task, "priority", None), "name", None) or getattr(task, "priority", None)
    return _with_task_projection_source(
        {
            "id": str(getattr(task, "id", "")),
            "subject": getattr(task, "subject", ""),
            "description": getattr(task, "description", ""),
            "status": status_value,
            "priority": priority_value,
            "claimed_by": getattr(task, "claimed_by", None),
            "result": result_payload if isinstance(result_payload, dict) else None,
            "metadata": metadata if isinstance(metadata, dict) else {},
        },
        fallback_source="director_local",
    )


def _task_rows_from_local_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    return [_task_row_from_object(task) for task in tasks]


def _task_id_from_row(row: dict[str, Any]) -> str:
    metadata = _as_dict(row.get("metadata"))
    return str(row.get("id") or row.get("task_id") or row.get("pm_task_id") or metadata.get("pm_task_id") or "").strip()


def _task_details(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _as_dict(row.get("metadata"))
    runtime_execution = _as_dict(metadata.get("runtime_execution"))
    result = _as_dict(row.get("result"))
    status_token = _normalize_task_status_token(
        row.get("status") or runtime_execution.get("effective_status") or runtime_execution.get("status")
    )
    worker = _first_text(
        row.get("worker"),
        row.get("claimed_by"),
        row.get("assignee"),
        metadata.get("worker"),
        metadata.get("worker_id"),
        metadata.get("assigned_worker"),
        metadata.get("claimed_by"),
        metadata.get("last_claimed_by"),
        runtime_execution.get("worker_id"),
        runtime_execution.get("claimed_by"),
    )
    error = _first_text(
        row.get("error"),
        row.get("error_message"),
        row.get("last_error"),
        metadata.get("error"),
        metadata.get("last_error"),
        metadata.get("last_execution_error"),
        runtime_execution.get("last_error"),
        result.get("error"),
        result.get("stderr"),
    )
    if status_token == "FAILED":
        error = error or _first_text(result.get("summary"), row.get("result_summary"))

    return {
        "status": status_token,
        "goal": _first_text(row.get("goal"), metadata.get("goal"), metadata.get("task_goal"), row.get("description"))
        or "",
        "acceptance": _first_string_list(
            row.get("acceptance"),
            row.get("acceptance_criteria"),
            metadata.get("acceptance"),
            metadata.get("acceptance_criteria"),
            _as_dict(metadata.get("qa_contract")).get("acceptance_criteria"),
        ),
        "target_files": _first_string_list(
            row.get("target_files"),
            metadata.get("target_files"),
            metadata.get("scope_paths"),
        ),
        "dependencies": _first_string_list(
            row.get("dependencies"),
            row.get("depends_on"),
            row.get("blocked_by"),
            row.get("blockedBy"),
            metadata.get("dependencies"),
            metadata.get("depends_on"),
            metadata.get("blocked_by"),
        ),
        "current_file": _first_text(
            row.get("current_file"),
            metadata.get("current_file"),
            metadata.get("current_file_path"),
            runtime_execution.get("current_file"),
        ),
        "error": error,
        "worker": worker,
        "pm_task_id": _first_text(
            row.get("pm_task_id"),
            metadata.get("pm_task_id"),
            metadata.get("external_task_id"),
            metadata.get("source_task_id"),
        ),
        "blueprint_id": _first_text(
            row.get("blueprint_id"),
            row.get("blueprintId"),
            metadata.get("blueprint_id"),
            metadata.get("blueprintId"),
        ),
        "blueprint_path": _first_text(
            row.get("blueprint_path"),
            row.get("runtime_blueprint_path"),
            row.get("blueprintPath"),
            metadata.get("blueprint_path"),
            metadata.get("runtime_blueprint_path"),
            metadata.get("blueprintPath"),
        ),
        "runtime_blueprint_path": _first_text(
            row.get("runtime_blueprint_path"),
            metadata.get("runtime_blueprint_path"),
            row.get("blueprint_path"),
            metadata.get("blueprint_path"),
        ),
    }


def _row_requires_blueprint_evidence(row: dict[str, Any], *, source: str) -> bool:
    metadata = _as_dict(row.get("metadata"))
    payload = _as_dict(row.get("payload"))
    for container in (row, metadata, payload):
        route = (
            str(
                container.get("task_market_route")
                or container.get("route")
                or container.get("routing")
                or container.get("dispatch_route")
                or ""
            )
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if route in {"direct_to_director", "direct", "director", "director_direct", "pending_exec"}:
            return False
        if route in {
            "chief_blueprint_required",
            "chief",
            "chief_engineer",
            "blueprint",
            "blueprint_required",
            "pending_design",
        }:
            return True
        raw_required = container.get("blueprint_required")
        if isinstance(raw_required, bool):
            return raw_required
        if isinstance(raw_required, str):
            token = raw_required.strip().lower()
            if token in {"1", "true", "yes", "y", "on"}:
                return True
            if token in {"0", "false", "no", "n", "off"}:
                return False
    return source == "workflow"


def _task_response_from_row(row: dict[str, Any]) -> TaskResponse:
    details = _task_details(row)
    result = row.get("result")
    return TaskResponse(
        id=str(row.get("id") or row.get("task_id") or ""),
        subject=str(row.get("subject") or row.get("title") or row.get("id") or "").strip(),
        description=str(row.get("description") or "").strip(),
        status=details["status"],
        priority=str(row.get("priority") or "MEDIUM").strip() or "MEDIUM",
        claimed_by=details["worker"],
        result=result if isinstance(result, dict) else None,
        metadata=_as_dict(row.get("metadata")),
        goal=details["goal"],
        acceptance=details["acceptance"],
        target_files=details["target_files"],
        dependencies=details["dependencies"],
        current_file=details["current_file"],
        error=details["error"],
        worker=details["worker"],
        pm_task_id=details["pm_task_id"],
        blueprint_id=details["blueprint_id"],
        blueprint_path=details["blueprint_path"],
        runtime_blueprint_path=details["runtime_blueprint_path"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _director_snapshot_status(snapshot: Any) -> str:
    status_value = getattr(snapshot, "status", None)
    value = getattr(status_value, "value", status_value)
    return str(value or "unknown")


def _director_snapshot_task_count(snapshot: Any) -> int:
    tasks = getattr(snapshot, "tasks", None)
    if isinstance(tasks, (dict, list, tuple, set)):
        return len(tasks)
    return 0


def _director_orchestration_response(snapshot: Any) -> DirectorOrchestrationResponse:
    status_value = _director_snapshot_status(snapshot)
    return DirectorOrchestrationResponse(
        run_id=str(snapshot.run_id),
        status=status_value,
        workspace=str(snapshot.workspace),
        tasks_queued=_director_snapshot_task_count(snapshot),
        message=f"Status: {status_value}",
    )


def _path_is_within(target: Path, root: str | Path | None) -> bool:
    root_text = str(root or "").strip()
    if not root_text:
        return False
    try:
        resolved_root = Path(root_text).resolve()
        resolved_target = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_target == resolved_root or resolved_root in resolved_target.parents


def _blueprint_reference_values(details: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(details.get("blueprint_id") or "").strip(),
        str(details.get("blueprint_path") or "").strip(),
        str(details.get("runtime_blueprint_path") or "").strip(),
    )


def _task_identity_tokens(task_id: str, details: dict[str, Any]) -> set[str]:
    tokens = {
        str(task_id or "").strip(),
        str(details.get("pm_task_id") or "").strip(),
    }
    return {token for token in tokens if token}


def _payload_task_identity_values(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("task_id", "pm_task_id", "id", "source_task_id", "external_task_id"):
        token = str(payload.get(key) or "").strip()
        if token:
            values.add(token)

    context = _as_dict(payload.get("context"))
    for key in ("task_id", "pm_task_id", "id", "source_task_id", "external_task_id"):
        token = str(context.get(key) or "").strip()
        if token:
            values.add(token)

    for nested_key in ("base_schema", "preflight_result"):
        nested_payload = payload.get(nested_key)
        if isinstance(nested_payload, dict):
            values.update(_payload_task_identity_values(nested_payload))

    task_update_map = payload.get("task_update_map")
    if isinstance(task_update_map, dict):
        for key, item in task_update_map.items():
            token = str(key or "").strip()
            if token:
                values.add(token)
            if isinstance(item, dict):
                values.update(_payload_task_identity_values(item))

    task_updates = payload.get("task_updates")
    if isinstance(task_updates, list):
        for item in task_updates:
            if isinstance(item, dict):
                values.update(_payload_task_identity_values(item))

    return values


def _blueprint_payload_matches_task(payload: dict[str, Any], identities: set[str]) -> bool:
    if not identities:
        return False
    if _blueprint_payload_is_traceability_only(payload):
        return False
    if bool(payload.get("hard_failure")):
        return False
    status_token = str(payload.get("status") or "").strip().lower()
    if status_token in {"failed", "failure", "error", "rejected", "blocked"}:
        return False
    if not _blueprint_payload_is_handoff_ready(payload):
        return False
    return bool(_payload_task_identity_values(payload) & identities)


def _blueprint_contract_list(payload: dict[str, Any], *keys: str) -> list[str]:
    base_schema = _as_dict(payload.get("base_schema"))
    context = _as_dict(payload.get("context")) or _as_dict(base_schema.get("context"))
    pm_task = _as_dict(payload.get("pm_task")) or _as_dict(base_schema.get("pm_task")) or _as_dict(context.get("task"))
    qa_contract = _as_dict(pm_task.get("qa_contract"))
    for key in keys:
        for source in (payload, base_schema, context, pm_task, qa_contract):
            rows = _string_list(source.get(key))
            if rows:
                return rows
    return []


def _blueprint_handoff_missing_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _blueprint_contract_list(payload, "target_files", "scope_paths", "files", "affected_files"):
        missing.append("target_files")
    if not _blueprint_contract_list(payload, "acceptance_criteria", "acceptance"):
        missing.append("acceptance_criteria")
    if not _blueprint_contract_list(payload, "execution_checklist", "steps"):
        missing.append("execution_checklist")
    return missing


def _blueprint_payload_is_handoff_ready(payload: dict[str, Any]) -> bool:
    if _blueprint_payload_is_traceability_only(payload):
        return False
    completeness = _as_dict(payload.get("contract_completeness"))
    missing_fields = _string_list(completeness.get("missing_fields"))
    if completeness.get("handoff_ready") is False or missing_fields:
        return False
    return not _blueprint_handoff_missing_fields(payload)


def _blueprint_payload_is_traceability_only(payload: dict[str, Any]) -> bool:
    if payload.get("traceability_only") is True:
        return True
    source = str(payload.get("source") or "").strip().lower()
    return source.startswith("pm_dispatch.traceability")


def _task_row_matches_id(row: dict[str, Any], task_id: str) -> bool:
    requested = str(task_id or "").strip()
    if not requested:
        return False

    metadata = _as_dict(row.get("metadata"))
    candidates = {
        row.get("id"),
        row.get("task_id"),
        row.get("pm_task_id"),
        metadata.get("pm_task_id"),
        metadata.get("external_task_id"),
        metadata.get("source_task_id"),
    }
    return requested in {str(candidate).strip() for candidate in candidates if candidate is not None}


def _cancel_success_payload(task_id: str, result: Any) -> dict[str, Any] | None:
    if isinstance(result, bool):
        return {"ok": True, "task_id": task_id} if result else None

    if not isinstance(result, dict):
        return None

    status_token = _normalize_task_status_token(result.get("status"))
    accepted = result.get("ok") is True or result.get("cancelled") is True or status_token == "CANCELLED"
    if not accepted:
        return None

    payload = dict(result)
    payload["ok"] = True
    payload.setdefault("task_id", task_id)
    return payload


def _cancel_failure_detail(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("error", "detail", "message"):
            text = _text_or_none(result.get(key))
            if text:
                return text
    return "Task cannot be cancelled"


def _is_workflow_shell_task(row: dict[str, Any]) -> bool:
    metadata = _as_dict(row.get("metadata"))
    task_id = str(row.get("id") or row.get("task_id") or "").strip()
    workflow_task_id = str(metadata.get("workflow_task_id") or "").strip()
    has_pm_identity = any(
        str(value or "").strip()
        for value in (
            row.get("pm_task_id"),
            metadata.get("pm_task_id"),
            metadata.get("source_task_id"),
            metadata.get("external_task_id"),
        )
    )
    if has_pm_identity:
        return False
    shell_ids = {task_id, workflow_task_id}
    return any(token.startswith("task-") and token.endswith("-director") for token in shell_ids if token)


def _merge_task_rows_by_identity(
    primary_rows: list[dict[str, Any]],
    overlay_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not primary_rows:
        return list(overlay_rows)
    if not overlay_rows:
        return list(primary_rows)

    merged_rows: list[dict[str, Any]] = [dict(row) for row in primary_rows]
    index_by_token: dict[str, int] = {}
    for index, row in enumerate(merged_rows):
        details = _task_details(row)
        for token in _task_identity_tokens(_task_id_from_row(row), details):
            index_by_token.setdefault(token, index)

    for overlay in overlay_rows:
        overlay_id = _task_id_from_row(overlay)
        overlay_details = _task_details(overlay)
        target_index = None
        for token in _task_identity_tokens(overlay_id, overlay_details):
            if token in index_by_token:
                target_index = index_by_token[token]
                break
        if target_index is None:
            index_by_token[overlay_id] = len(merged_rows)
            merged_rows.append(dict(overlay))
            continue

        current = merged_rows[target_index]
        merged = dict(current)
        merged.update(overlay)
        current_metadata = _as_dict(current.get("metadata"))
        overlay_metadata = _as_dict(overlay.get("metadata"))
        metadata = dict(current_metadata)
        metadata.update(overlay_metadata)
        merged["metadata"] = metadata
        merged_rows[target_index] = merged
    return merged_rows


def _worker_payload_from_object(worker: Any) -> dict[str, Any]:
    if isinstance(worker, dict):
        return worker
    to_dict = getattr(worker, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    return {
        "id": getattr(worker, "id", ""),
        "name": getattr(worker, "name", ""),
        "status": getattr(worker, "status", ""),
        "current_task_id": getattr(worker, "current_task_id", None) or getattr(worker, "task_id", None),
        "healthy": getattr(worker, "healthy", None),
    }


def _worker_id_from_row(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("worker_id") or row.get("name") or "").strip()


def _worker_row_matches_id(row: dict[str, Any], worker_id: str) -> bool:
    requested = str(worker_id or "").strip()
    if not requested:
        return False
    return requested in {
        str(row.get("id") or "").strip(),
        str(row.get("worker_id") or "").strip(),
        str(row.get("name") or "").strip(),
    }


def _worker_rows_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    worker_payload: Any = payload.get("workers")
    if worker_payload is None:
        status_payload = payload.get("status")
        if isinstance(status_payload, dict):
            worker_payload = status_payload.get("workers")

    candidates: Any = None
    if isinstance(worker_payload, list):
        candidates = worker_payload
    elif isinstance(worker_payload, dict):
        for key in ("worker_rows", "rows", "items", "workers"):
            value = worker_payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if candidates is None:
            dict_rows: list[dict[str, Any]] = []
            for key, value in worker_payload.items():
                if not isinstance(value, dict):
                    continue
                row = dict(value)
                row.setdefault("id", str(key))
                dict_rows.append(row)
            candidates = dict_rows
    elif isinstance(payload.get("worker_rows"), list):
        candidates = payload.get("worker_rows")

    rows: list[dict[str, Any]] = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if not _worker_id_from_row(row):
            continue
        rows.append(row)
    return rows


def _worker_rows_from_projection(projection: Any) -> list[dict[str, Any]]:
    merged_payload = getattr(projection, "director_merged", None)
    rows = _worker_rows_from_payload(merged_payload if isinstance(merged_payload, dict) else None)
    if rows:
        return rows
    local_payload = getattr(projection, "director_local", None)
    return _worker_rows_from_payload(local_payload if isinstance(local_payload, dict) else None)


def _worker_diagnostics_from_workers(workers: list[Any]) -> DirectorDiagnosticsWorkerSection:
    idle = 0
    busy = 0
    unhealthy = 0
    active_task_ids: list[str] = []
    idle_statuses = {"idle", "ready", "available", "waiting"}
    busy_statuses = {"busy", "running", "active", "claimed", "working", "executing"}
    unhealthy_statuses = {"failed", "error", "unhealthy", "offline"}

    for worker in workers:
        payload = _worker_payload_from_object(worker)
        status_token = str(payload.get("status") or "").strip().lower()
        if status_token in idle_statuses:
            idle += 1
        elif status_token in busy_statuses:
            busy += 1

        if payload.get("healthy") is False or status_token in unhealthy_statuses:
            unhealthy += 1

        task_id = str(
            payload.get("current_task_id") or payload.get("currentTaskId") or payload.get("task_id") or ""
        ).strip()
        if task_id and task_id not in active_task_ids:
            active_task_ids.append(task_id)

    total = len(workers)
    return DirectorDiagnosticsWorkerSection(
        ok=total > 0 and unhealthy == 0,
        total=total,
        idle=idle,
        busy=busy,
        healthy=max(0, total - unhealthy),
        unhealthy=unhealthy,
        active_task_ids=active_task_ids,
    )


def _role_payload(payload: dict[str, Any], role: str) -> dict[str, Any]:
    roles_value = payload.get("roles")
    roles = roles_value if isinstance(roles_value, dict) else {}
    target = str(role or "").strip().lower()
    for key, value in roles.items():
        if str(key or "").strip().lower() == target and isinstance(value, dict):
            return value
    return {}


def _director_diagnostic_issues(
    status_section: DirectorDiagnosticsStatusSection,
    task_section: DirectorDiagnosticsTaskSection,
    worker_section: DirectorDiagnosticsWorkerSection,
    llm_section: DirectorDiagnosticsLLMSection,
) -> list[str]:
    issues: list[str] = []
    if not llm_section.ok:
        issues.append("director_llm_not_ready")
    if not status_section.ok:
        issues.append("director_status_unavailable")
    if task_section.error:
        issues.append("director_tasks_unavailable")
    if task_section.total == 0:
        issues.append("director_no_tasks")
    else:
        if task_section.missing_blueprint_task_ids:
            issues.append("director_ready_tasks_missing_blueprints")
        if task_section.invalid_blueprint_task_ids:
            issues.append("director_ready_tasks_invalid_blueprints")
    if task_section.blocked > 0:
        issues.append("director_tasks_blocked")
    if task_section.failed > 0:
        issues.append("director_tasks_failed")
    if (
        task_section.total > 0
        and not task_section.missing_blueprint_task_ids
        and not task_section.invalid_blueprint_task_ids
        and task_section.blocked == 0
        and task_section.failed == 0
        and task_section.ready_to_execute == 0
        and task_section.running == 0
        and not status_section.running
    ):
        issues.append("director_no_ready_tasks")
    if worker_section.error:
        issues.append("director_workers_unavailable")
    if worker_section.total == 0:
        issues.append("director_no_workers")
    elif task_section.ready_to_execute > 0 and worker_section.idle == 0 and not status_section.running:
        issues.append("director_no_idle_workers")
    if worker_section.unhealthy > 0:
        issues.append("director_workers_unhealthy")
    return issues


def _director_execution_blockers(
    status_section: DirectorDiagnosticsStatusSection,
    task_section: DirectorDiagnosticsTaskSection,
    worker_section: DirectorDiagnosticsWorkerSection,
    llm_section: DirectorDiagnosticsLLMSection,
) -> list[str]:
    """Return hard blockers that should disable a new Director run."""

    blockers: list[str] = []
    if not llm_section.ok:
        blockers.append("director_llm_not_ready")

    if status_section.running:
        return blockers

    if not status_section.ok:
        blockers.append("director_status_unavailable")
    if task_section.error:
        blockers.append("director_tasks_unavailable")
    if task_section.total == 0:
        blockers.append("director_no_tasks")
    else:
        if task_section.missing_blueprint_task_ids:
            blockers.append("director_ready_tasks_missing_blueprints")
        if task_section.invalid_blueprint_task_ids:
            blockers.append("director_ready_tasks_invalid_blueprints")
    has_runnable_or_running_work = (
        task_section.ready_to_execute > 0 or task_section.running > 0 or status_section.running
    )
    if not has_runnable_or_running_work and task_section.blocked > 0:
        blockers.append("director_tasks_blocked")
    if not has_runnable_or_running_work and task_section.failed > 0:
        blockers.append("director_tasks_failed")
    if (
        task_section.total > 0
        and not task_section.missing_blueprint_task_ids
        and not task_section.invalid_blueprint_task_ids
        and task_section.blocked == 0
        and task_section.failed == 0
        and task_section.ready_to_execute == 0
    ):
        blockers.append("director_no_ready_tasks")

    if worker_section.error:
        blockers.append("director_workers_unavailable")
    elif worker_section.total > 0 and task_section.ready_to_execute > 0 and worker_section.idle == 0:
        blockers.append("director_no_idle_workers")
    return blockers


__all__ = [
    "_as_dict",
    "_blueprint_contract_list",
    "_blueprint_handoff_missing_fields",
    "_blueprint_payload_is_handoff_ready",
    "_blueprint_payload_is_traceability_only",
    "_blueprint_payload_matches_task",
    "_blueprint_reference_values",
    "_cancel_failure_detail",
    "_cancel_success_payload",
    "_director_diagnostic_issues",
    "_director_execution_blockers",
    "_director_orchestration_response",
    "_director_run_task_ids_from_diagnostics",
    "_director_snapshot_status",
    "_director_snapshot_task_count",
    "_director_tasks_queued",
    "_first_string_list",
    "_first_text",
    "_flatten_director_status",
    "_is_workflow_shell_task",
    "_merge_task_rows_by_identity",
    "_normalize_task_status_token",
    "_path_is_within",
    "_payload_task_identity_values",
    "_projection_source_for_task_rows",
    "_role_payload",
    "_row_requires_blueprint_evidence",
    "_state_token",
    "_string_list",
    "_task_details",
    "_task_id_from_row",
    "_task_identity_tokens",
    "_task_response_from_row",
    "_task_row_from_object",
    "_task_row_matches_id",
    "_task_rows_from_local_tasks",
    "_text_or_none",
    "_utc_now",
    "_with_task_projection_source",
    "_worker_diagnostics_from_workers",
    "_worker_id_from_row",
    "_worker_payload_from_object",
    "_worker_row_matches_id",
    "_worker_rows_from_payload",
    "_worker_rows_from_projection",
]
