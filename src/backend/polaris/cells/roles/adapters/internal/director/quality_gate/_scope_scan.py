"""Internal implementation module for quality_gate package (lossless split)."""

# Cross-module free names are injected by package __init__
# (_wire_cross_module_namespace). Static F821 is expected and lossless.
# Imports are intentionally complete for lossless behavior; do not strip.
# ruff: noqa: F401, F821

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.quality import (
    artifact_quality_issue_raw,
    artifact_quality_issues_for_errors,
    artifact_quality_issues_from_errors,
    build_scope_authority_decision,
    partition_paths_by_declared_scope,
    scope_authority_decision_summary,
)

from .. import execute_method as _em
from ..artifact_quality_diagnostics import (
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _build_unresolved_import_symbol_repair_block,
    _missing_unresolved_relative_import_target_files,
    _parse_missing_declared_target_files,
    _relative_import_repair_target_candidates,
)
from ..contract_verify import resolve_contract_step_verify
from ..helpers import has_successful_write_tool
from ..materialization_quality_boundary import run_materialization_quality_public_boundary
from ..materialization_quality_runtime_ports import has_materialization_quality_runtime_repair_coverage
from ..repair_profile_projection import project_repair_kernel_summary
from ..runtime_repair_tool_adapter import (
    defer_director_command_with_director_tools,
    run_runtime_repair_with_director_tools,
)
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _extract_project_declared_target_path_candidates,
    _extract_task_path_candidates,
    _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths,
    _normalize_declared_task_path,
    _path_candidate_exists_in_file_set,
    _task_has_declared_target_files,
    _task_text_blob,
    _workspace_path_exists_case_insensitive,
)
from ._package_ns import package_attr

# Cross-module symbols (defined in sibling submodules). Bare annotations
# satisfy mypy; package __init__._wire_cross_module_namespace injects
# real values into this module's __dict__ at import time.
_MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST: Any
_NPM_SCRIPT_MISSING_LOCAL_ENTRYPOINT_RE: Any
_NPM_SCRIPT_MISSING_LOCAL_MODULE_RE: Any
_filter_unresolved_import_errors_to_task_write_scope: Any
_extract_task_interface_contract: Any
_is_test_like_javascript_path: Any
_missing_workspace_file_quality_repair_target_files: Any
_npm_script_entrypoint_repair_target_candidates: Any
_partition_paths_by_task_write_scope: Any
_path_within_task_write_scope: Any
_quality_repair_execution_attempt: Any
_record_deferred_task_boundary_quality_errors: Any
_run_materialization_quality_public_boundary: Any
_single_file_step_target: Any
_task_write_scope_candidates: Any


def _quality_repair_cache_root(task: dict[str, Any], context: dict[str, Any]) -> str:
    """Resolve the cache root used by CE/runtime ledgers from local task context."""
    candidates: list[Any] = []
    for source in (context, task):
        if not isinstance(source, dict):
            continue
        candidates.extend(
            [
                source.get("cache_root"),
                source.get("cache_root_full"),
                source.get("runtime_cache_root"),
            ]
        )
        metadata = source.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend(
                [
                    metadata.get("cache_root"),
                    metadata.get("cache_root_full"),
                    metadata.get("runtime_cache_root"),
                ]
            )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def _task_boundary_requesting_task_id(task: dict[str, Any]) -> str:
    return str(task.get("id") or task.get("task_id") or task.get("external_task_id") or "").strip()


