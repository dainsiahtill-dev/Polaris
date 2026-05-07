"""Director API v2 routes.

Phase 6 Update: 新增统一编排兼容端点，内部可转发到 UnifiedOrchestrationService
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status

# Phase 6: 统一编排集成
from polaris.cells.orchestration.workflow_runtime.public.service import get_orchestration_service
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.projection.public.role_contracts import RoleTaskContractV1
from polaris.cells.runtime.projection.public.service import (
    RuntimeProjectionService,
    build_cache_root,
    build_workflow_status_payload,
    build_workflow_task_rows,
    get_workflow_runtime_status,
    merge_director_status,
    select_task_rows_from_projection,
)
from polaris.delivery.http.dependencies import (
    get_director_service as get_director_service_dep,
    require_auth,
)
from polaris.domain.entities import TaskPriority
from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM, DEFAULT_OPERATION_TIMEOUT_SECONDS
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from polaris.cells.director.execution.public.service import DirectorService

logger = logging.getLogger(__name__)


# TODO: remove after 2026-06-30
# Backward-compat re-export for tests.
# Tests should import merge_director_status directly from
# polaris.cells.runtime.projection.public.service.
# This alias will be removed in v2.0.
def _merge_director_status(*args, **kwargs):
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


def _append_debug(event: str, payload: dict[str, Any]) -> None:
    try:
        log_path = Path(os.environ.get("KERNELONE_BACKEND_DEBUG_LOG", "C:/Temp/hp_backend_debug.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "event": event,
            "payload": payload,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (RuntimeError, ValueError):
        # Event logging failure should not break main flow, but visibility is compromised
        pass


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


def _projection_task_rows(projection: Any) -> list[dict[str, Any]]:
    rows = getattr(projection, "task_rows", None)
    if isinstance(rows, list) and rows:
        return [item for item in rows if isinstance(item, dict)]
    selected = select_task_rows_from_projection(projection)
    if selected:
        return selected
    snapshot = getattr(projection, "snapshot", None)
    snapshot_tasks = snapshot.get("tasks") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot_tasks, list):
        return []

    fallback_rows: list[dict[str, Any]] = []
    for item in snapshot_tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or item.get("task_id") or "").strip()
        if not task_id:
            continue
        raw_metadata = item.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        metadata.setdefault("pm_task_id", task_id)
        status_token = str(item.get("status") or "PENDING").strip().upper()
        if status_token in {"TODO", "TO_DO"}:
            status_token = "PENDING"
        fallback_rows.append(
            {
                "id": task_id,
                "subject": str(item.get("subject") or item.get("title") or task_id).strip(),
                "description": str(item.get("description") or item.get("goal") or "").strip(),
                "status": status_token,
                "priority": str(item.get("priority") or "MEDIUM").strip() or "MEDIUM",
                "claimed_by": item.get("claimed_by"),
                "result": item.get("result") if isinstance(item.get("result"), dict) else None,
                "metadata": metadata,
            }
        )
    return fallback_rows


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
                item.get("title"),
                item.get("name"),
                item.get("path"),
                item.get("id"),
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
        "CLAIMED": "CLAIMED",
        "IN_PROGRESS": "RUNNING",
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
    result = getattr(task, "result", None)
    result_payload = result.to_dict() if result and hasattr(result, "to_dict") else result
    metadata = getattr(task, "metadata", None)
    status_value = getattr(getattr(task, "status", None), "name", None) or getattr(task, "status", None)
    priority_value = getattr(getattr(task, "priority", None), "name", None) or getattr(task, "priority", None)
    return {
        "id": str(getattr(task, "id", "")),
        "subject": getattr(task, "subject", ""),
        "description": getattr(task, "description", ""),
        "status": status_value,
        "priority": priority_value,
        "claimed_by": getattr(task, "claimed_by", None),
        "result": result_payload if isinstance(result_payload, dict) else None,
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


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


def _get_workflow_snapshot_sync(
    workspace: str,
    *,
    ramdisk_root: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    workspace_value = str(workspace or "").strip()
    if not workspace_value:
        return None, []
    runtime_root = str(ramdisk_root or resolve_env_str("ramdisk_root") or "").strip()
    try:
        if not runtime_root:
            from polaris.bootstrap.config import get_settings

            settings = get_settings()
            runtime_root = str(getattr(settings, "ramdisk_root", "") or "").strip()
        cache_root = build_cache_root(runtime_root, workspace_value)
        workflow_status = get_workflow_runtime_status(workspace_value, cache_root)
    except (RuntimeError, ValueError):
        # Workflow status unavailable - return empty to maintain graceful degradation
        logger.debug("Failed to get workflow status for workspace=%s", workspace_value)
        return None, []

    status_payload = build_workflow_status_payload(
        workflow_status,
        workspace=workspace_value,
        cache_root=cache_root,
    )
    if not isinstance(status_payload, dict):
        return None, []
    task_rows = build_workflow_task_rows(
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
    return await asyncio.to_thread(
        _get_workflow_snapshot_sync,
        workspace,
        ramdisk_root=ramdisk_root,
    )


# Request/Response models
# ============================================================================
# Phase 6: 统一编排请求模型
# ============================================================================


class DirectorRunOrchestrationRequest(BaseModel):
    """Director 运行编排请求"""

    workspace: str = Field(default=".", description="工作区路径")
    task_filter: str | None = Field(default=None, description="任务过滤条件")
    task_id: str | None = Field(default=None, description="指定单个任务 ID")
    max_workers: int = Field(default=DEFAULT_DIRECTOR_MAX_PARALLELISM, description="最大并行工作者数")
    execution_mode: str = Field(default="parallel", description="执行模式: serial, parallel")


class DirectorOrchestrationResponse(BaseModel):
    """Director 编排响应"""

    run_id: str
    status: str
    workspace: str
    tasks_queued: int
    message: str


# ============================================================================
# 原有请求/响应模型
# ============================================================================


class TaskCreateRequest(BaseModel):
    subject: str
    description: str = ""
    command: str | None = None
    priority: str = "MEDIUM"
    blocked_by: list[str] = Field(default_factory=list)
    timeout_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(RoleTaskContractV1):
    """Director task response bound to the shared role task contract."""


class DirectorStatusResponse(BaseModel):
    state: str
    workspace: str
    metrics: dict[str, Any]
    tasks: dict[str, Any]
    workers: dict[str, Any]
    token_budget: dict[str, Any]


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_director(
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Start the Director service."""
    await service.start()
    return {"ok": True, "state": service.state.name}


