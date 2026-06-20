"""Reusable artifact quality gates for generated workspaces."""

from __future__ import annotations

from polaris.kernelone.quality.artifact_quality import check_source_file_syntax, scan_workspace_artifact_quality
from polaris.kernelone.quality.role_output_markers import DEBT_MARKERS
from polaris.kernelone.quality.syntax_gate import (
    SyntaxCheckResult,
    check_file_syntax,
    first_syntax_failure,
)

__all__ = [
    "DEBT_MARKERS",
    "SyntaxCheckResult",
    "check_file_syntax",
    "check_source_file_syntax",
    "first_syntax_failure",
    "scan_workspace_artifact_quality",
]
