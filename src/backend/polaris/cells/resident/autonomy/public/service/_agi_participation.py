"""Resident AGI participation and repair-advisory overlay."""

from __future__ import annotations

from typing import Any

from polaris.cells.director.runtime.public import RepairAdvisoryV1
from polaris.cells.resident.autonomy.internal.agi_capability_surface import resident_agi_participation_policy_payload
from polaris.cells.resident.autonomy.internal.resident_runtime_service import get_resident_service
from polaris.cells.resident.autonomy.public import service as _service_pkg
from polaris.cells.resident.autonomy.public.contracts import (
    BuildResidentAgiRepairAdvisoryOverlayCommandV1,
    QueryResidentAgiAuditPackV1,
    QueryResidentAgiRepairAdvisoryOverlayV1,
    RunResidentAgiDecisionTurnCommandV1,
)

from ._helpers import _merge_non_empty_strings, logger


def query_resident_agi_audit_pack(query: QueryResidentAgiAuditPackV1) -> dict[str, Any]:
    """Handle :class:`QueryResidentAgiAuditPackV1` → Resident AGI audit pack."""
    status_payload = get_resident_service(query.workspace).get_status(include_details=True)
    audit_pack = _service_pkg.build_resident_agi_audit_pack(
        workspace=query.workspace, status_payload=status_payload, decision_limit=query.decision_limit
    )
    repair_advisory_overlay_query = query_resident_agi_repair_advisory_overlay(
        QueryResidentAgiRepairAdvisoryOverlayV1(
            workspace=query.workspace, limit=query.decision_limit, require_ready=False, require_eligible=False
        )
    )
    truth_sources_raw = audit_pack.get("truth_sources")
    truth_sources = truth_sources_raw if isinstance(truth_sources_raw, list) else []
    if "resident.agi_repair_advisory_overlay_query" not in truth_sources:
        truth_sources.append("resident.agi_repair_advisory_overlay_query")
    audit_pack["truth_sources"] = truth_sources
    audit_pack["repair_advisory_overlay_query"] = repair_advisory_overlay_query
    audit_pack["latest_repair_advisory_overlay"] = repair_advisory_overlay_query.get("overlay")
    return audit_pack


def _resident_agi_participation_scope_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(".", "_").replace("-", "_").replace(" ", "_")


def _resident_agi_known_participation_scope_keys() -> set[str]:
    policy = resident_agi_participation_policy_payload()
    keys: set[str] = set()
    flags_raw = policy.get("participation_flags")
    flags = flags_raw if isinstance(flags_raw, list) else []
    for flag in flags:
        key = _resident_agi_participation_scope_key(flag)
        if key:
            keys.add(key)
    scopes_raw = policy.get("available_scopes")
    scopes = scopes_raw if isinstance(scopes_raw, list) else []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        key = _resident_agi_participation_scope_key(scope.get("scope_id"))
        if key:
            keys.add(key)
    return keys


def _resident_agi_identity_participation(workspace: str) -> dict[str, Any]:
    try:
        participation = get_resident_service(workspace).identity.resident_agi_participation
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning("Resident AGI participation policy unavailable: %s", exc)
        return {}
    return participation.to_dict()


def _resident_agi_decision_turn_participation(
    *, command: RunResidentAgiDecisionTurnCommandV1, selected_decision_capability: dict[str, Any]
) -> dict[str, Any]:
    configured = _resident_agi_identity_participation(command.workspace)
    configured_enabled = bool(configured.get("enabled"))
    configured_scopes_raw = configured.get("scopes")
    configured_scopes = configured_scopes_raw if isinstance(configured_scopes_raw, list) else []
    configured_participation_raw = configured.get("participation")
    configured_participation = configured_participation_raw if isinstance(configured_participation_raw, dict) else {}
    selected_decision_id = str(selected_decision_capability.get("decision_id") or "").strip()
    required_role_turn_scopes = ("final_request_audit", "decision_trace", "capability_surface", "decision_boundary")
    decision_turn_scopes = _merge_non_empty_strings(
        tuple(configured_scopes), (*required_role_turn_scopes, command.decision_type, selected_decision_id)
    )
    configured_flags: dict[str, bool] = {}
    for key, value in configured_participation.items():
        normalized = _resident_agi_participation_scope_key(key)
        if normalized:
            configured_flags[normalized] = bool(value)
    normalized_scope_keys = {
        _resident_agi_participation_scope_key(scope)
        for scope in decision_turn_scopes
        if _resident_agi_participation_scope_key(scope)
    }
    known_scope_keys = _resident_agi_known_participation_scope_keys()
    automatic_participation = dict(configured_flags)
    for key in normalized_scope_keys & known_scope_keys:
        automatic_participation[key] = configured_enabled
    participation = dict(automatic_participation)
    for key in normalized_scope_keys & known_scope_keys:
        participation[key] = True
    for key in required_role_turn_scopes:
        participation[key] = True
    return {
        "schema_version": "resident.agi_participation.v1",
        "source": "resident.identity+resident_agi_decision_turn",
        "semantics": "enabled means this explicit resident_agi role turn is active; automatic_participation_enabled is the user-governed background switch",
        "enabled": True,
        "role_turn_enabled": True,
        "manual_role_turn_requested": True,
        "automatic_participation_enabled": configured_enabled,
        "configured_enabled": configured_enabled,
        "configured_scopes": configured_scopes,
        "scopes": decision_turn_scopes,
        "required_role_turn_scopes": list(required_role_turn_scopes),
        "configured_participation": configured_flags,
        "automatic_participation": automatic_participation,
        "participation": participation,
        "custom_scopes_allowed": bool(configured.get("custom_scopes_allowed", True)),
        "selected_decision_capability_id": selected_decision_id,
    }


