"""Reusable artifact quality gates for generated workspaces."""

from __future__ import annotations

from polaris.kernelone.quality.artifact_quality import check_source_file_syntax, scan_workspace_artifact_quality
from polaris.kernelone.quality.cross_artifact_interfaces import (
    CrossArtifactConsistencyIssue,
    CrossArtifactInterfaceContract,
    CrossArtifactInterfaceRequirement,
    InterfaceImport,
    InterfaceSymbol,
    SymbolIndexSnapshot,
    build_symbol_index_snapshot,
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
    "CrossArtifactConsistencyIssue",
    "CrossArtifactInterfaceContract",
    "CrossArtifactInterfaceRequirement",
    "InterfaceImport",
    "InterfaceSymbol",
    "PackageScriptsCheckResult",
    "SymbolIndexSnapshot",
    "SyntaxCheckResult",
    "build_symbol_index_snapshot",
    "check_file_syntax",
    "check_package_scripts",
    "check_source_file_syntax",
    "first_syntax_failure",
    "scan_cross_artifact_consistency",
    "scan_cross_artifact_consistency_errors",
    "scan_workspace_artifact_quality",
]
