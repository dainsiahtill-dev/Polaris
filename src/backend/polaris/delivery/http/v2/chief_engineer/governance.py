"""Governance CRUD + Director-handoff route handlers for the Chief Engineer v2 router.

Lossless extraction of the six governance domains (Risk Register, Tech-Debt
Ledger, Architecture Decision Log, Tech Radar, Post-Mortem / Incident Review,
stack-policy) plus the Director-handoff decision gate from the former
single-file ``chief_engineer`` module.

These handlers consume ``polaris.cells.chief_engineer.blueprint.public``
command/query contracts directly; they are not test-patch targets, so plain
module-level imports are safe. The shared workspace resolver comes from
``_router``.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from polaris.cells.chief_engineer.blueprint.public import (
    ADRStatus,
    IncidentSeverity,
    ListADRsQueryV1,
    ListPostMortemsQueryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    ListTechRadarQueryV1,
    PostMortemStatus,
    RegisterADRCommandV1,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    RiskSeverity,
    RiskStatus,
    TechDebtSeverity,
    TechDebtStatus,
    TechRadarRing,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
    check_stack_policy,
    evaluate_handoff_decision_for_blueprint,
    list_adrs,
    list_post_mortems,
    list_risks,
    list_tech_debt,
    list_tech_radar,
    register_adr,
    register_post_mortem,
    register_risk,
    register_tech_debt,
    register_tech_radar,
    summarize_adrs,
    summarize_post_mortems,
    summarize_risks,
    summarize_tech_debt,
    summarize_tech_radar,
    update_adr_status,
    update_post_mortem_status,
    update_risk_status,
    update_tech_debt_status,
    update_tech_radar_ring,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    require_auth,
)
from polaris.delivery.http.v2.chief_engineer._router import (
    _governance_workspace,
    _validate_blueprint_id,
    router,
)
from polaris.delivery.http.v2.chief_engineer._schemas import (
    ChiefEngineerRegisterADRRequest,
    ChiefEngineerRegisterPostMortemRequest,
    ChiefEngineerRegisterRiskRequest,
    ChiefEngineerRegisterTechDebtRequest,
    ChiefEngineerRegisterTechRadarRequest,
    ChiefEngineerStackPolicyCheckRequest,
    ChiefEngineerUpdateADRStatusRequest,
    ChiefEngineerUpdatePostMortemStatusRequest,
    ChiefEngineerUpdateRiskStatusRequest,
    ChiefEngineerUpdateTechDebtStatusRequest,
    ChiefEngineerUpdateTechRadarRingRequest,
)


@router.post("/chief-engineer/risks", dependencies=[Depends(require_auth)])
def register_chief_engineer_risk(
    request: Request,
    payload: ChiefEngineerRegisterRiskRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Register a new Risk Register entry for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_risk(
            RegisterRiskCommandV1(
                task_id=payload.task_id,
                title=payload.title,
                severity=RiskSeverity(payload.severity.strip().lower()),
                owner=payload.owner,
                mitigation=payload.mitigation,
                workspace=target_workspace,
                links=tuple(payload.links),
                supersedes=payload.supersedes,
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_RISK_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "risk": record.to_dict()}


@router.get("/chief-engineer/risks", dependencies=[Depends(require_auth)])
def list_chief_engineer_risks(
    request: Request,
    workspace: str = "",
    task_id: str = "",
    severity: str = "",
    status: str = "",
) -> dict[str, Any]:
    """List Risk Register entries for the workspace with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListRisksQueryV1(
            workspace=target_workspace,
            task_id=task_id.strip() or None,
            severity=RiskSeverity(severity.strip().lower()) if severity.strip() else None,
            status=RiskStatus(status.strip().lower()) if status.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_RISK_QUERY",
            message=str(exc),
        ) from exc
    records = list_risks(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "risks": [record.to_dict() for record in records],
        "summary": summarize_risks(target_workspace, task_id=task_id.strip() or None),
    }


