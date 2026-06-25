"""Resident engineer API v2."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from polaris.cells.resident.autonomy.public.service import (
    ApproveResidentGoalCommandV1,
    CreateResidentGoalCommandV1,
    ExtractResidentSkillsCommandV1,
    MaterializeResidentGoalCommandV1,
    QueryResidentAgiAuditPackV1,
    QueryResidentAgiEvidenceInterfacesV1,
    QueryResidentCapabilitiesV1,
    QueryResidentStatusV1,
    RecordResidentDecisionCommandV1,
    RejectResidentGoalCommandV1,
    ResidentMode,
    RunResidentAgiDecisionTurnCommandV1,
    RunResidentExperimentsCommandV1,
    RunResidentGoalCommandV1,
    RunResidentImprovementsCommandV1,
    RunResidentTickCommandV1,
    StageResidentGoalCommandV1,
    StartResidentCommandV1,
    StopResidentCommandV1,
    UpdateResidentIdentityCommandV1,
    approve_resident_goal,
    create_resident_goal,
    extract_resident_skills,
    get_resident_service,
    materialize_resident_goal,
    query_resident_agi_audit_pack,
    query_resident_agi_evidence_interfaces,
    query_resident_capabilities,
    query_resident_status,
    record_resident_decision_entry,
    reject_resident_goal,
    run_resident_agi_decision_turn,
    run_resident_experiments,
    run_resident_goal,
    run_resident_improvements,
    run_resident_tick,
    stage_resident_goal,
    start_resident,
    stop_resident,
    update_resident_identity,
)
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


class ResidentAgiDecisionTurnRequest(ResidentWorkspaceRequest):
    decision_type: str = Field(default="platform_supervision", description="Resident AGI decision category")
    objective: str = Field(min_length=1, description="Decision objective for the resident_agi role")
    run_id: str = ""
    task_id: str = ""
    goal_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    candidate_actions: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    include_audit_pack: bool = Field(
        default=True,
        description="Inject the canonical Resident AGI audit pack into the role turn",
    )
    audit_pack_decision_limit: int = Field(default=12, ge=1, le=100)


@router.get("/status", dependencies=[Depends(require_auth)])
def resident_status(request: Request, details: bool = False, workspace: str = "") -> dict[str, Any]:
    ws = _resolve_workspace(request, workspace)
    return query_resident_status(QueryResidentStatusV1(workspace=ws or "."), include_details=details)


@router.get("/capabilities", dependencies=[Depends(require_auth)])
def resident_capabilities(request: Request, workspace: str = "") -> dict[str, Any]:
    ws = _resolve_workspace(request, workspace)
    return query_resident_capabilities(QueryResidentCapabilitiesV1(workspace=ws or "."))


@router.get("/agi/audit-pack", dependencies=[Depends(require_auth)])
def resident_agi_audit_pack(
    request: Request,
    workspace: str = "",
    decision_limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Return the read-only evidence pack a Resident AGI turn should inspect."""

    ws = _resolve_workspace(request, workspace)
    return query_resident_agi_audit_pack(QueryResidentAgiAuditPackV1(workspace=ws, decision_limit=decision_limit))