def _task_boundary_scope_filter_evidence(
    task: dict[str, Any],
    *,
    target_files: list[str],
    reason: str,
    workspace: str = "",
    cache_root: str = "",
) -> dict[str, Any]:
    workspace_token = str(workspace or "").strip()
    workspace_name = Path(workspace_token).name if workspace_token else ""
    normalized_target_files = _dedupe_preserve_order(
        [
            normalized
            for target in target_files
            if (
                normalized := _normalize_declared_task_path(
                    str(target or ""),
                    workspace_name=workspace_name,
                )
            )
        ]
    )
    decision = build_scope_authority_decision(
        workspace=workspace_token,
        cache_root=str(cache_root or "").strip(),
        task_declared_write_targets=_task_write_scope_candidates(
            task,
            workspace_name=workspace_name,
        ),
        out_of_scope_repair_target_files=normalized_target_files,
        requesting_task_id=_task_boundary_requesting_task_id(task),
        reason=reason,
    )
    scope_authority = decision.to_dict()
    summary = scope_authority_decision_summary(scope_authority, limit=12)
    return {
        "schema_version": "director.task_boundary.repair_scope_filter.v1",
        "reason": reason,
        **summary,
        "scope_authority": scope_authority,
        "deferred": True,
    }


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _semantic_exporter_scope_discrepancy_evidence(
    *,
    task: dict[str, Any],
    semantic_exporter_targets: list[str],
    repair_target_files: list[str],
    artifact_quality_errors: list[str],
    task_scope_filter_evidence: dict[str, Any],
) -> dict[str, Any]:
    task_interface_contract = _extract_task_interface_contract(task)
    declared_write_targets = _task_write_scope_candidates(task)[:12]
    out_of_scope_targets = _dedupe_preserve_order(semantic_exporter_targets)[:12]
    raw_scope_authority = task_scope_filter_evidence.get("scope_authority")
    scope_authority = dict(raw_scope_authority) if isinstance(raw_scope_authority, dict) else {}
    ownership_handoff_requests = _dict_items(task_scope_filter_evidence.get("ownership_handoff_requests"))[:12]
    owner_task_retry_handoff_requests = _dict_items(
        task_scope_filter_evidence.get("owner_task_retry_handoff_requests")
    )[:12]
    unresolved_owner_handoff_requests = _dict_items(
        task_scope_filter_evidence.get("unresolved_owner_handoff_requests")
    )[:12]
    interface_delta = {
        "schema_version": "director.interface_delta.v1",
        "contract_present": bool(task_interface_contract),
        "contract_keys": sorted(str(key) for key in task_interface_contract),
        "diagnostic_paths": repair_target_files[:12],
        "semantic_exporter_owner_targets": out_of_scope_targets,
        "task_declared_write_targets": declared_write_targets,
        "artifact_quality_errors": artifact_quality_errors[:8],
    }
    triage_summary = {
        "schema_version": "director.interface_discrepancy_triage.v1",
        "recommended_owner": "chief_engineer",
        "recommended_route": "pending_design_interface_contract",
        "contract_present": bool(task_interface_contract),
        "director_retry_allowed": False,
        "llm_fallback_blocked": True,
        "macro_blueprint_regeneration_allowed": False,
        "triage_policy": "owner_task_repair_if_contract_present_else_contract_amendment",
        "reason": "semantic_exporter_owner_outside_current_task_scope",
        "ownership_handoff_request_count": len(ownership_handoff_requests),
        "owner_task_retry_handoff_request_count": len(owner_task_retry_handoff_requests),
        "unresolved_owner_handoff_request_count": len(unresolved_owner_handoff_requests),
    }
    receipt = DirectorInterfaceDiscrepancyReceiptV1(
        task_id=str(task.get("id") or task.get("task_id") or task.get("external_task_id") or "materialization-task"),
        source="roles.adapters.materialization_quality_scope_gate",
        plan_probe_status="scope_owner_conflict",
        diagnostics=(
            {
                "kind": "semantic_exporter_owner_outside_current_task_scope",
                "semantic_exporter_owner_targets": out_of_scope_targets,
                "task_declared_write_targets": declared_write_targets,
            },
        ),
        source_tools=(),
        recommended_owner="chief_engineer",
        recommended_route="pending_design_interface_contract",
        task_interface_contract_present=bool(task_interface_contract),
        llm_fallback_blocked=True,
        director_retry_allowed=False,
        reason="semantic_exporter_owner_outside_current_task_scope",
        interface_delta=interface_delta,
        triage_summary=triage_summary,
        metadata={
            "route": "task_boundary_quality_loop",
            "cross_artifact_route": "contract_amendment_request",
            "repair_target_files": repair_target_files[:12],
            "semantic_exporter_owner_targets": out_of_scope_targets,
            "task_declared_write_targets": declared_write_targets,
            "task_scope_filter": task_scope_filter_evidence,
            "scope_authority": scope_authority,
            "ownership_handoff_requests": ownership_handoff_requests,
            "owner_task_retry_handoff_requests": owner_task_retry_handoff_requests,
            "unresolved_owner_handoff_requests": unresolved_owner_handoff_requests,
            "artifact_quality_errors": artifact_quality_errors[:8],
            "task_interface_contract_keys": sorted(str(key) for key in task_interface_contract),
        },
    ).to_dict()
    receipt.update(
        {
            "route": "task_boundary_quality_loop",
            "cross_artifact_route": "contract_amendment_request",
            "repair_target_files": repair_target_files[:12],
            "semantic_exporter_owner_targets": out_of_scope_targets,
            "task_declared_write_targets": declared_write_targets,
            "task_scope_filter": task_scope_filter_evidence,
            "scope_authority": scope_authority,
            "ownership_handoff_requests": ownership_handoff_requests,
            "owner_task_retry_handoff_requests": owner_task_retry_handoff_requests,
            "unresolved_owner_handoff_requests": unresolved_owner_handoff_requests,
            "artifact_quality_errors": artifact_quality_errors[:8],
            "task_interface_contract_keys": sorted(str(key) for key in task_interface_contract),
        }
    )
    return receipt


