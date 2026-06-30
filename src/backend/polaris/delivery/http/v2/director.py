"""Director API v2 routes.

Director v2 endpoints backed by the governed workflow services.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import warnings
from pathlib import Path  # patched via director.Path / dereferenced by support helpers
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

# Governed orchestration integration
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,  # patched/dereferenced via director.<name>
)
from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationError,
    get_orchestration_service,
)
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path

# Backward-compat namespace re-exports. These names were importable as module
# attributes (``director.<name>``) before the model classes moved to
# ``director_models``; the model bodies that referenced them now live there.
# They are re-imported here purely to preserve the module surface for any
# caller or test that resolved them via this module.
from polaris.cells.runtime.projection.public.role_contracts import (
    RoleTaskContractV1,
)

# NOTE: ``select_task_rows_from_projection``, ``QueryTaskMarketStatusV1``,
# ``get_task_market_service`` and ``TaskRuntimeService`` are part of the frozen
# monkeypatch surface. They are no longer referenced by bare name in this module
# (the helpers that used them moved to ``director_task_rows`` and dereference them
# through this module object), so they appear unused to linters; they MUST stay
# importable as ``director.<name>`` for tests/helpers — hence the ``noqa``.
from polaris.cells.runtime.projection.public.service import (
    RuntimeProjectionService,
    build_cache_root,
    build_llm_status,  # patched/dereferenced via director.<name>
    build_workflow_status_payload,  # dereferenced via director.<name> from support
    build_workflow_task_rows,  # patched/dereferenced via director.<name>
    get_workflow_runtime_status,  # dereferenced via director.<name> from support
    merge_director_status,
    select_task_rows_from_projection,  # patched/dereferenced via director.<name>
)
from polaris.cells.runtime.task_market.public.contracts import (
    QueryTaskMarketStatusV1,  # referenced via director.<name>
)
from polaris.cells.runtime.task_market.public.service import (
    get_task_market_service,  # patched/dereferenced via director.<name>
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,  # referenced via director.<name>
)
from polaris.delivery.http.dependencies import (
    get_director_service as get_director_service_dep,
    require_auth,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,  # patched/dereferenced via director.<name>
)
from polaris.delivery.http.schemas.common import RoleCapabilitiesResponse

# Readiness diagnostics + blueprint-evidence helpers extracted to a sibling
# module. Re-imported here so ``director.<name>`` keeps resolving for callers/tests
# and so route handlers can keep calling them by bare name. These helpers
# dereference their patchable collaborators through the ``director`` module object
# at call time.
from polaris.delivery.http.v2.director_diagnostics import (
    _blueprint_artifact_state,
    _build_director_diagnostics,
    _build_director_diagnostics_for_request,
    _build_llm_diagnostics,
    _ensure_director_can_execute,
    _ensure_director_lifecycle_can_start,
    _load_all_blueprint_payloads,
    _load_blueprint_payload_by_id,
    _load_blueprint_payload_by_path,
    _parse_task_priority,
    _resolve_blueprint_path,
    _task_diagnostics_from_rows,
)

# Pure leaf helpers extracted to a sibling module during the lossless module
# split. They are re-imported here so that ``director.<name>`` keeps resolving
# for every existing caller/test, and so that the helpers and route handlers
# that remain in this module can keep calling them by bare name.
from polaris.delivery.http.v2.director_helpers import (
    _as_dict,
    _blueprint_contract_list,
    _blueprint_handoff_missing_fields,
    _blueprint_payload_is_handoff_ready,
    _blueprint_payload_is_traceability_only,
    _blueprint_payload_matches_task,
    _blueprint_reference_values,
    _cancel_failure_detail,
    _cancel_success_payload,
    _director_diagnostic_issues,
    _director_execution_blockers,
    _director_orchestration_response,
    _director_run_task_ids_from_diagnostics,
    _director_snapshot_status,
    _director_snapshot_task_count,
    _director_tasks_queued,
    _first_string_list,
    _first_text,
    _flatten_director_status,
    _is_workflow_shell_task,
    _merge_task_rows_by_identity,
    _normalize_task_status_token,
    _path_is_within,
    _payload_task_identity_values,
    _projection_source_for_task_rows,
    _role_payload,
    _row_requires_blueprint_evidence,
    _state_token,
    _string_list,
    _task_details,
    _task_id_from_row,
    _task_identity_tokens,
    _task_response_from_row,
    _task_row_from_object,
    _task_row_matches_id,
    _task_rows_from_local_tasks,
    _text_or_none,
    _utc_now,
    _with_task_projection_source,
    _worker_diagnostics_from_workers,
    _worker_id_from_row,
    _worker_payload_from_object,
    _worker_row_matches_id,
    _worker_rows_from_payload,
    _worker_rows_from_projection,
)

# Re-export barrel for the declarative request/response + diagnostics models so
# ``director.<ModelName>`` keeps resolving for callers/tests. The diagnostics
# section models are consumed by helpers in ``director_diagnostics`` (via the
# ``director`` namespace) and are part of the frozen public surface, so they are
# imported here even though this module no longer references them by bare name.
from polaris.delivery.http.v2.director_models import (
    DirectorDiagnosticsLLMSection,
    DirectorDiagnosticsResponse,
    DirectorDiagnosticsStatusSection,
    DirectorDiagnosticsTaskSection,
    DirectorDiagnosticsWorkerSection,
    DirectorIntegrationQaRequest,
    DirectorIntegrationQaResponse,
    DirectorOrchestrationResponse,
    DirectorRunOrchestrationRequest,
    DirectorStatusResponse,  # re-exported for backward-compat callers/tests
    TaskCreateRequest,
    TaskResponse,
)

# Request-bound support helpers (workspace / snapshot / projection IO + optional
# debug evidence) extracted to a sibling module. Re-imported here so
# ``director.<name>`` keeps resolving for callers/tests (notably the direct
# ``_append_debug`` import) and so route handlers can keep calling them by bare
# name. These helpers dereference their patchable collaborators through the
# ``director`` module object at call time.
from polaris.delivery.http.v2.director_support import (
    _append_debug,
    _ensure_snapshot_workspace,
    _get_workflow_snapshot,
    _get_workflow_snapshot_sync,
    _projected_task_response,
    _projected_worker_rows,
    _workspace_from_request,
)

# Row-assembly + task-market projection helpers extracted to a sibling module.
# Re-imported here so ``director.<name>`` keeps resolving for callers/tests and so
# in-module call sites can keep using bare names. These helpers dereference their
# patchable collaborators through the ``director`` module object at call time.
from polaris.delivery.http.v2.director_task_rows import (
    _contract_backed_task_rows,
    _projection_task_rows,
    _runtime_backed_task_rows,
    _runtime_task_rows_for_workspace,
    _task_market_execution_rows_for_workspace,
    _task_market_row_to_director_task_row,
)
from polaris.delivery.http.v2.llm_event_filters import filter_llm_events_by_workspace
from polaris.delivery.http.workspace import (
    active_workspace_value,  # dereferenced via director.<name> from diagnostics
    requested_or_active_workspace,
    settings_with_workspace_override,  # dereferenced via director.<name> from diagnostics
    workspace_values_match,  # dereferenced via director.<name> from support
)
from polaris.domain.entities import TaskPriority  # dereferenced via director.<name>
from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.constants import (
    DEFAULT_DIRECTOR_MAX_PARALLELISM,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
)
from pydantic import BaseModel, Field  # noqa: F401

if TYPE_CHECKING:
    from polaris.cells.director.execution.public.service import DirectorService

logger = logging.getLogger(__name__)

# Explicit re-export surface. The lossless module split moved ~90 helpers into
# sibling modules (``director_diagnostics``, ``director_support``,
# ``director_task_rows``, ``director_helpers``). Those siblings dereference their
# patchable collaborators (and each other) through this module object at call
# time (``from . import director as _d; _d.X(...)``) so that the monkeypatch
# contract is honored — tests patch ``polaris.delivery.http.v2.director.<name>``
# (e.g. ``BlueprintPersistence``, ``RuntimeProjectionService``,
# ``ensure_required_roles_ready``, ``select_task_rows_from_projection``,
# ``build_workflow_task_rows``, ``resolve_artifact_path``, ``get_task_market_service``,
# ``_contract_backed_task_rows`` ...) and drive full routes. Because those names
# are imported (not defined) here, ``mypy --strict --no-implicit-reexport`` would
# otherwise flag every ``_d.<name>`` access with ``[attr-defined]``. Declaring the
# re-export surface explicitly via ``__all__`` marks them as exported (behavior is
# unchanged: same objects, same ``_d.<name>`` dereference, same patch points).
__all__ = [
    "DEFAULT_DIRECTOR_MAX_PARALLELISM",
    "DEFAULT_OPERATION_TIMEOUT_SECONDS",
    "BlueprintPersistence",
    "DirectorDiagnosticsLLMSection",
    "DirectorDiagnosticsResponse",
    "DirectorDiagnosticsStatusSection",
    "DirectorDiagnosticsTaskSection",
    "DirectorDiagnosticsWorkerSection",
    "DirectorIntegrationQaRequest",
    "DirectorIntegrationQaResponse",
    "DirectorOrchestrationResponse",
    "DirectorRunOrchestrationRequest",
    "DirectorStatusResponse",
    "OrchestrationError",
    "Path",
    "QueryTaskMarketStatusV1",
    "RoleCapabilitiesResponse",
    "RoleTaskContractV1",
    "RuntimeProjectionService",
    "StructuredHTTPException",
    "TaskCreateRequest",
    "TaskPriority",
    "TaskResponse",
    "TaskRuntimeService",
    "_append_debug",
    "_as_dict",
    "_blueprint_artifact_state",
    "_blueprint_contract_list",
    "_blueprint_handoff_missing_fields",
    "_blueprint_payload_is_handoff_ready",
    "_blueprint_payload_is_traceability_only",
    "_blueprint_payload_matches_task",
    "_blueprint_reference_values",
    "_build_director_diagnostics",
    "_build_director_diagnostics_for_request",
    "_build_llm_diagnostics",
    "_cancel_failure_detail",
    "_cancel_success_payload",
    "_contract_backed_task_rows",
    "_director_diagnostic_issues",
    "_director_execution_blockers",
    "_director_orchestration_response",
    "_director_run_task_ids_from_diagnostics",
    "_director_snapshot_status",
    "_director_snapshot_task_count",
    "_director_tasks_queued",
    "_ensure_director_can_execute",
    "_ensure_director_lifecycle_can_start",
    "_ensure_snapshot_workspace",
    "_first_string_list",
    "_first_text",
    "_flatten_director_status",
    "_get_workflow_snapshot",
    "_get_workflow_snapshot_sync",
    "_is_workflow_shell_task",
    "_load_all_blueprint_payloads",
    "_load_blueprint_payload_by_id",
    "_load_blueprint_payload_by_path",
    "_merge_director_status",
    "_merge_task_rows_by_identity",
    "_normalize_task_status_token",
    "_parse_task_priority",
    "_path_is_within",
    "_payload_task_identity_values",
    "_projected_task_response",
    "_projected_worker_rows",
    "_projection_source_for_task_rows",
    "_projection_task_rows",
    "_resolve_blueprint_path",
    "_role_payload",
    "_row_requires_blueprint_evidence",
    "_runtime_backed_task_rows",
    "_runtime_task_rows_for_workspace",
    "_state_token",
    "_string_list",
    "_task_details",
    "_task_diagnostics_from_rows",
    "_task_id_from_row",
    "_task_identity_tokens",
    "_task_market_execution_rows_for_workspace",
    "_task_market_row_to_director_task_row",
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
    "_workspace_from_request",
    "active_workspace_value",
    "build_cache_root",
    "build_llm_status",
    "build_workflow_status_payload",
    "build_workflow_task_rows",
    "cancel_task",
    "clear_cache",
    "create_task",
    "director_cancel_orchestration",
    "director_get_orchestration",
    "director_run_integration_qa",
    "director_run_orchestration",
    "ensure_required_roles_ready",
    "filter_llm_events_by_workspace",
    "get_cache_stats",
    "get_director_capabilities",
    "get_director_diagnostics",
    "get_director_service_dep",
    "get_global_emitter",
    "get_global_token_budget",
    "get_llm_events",
    "get_orchestration_service",
    "get_status",
    "get_task",
    "get_task_llm_events",
    "get_task_market_service",
    "get_token_budget_stats",
    "get_worker",
    "get_workflow_runtime_status",
    "list_tasks",
    "list_workers",
    "logger",
    "merge_director_status",
    "requested_or_active_workspace",
    "resolve_artifact_path",
    "resolve_env_str",
    "router",
    "select_task_rows_from_projection",
    "settings_with_workspace_override",
    "start_director",
    "stop_director",
    "workspace_values_match",
]


# Deprecation removal target: 2026-06-30.
# Backward-compat re-export for tests.
# Tests should import merge_director_status directly from
# polaris.cells.runtime.projection.public.service.
# This alias will be removed in v2.0.
def _merge_director_status(*args: Any, **kwargs: Any) -> Any:
    warnings.warn(
        "_merge_director_status re-export is deprecated. "
        "Import merge_director_status from "
        "polaris.cells.runtime.projection.public.service instead. "
        "Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    return merge_director_status(*args, **kwargs)


router = APIRouter(prefix="/director", tags=["Director v2"])


# Request/Response models are declared in ``director_models`` and re-exported
# at the top of this module (see ``from .director_models import ...``).


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_director(
    request: Request,
    workspace: str = "",
) -> dict[str, Any]:
    """Start the Director service."""
    _ensure_director_lifecycle_can_start(request, workspace)
    service = await get_director_service_dep(request)
    await service.start()
    return {"ok": True, "state": service.state.name, "workspace": _workspace_from_request(request, workspace)}


@router.post("/stop", dependencies=[Depends(require_auth)])
async def stop_director(
    request: Request,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Stop the Director service."""
    await service.stop()
    return {"ok": True, "state": service.state.name, "workspace": _workspace_from_request(request, workspace)}


