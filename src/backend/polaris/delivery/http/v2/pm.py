"""PM (Project Manager) API routes v2.

Thin layer that delegates to PMService.
All business logic is in the service layer.

This is the V2 API - use /v2/pm/* endpoints.

Phase 6 Update: 新增统一编排兼容端点，内部转发到 UnifiedOrchestrationService
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from polaris.application.health import get_lancedb_status

# Phase 6: 统一编排集成
from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationError,
    get_orchestration_service,
)
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.projection.public.service import build_llm_status
from polaris.delivery.http.dependencies import get_pm_service, require_auth
from polaris.delivery.http.routers._shared import StructuredHTTPException, ensure_required_roles_ready
from polaris.delivery.http.workspace import (
    active_workspace_value,
    requested_or_active_workspace,
    settings_with_workspace_override,
)
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from polaris.cells.orchestration.pm_planning.public.service import PMService

router = APIRouter(prefix="/pm", tags=["PM"])

_TERMINAL_ORCHESTRATION_STATUSES = {"completed", "failed", "cancelled", "canceled", "blocked", "timeout"}


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


def _pm_snapshot_status(snapshot: Any) -> str:
    status = getattr(snapshot, "status", None)
    value = getattr(status, "value", status)
    return str(value or "unknown")


def _pm_snapshot_stage(snapshot: Any) -> str:
    tasks = getattr(snapshot, "tasks", {}) or {}
    task_roles = {
        str(getattr(task, "role_id", "") or "").strip()
        for task in tasks.values()
        if str(getattr(task, "role_id", "") or "").strip()
    }
    if len(task_roles) == 1:
        return next(iter(task_roles))

    current_phase = getattr(snapshot, "current_phase", None)
    phase_value = getattr(current_phase, "value", None)
    return str(phase_value or "unknown")


def _pm_orchestration_response(snapshot: Any) -> PMOrchestrationResponse:
    status_value = _pm_snapshot_status(snapshot)
    return PMOrchestrationResponse(
        run_id=str(snapshot.run_id),
        status=status_value,
        workspace=str(snapshot.workspace),
        stage=_pm_snapshot_stage(snapshot),
        message=f"Status: {status_value}",
    )


class PMDiagnosticsLanceDBStatus(BaseModel):
    """LanceDB readiness section for PM startup diagnostics."""

    ok: bool
    state: str
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PMDiagnosticsLLMStatus(BaseModel):
    """LLM readiness section for PM startup diagnostics."""

    ok: bool
    state: str
    blocked_roles: list[str] = Field(default_factory=list)
    unsupported_roles: list[str] = Field(default_factory=list)
    required_ready_roles: list[str] = Field(default_factory=list)
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PMDiagnosticsWorkspaceStatus(BaseModel):
    """Workspace readiness section for PM startup diagnostics."""

    ok: bool
    status: str
    workspace: str
    docs_present: bool
    error: str | None = None


class PMDiagnosticsResponse(BaseModel):
    """Side-effect-free PM startup readiness snapshot."""

    ok: bool
    can_start: bool
    generated_at: str
    lancedb: PMDiagnosticsLanceDBStatus
    llm: PMDiagnosticsLLMStatus
    workspace: PMDiagnosticsWorkspaceStatus
    issues: list[str] = Field(default_factory=list)
    startup_blockers: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _build_lancedb_diagnostics() -> PMDiagnosticsLanceDBStatus:
    try:
        payload = get_lancedb_status()
    except (RuntimeError, OSError, ValueError) as exc:
        return PMDiagnosticsLanceDBStatus(
            ok=False,
            state="error",
            error=str(exc),
        )

    details = payload if isinstance(payload, dict) else {}
    ok = bool(details.get("ok"))
    state = "ready" if ok else "unavailable"
    error = str(details.get("error") or details.get("detail") or "").strip() or None
    return PMDiagnosticsLanceDBStatus(
        ok=ok,
        state=state,
        error=error,
        details=details,
    )


def _build_llm_diagnostics(settings: Any) -> PMDiagnosticsLLMStatus:
    try:
        payload = build_llm_status(settings)
    except (RuntimeError, OSError, ValueError) as exc:
        return PMDiagnosticsLLMStatus(
            ok=False,
            state="error",
            error=str(exc),
        )

    state = str(payload.get("state") or "unknown").strip().lower() if isinstance(payload, dict) else "unknown"
    blocked_roles = _string_list(payload.get("blocked_roles")) if isinstance(payload, dict) else []
    unsupported_roles = _string_list(payload.get("unsupported_roles")) if isinstance(payload, dict) else []
    required_ready_roles = _string_list(payload.get("required_ready_roles")) if isinstance(payload, dict) else []
    ok = state == "ready" and not blocked_roles and not unsupported_roles
    return PMDiagnosticsLLMStatus(
        ok=ok,
        state=state,
        blocked_roles=blocked_roles,
        unsupported_roles=unsupported_roles,
        required_ready_roles=required_ready_roles,
        details=payload if isinstance(payload, dict) else {},
    )


def _workspace_value(settings: Any, workspace_override: str | None = None) -> str:
    override = str(workspace_override or "").strip()
    if override:
        return override
    return active_workspace_value(settings)


def _build_workspace_diagnostics(
    settings: Any,
    workspace_override: str | None = None,
) -> PMDiagnosticsWorkspaceStatus:
    workspace = _workspace_value(settings, workspace_override)
    if not workspace:
        return PMDiagnosticsWorkspaceStatus(
            ok=False,
            status="missing",
            workspace="",
            docs_present=False,
            error="workspace_not_configured",
        )

    workspace_path = Path(workspace)
    exists = workspace_path.exists()
    docs_present = (workspace_path / "docs").is_dir() if exists else False
    return PMDiagnosticsWorkspaceStatus(
        ok=exists,
        status="ok" if exists else "missing",
        workspace=workspace,
        docs_present=docs_present,
        error=None if exists else "workspace_path_missing",
    )


def _issue_tokens(
    lancedb: PMDiagnosticsLanceDBStatus,
    llm: PMDiagnosticsLLMStatus,
    workspace: PMDiagnosticsWorkspaceStatus,
) -> list[str]:
    issues: list[str] = []
    if not lancedb.ok:
        issues.append("lancedb_unavailable")
    if not llm.ok:
        issues.append("llm_not_ready")
    if not workspace.ok:
        issues.append("workspace_unavailable")
    elif not workspace.docs_present:
        issues.append("workspace_docs_missing")
    return issues


def _startup_blockers(
    lancedb: PMDiagnosticsLanceDBStatus,
    llm: PMDiagnosticsLLMStatus,
    workspace: PMDiagnosticsWorkspaceStatus,
) -> list[str]:
    """Return hard blockers that should disable PM desktop start controls."""
    blockers: list[str] = []
    if not lancedb.ok:
        blockers.append("lancedb_unavailable")
    if not llm.ok:
        blockers.append("llm_not_ready")
    if not workspace.ok:
        blockers.append("workspace_unavailable")
    elif not workspace.docs_present:
        blockers.append("workspace_docs_missing")
    return blockers


def _build_pm_diagnostics(
    settings: Any,
    workspace_override: str | None = None,
) -> PMDiagnosticsResponse:
    """Build the side-effect-free PM readiness snapshot used by UI and gates."""

    diagnostic_settings = settings_with_workspace_override(settings, workspace_override or "")
    lancedb = _build_lancedb_diagnostics()
    llm = _build_llm_diagnostics(diagnostic_settings)
    workspace = _build_workspace_diagnostics(diagnostic_settings)
    issues = _issue_tokens(lancedb, llm, workspace)
    startup_blockers = _startup_blockers(lancedb, llm, workspace)
    return PMDiagnosticsResponse(
        ok=not issues,
        can_start=not startup_blockers,
        generated_at=_utc_now(),
        lancedb=lancedb,
        llm=llm,
        workspace=workspace,
        issues=issues,
        startup_blockers=startup_blockers,
    )


def _build_pm_diagnostics_for_request(
    request: Request,
    workspace_override: str | None = None,
) -> PMDiagnosticsResponse:
    """Resolve settings and build PM diagnostics for guarded execution starts."""

    settings = request.app.state.app_state.settings
    return _build_pm_diagnostics(settings, workspace_override=workspace_override)


def _ensure_pm_can_start(diagnostics: PMDiagnosticsResponse) -> None:
    """Raise a structured 409 when PM startup prerequisites are not met."""

    if diagnostics.can_start and not diagnostics.startup_blockers:
        return

    raise StructuredHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        code="PM_START_BLOCKED",
        message="PM startup prerequisites are not satisfied",
        details={
            "startup_blockers": diagnostics.startup_blockers,
            "issues": diagnostics.issues,
            "diagnostics": diagnostics.model_dump(mode="json"),
        },
    )


def _ensure_director_handoff_llm_ready(request: Request) -> None:
    """Ensure Director runtime LLM is ready before PM auto-dispatch is allowed."""

    state = getattr(request.app.state, "app_state", None) or request.app.state
    ensure_required_roles_ready(state, default_roles=["director"], force_first="director")


@router.post(
    "/run_once",
    dependencies=[Depends(require_auth)],
    responses={
        409: {"description": "Process already running"},
        503: {"description": "Service unavailable"},
        500: {"description": "Process error"},
    },
)
async def pm_run_once(
    request: Request,
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Run PM once.

    Raises:
        ProcessAlreadyRunningError: If PM is already running
        ServiceUnavailableError: If backend is not available
        ProcessError: If process fails to start
    """
    _ensure_pm_can_start(_build_pm_diagnostics_for_request(request))
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
    request: Request,
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
    _ensure_pm_can_start(_build_pm_diagnostics_for_request(request))
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
    request: Request,
    resume: bool = False,
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Start PM in loop mode (deprecated — use /v2/pm/start).

    DEPRECATED: This endpoint is deprecated. Use /v2/pm/start instead.
    Will be removed in v2.0.
    """
    warnings.warn(
        "/pm/start_loop is deprecated. Use /v2/pm/start instead. Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    _ensure_pm_can_start(_build_pm_diagnostics_for_request(request))
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
def pm_status(
    request: Request,
    workspace: str = "",
    pm_service: PMService = Depends(get_pm_service),
) -> dict:
    """Get PM process status with the workspace used by desktop evidence."""
    settings = request.app.state.app_state.settings
    status_payload = dict(pm_service.get_status())
    status_payload["workspace"] = _workspace_value(settings, workspace)
    return status_payload


@router.get(
    "/diagnostics",
    response_model=PMDiagnosticsResponse,
    dependencies=[Depends(require_auth)],
)
def pm_diagnostics(request: Request, workspace: str = "") -> PMDiagnosticsResponse:
    """Return side-effect-free PM startup diagnostics for the desktop modal."""

    return _build_pm_diagnostics_for_request(request, workspace_override=workspace)


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

        settings = request.app.state.app_state.settings
        workspace = requested_or_active_workspace(settings, payload.workspace)
        _ensure_pm_can_start(_build_pm_diagnostics_for_request(request, workspace_override=workspace))
        if payload.run_director:
            _ensure_director_handoff_llm_ready(request)

        service = OrchestrationCommandService(settings)

        result = await service.execute_pm_run(
            workspace=workspace,
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
            workspace=workspace,
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

        return _pm_orchestration_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError, OrchestrationError) as e:
        import logging

        logging.getLogger(__name__).error("pm_get_orchestration failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


@router.post("/runs/{run_id}/cancel", response_model=PMOrchestrationResponse, dependencies=[Depends(require_auth)])
async def pm_cancel_orchestration(run_id: str) -> PMOrchestrationResponse:
    """Cancel a PM orchestration run and return the resulting snapshot."""
    try:
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        if _pm_snapshot_status(snapshot).lower() not in _TERMINAL_ORCHESTRATION_STATUSES:
            snapshot = await service.cancel_run(run_id)

        return _pm_orchestration_response(snapshot)

    except HTTPException:
        raise
    except (RuntimeError, ValueError, OrchestrationError) as e:
        import logging

        logging.getLogger(__name__).error("pm_cancel_orchestration failed: run_id=%s: %s", run_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error",
        ) from e


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/llm-events", dependencies=[Depends(require_auth)])
async def get_pm_llm_events(
    run_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """获取 PM 的 LLM 调用事件历史"""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role="pm", limit=limit)

    stats = {
        "total": len(events),
        "call_start": sum(1 for e in events if e.event_type == "llm_call_start"),
        "call_end": sum(1 for e in events if e.event_type == "llm_call_end"),
        "call_error": sum(1 for e in events if e.event_type == "llm_error"),
        "call_retry": sum(1 for e in events if e.event_type == "llm_retry"),
        "validation_pass": sum(1 for e in events if e.event_type == "validation_pass"),
        "validation_fail": sum(1 for e in events if e.event_type == "validation_fail"),
    }

    return {
        "run_id": run_id,
        "task_id": task_id,
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "stats": stats,
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
