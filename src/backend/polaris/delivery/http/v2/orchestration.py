"""统一编排 API 路由 (Unified Orchestration API)

POST /v2/orchestration/runs - 创建编排运行
GET /v2/orchestration/runs/{run_id} - 查询运行状态
GET /v2/orchestration/runs/{run_id}/tasks - 查询任务列表
POST /v2/orchestration/runs/{run_id}/signal - 发送控制信号

这是新的统一 API，PM/Director 专用 API 内部将转发到此处。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationMode,
    OrchestrationRunRequest,
    OrchestrationSignal,
    RoleEntrySpec,
    SignalRequest,
    get_orchestration_service,
)
from polaris.delivery.http.dependencies import require_auth
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestration", tags=["Orchestration"])


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
    """创建编排运行

    提交一个新的编排运行，系统将根据 mode 自动选择执行策略：
    - workflow: 合同驱动执行，适用于自动化场景
    - chat: 交互式执行，适用于人机协作
    """
    try:
        service = await get_orchestration_service()

        # 生成 run_id
        run_id = request.run_id or f"run-{uuid.uuid4().hex[:12]}"

        # 构建角色条目
        role_entries = [
            RoleEntrySpec(
                role_id=e.role_id,
                input=e.input,
                scope_paths=e.scope_paths,
            )
            for e in request.role_entries
        ]

        # 构建编排请求
        orch_request = OrchestrationRunRequest(
            run_id=run_id,
            workspace=__import__("pathlib").Path(request.workspace),
            mode=OrchestrationMode(request.mode),
            role_entries=role_entries,
            metadata=request.metadata,
        )

        # 校验请求
        errors = orch_request.validate()
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"errors": errors},
            )

        # 提交运行
        snapshot = await service.submit_run(orch_request)

        return _convert_snapshot_to_response(snapshot)

    except ValueError as e:
        logger.error("submit_run failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="internal error",
        ) from e
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.get("/runs/{run_id}", response_model=OrchestrationSnapshotResponse, dependencies=[Depends(require_auth)])
async def get_run(run_id: str) -> OrchestrationSnapshotResponse:
    """查询运行状态"""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

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
async def get_run_tasks(run_id: str) -> dict[str, Any]:
    """查询运行任务列表"""
    try:
        service = await get_orchestration_service()
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
    """发送控制信号

    支持的信号:
    - cancel: 取消运行
    - pause: 暂停运行
    - resume: 恢复运行
    - retry: 重试失败任务
    - skip: 跳过当前任务
    """
    try:
        service = await get_orchestration_service()

        # 转换信号
        try:
            signal = OrchestrationSignal(request.signal)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid signal: {request.signal}",
            ) from e

        signal_request = SignalRequest(
            signal=signal,
            task_id=request.task_id,
            payload=request.payload,
        )

        snapshot = await service.signal_run(run_id, signal_request)
        return _convert_snapshot_to_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error("signal_run failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.delete("/runs/{run_id}", dependencies=[Depends(require_auth)])
async def cancel_run(run_id: str, force: bool = False) -> OrchestrationSnapshotResponse:
    """取消运行"""
    try:
        service = await get_orchestration_service()
        snapshot = await service.cancel_run(run_id, force=force)
        return _convert_snapshot_to_response(snapshot)

    except (RuntimeError, ValueError) as e:
        logger.error("cancel_run failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.get("/runs", dependencies=[Depends(require_auth)])
async def list_runs(
    workspace: str | None = None,
    run_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """列出编排运行"""
    try:
        service = await get_orchestration_service()
        snapshots = await service.list_runs(
            workspace=workspace,
            status=run_status,
            limit=limit,
            offset=offset,
        )

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
