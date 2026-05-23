"""Chief Engineer v2 delivery routes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintResultV1,
    generate_task_blueprint,
    get_blueprint_status,
)
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.projection.public.role_contracts import (
    ChiefEngineerBlueprintDetailV1,
    ChiefEngineerBlueprintListV1,
    ChiefEngineerBlueprintSummaryV1,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth
from polaris.delivery.http.workspace import active_workspace_value
from pydantic import BaseModel, Field

router = APIRouter(tags=["chief-engineer"])

_BLUEPRINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ChiefEngineerBlueprintSummary(ChiefEngineerBlueprintSummaryV1):
    """Chief Engineer blueprint summary bound to the shared contract."""


class ChiefEngineerBlueprintListResponse(ChiefEngineerBlueprintListV1):
    """Chief Engineer blueprint list response bound to the shared contract."""


class ChiefEngineerBlueprintDetailResponse(ChiefEngineerBlueprintDetailV1):
    """Chief Engineer blueprint detail response bound to the shared contract."""


class ChiefEngineerGenerateBlueprintRequest(BaseModel):
    """Desktop request for generating a task-level Chief Engineer blueprint."""

    task_id: str
    objective: str
    run_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerTaskBlueprintResultResponse(BaseModel):
    """Chief Engineer command/query result with persisted blueprint context."""

    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    source: str = "runtime/blueprints"
    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    blueprint: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerDiagnosticsWorkspaceStatus(BaseModel):
    """Workspace readiness section for Chief Engineer desktop diagnostics."""

    ok: bool
    status: str
    workspace: str
    exists: bool
    error: str | None = None


class ChiefEngineerDiagnosticsBlueprintStatus(BaseModel):
    """Blueprint store readiness section for Chief Engineer desktop diagnostics."""

    ok: bool
    status: str
    source: str = "runtime/blueprints"
    total: int = 0
    loadable: int = 0
    invalid_payloads: int = 0
    director_handoff_ready: bool = False
    latest_updated_at: str | None = None
    error: str | None = None


class ChiefEngineerDiagnosticsResponse(BaseModel):
    """Side-effect-free Chief Engineer desktop readiness snapshot."""

    ok: bool
    role: str = "chief_engineer"
    generated_at: str
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus
    blueprints: ChiefEngineerDiagnosticsBlueprintStatus
    issues: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_blueprint_id(blueprint_id: str) -> str:
    token = str(blueprint_id or "").strip()
    if not _BLUEPRINT_ID_RE.fullmatch(token):
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_ID",
            message="invalid blueprint id",
        )
    return token


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(item.get("path") or item.get("file") or item.get("name") or item.get("id") or "").strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _blueprint_id_from_payload(payload: dict[str, Any], fallback: str) -> str:
    return (
        str(payload.get("blueprint_id") or payload.get("id") or payload.get("task_id") or fallback).strip() or fallback
    )


def _blueprint_summary(payload: dict[str, Any], fallback_id: str) -> ChiefEngineerBlueprintSummary:
    blueprint_id = _blueprint_id_from_payload(payload, fallback_id)
    return ChiefEngineerBlueprintSummary(
        blueprint_id=blueprint_id,
        title=str(payload.get("title") or payload.get("task_title") or payload.get("subject") or blueprint_id).strip(),
        summary=str(payload.get("summary") or payload.get("goal") or payload.get("description") or "").strip(),
        status=str(payload.get("status")).strip() if payload.get("status") is not None else None,
        target_files=_string_list(
            payload.get("target_files")
            or payload.get("scope_paths")
            or payload.get("files")
            or payload.get("affected_files")
        ),
        updated_at=str(payload.get("updated_at") or payload.get("created_at") or "").strip() or None,
        raw=payload,
    )


def _persistence_for_request(request: Request) -> BlueprintPersistence:
    state = get_state(request)
    workspace = _workspace_value(state.settings)
    if not workspace:
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace is not configured",
        )
    return BlueprintPersistence(workspace)


def _blueprint_payload_for_result(result: TaskBlueprintResultV1) -> dict[str, Any]:
    if not result.blueprint_id:
        return {}
    payload = BlueprintPersistence(result.workspace, ensure_directory=False).load(result.blueprint_id)
    return payload if isinstance(payload, dict) else {}


def _blueprint_result_response(result: TaskBlueprintResultV1) -> ChiefEngineerTaskBlueprintResultResponse:
    return ChiefEngineerTaskBlueprintResultResponse(
        ok=result.ok,
        task_id=result.task_id,
        workspace=result.workspace,
        status=result.status,
        blueprint_id=result.blueprint_id,
        blueprint_path=result.blueprint_path,
        summary=result.summary,
        recommendations=list(result.recommendations),
        risks=list(result.risks),
        blueprint=_blueprint_payload_for_result(result),
    )


def _workspace_value(settings: Any) -> str:
    return active_workspace_value(settings)


def _build_workspace_diagnostics(settings: Any) -> ChiefEngineerDiagnosticsWorkspaceStatus:
    workspace = _workspace_value(settings)
    if not workspace:
        return ChiefEngineerDiagnosticsWorkspaceStatus(
            ok=False,
            status="missing",
            workspace="",
            exists=False,
            error="workspace_not_configured",
        )

    exists = Path(workspace).exists()
    return ChiefEngineerDiagnosticsWorkspaceStatus(
        ok=exists,
        status="ok" if exists else "missing",
        workspace=workspace,
        exists=exists,
        error=None if exists else "workspace_path_missing",
    )


def _build_blueprint_diagnostics(settings: Any) -> ChiefEngineerDiagnosticsBlueprintStatus:
    workspace = _workspace_value(settings)
    if not workspace:
        return ChiefEngineerDiagnosticsBlueprintStatus(
            ok=False,
            status="missing_workspace",
            error="workspace_not_configured",
        )

    try:
        persistence = BlueprintPersistence(workspace, ensure_directory=False)
        blueprint_ids = persistence.list_all()
        loadable = 0
        invalid_payloads = 0
        updated_tokens: list[str] = []
        for blueprint_id in blueprint_ids:
            payload = persistence.load(blueprint_id)
            if isinstance(payload, dict):
                loadable += 1
                updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
                if updated_at:
                    updated_tokens.append(updated_at)
            else:
                invalid_payloads += 1
    except (OSError, RuntimeError, ValueError) as exc:
        return ChiefEngineerDiagnosticsBlueprintStatus(
            ok=False,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    if invalid_payloads:
        status = "degraded"
    elif loadable:
        status = "ready"
    else:
        status = "empty"

    return ChiefEngineerDiagnosticsBlueprintStatus(
        ok=invalid_payloads == 0,
        status=status,
        total=len(blueprint_ids),
        loadable=loadable,
        invalid_payloads=invalid_payloads,
        director_handoff_ready=loadable > 0,
        latest_updated_at=max(updated_tokens) if updated_tokens else None,
    )


def _diagnostic_issues(
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus,
    blueprints: ChiefEngineerDiagnosticsBlueprintStatus,
) -> list[str]:
    issues: list[str] = []
    if not workspace.ok:
        issues.append("workspace_unavailable")
    if blueprints.status == "error":
        issues.append("blueprint_store_unreadable")
    if blueprints.invalid_payloads:
        issues.append("blueprint_payload_invalid")
    return issues


def _llm_event_stats(events: list[Any]) -> dict[str, int]:
    return {
        "total": len(events),
        "call_start": sum(1 for event in events if event.event_type == "llm_call_start"),
        "call_end": sum(1 for event in events if event.event_type == "llm_call_end"),
        "call_error": sum(1 for event in events if event.event_type == "llm_error"),
        "call_retry": sum(1 for event in events if event.event_type == "llm_retry"),
        "validation_pass": sum(1 for event in events if event.event_type == "validation_pass"),
        "validation_fail": sum(1 for event in events if event.event_type == "validation_fail"),
    }


@router.get(
    "/chief-engineer/diagnostics",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerDiagnosticsResponse,
)
def get_chief_engineer_diagnostics(request: Request) -> ChiefEngineerDiagnosticsResponse:
    """Return side-effect-free Chief Engineer desktop readiness diagnostics."""

    settings = get_state(request).settings
    workspace = _build_workspace_diagnostics(settings)
    blueprints = _build_blueprint_diagnostics(settings)
    issues = _diagnostic_issues(workspace, blueprints)
    return ChiefEngineerDiagnosticsResponse(
        ok=workspace.ok and blueprints.ok,
        generated_at=_utc_now(),
        workspace=workspace,
        blueprints=blueprints,
        issues=issues,
    )


@router.get("/chief-engineer/llm-events", dependencies=[Depends(require_auth)])
async def get_chief_engineer_llm_events(
    run_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return Chief Engineer LLM event history from the shared roles kernel."""

    emitter = get_global_emitter()
    events = emitter.get_events(
        run_id=run_id,
        task_id=task_id,
        role="chief_engineer",
        limit=limit,
    )
    return {
        "run_id": run_id,
        "task_id": task_id,
        "role": "chief_engineer",
        "events": [event.to_dict() for event in events],
        "count": len(events),
        "stats": _llm_event_stats(events),
    }


