"""Public service exports for `resident.autonomy` cell."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from polaris.cells.audit.diagnosis.public import QueryAuditDiagnosisTrailV1, query_audit_diagnosis_trail
from polaris.cells.audit.evidence.public.service import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
    EvidenceBundleService,
    append_evidence_event,
    create_evidence_bundle_service,
)
from polaris.cells.audit.verdict.public import QueryAuditVerdictV1, create_artifact_service, query_audit_verdict
from polaris.cells.context.catalog.public import ContextCatalogService, SearchCellsQueryV1
from polaris.cells.context.engine.public import (
    ContextEngineError,
    QueryFinalProviderRequestAuditV1,
    get_anthropomorphic_context_v2,
    query_final_provider_request_audit,
)
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    RunLedgerAppendResultV1,
    append_run_ledger_event,
    read_run_ledger_projection,
    read_run_provenance_bundle,
)
from polaris.cells.control_plane.verifier_policy.public import (
    ReadVerifierPolicyQueryV1,
    read_verifier_policy,
)
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    query_director_repair_advisory_policy,
    query_director_repair_coverage,
    query_director_repair_strategy_catalog,
)
from polaris.cells.director.tasking.public import resolve_task_execution_profile
from polaris.cells.resident.autonomy.internal.agi_audit_pack import (
    build_resident_agi_audit_pack,
    resident_agi_context_snapshot_refs,
)
from polaris.cells.resident.autonomy.internal.agi_capability_surface import (
    build_resident_agi_authority_matrix,
    build_resident_agi_capability_access_registry,
    build_resident_agi_capability_surface,
    build_resident_agi_decision_boundaries,
    build_resident_agi_decision_capabilities,
    build_resident_agi_decision_capability_registry,
    build_resident_agi_evidence_interface_contract,
    resident_agi_capability_surface_payload,
    resident_agi_participation_policy_payload,
)
from polaris.cells.resident.autonomy.internal.agi_tactical_actions import (
    resident_agi_tactical_action_catalog,
    resident_agi_tactical_action_payload,
    resident_agi_tactical_action_spec,
)
from polaris.cells.resident.autonomy.internal.agi_tactical_chat import (
    build_resident_agi_tactical_chat_response,
)
from polaris.cells.resident.autonomy.internal.capability_graph import CapabilityGraph
from polaris.cells.resident.autonomy.internal.counterfactual_lab import CounterfactualLab
from polaris.cells.resident.autonomy.internal.decision_trace import DecisionTraceRecorder
from polaris.cells.resident.autonomy.internal.execution_projection import (
    ExecutionProjectionService,
    get_execution_projection_service,
)
from polaris.cells.resident.autonomy.internal.goal_attempt_ledger import (
    observe_goal_attempt,
    query_goal_execution,
    settle_goal_attempt,
    start_goal_attempt,
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
    ArchiveResidentGoalCommandV1,
    BuildResidentAgiRepairAdvisoryOverlayCommandV1,
    CreateResidentGoalCommandV1,
    ExecuteResidentAgiTacticalActionCommandV1,
    ExtractResidentSkillsCommandV1,
    MaterializeResidentGoalCommandV1,
    ObserveResidentGoalAttemptCommandV1,
    QueryResidentAgiAuditPackV1,
    QueryResidentAgiEvidenceInterfacesV1,
    QueryResidentAgiHandoffsV1,
    QueryResidentAgiRepairAdvisoryOverlayV1,
    QueryResidentAgiTacticalChatV1,
    QueryResidentCapabilitiesV1,
    QueryResidentGoalExecutionV1,
    QueryResidentStatusV1,
    RecordResidentDecisionCommandV1,
    RecordResidentEvidenceCommandV1,
    RejectResidentGoalCommandV1,
    ResidentAgiCapabilityV1,
    ResidentAgiDecisionCapabilityV1,
    ResidentAgiDecisionHandoffV1,
    ResidentAgiDecisionOutputV1,
    ResidentAutonomyError,
    ResidentAutonomyResultV1,
    ResidentCycleCompletedEventV1,
    ResidentGoalAttemptReceiptV1,
    ResidentGoalExecutionV1,
    ResidentGoalLifecycleErrorV1,
    RunResidentAgiDecisionTurnCommandV1,
    RunResidentCycleCommandV1,
    RunResidentExperimentsCommandV1,
    RunResidentGoalCommandV1,
    RunResidentImprovementsCommandV1,
    RunResidentTickCommandV1,
    SettleResidentGoalAttemptCommandV1,
    StageResidentGoalCommandV1,
    StartResidentCommandV1,
    StartResidentGoalAttemptCommandV1,
    StopResidentCommandV1,
    UpdateResidentAgiParticipationCommandV1,
    UpdateResidentIdentityCommandV1,
)
from polaris.cells.roles.adapters.public.service import create_role_adapter
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path
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
                "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
            },
            "meta": {
                "source": "resident.autonomy",
                "role_id": "resident_agi",
                "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
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


def start_resident_goal_attempt(
    command: StartResidentGoalAttemptCommandV1,
) -> ResidentGoalAttemptReceiptV1:
    if type(command) is not StartResidentGoalAttemptCommandV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_attempt_command", "invalid start command type")
    return start_goal_attempt(command)


def observe_resident_goal_attempt(
    command: ObserveResidentGoalAttemptCommandV1,
) -> ResidentGoalAttemptReceiptV1:
    if type(command) is not ObserveResidentGoalAttemptCommandV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_attempt_command", "invalid observe command type")
    return observe_goal_attempt(command)


def settle_resident_goal_attempt(
    command: SettleResidentGoalAttemptCommandV1,
) -> ResidentGoalAttemptReceiptV1:
    if type(command) is not SettleResidentGoalAttemptCommandV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_attempt_command", "invalid settle command type")
    return settle_goal_attempt(command)


def query_resident_goal_execution(query: QueryResidentGoalExecutionV1) -> ResidentGoalExecutionV1:
    """Zero-write durable execution query; does not construct ResidentService."""
    if type(query) is not QueryResidentGoalExecutionV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_execution_query", "invalid execution query type")
    return query_goal_execution(query)


def query_resident_capabilities(query: QueryResidentCapabilitiesV1) -> dict[str, Any]:
    """Handle :class:`QueryResidentCapabilitiesV1` → AGI capability surface."""
    _ = get_resident_service(query.workspace)
    return resident_agi_capability_surface_payload()


def query_resident_agi_audit_pack(query: QueryResidentAgiAuditPackV1) -> dict[str, Any]:
    """Handle :class:`QueryResidentAgiAuditPackV1` → Resident AGI audit pack."""

    status_payload = get_resident_service(query.workspace).get_status(include_details=True)
    audit_pack = build_resident_agi_audit_pack(
        workspace=query.workspace,
        status_payload=status_payload,
        decision_limit=query.decision_limit,
    )
    repair_advisory_overlay_query = query_resident_agi_repair_advisory_overlay(
        QueryResidentAgiRepairAdvisoryOverlayV1(
            workspace=query.workspace,
            limit=query.decision_limit,
            require_ready=False,
            require_eligible=False,
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
    *,
    command: RunResidentAgiDecisionTurnCommandV1,
    selected_decision_capability: dict[str, Any],
) -> dict[str, Any]:
    configured = _resident_agi_identity_participation(command.workspace)
    configured_enabled = bool(configured.get("enabled"))
    configured_scopes_raw = configured.get("scopes")
    configured_scopes = configured_scopes_raw if isinstance(configured_scopes_raw, list) else []
    configured_participation_raw = configured.get("participation")
    configured_participation = configured_participation_raw if isinstance(configured_participation_raw, dict) else {}
    selected_decision_id = str(selected_decision_capability.get("decision_id") or "").strip()
    required_role_turn_scopes = (
        "final_request_audit",
        "decision_trace",
        "capability_surface",
        "decision_boundary",
    )
    decision_turn_scopes = _merge_non_empty_strings(
        tuple(configured_scopes),
        (
            *required_role_turn_scopes,
            command.decision_type,
            selected_decision_id,
        ),
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


def _resident_agi_repair_advisory_participation_enabled(
    participation: dict[str, Any],
) -> bool:
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


def _resident_agi_repair_advisory_decision_relevant(
    *,
    decision: dict[str, Any],
    decision_capability_id: str,
) -> bool:
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
        decision=decision,
        decision_capability_id=decision_capability_id,
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
    if require_participation_enabled and not participation_enabled:
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


def _resident_agi_repair_advisory_overlay_from_decision_record(
    decision: dict[str, Any],
) -> dict[str, Any]:
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


def query_resident_agi_repair_advisory_overlay(
    query: QueryResidentAgiRepairAdvisoryOverlayV1,
) -> dict[str, Any]:
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
        if query.require_eligible and not bool(overlay.get("eligible_for_director_injection")):
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


def _resident_agi_policy_decision(
    *,
    agi_verdict: str,
    resident_verdict: str,
    runtime_success: bool,
    downstream_allowed: bool,
) -> str:
    """Map a Resident AGI judgement into a control-plane policy decision."""

    normalized_agi = str(agi_verdict or "").strip().lower()
    normalized_resident = str(resident_verdict or "").strip().lower()
    if not runtime_success:
        return "block"
    if normalized_agi == "continue" and downstream_allowed and normalized_resident == "success":
        return "allow"
    if normalized_agi in {"request_evidence", "escalate", "block"}:
        return normalized_agi
    if normalized_resident == "success" and downstream_allowed:
        return "allow"
    return "block"


def _resident_agi_control_run_id(
    command: RunResidentAgiDecisionTurnCommandV1,
    recorded: dict[str, Any],
) -> str:
    decision_id = str(recorded.get("decision_id") or "").strip()
    return (
        str(command.run_id or "").strip()
        or str(recorded.get("run_id") or "").strip()
        or (f"resident-agi-{decision_id}" if decision_id else "")
        or f"resident-agi-{uuid4().hex[:12]}"
    )


def _resident_agi_control_gate_summary(
    *,
    policy_decision: str,
    agi_verdict: str,
    error: str,
) -> str:
    if policy_decision == "allow":
        return "Resident AGI permitted downstream continuation."
    if policy_decision == "request_evidence":
        return "Resident AGI blocked downstream work until required evidence is available."
    if policy_decision == "escalate":
        return "Resident AGI escalated the decision before downstream work can continue."
    if error:
        return f"Resident AGI blocked downstream work: {error}"
    return f"Resident AGI blocked downstream work with verdict `{agi_verdict or 'unknown'}`."


def _append_resident_agi_control_plane_gate(
    *,
    command: RunResidentAgiDecisionTurnCommandV1,
    recorded: dict[str, Any],
    audit_pack: dict[str, Any] | None,
    selected_decision_capability: dict[str, Any],
    decision_preflight: dict[str, Any],
    output_contract_gate: dict[str, Any],
    runtime_contract_gate: dict[str, Any],
    agi_verdict: str,
    resident_verdict: str,
    downstream_allowed: bool,
    runtime_success: bool,
    next_action: str,
    rationale: str,
    risks: list[Any],
    error: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    """Persist Resident AGI judgement as a platform control-plane gate."""

    run_id = _resident_agi_control_run_id(command, recorded)
    decision_id = str(recorded.get("decision_id") or "").strip()
    policy_decision = _resident_agi_policy_decision(
        agi_verdict=agi_verdict,
        resident_verdict=resident_verdict,
        runtime_success=runtime_success,
        downstream_allowed=downstream_allowed,
    )
    control_downstream_allowed = policy_decision == "allow"
    gate_summary = _resident_agi_control_gate_summary(
        policy_decision=policy_decision,
        agi_verdict=agi_verdict,
        error=error,
    )
    event: dict[str, Any] = {
        "schema_version": "resident.agi_control_gate.v1",
        "event_type": "gate_evaluated",
        "decision_event_type": "resident_agi_decision_evaluated",
        "source": "resident.autonomy.public.run_resident_agi_decision_turn",
        "run_id": run_id,
        "task_id": command.task_id,
        "goal_id": command.goal_id,
        "stage": command.decision_type,
        "decision_id": decision_id,
        "decision_type": command.decision_type,
        "actor": "resident_agi",
        "gate": {
            "name": "resident_agi_decision",
            "ok": control_downstream_allowed,
            "summary": gate_summary,
            "policy_decision": policy_decision,
            "downstream_allowed": control_downstream_allowed,
            "runtime_success": runtime_success,
        },
        "resident_agi_decision": {
            "agi_verdict": agi_verdict,
            "resident_verdict": resident_verdict,
            "policy_decision": policy_decision,
            "downstream_allowed": control_downstream_allowed,
            "next_action": next_action,
            "rationale": rationale,
            "risks": list(risks),
            "error": error,
            "decision_capability_id": str(selected_decision_capability.get("decision_id") or ""),
        },
        "contract_gates": {
            "decision_preflight": decision_preflight,
            "output_contract_gate": output_contract_gate,
            "runtime_contract_gate": runtime_contract_gate,
        },
        "job_token": {
            "token_id": f"resident-agi-{decision_id or run_id}",
            "run_id": run_id,
            "task_id": command.task_id,
            "goal_id": command.goal_id,
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {
                "enabled_evidence_modalities": [
                    "resident_decision_trace",
                    "resident_agi_audit_pack",
                ],
                "required_evidence_modalities": [],
            },
        },
        "physical_evidence": {
            "modalities": {
                "resident_decision_trace": {
                    "present": bool(decision_id),
                    "ok": bool(decision_id),
                    "detail": "Resident decision trace entry recorded before control-plane gate append.",
                    "metadata": {"decision_id": decision_id},
                },
                "resident_agi_audit_pack": {
                    "present": audit_pack is not None,
                    "ok": audit_pack is not None,
                    "detail": "Resident AGI audit pack was injected into the decision turn.",
                    "metadata": {"schema_version": (audit_pack or {}).get("schema_version", "")},
                },
            },
            "decision_trace": {
                "decision_id": decision_id,
                "evidence_refs": list(evidence_refs),
            },
        },
    }
    evidence_result: EvidenceAppendedEventV1 = append_evidence_event(
        AppendEvidenceEventCommandV1(
            kind="resident_agi.decision_gate",
            workspace=command.workspace,
            payload={
                "run_id": run_id,
                "decision_id": decision_id,
                "event": event,
            },
            metadata={
                "source": "resident.autonomy.public.run_resident_agi_decision_turn",
                "run_id": run_id,
                "task_id": command.task_id,
                "goal_id": command.goal_id,
                "decision_id": decision_id,
                "decision_type": command.decision_type,
                "policy_decision": policy_decision,
            },
        )
    )
    event["physical_evidence"]["decision_trace"]["evidence_event_ref"] = evidence_result.receipt_path
    ledger_result: RunLedgerAppendResultV1 = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=command.workspace,
            run_id=run_id,
            event=event,
        )
    )
    return {
        "schema_version": "resident.agi_control_gate_receipt.v1",
        "persistence_ok": True,
        "run_id": run_id,
        "decision_id": decision_id,
        "policy_decision": policy_decision,
        "downstream_allowed": control_downstream_allowed,
        "gate_ok": control_downstream_allowed,
        "ledger_receipt": dict(ledger_result.receipt),
        "evidence_receipt_path": evidence_result.receipt_path,
    }


def _resident_agi_runtime_contract_gate(
    *,
    role_result: dict[str, Any],
    role_metadata: dict[str, Any],
    hard_rule_gate: dict[str, Any],
    decision_profile: dict[str, Any],
    decision_preflight: dict[str, Any] | None = None,
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
    preflight = decision_preflight if isinstance(decision_preflight, dict) else {}
    if preflight and not bool(preflight.get("passed")):
        return {
            "schema_version": "resident.agi_runtime_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked by decision evidence preflight.",
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


def _resident_agi_decision_sequence(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())


def _resident_agi_output_contract_gate(
    *,
    decision: dict[str, Any],
    selected_decision_capability: dict[str, Any],
    hard_rule_gate: dict[str, Any],
    evidence_gate: dict[str, Any],
    decision_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the Resident AGI model output before accepting its decision."""

    if hard_rule_gate.get("status") == "block":
        return {
            "schema_version": "resident.agi_output_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked before model output validation.",
            "checks": [],
            "failed_check_ids": [],
            "normalized_decision": {},
        }
    preflight = decision_preflight if isinstance(decision_preflight, dict) else {}
    if preflight and not bool(preflight.get("passed")):
        return {
            "schema_version": "resident.agi_output_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked before model output validation.",
            "checks": [],
            "failed_check_ids": [],
            "normalized_decision": {},
        }

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, detail: str) -> None:
        checks.append({"check_id": check_id, "passed": passed, "detail": detail})

    selected_decision_id = str(selected_decision_capability.get("decision_id") or "").strip()
    add_check(
        "selected_decision_capability.present",
        bool(selected_decision_id),
        "A Resident AGI output must be validated against a selected decision capability.",
    )
    output: ResidentAgiDecisionOutputV1 | None = None
    normalized_decision: dict[str, Any] = {}
    if not decision:
        add_check("decision.output_schema", False, "Resident AGI must return a non-empty JSON decision object.")
    else:
        try:
            downstream_allowed = decision.get("downstream_allowed")
            if type(downstream_allowed) is not bool:
                raise ValueError("downstream_allowed must be an exact bool")
            output = ResidentAgiDecisionOutputV1(
                verdict=str(decision.get("verdict") or ""),
                rationale=str(decision.get("rationale") or ""),
                evidence_refs=_resident_agi_decision_sequence(decision, "evidence_refs"),
                risks=_resident_agi_decision_sequence(decision, "risks"),
                next_action=str(decision.get("next_action") or ""),
                downstream_allowed=downstream_allowed,
                decision_capability_id=str(decision.get("decision_capability_id") or ""),
            )
            normalized_decision = output.to_dict()
            add_check("decision.output_schema", True, "Resident AGI output matches ResidentAgiDecisionOutputV1.")
        except ValueError as exc:
            add_check("decision.output_schema", False, str(exc))

    if output is not None:
        add_check(
            "decision_capability_id.matches_selected",
            output.decision_capability_id == selected_decision_id,
            "Resident AGI must echo the selected decision capability id.",
        )
        evidence_gate_status = str(evidence_gate.get("status") or "").strip().lower()
        evidence_blocks_downstream = evidence_gate_status in {"hold", "fail", "block"}
        add_check(
            "evidence_gate.continue_guard",
            not (evidence_blocks_downstream and output.verdict == "continue"),
            "Resident AGI cannot continue when the evidence gate is hold/fail/block.",
        )
        add_check(
            "evidence_gate.downstream_guard",
            not (evidence_blocks_downstream and output.downstream_allowed),
            "Resident AGI cannot allow downstream execution while evidence is incomplete.",
        )
        add_check(
            "non_continue.downstream_guard",
            output.verdict == "continue" or not output.downstream_allowed,
            "Only a continue verdict may allow downstream execution.",
        )
        next_action = str(output.next_action or "").strip()
        suggested_rules_raw = decision.get("suggested_rules")
        suggested_rules = suggested_rules_raw if isinstance(suggested_rules_raw, list) else []
        repair_advisory_relevant = _resident_agi_repair_advisory_decision_relevant(
            decision=decision,
            decision_capability_id=selected_decision_id,
        )
        if repair_advisory_relevant:
            add_check(
                "repair_advisory.suggested_rules_present",
                next_action != "suggest_repair_rule" or bool(suggested_rules),
                "Repair-rule suggestions require a non-empty suggested_rules list.",
            )
        if suggested_rules:
            try:
                advisory = RepairAdvisoryV1(
                    advisor_source="resident_agi",
                    message=output.rationale,
                    confidence=0.0,
                    suggested_rules=tuple(suggested_rules),
                    metadata={"source_role": "resident_agi"},
                )
                normalized_decision["suggested_rules"] = advisory.to_dict()["suggested_rules"]
                add_check(
                    "repair_advisory.suggested_rules_policy",
                    True,
                    "Resident AGI suggested_rules pass Director Runtime advisory policy.",
                )
            except (TypeError, ValueError) as exc:
                add_check(
                    "repair_advisory.suggested_rules_policy",
                    False,
                    str(exc),
                )

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_version": "resident.agi_output_contract_gate.v1",
        "status": "pass" if not failed else "fail",
        "passed": not failed,
        "required": True,
        "reason": "Resident AGI output contract accepted."
        if not failed
        else "Resident AGI output contract is incomplete or unsafe.",
        "checks": checks,
        "failed_check_ids": [str(item["check_id"]) for item in failed],
        "normalized_decision": normalized_decision,
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
        "director_repair": "director.repair.advisory",
        "director_repair_advisory": "director.repair.advisory",
        "repair_advisory": "director.repair.advisory",
        "repair_rule_suggestion": "director.repair.advisory",
        "suggest_repair_rule": "director.repair.advisory",
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