@router.get("/status", dependencies=[Depends(require_auth)])
async def get_status(
    request: Request,
    source: Literal["local", "auto"] = "auto",
    workspace: str = "",
) -> dict[str, Any]:
    """Get director status.

    By default this endpoint returns the workflow-aware RuntimeProjection view.
    Callers that specifically need local service state can request
    ``source=local``.
    """
    # Get service from app state
    state = getattr(request.app.state, "app_state", None) or request.app.state

    # Use RuntimeProjectionService for consistent state
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    projection = await RuntimeProjectionService.build_async(resolved_workspace, state=state)

    selected_status = (
        getattr(projection, "director_merged", None)
        if source == "auto" and getattr(projection, "director_merged", None)
        else projection.director_local
    )
    local_status = _flatten_director_status(selected_status or {"running": False, "status": {"state": "IDLE"}})
    return {
        "ok": True,
        **local_status,
        "projection_source": "director_merged"
        if source == "auto" and getattr(projection, "director_merged", None)
        else "director_local",
    }


@router.get("/capabilities", dependencies=[Depends(require_auth)], response_model=RoleCapabilitiesResponse)
def get_director_capabilities() -> dict[str, Any]:
    """Return the Director capability matrix for desktop and workflow hosts."""
    try:
        from polaris.domain.entities.capability import get_role_capabilities

        return {
            "ok": True,
            "role": "director",
            "capabilities": get_role_capabilities("director"),
        }
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="CAPABILITY_LOAD_FAILED",
            message=str(exc),
            details={"capabilities": {}},
        ) from exc


