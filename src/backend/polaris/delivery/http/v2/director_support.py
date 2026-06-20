"""Request-bound Director support helpers (workspace / snapshot / projection IO).

Extracted from ``polaris.delivery.http.v2.director`` during the lossless module
split. These helpers resolve the active workspace for a request, hide
cross-workspace orchestration runs, project task / worker detail rows on local
queue misses, assemble the workflow snapshot, and append optional debug evidence.

Monkeypatch contract (CRITICAL): tests patch collaborators on the ``director``
module namespace (``director.RuntimeProjectionService``, ``director.Path``,
``director.logger`` ...) and import some of these helpers (``_append_debug``)
directly from ``director``. Every patchable collaborator referenced from a helper
that lives here is dereferenced through the ``director`` module object at call
time (``from . import director as _d; _d.X(...)``) so the patch is honored. The
lazy ``director`` import also breaks the import cycle.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from fastapi import HTTPException, Request, status
from polaris.delivery.http.v2.director_models import TaskResponse


def _append_debug(event: str, payload: dict[str, Any]) -> None:
    from polaris.delivery.http.v2 import director as _d

    log_target = str(os.environ.get("KERNELONE_BACKEND_DEBUG_LOG", "") or "").strip()
    if not log_target:
        return

    try:
        log_path = _d.Path(log_target)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "event": event,
            "payload": payload,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        # Event logging failure should not break main flow, but visibility is compromised.
        _d.logger.debug("Director debug event append failed: %s", exc, exc_info=True)


def _workspace_from_request(request: Request, workspace: str | None = None) -> str:
    from polaris.delivery.http.v2 import director as _d

    state = getattr(request.app.state, "app_state", None) or request.app.state
    return _d.requested_or_active_workspace(state.settings, workspace or "")


def _ensure_snapshot_workspace(request: Request, snapshot: Any, run_id: str, workspace: str) -> None:
    """Hide orchestration runs that do not belong to the requested desktop workspace."""

    from polaris.delivery.http.v2 import director as _d

    if not str(workspace or "").strip():
        return
    resolved_workspace = _d._workspace_from_request(request, workspace)
    if not _d.workspace_values_match(getattr(snapshot, "workspace", ""), resolved_workspace):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run not found: {run_id}",
        )


async def _projected_task_response(request: Request, task_id: str, workspace: str | None = None) -> TaskResponse | None:
    """Return a workflow/projection task detail when local Director queue misses."""
    from polaris.delivery.http.v2 import director as _d

    state = getattr(request.app.state, "app_state", None) or request.app.state
    resolved_workspace = _d._workspace_from_request(request, workspace)

    projection_rows: list[dict[str, Any]] = []
    try:
        projection = await _d.RuntimeProjectionService.build_async(resolved_workspace, state=state)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        _d.logger.debug("Failed to build Director task projection for task_id=%s", task_id, exc_info=True)
    else:
        projection_rows = _d._projection_task_rows(projection)

    for row in projection_rows:
        if _d._task_row_matches_id(row, task_id):
            return _d._task_response_from_row(row)
    for row in _d._task_market_execution_rows_for_workspace(resolved_workspace):
        if _d._task_row_matches_id(row, task_id):
            return _d._task_response_from_row(row)
    return None


async def _projected_worker_rows(request: Request, workspace: str | None = None) -> list[dict[str, Any]]:
    from polaris.delivery.http.v2 import director as _d

    state = getattr(request.app.state, "app_state", None) or request.app.state
    resolved_workspace = _d._workspace_from_request(request, workspace)
    try:
        projection = await _d.RuntimeProjectionService.build_async(resolved_workspace, state=state)
    except (RuntimeError, ValueError):
        _d.logger.debug("Director worker projection unavailable for workspace=%s", resolved_workspace, exc_info=True)
        return []
    return _d._worker_rows_from_projection(projection)


def _get_workflow_snapshot_sync(
    workspace: str,
    *,
    ramdisk_root: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    from polaris.delivery.http.v2 import director as _d

    workspace_value = str(workspace or "").strip()
    if not workspace_value:
        return None, []
    runtime_root = str(ramdisk_root or _d.resolve_env_str("ramdisk_root") or "").strip()
    try:
        if not runtime_root:
            from polaris.bootstrap.config import get_settings

            settings = get_settings()
            runtime_root = str(getattr(settings, "ramdisk_root", "") or "").strip()
        cache_root = _d.build_cache_root(runtime_root, workspace_value)
        workflow_status = _d.get_workflow_runtime_status(workspace_value, cache_root)
    except (RuntimeError, ValueError):
        # Workflow status unavailable - return empty to maintain graceful degradation
        _d.logger.debug("Failed to get workflow status for workspace=%s", workspace_value)
        return None, []

    status_payload = _d.build_workflow_status_payload(
        workflow_status,
        workspace=workspace_value,
        cache_root=cache_root,
    )
    if not isinstance(status_payload, dict):
        return None, []
    task_rows = _d.build_workflow_task_rows(
        workflow_status,
        workspace=workspace_value,
        cache_root=cache_root,
    )
    return status_payload, task_rows


async def _get_workflow_snapshot(
    workspace: str,
    *,
    ramdisk_root: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    from polaris.delivery.http.v2 import director as _d

    return await asyncio.to_thread(
        _d._get_workflow_snapshot_sync,
        workspace,
        ramdisk_root=ramdisk_root,
    )


__all__ = [
    "_append_debug",
    "_ensure_snapshot_workspace",
    "_get_workflow_snapshot",
    "_get_workflow_snapshot_sync",
    "_projected_task_response",
    "_projected_worker_rows",
    "_workspace_from_request",
]
