"""Resident AGI handoffs and decision-turn execution."""

from __future__ import annotations

from typing import Any

from polaris.cells.resident.autonomy.internal.agi_tactical_actions import resident_agi_tactical_action_catalog
from polaris.cells.resident.autonomy.internal.resident_runtime_service import get_resident_service
from polaris.cells.resident.autonomy.public import service as _service_pkg
from polaris.cells.resident.autonomy.public.contracts import (
    QueryResidentAgiAuditPackV1,
    QueryResidentAgiEvidenceInterfacesV1,
    QueryResidentAgiHandoffsV1,
    RecordResidentDecisionCommandV1,
    ResidentAgiDecisionHandoffV1,
    RunResidentAgiDecisionTurnCommandV1,
)
from polaris.domain.models.resident import DecisionRecord

from ._agi_gates import (
    _append_resident_agi_control_plane_gate,
    _resident_agi_decision_summary,
    _resident_agi_output_contract_gate,
    _resident_agi_runtime_contract_gate,
    _resident_agi_select_decision_capability,
    _resident_decision_verdict,
)
from ._agi_interfaces import _resident_agi_audit_pack_with_current_refs, query_resident_agi_evidence_interfaces
from ._agi_participation import (
    _resident_agi_decision_turn_participation,
    _resident_agi_repair_advisory_overlay_from_decision,
    query_resident_agi_audit_pack,
)
from ._helpers import _merge_non_empty_strings, logger
from ._lifecycle import record_resident_decision_entry

_RESIDENT_AGI_PLATFORM_CONTRACT_REF_KEYS = (
    "execution_profile",
    "execution_envelope",
    "final_provider_request_audit",
    "run_provenance_bundle",
    "run_ledger_projection",
    "capability_ledger",
)
_RESIDENT_AGI_REQUIRED_PLATFORM_CONTRACT_REFS = (
    "execution_profile",
    "execution_envelope",
    "final_provider_request_audit",
    "run_provenance_bundle",
)
_RESIDENT_AGI_AUTHORITY_FIELD_BLOCKLIST = (
    "authoritative",
    "agi_execution_authority",
    "repair_plan",
    "policy_override",
    "success_verdict",
    "capability_token",
    "execution_envelope_override",
)