@router.post("/chief-engineer/risks/{risk_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_risk_status(
    request: Request,
    risk_id: str,
    payload: ChiefEngineerUpdateRiskStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition a Risk Register entry to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_risk_status(
            UpdateRiskStatusCommandV1(
                workspace=target_workspace,
                risk_id=risk_id,
                status=RiskStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_RISK_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="RISK_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "risk": record.to_dict()}


@router.post("/chief-engineer/tech-debt", dependencies=[Depends(require_auth)])
def register_chief_engineer_tech_debt(
    request: Request,
    payload: ChiefEngineerRegisterTechDebtRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Register a new Tech-Debt Ledger entry for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_tech_debt(
            RegisterTechDebtCommandV1(
                title=payload.title,
                description=payload.description,
                severity=TechDebtSeverity(payload.severity.strip().lower()),
                surface=payload.surface,
                owner=payload.owner,
                workspace=target_workspace,
                evidence=tuple(payload.evidence),
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_DEBT_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "tech_debt": record.to_dict()}


@router.get("/chief-engineer/tech-debt", dependencies=[Depends(require_auth)])
def list_chief_engineer_tech_debt(
    request: Request,
    workspace: str = "",
    severity: str = "",
    surface: str = "",
    status: str = "",
) -> dict[str, Any]:
    """List Tech-Debt Ledger entries for the workspace with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListTechDebtQueryV1(
            workspace=target_workspace,
            severity=TechDebtSeverity(severity.strip().lower()) if severity.strip() else None,
            surface=surface.strip() or None,
            status=TechDebtStatus(status.strip().lower()) if status.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_DEBT_QUERY",
            message=str(exc),
        ) from exc
    records = list_tech_debt(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "tech_debt": [record.to_dict() for record in records],
        "summary": summarize_tech_debt(target_workspace, surface=surface.strip() or None),
    }


@router.post("/chief-engineer/tech-debt/{debt_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_tech_debt_status(
    request: Request,
    debt_id: str,
    payload: ChiefEngineerUpdateTechDebtStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition a Tech-Debt Ledger entry to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_tech_debt_status(
            UpdateTechDebtStatusCommandV1(
                workspace=target_workspace,
                debt_id=debt_id,
                status=TechDebtStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_DEBT_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="TECH_DEBT_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "tech_debt": record.to_dict()}


@router.post("/chief-engineer/adrs", dependencies=[Depends(require_auth)])
def register_chief_engineer_adr(
    request: Request,
    payload: ChiefEngineerRegisterADRRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Record a new Architecture Decision Record for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_adr(
            RegisterADRCommandV1(
                title=payload.title,
                decision=payload.decision,
                owner=payload.owner,
                workspace=target_workspace,
                context=payload.context,
                consequences=payload.consequences,
                alternatives=tuple(payload.alternatives),
                related_task_ids=tuple(payload.related_task_ids),
                supersedes=payload.supersedes,
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_ADR_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "adr": record.to_dict()}


@router.get("/chief-engineer/adrs", dependencies=[Depends(require_auth)])
def list_chief_engineer_adrs(
    request: Request,
    workspace: str = "",
    status: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """List Architecture Decision Records with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListADRsQueryV1(
            workspace=target_workspace,
            status=ADRStatus(status.strip().lower()) if status.strip() else None,
            task_id=task_id.strip() or None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_ADR_QUERY",
            message=str(exc),
        ) from exc
    records = list_adrs(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "adrs": [record.to_dict() for record in records],
        "summary": summarize_adrs(target_workspace),
    }


@router.post("/chief-engineer/adrs/{adr_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_adr_status(
    request: Request,
    adr_id: str,
    payload: ChiefEngineerUpdateADRStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition an Architecture Decision Record to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_adr_status(
            UpdateADRStatusCommandV1(
                workspace=target_workspace,
                adr_id=adr_id,
                status=ADRStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_ADR_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="ADR_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "adr": record.to_dict()}


@router.get("/chief-engineer/handoff-decision", dependencies=[Depends(require_auth)])
def get_chief_engineer_handoff_decision(
    request: Request,
    blueprint_id: str,
    workspace: str = "",
) -> dict[str, Any]:
    """Return the Director-handoff gate decision for a persisted blueprint.

    The enforcement-consultation surface: PM / Director / desktop call this
    before dispatch to learn whether the blueprint is cleared for handoff.
    A missing blueprint is fail-closed: ``allowed=False``.
    """
    target_workspace = _governance_workspace(request, workspace)
    safe_blueprint_id = _validate_blueprint_id(blueprint_id)
    decision = evaluate_handoff_decision_for_blueprint(target_workspace, safe_blueprint_id)
    if decision is None:
        return {
            "ok": True,
            "workspace": target_workspace,
            "decision": {
                "allowed": False,
                "blueprint_id": safe_blueprint_id,
                "task_id": "",
                "blocker_count": 0,
                "warning_count": 0,
                "open_blocker_risk_count": 0,
                "blockers": [],
                "reason": "blueprint_not_found",
                "evaluated_at": "",
            },
        }
    return {"ok": True, "workspace": target_workspace, "decision": decision.to_dict()}


@router.post("/chief-engineer/tech-radar", dependencies=[Depends(require_auth)])
def register_chief_engineer_tech_radar(
    request: Request,
    payload: ChiefEngineerRegisterTechRadarRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Place a library on a Tech-Radar ring for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_tech_radar(
            RegisterTechRadarCommandV1(
                library=payload.library,
                ring=TechRadarRing(payload.ring.strip().lower()),
                owner=payload.owner,
                workspace=target_workspace,
                rationale=payload.rationale,
                supersedes=payload.supersedes,
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_RADAR_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "entry": record.to_dict()}


@router.get("/chief-engineer/tech-radar", dependencies=[Depends(require_auth)])
def list_chief_engineer_tech_radar(
    request: Request,
    workspace: str = "",
    ring: str = "",
) -> dict[str, Any]:
    """List Tech-Radar entries with an optional ring filter."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListTechRadarQueryV1(
            workspace=target_workspace,
            ring=TechRadarRing(ring.strip().lower()) if ring.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_RADAR_QUERY",
            message=str(exc),
        ) from exc
    records = list_tech_radar(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "entries": [record.to_dict() for record in records],
        "summary": summarize_tech_radar(target_workspace),
    }


@router.post("/chief-engineer/tech-radar/{entry_id}/ring", dependencies=[Depends(require_auth)])
def update_chief_engineer_tech_radar_ring(
    request: Request,
    entry_id: str,
    payload: ChiefEngineerUpdateTechRadarRingRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Move a Tech-Radar entry to a new ring."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_tech_radar_ring(
            UpdateTechRadarRingCommandV1(
                workspace=target_workspace,
                entry_id=entry_id,
                ring=TechRadarRing(payload.ring.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_TECH_RADAR_RING",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="TECH_RADAR_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "entry": record.to_dict()}


@router.post("/chief-engineer/stack-policy/check", dependencies=[Depends(require_auth)])
def check_chief_engineer_stack_policy(
    request: Request,
    payload: ChiefEngineerStackPolicyCheckRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Check a list of libraries against the Tech Radar (hold/deprecated)."""

    target_workspace = _governance_workspace(request, workspace)
    violations = check_stack_policy(target_workspace, list(payload.libraries))
    return {
        "ok": True,
        "workspace": target_workspace,
        "allowed": not violations,
        "violations": [v.to_dict() for v in violations],
    }


@router.post("/chief-engineer/post-mortems", dependencies=[Depends(require_auth)])
def register_chief_engineer_post_mortem(
    request: Request,
    payload: ChiefEngineerRegisterPostMortemRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Record a new post-mortem / incident review for the workspace."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = register_post_mortem(
            RegisterPostMortemCommandV1(
                title=payload.title,
                severity=IncidentSeverity(payload.severity.strip().lower()),
                occurred_at=payload.occurred_at,
                owner=payload.owner,
                workspace=target_workspace,
                summary=payload.summary,
                root_cause=payload.root_cause,
                impact=payload.impact,
                timeline=tuple(payload.timeline),
                action_items=tuple(payload.action_items),
                related_risk_ids=tuple(payload.related_risk_ids),
            )
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_POST_MORTEM_PAYLOAD",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "post_mortem": record.to_dict()}


@router.get("/chief-engineer/post-mortems", dependencies=[Depends(require_auth)])
def list_chief_engineer_post_mortems(
    request: Request,
    workspace: str = "",
    severity: str = "",
    status: str = "",
) -> dict[str, Any]:
    """List post-mortems with optional filters."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        query = ListPostMortemsQueryV1(
            workspace=target_workspace,
            severity=IncidentSeverity(severity.strip().lower()) if severity.strip() else None,
            status=PostMortemStatus(status.strip().lower()) if status.strip() else None,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_POST_MORTEM_QUERY",
            message=str(exc),
        ) from exc
    records = list_post_mortems(query)
    return {
        "ok": True,
        "workspace": target_workspace,
        "total": len(records),
        "post_mortems": [record.to_dict() for record in records],
        "summary": summarize_post_mortems(target_workspace),
    }


@router.post("/chief-engineer/post-mortems/{incident_id}/status", dependencies=[Depends(require_auth)])
def update_chief_engineer_post_mortem_status(
    request: Request,
    incident_id: str,
    payload: ChiefEngineerUpdatePostMortemStatusRequest,
    workspace: str = "",
) -> dict[str, Any]:
    """Transition a post-mortem to a new status."""

    target_workspace = _governance_workspace(request, workspace)
    try:
        record = update_post_mortem_status(
            UpdatePostMortemStatusCommandV1(
                workspace=target_workspace,
                incident_id=incident_id,
                status=PostMortemStatus(payload.status.strip().lower()),
                note=payload.note,
            ),
            actor=payload.actor.strip() or "chief_engineer",
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_POST_MORTEM_STATUS",
            message=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise StructuredHTTPException(
            status_code=404,
            code="POST_MORTEM_NOT_FOUND",
            message=str(exc),
        ) from exc
    return {"ok": True, "workspace": target_workspace, "post_mortem": record.to_dict()}


# ``assess_release_readiness`` is consumed by the release-readiness domain
# module; the package ``__init__`` re-exports it for the lossless surface.


__all__ = [
    "check_chief_engineer_stack_policy",
    "get_chief_engineer_handoff_decision",
    "list_chief_engineer_adrs",
    "list_chief_engineer_post_mortems",
    "list_chief_engineer_risks",
    "list_chief_engineer_tech_debt",
    "list_chief_engineer_tech_radar",
    "register_chief_engineer_adr",
    "register_chief_engineer_post_mortem",
    "register_chief_engineer_risk",
    "register_chief_engineer_tech_debt",
    "register_chief_engineer_tech_radar",
    "update_chief_engineer_adr_status",
    "update_chief_engineer_post_mortem_status",
    "update_chief_engineer_risk_status",
    "update_chief_engineer_tech_debt_status",
    "update_chief_engineer_tech_radar_ring",
]