@router.get("/chief-engineer/cache-stats", dependencies=[Depends(require_auth)])
async def get_chief_engineer_cache_stats() -> dict[str, Any]:
    """Return shared LLM cache statistics for Chief Engineer desktop evidence."""

    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    return cache.get_stats()


@router.post("/chief-engineer/cache-clear", dependencies=[Depends(require_auth)])
async def clear_chief_engineer_cache() -> dict[str, Any]:
    """Clear the shared LLM cache through the Chief Engineer role surface."""

    from polaris.cells.roles.kernel.public.service import get_global_llm_cache

    cache = get_global_llm_cache()
    cache.clear()
    return {"ok": True, "message": "Cache cleared"}


@router.get("/chief-engineer/token-budget-stats", dependencies=[Depends(require_auth)])
async def get_chief_engineer_token_budget_stats() -> dict[str, Any]:
    """Return shared token-budget statistics for Chief Engineer desktop evidence."""

    budget = get_global_token_budget()
    return budget.get_stats()


@router.get(
    "/chief-engineer/blueprints",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintListResponse,
)
def list_chief_engineer_blueprints(request: Request) -> ChiefEngineerBlueprintListResponse:
    """List persisted Chief Engineer blueprints for the active workspace."""

    persistence = _persistence_for_request(request)
    rows: list[ChiefEngineerBlueprintSummaryV1] = []
    for blueprint_id in persistence.list_all():
        payload = persistence.load(blueprint_id)
        if isinstance(payload, dict):
            rows.append(_blueprint_summary(payload, blueprint_id))

    rows.sort(key=lambda item: item.updated_at or item.blueprint_id, reverse=True)
    return ChiefEngineerBlueprintListResponse(blueprints=rows, total=len(rows))


