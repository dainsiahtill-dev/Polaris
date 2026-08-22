"""Stable public service exports for `chief_engineer.blueprint`.

This package is the lossless successor of the former ``service`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...public.service`` and ``from ...public.service import X``
keep resolving identically for all external importers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.director.tasking.public.service import (
    build_director_execution_profile_snapshot,
)
from polaris.kernelone.quality.file_ownership_ledger import record_task_file_owners

from ...internal.adr_log import ADRDecisionLog, build_adr_event
from ...internal.architecture_decisions import (
    infer_architecture_decisions,
    merge_architecture_decisions,
    normalize_architecture_decisions,
    selected_libraries_from_decisions,
)
from ...internal.blueprint_persistence import BlueprintPersistence
from ...internal.ce_consumer import CEConsumer, _control_plane_job_token
from ...internal.chief_engineer_agent import ChiefEngineerAgent
from ...internal.chief_engineer_preflight import run_pre_dispatch_chief_engineer
from ...internal.handoff import build_handoff_decision
from ...internal.post_mortem import PostMortemLog, build_post_mortem_event
from ...internal.project_completion_contract import build_project_completion_contract
from ...internal.quality_gate import evaluate_quality_gate
from ...internal.release_readiness import build_release_readiness
from ...internal.review_store import persist_chief_engineer_review_document
from ...internal.risks import RiskRegister, build_risk_event
from ...internal.rollback_guard import create_rollback_guard
from ...internal.rollback_link import build_rollback_link
from ...internal.tech_debt import TechDebtLedger, build_tech_debt_event
from ...internal.tech_radar import TechRadarLedger, build_tech_radar_event
from ..contracts import (
    ADRRecordV1,
    ArtifactObligationV1,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    CeHandoffDecisionBindingsV1,
    CeHandoffDecisionV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerBlueprintPortfolioV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerProjectInterfaceContractV1,
    EntrypointObligationV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    GovernanceSummaryV1,
    HandoffDecisionV1,
    ListADRsQueryV1,
    ListPostMortemsQueryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    ListTechRadarQueryV1,
    PostMortemRecordV1,
    ProjectCompletionContractV1,
    ProjectCompletionObligationsV1,
    ProjectKindAuthorityV1,
    QueryBlueprintProvenanceV1,
    QueryProjectCompletionContractV1,
    RegisterADRCommandV1,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    ReleaseReadinessV1,
    RiskRecordV1,
    StackPolicyViolationV1,
    TaskBlueprintProvenanceSnapshotV1,
    TaskBlueprintResultV1,
    TechDebtRecordV1,
    TechRadarEntryV1,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
    VerificationObligationV1,
    _ChiefEngineerPortfolioAuthorityCarrierV1,
    _portfolio_authority_receipt_hash,
    _strict_provenance_target_paths,
    _verify_chief_engineer_portfolio_authority_carrier,
    project_completion_catalog_snapshot_hash,
)
from ._governance import (
    assess_release_readiness,
    attach_governance_to_blueprint,
    build_blueprint_governance,
    check_stack_policy,
    get_blueprint_governance,
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
from ._handoff import (
    _CE_HANDOFF_POLICY_VERSION,
    _MISSING_HASH_PREFIX,
    _binding_hash_or_missing,
    _blueprint_id_from_payload,
    _execution_profile_hash_from_blueprint,
    _handoff_validation_result,
    _merged_payload_metadata,
    _project_task_completion_contract,
    _task_id_from_payload,
    assert_handoff_ready,
    build_ce_handoff_decision,
    evaluate_ce_handoff_decision_for_blueprint,
    evaluate_handoff_decision,
    evaluate_handoff_decision_for_blueprint,
    validate_director_handoff_from_payload,
)

# Private helpers re-exported for lossless module surface / monkeypatch compatibility.
from ._helpers import (
    _BLUEPRINT_FILE_CONTAINER_KEYS,
    _BLUEPRINT_FILE_PATH_KEYS,
    _BLUEPRINT_HASH_IGNORED_KEYS,
    _BLUEPRINT_PROVENANCE_HASH_SCHEME,
    _BLUEPRINT_PROVENANCE_SCHEMA_VERSION,
    _CAMEL_TOKEN_RE,
    _COMMON_EXTENSIONLESS_FILES,
    _GENERIC_SEMANTIC_TOKENS,
    _INTERFACE_SNAPSHOT_SOURCE_SUFFIXES,
    _LOWER_SHA256_RE,
    _PORTFOLIO_HASH_IGNORED_KEYS,
    _PROJECT_COMPLETION_PREDICATE_VERSION,
    _SAFE_ID_RE,
    _SEMANTIC_SUPPORT_BOUNDARY_FILENAMES,
    _SEMANTIC_SUPPORT_BOUNDARY_SUFFIXES,
    _SEMANTIC_TOKEN_RE,
    _WINDOWS_DRIVE_PATH_RE,
    _apply_delivery_depth_test_targets,
    _blueprint_contract_fields,
    _blueprint_declared_file_paths,
    _blueprint_hash,
    _blueprint_path,
    _blueprint_provenance_text,
    _compact_llm_blueprint_value,
    _contract_completeness,
    _default_delivery_depth_test_target,
    _delivery_depth_contract_from_context,
    _delivery_depth_minimums,
    _delivery_plan_document_from_context,
    _existing_export_symbols_by_path,
    _existing_target_files_from_payload,
    _first_string_list,
    _hashable_blueprint_payload,
    _hashable_portfolio_payload,
    _infer_language_from_targets,
    _is_semantic_support_boundary,
    _is_semantic_support_boundary_path,
    _latest_blueprint_for_task,
    _mapping,
    _merge_existing_target_file_summaries,
    _merge_string_lists,
    _module_interface_contract,
    _module_owner_terms,
    _module_role_from_path,
    _module_stem,
    _needs_workspace_interface_snapshot,
    _normalize_blueprint_file_path,
    _normalize_delivery_depth_payload,
    _normalize_llm_blueprint_overlay,
    _normalize_task_token,
    _owner_terms_overlap,
    _pascal_case,
    _path_looks_like_test,
    _plan_field_strings,
    _planned_public_symbols,
    _portfolio_hash,
    _positive_int,
    _public_symbols_from_export_summary,
    _qa_acceptance_from_task,
    _safe_token,
    _semantic_alignment_audit,
    _semantic_terms_from_delivery_contracts,
    _semantic_tokens_from_text,
    _semantic_tokens_from_values,
    _snake_case,
    _string_list,
    _summary_line_for_interface_symbol,
    _target_files_from_context,
    _task_payload_from_context,
    _tuple_from_payload,
    _utc_now,
    _workspace_existing_target_file_summaries,
    chief_engineer_source_suffixes_for_language,
    logger,
    query_blueprint_provenance,
)
from ._portfolio import (
    _bind_portfolio_task_overlays,
    _build_portfolio_completion_contract,
    _completion_path_is_within_scope,
    _deterministic_portfolio_plan,
    _merge_portfolio_construction_plan,
    _merge_risk_flags,
    _merge_scope_paths,
    _merge_scope_rejections,
    _normalize_interface_declarations,
    _normalize_portfolio_advisory_path,
    _normalize_portfolio_risk_flags,
    _parse_portfolio_llm_blueprint,
    _parse_scope_suggestions,
    _persist_immutable_blueprint_portfolio,
    _plan_path_suggestions,
    _portfolio_array,
    _portfolio_contract_error,
    _portfolio_mapping,
    _portfolio_risk_flag,
    _PortfolioLlmBlueprint,
    _project_blueprint_portfolio_context,
    _project_interface_seed,
    _read_portfolio_catalog_snapshot,
    _readable_portfolio_value,
    _revalidate_portfolio_authority_carrier,
    _scope_advisory_for_task,
    _scope_entry_text,
    _strict_completion_mapping,
    _strict_completion_rows,
    _task_plan_components,
    build_chief_engineer_blueprint_portfolio,
    classify_chief_engineer_pm_entrypoint_kind,
    derive_project_kind_authority_from_catalog_snapshot,
    project_chief_engineer_delivery_depth_feasibility_from_pm_tasks,
    project_chief_engineer_portfolio_delivery_depth_feasibility,
    project_chief_engineer_task_blueprint,
    query_project_completion_contract,
)
from ._semantic_repair import (
    build_chief_engineer_semantic_repair_patch_schema,
    compose_chief_engineer_semantic_repair,
    load_chief_engineer_semantic_repair_candidate,
    normalize_chief_engineer_portfolio_tool_arguments,
    persist_chief_engineer_semantic_repair_candidate,
    project_chief_engineer_semantic_repair_provider_context,
)
from ._task_blueprint import (
    generate_task_blueprint,
    get_blueprint_status,
)

# Contract types (dataclasses, enums, errors) are owned by contracts.py and
# re-exported through public/__init__.py from there — they are intentionally
# NOT listed here. service.__all__ exposes only service functions and the
# re-exported agent/consumer classes, uniformly across all ledgers.
__all__ = [
    "CEConsumer",
    "ChiefEngineerAgent",
    "assert_handoff_ready",
    "assess_release_readiness",
    "attach_governance_to_blueprint",
    "build_blueprint_governance",
    "build_ce_handoff_decision",
    "build_chief_engineer_blueprint_portfolio",
    "build_chief_engineer_semantic_repair_patch_schema",
    "check_stack_policy",
    "chief_engineer_source_suffixes_for_language",
    "classify_chief_engineer_pm_entrypoint_kind",
    "compose_chief_engineer_semantic_repair",
    "create_rollback_guard",
    "evaluate_ce_handoff_decision_for_blueprint",
    "evaluate_handoff_decision",
    "evaluate_handoff_decision_for_blueprint",
    "generate_task_blueprint",
    "get_blueprint_governance",
    "get_blueprint_status",
    "list_adrs",
    "list_post_mortems",
    "list_risks",
    "list_tech_debt",
    "list_tech_radar",
    "load_chief_engineer_semantic_repair_candidate",
    "normalize_chief_engineer_portfolio_tool_arguments",
    "persist_chief_engineer_review_document",
    "persist_chief_engineer_semantic_repair_candidate",
    "project_chief_engineer_delivery_depth_feasibility_from_pm_tasks",
    "project_chief_engineer_portfolio_delivery_depth_feasibility",
    "project_chief_engineer_semantic_repair_provider_context",
    "project_chief_engineer_task_blueprint",
    "query_blueprint_provenance",
    "register_adr",
    "register_post_mortem",
    "register_risk",
    "register_tech_debt",
    "register_tech_radar",
    "run_pre_dispatch_chief_engineer",
    "summarize_adrs",
    "summarize_post_mortems",
    "summarize_risks",
    "summarize_tech_debt",
    "summarize_tech_radar",
    "update_adr_status",
    "update_post_mortem_status",
    "update_risk_status",
    "update_tech_debt_status",
    "update_tech_radar_ring",
    "validate_director_handoff_from_payload",
]
