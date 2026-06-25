"""Resident AGI audit-pack construction.

This module keeps Resident AGI supervision evidence inside the
``resident.autonomy`` Cell instead of letting HTTP handlers assemble their own
control-plane facts.
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.llm.dialogue.public import get_registered_roles
from polaris.cells.resident.autonomy.internal.agi_capability_surface import (
    resident_agi_capability_surface_payload,
)
from polaris.cells.resident.autonomy.internal.autonomy_boundary import (
    resident_tick_autonomy_boundary,
)
from polaris.cells.roles.adapters.public.service import get_supported_roles

logger = logging.getLogger(__name__)


def resident_agi_role_registry_payload() -> dict[str, Any]:
    """Return the shared role-registry evidence required by Resident AGI."""

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


def resident_agi_boundary_summary(capability_surface: dict[str, Any]) -> dict[str, Any]:
    """Summarize Resident AGI decision boundaries for hard-rule checks."""

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


def resident_agi_audit_refs(
    *,
    decisions: list[dict[str, Any]],
    capability_surface: dict[str, Any],
) -> list[str]:
    """Collect evidence references visible to a Resident AGI decision turn."""

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


def resident_agi_director_repair_contract_payload(capability_surface: dict[str, Any]) -> dict[str, Any]:
    """Return the AGI-visible boundary for Director hard-coded repairs."""

    catalog_raw = capability_surface.get("hardcoded_repair_strategy_catalog")
    catalog = catalog_raw if isinstance(catalog_raw, dict) else {}
    summary_raw = catalog.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    return {
        "schema_version": "resident.agi_director_repair_contract.v1",
        "owner_cell": catalog.get("owner_cell") or "director.runtime",
        "source": catalog.get("source") or "director.runtime.repair_kernel.strategy_catalog",
        "catalog_schema": catalog.get("schema_version") or "director.deterministic_repair_strategy_catalog.v1",
        "profile_summary_schema": "director.deterministic_repair_profile_summary.v1",
        "unknown_source_tool_policy": catalog.get("unknown_source_tool_policy") or "fail_closed_high_risk",
        "execution_boundary": catalog.get("execution_boundary") or "director_authorized_tools_only",
        "chain": catalog.get("chain") or "PM → Chief Engineer → Director",
        "agi_advisory": {"active": False, "authoritative": False, "writes_allowed": False},
        "agi_execution_authority": bool(catalog.get("agi_execution_authority")),
        "director_tool_execution_required": bool(catalog.get("director_tool_execution_required", True)),
        "strategy_count": int(summary.get("total") or 0),
        "summary": summary,
    }


def resident_agi_run_ledger_summary(workspace: str, *, run_id: str = "", max_runs: int = 20) -> dict[str, Any]:
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


def resident_agi_evidence_gate(
    *,
    audit_refs: list[str],
    run_ledger_summary: dict[str, Any],
) -> dict[str, Any]:
    """Recommend whether AGI should continue, block, or request evidence."""

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


def resident_agi_hard_rule_gate(audit_pack: dict[str, Any]) -> dict[str, Any]:
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
    authority_matrix_raw = audit_pack.get("authority_matrix")
    authority_matrix: dict[str, Any] = authority_matrix_raw if isinstance(authority_matrix_raw, dict) else {}
    hard_rule_boundaries_raw = authority_matrix.get("platform_hard_rules")
    hard_rule_boundaries = (
        {str(item or "").strip() for item in hard_rule_boundaries_raw if str(item or "").strip()}
        if isinstance(hard_rule_boundaries_raw, list)
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
            "check_id": "authority_matrix.role_runtime_foundation",
            "passed": "role.runtime.foundation" in hard_rule_boundaries,
            "detail": "Resident AGI authority matrix must include the shared role-runtime hard rule.",
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


def _resident_agi_unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def resident_agi_evidence_interface_recommendations(
    *,
    capability_surface: dict[str, Any],
    hard_rule_status: str,
    evidence_status: str,
) -> list[dict[str, Any]]:
    """Return audit/context/verifier interfaces the AGI should consider next."""

    capabilities_raw = capability_surface.get("items")
    capabilities = capabilities_raw if isinstance(capabilities_raw, list) else []
    evidence_categories = {
        "audit_diagnosis",
        "audit_verdict",
        "audit_evidence",
        "context_discovery",
        "director_repair_strategy",
        "llm_audit",
        "run_ledger",
        "verification_policy",
    }
    priority_by_contract = {
        "roles.final_request_context_audit": 10,
        "control_plane.run_ledger": 20,
        "audit.diagnosis": 30,
        "audit.verdict": 40,
        "control_plane.verifier_policy": 50,
        "control_plane.verifier_execution": 60,
        "context.catalog": 70,
        "context.engine": 80,
        "director.deterministic_repair_strategy_catalog.v1": 85,
        "audit.evidence.bundle": 90,
    }
    recommendations: list[dict[str, Any]] = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        category = str(capability.get("category") or "").strip()
        contract_ref = str(capability.get("contract_ref") or "").strip()
        if (
            category not in evidence_categories
            and not contract_ref.startswith(("audit.", "context.", "control_plane.verifier"))
            and contract_ref
            not in {
                "control_plane.run_ledger",
                "roles.final_request_context_audit",
                "director.deterministic_repair_strategy_catalog.v1",
            }
        ):
            continue

        access = str(capability.get("access") or "read_only").strip() or "read_only"
        risk_level = str(capability.get("risk_level") or "low").strip() or "low"
        should_consider = access == "read_only" or evidence_status in {"fail", "hold"} or hard_rule_status == "block"
        reason = "Collect read-only decision evidence."
        if hard_rule_status == "block":
            reason = "Repair platform hard-rule evidence before AGI judgement."
        elif evidence_status == "fail":
            reason = "Investigate failed gate evidence before downstream execution."
        elif evidence_status == "hold":
            reason = "Request missing evidence before continuing."
        elif access != "read_only":
            reason = "Keep governed execution available only when later evidence becomes insufficient."

        evidence_refs_raw = capability.get("evidence_refs")
        evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []
        recommendations.append(
            {
                "capability_id": str(capability.get("capability_id") or "").strip(),
                "name": str(capability.get("name") or "").strip(),
                "category": category,
                "contract_ref": contract_ref,
                "access": access,
                "risk_level": risk_level,
                "priority": priority_by_contract.get(contract_ref, 100),
                "recommended_now": should_consider,
                "reason": reason,
                "evidence_refs": [str(item or "").strip() for item in evidence_refs if str(item or "").strip()],
            }
        )

    return sorted(
        recommendations,
        key=lambda item: (
            not bool(item.get("recommended_now")),
            int(item.get("priority") or 100),
            str(item.get("capability_id") or ""),
        ),
    )


def resident_agi_decision_profile(audit_pack: dict[str, Any]) -> dict[str, Any]:
    """Return the machine-readable execution profile for a Resident AGI turn."""

    hard_rule_gate_raw = audit_pack.get("hard_rule_gate")
    hard_rule_gate: dict[str, Any] = hard_rule_gate_raw if isinstance(hard_rule_gate_raw, dict) else {}
    evidence_gate_raw = audit_pack.get("evidence_gate")
    evidence_gate: dict[str, Any] = evidence_gate_raw if isinstance(evidence_gate_raw, dict) else {}
    authority_matrix_raw = audit_pack.get("authority_matrix")
    authority_matrix: dict[str, Any] = authority_matrix_raw if isinstance(authority_matrix_raw, dict) else {}
    capability_surface_raw = audit_pack.get("capability_surface")
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    decision_capability_registry_raw = capability_surface.get("decision_capability_registry")
    decision_capability_registry: dict[str, Any] = (
        decision_capability_registry_raw if isinstance(decision_capability_registry_raw, dict) else {}
    )
    decision_capabilities_raw = capability_surface.get("decision_capabilities")
    decision_capabilities = decision_capabilities_raw if isinstance(decision_capabilities_raw, list) else []
    autonomy_boundary_raw = audit_pack.get("autonomy_boundary")
    autonomy_boundary: dict[str, Any] = autonomy_boundary_raw if isinstance(autonomy_boundary_raw, dict) else {}

    hard_rule_status = str(hard_rule_gate.get("status") or "unknown").strip().lower()
    evidence_status = str(evidence_gate.get("status") or "unknown").strip().lower()
    hard_rule_passed = hard_rule_status == "pass"
    evidence_recommendation = str(evidence_gate.get("recommended_verdict") or "request_evidence").strip()
    if not hard_rule_passed:
        recommended_verdict = "block"
        recommended_next_action = "repair_platform_hard_rule_evidence"
        downstream_precheck = "blocked_by_platform_hard_rule"
        candidate_actions = ["block", "request_evidence", "escalate"]
    elif evidence_status == "pass" and evidence_recommendation == "continue":
        recommended_verdict = "continue"
        recommended_next_action = "run_resident_agi_judgement"
        downstream_precheck = "ready_for_agi_judgement"
        candidate_actions = ["continue", "block", "request_evidence", "escalate"]
    elif evidence_status == "fail":
        recommended_verdict = "block"
        recommended_next_action = "block_and_repair_failed_gate_evidence"
        downstream_precheck = "hold_for_gate_repair"
        candidate_actions = ["block", "request_evidence", "escalate"]
    else:
        recommended_verdict = evidence_recommendation or "request_evidence"
        recommended_next_action = "request_missing_contextos_or_run_ledger_evidence"
        downstream_precheck = "hold_for_evidence"
        candidate_actions = ["request_evidence", "block", "escalate"]

    boundaries_raw = capability_surface.get("decision_boundaries")
    boundaries = boundaries_raw if isinstance(boundaries_raw, list) else []
    required_evidence: list[Any] = []
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            continue
        evidence_required = boundary.get("evidence_required")
        if isinstance(evidence_required, list):
            required_evidence.extend(evidence_required)

    counts_raw = authority_matrix.get("counts")
    counts: dict[str, Any] = counts_raw if isinstance(counts_raw, dict) else {}
    decision_policy_raw = authority_matrix.get("decision_policy")
    decision_policy: dict[str, Any] = decision_policy_raw if isinstance(decision_policy_raw, dict) else {}
    canonical_contracts_raw = authority_matrix.get("canonical_contracts")
    canonical_contracts = canonical_contracts_raw if isinstance(canonical_contracts_raw, list) else []
    decision_capability_ids = [
        str(item.get("decision_id") or "").strip()
        for item in decision_capabilities
        if isinstance(item, dict) and str(item.get("decision_id") or "").strip()
    ]
    evidence_interface_recommendations = resident_agi_evidence_interface_recommendations(
        capability_surface=capability_surface,
        hard_rule_status=hard_rule_status,
        evidence_status=evidence_status,
    )

    return {
        "schema_version": "resident.agi_decision_profile.v1",
        "role_id": "resident_agi",
        "runtime_foundation": audit_pack.get("runtime_foundation") or "roles.runtime + ContextOS + TurnEngine",
        "role_turn_allowed": hard_rule_passed,
        "downstream_precheck": downstream_precheck,
        "recommended_verdict": recommended_verdict,
        "recommended_next_action": recommended_next_action,
        "candidate_actions": candidate_actions,
        "required_constraints": [
            "resident_agi_role_runtime_required",
            "contextos_expected",
            "turn_engine_expected",
            "preserve_pm_chief_engineer_director_qa_chain",
            "hard_platform_invariants_non_overridable",
            "resident_tick_is_deterministic_evidence_only",
            "execution_impacting_agi_judgement_requires_runtime_contract_gate",
        ],
        "required_evidence": _resident_agi_unique_strings(required_evidence),
        "evidence_interface_recommendations": evidence_interface_recommendations,
        "decision_capability_registry": decision_capability_registry,
        "decision_capability_ids": _resident_agi_unique_strings(decision_capability_ids),
        "contract_refs": _resident_agi_unique_strings(canonical_contracts),
        "authority_policy": {
            "hard_rules": decision_policy.get("hard_rules") or "platform_enforced_non_overridable",
            "evidence_gates": decision_policy.get("evidence_gates") or "agi_judgement_with_fail_closed_recommendation",
            "governed_execution": decision_policy.get("governed_execution") or "canonical_role_chain_only",
            "code_changes": decision_policy.get("code_changes") or "director_authorized_tools_only",
        },
        "platform_permission_counts": {
            "read_only": int(counts.get("read_only_capabilities") or 0),
            "governed_operations": int(counts.get("governed_operation_capabilities") or 0),
            "high_risk": int(counts.get("high_risk_capabilities") or 0),
        },
        "gate_refs": {
            "hard_rule_gate": hard_rule_gate.get("schema_version") or "resident.agi_hard_rule_gate.v1",
            "evidence_gate": evidence_gate.get("schema_version") or "resident.agi_evidence_gate.v1",
            "authority_matrix": authority_matrix.get("schema_version") or "resident.agi_authority_matrix.v1",
            "decision_capability_registry": decision_capability_registry.get("schema_version")
            or "resident.agi_decision_capability_registry.v1",
            "autonomy_boundary": autonomy_boundary.get("schema_version") or "resident.tick_autonomy_boundary.v1",
        },
        "llm_decision_required": True,
        "llm_override_allowed": False,
        "audit_pack_schema": audit_pack.get("schema_version") or "resident.agi_audit_pack.v1",
    }


def build_resident_agi_audit_pack(
    *,
    workspace: str,
    status_payload: dict[str, Any],
    decision_limit: int,
) -> dict[str, Any]:
    """Build the canonical evidence pack for Resident AGI role turns."""

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
    run_ledger_summary = resident_agi_run_ledger_summary(workspace, run_id=run_id)
    autonomy_boundary = resident_tick_autonomy_boundary()
    evidence_refs = resident_agi_audit_refs(
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
            "runtime.v2.status.resident",
            "runtime.v2.snapshot.resident",
            "roles.registry",
            "director.runtime.repair_kernel.strategy_catalog",
            "director.repair_receipts",
        ],
        "role_registry": resident_agi_role_registry_payload(),
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
        "autonomy_boundary": autonomy_boundary,
        "boundary_summary": resident_agi_boundary_summary(capability_surface),
        "authority_matrix": capability_surface.get("authority_matrix")
        if isinstance(capability_surface.get("authority_matrix"), dict)
        else {},
        "director_repair_contract": resident_agi_director_repair_contract_payload(capability_surface),
        "recent_decisions": recent_decisions,
        "evidence_refs": evidence_refs,
        "run_ledger_summary": run_ledger_summary,
        "evidence_gate": resident_agi_evidence_gate(
            audit_refs=evidence_refs,
            run_ledger_summary=run_ledger_summary,
        ),
        "execution_constraints": [
            "AGI decisions must execute as resident_agi role turns.",
            "Resident tick/labs are deterministic evidence producers, not AGI judgement turns.",
            "Execution-impacting AGI decisions must be recorded in resident.decision_trace.",
            "Downstream work must preserve PM → Chief Engineer → Director.",
            "Hard platform invariants cannot be overridden by AGI judgement.",
        ],
        "decision_endpoint": "/v2/resident/agi/decide",
    }
    audit_pack["hard_rule_gate"] = resident_agi_hard_rule_gate(audit_pack)
    audit_pack["decision_profile"] = resident_agi_decision_profile(audit_pack)
    return audit_pack


__all__ = [
    "build_resident_agi_audit_pack",
    "resident_agi_audit_refs",
    "resident_agi_boundary_summary",
    "resident_agi_decision_profile",
    "resident_agi_director_repair_contract_payload",
    "resident_agi_evidence_gate",
    "resident_agi_evidence_interface_recommendations",
    "resident_agi_hard_rule_gate",
    "resident_agi_role_registry_payload",
    "resident_agi_run_ledger_summary",
]
