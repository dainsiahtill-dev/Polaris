"""Reusable artifact quality gates for generated workspaces."""

from __future__ import annotations

from polaris.kernelone.quality.artifact_quality import (
    ArtifactQualityEvidence,
    ArtifactQualityIssue,
    artifact_quality_issues_from_errors,
    check_source_file_syntax,
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.quality.cross_artifact_interfaces import (
    CrossArtifactConsistencyIssue,
    CrossArtifactInterfaceContract,
    CrossArtifactInterfaceRequirement,
    CrossArtifactRepairPlan,
    InterfaceImport,
    InterfaceSymbol,
    SymbolIndexSnapshot,
    build_contract_amendment_request,
    build_symbol_index_snapshot,
    plan_cross_artifact_repairs,
    scan_cross_artifact_consistency,
    scan_cross_artifact_consistency_errors,
)
from polaris.kernelone.quality.file_ownership_ledger import (
    owner_task_identifier_token_aliases,
    task_identifier_token_aliases,
)
from polaris.kernelone.quality.package_scripts import PackageScriptsCheckResult, check_package_scripts
from polaris.kernelone.quality.role_output_markers import DEBT_MARKERS
from polaris.kernelone.quality.scope_authority import (
    ScopeAuthorityDecision,
    build_scope_authority_decision,
    glob_declared_scope_path_matches,
    matching_owner_handoff_request,
    normalize_declared_scope_path,
    owner_handoff_identifier_tokens,
    owner_task_retry_handoff_requests_from_scope_payload,
    ownership_handoff_requests_from_scope_payload,
    partition_paths_by_declared_scope,
    path_matches_any_declared_scope_candidate,
    path_matches_declared_scope_candidate,
    task_record_identifier_tokens,
    unresolved_owner_handoff_requests_from_scope_payload,
)
from polaris.kernelone.quality.syntax_gate import (
    SyntaxCheckResult,
    check_file_syntax,
    first_syntax_failure,
)

__all__ = [
    "DEBT_MARKERS",
    "ArtifactQualityEvidence",
    "ArtifactQualityIssue",
    "CrossArtifactConsistencyIssue",
    "CrossArtifactInterfaceContract",
    "CrossArtifactInterfaceRequirement",
    "CrossArtifactRepairPlan",
    "InterfaceImport",
    "InterfaceSymbol",
    "PackageScriptsCheckResult",
    "ScopeAuthorityDecision",
    "SymbolIndexSnapshot",
    "SyntaxCheckResult",
    "artifact_quality_issues_from_errors",
    "build_contract_amendment_request",
    "build_scope_authority_decision",
    "build_symbol_index_snapshot",
    "check_file_syntax",
    "check_package_scripts",
    "check_source_file_syntax",
    "first_syntax_failure",
    "glob_declared_scope_path_matches",
    "matching_owner_handoff_request",
    "normalize_declared_scope_path",
    "owner_handoff_identifier_tokens",
    "owner_task_identifier_token_aliases",
    "owner_task_retry_handoff_requests_from_scope_payload",
    "ownership_handoff_requests_from_scope_payload",
    "partition_paths_by_declared_scope",
    "path_matches_any_declared_scope_candidate",
    "path_matches_declared_scope_candidate",
    "plan_cross_artifact_repairs",
    "scan_cross_artifact_consistency",
    "scan_cross_artifact_consistency_errors",
    "scan_workspace_artifact_quality",
    "scan_workspace_artifact_quality_evidence",
    "task_identifier_token_aliases",
    "task_record_identifier_tokens",
    "unresolved_owner_handoff_requests_from_scope_payload",
]
