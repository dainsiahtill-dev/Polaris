"""Workspace-level artifact quality scan orchestration."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

from polaris.kernelone.quality.artifact_quality._constants import (
    _ARTIFACT_QUALITY_SOURCE_EXTS,
    _DETERMINISTIC_SCAFFOLD_MARKERS,
    _GENERIC_STORE_MAP_RE,
    _GENERIC_STORE_RECORD_RE,
    _NUMERIC_HELPER_FILLER_RE,
    _PATCH_RESIDUE_RE,
    _SOURCE_NARRATION_LEAK_RE,
    _TOOL_RECEIPT_CONTAMINATION_TOKENS,
    _TRIVIAL_ARITHMETIC_EXPECT_RE,
)
from polaris.kernelone.quality.artifact_quality._helpers import (
    _is_test_like_artifact_path,
    _iter_target_files,
    _looks_like_code_symbol,
    _merge_quality_names,
    _quality_string_list,
)
from polaris.kernelone.quality.artifact_quality._issues import (
    _artifact_quality_evidence,
    _artifact_quality_scan_failure_issue,
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    ArtifactQualityEvidence,
    ArtifactQualityIssue,
    _FileArtifactQualityEvidence,
)
from polaris.kernelone.quality.artifact_quality._scan_go import (
    _scan_go_project_compile_evidence,
    _scan_go_project_test_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_javascript import (
    _scan_javascript_named_export_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_package import (
    _scan_cargo_manifest_missing_binary_evidence,
    _scan_package_manifest_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_python import (
    _scan_python_acceptance_term_pair_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_typescript import (
    _scan_html_typescript_module_script_evidence,
    _scan_typescript_import_evidence,
    _scan_typescript_project_typecheck_evidence,
    _scan_typescript_syntax_red_flag_evidence,
    _scan_typescript_tsconfig_evidence,
)
from polaris.kernelone.quality.artifact_quality._syntax import (
    check_source_file_syntax,
)
from polaris.kernelone.quality.cross_artifact_interfaces import (
    CrossArtifactConsistencyIssue,
    CrossArtifactInterfaceContract,
    build_contract_amendment_request,
    plan_cross_artifact_repairs,
    scan_cross_artifact_consistency,
)
from polaris.kernelone.quality.interface_ledger import (
    read_all_declared_interfaces,
    read_declared_interfaces,
    validate_declared_interface_issues_against_snapshot,
)


def scan_workspace_artifact_quality(
    workspace_full: str,
    *,
    relative_paths: Iterable[str] | None = None,
) -> list[str]:
    """Reject known worthless generated artifacts.

    When ``relative_paths`` is provided, only those workspace-relative files are
    scanned. This lets Director validate the files it just changed without
    failing unrelated seed files that later tasks are expected to repair. QA
    calls this without ``relative_paths`` to scan the complete final workspace.
    """

    return list(scan_workspace_artifact_quality_evidence(workspace_full, relative_paths=relative_paths).errors)


def scan_workspace_artifact_quality_evidence(
    workspace_full: str,
    *,
    relative_paths: Iterable[str] | None = None,
    interface_contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None = None,
    task_id: str = "",
) -> ArtifactQualityEvidence:
    """Scan artifacts and return structured evidence without changing old callers."""

    try:
        root_full = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        message = "Artifact quality scan failed: workspace path cannot be resolved"
        return _artifact_quality_evidence(
            errors=(message,),
            issues=(
                ArtifactQualityIssue(
                    code="workspace_path_unresolved",
                    message=message,
                    source="artifact_quality_scanner",
                    metadata={
                        "raw": message,
                        "diagnostic_kind": "workspace_path_unresolved",
                    },
                ),
            ),
        )
    if not root_full.exists() or not root_full.is_dir():
        message = "Artifact quality scan failed: workspace path does not exist"
        return _artifact_quality_evidence(
            errors=(message,),
            issues=(
                ArtifactQualityIssue(
                    code="workspace_path_missing",
                    message=message,
                    source="artifact_quality_scanner",
                    metadata={
                        "raw": message,
                        "diagnostic_kind": "workspace_path_missing",
                    },
                ),
            ),
        )

    errors: list[str] = []
    typed_issues: list[Any] = []
    scanned_relative_paths: list[str] = []
    cross_artifact_issues: tuple[CrossArtifactConsistencyIssue, ...] = ()
    try:
        interface_contract = interface_contract or _declared_interface_contract(
            root_full=root_full,
            relative_paths=relative_paths,
            task_id=task_id,
        )
        paths = (
            _iter_target_files(root_full, relative_paths)
            if relative_paths is not None
            else sys.modules[__package__]._iter_workspace_source_files(root_full)
        )
        for full_path in paths:
            if len(errors) >= 50:
                return _artifact_quality_evidence(
                    errors=errors,
                    issues=typed_issues,
                    scanned_relative_paths=tuple(scanned_relative_paths),
                )
            relative_path = full_path.relative_to(root_full).as_posix()
            scanned_relative_paths.append(relative_path)
            file_evidence = _scan_file_evidence(root_full, full_path, relative_path)
            errors.extend(file_evidence.errors)
            typed_issues.extend(file_evidence.issues)
        if len(errors) < 50:
            typecheck_evidence = _scan_typescript_project_typecheck_evidence(root_full, scanned_relative_paths)
            errors.extend(typecheck_evidence.errors)
            typed_issues.extend(typecheck_evidence.issues)
        if len(errors) < 50:
            go_compile_evidence = _scan_go_project_compile_evidence(root_full, scanned_relative_paths)
            errors.extend(go_compile_evidence.errors)
            typed_issues.extend(go_compile_evidence.issues)
            if len(errors) < 50 and not go_compile_evidence.errors:
                go_test_evidence = _scan_go_project_test_evidence(root_full, scanned_relative_paths)
                errors.extend(go_test_evidence.errors)
                typed_issues.extend(go_test_evidence.issues)
        if len(errors) < 50:
            js_export_evidence = _scan_javascript_named_export_evidence(root_full, scanned_relative_paths)
            errors.extend(js_export_evidence.errors)
            typed_issues.extend(js_export_evidence.issues)
        if len(errors) < 50:
            python_term_evidence = _scan_python_acceptance_term_pair_evidence(root_full, scanned_relative_paths)
            errors.extend(python_term_evidence.errors)
            typed_issues.extend(python_term_evidence.issues)
        if len(errors) < 50:
            cross_artifact_issues = tuple(
                scan_cross_artifact_consistency(
                    root_full,
                    relative_paths=scanned_relative_paths if relative_paths is not None else None,
                    contract=interface_contract,
                )
            )
            errors.extend(
                issue.to_error_message() for issue in cross_artifact_issues if not issue.code.startswith("contract_")
            )
        if len(errors) < 50:
            declared_interface_issues = _scan_declared_interface_ledger_issues(
                root_full,
                scanned_relative_paths if relative_paths is not None else None,
            )
            typed_issues.extend(declared_interface_issues)
            errors.extend(
                issue["metadata"]["raw"]
                for issue in declared_interface_issues
                if isinstance(issue.get("metadata"), Mapping) and str(issue["metadata"].get("raw") or "").strip()
            )
    except (OSError, RuntimeError, ValueError) as exc:
        message = f"Artifact quality scan failed: {exc}"
        return _artifact_quality_evidence(
            errors=(message,),
            issues=(_artifact_quality_scan_failure_issue(message, exc=exc),),
        )
    return _artifact_quality_evidence(
        errors=errors,
        issues=typed_issues,
        scanned_relative_paths=scanned_relative_paths,
        cross_artifact_issues=cross_artifact_issues,
        cross_artifact_repair_plans=plan_cross_artifact_repairs(cross_artifact_issues),
        contract_amendment_request=build_contract_amendment_request(
            task_id=_artifact_quality_task_id(task_id=task_id, interface_contract=interface_contract),
            issues=cross_artifact_issues,
        ),
    )


def _declared_interface_contract(
    *,
    root_full: Path,
    relative_paths: Iterable[str] | None,
    task_id: str,
) -> CrossArtifactInterfaceContract | None:
    declared: dict[str, dict[str, Any]] = {}
    target_files = list(relative_paths) if relative_paths is not None else None
    for cache_root in ("", root_full.as_posix()):
        try:
            entries = (
                read_declared_interfaces(root_full.as_posix(), cache_root, target_files)
                if target_files is not None
                else read_all_declared_interfaces(root_full.as_posix(), cache_root)
            )
        except (OSError, RuntimeError, ValueError):
            continue
        for target, entry in entries.items():
            current = declared.setdefault(target, {"identifiers": [], "public_symbols": [], "signatures": []})
            current["identifiers"] = _merge_quality_names(current.get("identifiers"), entry.get("identifiers"))
            current["public_symbols"] = _merge_quality_names(current.get("public_symbols"), entry.get("public_symbols"))
            current["signatures"] = _merge_quality_names(current.get("signatures"), entry.get("signatures"))
    if not declared:
        return None
    interfaces = []
    for owner_path, entry in sorted(declared.items()):
        code_symbols = _quality_string_list(entry.get("public_symbols")) or [
            identifier
            for identifier in _quality_string_list(entry.get("identifiers"))
            if _looks_like_code_symbol(identifier)
        ]
        for identifier in code_symbols:
            interfaces.append(
                {
                    "domain": "declared_interface_ledger",
                    "owner_path": owner_path,
                    "name": identifier,
                    "kind": "code_symbol",
                }
            )
    if not interfaces:
        return None
    return CrossArtifactInterfaceContract.from_mapping(
        {
            "task_id": str(task_id or "").strip(),
            "language": "",
            "interfaces": interfaces,
        }
    )


def _artifact_quality_task_id(
    *,
    task_id: str,
    interface_contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None,
) -> str:
    explicit = str(task_id or "").strip()
    if explicit:
        return explicit
    if isinstance(interface_contract, CrossArtifactInterfaceContract):
        return interface_contract.task_id
    if isinstance(interface_contract, Mapping):
        return str(interface_contract.get("task_id") or "").strip()
    return ""


def _scan_declared_interface_ledger_issues(
    root_full: Path,
    relative_paths: Iterable[str] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    target_files = list(relative_paths) if relative_paths is not None else None
    for cache_root in ("", root_full.as_posix()):
        try:
            issues.extend(
                issue.to_artifact_quality_issue()
                for issue in validate_declared_interface_issues_against_snapshot(
                    root_full.as_posix(),
                    cache_root,
                    target_files,
                )
            )
        except (OSError, RuntimeError, ValueError):
            continue
    deduped: dict[str, dict[str, Any]] = {}
    for issue in issues:
        metadata_raw = issue.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        raw = str(metadata.get("raw") or issue.get("message") or "").strip()
        if raw:
            deduped.setdefault(raw, issue)
    return list(deduped.values())


def _tool_receipt_contamination_error(relative_path: str, text: str) -> str:
    lowered = str(text or "").lower()
    if not any(token in lowered for token in _TOOL_RECEIPT_CONTAMINATION_TOKENS):
        return ""
    return (
        "Artifact quality scan failed: tool execution receipt contamination in "
        f"{relative_path}; file contains a Polaris tool failure receipt instead of source code. "
        "Rewrite this artifact with real UTF-8 project code and do not copy tool error text."
    )


def _source_narration_contamination_error(relative_path: str, text: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix not in _ARTIFACT_QUALITY_SOURCE_EXTS:
        return ""
    stripped = str(text or "").lstrip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return ""
    if not _SOURCE_NARRATION_LEAK_RE.search(stripped[:500]):
        return ""
    return (
        "Artifact quality scan failed: source narration contamination in "
        f"{relative_path}; file starts with assistant prose instead of project source code. "
        "Rewrite this artifact with real UTF-8 source only."
    )


def _scan_file_evidence(root_full: Path, full_path: Path, relative_path: str) -> _FileArtifactQualityEvidence:
    """Return legacy and typed artifact-quality findings for one file."""

    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except (OSError, RuntimeError, ValueError):
        return _FileArtifactQualityEvidence()

    receipt_error = _tool_receipt_contamination_error(relative_path, text)
    if receipt_error:
        return _FileArtifactQualityEvidence(
            errors=(receipt_error,),
            issues=(
                _file_artifact_quality_issue(
                    receipt_error,
                    relative_path,
                    code="tool_receipt_contamination",
                ),
            ),
        )

    narration_error = _source_narration_contamination_error(relative_path, text)
    if narration_error:
        return _FileArtifactQualityEvidence(
            errors=(narration_error,),
            issues=(
                _file_artifact_quality_issue(
                    narration_error,
                    relative_path,
                    code="source_narration_contamination",
                ),
            ),
        )

    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []

    def append_file_issue(
        error: str,
        *,
        code: str,
        source: str = "file_artifact_scanner",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_error = str(error or "").strip()
        if not normalized_error:
            return
        errors.append(normalized_error)
        issues.append(
            _file_artifact_quality_issue(
                normalized_error,
                relative_path,
                code=code,
                source=source,
                metadata=metadata,
            )
        )

    syntax = check_source_file_syntax(str(full_path))
    if syntax is not None and syntax.get("ok") is False:
        syntax_detail = str(syntax.get("error"))[:200]
        syntax_error = f"Artifact quality scan failed: syntax error in {relative_path}: {syntax_detail}"
        errors.append(syntax_error)
        issues.append(
            ArtifactQualityIssue(
                code="syntax_error",
                message=f"syntax error in {relative_path}: {syntax_detail}",
                path=relative_path,
                source="source_syntax_checker",
                metadata={
                    "raw": syntax_error,
                    "syntax_error": syntax_detail,
                    "diagnostic_kind": "syntax_error",
                },
            )
        )
    if os.path.basename(relative_path).lower() == "package.json":
        manifest_evidence = _scan_package_manifest_evidence(root_full, text, relative_path)
        errors.extend(manifest_evidence.errors)
        issues.extend(manifest_evidence.issues)
    if os.path.basename(relative_path).lower() == "cargo.toml":
        cargo_evidence = _scan_cargo_manifest_missing_binary_evidence(root_full, text, relative_path)
        errors.extend(cargo_evidence.errors)
        issues.extend(cargo_evidence.issues)
    if os.path.basename(relative_path).lower() == "tsconfig.json":
        tsconfig_evidence = _scan_typescript_tsconfig_evidence(text, relative_path)
        errors.extend(tsconfig_evidence.errors)
        issues.extend(tsconfig_evidence.issues)
    typescript_import_evidence = _scan_typescript_import_evidence(root_full, full_path, text, relative_path)
    errors.extend(typescript_import_evidence.errors)
    issues.extend(typescript_import_evidence.issues)
    typescript_red_flag_evidence = _scan_typescript_syntax_red_flag_evidence(root_full, full_path, text, relative_path)
    if typescript_red_flag_evidence.issues:
        # Prefer the typed, directly repairable TS red-flag contract over the
        # environment-dependent generic syntax checker projection for the same
        # file. Keeping both duplicates one defect and can schedule competing
        # repairs; the typed issue retains the precise repair archetype.
        errors = [error for error in errors if not error.startswith("Artifact quality scan failed: syntax error in ")]
        issues = [issue for issue in issues if issue.code != "syntax_error"]
    errors.extend(typescript_red_flag_evidence.errors)
    issues.extend(typescript_red_flag_evidence.issues)
    html_module_script_evidence = _scan_html_typescript_module_script_evidence(
        root_full, full_path, text, relative_path
    )
    errors.extend(html_module_script_evidence.errors)
    issues.extend(html_module_script_evidence.issues)
    for marker in _DETERMINISTIC_SCAFFOLD_MARKERS:
        if marker in text:
            append_file_issue(
                f"Artifact quality scan failed: deterministic scaffold marker {marker!r} in {relative_path}",
                code="deterministic_scaffold_marker",
                metadata={
                    "marker_kind": "deterministic_scaffold",
                    "marker_value": marker,
                },
            )
            break
    helper_count = len(_NUMERIC_HELPER_FILLER_RE.findall(text))
    if helper_count >= 5:
        append_file_issue(
            f"Artifact quality scan failed: repeated numeric helper filler in {relative_path} (count={helper_count})",
            code="repeated_numeric_helper_filler",
            metadata={"helper_count": helper_count},
        )
    if helper_count >= 3 and _GENERIC_STORE_RECORD_RE.search(text) and _GENERIC_STORE_MAP_RE.search(text):
        append_file_issue(
            f"Artifact quality scan failed: generic payload/index store scaffold in {relative_path}",
            code="generic_payload_index_store_scaffold",
            metadata={
                "helper_count": helper_count,
                "scaffold_kind": "generic_payload_index_store",
            },
        )
    patch_residue_match = _PATCH_RESIDUE_RE.search(text)
    if patch_residue_match:
        append_file_issue(
            f"Artifact quality scan failed: patch residue marker in {relative_path}",
            code="patch_residue_marker",
            metadata={
                "marker_kind": "patch_residue",
                "marker_value": patch_residue_match.group(0).strip(),
            },
        )
    if _is_test_like_artifact_path(relative_path):
        trivial_count = len(_TRIVIAL_ARITHMETIC_EXPECT_RE.findall(text))
        if trivial_count >= 3:
            append_file_issue(
                "Artifact quality scan failed: repeated trivial arithmetic placeholder "
                f"tests in {relative_path} (count={trivial_count})",
                code="repeated_trivial_arithmetic_tests",
                metadata={
                    "assertion_count": trivial_count,
                },
            )
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in issues}
    residual_errors = tuple(error for error in errors if str(error or "").strip() not in direct_issue_messages)
    string_projected_issues = sys.modules[__package__]._artifact_quality_issues_from_errors(residual_errors)
    return _FileArtifactQualityEvidence(
        errors=tuple(errors),
        issues=(*issues, *string_projected_issues),
    )
