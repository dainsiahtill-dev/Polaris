"""Artifact quality checks shared by Director and integration QA.

This package is the lossless successor of the former ``artifact_quality`` module.
It re-exports every previously-public symbol from the same import path so
``import polaris.kernelone.quality.artifact_quality`` and
``from polaris.kernelone.quality.artifact_quality import X`` keep resolving
identically for all external importers.
"""

from __future__ import annotations

# Backward-compatible re-export of stdlib / typing names that were module-level
# attributes of the former single-file module (preserves full dir() surface).
import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

# Implementation submodules (import for side-effect registration / binding).
from polaris.kernelone.quality.artifact_quality import (
    _constants as _constants,
    _helpers as _helpers,
    _issues as _issues,
    _models as _models,
    _scan as _scan,
    _scan_package as _scan_package,
    _scan_typescript as _scan_typescript,
    _syntax as _syntax,
)

# Re-export private constants used by importers / characterization tests.
from polaris.kernelone.quality.artifact_quality._constants import *  # noqa: F403
from polaris.kernelone.quality.artifact_quality._helpers import (
    _first_nonempty_line,
    _is_source_artifact,
    _is_test_like_artifact_path,
    _iter_target_files,
    _iter_workspace_relative_files,
    _iter_workspace_source_files,
    _looks_like_code_symbol,
    _merge_quality_names,
    _package_root_name,
    _quality_string_list,
)
from polaris.kernelone.quality.artifact_quality._issues import (
    _LEGACY_ARTIFACT_QUALITY_ISSUE_CODE_CLASSIFIERS,
    _artifact_quality_evidence,
    _artifact_quality_issue_code_from_typed_metadata,
    _artifact_quality_issue_from_cross_artifact_issue,
    _artifact_quality_issue_from_error,
    _artifact_quality_issue_from_mapping,
    _artifact_quality_issue_from_value,
    _artifact_quality_issue_location,
    _artifact_quality_issue_metadata,
    _artifact_quality_issue_path,
    _artifact_quality_issues_from_errors,
    _artifact_quality_optional_int,
    _artifact_quality_scan_failure_issue,
    _compiled_entrypoint_from_node_module_error,
    _file_artifact_quality_issue,
    _javascript_module_error_issue,
    _javascript_module_error_metadata,
    _legacy_artifact_quality_issue_code_from_message,
    _legacy_compiler_diagnostic_metadata,
    _legacy_compiler_issue_code_from_explicit_code,
    _legacy_compiler_issue_code_from_path,
    _legacy_declared_target_missing_metadata,
    _legacy_hygiene_issue_code,
    _legacy_language_or_syntax_issue_code,
    _legacy_npm_manifest_issue_code,
    _legacy_npm_manifest_issue_metadata,
    _legacy_npm_script_metadata,
    _legacy_rust_missing_binary_issue_code,
    _legacy_target_or_import_issue_code,
    _legacy_undeclared_runtime_import_metadata,
    _legacy_unresolved_import_symbol_metadata,
    _legacy_unresolved_relative_import_metadata,
    _npm_manifest_script_issue,
    _relative_rust_bin_path_from_cargo_message,
    _script_name_from_npm_invocation,
    artifact_quality_issue_key,
    artifact_quality_issue_raw,
    artifact_quality_issue_structural_key,
    artifact_quality_issues_for_errors,
    artifact_quality_issues_from_errors,
)

# Models + public API (AST-owned public surface).
from polaris.kernelone.quality.artifact_quality._models import (
    ArtifactQualityEvidence,
    ArtifactQualityIssue,
    _FileArtifactQualityEvidence,
    _NodeEvalSyntaxIssue,
)
from polaris.kernelone.quality.artifact_quality._scan import (
    _scan_file_evidence,
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_package import (
    _package_manifest_evidence_from_errors,
    _scan_cargo_manifest_missing_binary_evidence,
    _scan_package_manifest_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_typescript import (
    _scan_html_typescript_module_script_evidence,
    _scan_typescript_import_evidence,
    _scan_typescript_project_typecheck_evidence,
    _scan_typescript_syntax_red_flag_evidence,
    _scan_typescript_tsconfig_evidence,
    _typescript_typecheck_diagnostic_detail,
)
from polaris.kernelone.quality.artifact_quality._syntax import (
    _check_html_completeness,
    _compress_node_syntax_error,
    _iter_typescript_return_object_bodies,
    check_source_file_syntax,
)
from polaris.kernelone.quality.cross_artifact_interfaces import (
    ContractAmendmentRequest,
    CrossArtifactConsistencyIssue,
    CrossArtifactInterfaceContract,
    CrossArtifactRepairPlan,
    build_contract_amendment_request,
    plan_cross_artifact_repairs,
    scan_cross_artifact_consistency,
)
from polaris.kernelone.quality.interface_ledger import (
    read_all_declared_interfaces,
    read_declared_interfaces,
    validate_declared_interface_issues_against_snapshot,
)
from polaris.kernelone.quality.package_scripts import (
    PackageScriptIssue,
    check_package_scripts,
)


def _reexport_implementation_surface() -> None:
    """Bind every submodule symbol onto this package (monkeypatch / from-import surface)."""

    import sys
    import types

    pkg = sys.modules[__name__]
    # Stdlib modules that were attributes of the original single-file module.
    original_module_attrs = {
        "json",
        "os",
        "py_compile",
        "re",
        "shlex",
        "shutil",
        "subprocess",
    }
    for mod in (
        _constants,
        _models,
        _helpers,
        _syntax,
        _issues,
        _scan_package,
        _scan_typescript,
        _scan,
    ):
        for name, value in mod.__dict__.items():
            if name.startswith("__"):
                continue
            # Do not leak helper imports (e.g. sys) onto the public package dir().
            if isinstance(value, types.ModuleType) and name not in original_module_attrs:
                continue
            setattr(pkg, name, value)


_reexport_implementation_surface()

__all__ = [
    "ArtifactQualityEvidence",
    "ArtifactQualityIssue",
    "artifact_quality_issue_key",
    "artifact_quality_issue_raw",
    "artifact_quality_issue_structural_key",
    "artifact_quality_issues_for_errors",
    "artifact_quality_issues_from_errors",
    "check_source_file_syntax",
    "scan_workspace_artifact_quality",
    "scan_workspace_artifact_quality_evidence",
]