def _artifact_quality_issue_paths_by_raw(
    issue_payloads: tuple[dict[str, Any], ...],
) -> dict[str, str]:
    """Index typed artifact-quality paths by their raw display diagnostic.

    This keeps task-boundary filters on structured issue facts when scanner
    payloads are present, while preserving regex fallback for legacy diagnostics
    that have not been typed yet.

    Complexity:
        O(n) time and memory for ``n`` issue payloads.
    """

    paths_by_raw: dict[str, str] = {}
    for issue_payload in issue_payloads:
        raw = artifact_quality_issue_raw(issue_payload)
        if not raw or raw in paths_by_raw:
            continue
        path = str(issue_payload.get("path") or "").strip().replace("\\", "/")
        if path:
            paths_by_raw[raw] = path
    return paths_by_raw


def _filter_npm_script_entrypoint_errors_to_task_write_scope(
    errors: list[str],
    *,
    task: dict[str, Any],
    workspace_name: str = "",
    context: dict[str, Any] | None = None,
    issue_payloads: tuple[dict[str, Any], ...] = (),
) -> list[str]:
    """Defer package-script entrypoint diagnostics that belong to another task."""

    if not _task_write_scope_candidates(task, workspace_name=workspace_name):
        return errors
    project_targets = _dedupe_preserve_order(
        [
            *_extract_project_declared_target_path_candidates(context),
            *_extract_project_declared_target_path_candidates(task),
        ]
    )
    typed_issue_paths_by_raw = _artifact_quality_issue_paths_by_raw(issue_payloads)
    retained: list[str] = []
    deferred_errors: list[str] = []
    deferred_targets: list[str] = []
    for error in errors:
        text = str(error or "")
        entrypoint_match = _NPM_SCRIPT_MISSING_LOCAL_ENTRYPOINT_RE.search(text)
        if entrypoint_match:
            script_name = str(entrypoint_match.group("script") or "").strip().lower()
            entrypoint = str(entrypoint_match.group("path") or "").strip()
            candidates = _npm_script_entrypoint_repair_target_candidates(script_name, entrypoint)
            in_scope, out_of_scope = _partition_paths_by_task_write_scope(
                candidates,
                task=task,
                workspace_name=workspace_name,
            )
            owner_targets, _unowned_targets = partition_paths_by_declared_scope(
                out_of_scope,
                project_targets,
                workspace_name=workspace_name,
            )
            if candidates and not in_scope and owner_targets:
                deferred_errors.append(text)
                deferred_targets.extend(owner_targets)
                continue
        local_module_match = _NPM_SCRIPT_MISSING_LOCAL_MODULE_RE.search(text)
        if local_module_match:
            entrypoint = typed_issue_paths_by_raw.get(text) or str(local_module_match.group("entrypoint") or "").strip()
            entrypoint_owned, _entrypoint_unowned = partition_paths_by_declared_scope(
                [entrypoint] if entrypoint else [],
                project_targets,
                workspace_name=workspace_name,
            )
            if (
                entrypoint
                and not _path_within_task_write_scope(
                    entrypoint,
                    task=task,
                    workspace_name=workspace_name,
                )
                and entrypoint_owned
            ):
                deferred_errors.append(text)
                deferred_targets.append(entrypoint)
                continue
        retained.append(text)
    _record_deferred_task_boundary_quality_errors(
        context,
        errors=_dedupe_preserve_order(deferred_errors),
        target_files=_dedupe_preserve_order(deferred_targets),
        reason="npm_script_entrypoint_outside_current_task_target_files",
        issue_payloads=issue_payloads,
    )
    return _dedupe_preserve_order(retained)


