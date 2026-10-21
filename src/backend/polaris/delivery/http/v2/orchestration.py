"""统一编排 API 路由 (Unified Orchestration API)

POST /v2/orchestration/runs - 创建编排运行
GET /v2/orchestration/runs/{run_id} - 查询运行状态
GET /v2/orchestration/runs/{run_id}/tasks - 查询任务列表
POST /v2/orchestration/runs/{run_id}/signal - 发送控制信号

这是新的统一 API，PM/Director 专用 API 内部将转发到此处。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from polaris.cells.orchestration.workflow_runtime.public.service import (
    get_orchestration_service,
)
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.legacy_engine_retired import legacy_engine_retired
from polaris.delivery.http.routers._shared import get_state
from polaris.delivery.http.workspace import active_workspace_value
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestration", tags=["Orchestration"])


def _bound_history_workspace(request: Request, workspace: str | None) -> str:
    bound = active_workspace_value(get_state(request).settings)
    if not bound:
        raise HTTPException(status_code=400, detail="Workspace not configured")
    if workspace and Path(workspace).resolve() != Path(bound).resolve():
        raise HTTPException(status_code=404, detail="Workspace not found")
    return str(Path(bound).resolve())


def _require_history_workspace(snapshot: Any, workspace: str) -> None:
    actual = str(getattr(snapshot, "workspace", "") or "").strip()
    if not actual or Path(actual).resolve() != Path(workspace).resolve():
        raise HTTPException(status_code=404, detail="Run not found in this workspace")


# ============================================================================
# Pydantic 请求/响应模型
# ============================================================================


class RoleEntrySpecRequest(BaseModel):
    """角色条目请求"""

    role_id: str = Field(..., description="角色标识: pm, director, qa, chief_engineer, architect")
    input: str = Field(default="", description="角色输入/指令")
    scope_paths: list[str] = Field(default_factory=list, description="作用域路径")


class PipelineTaskRequest(BaseModel):
    """流水线任务请求"""

    task_id: str = Field(..., description="任务唯一标识")
    role_entry: RoleEntrySpecRequest = Field(..., description="角色规格")
    depends_on: list[str] = Field(default_factory=list, description="依赖任务ID")
    timeout_seconds: int = Field(default=MAX_WORKFLOW_TIMEOUT_SECONDS, description="超时秒数")


class PipelineSpecRequest(BaseModel):
    """流水线规格请求"""

    tasks: list[PipelineTaskRequest] = Field(..., description="任务列表")
    max_concurrency: int = Field(default=3, description="最大并发度")
    global_timeout_seconds: int = Field(default=7200, description="全局超时秒数")
    continue_on_error: bool = Field(default=False, description="错误时是否继续")


class CreateRunRequest(BaseModel):
    """创建运行请求"""

    run_id: str | None = Field(default=None, description="运行标识（不指定则自动生成）")
    workspace: str = Field(..., description="工作区路径")
    mode: str = Field(default="workflow", description="执行模式: chat, workflow")
    role_entries: list[RoleEntrySpecRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SignalRequestModel(BaseModel):
    """信号请求"""

    signal: str = Field(..., description="信号类型: cancel, pause, resume, retry, skip")
    task_id: str | None = Field(default=None, description="目标任务ID，None表示整体")
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskSnapshotResponse(BaseModel):
    """任务快照响应"""

    task_id: str
    status: str
    phase: str
    role_id: str
    current_file: str | None = None
    progress_percent: float = 0.0
    retry_count: int = 0
    error_category: str | None = None
    error_message: str | None = None


class OrchestrationSnapshotResponse(BaseModel):
    """编排快照响应"""

    schema_version: str
    run_id: str
    workspace: str
    mode: str
    status: str
    current_phase: str
    overall_progress: float
    tasks: dict[str, TaskSnapshotResponse]
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


# ============================================================================
# API 端点
# ============================================================================


@router.post("/runs", response_model=OrchestrationSnapshotResponse, dependencies=[Depends(require_auth)])
async def create_run(request: CreateRunRequest) -> OrchestrationSnapshotResponse:
    """Reject retired execution before preflight, storage or scheduling."""
    raise legacy_engine_retired("Legacy create_run")


@router.get("/runs/{run_id}", response_model=OrchestrationSnapshotResponse, dependencies=[Depends(require_auth)])
async def get_run(run_id: str, request: Request, workspace: str = "") -> OrchestrationSnapshotResponse:
    """查询运行状态"""
    try:
        bound = _bound_history_workspace(request, workspace)
        service = await get_orchestration_service(read_only=True)
        snapshot = await service.query_run(run_id) if service is not None else None

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        _require_history_workspace(snapshot, bound)
        return _convert_snapshot_to_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error("query_run failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.get("/runs/{run_id}/tasks", dependencies=[Depends(require_auth)])
async def get_run_tasks(run_id: str, request: Request, workspace: str = "") -> dict[str, Any]:
    """查询运行任务列表"""
    try:
        bound = _bound_history_workspace(request, workspace)
        service = await get_orchestration_service(read_only=True)
        snapshot = await service.query_run(run_id) if service is not None else None
        if snapshot is None or service is None:
            raise HTTPException(status_code=404, detail="Run not found")
        _require_history_workspace(snapshot, bound)
        result = await service.query_run_tasks(run_id)

        if "error" in result:
            logger.error("query_run_tasks returned error: run_id=%s: %s", run_id, result["error"])
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="internal error",
            )

        return result

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error("query_run_tasks failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.post("/runs/{run_id}/signal", dependencies=[Depends(require_auth)])
async def signal_run(run_id: str, request: SignalRequestModel) -> OrchestrationSnapshotResponse:
    """New processes cannot mutate a frozen legacy owner without its authority."""
    raise legacy_engine_retired("Legacy signal_run")


@router.delete("/runs/{run_id}", dependencies=[Depends(require_auth)])
async def cancel_run(run_id: str, force: bool = False) -> OrchestrationSnapshotResponse:
    """New processes cannot mutate a frozen legacy owner without its authority."""
    raise legacy_engine_retired("Legacy cancel_run")


@router.get("/runs", dependencies=[Depends(require_auth)])
async def list_runs(
    request: Request,
    workspace: str | None = None,
    run_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """列出编排运行"""
    try:
        bound = _bound_history_workspace(request, workspace)
        service = await get_orchestration_service(read_only=True)
        snapshots = (
            await service.list_runs(
                workspace=bound,
                status=run_status,
                limit=limit,
                offset=offset,
            )
            if service is not None
            else []
        )
        snapshots = [snapshot for snapshot in snapshots if str(Path(snapshot.workspace).resolve()) == bound]

        return {
            "runs": [_convert_snapshot_to_response(s) for s in snapshots],
            "total": len(snapshots),
            "limit": limit,
            "offset": offset,
        }

    except (RuntimeError, ValueError) as e:
        logger.error("list_runs failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


# ============================================================================
# 辅助函数
# ============================================================================


def _convert_snapshot_to_response(snapshot) -> OrchestrationSnapshotResponse:
    """转换快照为响应模型"""
    tasks = {}
    for task_id, task in snapshot.tasks.items():
        tasks[task_id] = TaskSnapshotResponse(
            task_id=task.task_id,
            status=task.status.value,
            phase=task.phase.value,
            role_id=task.role_id,
            current_file=task.current_file,
            progress_percent=task.progress_percent,
            retry_count=task.retry_count,
            error_category=task.error_category,
            error_message=task.error_message,
        )

    return OrchestrationSnapshotResponse(
        schema_version=snapshot.schema_version,
        run_id=snapshot.run_id,
        workspace=snapshot.workspace,
        mode=snapshot.mode,
        status=snapshot.status.value,
        current_phase=snapshot.current_phase.value,
        overall_progress=snapshot.overall_progress,
        tasks=tasks,
        created_at=snapshot.created_at.isoformat() if snapshot.created_at else None,
        updated_at=snapshot.updated_at.isoformat() if snapshot.updated_at else None,
        completed_at=snapshot.completed_at.isoformat() if snapshot.completed_at else None,
    )
