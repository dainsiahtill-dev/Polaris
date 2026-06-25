"""Public service exports for `resident.autonomy` cell."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from polaris.cells.audit.evidence.public.service import (
    EvidenceBundleService,
    create_evidence_bundle_service,
)
from polaris.cells.resident.autonomy.internal.agi_audit_pack import build_resident_agi_audit_pack
from polaris.cells.resident.autonomy.internal.agi_capability_surface import (
    build_resident_agi_authority_matrix,
    build_resident_agi_capability_surface,
    build_resident_agi_decision_boundaries,
    build_resident_agi_decision_capabilities,
    build_resident_agi_decision_capability_registry,
    resident_agi_capability_surface_payload,
)
from polaris.cells.resident.autonomy.internal.capability_graph import CapabilityGraph
from polaris.cells.resident.autonomy.internal.counterfactual_lab import CounterfactualLab
from polaris.cells.resident.autonomy.internal.decision_trace import DecisionTraceRecorder
from polaris.cells.resident.autonomy.internal.execution_projection import (
    ExecutionProjectionService,
    get_execution_projection_service,
)
from polaris.cells.resident.autonomy.internal.goal_governor import GoalGovernor
from polaris.cells.resident.autonomy.internal.meta_cognition import StrategyInsightEngine
from polaris.cells.resident.autonomy.internal.pm_bridge import ResidentPMBridge
from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    ResidentService,
    get_resident_service,
    record_resident_decision,
    reset_resident_services,
)
from polaris.cells.resident.autonomy.internal.resident_storage import ResidentPaths, ResidentStorage
from polaris.cells.resident.autonomy.internal.self_improvement_lab import SelfImprovementLab
from polaris.cells.resident.autonomy.internal.skill_foundry import SkillFoundry
from polaris.cells.resident.autonomy.public.contracts import (
    ApproveResidentGoalCommandV1,
    CreateResidentGoalCommandV1,
    ExtractResidentSkillsCommandV1,
    MaterializeResidentGoalCommandV1,
    QueryResidentAgiAuditPackV1,
    QueryResidentCapabilitiesV1,
    QueryResidentStatusV1,
    RecordResidentDecisionCommandV1,
    RecordResidentEvidenceCommandV1,
    RejectResidentGoalCommandV1,
    ResidentAgiCapabilityV1,
    ResidentAgiDecisionCapabilityV1,
    ResidentAutonomyError,
    ResidentAutonomyResultV1,
    ResidentCycleCompletedEventV1,
    RunResidentAgiDecisionTurnCommandV1,
    RunResidentCycleCommandV1,
    RunResidentExperimentsCommandV1,
    RunResidentGoalCommandV1,
    RunResidentImprovementsCommandV1,
    RunResidentTickCommandV1,
    StageResidentGoalCommandV1,
    StartResidentCommandV1,
    StopResidentCommandV1,
    UpdateResidentIdentityCommandV1,
)
from polaris.cells.roles.adapters.public.service import create_role_adapter
from polaris.domain.entities.evidence_bundle import (
    EvidenceBundle,
    FileChange,
    PerfEvidence,
    SourceType,
    StaticAnalysisEvidence,
    TestRunEvidence,
)
from polaris.domain.models.resident import (
    DecisionRecord,
    GoalProposal,
    ResidentAgenda,
    ResidentIdentity,
    ResidentMode,
    ResidentRuntimeState,
    SkillProposal,
    SkillProposalStatus,
    utc_now_iso,
)
from polaris.infrastructure.log_pipeline.jetstream_publisher import get_log_jetstream_publisher
from polaris.kernelone.storage import resolve_storage_roots

logger = logging.getLogger(__name__)
_JETSTREAM_PUBLISH_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_RESIDENT_STATUS_CHANNEL = "status.resident"


def _jetstream_publish_enabled() -> bool:
    raw = str(os.environ.get("KERNELONE_JETSTREAM_PUBLISH") or "").strip().lower()
    return bool(raw) and raw not in _JETSTREAM_PUBLISH_FALSE_VALUES


def publish_resident_status_update(
    *,
    workspace: str,
    action: str,
    status_payload: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Publish the latest Resident AGI projection to runtime.v2."""

    if not _jetstream_publish_enabled():
        return False
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return False
    try:
        roots = resolve_storage_roots(workspace_token)
        workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
        if not workspace_key:
            return False
        resident_status = status_payload or get_resident_service(workspace_token).get_status(include_details=True)
        now = datetime.now(timezone.utc)
        event_id = f"resident-status-{uuid4().hex[:12]}"
        envelope = {
            "schema_version": "runtime.v2",
            "event_id": event_id,
            "workspace_key": workspace_key,
            "run_id": str((detail or {}).get("run_id") or ""),
            "channel": _RESIDENT_STATUS_CHANNEL,
            "kind": "resident_status_update",
            "ts": now.isoformat(),
            "cursor": 0,
            "trace_id": event_id,
            "payload": {
                "action": str(action or "updated").strip() or "updated",
                "workspace": workspace_token,
                "resident": resident_status,
                "projection": resident_status,
                "detail": dict(detail or {}),
                "role_id": "resident_agi",
                "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
            },
            "meta": {
                "source": "resident.autonomy",
                "role_id": "resident_agi",
                "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
                "channel": f"runtime.v2.{_RESIDENT_STATUS_CHANNEL}",
            },
        }
        return get_log_jetstream_publisher().publish(
            subject=f"hp.runtime.{workspace_key}.{_RESIDENT_STATUS_CHANNEL}",
            payload=envelope,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Resident AGI status JetStream publish failed: %s", exc)
        return False


def get_evidence_service() -> EvidenceBundleService:
    """Return the canonical evidence bundle service."""
    return create_evidence_bundle_service()


# ---------------------------------------------------------------------------
# Public contract handlers
#
# Map the declared `resident.autonomy` CQRS contracts onto the runtime service
# so the public command/query/event surface is actually exercised (not inert).
# ---------------------------------------------------------------------------

# Labs advanced by a single autonomy tick when the resident is active.
_CYCLE_ACTIONS: tuple[str, ...] = (
    "meta_cognition",
    "skill_foundry",
    "capability_graph",
    "counterfactual_lab",
    "self_improvement_lab",
    "goal_generation",
)


def query_resident_status(query: QueryResidentStatusV1, *, include_details: bool = False) -> dict[str, Any]:
    """Handle :class:`QueryResidentStatusV1` → resident status snapshot."""
    return get_resident_service(query.workspace).get_status(include_details=include_details)


def query_resident_capabilities(query: QueryResidentCapabilitiesV1) -> dict[str, Any]:
    """Handle :class:`QueryResidentCapabilitiesV1` → AGI capability surface."""
    _ = get_resident_service(query.workspace)
    return resident_agi_capability_surface_payload()


def query_resident_agi_audit_pack(query: QueryResidentAgiAuditPackV1) -> dict[str, Any]:
    """Handle :class:`QueryResidentAgiAuditPackV1` → Resident AGI audit pack."""

    status_payload = get_resident_service(query.workspace).get_status(include_details=True)
    return build_resident_agi_audit_pack(
        workspace=query.workspace,
        status_payload=status_payload,
        decision_limit=query.decision_limit,
    )


def _merge_non_empty_strings(*groups: list[Any] | tuple[Any, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for value in group:
            token = str(value or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
    return result


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


def _resident_agi_runtime_contract_gate(
    *,
    role_result: dict[str, Any],
    role_metadata: dict[str, Any],
    hard_rule_gate: dict[str, Any],
    decision_profile: dict[str, Any],
) -> dict[str, Any]:
    """Verify that a successful Resident AGI turn returned RoleRuntime receipt evidence."""

    if hard_rule_gate.get("status") == "block":
        return {
            "schema_version": "resident.agi_runtime_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked before runtime execution.",
            "checks": [],
            "failed_check_ids": [],
        }

    role_turn_allowed = bool(decision_profile.get("role_turn_allowed", True))
    checks = [
        {
            "check_id": "role_result.success",
            "passed": bool(role_result.get("success")),
            "detail": "Role adapter must report runtime success before the decision can be accepted.",
        },
        {
            "check_id": "metadata.role_runtime_entrypoint",
            "passed": role_metadata.get("role_runtime_entrypoint") == "roles.runtime.execute_role_session",
            "detail": "Resident AGI must return the canonical RoleRuntime entrypoint receipt.",
        },
        {
            "check_id": "metadata.context_os_expected",
            "passed": role_metadata.get("context_os_expected") is True,
            "detail": "Resident AGI runtime receipt must preserve ContextOS expectation evidence.",
        },
        {
            "check_id": "metadata.runtime_fallback_used",
            "passed": role_metadata.get("runtime_fallback_used") is False,
            "detail": "Resident AGI runtime cannot fall back to a sidecar or direct LLM path.",
        },
        {
            "check_id": "metadata.fallback_policy",
            "passed": role_metadata.get("fallback_policy") == "fail_closed",
            "detail": "Resident AGI runtime fallback policy must be fail-closed.",
        },
        {
            "check_id": "decision_profile.role_turn_allowed",
            "passed": role_turn_allowed,
            "detail": "Decision profile must allow a Resident AGI role turn.",
        },
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_version": "resident.agi_runtime_contract_gate.v1",
        "status": "pass" if not failed else "fail",
        "passed": not failed,
        "required": True,
        "reason": "RoleRuntime receipt evidence accepted."
        if not failed
        else "RoleRuntime receipt evidence is incomplete.",
        "checks": checks,
        "failed_check_ids": [str(item["check_id"]) for item in failed],
    }


def _resident_agi_decision_type_tokens(decision_type: str) -> set[str]:
    token = str(decision_type or "").strip().lower()
    compact = token.replace("-", "_").replace(".", "_").replace(" ", "_")
    dotted = compact.replace("_", ".")
    aliases = {
        "architecture": "architecture.option.selection",
        "architecture_option": "architecture.option.selection",
        "architecture_option_selection": "architecture.option.selection",
        "architecture_options": "architecture.option.selection",
        "dependency_choice": "architecture.option.selection",
        "evidence": "evidence.interface.selection",
        "evidence_interface": "evidence.interface.selection",
        "evidence_interface_selection": "evidence.interface.selection",
        "goal_execution": "goal.promotion.readiness",
        "goal_promotion": "goal.promotion.readiness",
        "goal_promotion_readiness": "goal.promotion.readiness",
        "hard_rule": "platform.invariant.blocker",
        "invariant": "platform.invariant.blocker",
        "platform_invariant": "platform.invariant.blocker",
        "platform_invariant_blocker": "platform.invariant.blocker",
        "platform_supervision": "evidence.interface.selection",
        "quality_gate": "quality.gate.response",
        "quality_gate_response": "quality.gate.response",
        "verification": "quality.gate.response",
    }
    return {value for value in {token, compact, dotted, aliases.get(compact), aliases.get(dotted)} if value}


def _resident_agi_select_decision_capability(
    *,
    decision_type: str,
    audit_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    """Select the AGI decision capability that governs a requested decision type."""

    capability_surface_raw = (audit_pack or {}).get("capability_surface")
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    decision_capabilities_raw = capability_surface.get("decision_capabilities")
    decision_capabilities = decision_capabilities_raw if isinstance(decision_capabilities_raw, list) else []
    valid_capabilities = [item for item in decision_capabilities if isinstance(item, dict)]
    if not valid_capabilities:
        return {}

    requested_tokens = _resident_agi_decision_type_tokens(decision_type)
    for capability in valid_capabilities:
        capability_id = str(capability.get("decision_id") or "").strip().lower()
        if capability_id in requested_tokens:
            return dict(capability)

    for fallback_id in ("evidence.interface.selection", "quality.gate.response"):
        for capability in valid_capabilities:
            if str(capability.get("decision_id") or "").strip().lower() == fallback_id:
                return dict(capability)
    return dict(valid_capabilities[0])


async def run_resident_agi_decision_turn(command: RunResidentAgiDecisionTurnCommandV1) -> dict[str, Any]:
    """Handle Resident AGI judgement through the shared role runtime contract."""

    audit_pack: dict[str, Any] | None = None
    if command.include_audit_pack:
        audit_pack = query_resident_agi_audit_pack(
            QueryResidentAgiAuditPackV1(
                workspace=command.workspace,
                decision_limit=command.audit_pack_decision_limit,
            )
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
        "include_audit_pack": command.include_audit_pack,
        "audit_pack_decision_limit": command.audit_pack_decision_limit,
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
    selected_decision_capability = _resident_agi_select_decision_capability(
        decision_type=command.decision_type,
        audit_pack=audit_pack,
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

    if audit_pack is not None:
        input_data["resident_agi_audit_pack"] = audit_pack
        profile_candidate_actions_raw = decision_profile.get("candidate_actions")
        profile_candidate_actions = (
            profile_candidate_actions_raw if isinstance(profile_candidate_actions_raw, list) else []
        )
        profile_constraints_raw = decision_profile.get("required_constraints")
        profile_constraints = profile_constraints_raw if isinstance(profile_constraints_raw, list) else []
        effective_candidate_actions = _merge_non_empty_strings(
            tuple(command.candidate_actions),
            selected_candidate_actions,
            profile_candidate_actions,
        )
        effective_constraints = _merge_non_empty_strings(
            tuple(command.constraints),
            selected_hard_constraints,
            profile_constraints,
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
        "resident_agi_audit_pack": audit_pack or {},
        "metadata": {
            "source": "resident.autonomy.public.run_resident_agi_decision_turn",
            "resident_agi_role_runtime_required": True,
            "context_os_expected": True,
            "turn_engine_expected": True,
            "resident_agi_audit_pack_injected": audit_pack is not None,
            "resident_agi_audit_pack_schema": (audit_pack or {}).get("schema_version", ""),
            "resident_agi_hard_rule_gate_status": hard_rule_gate.get("status", ""),
            "resident_agi_evidence_gate_status": evidence_gate.get("status", ""),
            "resident_agi_authority_matrix_schema": authority_matrix.get("schema_version", ""),
            "resident_agi_decision_profile_schema": decision_profile.get("schema_version", ""),
            "resident_agi_role_turn_allowed": bool(decision_profile.get("role_turn_allowed", False)),
            "resident_agi_selected_decision_capability": selected_decision_capability.get("decision_id", ""),
            "resident_agi_required_evidence_interfaces": selected_required_evidence_interfaces,
            "resident_agi_optional_evidence_interfaces": selected_optional_evidence_interfaces,
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
    else:
        adapter = create_role_adapter("resident_agi", command.workspace)
        try:
            role_result = await adapter.execute(
                command.task_id or "resident-agi-decision",
                input_data,
                runtime_context,
            )
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
    )
    if bool(runtime_contract_gate.get("required")) and not bool(runtime_contract_gate.get("passed")):
        runtime_success = False
        gate_error = str(runtime_contract_gate.get("reason") or "Resident AGI runtime contract gate failed.")
        error = error or gate_error
        failed_contract_checks_raw = runtime_contract_gate.get("failed_check_ids")
        failed_contract_checks = failed_contract_checks_raw if isinstance(failed_contract_checks_raw, list) else []
        risks = [
            *list(risks),
            *[f"failed runtime-contract check: {item}" for item in failed_contract_checks],
        ]
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
                    objective=command.objective,
                    agi_verdict=agi_verdict,
                    rationale=rationale,
                    error=error,
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
                "strategy_tags": [
                    "resident_agi_turn",
                    command.decision_type,
                    agi_verdict or resident_verdict,
                ],
                "expected_outcome": {
                    "objective": command.objective,
                    "decision_capability": selected_decision_capability,
                    "required_evidence_interfaces": selected_required_evidence_interfaces,
                    "optional_evidence_interfaces": selected_optional_evidence_interfaces,
                    "candidate_actions": effective_candidate_actions,
                    "constraints": effective_constraints,
                    "resident_agi_audit_pack_required": command.include_audit_pack,
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
                    "resident_agi_required_evidence_interfaces": selected_required_evidence_interfaces,
                    "resident_agi_optional_evidence_interfaces": selected_optional_evidence_interfaces,
                    "resident_agi_runtime_contract_gate": runtime_contract_gate,
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
    return {
        "ok": runtime_success,
        "workspace": command.workspace,
        "decision": decision,
        "recorded_decision": recorded,
        "role_result": role_result,
        "audit_pack": audit_pack,
        "selected_decision_capability": selected_decision_capability,
        "required_evidence_interfaces": selected_required_evidence_interfaces,
        "optional_evidence_interfaces": selected_optional_evidence_interfaces,
        "runtime_contract_gate": runtime_contract_gate,
        "error": error or None,
    }


def start_resident(command: StartResidentCommandV1) -> dict[str, Any]:
    """Handle :class:`StartResidentCommandV1` → activate Resident AGI."""

    result = get_resident_service(command.workspace).start(command.mode)
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_started",
        status_payload=result,
    )
    return result


def stop_resident(command: StopResidentCommandV1) -> dict[str, Any]:
    """Handle :class:`StopResidentCommandV1` → deactivate Resident AGI."""

    result = get_resident_service(command.workspace).stop()
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_stopped",
        status_payload=result,
    )
    return result


def run_resident_tick(command: RunResidentTickCommandV1) -> dict[str, Any]:
    """Handle :class:`RunResidentTickCommandV1` → advance one Resident loop."""

    result = get_resident_service(command.workspace).tick(force=command.force)
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_tick",
        status_payload=result,
        detail={"force": command.force},
    )
    return result


def update_resident_identity(command: UpdateResidentIdentityCommandV1) -> dict[str, Any]:
    """Handle :class:`UpdateResidentIdentityCommandV1` → update Resident identity."""

    result = get_resident_service(command.workspace).update_identity(command.payload)
    publish_resident_status_update(
        workspace=command.workspace,
        action="identity_updated",
    )
    return result


def create_resident_goal(command: CreateResidentGoalCommandV1) -> dict[str, Any]:
    """Handle :class:`CreateResidentGoalCommandV1` → create a governed goal."""

    result = get_resident_service(command.workspace).create_goal_proposal(command.payload).to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_created",
        detail={"goal_id": str(result.get("goal_id") or "")},
    )
    return result


def approve_resident_goal(command: ApproveResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`ApproveResidentGoalCommandV1` → approve a governed goal."""

    goal = get_resident_service(command.workspace).approve_goal(command.goal_id, note=command.note)
    if goal is None:
        return None
    result = goal.to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_approved",
        detail={"goal_id": command.goal_id},
    )
    return result


def reject_resident_goal(command: RejectResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`RejectResidentGoalCommandV1` → reject a governed goal."""

    goal = get_resident_service(command.workspace).reject_goal(command.goal_id, note=command.note)
    if goal is None:
        return None
    result = goal.to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_rejected",
        detail={"goal_id": command.goal_id},
    )
    return result


def materialize_resident_goal(command: MaterializeResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`MaterializeResidentGoalCommandV1` through the Resident goal bridge."""

    service = get_resident_service(command.workspace)
    contract = service.materialize_goal(command.goal_id)
    if contract is not None:
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_materialized",
            status_payload=service.get_status(include_details=True),
            detail={"goal_id": command.goal_id},
        )
    return contract


def stage_resident_goal(command: StageResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`StageResidentGoalCommandV1` through the Resident goal bridge."""

    service = get_resident_service(command.workspace)
    staged = service.stage_goal(
        command.goal_id,
        promote_to_pm_runtime=command.promote_to_pm_runtime,
        ramdisk_root=command.ramdisk_root,
    )
    if staged is not None:
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_staged",
            status_payload=service.get_status(include_details=True),
            detail={
                "goal_id": command.goal_id,
                "promote_to_pm_runtime": command.promote_to_pm_runtime,
            },
        )
    return staged


async def run_resident_goal(command: RunResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`RunResidentGoalCommandV1` through the governed PM bridge."""

    service = get_resident_service(command.workspace)
    result = await service.run_goal(
        command.goal_id,
        settings=command.settings,
        run_type=command.run_type,
        run_director=command.run_director,
        director_iterations=command.director_iterations,
    )
    if result is not None:
        pm_run = result.get("pm_run") if isinstance(result, dict) else {}
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_run_submitted",
            status_payload=service.get_status(include_details=True),
            detail={
                "goal_id": command.goal_id,
                "run_id": str(pm_run.get("run_id") or "") if isinstance(pm_run, dict) else "",
                "run_director": command.run_director,
                "director_iterations": command.director_iterations,
            },
        )
    return result


def _emit_cycle_completed_event(
    command: RunResidentCycleCommandV1, result: ResidentAutonomyResultV1
) -> ResidentCycleCompletedEventV1:
    """Construct and surface a :class:`ResidentCycleCompletedEventV1`."""
    event = ResidentCycleCompletedEventV1(
        event_id=f"resident-cycle-{uuid4().hex[:12]}",
        workspace=result.workspace,
        cycle_id=command.cycle_id,
        status=result.status,
        completed_at=utc_now_iso(),
    )
    logger.info(
        "[resident] cycle completed: workspace=%s cycle=%s status=%s actions=%s",
        event.workspace,
        event.cycle_id,
        event.status,
        result.actions,
    )
    return event


def run_resident_cycle(command: RunResidentCycleCommandV1) -> ResidentAutonomyResultV1:
    """Handle :class:`RunResidentCycleCommandV1` → advance the resident loop.

    ``force`` is read from ``command.context['force']`` (default ``False``), so
    unattended callers preserve the "only ticks when active" gate.  Emits a
    :class:`ResidentCycleCompletedEventV1`; normalizes any failure to
    :class:`ResidentAutonomyError`.
    """
    force = bool(command.context.get("force", False))
    try:
        status = get_resident_service(command.workspace).tick(force=force)
    except ResidentAutonomyError:
        raise
    except Exception as exc:
        raise ResidentAutonomyError(
            f"resident cycle execution failed: {exc}",
            code="cycle_execution_failed",
            details={"cycle_id": command.cycle_id, "workspace": command.workspace},
        ) from exc

    runtime = status.get("runtime", {}) if isinstance(status, dict) else {}
    summary = runtime.get("last_summary", {}) if isinstance(runtime, dict) else {}
    active = bool(runtime.get("active"))
    metrics = (
        {k: v for k, v in summary.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if isinstance(summary, dict)
        else {}
    )
    result = ResidentAutonomyResultV1(
        ok=True,
        workspace=command.workspace,
        cycle_id=command.cycle_id,
        status="completed" if active else "skipped_inactive",
        actions=_CYCLE_ACTIONS if active else (),
        evidence_refs=(),
        metrics=metrics,
    )
    _emit_cycle_completed_event(command, result)
    publish_resident_status_update(
        workspace=command.workspace,
        action="cycle_completed" if active else "cycle_skipped_inactive",
        status_payload=status,
        detail={"cycle_id": command.cycle_id, "actions": list(result.actions)},
    )
    return result


def record_resident_evidence(command: RecordResidentEvidenceCommandV1) -> dict[str, Any]:
    """Handle :class:`RecordResidentEvidenceCommandV1` → append to the decision trace.

    Evidence is mapped onto the evidence-first decision trace as an
    ``evidence:{kind}`` stage entry, so it feeds the same meta-cognition loop.
    """
    recorded = record_resident_decision(
        command.workspace,
        {
            "actor": "resident",
            "stage": f"evidence:{command.evidence_kind}",
            "summary": f"Recorded {command.evidence_kind} evidence for cycle {command.cycle_id}",
            "verdict": "success",
            "context_refs": [command.cycle_id],
            "actual_outcome": dict(command.payload),
        },
    )
    publish_resident_status_update(
        workspace=command.workspace,
        action="evidence_recorded",
        detail={
            "cycle_id": command.cycle_id,
            "evidence_kind": command.evidence_kind,
            "decision_id": str(recorded.get("decision_id") or ""),
        },
    )
    return recorded


def record_resident_decision_entry(command: RecordResidentDecisionCommandV1) -> dict[str, Any]:
    """Handle :class:`RecordResidentDecisionCommandV1` → append a decision trace entry."""

    recorded = record_resident_decision(command.workspace, command.payload)
    detail = {
        "decision_id": str(recorded.get("decision_id") or ""),
        "run_id": str(command.payload.get("run_id") or ""),
        "task_id": str(command.payload.get("task_id") or ""),
        "goal_id": str(command.payload.get("goal_id") or ""),
        **dict(command.detail),
    }
    publish_resident_status_update(
        workspace=command.workspace,
        action=command.action,
        detail=detail,
    )
    return recorded


def extract_resident_skills(command: ExtractResidentSkillsCommandV1) -> list[dict[str, Any]]:
    """Handle :class:`ExtractResidentSkillsCommandV1` → refresh skill artifacts."""

    skills = [item.to_dict() for item in get_resident_service(command.workspace).run_skill_foundry()]
    publish_resident_status_update(
        workspace=command.workspace,
        action="skills_extracted",
        detail={"count": len(skills)},
    )
    return skills


def run_resident_experiments(command: RunResidentExperimentsCommandV1) -> list[dict[str, Any]]:
    """Handle :class:`RunResidentExperimentsCommandV1` → replay counterfactual experiments."""

    experiments = get_resident_service(command.workspace).run_counterfactual_lab()
    publish_resident_status_update(
        workspace=command.workspace,
        action="experiments_run",
        detail={"count": len(experiments)},
    )
    return experiments


def run_resident_improvements(command: RunResidentImprovementsCommandV1) -> list[dict[str, Any]]:
    """Handle :class:`RunResidentImprovementsCommandV1` → propose self-improvements."""

    improvements = get_resident_service(command.workspace).run_self_improvement_lab()
    publish_resident_status_update(
        workspace=command.workspace,
        action="improvements_run",
        detail={"count": len(improvements)},
    )
    return improvements


__all__ = [
    "ApproveResidentGoalCommandV1",
    "CapabilityGraph",
    "CounterfactualLab",
    "CreateResidentGoalCommandV1",
    "DecisionRecord",
    "DecisionTraceRecorder",
    "EvidenceBundle",
    "EvidenceBundleService",
    "ExecutionProjectionService",
    "ExtractResidentSkillsCommandV1",
    "FileChange",
    "GoalGovernor",
    "GoalProposal",
    "MaterializeResidentGoalCommandV1",
    "PerfEvidence",
    "QueryResidentAgiAuditPackV1",
    "QueryResidentCapabilitiesV1",
    "QueryResidentStatusV1",
    "RecordResidentDecisionCommandV1",
    "RecordResidentEvidenceCommandV1",
    "RejectResidentGoalCommandV1",
    "ResidentAgenda",
    "ResidentAgiCapabilityV1",
    "ResidentAgiDecisionCapabilityV1",
    "ResidentAutonomyError",
    "ResidentAutonomyResultV1",
    "ResidentCycleCompletedEventV1",
    "ResidentIdentity",
    "ResidentMode",
    "ResidentPMBridge",
    "ResidentPaths",
    "ResidentRuntimeState",
    "ResidentService",
    "ResidentStorage",
    "RunResidentAgiDecisionTurnCommandV1",
    "RunResidentCycleCommandV1",
    "RunResidentExperimentsCommandV1",
    "RunResidentGoalCommandV1",
    "RunResidentImprovementsCommandV1",
    "RunResidentTickCommandV1",
    "SelfImprovementLab",
    "SkillFoundry",
    "SkillProposal",
    "SkillProposalStatus",
    "SourceType",
    "StageResidentGoalCommandV1",
    "StartResidentCommandV1",
    "StaticAnalysisEvidence",
    "StopResidentCommandV1",
    "StrategyInsightEngine",
    "TestRunEvidence",
    "UpdateResidentIdentityCommandV1",
    "approve_resident_goal",
    "build_resident_agi_authority_matrix",
    "build_resident_agi_capability_surface",
    "build_resident_agi_decision_boundaries",
    "build_resident_agi_decision_capabilities",
    "build_resident_agi_decision_capability_registry",
    "create_evidence_bundle_service",
    "create_resident_goal",
    "extract_resident_skills",
    "get_evidence_service",
    "get_execution_projection_service",
    "get_resident_service",
    "materialize_resident_goal",
    "publish_resident_status_update",
    "query_resident_agi_audit_pack",
    "query_resident_capabilities",
    "query_resident_status",
    "record_resident_decision",
    "record_resident_decision_entry",
    "record_resident_evidence",
    "reject_resident_goal",
    "reset_resident_services",
    "resident_agi_capability_surface_payload",
    "run_resident_agi_decision_turn",
    "run_resident_cycle",
    "run_resident_experiments",
    "run_resident_goal",
    "run_resident_improvements",
    "run_resident_tick",
    "stage_resident_goal",
    "start_resident",
    "stop_resident",
    "update_resident_identity",
]
