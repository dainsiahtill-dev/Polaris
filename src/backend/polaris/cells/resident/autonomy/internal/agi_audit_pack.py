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
            "runtime.v2.snapshot.resident",
            "roles.registry",
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
        "boundary_summary": resident_agi_boundary_summary(capability_surface),
        "authority_matrix": capability_surface.get("authority_matrix")
        if isinstance(capability_surface.get("authority_matrix"), dict)
        else {},
        "recent_decisions": recent_decisions,
        "evidence_refs": evidence_refs,
        "run_ledger_summary": run_ledger_summary,
        "evidence_gate": resident_agi_evidence_gate(
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
    audit_pack["hard_rule_gate"] = resident_agi_hard_rule_gate(audit_pack)
    return audit_pack


__all__ = [
    "build_resident_agi_audit_pack",
    "resident_agi_audit_refs",
    "resident_agi_boundary_summary",
    "resident_agi_evidence_gate",
    "resident_agi_hard_rule_gate",
    "resident_agi_role_registry_payload",
    "resident_agi_run_ledger_summary",
]