_RESIDENT_AGI_REPAIR_ADVISORY_SCOPE_KEYS = frozenset(
    _resident_agi_participation_scope_key(value)
    for value in (
        "director.repair.advisory",
        "director_repair_advisory",
        "director_repair_advisory_policy",
        "repair_advisory",
        "repair_rule_suggestion",
        "suggest_repair_rule",
    )
)


def _resident_agi_repair_advisory_participation_enabled(participation: dict[str, Any]) -> bool:
    if not bool(
        participation.get("enabled")
        or participation.get("configured_enabled")
        or participation.get("automatic_participation_enabled")
    ):
        return False
    scopes_raw = participation.get("scopes") or participation.get("configured_scopes") or ()
    scope_keys = {
        _resident_agi_participation_scope_key(scope)
        for scope in scopes_raw
        if _resident_agi_participation_scope_key(scope)
    }
    for flag_group_key in ("participation", "configured_participation", "automatic_participation"):
        flag_group_raw = participation.get(flag_group_key)
        flag_group = flag_group_raw if isinstance(flag_group_raw, dict) else {}
        for key, enabled in flag_group.items():
            normalized = _resident_agi_participation_scope_key(key)
            if enabled and normalized:
                scope_keys.add(normalized)
    return bool(scope_keys & _RESIDENT_AGI_REPAIR_ADVISORY_SCOPE_KEYS)


def _resident_agi_repair_advisory_decision_relevant(*, decision: dict[str, Any], decision_capability_id: str) -> bool:
    capability_id = _resident_agi_participation_scope_key(
        decision_capability_id or str(decision.get("decision_capability_id") or "")
    )
    next_action = _resident_agi_participation_scope_key(str(decision.get("next_action") or ""))
    has_rules = isinstance(decision.get("suggested_rules"), list) and bool(decision.get("suggested_rules"))
    return (
        capability_id in _RESIDENT_AGI_REPAIR_ADVISORY_SCOPE_KEYS or next_action == "suggest_repair_rule" or has_rules
    )


def _resident_agi_repair_advisory_overlay_from_decision(
    *,
    workspace: str,
    decision: dict[str, Any],
    decision_capability_id: str,
    participation: dict[str, Any],
    message: str = "",
    confidence: float = 0.0,
    evidence_refs: tuple[str, ...] = (),
    context_refs: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    require_participation_enabled: bool = True,
) -> dict[str, Any]:
    relevant = _resident_agi_repair_advisory_decision_relevant(
        decision=decision, decision_capability_id=decision_capability_id
    )
    participation_enabled = _resident_agi_repair_advisory_participation_enabled(participation)
    base: dict[str, Any] = {
        "schema_version": "resident.agi_repair_advisory_overlay.v1",
        "source": "resident.autonomy.public.build_resident_agi_repair_advisory_overlay",
        "workspace": workspace,
        "status": "not_applicable",
        "active": False,
        "eligible_for_director_injection": False,
        "advisory_only": True,
        "authoritative": False,
        "agi_execution_authority": False,
        "director_runtime_contract": "director.repair_advisory_policy.v1",
        "decision_capability_id": decision_capability_id or str(decision.get("decision_capability_id") or ""),
        "participation_enabled": participation_enabled,
        "advisor_notes": [],
        "error": "",
    }
    if not relevant:
        return base
    if require_participation_enabled and (not participation_enabled):
        return {
            **base,
            "status": "disabled_by_participation_policy",
            "reason": "Resident AGI repair advisory participation is not enabled for this workspace.",
        }
    suggested_rules_raw = decision.get("suggested_rules")
    suggested_rules = suggested_rules_raw if isinstance(suggested_rules_raw, list) else []
    if not suggested_rules:
        return {
            **base,
            "status": "no_suggested_rules",
            "reason": "Resident AGI did not provide advisory suggested_rules.",
        }
    advisory_metadata = {
        **dict(metadata or {}),
        "workspace": workspace,
        "decision_capability_id": base["decision_capability_id"],
        "context_refs": list(context_refs),
        "evidence_refs": list(evidence_refs),
        "source_role": "resident_agi",
    }
    try:
        note = RepairAdvisoryV1(
            advisor_source="resident_agi",
            message=message
            or str(decision.get("rationale") or "Resident AGI suggested non-authoritative repair rules."),
            confidence=confidence or float(decision.get("confidence") or 0.0),
            suggested_rules=tuple(suggested_rules),
            metadata=advisory_metadata,
        )
    except (TypeError, ValueError) as exc:
        return {
            **base,
            "status": "invalid_advisory",
            "reason": "Resident AGI repair advisory failed Director Runtime policy validation.",
            "error": str(exc),
        }
    return {
        **base,
        "status": "ready",
        "active": True,
        "eligible_for_director_injection": participation_enabled,
        "advisor_notes": [note.to_dict()],
        "reason": "Resident AGI repair advisory is valid and non-authoritative.",
    }