def _resident_agi_platform_contract_refs(record: DecisionRecord, handoff: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    raw_refs = handoff.get("platform_contract_refs")
    if isinstance(raw_refs, dict):
        for key in _RESIDENT_AGI_PLATFORM_CONTRACT_REF_KEYS:
            value = str(raw_refs.get(key) or "").strip()
            if value:
                refs[key] = value
    direct_ref_keys = {
        "execution_profile_ref": "execution_profile",
        "execution_envelope_ref": "execution_envelope",
        "final_provider_request_audit_ref": "final_provider_request_audit",
        "run_provenance_bundle_ref": "run_provenance_bundle",
        "run_ledger_projection_ref": "run_ledger_projection",
        "capability_ledger_ref": "capability_ledger",
    }
    for raw_key, normalized_key in direct_ref_keys.items():
        value = str(handoff.get(raw_key) or "").strip()
        if value:
            refs.setdefault(normalized_key, value)
    candidate_refs: list[str] = []
    candidate_refs.extend(str(item or "").strip() for item in record.evidence_refs if str(item or "").strip())
    candidate_refs.extend(str(item or "").strip() for item in record.context_refs if str(item or "").strip())
    for raw_key in ("evidence_refs", "context_refs"):
        raw_items = handoff.get(raw_key)
        if isinstance(raw_items, list):
            candidate_refs.extend(str(item or "").strip() for item in raw_items if str(item or "").strip())
    for ref in candidate_refs:
        lower = ref.lower()
        if "execution_profile" in lower or "task.execution_profile" in lower:
            refs.setdefault("execution_profile", ref)
        if "execution_envelope" in lower or "execution-envelope" in lower:
            refs.setdefault("execution_envelope", ref)
        if "final_provider_request" in lower or "provider-request" in lower or "runtime/contexts" in lower:
            refs.setdefault("final_provider_request_audit", ref)
        if "provenance" in lower:
            refs.setdefault("run_provenance_bundle", ref)
        if "run_ledger" in lower or "run-ledger" in lower or "ledger" in lower:
            refs.setdefault("run_ledger_projection", ref)
        if "capability_ledger" in lower or ("capability" in lower and "ledger" in lower):
            refs.setdefault("capability_ledger", ref)
    return {key: refs[key] for key in _RESIDENT_AGI_PLATFORM_CONTRACT_REF_KEYS if key in refs}


def _resident_agi_sanitize_handoff(record: DecisionRecord, handoff: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(handoff)
    blocked_fields: list[str] = []
    for key in _RESIDENT_AGI_AUTHORITY_FIELD_BLOCKLIST:
        if key in sanitized:
            if key not in {"authoritative", "agi_execution_authority"} or bool(sanitized.get(key)):
                blocked_fields.append(key)
            sanitized.pop(key, None)
    platform_refs = _resident_agi_platform_contract_refs(record, sanitized)
    existing_blocked = sanitized.get("blocked_authority_fields")
    if isinstance(existing_blocked, list):
        blocked_fields.extend(str(item) for item in existing_blocked if str(item).strip())
    sanitized["platform_contract_refs"] = platform_refs
    sanitized["missing_platform_contract_refs"] = [
        ref_key for ref_key in _RESIDENT_AGI_REQUIRED_PLATFORM_CONTRACT_REFS if ref_key not in platform_refs
    ]
    sanitized["blocked_authority_fields"] = sorted(set(blocked_fields))
    sanitized["advisory_only"] = True
    sanitized["authoritative"] = False
    sanitized["agi_execution_authority"] = False
    sanitized["required_chain"] = "PM → Chief Engineer → Director"
    return sanitized


def _resident_agi_handoff_row(record: DecisionRecord, handoff: dict[str, Any]) -> dict[str, Any]:
    safe_handoff = _resident_agi_sanitize_handoff(record, handoff)
    return {
        "schema_version": "resident.agi_handoff_inbox_item.v1",
        "workspace": record.workspace,
        "decision_id": record.decision_id,
        "timestamp": record.timestamp,
        "run_id": record.run_id,
        "task_id": record.task_id,
        "goal_id": record.goal_id,
        "actor": record.actor,
        "stage": record.stage,
        "summary": record.summary,
        "verdict": record.verdict.value,
        "evidence_refs": list(record.evidence_refs),
        "context_refs": list(record.context_refs),
        "handoff": safe_handoff,
    }


def query_resident_agi_handoffs(query: QueryResidentAgiHandoffsV1) -> dict[str, Any]:
    """Return Resident AGI handoff inbox items derived from decision_trace."""
    target_role = str(query.target_role or "").strip().lower()
    status_filter = str(query.handoff_status or "").strip().lower()
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    target_role_counts: dict[str, int] = {}
    for record in get_resident_service(query.workspace).list_decisions(limit=query.limit, actor="resident_agi"):
        outcome = record.actual_outcome if isinstance(record.actual_outcome, dict) else {}
        handoff_raw = outcome.get("resident_agi_decision_handoff")
        if not isinstance(handoff_raw, dict):
            continue
        handoff = _resident_agi_sanitize_handoff(record, dict(handoff_raw))
        handoff_status = str(handoff.get("handoff_status") or "unknown").strip().lower() or "unknown"
        target_roles = [str(item or "").strip() for item in handoff.get("target_roles", []) if str(item or "").strip()]
        normalized_targets = {item.lower() for item in target_roles}
        if target_role and target_role not in normalized_targets:
            continue
        if status_filter and status_filter != handoff_status:
            continue
        status_counts[handoff_status] = status_counts.get(handoff_status, 0) + 1
        for role in target_roles:
            role_key = role.lower()
            target_role_counts[role_key] = target_role_counts.get(role_key, 0) + 1
        rows.append(_resident_agi_handoff_row(record, handoff))
    return {
        "schema_version": "resident.agi_handoff_inbox.v1",
        "workspace": query.workspace,
        "source": "resident.decision_trace",
        "role_id": "resident_agi",
        "target_role": query.target_role,
        "handoff_status": query.handoff_status,
        "items": rows,
        "count": len(rows),
        "summary": {
            "total": len(rows),
            "by_status": dict(sorted(status_counts.items())),
            "by_target_role": dict(sorted(target_role_counts.items())),
            "advisory_only": True,
            "agi_execution_authority": False,
            "required_chain": "PM → Chief Engineer → Director",
        },
    }


def _resident_agi_required_interface_statuses(evidence_interfaces: dict[str, Any]) -> list[dict[str, Any]]:
    required_raw = evidence_interfaces.get("required_evidence_interfaces")
    required_ids = [str(item or "").strip() for item in required_raw] if isinstance(required_raw, list) else []
    interface_rows_raw = evidence_interfaces.get("interfaces")
    interface_rows = interface_rows_raw if isinstance(interface_rows_raw, list) else []
    by_id = {
        str(item.get("interface_id") or "").strip(): item
        for item in interface_rows
        if isinstance(item, dict) and str(item.get("interface_id") or "").strip()
    }
    statuses: list[dict[str, Any]] = []
    for interface_id in required_ids:
        item = by_id.get(interface_id, {})
        statuses.append(
            {
                "interface_id": interface_id,
                "status": str(item.get("status") or "missing"),
                "available": bool(item.get("available")),
                "source": str(item.get("source") or ""),
                "gaps": list(item.get("gaps") or []) if isinstance(item.get("gaps"), list) else [],
                "recommended_next_action": str(item.get("recommended_next_action") or ""),
            }
        )
    return statuses


def _resident_agi_decision_preflight(
    *, command: RunResidentAgiDecisionTurnCommandV1, audit_pack: dict[str, Any], hard_rule_gate: dict[str, Any]
) -> dict[str, Any]:
    """Verify required decision evidence before allowing a Resident AGI LLM turn."""
    if hard_rule_gate.get("status") == "block":
        return {
            "schema_version": "resident.agi_decision_preflight.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Platform hard-rule gate blocked evidence preflight.",
            "adapter_execution_allowed": False,
            "recommended_verdict": "block",
            "recommended_next_action": "repair_platform_hard_rule_evidence",
            "missing_required_interface_ids": [],
            "required_interface_statuses": [],
            "evidence_interfaces": {},
            "evidence_capability_matrix": {},
        }
    evidence_interfaces = query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=command.workspace,
            decision_type=command.decision_type,
            run_id=command.run_id,
            task_id=command.task_id,
            context_refs=command.context_refs,
            evidence_refs=command.evidence_refs,
            decision_limit=command.audit_pack_decision_limit,
        )
    )
    summary_raw = evidence_interfaces.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    missing_required = [
        str(item or "").strip() for item in summary.get("missing_required_interface_ids", []) if str(item or "").strip()
    ]
    selected_raw = evidence_interfaces.get("selected_decision_capability")
    selected: dict[str, Any] = selected_raw if isinstance(selected_raw, dict) else {}
    evidence_gate_raw = audit_pack.get("evidence_gate")
    evidence_gate: dict[str, Any] = evidence_gate_raw if isinstance(evidence_gate_raw, dict) else {}
    capability_matrix_raw = evidence_interfaces.get("capability_matrix")
    capability_matrix: dict[str, Any] = capability_matrix_raw if isinstance(capability_matrix_raw, dict) else {}
    passed = not missing_required
    recommended_verdict = "continue" if passed else "request_evidence"
    recommended_next_action = "run_resident_agi_judgement" if passed else "request_missing_required_evidence_interfaces"
    return {
        "schema_version": "resident.agi_decision_preflight.v1",
        "status": "pass" if passed else "block",
        "passed": passed,
        "required": True,
        "reason": "Required Resident AGI evidence interfaces are available."
        if passed
        else "Required Resident AGI evidence interfaces are missing or unavailable.",
        "adapter_execution_allowed": passed,
        "recommended_verdict": recommended_verdict,
        "recommended_next_action": recommended_next_action,
        "selected_decision_capability_id": str(selected.get("decision_id") or ""),
        "missing_required_interface_ids": missing_required,
        "required_interface_statuses": _resident_agi_required_interface_statuses(evidence_interfaces),
        "evidence_gate_status": str(evidence_gate.get("status") or ""),
        "evidence_gate_recommended_verdict": str(evidence_gate.get("recommended_verdict") or ""),
        "evidence_interfaces": evidence_interfaces,
        "evidence_capability_matrix": capability_matrix,
    }