def _filter_project_completion_errors_to_task_boundary(
    errors: list[str],
    *,
    task: dict[str, Any],
    workspace_name: str = "",
    context: dict[str, Any] | None = None,
    issue_payloads: tuple[dict[str, Any], ...] = (),
) -> list[str]:
    """Defer project-completion findings owned by explicit downstream targets.

    A source/model task cannot satisfy a project-level test-file obligation
    owned by a later task.  Deferral is allowed only when the project target
    inventory explicitly names downstream test/spec files and the current task
    owns none of them.  Missing or ambiguous ownership remains fail-closed.
    """

    project_targets = _dedupe_preserve_order(
        [
            *_extract_project_declared_target_path_candidates(context),
            *_extract_project_declared_target_path_candidates(task),
        ]
    )
    declared_test_targets = [target for target in project_targets if _is_test_like_javascript_path(target)]
    if not declared_test_targets:
        return errors
    current_test_targets, downstream_test_targets = _partition_paths_by_task_write_scope(
        declared_test_targets,
        task=task,
        workspace_name=workspace_name,
    )
    if current_test_targets or not downstream_test_targets:
        return errors

    issues_by_raw = {
        artifact_quality_issue_raw(issue): issue for issue in issue_payloads if artifact_quality_issue_raw(issue)
    }
    retained: list[str] = []
    deferred: list[str] = []
    for error in errors:
        text = str(error or "")
        issue = issues_by_raw.get(text)
        metadata = issue.get("metadata") if isinstance(issue, dict) else None
        script_issue = str(metadata.get("script_issue") or "") if isinstance(metadata, dict) else ""
        if script_issue == "missing_node_test_files":
            deferred.append(text)
            continue
        retained.append(text)
    _record_deferred_task_boundary_quality_errors(
        context,
        errors=_dedupe_preserve_order(deferred),
        target_files=downstream_test_targets,
        reason="project_test_targets_not_unlocked",
        issue_payloads=issue_payloads,
    )
    return _dedupe_preserve_order(retained)