@router.post(
    "/chief-engineer/blueprints",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerTaskBlueprintResultResponse,
)
def generate_chief_engineer_blueprint(
    request: Request,
    payload: ChiefEngineerGenerateBlueprintRequest,
) -> ChiefEngineerTaskBlueprintResultResponse:
    """Generate and persist a Chief Engineer blueprint through the cell command contract."""

    workspace = _workspace_value(get_state(request).settings)
    try:
        command = GenerateTaskBlueprintCommandV1(
            task_id=payload.task_id,
            workspace=workspace,
            objective=payload.objective,
            run_id=payload.run_id,
            constraints=payload.constraints,
            context=payload.context,
        )
        result = generate_task_blueprint(command)
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_COMMAND",
            message=str(exc),
        ) from exc
    return _blueprint_result_response(result)


@router.get(
    "/chief-engineer/blueprints/status",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerTaskBlueprintResultResponse,
)
def get_chief_engineer_blueprint_status(
    request: Request,
    task_id: str,
    run_id: str | None = None,
) -> ChiefEngineerTaskBlueprintResultResponse:
    """Return Chief Engineer blueprint status for a task through the cell query contract."""

    workspace = _workspace_value(get_state(request).settings)
    try:
        query = GetBlueprintStatusQueryV1(
            task_id=task_id,
            workspace=workspace,
            run_id=run_id,
        )
        result = get_blueprint_status(query)
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_BLUEPRINT_STATUS_QUERY",
            message=str(exc),
        ) from exc
    return _blueprint_result_response(result)


@router.get(
    "/chief-engineer/blueprints/{blueprint_id}",
    dependencies=[Depends(require_auth)],
    response_model=ChiefEngineerBlueprintDetailResponse,
)
def get_chief_engineer_blueprint(
    request: Request,
    blueprint_id: str,
) -> ChiefEngineerBlueprintDetailResponse:
    """Load one persisted Chief Engineer blueprint by id."""

    safe_blueprint_id = _validate_blueprint_id(blueprint_id)
    payload = _persistence_for_request(request).load(safe_blueprint_id)
    if not isinstance(payload, dict):
        raise StructuredHTTPException(
            status_code=404,
            code="BLUEPRINT_NOT_FOUND",
            message="blueprint not found",
        )
    return ChiefEngineerBlueprintDetailResponse(
        blueprint_id=_blueprint_id_from_payload(payload, safe_blueprint_id),
        blueprint=payload,
    )
