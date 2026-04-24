"""Resident engineer API v2."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from polaris.cells.resident.autonomy.public.service import ResidentMode, get_resident_service
from polaris.delivery.http.dependencies import require_auth
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resident", tags=["Resident"])


def _resolve_workspace(request: Request, workspace: str = "") -> str:
    explicit = str(workspace or "").strip()
    if explicit:
        return explicit
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        app_state = getattr(request.app.state, "app_state", None)
        settings = getattr(app_state, "settings", None)
    configured = str(getattr(settings, "workspace", "") or "").strip()
    return configured or "."


def _resolve_settings(request: Request) -> Any:
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        return settings
    app_state = getattr(request.app.state, "app_state", None)
    return getattr(app_state, "settings", None)


class ResidentWorkspaceRequest(BaseModel):
    workspace: str = Field(default="", description="Optional workspace override")


class ResidentStartRequest(ResidentWorkspaceRequest):
    mode: str = Field(default=ResidentMode.OBSERVE.value, description="Resident mode")


class ResidentIdentityPatch(BaseModel):
    workspace: str = Field(default="", description="Optional workspace override")
    name: str | None = None
    mission: str | None = None
    owner: str | None = None
    operating_mode: str | None = None
    values: list[str] | None = None
    memory_lineage: list[str] | None = None
    capability_profile: dict[str, float] | None = None


class DecisionOptionPayload(BaseModel):
    option_id: str = ""
    label: str = ""
    rationale: str = ""
    strategy_tags: list[str] = Field(default_factory=list)
    estimated_score: float = 0.0


class DecisionRecordPayload(BaseModel):
    workspace: str = ""
    timestamp: str = ""
    run_id: str = ""
    actor: str
    stage: str
    goal_id: str = ""
    task_id: str = ""
    summary: str = ""
    context_refs: list[str] = Field(default_factory=list)
    options: list[DecisionOptionPayload] = Field(default_factory=list)
    selected_option_id: str = ""
    strategy_tags: list[str] = Field(default_factory=list)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    actual_outcome: dict[str, Any] = Field(default_factory=dict)
    verdict: str = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class GoalProposalPayload(BaseModel):
    workspace: str = ""
    goal_type: str = "maintenance"
    title: str
    motivation: str = ""
    source: str = "manual"
    expected_value: float = 0.6
    risk_score: float = 0.2
    scope: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)


class GoalNotePayload(ResidentWorkspaceRequest):
    note: str = ""


class GoalStageRequest(ResidentWorkspaceRequest):
    promote_to_pm_runtime: bool = False


class GoalRunRequest(ResidentWorkspaceRequest):
    run_type: str = Field(default="pm", description="PM orchestration run type")
    run_director: bool = False
    director_iterations: int = Field(default=1, ge=1, le=10)


@router.get("/status", dependencies=[Depends(require_auth)])
def resident_status(request: Request, details: bool = False, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    return service.get_status(include_details=details)


@router.post("/start", dependencies=[Depends(require_auth)])
def resident_start(request: Request, payload: ResidentStartRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    return service.start(payload.mode)


@router.post("/stop", dependencies=[Depends(require_auth)])
def resident_stop(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    return service.stop()


@router.post("/tick", dependencies=[Depends(require_auth)])
def resident_tick(
    request: Request,
    payload: ResidentWorkspaceRequest,
    force: bool = False,
) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    return service.tick(force=force)


@router.get("/identity", dependencies=[Depends(require_auth)])
def resident_identity(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    return service.get_status(include_details=False)["identity"]


@router.patch("/identity", dependencies=[Depends(require_auth)])
def resident_patch_identity(request: Request, payload: ResidentIdentityPatch) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    return service.update_identity(payload.model_dump(exclude_none=True))


@router.get("/agenda", dependencies=[Depends(require_auth)])
def resident_agenda(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    return service.get_status(include_details=False)["agenda"]


@router.get("/goals", dependencies=[Depends(require_auth)])
def resident_goals(request: Request, workspace: str = "", status_filter: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    goals = [item.to_dict() for item in service.list_goals(status=status_filter)]
    return {"items": goals, "count": len(goals)}


@router.post("/goals", dependencies=[Depends(require_auth)])
def resident_create_goal(request: Request, payload: GoalProposalPayload) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    return service.create_goal_proposal(payload.model_dump()).to_dict()


@router.post("/goals/{goal_id}/approve", dependencies=[Depends(require_auth)])
def resident_approve_goal(request: Request, goal_id: str, payload: GoalNotePayload) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    goal = service.approve_goal(goal_id, note=payload.note)
    if goal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return goal.to_dict()


@router.post("/goals/{goal_id}/reject", dependencies=[Depends(require_auth)])
def resident_reject_goal(request: Request, goal_id: str, payload: GoalNotePayload) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    goal = service.reject_goal(goal_id, note=payload.note)
    if goal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return goal.to_dict()


@router.post("/goals/{goal_id}/materialize", dependencies=[Depends(require_auth)])
def resident_materialize_goal(request: Request, goal_id: str, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    try:
        contract = service.materialize_goal(goal_id)
    except ValueError as exc:
        logger.error("resident_materialize_goal failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="internal error") from exc
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return contract


@router.post("/goals/{goal_id}/stage", dependencies=[Depends(require_auth)])
def resident_stage_goal(request: Request, goal_id: str, payload: GoalStageRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    settings = _resolve_settings(request)
    try:
        staged = service.stage_goal(
            goal_id,
            promote_to_pm_runtime=payload.promote_to_pm_runtime,
            ramdisk_root=str(getattr(settings, "ramdisk_root", "") or ""),
        )
    except ValueError as exc:
        logger.error("resident_stage_goal failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="internal error") from exc
    if staged is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return staged


@router.post("/goals/{goal_id}/run", dependencies=[Depends(require_auth)])
async def resident_run_goal(request: Request, goal_id: str, payload: GoalRunRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    try:
        result = await service.run_goal(
            goal_id,
            settings=_resolve_settings(request),
            run_type=payload.run_type,
            run_director=payload.run_director,
            director_iterations=payload.director_iterations,
        )
    except ValueError as exc:
        logger.error("resident_run_goal failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="internal error") from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return result


@router.get("/goals/{goal_id}/execution", dependencies=[Depends(require_auth)])
def resident_goal_execution(
    request: Request,
    goal_id: str,
    workspace: str = "",
) -> dict[str, Any]:
    """Get GoalExecutionView for a specific goal.

    Phase 1.2: Goal Execution Projection - retrieve execution view.
    """
    service = get_resident_service(_resolve_workspace(request, workspace))
    result = service.get_goal_execution_view(goal_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return result


@router.get("/goals/execution/bulk", dependencies=[Depends(require_auth)])
def resident_goals_execution_bulk(
    request: Request,
    workspace: str = "",
) -> dict[str, Any]:
    """Get GoalExecutionView for all active goals.

    Phase 1.2: Goal Execution Projection - bulk retrieve.
    """
    service = get_resident_service(_resolve_workspace(request, workspace))
    executions = service.list_goal_executions()
    return {"items": executions, "count": len(executions)}


@router.get("/decisions", dependencies=[Depends(require_auth)])
def resident_decisions(
    request: Request,
    workspace: str = "",
    limit: int = 100,
    actor: str = "",
    verdict: str = "",
) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    decisions = [item.to_dict() for item in service.list_decisions(limit=limit, actor=actor, verdict=verdict)]
    return {"items": decisions, "count": len(decisions)}


@router.get("/decisions/{decision_id}/evidence", dependencies=[Depends(require_auth)])
def resident_decision_evidence(
    request: Request,
    decision_id: str,
    workspace: str = "",
) -> dict[str, Any]:
    """Get EvidenceBundle for a specific decision.

    Phase 1.1: Decision traceability - retrieve EvidenceBundle.
    """
    service = get_resident_service(_resolve_workspace(request, workspace))
    result = service.get_decision_evidence_bundle(decision_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision or evidence bundle not found")
    return result


@router.post("/decisions", dependencies=[Depends(require_auth)])
def resident_record_decision(request: Request, payload: DecisionRecordPayload) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    return service.record_decision(payload.model_dump()).to_dict()


@router.get("/skills", dependencies=[Depends(require_auth)])
def resident_skills(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    skills = [item.to_dict() for item in service.list_skills()]
    return {"items": skills, "count": len(skills)}


@router.post("/skills/extract", dependencies=[Depends(require_auth)])
def resident_extract_skills(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    skills = [item.to_dict() for item in service.run_skill_foundry()]
    return {"items": skills, "count": len(skills)}


@router.get("/experiments", dependencies=[Depends(require_auth)])
def resident_experiments(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    experiments = service.list_experiments()
    return {"items": experiments, "count": len(experiments)}


@router.post("/experiments/run", dependencies=[Depends(require_auth)])
def resident_run_experiments(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    experiments = service.run_counterfactual_lab()
    return {"items": experiments, "count": len(experiments)}


@router.get("/improvements", dependencies=[Depends(require_auth)])
def resident_improvements(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    improvements = service.list_improvements()
    return {"items": improvements, "count": len(improvements)}


@router.post("/improvements/run", dependencies=[Depends(require_auth)])
def resident_run_improvements(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, payload.workspace))
    improvements = service.run_self_improvement_lab()
    return {"items": improvements, "count": len(improvements)}


__all__ = ["router"]