def _filter_missing_workspace_file_errors_to_task_write_scope(
    errors: list[str],
    *,
    task: dict[str, Any],
    workspace_full: str,
    workspace_name: str = "",
    context: dict[str, Any] | None = None,
    issue_payloads: tuple[dict[str, Any], ...] = (),
) -> list[str]:
    """Defer verifier missing-file diagnostics that belong to another task."""

    if not _task_write_scope_candidates(task, workspace_name=workspace_name):
        return errors
    if not str(workspace_full or "").strip():
        return errors
    retained: list[str] = []
    deferred_errors: list[str] = []
    deferred_targets: list[str] = []
    project_declared_targets = _dedupe_preserve_order(
        [
            normalized
            for candidate in (
                *_extract_project_declared_target_path_candidates(context),
                *_extract_project_declared_target_path_candidates(task),
            )
            if (
                normalized := _normalize_declared_task_path(
                    candidate,
                    workspace_name=workspace_name,
                )
            )
        ]
    )
    for error in errors:
        text = str(error or "")
        rust_missing_binary = _is_rust_missing_binary_quality_error(text, issue_payloads)
        missing_targets = _dedupe_preserve_order(
            [
                *_missing_workspace_file_quality_repair_target_files(
                    artifact_quality_errors=[text],
                    workspace_full=workspace_full,
                    artifact_quality_issues=issue_payloads,
                ),
                *_missing_unresolved_relative_import_target_files(
                    [text],
                    workspace_full,
                ),
            ]
        )
        if not missing_targets:
            retained.append(text)
            continue
        defer_candidates = [target for target in missing_targets if _should_defer_missing_workspace_target(target)]
        if not defer_candidates:
            retained.append(text)
            continue
        in_scope, out_of_scope = _partition_paths_by_task_write_scope(
            defer_candidates,
            task=task,
            workspace_name=workspace_name,
        )
        if defer_candidates and not in_scope:
            if rust_missing_binary:
                if not project_declared_targets:
                    retained.append(text)
                    continue
                project_owned, project_unowned = partition_paths_by_declared_scope(
                    out_of_scope,
                    project_declared_targets,
                    workspace_name=workspace_name,
                )
                # Missing Cargo binaries are repairable by the current task only
                # when they are in its write scope. Deferral is safe only when
                # the project contract explicitly assigns every missing path to
                # another task; absent/ambiguous ownership remains fail-closed.
                if not project_owned or project_unowned:
                    retained.append(text)
                    continue
            deferred_errors.append(text)
            deferred_targets.extend(out_of_scope)
            continue
        retained.append(text)
    _record_deferred_task_boundary_quality_errors(
        context,
        errors=_dedupe_preserve_order(deferred_errors),
        target_files=_dedupe_preserve_order(deferred_targets),
        reason="missing_workspace_file_outside_current_task_target_files",
        issue_payloads=issue_payloads,
    )
    return _dedupe_preserve_order(retained)


def _is_rust_missing_binary_quality_error(
    error: str,
    issue_payloads: tuple[dict[str, Any], ...] = (),
) -> bool:
    text = str(error or "")
    if "can't find bin" in text.lower() or "cant find bin" in text.lower():
        return True
    raw = text.strip()
    for issue in issue_payloads:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "").strip()
        if code != "rust_missing_binary_entrypoint":
            continue
        issue_raw = str((issue.get("metadata") or {}).get("raw") or issue.get("message") or "").strip()
        if not raw or issue_raw == raw or raw in issue_raw or issue_raw in raw:
            return True
    return False


def _task_write_scope_touches_rust(task: Mapping[str, Any] | None, *, workspace_name: str = "") -> bool:
    normalized_task = dict(task) if isinstance(task, Mapping) else {}
    for candidate in _task_write_scope_candidates(normalized_task, workspace_name=workspace_name):
        normalized = str(candidate or "").strip().replace("\\", "/").lower()
        if not normalized:
            continue
        if normalized == "cargo.toml" or normalized.endswith("/cargo.toml"):
            return True
        if Path(normalized).suffix == ".rs":
            return True
    return False


def _should_defer_missing_workspace_target(path: str) -> bool:
    normalized = _normalize_declared_task_path(path)
    if not normalized:
        return False
    return not ("/" not in normalized and normalized in _MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST)


def _collect_materialization_quality_errors(
    adapter: Any,
    *,
    task: dict[str, Any],
    all_affected_files: list[str],
    workspace_name: str,
    context: dict[str, Any] | None = None,
    task_boundary: bool = False,
) -> list[str]:
    errors, _issues = package_attr("_collect_materialization_quality_findings")(
        adapter,
        task=task,
        all_affected_files=all_affected_files,
        workspace_name=workspace_name,
        context=context,
        task_boundary=task_boundary,
    )
    return errors


