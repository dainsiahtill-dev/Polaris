"""Public service exports for `resident.autonomy` cell.

This package is the lossless successor of the former ``service`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...public.service`` and ``from ...public.service import X``
keep resolving identically for all external importers.
"""

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

from ._agi_decision import (
    _RESIDENT_AGI_AUTHORITY_FIELD_BLOCKLIST,
    _RESIDENT_AGI_HANDOFF_BLOCKED_ACTIONS,
    _RESIDENT_AGI_PLATFORM_CONTRACT_REF_KEYS,
    _RESIDENT_AGI_REQUIRED_PLATFORM_CONTRACT_REFS,
    _resident_agi_decision_handoff,
    _resident_agi_decision_preflight,
    _resident_agi_handoff_allowed_actions,
    _resident_agi_handoff_row,
    _resident_agi_handoff_status,
    _resident_agi_handoff_target_roles,
    _resident_agi_platform_contract_refs,
    _resident_agi_required_interface_statuses,
    _resident_agi_sanitize_handoff,
    query_resident_agi_handoffs,
    run_resident_agi_decision_turn,
)
from ._agi_gates import (
    _append_resident_agi_control_plane_gate,
    _resident_agi_capability_by_id,
    _resident_agi_control_gate_summary,
    _resident_agi_control_run_id,
    _resident_agi_decision_sequence,
    _resident_agi_decision_summary,
    _resident_agi_decision_type_tokens,
    _resident_agi_output_contract_gate,
    _resident_agi_policy_decision,
    _resident_agi_runtime_contract_gate,
    _resident_agi_select_decision_capability,
    _resident_decision_verdict,
)
from ._agi_interfaces import (
    _looks_like_repair_diagnostic,
    _resident_agi_audit_diagnosis_interface,
    _resident_agi_audit_pack_with_current_refs,
    _resident_agi_audit_verdict_interface,
    _resident_agi_candidate_paths_from_refs,
    _resident_agi_chief_engineer_blueprint_interface,
    _resident_agi_context_catalog_interface,
    _resident_agi_context_engine_interface,
    _resident_agi_contextos_final_request_interface,
    _resident_agi_director_repair_advisory_policy_interface,
    _resident_agi_director_repair_coverage_interface,
    _resident_agi_director_repair_strategy_catalog_interface,
    _resident_agi_evidence_capability_matrix,
    _resident_agi_evidence_group_name,
    _resident_agi_evidence_interface_group_id,
    _resident_agi_find_profile_payload,
    _resident_agi_interface_base,
    _resident_agi_metadata_only_interface,
    _resident_agi_read_json_artifact,
    _resident_agi_repair_diagnostic_candidates,
    _resident_agi_run_ledger_interface,
    _resident_agi_run_provenance_bundle_interface,
    _resident_agi_runtime_events_interface,
    _resident_agi_task_execution_profile_interface,
    _resident_agi_verifier_policy_interface,
    query_resident_agi_evidence_interfaces,
)
from ._agi_participation import (
    _RESIDENT_AGI_REPAIR_ADVISORY_SCOPE_KEYS,
    _resident_agi_decision_turn_participation,
    _resident_agi_identity_participation,
    _resident_agi_known_participation_scope_keys,
    _resident_agi_participation_scope_key,
    _resident_agi_repair_advisory_decision_relevant,
    _resident_agi_repair_advisory_overlay_from_decision,
    _resident_agi_repair_advisory_overlay_from_decision_record,
    _resident_agi_repair_advisory_participation_enabled,
    build_resident_agi_repair_advisory_overlay,
    query_resident_agi_audit_pack,
    query_resident_agi_repair_advisory_overlay,
)
from ._agi_tactical import (
    _resident_agi_tactical_action_tool_trace,
    _resident_agi_tactical_follow_up_actions,
    execute_resident_agi_tactical_action,
    query_resident_agi_tactical_action_catalog,
    query_resident_agi_tactical_chat,
)
from ._helpers import (
    _JETSTREAM_PUBLISH_FALSE_VALUES,
    _RESIDENT_STATUS_CHANNEL,
    _jetstream_publish_enabled,
    _merge_non_empty_strings,
    get_evidence_service,
    logger,
    publish_resident_status_update,
)
from ._lifecycle import (
    _CYCLE_ACTIONS,
    _emit_cycle_completed_event,
    approve_resident_goal,
    archive_resident_goal,
    create_resident_goal,
    extract_resident_skills,
    materialize_resident_goal,
    observe_resident_goal_attempt,
    query_resident_capabilities,
    query_resident_goal_execution,
    query_resident_status,
    record_resident_decision_entry,
    record_resident_evidence,
    reject_resident_goal,
    run_resident_cycle,
    run_resident_experiments,
    run_resident_goal,
    run_resident_improvements,
    run_resident_tick,
    settle_resident_goal_attempt,
    stage_resident_goal,
    start_resident,
    start_resident_goal_attempt,
    stop_resident,
    update_resident_agi_participation,
    update_resident_identity,
)

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