@router.get("/diagnostics", dependencies=[Depends(require_auth)], response_model=DirectorDiagnosticsResponse)
async def get_director_diagnostics(
    request: Request,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> DirectorDiagnosticsResponse:
    """Return side-effect-free Director desktop readiness diagnostics."""
    return await _build_director_diagnostics(request, service, workspace_override=workspace)


@router.post("/tasks", response_model=TaskResponse, dependencies=[Depends(require_auth)])
async def create_task(
    http_request: Request,
    request: TaskCreateRequest,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> TaskResponse:
    """Create and submit a new task."""
    priority = _parse_task_priority(request.priority)
    blocked_by: list[int | str] = list(request.blocked_by)
    resolved_workspace = _workspace_from_request(http_request, workspace)
    metadata = dict(request.metadata)
    metadata.setdefault("workspace", resolved_workspace)
    metadata.setdefault("director_workspace", resolved_workspace)

    task = await service.submit_task(
        subject=request.subject,
        description=request.description,
        command=request.command,
        priority=priority,
        blocked_by=blocked_by,
        timeout_seconds=request.timeout_seconds,
        metadata=metadata,
    )

    row = _task_row_from_object(task)
    task_metadata = _as_dict(row.get("metadata"))
    task_metadata.setdefault("workspace", resolved_workspace)
    task_metadata.setdefault("director_workspace", resolved_workspace)
    row["metadata"] = task_metadata
    return _task_response_from_row(row)


@router.get("/tasks", dependencies=[Depends(require_auth)])
async def list_tasks(
    request: Request,
    status: str | None = None,
    source: Literal["auto", "local", "workflow"] = "auto",
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> list[TaskResponse]:
    """List all tasks.

    Task selection follows "二选一" rule:
    - workflow: use workflow tasks if available
    - local: use local service tasks
    - auto: prefer workflow, fallback to local live tasks
    """
    requested_status = _normalize_task_status_token(status) if status else None
    start = time.perf_counter()
    tasks: list[dict[str, Any]] = []
    used_projection = False
    used_local_fallback = False

    try:
        if source == "local":
            tasks = _task_rows_from_local_tasks(await service.list_tasks(status=None))
        else:
            # Use RuntimeProjectionService for workflow/auto selection only.
            # Keep local-only path fast for high-frequency observers and stress tracer.
            state = getattr(request.app.state, "app_state", None) or request.app.state
            resolved_workspace = requested_or_active_workspace(state.settings, workspace)
            projection = await RuntimeProjectionService.build_async(resolved_workspace, state=state)
            used_projection = True

            tasks = _projection_task_rows(projection)
            if tasks:
                ramdisk_root = str(
                    getattr(state.settings, "ramdisk_root", "") or resolve_env_str("ramdisk_root") or ""
                ).strip()
                cache_root = build_cache_root(ramdisk_root, resolved_workspace)
                tasks = _runtime_backed_task_rows(tasks, workspace=resolved_workspace)
                tasks = _contract_backed_task_rows(tasks, workspace=resolved_workspace, cache_root=cache_root)
            task_market_rows = _task_market_execution_rows_for_workspace(resolved_workspace)
            if task_market_rows:
                tasks = _merge_task_rows_by_identity(tasks, task_market_rows)
            if source == "auto" and not tasks:
                tasks = _task_rows_from_local_tasks(await service.list_tasks(status=None))
                used_local_fallback = True

        responses = [_task_response_from_row(t) for t in tasks]
        if requested_status is not None:
            responses = [item for item in responses if item.status == requested_status]

        return responses
    finally:
        _append_debug(
            "api.director.list_tasks",
            {
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "source": source,
                "status_filter": requested_status or "",
                "task_count": len(tasks),
                "used_projection": used_projection,
                "used_local_fallback": used_local_fallback,
            },
        )


@router.get("/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def get_task(
    request: Request,
    task_id: str,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> TaskResponse:
    """Get task by ID."""
    task = await service.get_task(task_id)
    if task:
        return _task_response_from_row(_task_row_from_object(task))

    projected = await _projected_task_response(request, task_id, workspace)
    if projected is not None:
        return projected

    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_task(
    request: Request,
    task_id: str,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Cancel a task."""
    result = await service.cancel_task(task_id)
    payload = _cancel_success_payload(task_id, result)

    if payload is None:
        raise HTTPException(status_code=400, detail=_cancel_failure_detail(result))

    payload.setdefault("workspace", _workspace_from_request(request, workspace))
    return payload


@router.get("/workers", dependencies=[Depends(require_auth)])
async def list_workers(
    request: Request,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> list[dict[str, Any]]:
    """List all workers."""
    workers = await service.list_workers()
    local_rows = [_worker_payload_from_object(worker) for worker in workers]
    if local_rows:
        return local_rows
    return await _projected_worker_rows(request, workspace)


@router.get("/workers/{worker_id}", dependencies=[Depends(require_auth)])
async def get_worker(
    request: Request,
    worker_id: str,
    workspace: str = "",
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Get worker by ID."""
    worker = await service.get_worker(worker_id)

    if worker:
        return _worker_payload_from_object(worker)

    for row in await _projected_worker_rows(request, workspace):
        if _worker_row_matches_id(row, worker_id):
            return row

    raise HTTPException(status_code=404, detail="Worker not found")


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/tasks/{task_id}/llm-events", dependencies=[Depends(require_auth)])
async def get_task_llm_events(
    request: Request,
    task_id: str,
    run_id: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """获取任务的 LLM 调用事件历史"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, limit=limit)
    resolved_workspace = _workspace_from_request(request, workspace)
    events = filter_llm_events_by_workspace(events, resolved_workspace)

    # 分类统计
    stats = {
        "total": len(events),
        "call_start": sum(1 for e in events if e.event_type == "llm_call_start"),
        "call_end": sum(1 for e in events if e.event_type == "llm_call_end"),
        "call_error": sum(1 for e in events if e.event_type == "llm_error"),
        "call_retry": sum(1 for e in events if e.event_type == "llm_retry"),
        "validation_pass": sum(1 for e in events if e.event_type == "validation_pass"),
        "validation_fail": sum(1 for e in events if e.event_type == "validation_fail"),
        "tool_execute": sum(1 for e in events if e.event_type == "tool_execute"),
    }

    return {
        "task_id": task_id,
        "run_id": run_id,
        "workspace": resolved_workspace,
        "events": [e.to_dict() for e in events],
        "stats": stats,
    }


@router.get("/llm-events", dependencies=[Depends(require_auth)])
async def get_llm_events(
    request: Request,
    run_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """获取全局 LLM 调用事件历史（按角色/任务过滤）"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)
    resolved_workspace = _workspace_from_request(request, workspace)
    events = filter_llm_events_by_workspace(events, resolved_workspace)

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "workspace": resolved_workspace,
    }


@router.get("/cache-stats", dependencies=[Depends(require_auth)])
async def get_cache_stats() -> dict[str, Any]:
    """获取 LLM 缓存统计信息"""
    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    return cache.get_stats()


@router.post("/cache-clear", dependencies=[Depends(require_auth)])
async def clear_cache() -> dict[str, Any]:
    """清空 LLM 缓存"""
    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    cache.clear()
    return {"ok": True, "message": "Cache cleared"}


@router.post(
    "/integration-qa",
    response_model=DirectorIntegrationQaResponse,
    dependencies=[Depends(require_auth)],
)
async def director_run_integration_qa(
    request: Request,
    payload: DirectorIntegrationQaRequest,
) -> DirectorIntegrationQaResponse:
    """Run the canonical post-dispatch integration QA after Director reaches terminal state."""
    try:
        from polaris.cells.orchestration.pm_dispatch.public.service import (
            run_post_dispatch_integration_qa,
        )
        from polaris.cells.orchestration.workflow_runtime.public.service import (
            build_integration_qa_tasks_from_director_result,
            persist_director_result_from_runtime,
        )

        settings = request.app.state.app_state.settings
        workspace = requested_or_active_workspace(settings, payload.workspace)
        run_id = str(payload.run_id or "").strip() or f"director-qa-{int(time.time())}"
        director_result = persist_director_result_from_runtime(workspace=workspace, run_id=run_id)
        if director_result is None:
            raise StructuredHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                code="director_result_not_terminal",
                message="Director tasks are not terminal; integration QA cannot run yet.",
                details={"workspace": workspace, "run_id": run_id},
            )

        task_rows = build_integration_qa_tasks_from_director_result(director_result)
        run_dir = resolve_artifact_path(workspace, "", f"runtime/runs/{run_id}")
        await asyncio.to_thread(os.makedirs, run_dir, exist_ok=True)
        run_events = resolve_artifact_path(workspace, "", f"runtime/runs/{run_id}/events/runtime.events.jsonl")
        dialogue_full = resolve_artifact_path(workspace, "", "runtime/events/dialogue.transcript.jsonl")
        qa_result = await asyncio.to_thread(
            run_post_dispatch_integration_qa,
            args=SimpleNamespace(integration_qa=True),
            workspace_full=workspace,
            cache_root_full="",
            run_dir=run_dir,
            run_id=run_id,
            iteration=int(payload.iteration or 0),
            tasks=task_rows,
            run_events=run_events,
            dialogue_full=dialogue_full,
        )
        return DirectorIntegrationQaResponse(
            ok=bool(qa_result.get("passed") is True),
            workspace=workspace,
            run_id=run_id,
            director_result=director_result,
            result=qa_result,
        )
    except StructuredHTTPException:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error("Failed to run Director integration QA: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from exc


@router.get("/token-budget-stats", dependencies=[Depends(require_auth)])
async def get_token_budget_stats() -> dict[str, Any]:
    """获取 Token 预算统计信息"""
    budget = get_global_token_budget()
    return budget.get_stats()


# ============================================================================
# Unified orchestration endpoint
# ============================================================================


@router.post(
    "/run",
    response_model=DirectorOrchestrationResponse,
    dependencies=[Depends(require_auth)],
)
async def director_run_orchestration(
    request: Request,
    payload: DirectorRunOrchestrationRequest,
) -> DirectorOrchestrationResponse:
    """Execute Director run - unified entry point

    Phase 4: Uses OrchestrationCommandService as the single write path.
    All Director execution goes through this endpoint for consistency.

    Example:
        POST /v2/director/run
        {
            "workspace": ".",
            "max_workers": 3,
            "execution_mode": "parallel"
        }
    """
    try:
        # Phase 4: Use OrchestrationCommandService as single entry point
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        settings = request.app.state.app_state.settings
        workspace = requested_or_active_workspace(settings, payload.workspace)
        diagnostics = await _build_director_diagnostics_for_request(request, workspace)
        _ensure_director_can_execute(diagnostics)

        service = OrchestrationCommandService(settings)
        explicit_task_ids = [str(payload.task_id).strip()] if str(payload.task_id or "").strip() else []
        if explicit_task_ids or not str(payload.task_filter or "").strip():
            task_ids, task_selection_source = _director_run_task_ids_from_diagnostics(diagnostics, explicit_task_ids)
        else:
            task_ids, task_selection_source = [], "filter_request"
        task_filter = payload.task_filter or (task_ids[0] if len(task_ids) == 1 else None)

        result = await service.execute_director_run(
            workspace=workspace,
            tasks=task_ids,
            options={
                "task_filter": task_filter,
                "task_id": payload.task_id,
                "max_workers": payload.max_workers,
                "execution_mode": payload.execution_mode,
                "metadata": {
                    "task_selection_source": task_selection_source,
                    "selected_task_ids": task_ids,
                },
            },
        )

        # Register adapters for execution
        orch_service = await get_orchestration_service()
        from polaris.cells.roles.adapters.public.service import register_all_adapters

        register_all_adapters(orch_service)

        return DirectorOrchestrationResponse(
            run_id=result.run_id,
            status=result.status,
            workspace=workspace,
            tasks_queued=_director_tasks_queued(result, task_ids),
            message=result.message or f"Director started in {payload.execution_mode} mode",
        )

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error("Failed to start Director orchestration: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.get("/runs/{run_id}", response_model=DirectorOrchestrationResponse, dependencies=[Depends(require_auth)])
async def director_get_orchestration(
    request: Request,
    run_id: str,
    workspace: str = "",
) -> DirectorOrchestrationResponse:
    """查询 Director 编排运行状态"""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        _ensure_snapshot_workspace(request, snapshot, run_id, workspace)
        return _director_orchestration_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError, OrchestrationError) as e:
        logger.error("Failed to query Director run: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.post(
    "/runs/{run_id}/cancel",
    response_model=DirectorOrchestrationResponse,
    dependencies=[Depends(require_auth)],
)
async def director_cancel_orchestration(
    request: Request,
    run_id: str,
    workspace: str = "",
) -> DirectorOrchestrationResponse:
    """Cancel a Director orchestration run and return the resulting snapshot."""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        _ensure_snapshot_workspace(request, snapshot, run_id, workspace)
        status_token = _director_snapshot_status(snapshot).lower()
        terminal_statuses = {"completed", "failed", "cancelled", "canceled", "blocked", "timeout"}
        if status_token not in terminal_statuses:
            snapshot = await service.cancel_run(run_id)

        return _director_orchestration_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError, OrchestrationError) as e:
        logger.error("Failed to cancel Director run: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e