_RESIDENT_AGI_HANDOFF_BLOCKED_ACTIONS = (
    "direct_file_write_by_agi",
    "director_tool_execution_by_agi",
    "pm_to_director_shortcut",
    "mark_failed_gate_passed",
    "policy_override",
    "authoritative_repair_metadata",
)


def _resident_agi_handoff_target_roles(
    *, decision_capability_id: str, agi_verdict: str, downstream_allowed: bool
) -> tuple[str, ...]:
    capability_id = str(decision_capability_id or "").strip().lower()
    verdict = str(agi_verdict or "").strip().lower()
    if verdict == "request_evidence":
        return ("resident_agi", "qa")
    if "architecture" in capability_id:
        return ("chief_engineer",)
    if "goal.promotion" in capability_id:
        return ("pm", "chief_engineer", "director")
    if "quality.gate" in capability_id:
        return ("chief_engineer", "director", "qa") if downstream_allowed else ("chief_engineer", "qa")
    if "repair" in capability_id:
        return ("director", "qa")
    if "platform.invariant" in capability_id:
        return ("chief_engineer", "qa")
    if "evidence.interface" in capability_id:
        return ("resident_agi", "qa")
    return ("pm", "chief_engineer", "director", "qa") if downstream_allowed else ("resident_agi", "qa")


def _resident_agi_handoff_status(
    *,
    agi_verdict: str,
    runtime_success: bool,
    downstream_allowed: bool,
    decision_preflight: dict[str, Any],
    output_contract_gate: dict[str, Any],
    runtime_contract_gate: dict[str, Any],
) -> str:
    if not runtime_success:
        return "blocked"
    if not bool(decision_preflight.get("passed")):
        return "hold"
    if not bool(output_contract_gate.get("passed", True)) or not bool(runtime_contract_gate.get("passed", True)):
        return "blocked"
    verdict = str(agi_verdict or "").strip().lower()
    if verdict == "escalate":
        return "escalate"
    if verdict == "request_evidence":
        return "hold"
    if verdict == "continue" and downstream_allowed:
        return "ready"
    return "hold"


def _resident_agi_handoff_allowed_actions(
    *, handoff_status: str, agi_verdict: str, effective_candidate_actions: list[str]
) -> tuple[str, ...]:
    actions = ["record_decision_trace"]
    verdict = str(agi_verdict or "").strip().lower()
    status = str(handoff_status or "").strip().lower()
    if verdict == "request_evidence" or status == "hold":
        actions.append("request_evidence_via_public_cell_contract")
    if verdict == "escalate" or status == "escalate":
        actions.append("escalate_to_chief_engineer")
    if status == "ready":
        actions.append("handoff_to_pm_chief_engineer_director_chain")
    for action in effective_candidate_actions:
        normalized = str(action or "").strip()
        if normalized and normalized not in actions:
            actions.append(normalized)
    return tuple(actions)


def _resident_agi_decision_handoff(
    *,
    command: RunResidentAgiDecisionTurnCommandV1,
    selected_decision_capability: dict[str, Any],
    decision_preflight: dict[str, Any],
    output_contract_gate: dict[str, Any],
    runtime_contract_gate: dict[str, Any],
    hard_rule_gate: dict[str, Any],
    evidence_gate: dict[str, Any],
    agi_verdict: str,
    downstream_allowed: bool,
    runtime_success: bool,
    next_action: str,
    rationale: str,
    error: str,
    effective_candidate_actions: list[str],
    evidence_refs: list[str],
) -> dict[str, Any]:
    decision_capability_id = str(selected_decision_capability.get("decision_id") or command.decision_type).strip()
    handoff_status = _resident_agi_handoff_status(
        agi_verdict=agi_verdict,
        runtime_success=runtime_success,
        downstream_allowed=downstream_allowed,
        decision_preflight=decision_preflight,
        output_contract_gate=output_contract_gate,
        runtime_contract_gate=runtime_contract_gate,
    )
    target_roles = _resident_agi_handoff_target_roles(
        decision_capability_id=decision_capability_id, agi_verdict=agi_verdict, downstream_allowed=downstream_allowed
    )
    allowed_actions = _resident_agi_handoff_allowed_actions(
        handoff_status=handoff_status, agi_verdict=agi_verdict, effective_candidate_actions=effective_candidate_actions
    )
    reason = (
        str(next_action or "").strip()
        or str(rationale or "").strip()
        or str(error or "").strip()
        or "Resident AGI decision requires governed handoff."
    )
    return ResidentAgiDecisionHandoffV1(
        decision_type=command.decision_type,
        decision_capability_id=decision_capability_id,
        handoff_status=handoff_status,
        target_roles=target_roles,
        allowed_actions=allowed_actions,
        blocked_actions=_RESIDENT_AGI_HANDOFF_BLOCKED_ACTIONS,
        downstream_allowed=bool(downstream_allowed and handoff_status == "ready"),
        reason=reason,
        evidence_refs=tuple(evidence_refs),
        context_refs=tuple(command.context_refs),
        gate_statuses={
            "hard_rule_gate": hard_rule_gate.get("status", ""),
            "evidence_gate": evidence_gate.get("status", ""),
            "decision_preflight": decision_preflight.get("status", ""),
            "output_contract_gate": output_contract_gate.get("status", ""),
            "runtime_contract_gate": runtime_contract_gate.get("status", ""),
        },
    ).to_dict()


