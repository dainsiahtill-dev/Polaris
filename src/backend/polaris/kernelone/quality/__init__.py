"""Reusable artifact quality gates for generated workspaces."""

from __future__ import annotations

from polaris.kernelone.quality.artifact_quality import (
    ArtifactQualityEvidence,
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
from polaris.kernelone.quality.package_scripts import PackageScriptsCheckResult, check_package_scripts
from polaris.kernelone.quality.role_output_markers import DEBT_MARKERS
from polaris.kernelone.quality.syntax_gate import (
    SyntaxCheckResult,
    check_file_syntax,
    first_syntax_failure,
)

__all__ = [
    "DEBT_MARKERS",
    "ArtifactQualityEvidence",
    "CrossArtifactConsistencyIssue",
    "CrossArtifactInterfaceContract",
    "CrossArtifactInterfaceRequirement",
    "CrossArtifactRepairPlan",
    "InterfaceImport",
    "InterfaceSymbol",
    "PackageScriptsCheckResult",
    "SymbolIndexSnapshot",
    "SyntaxCheckResult",
    "build_contract_amendment_request",
    "build_symbol_index_snapshot",
    "check_file_syntax",
    "check_package_scripts",
    "check_source_file_syntax",
    "first_syntax_failure",
    "plan_cross_artifact_repairs",
    "scan_cross_artifact_consistency",
    "scan_cross_artifact_consistency_errors",
    "scan_workspace_artifact_quality",
    "scan_workspace_artifact_quality_evidence",
]