def _collect_materialization_quality_findings(
    adapter: Any,
    *,
    task: dict[str, Any],
    all_affected_files: list[str],
    workspace_name: str,
    context: dict[str, Any] | None = None,
    task_boundary: bool = False,
) -> tuple[list[str], tuple[dict[str, Any], ...]]:
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    step_target = "" if task_boundary else (_single_file_step_target(context) or _single_file_step_target(task))
    if step_target:
        # Adversarial-review C-fix: a pinned single-file step turn is judged
        # only on the file it owns. Scanning package.json or other affected
        # files would demand repairs the enum-pinned write tools cannot
        # perform — a bounce loop that can never converge; junk in other
        # files belongs to the steps that own them.
        quality_scan_paths = [step_target]
    else:
        declared_target_paths = [
            normalized
            for candidate in _extract_task_target_path_candidates(task)
            if (
                normalized := _normalize_declared_task_path(
                    candidate,
                    workspace_name=workspace_name,
                )
            )
            and Path(normalized).suffix
            and "*" not in normalized
            and "?" not in normalized
        ]
        quality_scan_paths = _materialization_quality_scan_paths_with_package_manifest(
            workspace_full=workspace_full,
            affected_files=[*all_affected_files, *declared_target_paths],
        )
    errors, scan_issues = _scan_workspace_artifact_quality_findings(
        workspace_full,
        relative_paths=quality_scan_paths,
        task_id=_materialization_quality_task_id(task, context),
    )
    declared_errors, declared_issues = _declared_target_file_quality_findings(
        workspace_full=workspace_full,
        task=task,
        workspace_name=workspace_name,
    )
    errors.extend(declared_errors)
    scan_issues = (*scan_issues, *declared_issues)
    scoped_errors = _filter_unresolved_import_errors_to_task_write_scope(
        _filter_npm_script_entrypoint_errors_to_task_write_scope(
            _dedupe_preserve_order(errors),
            task=task,
            workspace_name=workspace_name,
            context=context,
            issue_payloads=scan_issues,
        ),
        task=task,
        workspace_name=workspace_name,
    )
    boundary_errors = _filter_project_completion_errors_to_task_boundary(
        scoped_errors,
        task=task,
        workspace_name=workspace_name,
        context=context,
        issue_payloads=scan_issues,
    )
    filtered_errors = _filter_missing_workspace_file_errors_to_task_write_scope(
        boundary_errors,
        task=task,
        workspace_full=workspace_full,
        workspace_name=workspace_name,
        context=context,
        issue_payloads=scan_issues,
    )
    return filtered_errors, artifact_quality_issues_for_errors(filtered_errors, scan_issues)


def _scan_workspace_artifact_quality_findings(
    workspace_full: str,
    *,
    relative_paths: list[str],
    task_id: str = "",
) -> tuple[list[str], tuple[dict[str, Any], ...]]:
    evidence_scanner = getattr(package_attr("_em"), "scan_workspace_artifact_quality_evidence", None)
    if callable(evidence_scanner) and _execute_method_artifact_quality_scanner_is_default():
        evidence = evidence_scanner(workspace_full, relative_paths=relative_paths, task_id=task_id)
        return list(evidence.errors), tuple(issue.to_dict() for issue in evidence.issues)

    errors = package_attr("_em").scan_workspace_artifact_quality(workspace_full, relative_paths=relative_paths)
    return list(errors), package_attr("artifact_quality_issues_from_errors")(errors)


def _materialization_quality_task_id(task: dict[str, Any], context: dict[str, Any] | None) -> str:
    for source in (context, task):
        if not isinstance(source, dict):
            continue
        for key in ("target_task_id", "task_id", "id", "pm_task_id"):
            task_id = str(source.get(key) or "").strip()
            if task_id:
                return task_id
    return ""


def _execute_method_artifact_quality_scanner_is_default() -> bool:
    scanner = getattr(package_attr("_em"), "scan_workspace_artifact_quality", None)
    return (
        str(getattr(scanner, "__module__", "")).startswith("polaris.kernelone.quality.artifact_quality")
        and getattr(scanner, "__name__", "") == "scan_workspace_artifact_quality"
    )


def _materialization_quality_scan_paths_with_package_manifest(
    *,
    workspace_full: str,
    affected_files: list[str],
) -> list[str]:
    paths = _dedupe_preserve_order(
        [_normalize_declared_task_path(path) for path in affected_files if _normalize_declared_task_path(path)]
    )
    if _node_package_manifest_should_be_rescanned_for_test_files(workspace_full=workspace_full, paths=paths):
        paths.append("package.json")
    # R71/R73: Rust materialization quality must rescan Cargo.toml whenever any
    # .rs file is in scope so declared [[bin]] paths missing on disk become
    # artifact-quality errors that plan missing-binary entrypoint repair.
    if _cargo_manifest_should_be_rescanned_for_rust_files(workspace_full=workspace_full, paths=paths):
        paths.append("Cargo.toml")
    return _dedupe_preserve_order(paths)