@router.post("/stop", dependencies=[Depends(require_auth)])
async def stop_director(
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Stop the Director service."""
    await service.stop()
    return {"ok": True, "state": service.state.name}


@router.get("/status", dependencies=[Depends(require_auth)])
async def get_status(
    request: Request,
    source: Literal["local", "auto"] = "local",
) -> dict[str, Any]:
    """Get director status.

    By default this endpoint preserves the legacy local-only contract. Callers
    that need PM-workflow-aware status can request ``source=auto``.
    """
    # Get service from app state
    state = getattr(request.app.state, "app_state", None) or request.app.state
    settings = state.settings

    # Use RuntimeProjectionService for consistent state
    workspace = getattr(settings, "workspace_path", None) or getattr(settings, "workspace", "")
    projection = await RuntimeProjectionService.build_async(str(workspace), state=state)

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


@router.post("/tasks", response_model=TaskResponse, dependencies=[Depends(require_auth)])
async def create_task(
    request: TaskCreateRequest,
    service: DirectorService = Depends(get_director_service_dep),
) -> TaskResponse:
    """Create and submit a new task."""
    priority = TaskPriority[request.priority]

    task = await service.submit_task(
        subject=request.subject,
        description=request.description,
        command=request.command,
        priority=priority,
        blocked_by=request.blocked_by,
        timeout_seconds=request.timeout_seconds,
        metadata=request.metadata,
    )

    return _task_response_from_row(_task_row_from_object(task))


@router.get("/tasks", dependencies=[Depends(require_auth)])
async def list_tasks(
    request: Request,
    status: str | None = None,
    source: Literal["auto", "local", "workflow"] = "auto",
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

    try:
        if source == "local":
            tasks = await service.list_tasks(status=None)
        else:
            # Use RuntimeProjectionService for workflow/auto selection only.
            # Keep local-only path fast for high-frequency observers and stress tracer.
            state = getattr(request.app.state, "app_state", None) or request.app.state
            settings = state.settings
            workspace = getattr(settings, "workspace_path", None) or getattr(settings, "workspace", "")
            projection = await RuntimeProjectionService.build_async(str(workspace), state=state)
            used_projection = True

            tasks = _projection_task_rows(projection)

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
            },
        )


@router.get("/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def get_task(
    task_id: str,
    service: DirectorService = Depends(get_director_service_dep),
) -> TaskResponse:
    """Get task by ID."""
    task = await service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return _task_response_from_row(_task_row_from_object(task))


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_task(
    task_id: str,
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Cancel a task."""
    cancelled = await service.cancel_task(task_id)

    if not cancelled:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")

    return {"ok": True, "task_id": task_id}


@router.get("/workers", dependencies=[Depends(require_auth)])
async def list_workers(
    service: DirectorService = Depends(get_director_service_dep),
) -> list[dict[str, Any]]:
    """List all workers."""
    workers = await service.list_workers()
    return [w.to_dict() for w in workers]


@router.get("/workers/{worker_id}", dependencies=[Depends(require_auth)])
async def get_worker(
    worker_id: str,
    service: DirectorService = Depends(get_director_service_dep),
) -> dict[str, Any]:
    """Get worker by ID."""
    worker = await service.get_worker(worker_id)

    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    return worker.to_dict()


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/tasks/{task_id}/llm-events", dependencies=[Depends(require_auth)])
async def get_task_llm_events(
    task_id: str,
    run_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """获取任务的 LLM 调用事件历史"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, limit=limit)

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
        "events": [e.to_dict() for e in events],
        "stats": stats,
    }


@router.get("/llm-events", dependencies=[Depends(require_auth)])
async def get_llm_events(
    run_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """获取全局 LLM 调用事件历史（按角色/任务过滤）"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
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


@router.get("/token-budget-stats", dependencies=[Depends(require_auth)])
async def get_token_budget_stats() -> dict[str, Any]:
    """获取 Token 预算统计信息"""
    budget = get_global_token_budget()
    return budget.get_stats()


# ============================================================================
# Phase 6: 统一编排兼容端点
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

        service = OrchestrationCommandService(request.app.state.app_state.settings)

        result = await service.execute_director_run(
            workspace=payload.workspace,
            tasks=[],
            options={
                "task_filter": payload.task_filter or payload.task_id,
                "task_id": payload.task_id,
                "max_workers": payload.max_workers,
                "execution_mode": payload.execution_mode,
            },
        )

        # Register adapters for execution
        orch_service = await get_orchestration_service()
        from polaris.cells.roles.adapters.public.service import register_all_adapters

        register_all_adapters(orch_service)

        return DirectorOrchestrationResponse(
            run_id=result.run_id,
            status=result.status,
            workspace=payload.workspace,
            tasks_queued=0,  # Will be populated by query
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
async def director_get_orchestration(run_id: str) -> DirectorOrchestrationResponse:
    """查询 Director 编排运行状态"""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        return DirectorOrchestrationResponse(
            run_id=snapshot.run_id,
            status=snapshot.status.value,
            workspace=str(snapshot.workspace),
            tasks_queued=len(snapshot.tasks),
            message=f"Status: {snapshot.status.value}",
        )

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error("Failed to query Director run: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e