def _resident_agi_capability_by_id(audit_pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capability_surface_raw = audit_pack.get("capability_surface")
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    items_raw = capability_surface.get("items")
    items = items_raw if isinstance(items_raw, list) else []
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        capability_id = str(item.get("capability_id") or "").strip()
        if capability_id:
            result[capability_id] = dict(item)
    return result


def _resident_agi_interface_base(
    *,
    interface_id: str,
    capability: dict[str, Any] | None,
) -> dict[str, Any]:
    capability_payload = dict(capability or {})
    return {
        "interface_id": interface_id,
        "capability": capability_payload,
        "name": str(capability_payload.get("name") or interface_id).strip() or interface_id,
        "category": str(capability_payload.get("category") or "unknown").strip() or "unknown",
        "access": str(capability_payload.get("access") or "unknown").strip() or "unknown",
        "contract_ref": str(capability_payload.get("contract_ref") or "").strip(),
        "risk_level": str(capability_payload.get("risk_level") or "unknown").strip() or "unknown",
        "endpoint": str(capability_payload.get("endpoint") or "").strip(),
        "available": False,
        "callable": False,
        "status": "unknown_interface" if not capability_payload else "metadata_only",
        "source": "resident.agi_capability_surface",
        "summary": {},
        "payload": {},
        "gaps": [],
        "recommended_next_action": "request_evidence",
    }


def _resident_agi_run_ledger_interface(
    *,
    workspace: str,
    run_id: str,
    max_runs: int,
    base: dict[str, Any],
) -> dict[str, Any]:
    try:
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=workspace,
                run_id=run_id,
                max_runs=max_runs,
            )
        ).projection
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.run_ledger.public.read_run_ledger_projection",
                "gaps": [str(exc)],
                "recommended_next_action": "request_run_ledger_evidence",
            }
        )
        return base

    base.update(
        {
            "available": bool(projection.get("available")),
            "callable": True,
            "status": "available" if bool(projection.get("available")) else "unavailable",
            "source": "control_plane.run_ledger.public.read_run_ledger_projection",
            "summary": {
                "ok": bool(projection.get("ok")),
                "status": str(projection.get("status") or ""),
                "total": int(projection.get("total") or 0),
                "projected": int(projection.get("projected") or 0),
                "failed": int(projection.get("failed") or 0),
                "missing": int(projection.get("missing") or 0),
                "detail": str(projection.get("detail") or ""),
                "evidence_policy": projection.get("evidence_policy")
                if isinstance(projection.get("evidence_policy"), dict)
                else {},
            },
            "payload": {"projection": projection},
            "gaps": [] if bool(projection.get("available")) else ["run ledger projection is not available yet"],
            "recommended_next_action": "use_run_ledger_projection"
            if bool(projection.get("available"))
            else "request_run_ledger_evidence",
        }
    )
    return base


