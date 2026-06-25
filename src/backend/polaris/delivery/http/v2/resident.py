"""Resident engineer API v2."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.llm.dialogue.public import get_registered_roles
from polaris.cells.resident.autonomy.public.service import (
    QueryResidentCapabilitiesV1,
    QueryResidentStatusV1,
    ResidentMode,
    get_resident_service,
    query_resident_capabilities,
    query_resident_status,
    resident_agi_capability_surface_payload,
)
from polaris.cells.roles.adapters.public.service import create_role_adapter, get_supported_roles
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


def _resident_decision_verdict(agi_verdict: str, *, runtime_success: bool) -> str:
    normalized = str(agi_verdict or "").strip().lower()
    if not runtime_success and normalized in {"block", "escalate", "request_evidence"}:
        return "blocked"
    if not runtime_success:
        return "failure"
    if normalized == "continue":
        return "success"
    if normalized in {"block", "escalate", "request_evidence"}:
        return "blocked"
    return "unknown"


def _resident_agi_decision_summary(
    *,
    objective: str,
    agi_verdict: str,
    rationale: str,
    error: str,
) -> str:
    verdict = str(agi_verdict or "").strip() or "unknown"
    detail = str(rationale or error or objective or "").strip()
    if len(detail) > 180:
        detail = f"{detail[:177]}..."
    return f"Resident AGI decision [{verdict}]: {detail}" if detail else f"Resident AGI decision [{verdict}]"


def _resident_agi_role_registry_payload() -> dict[str, Any]:
    dialogue_roles = sorted({str(role).strip() for role in get_registered_roles() if str(role).strip()})
    adapter_roles = sorted({str(role).strip() for role in get_supported_roles() if str(role).strip()})
    required_roles = ("pm", "chief_engineer", "director", "qa", "resident_agi")
    missing_required_roles = [
        role for role in required_roles if role not in dialogue_roles or role not in adapter_roles
    ]
    return {
        "schema_version": "resident.agi_role_registry.v1",
        "source": "llm.dialogue.registry + roles.adapters.registry",
        "dialogue_roles": dialogue_roles,
        "adapter_roles": adapter_roles,
        "required_roles": list(required_roles),
        "missing_required_roles": missing_required_roles,
        "resident_agi_available": "resident_agi" in dialogue_roles and "resident_agi" in adapter_roles,
    }


def _resident_agi_boundary_summary(capability_surface: dict[str, Any]) -> dict[str, Any]:
    boundaries = capability_surface.get("decision_boundaries")
    boundary_items = boundaries if isinstance(boundaries, list) else []
    counts: dict[str, int] = {}
    for item in boundary_items:
        if not isinstance(item, dict):
            continue
        authority = str(item.get("authority") or "unknown").strip() or "unknown"
        counts[authority] = counts.get(authority, 0) + 1
    return {
        "schema": capability_surface.get("decision_boundary_schema") or "resident.agi_decision_boundary.v1",
        "counts_by_authority": counts,
        "boundary_ids": [
            str(item.get("boundary_id") or "").strip()
            for item in boundary_items
            if isinstance(item, dict) and str(item.get("boundary_id") or "").strip()
        ],
    }


def _resident_agi_audit_refs(
    *,
    decisions: list[dict[str, Any]],
    capability_surface: dict[str, Any],
) -> list[str]:
    refs: set[str] = set()
    for decision in decisions:
        for key in ("context_refs", "evidence_refs", "affected_files", "affected_symbols"):
            values = decision.get(key)
            if not isinstance(values, list):
                continue
            refs.update(str(value).strip() for value in values if str(value).strip())
        bundle_id = str(decision.get("evidence_bundle_id") or "").strip()
        if bundle_id:
            refs.add(bundle_id)

    capabilities = capability_surface.get("items")
    if isinstance(capabilities, list):
        for capability in capabilities:
            if not isinstance(capability, dict):
                continue
            evidence_refs = capability.get("evidence_refs")
            if isinstance(evidence_refs, list):
                refs.update(str(value).strip() for value in evidence_refs if str(value).strip())
    return sorted(refs)


def _resident_agi_run_ledger_summary(workspace: str, *, run_id: str = "", max_runs: int = 20) -> dict[str, Any]:
    """Read the platform Run Ledger projection and return a compact summary."""

    try:
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=workspace,
                run_id=run_id,
                max_runs=max_runs,
            )
        ).projection
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning("Resident AGI run ledger summary unavailable: %s", exc)
        return {
            "schema_version": "resident.agi_run_ledger_summary.v1",
            "source": "run_ledger_projection",
            "available": False,
            "ok": False,
            "status": "unavailable",
            "projected": 0,
            "total": 0,
            "failed": 0,
            "missing": 0,
            "detail": str(exc),
            "evidence_policy": {},
            "evidence_modalities": {},
        }

    evidence_policy = projection.get("evidence_policy")
    evidence_modalities = projection.get("evidence_modalities")
    return {
        "schema_version": "resident.agi_run_ledger_summary.v1",
        "source": projection.get("source") or "run_ledger_projection",
        "available": bool(projection.get("available")),
        "ok": bool(projection.get("ok")),
        "status": str(projection.get("status") or ""),
        "projected": int(projection.get("projected") or 0),
        "total": int(projection.get("total") or 0),
        "failed": int(projection.get("failed") or 0),
        "missing": int(projection.get("missing") or 0),
        "detail": str(projection.get("detail") or ""),
        "evidence_policy": evidence_policy if isinstance(evidence_policy, dict) else {},
        "evidence_modalities": evidence_modalities if isinstance(evidence_modalities, dict) else {},
    }


def _resident_agi_evidence_gate(
    *,
    audit_refs: list[str],
    run_ledger_summary: dict[str, Any],
) -> dict[str, Any]:
    context_refs = [item for item in audit_refs if item.startswith("runtime/contexts/")]
    ledger_available = bool(run_ledger_summary.get("available"))
    ledger_ok = bool(run_ledger_summary.get("ok"))
    ledger_failed = int(run_ledger_summary.get("failed") or 0)
    if ledger_failed > 0:
        status = "fail"
        recommended_verdict = "block"
        reason = "Run Ledger projection contains failed gate evidence."
    elif ledger_ok and context_refs:
        status = "pass"
        recommended_verdict = "continue"
        reason = "Run Ledger projection and ContextOS snapshot refs are available."
    elif ledger_available:
        status = "hold"
        recommended_verdict = "request_evidence"
        reason = "Run Ledger projection is available but ContextOS snapshot refs are incomplete."
    else:
        status = "hold"
        recommended_verdict = "request_evidence"
        reason = "Run Ledger projection is not available yet."
    return {
        "schema_version": "resident.agi_evidence_gate.v1",
        "status": status,
        "recommended_verdict": recommended_verdict,
        "reason": reason,
        "run_ledger_available": ledger_available,
        "run_ledger_ok": ledger_ok,
        "context_snapshot_ref_count": len(context_refs),
        "platform_enforced": False,
        "llm_decision_required": True,
    }


def _resident_agi_hard_rule_gate(audit_pack: dict[str, Any]) -> dict[str, Any]:
    """Evaluate non-negotiable platform invariants before AGI judgement."""

    role_registry_raw = audit_pack.get("role_registry")
    role_registry: dict[str, Any] = role_registry_raw if isinstance(role_registry_raw, dict) else {}
    capability_surface_raw = audit_pack.get("capability_surface")
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    capabilities_raw = capability_surface.get("items")
    capabilities: list[Any] = capabilities_raw if isinstance(capabilities_raw, list) else []
    capability_ids = {
        str(item.get("capability_id") or "").strip()
        for item in capabilities
        if isinstance(item, dict) and str(item.get("capability_id") or "").strip()
    }
    boundary_summary_raw = audit_pack.get("boundary_summary")
    boundary_summary: dict[str, Any] = boundary_summary_raw if isinstance(boundary_summary_raw, dict) else {}
    boundary_ids_raw = boundary_summary.get("boundary_ids")
    boundary_ids = (
        {str(item or "").strip() for item in boundary_ids_raw if str(item or "").strip()}
        if isinstance(boundary_ids_raw, list)
        else set()
    )
    constraints_raw = audit_pack.get("execution_constraints")
    constraints = [str(item or "").strip() for item in constraints_raw] if isinstance(constraints_raw, list) else []

    checks: list[dict[str, Any]] = [
        {
            "check_id": "role_registry.resident_agi_available",
            "passed": bool(role_registry.get("resident_agi_available")),
            "detail": "resident_agi must exist in dialogue and adapter registries.",
        },
        {
            "check_id": "capability.resident_agi_decision_turn",
            "passed": "resident.agi_decision_turn.execute" in capability_ids,
            "detail": "Resident AGI decisions must have a canonical role-runtime capability.",
        },
        {
            "check_id": "boundary.role_runtime_foundation",
            "passed": "role.runtime.foundation" in boundary_ids,
            "detail": "Resident AGI must be tied to the shared RoleRuntime/ContextOS/TurnEngine boundary.",
        },
        {
            "check_id": "topology.pm_ce_director_preserved",
            "passed": any("PM → Chief Engineer → Director" in item for item in constraints),
            "detail": "Downstream execution must preserve PM → Chief Engineer → Director.",
        },
        {
            "check_id": "decision_endpoint.canonical",
            "passed": audit_pack.get("decision_endpoint") == "/v2/resident/agi/decide",
            "detail": "Resident AGI decisions must enter through the canonical HTTP contract.",
        },
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_version": "resident.agi_hard_rule_gate.v1",
        "status": "pass" if not failed else "block",
        "checks": checks,
        "failed_check_ids": [str(item["check_id"]) for item in failed],
        "platform_enforced": True,
        "llm_override_allowed": False,
    }


def _resident_agi_audit_pack(
    *,
    workspace: str,
    status_payload: dict[str, Any],
    decision_limit: int,
) -> dict[str, Any]:
    capability_surface = status_payload.get("agi_capability_surface")
    if not isinstance(capability_surface, dict):
        capability_surface = resident_agi_capability_surface_payload()
    decisions_raw = status_payload.get("decisions")
    decisions = [item for item in decisions_raw if isinstance(item, dict)] if isinstance(decisions_raw, list) else []
    recent_decisions = decisions[:decision_limit]
    runtime_raw = status_payload.get("runtime")
    runtime: dict[str, Any] = runtime_raw if isinstance(runtime_raw, dict) else {}
    counts_raw = status_payload.get("counts")
    counts: dict[str, Any] = counts_raw if isinstance(counts_raw, dict) else {}
    run_id = str(status_payload.get("run_id") or "").strip()
    run_ledger_summary = _resident_agi_run_ledger_summary(workspace, run_id=run_id)
    evidence_refs = _resident_agi_audit_refs(
        decisions=recent_decisions,
        capability_surface=capability_surface,
    )
    audit_pack: dict[str, Any] = {
        "schema_version": "resident.agi_audit_pack.v1",
        "workspace": workspace,
        "role_id": "resident_agi",
        "runtime_foundation": capability_surface.get("runtime_foundation") or "roles.runtime + ContextOS + TurnEngine",
        "truth_sources": [
            "resident.status",
            "resident.agi_capability_surface",
            "resident.decision_trace",
            "roles.registry",
        ],
        "role_registry": _resident_agi_role_registry_payload(),
        "runtime_summary": {
            "active": bool(runtime.get("active")),
            "mode": runtime.get("mode") or "",
            "last_tick_at": runtime.get("last_tick_at") or "",
            "tick_count": runtime.get("tick_count") or 0,
            "last_error": runtime.get("last_error") or "",
            "last_summary": runtime.get("last_summary") if isinstance(runtime.get("last_summary"), dict) else {},
        },
        "counts": counts,
        "capability_surface": capability_surface,
        "boundary_summary": _resident_agi_boundary_summary(capability_surface),
        "recent_decisions": recent_decisions,
        "evidence_refs": evidence_refs,
        "run_ledger_summary": run_ledger_summary,
        "evidence_gate": _resident_agi_evidence_gate(
            audit_refs=evidence_refs,
            run_ledger_summary=run_ledger_summary,
        ),
        "execution_constraints": [
            "AGI decisions must execute as resident_agi role turns.",
            "Execution-impacting AGI decisions must be recorded in resident.decision_trace.",
            "Downstream work must preserve PM → Chief Engineer → Director.",
            "Hard platform invariants cannot be overridden by AGI judgement.",
        ],
        "decision_endpoint": "/v2/resident/agi/decide",
    }
    audit_pack["hard_rule_gate"] = _resident_agi_hard_rule_gate(audit_pack)
    return audit_pack


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
    status_payload = get_resident_service(ws).get_status(include_details=True)
    return _resident_agi_audit_pack(
        workspace=ws,
        status_payload=status_payload,
        decision_limit=decision_limit,
    )


@router.post("/agi/decide", dependencies=[Depends(require_auth)])
async def resident_agi_decide(request: Request, payload: ResidentAgiDecisionTurnRequest) -> dict[str, Any]:
    """Run a Resident AGI decision turn through the shared role runtime."""

    ws = _resolve_workspace(request, payload.workspace)
    service = get_resident_service(ws)
    audit_pack: dict[str, Any] | None = None
    if payload.include_audit_pack:
        status_payload = service.get_status(include_details=True)
        audit_pack = _resident_agi_audit_pack(
            workspace=ws,
            status_payload=status_payload,
            decision_limit=payload.audit_pack_decision_limit,
        )
    input_data = payload.model_dump()
    hard_rule_gate_raw = audit_pack.get("hard_rule_gate") if audit_pack is not None else None
    hard_rule_gate: dict[str, Any] = hard_rule_gate_raw if isinstance(hard_rule_gate_raw, dict) else {}
    evidence_gate_raw = audit_pack.get("evidence_gate") if audit_pack is not None else None
    evidence_gate: dict[str, Any] = evidence_gate_raw if isinstance(evidence_gate_raw, dict) else {}
    if audit_pack is not None:
        input_data["resident_agi_audit_pack"] = audit_pack
        evidence = dict(input_data.get("evidence") or {})
        role_registry = audit_pack.get("role_registry")
        resident_agi_available = (
            bool(role_registry.get("resident_agi_available")) if isinstance(role_registry, dict) else False
        )
        evidence.update(
            {
                "resident_agi_audit_pack_schema": audit_pack.get("schema_version"),
                "resident_agi_audit_pack_truth_sources": list(audit_pack.get("truth_sources") or []),
                "resident_agi_available": resident_agi_available,
                "resident_agi_hard_rule_gate_status": hard_rule_gate.get("status", ""),
                "resident_agi_evidence_gate_status": evidence_gate.get("status", ""),
                "resident_agi_evidence_gate_recommended_verdict": evidence_gate.get("recommended_verdict", ""),
            }
        )
        input_data["evidence"] = evidence
    runtime_context = {
        "run_id": payload.run_id,
        "task_id": payload.task_id,
        "goal_id": payload.goal_id,
        "decision_type": payload.decision_type,
        "context_refs": list(payload.context_refs),
        "evidence_refs": list(payload.evidence_refs),
        "resident_agi_audit_pack": audit_pack or {},
        "metadata": {
            "source": "resident_api.agi_decide",
            "resident_agi_role_runtime_required": True,
            "context_os_expected": True,
            "turn_engine_expected": True,
            "resident_agi_audit_pack_injected": audit_pack is not None,
            "resident_agi_audit_pack_schema": (audit_pack or {}).get("schema_version", ""),
            "resident_agi_hard_rule_gate_status": hard_rule_gate.get("status", ""),
            "resident_agi_evidence_gate_status": evidence_gate.get("status", ""),
        },
    }

    role_result: dict[str, Any]
    if hard_rule_gate.get("status") == "block":
        role_result = {
            "success": False,
            "stage": "resident_agi",
            "decision_type": payload.decision_type,
            "error": "Resident AGI hard-rule gate blocked role execution.",
            "decision": {
                "verdict": "block",
                "rationale": "Platform hard-rule gate failed before LLM judgement.",
                "evidence_refs": [],
                "risks": [f"failed hard-rule check: {item}" for item in hard_rule_gate.get("failed_check_ids", [])],
                "next_action": "repair platform evidence before running Resident AGI",
                "downstream_allowed": False,
            },
            "metadata": {"role_runtime_entrypoint": "roles.runtime.execute_role_session"},
        }
    else:
        adapter = create_role_adapter("resident_agi", ws)
        try:
            role_result = await adapter.execute(
                payload.task_id or "resident-agi-decision",
                input_data,
                runtime_context,
            )
        except (RuntimeError, ValueError) as exc:
            logger.error("resident_agi_decide runtime failed: %s", exc)
            role_result = {
                "success": False,
                "stage": "resident_agi",
                "decision_type": payload.decision_type,
                "error": str(exc),
                "decision": {},
                "metadata": {"role_runtime_entrypoint": "roles.runtime.execute_role_session"},
            }

    decision_raw = role_result.get("decision")
    decision: dict[str, Any] = decision_raw if isinstance(decision_raw, dict) else {}
    agi_verdict = str(decision.get("verdict") or "").strip().lower()
    rationale = str(decision.get("rationale") or "").strip()
    next_action = str(decision.get("next_action") or "").strip()
    downstream_allowed = bool(decision.get("downstream_allowed", False))
    risks = decision.get("risks") if isinstance(decision.get("risks"), list) else []
    role_metadata_raw = role_result.get("metadata")
    role_metadata: dict[str, Any] = role_metadata_raw if isinstance(role_metadata_raw, dict) else {}
    error = str(role_result.get("error") or "").strip()
    runtime_success = bool(role_result.get("success"))
    resident_verdict = _resident_decision_verdict(agi_verdict, runtime_success=runtime_success)
    evidence_refs = list(payload.evidence_refs)
    decision_evidence_refs_raw = decision.get("evidence_refs")
    decision_evidence_refs: list[Any] = (
        decision_evidence_refs_raw if isinstance(decision_evidence_refs_raw, list) else []
    )
    for item in decision_evidence_refs:
        token = str(item or "").strip()
        if token:
            evidence_refs.append(token)

    recorded = service.record_decision(
        {
            "workspace": ws,
            "run_id": payload.run_id,
            "actor": "resident_agi",
            "stage": payload.decision_type,
            "goal_id": payload.goal_id,
            "task_id": payload.task_id,
            "summary": _resident_agi_decision_summary(
                objective=payload.objective,
                agi_verdict=agi_verdict,
                rationale=rationale,
                error=error,
            ),
            "context_refs": list(payload.context_refs),
            "options": [
                {
                    "option_id": agi_verdict or resident_verdict,
                    "label": next_action or agi_verdict or resident_verdict,
                    "rationale": rationale or error,
                    "strategy_tags": ["resident_agi_turn", payload.decision_type],
                    "estimated_score": payload.confidence,
                }
            ],
            "selected_option_id": agi_verdict or resident_verdict,
            "strategy_tags": [
                "resident_agi_turn",
                payload.decision_type,
                agi_verdict or resident_verdict,
            ],
            "expected_outcome": {
                "objective": payload.objective,
                "candidate_actions": list(payload.candidate_actions),
                "constraints": list(payload.constraints),
                "resident_agi_audit_pack_required": payload.include_audit_pack,
            },
            "actual_outcome": {
                "decision_source": "resident_agi_role_runtime",
                "role_runtime_entrypoint": role_metadata.get("role_runtime_entrypoint"),
                "resident_agi_audit_pack_injected": audit_pack is not None,
                "resident_agi_audit_pack_schema": (audit_pack or {}).get("schema_version", ""),
                "resident_agi_audit_pack_evidence_ref_count": len((audit_pack or {}).get("evidence_refs") or []),
                "resident_agi_hard_rule_gate": hard_rule_gate,
                "resident_agi_evidence_gate": evidence_gate,
                "agi_verdict": agi_verdict,
                "resident_verdict": resident_verdict,
                "downstream_allowed": downstream_allowed,
                "next_action": next_action,
                "rationale": rationale,
                "risks": risks,
                "runtime_success": runtime_success,
                "error": error,
            },
            "verdict": resident_verdict,
            "evidence_refs": evidence_refs,
            "confidence": payload.confidence,
        }
    ).to_dict()
    return {
        "ok": runtime_success,
        "workspace": ws,
        "decision": decision,
        "recorded_decision": recorded,
        "role_result": role_result,
        "audit_pack": audit_pack,
        "error": error or None,
    }


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