def _node_package_manifest_should_be_rescanned_for_test_files(*, workspace_full: str, paths: list[str]) -> bool:
    package_path = Path(str(workspace_full or "")).resolve() / "package.json"
    if not package_path.is_file():
        return False
    return any(_is_node_runtime_source_path(path) for path in paths)


def _cargo_manifest_should_be_rescanned_for_rust_files(*, workspace_full: str, paths: list[str]) -> bool:
    cargo_path = Path(str(workspace_full or "")).resolve() / "Cargo.toml"
    if not cargo_path.is_file():
        return False
    return any(_is_rust_source_path(path) for path in paths) or any(
        str(path or "").strip().replace("\\", "/").lower() == "cargo.toml" for path in paths
    )


def _is_rust_source_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    return Path(normalized).suffix == ".rs"


def _is_node_runtime_source_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    name = Path(normalized).name
    if "/tests/" in f"/{normalized}" or "/test/" in f"/{normalized}" or ".test." in name or ".spec." in name:
        return False
    return Path(normalized).suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _case_insensitive_file_match(target_path: Path) -> bool:
    """Return True when a sibling file matches ``target_path``'s name ignoring case.

    PM/CE often declare a target with different casing than the file the
    Director actually wrote (declared ``readme.md`` vs disk ``README.md``). On a
    case-sensitive filesystem the strict ``is_file`` check below would report the
    declared target missing and drive a spurious materialization-quality repair
    loop that never clears — failing an otherwise-complete, runnable product.
    The write-side already collapses case variants (the case-variant redirect),
    so this scan must agree. Mirrors the existence-gate / soft-check
    case-insensitive matching (F19/F20).
    """
    name_lower = target_path.name.lower()
    if not name_lower:
        return False
    try:
        return any(entry.name.lower() == name_lower and entry.is_file() for entry in target_path.parent.iterdir())
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return False


def _declared_target_file_quality_errors(
    *,
    workspace_full: str,
    task: dict[str, Any],
    workspace_name: str = "",
) -> list[str]:
    errors, _issues = _declared_target_file_quality_findings(
        workspace_full=workspace_full,
        task=task,
        workspace_name=workspace_name,
    )
    return errors


def _declared_target_file_quality_findings(
    *,
    workspace_full: str,
    task: dict[str, Any],
    workspace_name: str = "",
) -> tuple[list[str], tuple[dict[str, Any], ...]]:
    try:
        workspace_path = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        return [], ()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return [], ()

    errors: list[str] = []
    issues: list[dict[str, Any]] = []
    for candidate in _extract_task_target_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized or any(ch in normalized for ch in ("*", "?")):
            continue
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not Path(normalized).suffix:
            continue
        if not target_path.is_file() and not _case_insensitive_file_match(target_path):
            raw_error = f"Artifact quality scan failed: declared target file missing {normalized!r}"
            errors.append(raw_error)
            issues.append(
                {
                    "code": "declared_target_missing",
                    "message": f"declared target file missing {normalized!r}",
                    "path": normalized,
                    "severity": "error",
                    "source": "declared_target_contract",
                    "metadata": {
                        "raw": raw_error,
                        "declared_target_path": normalized,
                    },
                }
            )
    return errors, tuple(issues)


