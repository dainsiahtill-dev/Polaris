"""PM (Project Manager) API routes v2.

Thin layer that delegates to PMService.
All business logic is in the service layer.

This is the V2 API - use /v2/pm/* endpoints.

Phase 6 Update: 新增统一编排兼容端点，内部转发到 UnifiedOrchestrationService
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

# Phase 6: 统一编排集成
from polaris.cells.orchestration.workflow_runtime.public.service import get_orchestration_service
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.delivery.http.dependencies import get_pm_service, require_auth
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from polaris.cells.orchestration.pm_planning.public.service import PMService

router = APIRouter(prefix="/pm", tags=["PM"])


# ============================================================================
# Phase 6: 统一编排请求模型
# ============================================================================


class PMRunOrchestrationRequest(BaseModel):
    """PM 运行编排请求（统一编排 API）"""

    workspace: str = Field(default=".", description="工作区路径")
    directive: str = Field(default="", description="需求指令")
    stage: str = Field(default="pm", description="阶段: architect 或 pm")
    run_director: bool = Field(default=False, description="是否自动运行 Director")
    director_iterations: int = Field(default=2, description="Director 迭代次数")
    metadata: dict[str, object] = Field(default_factory=dict, description="可选运行时元数据")


class PMOrchestrationResponse(BaseModel):
    """PM 编排响应"""

    run_id: str
    status: str
    workspace: str
    stage: str
    message: str


@router.post(
    "/run_once",
    dependencies=[Depends(require_auth)],
    responses={
        409: {"description": "Process already running"},
        503: {"description": "Service unavailable"},
        500: {"description": "Process error"},
    },
)
async def pm_run_once(pm_service: PMService = Depends(get_pm_service)) -> dict:
    """Run PM once.

    Raises:
        ProcessAlreadyRunningError: If PM is already running
        ServiceUnavailableError: If backend is not available
        ProcessError: If process fails to start
    """
    return await pm_service.run_once()


@router.post(
    "/start",
    dependencies=[Depends(require_auth)],
    responses={
        409: {"description": "Process already running"},
        503: {"description": "Service unavailable"},
        500: {"description": "Process error"},
    },
)
async def pm_start(
    resume: bool = False,
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Start PM in loop mode.

    This is the V2 endpoint - prefer /v2/pm/start over /pm/start_loop.

    Args:
        resume: Whether to resume from previous state

    Raises:
        ProcessAlreadyRunningError: If PM is already running
        ServiceUnavailableError: If backend is not available
        ProcessError: If process fails to start
    """
    return await pm_service.start_loop(resume=resume)


@router.post(
    "/start_loop",
    dependencies=[Depends(require_auth)],
    responses={
        409: {"description": "Process already running"},
        503: {"description": "Service unavailable"},
        500: {"description": "Process error"},
    },
)
async def pm_start_loop(
    resume: bool = False,
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Start PM in loop mode (deprecated - use /v2/pm/start).

    DEPRECATED: This endpoint is deprecated. Use /v2/pm/start instead.
    Will be removed in v2.0.
    """
    warnings.warn(
        "/pm/start_loop is deprecated. Use /v2/pm/start instead. Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    return await pm_service.start_loop(resume=resume)


@router.post("/stop", dependencies=[Depends(require_auth)])
async def pm_stop(
    graceful: bool = True,
    graceful_timeout: float = 5.0,
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Stop PM process with graceful shutdown support.

    Args:
        graceful: Whether to attempt graceful shutdown first (via stop flag)
        graceful_timeout: Seconds to wait for graceful shutdown
    """
    return await pm_service.stop(
        graceful=graceful,
        graceful_timeout=graceful_timeout,
    )


@router.get("/status", dependencies=[Depends(require_auth)])
def pm_status(pm_service: PMService = Depends(get_pm_service)) -> dict:
    """Get PM process status."""
    return pm_service.get_status()


# ============================================================================
# Phase 6: 统一编排兼容端点
# ============================================================================


@router.post(
    "/run",
    response_model=PMOrchestrationResponse,
    dependencies=[Depends(require_auth)],
)
async def pm_run_orchestration(
    request: Request,
    payload: PMRunOrchestrationRequest,
) -> PMOrchestrationResponse:
    """Execute PM run - unified entry point

    Phase 4: Uses OrchestrationCommandService as the single write path.
    All PM execution goes through this endpoint for consistency.

    Example:
        POST /v2/pm/run
        {
            "workspace": ".",
            "directive": "实现用户登录功能",
            "stage": "architect",
            "run_director": true
        }
    """
    try:
        # Phase 4: Use OrchestrationCommandService as single entry point
        from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

        service = OrchestrationCommandService(request.app.state.app_state.settings)

        result = await service.execute_pm_run(
            workspace=payload.workspace,
            run_type=payload.stage,
            options={
                "directive": payload.directive,
                "run_director": payload.run_director,
                "director_iterations": payload.director_iterations,
                "metadata": dict(payload.metadata),
            },
        )

        # Register adapters for execution
        orch_service = await get_orchestration_service()
        from polaris.cells.roles.adapters.public.service import register_all_adapters

        register_all_adapters(orch_service)

        return PMOrchestrationResponse(
            run_id=result.run_id,
            status=result.status,
            workspace=payload.workspace,
            stage=payload.stage,
            message=result.message or f"PM {payload.stage} run started",
        )

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.get("/runs/{run_id}", response_model=PMOrchestrationResponse, dependencies=[Depends(require_auth)])
async def pm_get_orchestration(run_id: str) -> PMOrchestrationResponse:
    """查询 PM 编排运行状态"""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        task_roles = {
            str(task.role_id or "").strip() for task in snapshot.tasks.values() if str(task.role_id or "").strip()
        }
        if len(task_roles) == 1:
            stage = next(iter(task_roles))
        else:
            stage = snapshot.current_phase.value if snapshot.current_phase else "unknown"

        return PMOrchestrationResponse(
            run_id=snapshot.run_id,
            status=snapshot.status.value,
            workspace=str(snapshot.workspace),
            stage=stage,
            message=f"Status: {snapshot.status.value}",
        )

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        import logging

        logging.getLogger(__name__).error("pm_get_orchestration failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/llm-events", dependencies=[Depends(require_auth)])
async def get_pm_llm_events(
    run_id: str,
    task_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """获取 PM 的 LLM 调用事件历史"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role="pm", limit=limit)

    return {
        "run_id": run_id,
        "task_id": task_id,
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


__all__ = ["router"]
