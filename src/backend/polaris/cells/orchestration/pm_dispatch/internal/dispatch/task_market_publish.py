"""Task-market route classification and revision/change-order helpers.

Pure logic extracted verbatim from ``dispatch_pipeline.py``: rollout-mode
resolution, route classification, revision-context digests and the
lineage-status snapshot. Cross-Cell imports remain lazy (in-function)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TASK_MARKET_TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "rejected", "dead_letter"})
_TASK_ROUTE_DIRECT_TO_DIRECTOR = "direct_to_director"
_TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED = "chief_blueprint_required"


def _resolve_task_market_mode() -> str:
    """Resolve task-market mode to a stable internal value."""
    rollout_mode = _resolve_task_market_rollout_mode()
    if rollout_mode in {"mainline", "mainline-design", "mainline-full", "mainline-durable"}:
        return "mainline"
    return "mainline"


def _resolve_task_market_rollout_mode() -> str:
    """Resolve task-market rollout phase from environment."""
    raw_mode = str(os.environ.get("KERNELONE_TASK_MARKET_MODE", "mainline-full") or "mainline-full").strip().lower()
    if raw_mode in {"mainline", "mainline-design", "mainline-full", "mainline-durable"}:
        return raw_mode
    if raw_mode == "mainline-exec":
        # Preserve forward compatibility with docs that mention this phase.
        return "mainline-full"
    if raw_mode in {"off", "shadow"}:
        logger.warning(
            "KERNELONE_TASK_MARKET_MODE=%s is retired; forcing governed PM -> Chief Engineer -> Director flow",
            raw_mode,
        )
    else:
        logger.warning(
            "unknown KERNELONE_TASK_MARKET_MODE=%s; forcing governed PM -> Chief Engineer -> Director flow",
            raw_mode or "<empty>",
        )
    return "mainline-full"


def _hash_payload(payload: Any) -> str:
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_revision_context(
    *,
    workspace_full: str,
    run_id: str,
    tasks: list[dict[str, Any]],
    normalized: dict[str, Any] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> dict[str, str]:
    normalized_payload = normalized if isinstance(normalized, dict) else {}
    docs_payload = docs_stage if isinstance(docs_stage, dict) else {}
    default_plan_id = (
        str(normalized_payload.get("project_id") or normalized_payload.get("initiative_id") or "").strip()
        or str(docs_payload.get("active_doc_path") or "").strip()
        or f"workspace::{workspace_full}"
    )
    plan_id = str(normalized_payload.get("plan_id") or default_plan_id).strip()
    task_projection: list[dict[str, Any]] = [
        {
            "id": str(task.get("id") or "").strip(),
            "title": str(task.get("title") or "").strip(),
            "goal": str(task.get("goal") or "").strip(),
            "depends_on": task.get("depends_on") if isinstance(task.get("depends_on"), list) else [],
            "scope_paths": task.get("scope_paths") if isinstance(task.get("scope_paths"), list) else [],
            "target_files": task.get("target_files") if isinstance(task.get("target_files"), list) else [],
        }
        for task in tasks
        if isinstance(task, dict)
    ]
    requirement_basis = {
        "overall_goal": str(normalized_payload.get("overall_goal") or "").strip(),
        "focus": str(normalized_payload.get("focus") or "").strip(),
        "notes": str(normalized_payload.get("notes") or "").strip(),
        "tasks": task_projection,
        "docs_active_path": str(docs_payload.get("active_doc_path") or "").strip(),
    }
    requirement_digest = _hash_payload(requirement_basis)
    constraint_basis = {
        "docs_enabled": bool(docs_payload.get("enabled")),
        "dispatch_task_count": len(task_projection),
        "run_id": str(run_id or "").strip(),
    }
    constraint_digest = _hash_payload(constraint_basis)
    plan_revision_id = str(normalized_payload.get("plan_revision_id") or "").strip() or f"rev-{requirement_digest[:12]}"
    return {
        "plan_id": plan_id,
        "plan_revision_id": plan_revision_id,
        "requirement_digest": requirement_digest,
        "constraint_digest": constraint_digest,
    }


def _extract_task_dependencies(
    task: dict[str, Any],
    *,
    known_task_ids: set[str] | None = None,
) -> tuple[str, ...]:
    raw_depends_on = task.get("depends_on")
    if isinstance(raw_depends_on, list):
        source = raw_depends_on
    else:
        raw_dependencies = task.get("dependencies")
        source = raw_dependencies if isinstance(raw_dependencies, list) else []
    normalized = [str(item).strip() for item in source if str(item).strip()]
    deduped: list[str] = []
    for item in normalized:
        if item not in deduped:
            deduped.append(item)
    if known_task_ids is None:
        return tuple(deduped)
    # Planner dependency lists are unvalidated free text: a ref to a task id
    # that is not in this plan can never resolve on the market, and the exec
    # readiness gate would strand the task as permanently unclaimable.
    dropped = [item for item in deduped if item not in known_task_ids]
    if dropped:
        logger.warning(
            "task %s: dropping depends_on refs to unknown plan tasks %s",
            str(task.get("id") or "").strip(),
            dropped,
        )
    return tuple(item for item in deduped if item in known_task_ids)


def _task_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "y", "on"}:
            return True
        if token in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _normalize_task_market_route(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {
        _TASK_ROUTE_DIRECT_TO_DIRECTOR,
        "direct",
        "director",
        "director_direct",
        "direct_director",
        "pending_exec",
        "exec",
        "execution",
    }:
        return _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED
    if token in {
        _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED,
        "chief",
        "chief_engineer",
        "chiefengineer",
        "blueprint",
        "blueprint_required",
        "requires_blueprint",
        "pending_design",
        "design",
    }:
        return _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED
    return ""


def _task_market_route_for_task(task: dict[str, Any]) -> str:
    metadata_raw = task.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    for container in (task, metadata):
        for key in (
            "task_market_route",
            "route",
            "routing",
            "dispatch_route",
            "execution_route",
            "handoff_route",
        ):
            route = _normalize_task_market_route(container.get(key))
            if route:
                return route
        for key in (
            "requires_blueprint",
            "blueprint_required",
            "chief_engineer_required",
            "requires_chief_engineer",
            "requires_design",
        ):
            explicit = _task_bool(container.get(key))
            if explicit is True:
                return _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED
            if explicit is False:
                return _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED

    role_values = (
        task.get("assigned_to"),
        task.get("assignee"),
        task.get("owner_role"),
        task.get("role"),
        metadata.get("assigned_to"),
        metadata.get("owner_role"),
        metadata.get("role"),
    )
    role_tokens = {str(value or "").strip().lower().replace(" ", "_") for value in role_values if str(value or "")}
    if role_tokens & {"chief", "chief_engineer", "chiefengineer"}:
        return _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED

    # Mainline always starts with the governed design route. Historical
    # direct-to-Director aliases are normalized to this route above.
    return _TASK_ROUTE_CHIEF_BLUEPRINT_REQUIRED


def _task_market_stage_for_route(route: str) -> str:
    return "pending_design"


def _task_market_lineage_snapshot(
    *,
    task_market_service: Any,
    workspace_full: str,
    published_id_set: set[str],
) -> dict[str, Any]:
    """Return task-market state scoped to this dispatch's published lineage."""
    if not published_id_set or not hasattr(task_market_service, "query_status"):
        return {"available": False, "reason": "query_status_unavailable"}

    from polaris.cells.runtime.task_market.public.contracts import (
        QueryTaskMarketStatusV1,
    )

    status_result = task_market_service.query_status(QueryTaskMarketStatusV1(workspace=workspace_full, limit=10_000))
    terminal_status_by_task: dict[str, str] = {}
    open_task_ids: list[str] = []
    scoped_task_ids: list[str] = []
    status_counts: dict[str, int] = {}
    for row in status_result.items:
        row_task_id = str(row.get("task_id") or "").strip()
        row_status = str(row.get("status") or "").strip().lower()
        row_lineage = {
            row_task_id,
            str(row.get("root_task_id") or "").strip(),
            str(row.get("parent_task_id") or "").strip(),
        }
        if not (row_lineage & published_id_set) or not row_task_id:
            continue
        scoped_task_ids.append(row_task_id)
        status_counts[row_status] = status_counts.get(row_status, 0) + 1
        if row_status in _TASK_MARKET_TERMINAL_STATUSES:
            terminal_status_by_task[row_task_id] = row_status
        else:
            open_task_ids.append(row_task_id)

    return {
        "available": True,
        "scoped_task_ids": tuple(scoped_task_ids),
        "open_task_ids": tuple(open_task_ids),
        "terminal_status_by_task": terminal_status_by_task,
        "status_counts": status_counts,
    }