def _extract_successful_write_paths(tool_results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict) or not bool(item.get("success")):
            continue
        raw_result = item.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        for key in ("file", "path"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(_normalize_declared_task_path(value))
                break
    return _dedupe_preserve_order([path for path in paths if path])


def _merge_successful_write_paths(all_affected_files: list[str], write_paths: list[str]) -> list[str]:
    return sorted({*all_affected_files, *write_paths})


def _materialization_quality_scan_paths(
    all_affected_files: list[str],
    tool_results: list[dict[str, Any]],
) -> list[str]:
    return _merge_successful_write_paths(
        all_affected_files,
        _extract_successful_write_paths(tool_results),
    )


def _run_post_llm_materialization_runtime_guard(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    context: dict[str, Any],
    changed_files: list[str],
    repair_tool_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Re-run runtime-owned materialization repairs after LLM repair writes.

    LLM repair turns may touch package/config files after an earlier
    deterministic repair fixed them. This guard performs a final artifact scan
    over the actual write set and only routes diagnostics that the
    director.runtime catalog already covers.
    """

    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        return [], {"attempted": False, "reason": "no_successful_llm_repair_write"}
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    workspace_name = Path(workspace_full).name if workspace_full else ""
    post_repair_errors, post_repair_issues = package_attr("_collect_materialization_quality_findings")(
        adapter,
        task=task,
        all_affected_files=_materialization_quality_scan_paths(changed_files, repair_tool_results),
        workspace_name=workspace_name,
        context=context,
        task_boundary=True,
    )
    if not post_repair_errors:
        return [], {"attempted": False, "reason": "post_llm_artifact_quality_clean"}
    if not _has_materialization_quality_runtime_repair_coverage(
        post_repair_errors,
        artifact_quality_issues=post_repair_issues,
    ):
        return [], {
            "attempted": False,
            "reason": "post_llm_errors_not_runtime_covered",
            "artifact_quality_errors": post_repair_errors[:20],
            "artifact_quality_issue_count": len(post_repair_issues),
        }
    guard_tool_results, guard_summary = package_attr("_run_materialization_quality_public_boundary")(
        adapter,
        task=task,
        task_id=target_task_id,
        artifact_quality_errors=post_repair_errors,
        artifact_quality_issues=post_repair_issues,
        execution_attempt=_quality_repair_execution_attempt(context),
    )
    summary = dict(guard_summary or {})
    summary.update(
        {
            "stage": "post_llm_materialization_runtime_guard",
            "attempted": True,
            "artifact_quality_errors": post_repair_errors[:20],
            "artifact_quality_issue_count": len(post_repair_issues),
            "tool_results": len(guard_tool_results),
            "write_tool_evidence": has_successful_write_tool(guard_tool_results),
        }
    )
    return guard_tool_results, summary


def _has_materialization_quality_runtime_repair_coverage(
    artifact_quality_errors: list[str],
    *,
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
) -> bool:
    try:
        return package_attr("has_materialization_quality_runtime_repair_coverage")(
            artifact_quality_errors,
            artifact_quality_issues=artifact_quality_issues,
        )
    except TypeError as exc:
        if "artifact_quality_issues" not in str(exc):
            raise
        return package_attr("has_materialization_quality_runtime_repair_coverage")(artifact_quality_errors)


__all__ = [
    "_artifact_quality_issue_paths_by_raw",
    "_cargo_manifest_should_be_rescanned_for_rust_files",
    "_case_insensitive_file_match",
    "_collect_materialization_quality_errors",
    "_collect_materialization_quality_findings",
    "_declared_target_file_quality_errors",
    "_declared_target_file_quality_findings",
    "_dict_items",
    "_execute_method_artifact_quality_scanner_is_default",
    "_extract_successful_write_paths",
    "_filter_missing_workspace_file_errors_to_task_write_scope",
    "_filter_npm_script_entrypoint_errors_to_task_write_scope",
    "_filter_project_completion_errors_to_task_boundary",
    "_has_materialization_quality_runtime_repair_coverage",
    "_is_node_runtime_source_path",
    "_is_rust_missing_binary_quality_error",
    "_is_rust_source_path",
    "_materialization_quality_scan_paths",
    "_materialization_quality_scan_paths_with_package_manifest",
    "_materialization_quality_task_id",
    "_merge_successful_write_paths",
    "_node_package_manifest_should_be_rescanned_for_test_files",
    "_quality_repair_cache_root",
    "_run_post_llm_materialization_runtime_guard",
    "_scan_workspace_artifact_quality_findings",
    "_semantic_exporter_scope_discrepancy_evidence",
    "_should_defer_missing_workspace_target",
    "_task_boundary_requesting_task_id",
    "_task_boundary_scope_filter_evidence",
    "_task_write_scope_touches_rust",
]