async def run_resident_agi_decision_turn(command: RunResidentAgiDecisionTurnCommandV1) -> dict[str, Any]:
    """Handle Resident AGI judgement through the shared role runtime contract."""
    tactical_action_catalog = resident_agi_tactical_action_catalog()
    tactical_action_items_raw = tactical_action_catalog.get("items")
    tactical_action_items = tactical_action_items_raw if isinstance(tactical_action_items_raw, list) else []
    tactical_action_summary_raw = tactical_action_catalog.get("summary")
    tactical_action_summary: dict[str, Any] = (
        tactical_action_summary_raw if isinstance(tactical_action_summary_raw, dict) else {}
    )
    tactical_action_ids = [
        str(item.get("action_id") or "").strip()
        for item in tactical_action_items
        if isinstance(item, dict) and str(item.get("action_id") or "").strip()
    ]
    audit_pack: dict[str, Any] | None = query_resident_agi_audit_pack(
        QueryResidentAgiAuditPackV1(workspace=command.workspace, decision_limit=command.audit_pack_decision_limit)
    )
    if audit_pack is not None:
        audit_pack = _resident_agi_audit_pack_with_current_refs(
            audit_pack, context_refs=command.context_refs, evidence_refs=command.evidence_refs
        )
    input_data: dict[str, Any] = {
        "workspace": command.workspace,
        "decision_type": command.decision_type,
        "objective": command.objective,
        "run_id": command.run_id,
        "task_id": command.task_id,
        "goal_id": command.goal_id,
        "evidence": dict(command.evidence),
        "constraints": list(command.constraints),
        "candidate_actions": list(command.candidate_actions),
        "context_refs": list(command.context_refs),
        "evidence_refs": list(command.evidence_refs),
        "confidence": command.confidence,
        "include_audit_pack": True,
        "audit_pack_decision_limit": command.audit_pack_decision_limit,
        "resident_agi_tactical_action_catalog": tactical_action_catalog,
    }
    effective_candidate_actions = list(command.candidate_actions)
    effective_constraints = list(command.constraints)
    hard_rule_gate_raw = audit_pack.get("hard_rule_gate") if audit_pack is not None else None
    hard_rule_gate: dict[str, Any] = hard_rule_gate_raw if isinstance(hard_rule_gate_raw, dict) else {}
    evidence_gate_raw = audit_pack.get("evidence_gate") if audit_pack is not None else None
    evidence_gate: dict[str, Any] = evidence_gate_raw if isinstance(evidence_gate_raw, dict) else {}
    authority_matrix_raw = audit_pack.get("authority_matrix") if audit_pack is not None else None
    authority_matrix: dict[str, Any] = authority_matrix_raw if isinstance(authority_matrix_raw, dict) else {}
    decision_profile_raw = audit_pack.get("decision_profile") if audit_pack is not None else None
    decision_profile: dict[str, Any] = decision_profile_raw if isinstance(decision_profile_raw, dict) else {}
    capability_surface_raw = audit_pack.get("capability_surface") if audit_pack is not None else None
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    decision_boundary_policy_raw = capability_surface.get("decision_boundary_policy")
    decision_boundary_policy: dict[str, Any] = (
        decision_boundary_policy_raw if isinstance(decision_boundary_policy_raw, dict) else {}
    )
    decision_boundary_policy_counts_raw = decision_boundary_policy.get("counts")
    decision_boundary_policy_counts: dict[str, Any] = (
        decision_boundary_policy_counts_raw if isinstance(decision_boundary_policy_counts_raw, dict) else {}
    )
    decision_boundary_execution_raw = decision_boundary_policy.get("capability_execution_policy")
    decision_boundary_execution: dict[str, Any] = (
        decision_boundary_execution_raw if isinstance(decision_boundary_execution_raw, dict) else {}
    )
    selected_decision_capability = _resident_agi_select_decision_capability(
        decision_type=command.decision_type, audit_pack=audit_pack
    )
    resident_agi_participation = _resident_agi_decision_turn_participation(
        command=command, selected_decision_capability=selected_decision_capability
    )
    selected_required_evidence_interfaces = [
        str(item or "").strip()
        for item in selected_decision_capability.get("required_evidence_interfaces", [])
        if str(item or "").strip()
    ]
    selected_optional_evidence_interfaces = [
        str(item or "").strip()
        for item in selected_decision_capability.get("optional_evidence_interfaces", [])
        if str(item or "").strip()
    ]
    selected_candidate_actions = [
        str(item or "").strip()
        for item in selected_decision_capability.get("candidate_actions", [])
        if str(item or "").strip()
    ]
    selected_hard_constraints = [
        str(item or "").strip()
        for item in selected_decision_capability.get("hard_constraints", [])
        if str(item or "").strip()
    ]
    decision_preflight = _resident_agi_decision_preflight(
        command=command, audit_pack=audit_pack or {}, hard_rule_gate=hard_rule_gate
    )
    evidence_interfaces_raw = decision_preflight.get("evidence_interfaces")
    evidence_interfaces: dict[str, Any] = evidence_interfaces_raw if isinstance(evidence_interfaces_raw, dict) else {}
    evidence_capability_matrix_raw = decision_preflight.get("evidence_capability_matrix")
    evidence_capability_matrix: dict[str, Any] = (
        evidence_capability_matrix_raw if isinstance(evidence_capability_matrix_raw, dict) else {}
    )
    evidence_capability_matrix_summary_raw = evidence_capability_matrix.get("summary")
    evidence_capability_matrix_summary: dict[str, Any] = (
        evidence_capability_matrix_summary_raw if isinstance(evidence_capability_matrix_summary_raw, dict) else {}
    )
    if audit_pack is not None:
        input_data["resident_agi_audit_pack"] = audit_pack
        input_data["resident_agi_decision_preflight"] = decision_preflight
        input_data["resident_agi_evidence_interfaces"] = evidence_interfaces
        input_data["resident_agi_evidence_capability_matrix"] = evidence_capability_matrix
        input_data["resident_agi_decision_boundary_policy"] = decision_boundary_policy
        input_data["resident_agi_participation"] = resident_agi_participation
        profile_candidate_actions_raw = decision_profile.get("candidate_actions")
        profile_candidate_actions = (
            profile_candidate_actions_raw if isinstance(profile_candidate_actions_raw, list) else []
        )
        profile_constraints_raw = decision_profile.get("required_constraints")
        profile_constraints = profile_constraints_raw if isinstance(profile_constraints_raw, list) else []
        effective_candidate_actions = _merge_non_empty_strings(
            tuple(command.candidate_actions), selected_candidate_actions, profile_candidate_actions
        )
        effective_constraints = _merge_non_empty_strings(
            tuple(command.constraints), selected_hard_constraints, profile_constraints
        )
        input_data["candidate_actions"] = effective_candidate_actions
        input_data["constraints"] = effective_constraints
        input_data["selected_decision_capability"] = selected_decision_capability
        input_data["required_evidence_interfaces"] = selected_required_evidence_interfaces
        input_data["optional_evidence_interfaces"] = selected_optional_evidence_interfaces
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
                "resident_agi_tactical_action_catalog_schema": tactical_action_catalog.get("schema_version", ""),
                "resident_agi_tactical_action_ids": tactical_action_ids,
                "resident_agi_tactical_action_count": len(tactical_action_ids),
                "resident_agi_tactical_controlled_action_count": int(tactical_action_summary.get("controlled") or 0),
                "resident_agi_tactical_authoritative_actions": int(
                    tactical_action_summary.get("authoritative_actions") or 0
                ),
                "resident_agi_tactical_direct_execution_allowed": bool(
                    tactical_action_summary.get("agi_direct_execution_allowed")
                ),
                "resident_agi_tactical_required_chain": tactical_action_summary.get("required_chain", ""),
                "resident_agi_hard_rule_gate_status": hard_rule_gate.get("status", ""),
                "resident_agi_evidence_gate_status": evidence_gate.get("status", ""),
                "resident_agi_evidence_gate_recommended_verdict": evidence_gate.get("recommended_verdict", ""),
                "resident_agi_authority_matrix_schema": authority_matrix.get("schema_version", ""),
                "resident_agi_chain_required": bool(authority_matrix.get("chain_required")),
                "resident_agi_decision_profile_schema": decision_profile.get("schema_version", ""),
                "resident_agi_decision_profile_recommended_verdict": decision_profile.get("recommended_verdict", ""),
                "resident_agi_decision_profile_next_action": decision_profile.get("recommended_next_action", ""),
                "resident_agi_role_turn_allowed": bool(decision_profile.get("role_turn_allowed", False)),
                "resident_agi_downstream_precheck": decision_profile.get("downstream_precheck", ""),
                "resident_agi_selected_decision_capability": selected_decision_capability.get("decision_id", ""),
                "resident_agi_selected_decision_capability_owner": selected_decision_capability.get("owner", ""),
                "resident_agi_selected_decision_capability_risk": selected_decision_capability.get("risk_level", ""),
                "resident_agi_required_evidence_interfaces": selected_required_evidence_interfaces,
                "resident_agi_optional_evidence_interfaces": selected_optional_evidence_interfaces,
                "resident_agi_decision_preflight_status": decision_preflight.get("status", ""),
                "resident_agi_decision_preflight_passed": bool(decision_preflight.get("passed")),
                "resident_agi_missing_required_interface_ids": list(
                    decision_preflight.get("missing_required_interface_ids") or []
                ),
                "resident_agi_evidence_capability_matrix_schema": evidence_capability_matrix.get("schema_version", ""),
                "resident_agi_evidence_matrix_required_available": int(
                    evidence_capability_matrix_summary.get("required_available") or 0
                ),
                "resident_agi_evidence_matrix_required": int(evidence_capability_matrix_summary.get("required") or 0),
                "resident_agi_evidence_matrix_missing_required": int(
                    evidence_capability_matrix_summary.get("missing_required") or 0
                ),
                "resident_agi_evidence_matrix_recommended_now": int(
                    evidence_capability_matrix_summary.get("recommended_now") or 0
                ),
                "resident_agi_decision_boundary_policy_schema": decision_boundary_policy.get("schema_version", ""),
                "resident_agi_policy_platform_hard_rules": int(
                    decision_boundary_policy_counts.get("platform_hard_rules") or 0
                ),
                "resident_agi_policy_agi_judgement": int(decision_boundary_policy_counts.get("agi_judgement") or 0),
                "resident_agi_policy_governed_execution": int(
                    decision_boundary_policy_counts.get("governed_execution") or 0
                ),
                "resident_agi_policy_direct_writes_allowed": bool(
                    decision_boundary_execution.get("agi_direct_writes_allowed")
                ),
                "resident_agi_policy_direct_tools_allowed": bool(
                    decision_boundary_execution.get("agi_direct_tool_execution_allowed")
                ),
                "resident_agi_manual_role_turn_requested": bool(
                    resident_agi_participation.get("manual_role_turn_requested")
                ),
                "resident_agi_automatic_participation_enabled": bool(
                    resident_agi_participation.get("automatic_participation_enabled")
                ),
            }
        )
        input_data["evidence"] = evidence
    runtime_context = {
        "run_id": command.run_id,
        "task_id": command.task_id,
        "goal_id": command.goal_id,
        "decision_type": command.decision_type,
        "context_refs": list(command.context_refs),
        "evidence_refs": list(command.evidence_refs),
        "resident_agi_enabled": bool(resident_agi_participation.get("enabled")),
        "resident_agi_role_turn_enabled": bool(resident_agi_participation.get("role_turn_enabled")),
        "resident_agi_manual_role_turn_requested": bool(resident_agi_participation.get("manual_role_turn_requested")),
        "resident_agi_automatic_participation_enabled": bool(
            resident_agi_participation.get("automatic_participation_enabled")
        ),
        "resident_agi_participation": resident_agi_participation,
        "resident_agi_participation_scopes": list(resident_agi_participation.get("scopes") or []),
        "resident_agi_audit_pack": audit_pack or {},
        "resident_agi_decision_preflight": decision_preflight,
        "resident_agi_evidence_interfaces": evidence_interfaces,
        "resident_agi_evidence_capability_matrix": evidence_capability_matrix,
        "resident_agi_decision_boundary_policy": decision_boundary_policy,
        "resident_agi_tactical_action_catalog": tactical_action_catalog,
        "metadata": {
            "source": "resident.autonomy.public.run_resident_agi_decision_turn",
            "resident_agi_role_runtime_required": True,
            "context_os_expected": True,
            "transaction_kernel_expected": True,
            "resident_agi_audit_pack_injected": audit_pack is not None,
            "resident_agi_audit_pack_schema": (audit_pack or {}).get("schema_version", ""),
            "resident_agi_tactical_action_catalog_schema": tactical_action_catalog.get("schema_version", ""),
            "resident_agi_tactical_action_count": len(tactical_action_ids),
            "resident_agi_tactical_controlled_action_count": int(tactical_action_summary.get("controlled") or 0),
            "resident_agi_tactical_direct_execution_allowed": bool(
                tactical_action_summary.get("agi_direct_execution_allowed")
            ),
            "resident_agi_hard_rule_gate_status": hard_rule_gate.get("status", ""),
            "resident_agi_evidence_gate_status": evidence_gate.get("status", ""),
            "resident_agi_authority_matrix_schema": authority_matrix.get("schema_version", ""),
            "resident_agi_decision_profile_schema": decision_profile.get("schema_version", ""),
            "resident_agi_role_turn_allowed": bool(decision_profile.get("role_turn_allowed", False)),
            "resident_agi_selected_decision_capability": selected_decision_capability.get("decision_id", ""),
            "resident_agi_required_evidence_interfaces": selected_required_evidence_interfaces,
            "resident_agi_optional_evidence_interfaces": selected_optional_evidence_interfaces,
            "resident_agi_decision_preflight_status": decision_preflight.get("status", ""),
            "resident_agi_decision_preflight_passed": bool(decision_preflight.get("passed")),
            "resident_agi_missing_required_interface_ids": list(
                decision_preflight.get("missing_required_interface_ids") or []
            ),
            "resident_agi_evidence_capability_matrix_schema": evidence_capability_matrix.get("schema_version", ""),
            "resident_agi_evidence_matrix_required_available": int(
                evidence_capability_matrix_summary.get("required_available") or 0
            ),
            "resident_agi_evidence_matrix_required": int(evidence_capability_matrix_summary.get("required") or 0),
            "resident_agi_evidence_matrix_missing_required": int(
                evidence_capability_matrix_summary.get("missing_required") or 0
            ),
            "resident_agi_evidence_matrix_recommended_now": int(
                evidence_capability_matrix_summary.get("recommended_now") or 0
            ),
            "resident_agi_decision_boundary_policy_schema": decision_boundary_policy.get("schema_version", ""),
            "resident_agi_policy_platform_hard_rules": int(
                decision_boundary_policy_counts.get("platform_hard_rules") or 0
            ),
            "resident_agi_policy_agi_judgement": int(decision_boundary_policy_counts.get("agi_judgement") or 0),
            "resident_agi_policy_governed_execution": int(
                decision_boundary_policy_counts.get("governed_execution") or 0
            ),
            "resident_agi_policy_direct_writes_allowed": bool(
                decision_boundary_execution.get("agi_direct_writes_allowed")
            ),
            "resident_agi_policy_direct_tools_allowed": bool(
                decision_boundary_execution.get("agi_direct_tool_execution_allowed")
            ),
            "resident_agi_manual_role_turn_requested": bool(
                resident_agi_participation.get("manual_role_turn_requested")
            ),
            "resident_agi_automatic_participation_enabled": bool(
                resident_agi_participation.get("automatic_participation_enabled")
            ),
        },
    }
    role_result: dict[str, Any]
    if hard_rule_gate.get("status") == "block":
        role_result = {
            "success": False,
            "stage": "resident_agi",
            "decision_type": command.decision_type,
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
    elif not bool(decision_preflight.get("passed")):
        missing_required = [
            str(item or "").strip()
            for item in decision_preflight.get("missing_required_interface_ids", [])
            if str(item or "").strip()
        ]
        preflight_refs = _merge_non_empty_strings(
            list(command.context_refs), list(command.evidence_refs), ["resident.agi_decision_preflight.v1"]
        )
        role_result = {
            "success": False,
            "stage": "resident_agi",
            "decision_type": command.decision_type,
            "error": "Resident AGI decision evidence preflight blocked role execution.",
            "decision": {
                "verdict": "request_evidence",
                "rationale": "Required evidence interfaces are missing before Resident AGI judgement.",
                "evidence_refs": preflight_refs,
                "risks": [f"missing required evidence interface: {item}" for item in missing_required],
                "next_action": "request missing evidence before running Resident AGI",
                "downstream_allowed": False,
                "decision_capability_id": str(selected_decision_capability.get("decision_id") or ""),
            },
            "metadata": {"role_runtime_entrypoint": "roles.runtime.execute_role_session"},
        }
    else:
        adapter = _service_pkg.create_role_adapter("resident_agi", command.workspace)
        try:
            role_result = await adapter.execute(command.task_id or "resident-agi-decision", input_data, runtime_context)
        except (RuntimeError, ValueError) as exc:
            logger.error("run_resident_agi_decision_turn runtime failed: %s", exc)
            role_result = {
                "success": False,
                "stage": "resident_agi",
                "decision_type": command.decision_type,
                "error": str(exc),
                "decision": {},
                "metadata": {"role_runtime_entrypoint": "roles.runtime.execute_role_session"},
            }
    decision_raw = role_result.get("decision")
    decision: dict[str, Any] = decision_raw if isinstance(decision_raw, dict) else {}
    output_contract_gate = _resident_agi_output_contract_gate(
        decision=decision,
        selected_decision_capability=selected_decision_capability,
        hard_rule_gate=hard_rule_gate,
        evidence_gate=evidence_gate,
        decision_preflight=decision_preflight,
    )
    normalized_decision_raw = output_contract_gate.get("normalized_decision")
    if isinstance(normalized_decision_raw, dict) and normalized_decision_raw:
        decision = normalized_decision_raw
    agi_verdict = str(decision.get("verdict") or "").strip().lower()
    rationale = str(decision.get("rationale") or "").strip()
    next_action = str(decision.get("next_action") or "").strip()
    downstream_allowed = bool(decision.get("downstream_allowed", False))
    risks_raw = decision.get("risks")
    risks: list[Any] = risks_raw if isinstance(risks_raw, list) else []
    role_metadata_raw = role_result.get("metadata")
    role_metadata: dict[str, Any] = role_metadata_raw if isinstance(role_metadata_raw, dict) else {}
    error = str(role_result.get("error") or "").strip()
    runtime_success = bool(role_result.get("success"))
    runtime_contract_gate = _resident_agi_runtime_contract_gate(
        role_result=role_result,
        role_metadata=role_metadata,
        hard_rule_gate=hard_rule_gate,
        decision_profile=decision_profile,
        decision_preflight=decision_preflight,
    )
    if bool(runtime_contract_gate.get("required")) and (not bool(runtime_contract_gate.get("passed"))):
        runtime_success = False
        gate_error = str(runtime_contract_gate.get("reason") or "Resident AGI runtime contract gate failed.")
        error = error or gate_error
        failed_contract_checks_raw = runtime_contract_gate.get("failed_check_ids")
        failed_contract_checks = failed_contract_checks_raw if isinstance(failed_contract_checks_raw, list) else []
        risks = [*list(risks), *[f"failed runtime-contract check: {item}" for item in failed_contract_checks]]
    if bool(output_contract_gate.get("required")) and (not bool(output_contract_gate.get("passed"))):
        runtime_success = False
        gate_error = str(output_contract_gate.get("reason") or "Resident AGI output contract gate failed.")
        error = error or gate_error
        failed_output_checks_raw = output_contract_gate.get("failed_check_ids")
        failed_output_checks = failed_output_checks_raw if isinstance(failed_output_checks_raw, list) else []
        risks = [*list(risks), *[f"failed output-contract check: {item}" for item in failed_output_checks]]
    resident_verdict = _resident_decision_verdict(agi_verdict, runtime_success=runtime_success)
    evidence_refs = list(command.evidence_refs)
    decision_evidence_refs_raw = decision.get("evidence_refs")
    decision_evidence_refs: list[Any] = (
        decision_evidence_refs_raw if isinstance(decision_evidence_refs_raw, list) else []
    )
    for item in decision_evidence_refs:
        token = str(item or "").strip()
        if token:
            evidence_refs.append(token)
    decision_handoff = _resident_agi_decision_handoff(
        command=command,
        selected_decision_capability=selected_decision_capability,
        decision_preflight=decision_preflight,
        output_contract_gate=output_contract_gate,
        runtime_contract_gate=runtime_contract_gate,
        hard_rule_gate=hard_rule_gate,
        evidence_gate=evidence_gate,
        agi_verdict=agi_verdict,
        downstream_allowed=downstream_allowed,
        runtime_success=runtime_success,
        next_action=next_action,
        rationale=rationale,
        error=error,
        effective_candidate_actions=effective_candidate_actions,
        evidence_refs=evidence_refs,
    )
    repair_advisory_overlay = _resident_agi_repair_advisory_overlay_from_decision(
        workspace=command.workspace,
        decision=decision,
        decision_capability_id=str(selected_decision_capability.get("decision_id") or command.decision_type),
        participation=resident_agi_participation,
        message=rationale,
        confidence=command.confidence,
        evidence_refs=tuple(evidence_refs),
        context_refs=tuple(command.context_refs),
        metadata={"run_id": command.run_id, "task_id": command.task_id, "goal_id": command.goal_id},
        require_participation_enabled=True,
    )
    recorded = record_resident_decision_entry(
        RecordResidentDecisionCommandV1(
            workspace=command.workspace,
            action="resident_agi_decision_recorded",
            detail={"decision_type": command.decision_type},
            payload={
                "workspace": command.workspace,
                "run_id": command.run_id,
                "actor": "resident_agi",
                "stage": command.decision_type,
                "goal_id": command.goal_id,
                "task_id": command.task_id,
                "summary": _resident_agi_decision_summary(
                    objective=command.objective, agi_verdict=agi_verdict, rationale=rationale, error=error
                ),
                "context_refs": list(command.context_refs),
                "options": [
                    {
                        "option_id": agi_verdict or resident_verdict,
                        "label": next_action or agi_verdict or resident_verdict,
                        "rationale": rationale or error,
                        "strategy_tags": ["resident_agi_turn", command.decision_type],
                        "estimated_score": command.confidence,
                    }
                ],
                "selected_option_id": agi_verdict or resident_verdict,
                "strategy_tags": ["resident_agi_turn", command.decision_type, agi_verdict or resident_verdict],
                "expected_outcome": {
                    "objective": command.objective,
                    "decision_capability": selected_decision_capability,
                    "required_evidence_interfaces": selected_required_evidence_interfaces,
                    "optional_evidence_interfaces": selected_optional_evidence_interfaces,
                    "candidate_actions": effective_candidate_actions,
                    "constraints": effective_constraints,
                    "resident_agi_participation": resident_agi_participation,
                    "resident_agi_audit_pack_required": True,
                    "resident_agi_tactical_action_catalog": tactical_action_catalog,
                },
                "actual_outcome": {
                    "decision_source": "resident_agi_role_runtime",
                    "role_runtime_entrypoint": role_metadata.get("role_runtime_entrypoint"),
                    "resident_agi_audit_pack_injected": audit_pack is not None,
                    "resident_agi_audit_pack_schema": (audit_pack or {}).get("schema_version", ""),
                    "resident_agi_audit_pack_evidence_ref_count": len((audit_pack or {}).get("evidence_refs") or []),
                    "resident_agi_hard_rule_gate": hard_rule_gate,
                    "resident_agi_evidence_gate": evidence_gate,
                    "resident_agi_authority_matrix": authority_matrix,
                    "resident_agi_decision_profile": decision_profile,
                    "resident_agi_decision_capability": selected_decision_capability,
                    "resident_agi_participation": resident_agi_participation,
                    "resident_agi_required_evidence_interfaces": selected_required_evidence_interfaces,
                    "resident_agi_optional_evidence_interfaces": selected_optional_evidence_interfaces,
                    "resident_agi_decision_preflight": decision_preflight,
                    "resident_agi_evidence_capability_matrix": evidence_capability_matrix,
                    "resident_agi_decision_boundary_policy": decision_boundary_policy,
                    "resident_agi_output_contract_gate": output_contract_gate,
                    "resident_agi_runtime_contract_gate": runtime_contract_gate,
                    "resident_agi_decision_handoff": decision_handoff,
                    "resident_agi_repair_advisory_overlay": repair_advisory_overlay,
                    "resident_agi_tactical_action_catalog": tactical_action_catalog,
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
                "confidence": command.confidence,
            },
        )
    )
    control_plane_gate = _append_resident_agi_control_plane_gate(
        command=command,
        recorded=recorded,
        audit_pack=audit_pack,
        selected_decision_capability=selected_decision_capability,
        decision_preflight=decision_preflight,
        output_contract_gate=output_contract_gate,
        runtime_contract_gate=runtime_contract_gate,
        agi_verdict=agi_verdict,
        resident_verdict=resident_verdict,
        downstream_allowed=downstream_allowed,
        runtime_success=runtime_success,
        next_action=next_action,
        rationale=rationale,
        risks=risks,
        error=error,
        evidence_refs=evidence_refs,
    )
    return {
        "ok": runtime_success,
        "workspace": command.workspace,
        "decision": decision,
        "recorded_decision": recorded,
        "control_plane_gate": control_plane_gate,
        "role_result": role_result,
        "audit_pack": audit_pack,
        "selected_decision_capability": selected_decision_capability,
        "resident_agi_participation": resident_agi_participation,
        "decision_handoff": decision_handoff,
        "repair_advisory_overlay": repair_advisory_overlay,
        "required_evidence_interfaces": selected_required_evidence_interfaces,
        "optional_evidence_interfaces": selected_optional_evidence_interfaces,
        "decision_preflight": decision_preflight,
        "evidence_capability_matrix": evidence_capability_matrix,
        "decision_boundary_policy": decision_boundary_policy,
        "output_contract_gate": output_contract_gate,
        "runtime_contract_gate": runtime_contract_gate,
        "error": error or None,
    }