def _resident_agi_run_provenance_bundle_interface(
    *,
    workspace: str,
    run_id: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    if not str(run_id or "").strip():
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.run_ledger.public.read_run_provenance_bundle",
                "gaps": ["run_id is required to read a run provenance bundle"],
                "recommended_next_action": "request_run_id_or_run_ledger_evidence",
            }
        )
        return base
    try:
        bundle = read_run_provenance_bundle(
            ReadRunProvenanceBundleQueryV1(
                workspace=workspace,
                run_id=run_id,
            )
        ).bundle
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.run_ledger.public.read_run_provenance_bundle",
                "gaps": [str(exc)],
                "recommended_next_action": "request_run_provenance_evidence",
            }
        )
        return base

    missing_authority_hashes = [
        key
        for key in (
            "pm_contract_hash",
            "ce_blueprint_hash",
            "handoff_decision_hash",
            "execution_envelope_hash",
        )
        if str(bundle.get(key) or "").startswith("missing:")
    ]
    available = bool(bundle.get("bundle_id")) and not missing_authority_hashes
    base.update(
        {
            "available": available,
            "callable": True,
            "status": "available" if available else "empty",
            "source": "control_plane.run_ledger.public.read_run_provenance_bundle",
            "summary": {
                "bundle_id": str(bundle.get("bundle_id") or ""),
                "run_id": str(bundle.get("run_id") or ""),
                "task_id": str(bundle.get("task_id") or ""),
                "status": str(bundle.get("status") or ""),
                "final_status": str(bundle.get("final_status") or ""),
                "final_provider_request_count": len(bundle.get("final_provider_request_hashes") or []),
                "tool_receipt_count": len(bundle.get("tool_receipt_hashes") or []),
                "command_receipt_count": len(bundle.get("command_receipt_hashes") or []),
                "missing_authority_hashes": missing_authority_hashes,
            },
            "payload": {"bundle": bundle},
            "gaps": missing_authority_hashes,
            "recommended_next_action": "use_run_provenance_bundle"
            if available
            else "request_missing_provenance_evidence",
        }
    )
    return base


def _resident_agi_verifier_policy_interface(
    *,
    workspace: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    try:
        policy = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=workspace)).policy
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "control_plane.verifier_policy.public.read_verifier_policy",
                "gaps": [str(exc)],
                "recommended_next_action": "request_verifier_policy_evidence",
            }
        )
        return base

    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "control_plane.verifier_policy.public.read_verifier_policy",
            "summary": {
                "enabled_modalities": list(policy.get("enabled_modalities") or []),
                "required_modalities": list(policy.get("required_modalities") or []),
                "policy_source": str(policy.get("source") or ""),
            },
            "payload": {"policy": policy},
            "gaps": [],
            "recommended_next_action": "use_verifier_policy_snapshot",
        }
    )
    return base


def _resident_agi_director_repair_strategy_catalog_interface(base: dict[str, Any]) -> dict[str, Any]:
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1())
    payload = result.to_dict()
    summary_raw = payload.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "director.runtime.public.query_director_repair_strategy_catalog",
            "summary": {
                **summary,
                "schema_version": payload.get("schema_version"),
                "owner_cell": payload.get("owner_cell"),
                "access": payload.get("access"),
                "execution_boundary": payload.get("execution_boundary"),
                "chain": payload.get("chain"),
                "agi_execution_authority": bool(payload.get("agi_execution_authority")),
                "director_tool_execution_required": bool(payload.get("director_tool_execution_required")),
                "unknown_source_tool_policy": payload.get("unknown_source_tool_policy") or "fail_closed_high_risk",
            },
            "payload": payload,
            "gaps": [],
            "recommended_next_action": "use_director_repair_strategy_catalog_as_read_only_evidence",
        }
    )
    return base


def _resident_agi_audit_diagnosis_interface(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    result = query_audit_diagnosis_trail(
        QueryAuditDiagnosisTrailV1(
            workspace=workspace,
            run_id=run_id or None,
            task_id=task_id or None,
            limit=100,
        )
    )
    payload = dict(result.payload)
    base.update(
        {
            "available": bool(result.ok),
            "callable": True,
            "status": result.status if result.ok else "unavailable",
            "source": "audit.diagnosis.public.query_audit_diagnosis_trail",
            "summary": {
                "ok": result.ok,
                "status": result.status,
                "total": int(payload.get("total") or 0),
                "run_id": str(payload.get("run_id") or ""),
                "task_id": str(payload.get("task_id") or ""),
            },
            "payload": payload,
            "gaps": []
            if result.ok
            else [str(result.error_message or result.error_code or "audit diagnosis trail unavailable")],
            "recommended_next_action": "use_audit_diagnosis_trail" if result.ok else "request_audit_diagnosis_evidence",
        }
    )
    return base


def _resident_agi_audit_verdict_interface(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    result = query_audit_verdict(
        QueryAuditVerdictV1(
            workspace=workspace,
            run_id=run_id or None,
            task_id=task_id or None,
            include_artifacts=True,
        )
    )
    details = dict(result.details)
    base.update(
        {
            "available": bool(result.ok),
            "callable": True,
            "status": result.status if result.ok else "unavailable",
            "source": "audit.verdict.public.query_audit_verdict",
            "summary": {
                "ok": result.ok,
                "status": result.status,
                "verdict": result.verdict or "",
                "change_count": int(details.get("change_count") or 0),
                "review_count": int(details.get("review_count") or 0),
                "task_review_status": str(details.get("task_review_status") or ""),
            },
            "payload": {"details": details, "verdict": result.verdict},
            "gaps": []
            if result.ok
            else [str(result.error_message or result.error_code or "audit verdict unavailable")],
            "recommended_next_action": "use_audit_verdict_snapshot" if result.ok else "request_audit_verdict_evidence",
        }
    )
    return base


def _resident_agi_context_catalog_interface(
    *,
    workspace: str,
    decision_type: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    query_text = " ".join(
        str(token or "").strip()
        for token in (
            decision_type,
            base.get("contract_ref"),
            base.get("category"),
            base.get("name"),
        )
        if str(token or "").strip()
    )
    try:
        result = ContextCatalogService(workspace).search(SearchCellsQueryV1(query=query_text, limit=5))
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "context.catalog.public.ContextCatalogService.search",
                "gaps": [str(exc)],
                "recommended_next_action": "sync_context_catalog_before_search",
            }
        )
        return base

    descriptors = [
        {
            "cell_id": item.cell_id,
            "title": item.title,
            "purpose": item.purpose,
            "domain": item.domain,
            "kind": item.kind,
            "visibility": item.visibility,
            "stateful": item.stateful,
            "owner": item.owner,
            "capability_summary": item.capability_summary,
        }
        for item in result.descriptors
    ]
    base.update(
        {
            "available": result.total > 0,
            "callable": True,
            "status": "available" if result.total > 0 else "empty",
            "source": "context.catalog.public.ContextCatalogService.search",
            "summary": {"query": query_text, "total": result.total},
            "payload": {"descriptors": descriptors},
            "gaps": [] if result.total > 0 else ["context catalog has no matching descriptors"],
            "recommended_next_action": "use_catalog_descriptors"
            if result.total > 0
            else "sync_context_catalog_before_search",
        }
    )
    return base


def _resident_agi_context_engine_interface(
    *,
    workspace: str,
    decision_type: str,
    run_id: str,
    task_id: str,
    context_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    base: dict[str, Any],
) -> dict[str, Any]:
    query_text = " ".join(
        _merge_non_empty_strings(
            [
                decision_type,
                task_id,
                str(base.get("contract_ref") or ""),
                str(base.get("category") or ""),
                str(base.get("name") or ""),
            ],
            list(context_refs),
            list(evidence_refs),
        )
    )
    try:
        context_payload = get_anthropomorphic_context_v2(
            project_root=workspace,
            role="resident_agi",
            query=query_text or "Resident AGI evidence resolution",
            step=0,
            run_id=run_id or "resident-agi-evidence",
            phase=f"resident_agi_{decision_type or 'evidence'}",
        )
    except (ContextEngineError, RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "status": "unavailable",
                "source": "context.engine.public.get_anthropomorphic_context_v2",
                "gaps": [str(exc)],
                "recommended_next_action": "request_context_engine_snapshot_or_catalog_search",
            }
        )
        return base

    context_text = str(context_payload.get("anthropomorphic_context") or "")
    context_os_summary_raw = context_payload.get("context_os_summary")
    context_os_summary = context_os_summary_raw if isinstance(context_os_summary_raw, dict) else {}
    context_pack = context_payload.get("context_pack")
    raw_items = getattr(context_pack, "items", ()) if context_pack is not None else ()
    raw_item_list = list(raw_items or ())
    context_items = [
        {
            "id": str(getattr(item, "id", "") or ""),
            "kind": str(getattr(item, "kind", "") or ""),
            "provider": str(getattr(item, "provider", "") or ""),
            "priority": getattr(item, "priority", None),
            "reason": str(getattr(item, "reason", "") or ""),
        }
        for item in raw_item_list[:8]
    ]
    prompt_context = context_payload.get("prompt_context_obj")
    prompt_context_payload = {
        "run_id": str(getattr(prompt_context, "run_id", "") or ""),
        "phase": str(getattr(prompt_context, "phase", "") or ""),
        "step": getattr(prompt_context, "step", None),
        "persona_id": str(getattr(prompt_context, "persona_id", "") or ""),
        "token_usage_estimate": getattr(prompt_context, "token_usage_estimate", None),
    }
    available = bool(context_text or context_items or context_os_summary)
    base.update(
        {
            "available": available,
            "callable": True,
            "status": "available" if available else "empty",
            "source": "context.engine.public.get_anthropomorphic_context_v2",
            "summary": {
                "role": "resident_agi",
                "query": query_text[:240],
                "context_item_count": len(raw_item_list),
                "context_preview_chars": min(len(context_text), 1200),
                "context_os_current_goal": str(context_os_summary.get("current_goal") or ""),
                "token_usage_estimate": prompt_context_payload["token_usage_estimate"],
            },
            "payload": {
                "context_os_summary": context_os_summary,
                "prompt_context": prompt_context_payload,
                "context_items": context_items,
                "anthropomorphic_context_preview": context_text[:1200],
            },
            "gaps": [] if available else ["context engine returned no role context items"],
            "recommended_next_action": "use_resolved_role_context" if available else "request_context_catalog_search",
        }
    )
    return base


