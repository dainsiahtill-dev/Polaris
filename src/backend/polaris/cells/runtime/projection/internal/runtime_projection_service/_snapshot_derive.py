"""Snapshot field derivation helpers for runtime projection service package."""

from __future__ import annotations

from typing import Any

from polaris.cells.workspace.integrity.public.service import read_workspace_status, workspace_has_docs

from ._helpers import _safe_int
from ._models import RuntimeProjection, logger


def _workspace_readiness_projection(workspace: str) -> tuple[bool, dict[str, Any]]:
    """Return docs readiness in the canonical snapshot payload.

    The desktop consumes runtime snapshots from both HTTP and WebSocket. Keeping
    this projection in the runtime snapshot builder prevents stale
    ``NEEDS_DOCS_INIT`` metadata from surviving after docs are created.
    """

    docs_present = workspace_has_docs(workspace)
    try:
        raw_status = read_workspace_status(workspace) or {}
    except (RuntimeError, ValueError, OSError) as exc:
        logger.debug("Failed to read workspace status for runtime snapshot: %s", exc)
        raw_status = {}

    status = dict(raw_status) if isinstance(raw_status, dict) else {}
    status_token = str(status.get("status") or "").strip().upper()

    if docs_present:
        if not status or status_token == "NEEDS_DOCS_INIT":
            return True, {
                "status": "READY",
                "reason": "docs detected",
                "actions": [],
                "source": "runtime_projection",
            }
        return True, status

    if not status:
        status = {
            "status": "NEEDS_DOCS_INIT",
            "reason": "docs/ directory not found",
            "actions": ["INIT_DOCS_WIZARD"],
            "source": "runtime_projection",
        }
    return False, status


def _derive_projection_fields(projection: RuntimeProjection) -> dict[str, Any]:
    """Derive snapshot metadata fields from the current projection."""

    def _director_state(payload: dict[str, Any]) -> str:
        for key in ("execution_state", "state"):
            token = str(payload.get(key) or "").strip()
            if token:
                return token
        status_value = payload.get("status")
        if isinstance(status_value, dict):
            for key in ("execution_state", "state"):
                nested_state = str(status_value.get(key) or "").strip()
                if nested_state:
                    return nested_state
        elif status_value:
            return str(status_value).strip()
        return "running" if bool(payload.get("running")) else "idle"

    def _director_tasks(payload: dict[str, Any]) -> dict[str, Any]:
        direct_tasks = payload.get("tasks")
        if isinstance(direct_tasks, dict):
            return direct_tasks
        status_value = payload.get("status")
        nested_tasks = status_value.get("tasks") if isinstance(status_value, dict) else None
        return nested_tasks if isinstance(nested_tasks, dict) else {}

    def _completed_task_count(task_rows: list[dict[str, Any]], tasks_payload: dict[str, Any]) -> int:
        by_status = tasks_payload.get("by_status")
        if isinstance(by_status, dict):
            completed = max(
                _safe_int(by_status.get("COMPLETED") or by_status.get("completed")),
                _safe_int(by_status.get("COMPLETED_VERIFIED") or by_status.get("completed_verified")),
            )
            if completed > 0:
                return completed
        return len(
            [
                item
                for item in task_rows
                if str(item.get("status") or item.get("state") or "").strip().upper()
                in {"COMPLETED", "COMPLETED_VERIFIED"}
            ]
        )

    derived: dict[str, Any] = {}

    # PM status from pm_local
    pm_payload = projection.pm_local
    if pm_payload:
        derived["pm_status"] = pm_payload.get("status") or ("running" if pm_payload.get("running") else "idle")
        derived["pm_current_task"] = pm_payload.get("current_task_id") or pm_payload.get("task_id")

    # Director status from merged projection first, local fallback second.
    director_payload = projection.director_merged or projection.director_local
    if director_payload:
        derived["director_status"] = _director_state(director_payload)
        tasks_payload = _director_tasks(director_payload)
        by_status_raw = tasks_payload.get("by_status")
        by_status: dict[str, Any] = by_status_raw if isinstance(by_status_raw, dict) else {}
        derived["director_active"] = (
            director_payload.get("active_tasks")
            or tasks_payload.get("active")
            or _safe_int(by_status.get("IN_PROGRESS"))
            + _safe_int(by_status.get("RUNNING"))
            + _safe_int(by_status.get("CLAIMED"))
        )

    # Workflow archive precedence
    if projection.workflow_archive:
        derived["workflow_loaded"] = True
        workflow_task_rows = [item for item in projection.task_rows if isinstance(item, dict)]
        workflow_tasks_payload = _director_tasks(projection.workflow_archive)
        derived["workflow_tasks"] = len(workflow_task_rows) or _safe_int(workflow_tasks_payload.get("total"))
        derived["workflow_completed_tasks"] = _completed_task_count(workflow_task_rows, workflow_tasks_payload)
        # Include run_id from workflow if available
        if "run_id" in projection.workflow_archive:
            derived["run_id"] = projection.workflow_archive["run_id"]
        elif "workflow_id" in projection.workflow_archive:
            derived["run_id"] = projection.workflow_archive["workflow_id"]

    return derived