def build_resident_agi_repair_advisory_overlay(
    command: BuildResidentAgiRepairAdvisoryOverlayCommandV1,
) -> dict[str, Any]:
    """Project Resident AGI repair suggestions into Director advisory notes."""
    participation = _resident_agi_identity_participation(command.workspace)
    return _resident_agi_repair_advisory_overlay_from_decision(
        workspace=command.workspace,
        decision=dict(command.decision),
        decision_capability_id=command.decision_capability_id,
        participation=participation,
        message=command.message,
        confidence=command.confidence,
        evidence_refs=command.evidence_refs,
        context_refs=command.context_refs,
        metadata=dict(command.metadata),
        require_participation_enabled=command.require_participation_enabled,
    )


def _resident_agi_repair_advisory_overlay_from_decision_record(decision: dict[str, Any]) -> dict[str, Any]:
    actual_outcome_raw = decision.get("actual_outcome")
    actual_outcome = actual_outcome_raw if isinstance(actual_outcome_raw, dict) else {}
    for key in ("resident_agi_repair_advisory_overlay", "repair_advisory_overlay"):
        overlay_raw = actual_outcome.get(key)
        overlay = overlay_raw if isinstance(overlay_raw, dict) else {}
        if overlay.get("schema_version") != "resident.agi_repair_advisory_overlay.v1":
            continue
        if bool(overlay.get("authoritative")) or bool(overlay.get("agi_execution_authority")):
            continue
        return dict(overlay)
    return {}


def query_resident_agi_repair_advisory_overlay(query: QueryResidentAgiRepairAdvisoryOverlayV1) -> dict[str, Any]:
    """Return the latest persisted Resident AGI repair advisory overlay."""
    decisions = [item.to_dict() for item in get_resident_service(query.workspace).list_decisions(limit=query.limit)]
    matched = 0
    rejected_by_filter = 0
    for decision in decisions:
        overlay = _resident_agi_repair_advisory_overlay_from_decision_record(decision)
        if not overlay:
            continue
        matched += 1
        if query.require_ready and str(overlay.get("status") or "").strip().lower() != "ready":
            rejected_by_filter += 1
            continue
        if query.require_eligible and (not bool(overlay.get("eligible_for_director_injection"))):
            rejected_by_filter += 1
            continue
        return {
            "schema_version": "resident.agi_repair_advisory_overlay_query.v1",
            "source": "resident.autonomy.public.query_resident_agi_repair_advisory_overlay",
            "workspace": query.workspace,
            "status": "found",
            "found": True,
            "overlay": overlay,
            "decision_ref": {
                "decision_id": str(decision.get("decision_id") or ""),
                "timestamp": str(decision.get("timestamp") or ""),
                "run_id": str(decision.get("run_id") or ""),
                "task_id": str(decision.get("task_id") or ""),
                "stage": str(decision.get("stage") or ""),
                "actor": str(decision.get("actor") or ""),
            },
            "filters": {
                "limit": query.limit,
                "require_ready": query.require_ready,
                "require_eligible": query.require_eligible,
            },
            "considered_decision_count": len(decisions),
            "matched_overlay_count": matched,
            "rejected_by_filter_count": rejected_by_filter,
            "advisory_only": True,
            "authoritative": False,
            "agi_execution_authority": False,
            "director_runtime_contract": str(
                overlay.get("director_runtime_contract") or "director.repair_advisory_policy.v1"
            ),
        }
    return {
        "schema_version": "resident.agi_repair_advisory_overlay_query.v1",
        "source": "resident.autonomy.public.query_resident_agi_repair_advisory_overlay",
        "workspace": query.workspace,
        "status": "missing",
        "found": False,
        "overlay": None,
        "decision_ref": {},
        "filters": {
            "limit": query.limit,
            "require_ready": query.require_ready,
            "require_eligible": query.require_eligible,
        },
        "considered_decision_count": len(decisions),
        "matched_overlay_count": matched,
        "rejected_by_filter_count": rejected_by_filter,
        "advisory_only": True,
        "authoritative": False,
        "agi_execution_authority": False,
        "director_runtime_contract": "director.repair_advisory_policy.v1",
    }