def _resident_agi_read_json_artifact(
    *,
    workspace: str,
    relative_path: str,
) -> tuple[dict[str, Any], str, str]:
    try:
        resolved = resolve_artifact_path(workspace, "", relative_path)
    except (RuntimeError, ValueError, OSError) as exc:
        return {}, "", str(exc)
    if not resolved:
        return {}, "", "artifact path could not be resolved"
    path = Path(resolved)
    if not path.is_file():
        return {}, str(path), ""
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {}, str(path), str(exc)
    if not isinstance(payload, dict):
        return {}, str(path), "artifact payload is not a JSON object"
    return payload, str(path), ""


def _resident_agi_chief_engineer_blueprint_interface(
    *,
    workspace: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    payload, path, error = _resident_agi_read_json_artifact(
        workspace=workspace,
        relative_path="runtime/contracts/chief_engineer.blueprint.json",
    )
    if error:
        base.update(
            {
                "callable": True,
                "status": "unavailable",
                "source": "runtime.artifact_store.resolve_artifact_path",
                "summary": {"path": path},
                "payload": {"path": path},
                "gaps": [error],
                "recommended_next_action": "repair_or_regenerate_chief_engineer_blueprint",
            }
        )
        return base
    if not payload:
        base.update(
            {
                "callable": True,
                "status": "empty",
                "source": "runtime.artifact_store.resolve_artifact_path",
                "summary": {"path": path},
                "payload": {"path": path},
                "gaps": ["runtime/contracts/chief_engineer.blueprint.json is not present"],
                "recommended_next_action": "run_chief_engineer_preflight_before_director_execution",
            }
        )
        return base

    task_updates = payload.get("task_updates")
    architecture_decisions = payload.get("architecture_decisions")
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "runtime/contracts/chief_engineer.blueprint.json",
            "summary": {
                "path": path,
                "schema_version": str(payload.get("schema_version") or ""),
                "role": str(payload.get("role") or payload.get("actor") or "ChiefEngineer"),
                "task_update_count": len(task_updates) if isinstance(task_updates, list) else 0,
                "architecture_decision_count": len(architecture_decisions)
                if isinstance(architecture_decisions, list)
                else 0,
                "reason": str(payload.get("reason") or ""),
                "summary": str(payload.get("summary") or "")[:240],
            },
            "payload": payload,
            "gaps": [],
            "recommended_next_action": "use_chief_engineer_blueprint_for_architecture_decision",
        }
    )
    return base


def _resident_agi_find_profile_payload(payload: Any) -> dict[str, Any]:
    stack: list[Any] = [payload]
    visited = 0
    while stack and visited < 500:
        visited += 1
        current = stack.pop()
        if isinstance(current, dict):
            for key in ("task_execution_profile", "director_execution_profile"):
                nested = current.get(key)
                if isinstance(nested, dict) and nested:
                    return dict(nested)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return {}


def _resident_agi_candidate_paths_from_refs(*refs: str) -> list[str]:
    paths: list[str] = []
    for ref in refs:
        value = str(ref or "").strip().replace("\\", "/")
        if not value or value.startswith(("http://", "https://")):
            continue
        if value.startswith(("runtime/", "workspace/", "config/")):
            continue
        if "/" not in value and "." not in value:
            continue
        paths.append(value)
    return paths[:12]


def _resident_agi_task_execution_profile_interface(
    *,
    workspace: str,
    decision_type: str,
    run_id: str,
    task_id: str,
    audit_pack: dict[str, Any],
    context_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    base: dict[str, Any],
) -> dict[str, Any]:
    existing_profile = _resident_agi_find_profile_payload(audit_pack)
    if existing_profile:
        profile_payload = existing_profile
        source = "resident.agi_audit_pack.task_execution_profile"
        computed_from_current_query = False
    else:
        refs = _merge_non_empty_strings(list(context_refs), list(evidence_refs))
        try:
            profile = resolve_task_execution_profile(
                subject=task_id or decision_type or "Resident AGI evidence query",
                description=" ".join(refs),
                metadata={
                    "source": "resident.agi_evidence_interface",
                    "decision_type": decision_type,
                    "run_id": run_id,
                    "task_id": task_id,
                },
                target_files=_resident_agi_candidate_paths_from_refs(*refs),
                workspace=workspace,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            base.update(
                {
                    "callable": True,
                    "status": "unavailable",
                    "source": "director.tasking.resolve_task_execution_profile",
                    "gaps": [str(exc)],
                    "recommended_next_action": "request_director_task_execution_profile_evidence",
                }
            )
            return base
        profile_payload = profile.to_dict()
        source = "director.tasking.resolve_task_execution_profile"
        computed_from_current_query = True

    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": source,
            "summary": {
                "schema_version": str(profile_payload.get("schema_version") or ""),
                "task_type": str(profile_payload.get("task_type") or ""),
                "phase": str(profile_payload.get("phase") or ""),
                "project_type": str(profile_payload.get("project_type") or ""),
                "language": str(profile_payload.get("language") or ""),
                "framework": str(profile_payload.get("framework") or ""),
                "temperature_phase": str(profile_payload.get("temperature_phase") or ""),
                "temperature": profile_payload.get("temperature"),
                "computed_from_current_query": computed_from_current_query,
            },
            "payload": {"profile": profile_payload},
            "gaps": [],
            "recommended_next_action": "use_task_execution_profile_for_prompt_temperature_and_output_contract",
        }
    )
    return base


def _resident_agi_runtime_events_interface(
    *,
    workspace: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    try:
        service = create_artifact_service(workspace)
        events = service.read_runtime_events(limit=50)
        events_path = service.get_runtime_events_path()
    except (RuntimeError, ValueError, OSError) as exc:
        base.update(
            {
                "callable": True,
                "status": "unavailable",
                "source": "audit.verdict.public.create_artifact_service.read_runtime_events",
                "gaps": [str(exc)],
                "recommended_next_action": "repair_runtime_events_artifact_or_use_run_ledger",
            }
        )
        return base

    available = bool(events)
    recent_event_types = [
        str(event.get("type") or event.get("event_type") or event.get("name") or "").strip()
        for event in events[-8:]
        if isinstance(event, dict)
    ]
    base.update(
        {
            "available": available,
            "callable": True,
            "status": "available" if available else "empty",
            "source": "audit.verdict.public.ArtifactService.read_runtime_events",
            "summary": {
                "path": events_path,
                "event_count": len(events),
                "recent_event_types": [item for item in recent_event_types if item],
            },
            "payload": {"path": events_path, "events": events},
            "gaps": [] if available else ["runtime/events/runtime.events.jsonl has no readable events"],
            "recommended_next_action": "use_runtime_event_stream_evidence"
            if available
            else "use_run_ledger_projection",
        }
    )
    return base


def _resident_agi_contextos_final_request_interface(
    *,
    workspace: str,
    audit_pack: dict[str, Any],
    base: dict[str, Any],
) -> dict[str, Any]:
    evidence_refs_raw = audit_pack.get("evidence_refs")
    evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []
    context_refs = resident_agi_context_snapshot_refs(evidence_refs)
    if not context_refs:
        base.update(
            {
                "available": False,
                "callable": True,
                "status": "metadata_only",
                "source": "context.engine.public.query_final_provider_request_audit",
                "summary": {
                    "context_snapshot_ref_count": 0,
                    "context_snapshot_refs": [],
                },
                "payload": {"context_snapshot_refs": []},
                "gaps": [
                    "no ContextOS snapshot hash or runtime/contexts/<hash> reference is present in the audit pack"
                ],
                "recommended_next_action": "request_final_request_snapshot",
            }
        )
        return base

    last_result_payload: dict[str, Any] = {}
    last_error = ""
    for context_ref in context_refs:
        result = query_final_provider_request_audit(
            QueryFinalProviderRequestAuditV1(
                workspace=workspace,
                context_snapshot_ref=context_ref,
            )
        )
        if result.ok:
            payload = dict(result.payload)
            final_audit = payload.get("final_request_context_audit")
            base.update(
                {
                    "available": True,
                    "callable": True,
                    "status": "available",
                    "source": "context.engine.public.query_final_provider_request_audit",
                    "summary": {
                        "context_snapshot_ref_count": len(context_refs),
                        "selected_context_snapshot_ref": context_ref,
                        "final_request_token_estimate": final_audit.get("final_request_token_estimate")
                        if isinstance(final_audit, dict)
                        else None,
                        "tool_schema_count": payload.get("provider_request", {}).get("tool_schema_count")
                        if isinstance(payload.get("provider_request"), dict)
                        else None,
                    },
                    "payload": payload,
                    "gaps": [],
                    "recommended_next_action": "use_final_provider_request_audit",
                }
            )
            return base
        last_result_payload = dict(result.payload)
        last_error = result.error_code or result.error_message or result.status

    base.update(
        {
            "available": False,
            "callable": True,
            "status": "unavailable",
            "source": "context.engine.public.query_final_provider_request_audit",
            "summary": {
                "context_snapshot_ref_count": len(context_refs),
                "context_snapshot_refs": context_refs[:10],
                "last_error": last_error,
            },
            "payload": {"context_snapshot_refs": context_refs, "last_result": last_result_payload},
            "gaps": ["no referenced context snapshot contains readable final provider request audit evidence"],
            "recommended_next_action": "request_final_request_snapshot",
        }
    )
    return base


def _resident_agi_director_repair_advisory_policy_interface(base: dict[str, Any]) -> dict[str, Any]:
    result = query_director_repair_advisory_policy(QueryDirectorRepairAdvisoryPolicyV1())
    payload = result.to_dict()
    summary_raw = payload.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "director.runtime.public.query_director_repair_advisory_policy",
            "summary": {
                **summary,
                "schema_version": payload.get("schema_version"),
                "owner_cell": payload.get("owner_cell"),
                "access": payload.get("access"),
                "execution_boundary": payload.get("execution_boundary"),
                "agi_execution_authority": bool(payload.get("agi_execution_authority")),
                "writes_allowed": bool(payload.get("writes_allowed")),
                "registration_allowed": bool(payload.get("registration_allowed")),
                "authoritative_receipts_allowed": bool(payload.get("authoritative_receipts_allowed")),
            },
            "payload": payload,
            "gaps": [],
            "recommended_next_action": "use_repair_advisory_policy_before_accepting_agi_suggested_rules",
        }
    )
    return base