@router.get("/agi/evidence-interfaces", dependencies=[Depends(require_auth)])
def resident_agi_evidence_interfaces(
    request: Request,
    workspace: str = "",
    decision_type: str = "platform_supervision",
    interface_ids: str = "",
    run_id: str = "",
    task_id: str = "",
    decision_limit: int = Query(default=20, ge=1, le=100),
    max_runs: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Return safe evidence-interface readiness for Resident AGI decisions."""

    ws = _resolve_workspace(request, workspace)
    requested_interfaces = tuple(item.strip() for item in str(interface_ids or "").split(",") if item.strip())
    return query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=ws,
            decision_type=decision_type,
            interface_ids=requested_interfaces,
            run_id=run_id,
            task_id=task_id,
            decision_limit=decision_limit,
            max_runs=max_runs,
        )
    )


@router.post("/agi/decide", dependencies=[Depends(require_auth)])
async def resident_agi_decide(request: Request, payload: ResidentAgiDecisionTurnRequest) -> dict[str, Any]:
    """Run a Resident AGI decision turn through the shared role runtime."""

    ws = _resolve_workspace(request, payload.workspace)
    return await run_resident_agi_decision_turn(
        RunResidentAgiDecisionTurnCommandV1(
            workspace=ws,
            objective=payload.objective,
            decision_type=payload.decision_type,
            run_id=payload.run_id,
            task_id=payload.task_id,
            goal_id=payload.goal_id,
            evidence=payload.evidence,
            constraints=tuple(payload.constraints),
            candidate_actions=tuple(payload.candidate_actions),
            context_refs=tuple(payload.context_refs),
            evidence_refs=tuple(payload.evidence_refs),
            confidence=payload.confidence,
            include_audit_pack=payload.include_audit_pack,
            audit_pack_decision_limit=payload.audit_pack_decision_limit,
        )
    )


@router.post("/start", dependencies=[Depends(require_auth)])
def resident_start(request: Request, payload: ResidentStartRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    return start_resident(StartResidentCommandV1(workspace=ws, mode=payload.mode))


@router.post("/stop", dependencies=[Depends(require_auth)])
def resident_stop(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    return stop_resident(StopResidentCommandV1(workspace=ws))


@router.post("/tick", dependencies=[Depends(require_auth)])
def resident_tick(
    request: Request,
    payload: ResidentWorkspaceRequest,
    force: bool = False,
) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    return run_resident_tick(RunResidentTickCommandV1(workspace=ws, force=force))


@router.get("/identity", dependencies=[Depends(require_auth)])
def resident_identity(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    return service.get_status(include_details=False)["identity"]


@router.patch("/identity", dependencies=[Depends(require_auth)])
def resident_patch_identity(request: Request, payload: ResidentIdentityPatch) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    return update_resident_identity(
        UpdateResidentIdentityCommandV1(
            workspace=ws,
            payload=payload.model_dump(exclude_none=True),
        )
    )


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
    ws = _resolve_workspace(request, payload.workspace)
    return create_resident_goal(CreateResidentGoalCommandV1(workspace=ws, payload=payload.model_dump()))


@router.post("/goals/{goal_id}/approve", dependencies=[Depends(require_auth)])
def resident_approve_goal(request: Request, goal_id: str, payload: GoalNotePayload) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    result = approve_resident_goal(ApproveResidentGoalCommandV1(workspace=ws, goal_id=goal_id, note=payload.note))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return result


@router.post("/goals/{goal_id}/reject", dependencies=[Depends(require_auth)])
def resident_reject_goal(request: Request, goal_id: str, payload: GoalNotePayload) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    result = reject_resident_goal(RejectResidentGoalCommandV1(workspace=ws, goal_id=goal_id, note=payload.note))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return result


@router.post("/goals/{goal_id}/materialize", dependencies=[Depends(require_auth)])
def resident_materialize_goal(request: Request, goal_id: str, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    try:
        contract = materialize_resident_goal(MaterializeResidentGoalCommandV1(workspace=ws, goal_id=goal_id))
    except ValueError as exc:
        logger.error("resident_materialize_goal failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="internal error") from exc
    if contract is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return contract


@router.post("/goals/{goal_id}/stage", dependencies=[Depends(require_auth)])
def resident_stage_goal(request: Request, goal_id: str, payload: GoalStageRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    settings = _resolve_settings(request)
    try:
        staged = stage_resident_goal(
            StageResidentGoalCommandV1(
                workspace=ws,
                goal_id=goal_id,
                ramdisk_root=str(getattr(settings, "ramdisk_root", "") or ""),
                promote_to_pm_runtime=payload.promote_to_pm_runtime,
            )
        )
    except ValueError as exc:
        logger.error("resident_stage_goal failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="internal error") from exc
    if staged is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal not found")
    return staged


@router.post("/goals/{goal_id}/run", dependencies=[Depends(require_auth)])
async def resident_run_goal(request: Request, goal_id: str, payload: GoalRunRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    try:
        result = await run_resident_goal(
            RunResidentGoalCommandV1(
                workspace=ws,
                goal_id=goal_id,
                settings=_resolve_settings(request),
                run_type=payload.run_type,
                run_director=payload.run_director,
                director_iterations=payload.director_iterations,
            )
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
    ws = _resolve_workspace(request, payload.workspace)
    return record_resident_decision_entry(RecordResidentDecisionCommandV1(workspace=ws, payload=payload.model_dump()))


@router.get("/skills", dependencies=[Depends(require_auth)])
def resident_skills(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    skills = [item.to_dict() for item in service.list_skills()]
    return {"items": skills, "count": len(skills)}


@router.post("/skills/extract", dependencies=[Depends(require_auth)])
def resident_extract_skills(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    skills = extract_resident_skills(ExtractResidentSkillsCommandV1(workspace=ws))
    return {"items": skills, "count": len(skills)}


@router.get("/experiments", dependencies=[Depends(require_auth)])
def resident_experiments(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    experiments = service.list_experiments()
    return {"items": experiments, "count": len(experiments)}


@router.post("/experiments/run", dependencies=[Depends(require_auth)])
def resident_run_experiments(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    experiments = run_resident_experiments(RunResidentExperimentsCommandV1(workspace=ws))
    return {"items": experiments, "count": len(experiments)}


@router.get("/improvements", dependencies=[Depends(require_auth)])
def resident_improvements(request: Request, workspace: str = "") -> dict[str, Any]:
    service = get_resident_service(_resolve_workspace(request, workspace))
    improvements = service.list_improvements()
    return {"items": improvements, "count": len(improvements)}


@router.post("/improvements/run", dependencies=[Depends(require_auth)])
def resident_run_improvements(request: Request, payload: ResidentWorkspaceRequest) -> dict[str, Any]:
    ws = _resolve_workspace(request, payload.workspace)
    improvements = run_resident_improvements(RunResidentImprovementsCommandV1(workspace=ws))
    return {"items": improvements, "count": len(improvements)}


__all__ = ["router"]
