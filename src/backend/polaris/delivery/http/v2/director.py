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


# DEPRECATED: Backward-compat re-export for tests.
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
    blocked_by: list[str] = []
    timeout_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    metadata: dict[str, Any] = {}


class TaskResponse(BaseModel):
    id: str
    subject: str
    description: str
    status: str
    priority: str
    claimed_by: str | None
    result: dict | None
    metadata: dict[str, Any] = {}


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
async def get_status(request: Request) -> dict[str, Any]:
    """Get director status - returns local role state only, no workflow merge.

    This endpoint returns only the local Director service status.
    For unified runtime projection, use the WebSocket status endpoint.
    """
    # Get service from app state
    state = getattr(request.app.state, "app_state", None) or request.app.state
    settings = state.settings

    # Use RuntimeProjectionService for consistent state
    workspace = getattr(settings, "workspace_path", None) or getattr(settings, "workspace", "")
    projection = await RuntimeProjectionService.build_async(str(workspace), state=state)

    return {
        "ok": True,
        "status": projection.director_local or {"running": False, "status": {"state": "IDLE"}},
        "projection_source": "director_local",
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
        blocked_by=request.blocked_by,  # type: ignore[arg-type]
        timeout_seconds=request.timeout_seconds,
        metadata=request.metadata,
    )

    return TaskResponse(
        id=str(task.id),
        subject=task.subject,
        description=task.description,
        status=task.status.name,
        priority=task.priority.name,
        claimed_by=task.claimed_by,
        result=task.result.to_dict() if task.result else None,
        metadata=task.metadata,
    )


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
    from polaris.domain.entities import TaskStatus

    task_status = TaskStatus[status] if status else None
    start = time.perf_counter()
    tasks: list[dict[str, Any]] = []
    used_projection = False

    try:
        if source == "local":
            tasks = await service.list_tasks(status=task_status)
        else:
            # Use RuntimeProjectionService for workflow/auto selection only.
            # Keep local-only path fast for high-frequency observers and stress tracer.
            state = getattr(request.app.state, "app_state", None) or request.app.state
            settings = state.settings
            workspace = getattr(settings, "workspace_path", None) or getattr(settings, "workspace", "")
            projection = await RuntimeProjectionService.build_async(str(workspace), state=state)
            used_projection = True

            if source == "workflow":
                tasks = projection.workflow_archive.get("tasks", []) if projection.workflow_archive else []
            else:  # auto
                tasks = select_task_rows_from_projection(projection)

        if task_status is not None and tasks:
            selected = str(task_status.name or "").strip().upper()
            tasks = [item for item in tasks if str(item.get("status") or "").strip().upper() == selected]

        return [
            TaskResponse(
                id=str(t["id"]),
                subject=t["subject"],
                description=t.get("description", ""),
                status=t["status"],
                priority=t["priority"],
                claimed_by=t.get("claimed_by"),
                result=t.get("result"),
                metadata=t.get("metadata", {}),
            )
            for t in tasks
        ]
    finally:
        _append_debug(
            "api.director.list_tasks",
            {
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "source": source,
                "status_filter": str(task_status.name) if task_status else "",
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

    return TaskResponse(
        id=str(task.id),
        subject=task.subject,
        description=task.description,
        status=task.status.name,
        priority=task.priority.name,
        claimed_by=task.claimed_by,
        result=task.result.to_dict() if task.result else None,
        metadata=task.metadata,
    )


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
                "task_filter": payload.task_filter,
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