def _resident_agi_repair_diagnostic_candidates(
    audit_pack: dict[str, Any],
    *,
    evidence_refs: tuple[str, ...] = (),
    context_refs: tuple[str, ...] = (),
) -> tuple[str, ...]:
    candidates: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            token = value.strip()
            if token and _looks_like_repair_diagnostic(token):
                candidates.append(token)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        for key, nested in value.items():
            key_text = str(key or "").lower()
            if key_text in {
                "artifact_quality_errors",
                "quality_errors",
                "diagnostics",
                "errors",
                "compiler_errors",
            } or key_text in {"actual_outcome", "expected_outcome", "verifier", "quality", "repair", "metadata"}:
                collect(nested)

    for ref in (*context_refs, *evidence_refs):
        collect(ref)
    collect(audit_pack.get("run_ledger_summary"))
    collect(audit_pack.get("recent_decisions"))
    return tuple(dict.fromkeys(candidates))[:50]


def _looks_like_repair_diagnostic(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "error ts",
            "ts1005",
            "error[",
            ".go:",
            "artifact_quality",
            "syntax check failed",
            "cannot find",
            "unresolved",
            "unlinked crate",
            "import path must be string",
        )
    )


def _resident_agi_director_repair_coverage_interface(
    *,
    audit_pack: dict[str, Any],
    context_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    base: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = _resident_agi_repair_diagnostic_candidates(
        audit_pack,
        context_refs=context_refs,
        evidence_refs=evidence_refs,
    )
    result = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    payload = result.to_dict()
    base.update(
        {
            "available": True,
            "callable": True,
            "status": "available",
            "source": "director.runtime.public.query_director_repair_coverage",
            "summary": {
                "schema_version": payload.get("schema_version"),
                "owner_cell": payload.get("owner_cell"),
                "access": payload.get("access"),
                "diagnostic_candidate_count": len(diagnostics),
                "total_diagnostics": payload.get("total_diagnostics"),
                "covered_diagnostic_count": payload.get("covered_diagnostic_count"),
                "uncovered_diagnostic_count": payload.get("uncovered_diagnostic_count"),
                "agi_execution_authority": bool(payload.get("agi_execution_authority")),
                "execution_boundary": payload.get("execution_boundary"),
            },
            "payload": payload,
            "gaps": []
            if diagnostics
            else ["no repair diagnostics were found in current AGI evidence refs or audit pack decisions"],
            "recommended_next_action": "use_repair_coverage_to_choose_retry_escalate_or_suggest_rule",
        }
    )
    return base


def _resident_agi_audit_pack_with_current_refs(
    audit_pack: dict[str, Any],
    *,
    context_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return an audit pack view that prioritizes refs from the current decision."""

    audit_refs_raw = audit_pack.get("evidence_refs")
    audit_refs: list[Any] = audit_refs_raw if isinstance(audit_refs_raw, list) else []
    merged_refs = _merge_non_empty_strings(
        list(context_refs),
        list(evidence_refs),
        audit_refs,
    )
    if merged_refs == audit_refs:
        return audit_pack
    payload = dict(audit_pack)
    payload["evidence_refs"] = merged_refs
    return payload


def _resident_agi_metadata_only_interface(base: dict[str, Any]) -> dict[str, Any]:
    access = str(base.get("access") or "").strip()
    contract_ref = str(base.get("contract_ref") or "").strip()
    if "execute" in access:
        base.update(
            {
                "status": "governed_execute_only",
                "source": "resident.agi_capability_surface",
                "gaps": ["this endpoint requires a governed command and is not executed by read-only evidence query"],
                "recommended_next_action": "request_governed_execution_if_read_evidence_is_insufficient",
            }
        )
        return base
    if contract_ref in {"audit.diagnosis", "audit.verdict", "audit.evidence.bundle", "context.engine"}:
        base.update(
            {
                "status": "needs_public_facade",
                "source": "resident.agi_capability_surface",
                "gaps": [f"{contract_ref} has no safe Resident AGI read facade wired here yet"],
                "recommended_next_action": "request_platform_facade_or_use_existing_audit_pack_summary",
            }
        )
    return base


def _resident_agi_evidence_interface_group_id(interface: dict[str, Any]) -> str:
    interface_id = str(interface.get("interface_id") or "").strip()
    category = str(interface.get("category") or "").strip()
    contract_ref = str(interface.get("contract_ref") or "").strip()
    if interface_id.startswith("director.") or category.startswith("director_repair"):
        return "director_repair"
    if interface_id.startswith("verifier.") or contract_ref.startswith("control_plane.verifier"):
        return "verifier"
    if interface_id.startswith("audit.") or contract_ref.startswith("audit."):
        return "audit"
    if interface_id.startswith("context.") or contract_ref.startswith("context."):
        return "context"
    if interface_id.startswith("contextos.") or contract_ref == "roles.final_request_context_audit":
        return "llm_context"
    if (
        interface_id.startswith("run_ledger.")
        or interface_id.startswith("run_provenance_bundle.")
        or contract_ref in {"control_plane.run_ledger", "control_plane.run_provenance_bundle"}
    ):
        return "run_ledger"
    if "execute" in str(interface.get("access") or "").strip().lower():
        return "governed_execution"
    return "other"


def _resident_agi_evidence_group_name(group_id: str) -> str:
    return {
        "audit": "Audit",
        "context": "Context",
        "director_repair": "Director repair",
        "governed_execution": "Governed execution",
        "llm_context": "LLM context",
        "run_ledger": "Run ledger",
        "verifier": "Verifier",
        "other": "Other",
    }.get(group_id, group_id)


def _resident_agi_evidence_capability_matrix(
    *,
    decision_type: str,
    selected_decision_capability: dict[str, Any],
    interfaces: list[dict[str, Any]],
    required_interface_ids: list[str],
    optional_interface_ids: list[str],
    audit_pack: dict[str, Any],
) -> dict[str, Any]:
    decision_profile_raw = audit_pack.get("decision_profile")
    decision_profile: dict[str, Any] = decision_profile_raw if isinstance(decision_profile_raw, dict) else {}
    recommendations_raw = decision_profile.get("evidence_interface_recommendations")
    recommendations = recommendations_raw if isinstance(recommendations_raw, list) else []
    recommendation_by_id = {
        str(item.get("capability_id") or "").strip(): item
        for item in recommendations
        if isinstance(item, dict) and str(item.get("capability_id") or "").strip()
    }
    required_set = set(required_interface_ids)
    optional_set = set(optional_interface_ids)
    rows: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    missing_required_interface_ids: list[str] = []
    status_counts: dict[str, int] = {}

    for interface in interfaces:
        interface_id = str(interface.get("interface_id") or "").strip()
        status = str(interface.get("status") or "unknown").strip() or "unknown"
        available = bool(interface.get("available")) or status == "available"
        callable_now = bool(interface.get("callable"))
        access = str(interface.get("access") or "").strip()
        risk_level = str(interface.get("risk_level") or "").strip()
        group_id = _resident_agi_evidence_interface_group_id(interface)
        recommendation = recommendation_by_id.get(interface_id, {})
        required = interface_id in required_set
        optional = interface_id in optional_set
        recommended_now = bool(recommendation.get("recommended_now")) or required
        gaps_raw = interface.get("gaps")
        gaps = (
            [str(item or "").strip() for item in gaps_raw if str(item or "").strip()]
            if isinstance(gaps_raw, list)
            else []
        )

        if required and not available:
            missing_required_interface_ids.append(interface_id)
        status_counts[status] = status_counts.get(status, 0) + 1

        row = {
            "interface_id": interface_id,
            "name": str(interface.get("name") or interface_id).strip() or interface_id,
            "group_id": group_id,
            "group_name": _resident_agi_evidence_group_name(group_id),
            "required": required,
            "optional": optional,
            "recommended_now": recommended_now,
            "available": available,
            "callable": callable_now,
            "status": status,
            "source": str(interface.get("source") or "").strip(),
            "access": access,
            "risk_level": risk_level,
            "contract_ref": str(interface.get("contract_ref") or "").strip(),
            "recommended_next_action": str(interface.get("recommended_next_action") or "").strip(),
            "priority": int(recommendation.get("priority") or 100),
            "reason": str(recommendation.get("reason") or "").strip(),
            "gap_count": len(gaps),
            "gaps": gaps[:5],
        }
        rows.append(row)

        group = groups.setdefault(
            group_id,
            {
                "group_id": group_id,
                "name": _resident_agi_evidence_group_name(group_id),
                "interface_ids": [],
                "total": 0,
                "available": 0,
                "required": 0,
                "missing_required": 0,
                "recommended_now": 0,
                "high_risk": 0,
                "governed_execute": 0,
            },
        )
        group["interface_ids"].append(interface_id)
        group["total"] += 1
        group["available"] += 1 if available else 0
        group["required"] += 1 if required else 0
        group["missing_required"] += 1 if required and not available else 0
        group["recommended_now"] += 1 if recommended_now else 0
        group["high_risk"] += 1 if risk_level.lower() == "high" else 0
        group["governed_execute"] += 1 if "execute" in access.lower() else 0

    return {
        "schema_version": "resident.agi_evidence_capability_matrix.v1",
        "workspace_evidence_source": "resident.autonomy.public.query_resident_agi_evidence_interfaces",
        "decision_type": decision_type,
        "selected_decision_id": str(selected_decision_capability.get("decision_id") or decision_type).strip(),
        "rows": sorted(
            rows, key=lambda item: (int(item["priority"]), str(item["group_id"]), str(item["interface_id"]))
        ),
        "groups": sorted(groups.values(), key=lambda item: str(item["group_id"])),
        "summary": {
            "total": len(rows),
            "available": sum(1 for item in rows if bool(item["available"])),
            "required": len(required_set),
            "required_available": sum(1 for item in rows if bool(item["required"]) and bool(item["available"])),
            "missing_required": len(missing_required_interface_ids),
            "missing_required_interface_ids": missing_required_interface_ids,
            "recommended_now": sum(1 for item in rows if bool(item["recommended_now"])),
            "callable": sum(1 for item in rows if bool(item["callable"])),
            "high_risk": sum(1 for item in rows if str(item["risk_level"]).lower() == "high"),
            "governed_execute": sum(1 for item in rows if "execute" in str(item["access"]).lower()),
            "status_counts": status_counts,
            "advisory_only": True,
            "authoritative": False,
            "agi_execution_authority": False,
        },
    }


def query_resident_agi_evidence_interfaces(query: QueryResidentAgiEvidenceInterfacesV1) -> dict[str, Any]:
    """Return the evidence interfaces a Resident AGI turn can safely inspect."""

    status_payload = get_resident_service(query.workspace).get_status(include_details=True)
    audit_pack = build_resident_agi_audit_pack(
        workspace=query.workspace,
        status_payload=status_payload,
        decision_limit=query.decision_limit,
    )
    audit_pack = _resident_agi_audit_pack_with_current_refs(
        audit_pack,
        context_refs=query.context_refs,
        evidence_refs=query.evidence_refs,
    )
    selected_decision_capability = _resident_agi_select_decision_capability(
        decision_type=query.decision_type,
        audit_pack=audit_pack,
    )
    selected_required_interfaces = [
        str(item or "").strip()
        for item in selected_decision_capability.get("required_evidence_interfaces", [])
        if str(item or "").strip()
    ]
    selected_optional_interfaces = [
        str(item or "").strip()
        for item in selected_decision_capability.get("optional_evidence_interfaces", [])
        if str(item or "").strip()
    ]
    requested_interface_ids = _merge_non_empty_strings(
        list(query.interface_ids),
        selected_required_interfaces if not query.interface_ids else [],
        selected_optional_interfaces if not query.interface_ids else [],
    )
    capability_by_id = _resident_agi_capability_by_id(audit_pack)
    interfaces: list[dict[str, Any]] = []
    for interface_id in requested_interface_ids:
        base = _resident_agi_interface_base(
            interface_id=interface_id,
            capability=capability_by_id.get(interface_id),
        )
        if interface_id == "run_ledger.read":
            item = _resident_agi_run_ledger_interface(
                workspace=query.workspace,
                run_id=query.run_id,
                max_runs=query.max_runs,
                base=base,
            )
        elif interface_id == "run_provenance_bundle.read":
            item = _resident_agi_run_provenance_bundle_interface(
                workspace=query.workspace,
                run_id=query.run_id,
                base=base,
            )
        elif interface_id == "audit.diagnosis.read":
            item = _resident_agi_audit_diagnosis_interface(
                workspace=query.workspace,
                run_id=query.run_id,
                task_id=query.task_id,
                base=base,
            )
        elif interface_id == "audit.verdict.read":
            item = _resident_agi_audit_verdict_interface(
                workspace=query.workspace,
                run_id=query.run_id,
                task_id=query.task_id,
                base=base,
            )
        elif interface_id == "verifier.policy.read":
            item = _resident_agi_verifier_policy_interface(workspace=query.workspace, base=base)
        elif interface_id == "context.catalog.search":
            item = _resident_agi_context_catalog_interface(
                workspace=query.workspace,
                decision_type=query.decision_type,
                base=base,
            )
        elif interface_id == "context.engine.resolve":
            item = _resident_agi_context_engine_interface(
                workspace=query.workspace,
                decision_type=query.decision_type,
                run_id=query.run_id,
                task_id=query.task_id,
                context_refs=query.context_refs,
                evidence_refs=query.evidence_refs,
                base=base,
            )
        elif interface_id == "contextos.final_request_audit.read":
            item = _resident_agi_contextos_final_request_interface(
                workspace=query.workspace,
                audit_pack=audit_pack,
                base=base,
            )
        elif interface_id == "task.execution_profile.read":
            item = _resident_agi_task_execution_profile_interface(
                workspace=query.workspace,
                decision_type=query.decision_type,
                run_id=query.run_id,
                task_id=query.task_id,
                audit_pack=audit_pack,
                context_refs=query.context_refs,
                evidence_refs=query.evidence_refs,
                base=base,
            )
        elif interface_id == "chief_engineer.blueprint.read":
            item = _resident_agi_chief_engineer_blueprint_interface(
                workspace=query.workspace,
                base=base,
            )
        elif interface_id == "director.deterministic_repair_strategy_catalog.read":
            item = _resident_agi_director_repair_strategy_catalog_interface(base)
        elif interface_id == "director.repair_coverage.read":
            item = _resident_agi_director_repair_coverage_interface(
                audit_pack=audit_pack,
                context_refs=query.context_refs,
                evidence_refs=query.evidence_refs,
                base=base,
            )
        elif interface_id == "director.repair_advisory_policy.read":
            item = _resident_agi_director_repair_advisory_policy_interface(base)
        elif interface_id == "runtime.events.read":
            item = _resident_agi_runtime_events_interface(
                workspace=query.workspace,
                base=base,
            )
        else:
            item = _resident_agi_metadata_only_interface(base)
        interfaces.append(item)

    required_set = set(selected_required_interfaces)
    missing_required = [
        str(item.get("interface_id") or "")
        for item in interfaces
        if str(item.get("interface_id") or "") in required_set and str(item.get("status") or "") != "available"
    ]
    status_counts: dict[str, int] = {}
    for item in interfaces:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    capability_matrix = _resident_agi_evidence_capability_matrix(
        decision_type=query.decision_type,
        selected_decision_capability=selected_decision_capability,
        interfaces=interfaces,
        required_interface_ids=selected_required_interfaces,
        optional_interface_ids=selected_optional_interfaces,
        audit_pack=audit_pack,
    )
    return {
        "schema_version": "resident.agi_evidence_interfaces.v1",
        "workspace": query.workspace,
        "decision_type": query.decision_type,
        "run_id": query.run_id,
        "task_id": query.task_id,
        "context_refs": list(query.context_refs),
        "evidence_refs": list(query.evidence_refs),
        "selected_decision_capability": selected_decision_capability,
        "required_evidence_interfaces": selected_required_interfaces,
        "optional_evidence_interfaces": selected_optional_interfaces,
        "requested_interface_ids": requested_interface_ids,
        "interfaces": interfaces,
        "capability_matrix": capability_matrix,
        "summary": {
            "total": len(interfaces),
            "available": status_counts.get("available", 0),
            "metadata_only": status_counts.get("metadata_only", 0),
            "needs_public_facade": status_counts.get("needs_public_facade", 0),
            "governed_execute_only": status_counts.get("governed_execute_only", 0),
            "unavailable": status_counts.get("unavailable", 0),
            "empty": status_counts.get("empty", 0),
            "unknown_interface": status_counts.get("unknown_interface", 0),
            "missing_required_interface_ids": missing_required,
        },
        "audit_pack_ref": {
            "schema_version": audit_pack.get("schema_version"),
            "evidence_gate_status": (audit_pack.get("evidence_gate") or {}).get("status")
            if isinstance(audit_pack.get("evidence_gate"), dict)
            else "",
            "hard_rule_gate_status": (audit_pack.get("hard_rule_gate") or {}).get("status")
            if isinstance(audit_pack.get("hard_rule_gate"), dict)
            else "",
        },
    }


def query_resident_agi_tactical_chat(query: QueryResidentAgiTacticalChatV1) -> dict[str, Any]:
    """Return a tactical-console response backed by Resident AGI public evidence."""

    status_payload = query_resident_status(QueryResidentStatusV1(workspace=query.workspace), include_details=True)
    audit_pack = query_resident_agi_audit_pack(
        QueryResidentAgiAuditPackV1(workspace=query.workspace, decision_limit=query.decision_limit)
    )
    evidence_interfaces = query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=query.workspace,
            decision_type=query.decision_type,
            run_id=query.run_id,
            task_id=query.task_id,
            context_refs=query.context_refs,
            evidence_refs=query.evidence_refs,
            decision_limit=query.decision_limit,
            max_runs=query.max_runs,
        )
    )
    repair_overlay_query = query_resident_agi_repair_advisory_overlay(
        QueryResidentAgiRepairAdvisoryOverlayV1(workspace=query.workspace, limit=query.decision_limit)
    )
    return build_resident_agi_tactical_chat_response(
        workspace=query.workspace,
        message=query.message,
        context_refs=query.context_refs,
        evidence_refs=query.evidence_refs,
        status_payload=status_payload,
        audit_pack=audit_pack,
        evidence_interfaces=evidence_interfaces,
        repair_overlay_query=repair_overlay_query,
    )


def query_resident_agi_tactical_action_catalog() -> dict[str, Any]:
    """Return the read-only Resident AGI tactical-console action catalog."""

    return resident_agi_tactical_action_catalog()


def _resident_agi_tactical_action_tool_trace(
    *,
    action_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    for item in items:
        status_key = str(item.get("status") or "unknown").strip() or "unknown"
        mode_key = str(item.get("mode") or "unknown").strip() or "unknown"
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        mode_counts[mode_key] = mode_counts.get(mode_key, 0) + 1
    return {
        "schema_version": "resident.agi_tactical_action_tool_trace.v1",
        "source": "resident.autonomy.public.execute_resident_agi_tactical_action",
        "action_id": action_id,
        "items": items,
        "summary": {
            "total": len(items),
            "by_status": dict(sorted(status_counts.items())),
            "by_mode": dict(sorted(mode_counts.items())),
            "direct_execution_allowed": False,
            "agi_direct_repair_allowed": False,
            "required_chain": "PM → Chief Engineer → Director → QA",
        },
    }


def _resident_agi_tactical_follow_up_actions(
    *,
    action_id: str,
    chat: dict[str, Any],
    verdict: str = "",
    created_goal: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(action: dict[str, Any]) -> None:
        action_key = str(action.get("action_id") or "").strip()
        if not action_key or action_key in seen:
            return
        seen.add(action_key)
        actions.append(action)

    add(
        resident_agi_tactical_action_payload(
            "open_evidence_black_box",
            status="available",
            reason="查看本次 AGI 动作使用过的审计证据和契约轨迹。",
        )
    )
    normalized_verdict = str(verdict or "").strip().lower()
    if normalized_verdict == "request_evidence":
        add(
            resident_agi_tactical_action_payload(
                "refresh_evidence_interfaces",
                status="available",
                reason="AGI 判断需要更多证据；刷新 evidence interface read model。",
            )
        )

    if action_id == "request_resident_agi_judgement" and normalized_verdict in {
        "block",
        "escalate",
        "request_evidence",
    }:
        chat_actions_raw = chat.get("suggested_actions")
        chat_actions = chat_actions_raw if isinstance(chat_actions_raw, list) else []
        for item in chat_actions:
            if isinstance(item, dict) and str(item.get("action_id") or "") == "request_director_controlled_repair":
                add(dict(item))
                break

    if action_id == "request_director_controlled_repair" and created_goal:
        add(resident_agi_tactical_action_payload("open_goals_tab", status="available"))
        add(
            resident_agi_tactical_action_payload(
                "request_resident_agi_judgement",
                status="preview_only",
                reason="让 resident_agi 角色回合基于新目标和证据再判断下一步。",
            )
        )
    return actions


async def execute_resident_agi_tactical_action(command: ExecuteResidentAgiTacticalActionCommandV1) -> dict[str, Any]:
    """Execute a governed Resident AGI tactical-console action."""

    chat = query_resident_agi_tactical_chat(
        QueryResidentAgiTacticalChatV1(
            workspace=command.workspace,
            message=command.message,
            decision_type=command.decision_type,
            run_id=command.run_id,
            task_id=command.task_id,
            goal_id=command.goal_id,
            context=command.context,
            context_refs=command.context_refs,
            evidence_refs=command.evidence_refs,
            decision_limit=command.decision_limit,
            max_runs=command.max_runs,
        )
    )
    actions_raw = chat.get("suggested_actions")
    actions = actions_raw if isinstance(actions_raw, list) else []
    selected_action = next(
        (
            dict(item)
            for item in actions
            if isinstance(item, dict) and str(item.get("action_id") or "").strip() == command.action_id
        ),
        {},
    )
    action_spec = resident_agi_tactical_action_spec(command.action_id)
    action_spec_payload = action_spec.to_catalog_item() if action_spec else None
    action_catalog_raw = chat.get("action_catalog")
    action_catalog = (
        action_catalog_raw if isinstance(action_catalog_raw, dict) else resident_agi_tactical_action_catalog()
    )
    if not selected_action:
        return {
            "schema_version": "resident.agi_tactical_action_result.v1",
            "workspace": command.workspace,
            "action_id": command.action_id,
            "action_spec": action_spec_payload,
            "status": "blocked",
            "reason": "action is not available under current Resident AGI evidence and participation policy",
            "chat": chat,
            "goal": None,
            "decision": None,
            "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                action_id=command.action_id,
                chat=chat,
            ),
            "tool_trace": _resident_agi_tactical_action_tool_trace(
                action_id=command.action_id,
                items=[
                    {
                        "step_id": "resident.agi_tactical_chat.revalidate",
                        "label": "重新读取战术上下文",
                        "mode": "read_only",
                        "status": "available",
                        "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                        "summary": "执行前重新读取 Resident AGI public facts。",
                    },
                    {
                        "step_id": "resident.agi_action.policy_gate",
                        "label": "动作策略门禁",
                        "mode": "policy_gate",
                        "status": "blocked",
                        "contract": "resident.agi_tactical_chat_participation.v1",
                        "summary": "当前 action 未出现在后端建议动作列表中。",
                    },
                ],
            ),
            "receipt": {
                "schema_version": "resident.agi_tactical_action_receipt.v1",
                "status": "BLOCKED",
                "title": "受控动作阻断凭证",
                "summary": "后端重新读取事实源后，当前 action 不可执行。",
            },
            "policy": {
                "advisory_only": True,
                "agi_direct_repair_allowed": False,
                "required_chain": "PM → Chief Engineer → Director → QA",
            },
        }

    evidence_refs_raw = chat.get("evidence_refs")
    chat_evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []
    context_refs_raw = chat.get("context_refs")
    chat_context_refs = context_refs_raw if isinstance(context_refs_raw, list) else []

    if command.action_id == "request_resident_agi_judgement":
        evidence_refs = _merge_non_empty_strings(list(command.evidence_refs), chat_evidence_refs)
        context_refs = _merge_non_empty_strings(list(command.context_refs), chat_context_refs)
        try:
            decision_turn_result = await run_resident_agi_decision_turn(
                RunResidentAgiDecisionTurnCommandV1(
                    workspace=command.workspace,
                    objective=f"Resident AGI tactical-console judgement request: {command.message}",
                    decision_type=command.decision_type,
                    run_id=command.run_id,
                    task_id=command.task_id,
                    goal_id=command.goal_id,
                    evidence={
                        "source": "resident_agi_tactical_console",
                        "action_id": command.action_id,
                        "selected_action_spec": action_spec_payload or {},
                        "selected_action": selected_action,
                        "available_tactical_actions": actions,
                        "tactical_action_catalog": action_catalog,
                        "chat_intent": chat.get("intent"),
                        "chat_status": chat.get("status"),
                        "chat_policy": chat.get("policy"),
                        "chat_facts": chat.get("facts"),
                        "user_context": dict(command.context),
                    },
                    constraints=(
                        "preserve_pm_chief_engineer_director_qa_chain",
                        "do_not_mark_failed_gates_as_passed",
                        "do_not_execute_direct_writes_or_direct_repairs",
                        "use_public_cell_contracts_only",
                    ),
                    candidate_actions=("continue", "block", "request_evidence", "escalate"),
                    context_refs=tuple(context_refs),
                    evidence_refs=tuple(evidence_refs),
                    confidence=0.55,
                    include_audit_pack=True,
                    audit_pack_decision_limit=command.decision_limit,
                )
            )
        except Exception as exc:
            logger.exception("execute_resident_agi_tactical_action judgement failed: %s", exc)
            return {
                "schema_version": "resident.agi_tactical_action_result.v1",
                "workspace": command.workspace,
                "action_id": command.action_id,
                "action_spec": action_spec_payload,
                "status": "blocked",
                "reason": f"Resident AGI judgement failed before producing a governed decision: {exc}",
                "chat": chat,
                "goal": None,
                "decision": None,
                "role_result": None,
                "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                    action_id=command.action_id,
                    chat=chat,
                ),
                "tool_trace": _resident_agi_tactical_action_tool_trace(
                    action_id=command.action_id,
                    items=[
                        {
                            "step_id": "resident.agi_tactical_chat.revalidate",
                            "label": "重新读取战术上下文",
                            "mode": "read_only",
                            "status": "available",
                            "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                            "summary": "执行前重新读取 Resident AGI public facts。",
                        },
                        {
                            "step_id": "resident.agi_decision_turn.execute",
                            "label": "AGI 判断回合",
                            "mode": "execute_through_role_runtime",
                            "status": "failed",
                            "contract": "resident.autonomy.public.run_resident_agi_decision_turn",
                            "summary": "角色回合失败，按 fail-closed 返回阻断凭证。",
                        },
                    ],
                ),
                "receipt": {
                    "schema_version": "resident.agi_tactical_action_receipt.v1",
                    "status": "BLOCKED",
                    "title": "AGI 判断阻断凭证",
                    "summary": "Resident AGI 角色回合未能完成；未创建目标、未执行修复、未放行门禁。",
                    "rows": [
                        {"label": "动作", "value": command.action_id},
                        {"label": "边界", "value": "fail_closed"},
                    ],
                },
                "policy": {
                    "advisory_only": True,
                    "agi_direct_repair_allowed": False,
                    "required_chain": "PM → Chief Engineer → Director → QA",
                    "role_runtime_required": True,
                },
            }

        recorded_decision_raw = decision_turn_result.get("recorded_decision")
        recorded_decision = recorded_decision_raw if isinstance(recorded_decision_raw, dict) else None
        decision_raw = decision_turn_result.get("decision")
        decision = decision_raw if isinstance(decision_raw, dict) else {}
        verdict = str(decision.get("verdict") or (recorded_decision or {}).get("verdict") or "unknown")
        return {
            "schema_version": "resident.agi_tactical_action_result.v1",
            "workspace": command.workspace,
            "action_id": command.action_id,
            "action_spec": action_spec_payload,
            "status": "executed",
            "reason": "ran Resident AGI judgement through the shared role runtime contract",
            "chat": chat,
            "goal": None,
            "decision": recorded_decision,
            "role_result": decision_turn_result,
            "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                action_id=command.action_id,
                chat=chat,
                verdict=verdict,
            ),
            "tool_trace": _resident_agi_tactical_action_tool_trace(
                action_id=command.action_id,
                items=[
                    {
                        "step_id": "resident.agi_tactical_chat.revalidate",
                        "label": "重新读取战术上下文",
                        "mode": "read_only",
                        "status": "available",
                        "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                        "summary": "执行前重新读取 Resident AGI public facts。",
                    },
                    {
                        "step_id": "resident.agi_decision_turn.execute",
                        "label": "AGI 判断回合",
                        "mode": "execute_through_role_runtime",
                        "status": "executed",
                        "contract": "resident.autonomy.public.run_resident_agi_decision_turn",
                        "summary": f"resident_agi 角色回合产出 {verdict} 判断。",
                    },
                    {
                        "step_id": "resident.decision_trace.write",
                        "label": "写入决策轨迹",
                        "mode": "write_through_resident_contract",
                        "status": "recorded" if recorded_decision else "empty",
                        "contract": "resident.decision_trace",
                        "summary": "判断结果已进入 Resident decision trace。",
                    },
                ],
            ),
            "receipt": {
                "schema_version": "resident.agi_tactical_action_receipt.v1",
                "status": "JUDGED",
                "title": "AGI 判断凭证",
                "summary": "已通过 resident_agi 角色回合完成受控判断；未创建目标、未直接修复、未跳过门禁。",
                "rows": [
                    {"label": "结论", "value": verdict},
                    {"label": "决策", "value": str((recorded_decision or {}).get("decision_id") or "not_recorded")},
                    {"label": "动作", "value": command.action_id},
                    {"label": "角色回合", "value": "resident_agi"},
                ],
            },
            "policy": {
                "advisory_only": True,
                "agi_direct_repair_allowed": False,
                "required_chain": "PM → Chief Engineer → Director → QA",
                "role_runtime_required": True,
                "resident_agi_decision_endpoint": "/v2/resident/agi/decide",
            },
        }

    goal_draft_raw = selected_action.get("goal_draft")
    goal_draft = goal_draft_raw if isinstance(goal_draft_raw, dict) else {}
    if command.action_id != "request_director_controlled_repair" or not goal_draft:
        return {
            "schema_version": "resident.agi_tactical_action_result.v1",
            "workspace": command.workspace,
            "action_id": command.action_id,
            "action_spec": action_spec_payload,
            "status": "blocked",
            "reason": "action has no governed Resident goal draft",
            "chat": chat,
            "goal": None,
            "decision": None,
            "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                action_id=command.action_id,
                chat=chat,
            ),
            "tool_trace": _resident_agi_tactical_action_tool_trace(
                action_id=command.action_id,
                items=[
                    {
                        "step_id": "resident.agi_tactical_chat.revalidate",
                        "label": "重新读取战术上下文",
                        "mode": "read_only",
                        "status": "available",
                        "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                        "summary": "执行前重新读取 Resident AGI public facts。",
                    },
                    {
                        "step_id": "resident.goal_draft.policy_gate",
                        "label": "目标草案门禁",
                        "mode": "policy_gate",
                        "status": "blocked",
                        "contract": "resident.agi_tactical_action_result.v1",
                        "summary": "后端未生成受控 Resident goal draft，禁止前端补造。",
                    },
                ],
            ),
            "receipt": {
                "schema_version": "resident.agi_tactical_action_receipt.v1",
                "status": "BLOCKED",
                "title": "受控动作阻断凭证",
                "summary": "缺少后端生成的 Resident goal draft，未执行写入。",
            },
            "policy": {
                "advisory_only": True,
                "agi_direct_repair_allowed": False,
                "required_chain": "PM → Chief Engineer → Director → QA",
            },
        }

    created_goal = create_resident_goal(CreateResidentGoalCommandV1(workspace=command.workspace, payload=goal_draft))
    evidence_refs = chat_evidence_refs
    decision_payload = {
        "actor": "resident_agi",
        "stage": "tactical_console_action",
        "goal_id": str(created_goal.get("goal_id") or ""),
        "task_id": command.task_id,
        "run_id": command.run_id,
        "summary": f"AGI tactical console created a governed repair goal: {created_goal.get('title') or goal_draft.get('title')}",
        "context_refs": list(command.context_refs),
        "evidence_refs": list(evidence_refs),
        "options": [
            {
                "option_id": command.action_id,
                "label": str(selected_action.get("label") or command.action_id),
                "rationale": str(selected_action.get("reason") or ""),
                "strategy_tags": ["resident_agi_tactical_console", "controlled_repair_goal"],
                "estimated_score": float(goal_draft.get("expected_value") or 0.72),
            }
        ],
        "selected_option_id": command.action_id,
        "strategy_tags": [
            "resident_agi_tactical_console",
            "controlled_repair_goal",
            "pm_ce_director_qa_chain",
        ],
        "expected_outcome": {
            "next_state": "resident_goal_pending_governance",
            "required_chain": "PM → Chief Engineer → Director → QA",
            "agi_direct_repair_allowed": False,
        },
        "actual_outcome": {
            "created_goal_id": str(created_goal.get("goal_id") or ""),
            "created_goal_title": str(created_goal.get("title") or goal_draft.get("title") or ""),
            "action_id": command.action_id,
            "goal_draft": dict(goal_draft),
        },
        "verdict": "partial",
        "confidence": float(goal_draft.get("expected_value") or 0.72),
    }
    recorded_decision = record_resident_decision_entry(
        RecordResidentDecisionCommandV1(
            workspace=command.workspace,
            payload=decision_payload,
            action="resident_agi_tactical_action_executed",
            detail={
                "action_id": command.action_id,
                "goal_id": str(created_goal.get("goal_id") or ""),
            },
        )
    )
    return {
        "schema_version": "resident.agi_tactical_action_result.v1",
        "workspace": command.workspace,
        "action_id": command.action_id,
        "action_spec": action_spec_payload,
        "status": "executed",
        "reason": "created governed Resident goal and recorded decision trace",
        "chat": chat,
        "goal": created_goal,
        "decision": recorded_decision,
        "follow_up_actions": _resident_agi_tactical_follow_up_actions(
            action_id=command.action_id,
            chat=chat,
            created_goal=created_goal,
        ),
        "tool_trace": _resident_agi_tactical_action_tool_trace(
            action_id=command.action_id,
            items=[
                {
                    "step_id": "resident.agi_tactical_chat.revalidate",
                    "label": "重新读取战术上下文",
                    "mode": "read_only",
                    "status": "available",
                    "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                    "summary": "执行前重新读取 Resident AGI public facts。",
                },
                {
                    "step_id": "resident.goal_governance.commands",
                    "label": "Resident 目标治理",
                    "mode": "write_through_resident_contract",
                    "status": "executed",
                    "contract": "resident.goal_governance.commands",
                    "summary": "已创建待治理目标；没有直接调用 Director 修复。",
                },
                {
                    "step_id": "resident.decision_trace.write",
                    "label": "写入决策轨迹",
                    "mode": "write_through_resident_contract",
                    "status": "recorded",
                    "contract": "resident.decision_trace",
                    "summary": "已记录 AGI 战术动作和治理链路。",
                },
            ],
        ),
        "receipt": {
            "schema_version": "resident.agi_tactical_action_receipt.v1",
            "status": "EXECUTED",
            "title": "受控动作执行凭证",
            "summary": "已通过 Resident public contract 创建目标并写入 decision trace；未直接执行 Director 修复。",
            "rows": [
                {"label": "目标", "value": str(created_goal.get("goal_id") or "")},
                {"label": "决策", "value": str(recorded_decision.get("decision_id") or "")},
                {"label": "动作", "value": command.action_id},
                {"label": "角色链", "value": "PM→CE→Director→QA preserved"},
            ],
        },
        "policy": {
            "advisory_only": True,
            "agi_direct_repair_allowed": False,
            "required_chain": "PM → Chief Engineer → Director → QA",
        },
    }


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
    *,
    command: RunResidentAgiDecisionTurnCommandV1,
    audit_pack: dict[str, Any],
    hard_rule_gate: dict[str, Any],
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
    *,
    decision_capability_id: str,
    agi_verdict: str,
    downstream_allowed: bool,
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
    *,
    handoff_status: str,
    agi_verdict: str,
    effective_candidate_actions: list[str],
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
        decision_capability_id=decision_capability_id,
        agi_verdict=agi_verdict,
        downstream_allowed=downstream_allowed,
    )
    allowed_actions = _resident_agi_handoff_allowed_actions(
        handoff_status=handoff_status,
        agi_verdict=agi_verdict,
        effective_candidate_actions=effective_candidate_actions,
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
        QueryResidentAgiAuditPackV1(
            workspace=command.workspace,
            decision_limit=command.audit_pack_decision_limit,
        )
    )
    if audit_pack is not None:
        audit_pack = _resident_agi_audit_pack_with_current_refs(
            audit_pack,
            context_refs=command.context_refs,
            evidence_refs=command.evidence_refs,
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
        decision_type=command.decision_type,
        audit_pack=audit_pack,
    )
    resident_agi_participation = _resident_agi_decision_turn_participation(
        command=command,
        selected_decision_capability=selected_decision_capability,
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
        command=command,
        audit_pack=audit_pack or {},
        hard_rule_gate=hard_rule_gate,
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
                "resident_agi_evidence_capability_matrix_schema": evidence_capability_matrix.get(
                    "schema_version",
                    "",
                ),
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
            list(command.context_refs),
            list(command.evidence_refs),
            ["resident.agi_decision_preflight.v1"],
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
    if bool(output_contract_gate.get("required")) and not bool(output_contract_gate.get("passed")):
        runtime_success = False
        gate_error = str(output_contract_gate.get("reason") or "Resident AGI output contract gate failed.")
        error = error or gate_error
        failed_output_checks_raw = output_contract_gate.get("failed_check_ids")
        failed_output_checks = failed_output_checks_raw if isinstance(failed_output_checks_raw, list) else []
        risks = [
            *list(risks),
            *[f"failed output-contract check: {item}" for item in failed_output_checks],
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
        metadata={
            "run_id": command.run_id,
            "task_id": command.task_id,
            "goal_id": command.goal_id,
        },
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


def update_resident_agi_participation(command: UpdateResidentAgiParticipationCommandV1) -> dict[str, Any]:
    """Handle AGI participation policy updates through a scoped public contract."""

    payload = {
        "enabled": command.enabled,
        "scopes": list(command.scopes),
        "participation": dict(command.participation),
        "custom_scopes_allowed": command.custom_scopes_allowed,
    }
    updated_identity = get_resident_service(command.workspace).update_identity({"resident_agi_participation": payload})
    participation_raw = updated_identity.get("resident_agi_participation")
    participation = participation_raw if isinstance(participation_raw, dict) else payload
    scopes_raw = participation.get("scopes")
    scopes = scopes_raw if isinstance(scopes_raw, list) else []
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_agi_participation_updated",
        detail={
            "enabled": bool(participation.get("enabled")),
            "scope_count": len(scopes),
            "configured_scope_ids": scopes,
        },
    )
    return participation


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

    goal = get_resident_service(command.workspace).approve_goal(
        command.goal_id,
        note=command.note,
        expected_revision=command.expected_revision,
    )
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

    goal = get_resident_service(command.workspace).reject_goal(
        command.goal_id,
        note=command.note,
        expected_revision=command.expected_revision,
    )
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
    contract = service.materialize_goal(
        command.goal_id,
        expected_revision=command.expected_revision,
    )
    if contract is not None:
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_materialized",
            status_payload=service.get_status(include_details=True),
            detail={"goal_id": command.goal_id},
        )
    return contract


def archive_resident_goal(command: ArchiveResidentGoalCommandV1) -> dict[str, Any] | None:
    """Archive a rejected or materialized Goal through strict CAS."""
    service = get_resident_service(command.workspace)
    goal = service.archive_goal(command.goal_id, expected_revision=command.expected_revision)
    if goal is None:
        return None
    result = goal.to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_archived",
        detail={"goal_id": command.goal_id},
    )
    return result


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
    "BuildResidentAgiRepairAdvisoryOverlayCommandV1",
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
    "QueryResidentAgiEvidenceInterfacesV1",
    "QueryResidentAgiHandoffsV1",
    "QueryResidentAgiRepairAdvisoryOverlayV1",
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
    "UpdateResidentAgiParticipationCommandV1",
    "UpdateResidentIdentityCommandV1",
    "approve_resident_goal",
    "archive_resident_goal",
    "build_resident_agi_authority_matrix",
    "build_resident_agi_capability_access_registry",
    "build_resident_agi_capability_surface",
    "build_resident_agi_decision_boundaries",
    "build_resident_agi_decision_capabilities",
    "build_resident_agi_decision_capability_registry",
    "build_resident_agi_evidence_interface_contract",
    "build_resident_agi_repair_advisory_overlay",
    "create_evidence_bundle_service",
    "create_resident_goal",
    "extract_resident_skills",
    "get_evidence_service",
    "get_execution_projection_service",
    "get_resident_service",
    "materialize_resident_goal",
    "observe_resident_goal_attempt",
    "publish_resident_status_update",
    "query_resident_agi_audit_pack",
    "query_resident_agi_evidence_interfaces",
    "query_resident_agi_handoffs",
    "query_resident_agi_repair_advisory_overlay",
    "query_resident_capabilities",
    "query_resident_goal_execution",
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
    "settle_resident_goal_attempt",
    "stage_resident_goal",
    "start_resident",
    "start_resident_goal_attempt",
    "stop_resident",
    "update_resident_agi_participation",
    "update_resident_identity",
]
