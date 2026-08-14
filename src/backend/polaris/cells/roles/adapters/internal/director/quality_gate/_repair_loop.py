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
_GO_COMPILE_PATH_RE: Any
_SOURCE_REPAIR_EXTENSIONS: Any
_artifact_quality_failed_test_count: Any
_build_materialization_quality_failure_evidence_context: Any
_build_materialization_quality_repair_message: Any
_build_materialization_quality_workspace_evidence_context: Any
_deterministic_single_missing_python_module_alias_to_write_file: Any
_deterministic_single_missing_quality_repair_to_write_file: Any
_director_repair_force_existing_write_enabled: Any
_explicit_artifact_quality_repair_target_files: Any
_extract_successful_write_paths: Any
_filter_missing_workspace_file_errors_to_task_write_scope: Any
_filter_npm_script_entrypoint_errors_to_task_write_scope: Any
_format_quality_error_for_repair_prompt: Any
_go_runtime_smoke_repair_target_files: Any
_has_non_test_python_traceback_source: Any
_is_generated_quality_repair_target: Any
_is_rust_missing_binary_quality_error: Any
_is_test_like_javascript_path: Any
_is_test_like_python_path: Any
_javascript_runtime_smoke_repair_target_files: Any
_looks_like_go_workspace_quality_error: Any
_looks_like_python_missing_module_failure: Any
_looks_like_python_test_behavior_failure: Any
_looks_like_python_test_harness_quality_failure: Any
_missing_materialization_quality_repair_target_files: Any
_missing_npm_script_entrypoint_repair_target_files: Any
_missing_python_module_alias_repair_target_files: Any
_missing_workspace_file_quality_repair_target_files: Any
_partition_paths_by_task_write_scope: Any
_python_runtime_smoke_repair_target_files: Any
_quality_repair_cache_root: Any
_quality_repair_edit_file_tool_definition: Any
_quality_repair_execution_attempt: Any
_quality_repair_existing_target_tool_definitions: Any
_quality_repair_write_file_tool_definition: Any
_reject_raw_single_target_repair_body: Any
_run_materialization_quality_public_boundary: Any
_run_post_llm_materialization_runtime_guard: Any
_semantic_exporter_scope_discrepancy_evidence: Any
_semantic_quality_exporting_module_targets: Any
_semantic_quality_repair_target_files: Any
_summarize_llm_stage_result: Any
_task_boundary_scope_filter_evidence: Any
_tool_receipt_safe_quality_errors: Any


async def _run_materialization_quality_repair_retry(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    original_message: str,
    llm_call_timeout: float,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    repair_attempt: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask Director for one concrete repair when changed artifacts fail quality gates."""

    if not artifact_quality_errors:
        return [], {"attempted": False, "reason": "no_artifact_quality_errors"}

    workspace_full = str(getattr(adapter, "workspace", "") or "")
    cache_root_full = _quality_repair_cache_root(task, context)
    repair_quality_errors = _tool_receipt_safe_quality_errors(artifact_quality_errors)
    deferred_scope_context: dict[str, Any] = {}
    repair_quality_errors = _filter_npm_script_entrypoint_errors_to_task_write_scope(
        repair_quality_errors,
        task=task,
        context=deferred_scope_context,
    )
    repair_quality_errors = _filter_missing_workspace_file_errors_to_task_write_scope(
        repair_quality_errors,
        task=task,
        workspace_full=workspace_full,
        context=deferred_scope_context,
    )
    deferred_scope_records = deferred_scope_context.get("director_task_boundary_deferred_quality_errors")
    deferred_scope_targets: list[str] = []
    deferred_scope_reasons: list[str] = []
    if isinstance(deferred_scope_records, list):
        for record in deferred_scope_records:
            if not isinstance(record, dict):
                continue
            raw_reason = str(record.get("reason") or "").strip()
            if raw_reason:
                deferred_scope_reasons.append(raw_reason)
            raw_targets = record.get("target_files")
            if isinstance(raw_targets, list):
                deferred_scope_targets.extend(str(item) for item in raw_targets if str(item or "").strip())
    task_scope_filter_evidence: dict[str, Any] = {}
    if deferred_scope_targets:
        scope_filter_reason = (
            deferred_scope_reasons[0]
            if len(set(deferred_scope_reasons)) == 1
            else "quality_repair_targets_outside_current_task_target_files"
        )
        task_scope_filter_evidence = _task_boundary_scope_filter_evidence(
            task,
            target_files=deferred_scope_targets,
            reason=scope_filter_reason,
            workspace=workspace_full,
            cache_root=cache_root_full,
        )
    if not repair_quality_errors and task_scope_filter_evidence:
        return [], {
            "stage": "task_boundary_repair_targets_deferred",
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "success_reason": "repair_targets_outside_current_task_target_files",
            "tool_results": 0,
            "write_tool_evidence": False,
            "missing_target_files": [],
            "runtime_smoke_target_files": [],
            "semantic_quality_target_files": [],
            "explicit_quality_target_files": [],
            "repair_target_files": [],
            "rotated_repair_targets": False,
            "task_boundary_scope_filter": task_scope_filter_evidence,
            "llm_fallback_blocked": True,
        }
    missing_target_files = _missing_materialization_quality_repair_target_files(
        task,
        workspace_full,
        repair_quality_errors,
    )
    missing_script_entrypoint_files = _missing_npm_script_entrypoint_repair_target_files(
        artifact_quality_errors=repair_quality_errors,
        workspace_full=workspace_full,
    )
    missing_script_entrypoint_files, out_of_scope_script_entrypoint_files = _partition_paths_by_task_write_scope(
        missing_script_entrypoint_files,
        task=task,
    )
    missing_target_files = _dedupe_preserve_order([*missing_target_files, *missing_script_entrypoint_files])
    runtime_smoke_target_files = _dedupe_preserve_order(
        [
            *_python_runtime_smoke_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
            *_javascript_runtime_smoke_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
            *_go_runtime_smoke_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
        ]
    )
    semantic_quality_target_files = _semantic_quality_repair_target_files(
        artifact_quality_errors=repair_quality_errors,
        changed_files=changed_files,
        workspace_full=workspace_full,
    )
    semantic_exporter_owner_targets: list[str] = []
    if workspace_full:
        workspace_root = Path(workspace_full).resolve()
        if workspace_root.is_dir():
            semantic_exporter_owner_targets, _ = _semantic_quality_exporting_module_targets(
                repair_quality_errors,
                workspace_root,
            )
    explicit_quality_target_files = _dedupe_preserve_order(
        [
            *_explicit_artifact_quality_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                changed_files=changed_files,
                workspace_full=workspace_full,
            ),
            *_verifier_test_failure_target_files(
                artifact_quality_errors=repair_quality_errors,
                workspace_full=workspace_full,
            ),
        ]
    )
    explicit_missing_quality_targets = _dedupe_preserve_order(
        [
            *_parse_missing_declared_target_files(repair_quality_errors),
            *_missing_unresolved_relative_import_target_files(repair_quality_errors, workspace_full),
            *_missing_workspace_file_quality_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                workspace_full=workspace_full,
            ),
            *_missing_python_module_alias_repair_target_files(
                artifact_quality_errors=repair_quality_errors,
                workspace_full=workspace_full,
            ),
            *missing_script_entrypoint_files,
        ]
    )
    if out_of_scope_script_entrypoint_files:
        merged_out_of_scope = [
            *list(task_scope_filter_evidence.get("out_of_scope_repair_target_files", [])),
            *out_of_scope_script_entrypoint_files,
        ]
        task_scope_filter_evidence = _task_boundary_scope_filter_evidence(
            task,
            target_files=merged_out_of_scope,
            reason="npm_script_entrypoint_outside_current_task_target_files",
            workspace=workspace_full,
            cache_root=cache_root_full,
        )
    should_merge_missing_targets = bool(explicit_missing_quality_targets) or not (
        runtime_smoke_target_files or semantic_quality_target_files or explicit_quality_target_files
    )
    repair_target_candidates = _ordered_materialization_quality_repair_target_candidates(
        missing_target_files=missing_target_files,
        runtime_smoke_target_files=runtime_smoke_target_files,
        semantic_quality_target_files=semantic_quality_target_files,
        explicit_quality_target_files=explicit_quality_target_files,
        should_merge_missing_targets=should_merge_missing_targets,
    )
    rotate_repair_targets = bool(
        len(repair_target_candidates) > 1
        and semantic_quality_target_files
        and _should_rotate_materialization_quality_repair_targets(repair_quality_errors)
    )
    repair_target_files = _select_materialization_quality_repair_target_batch(
        repair_target_candidates,
        repair_attempt=repair_attempt,
        rotate_after_first_attempt=rotate_repair_targets,
        preserve_batch_after_first_attempt=_should_preserve_materialization_quality_repair_batch(
            repair_quality_errors,
            repair_target_candidates=repair_target_candidates,
        ),
    )
    in_scope_repair_target_files, out_of_scope_repair_target_files = _partition_paths_by_task_write_scope(
        repair_target_files,
        task=task,
    )
    task_boundary_discrepancy_evidence: dict[str, Any] = {}
    if out_of_scope_repair_target_files:
        merged_out_of_scope = [
            *list(task_scope_filter_evidence.get("out_of_scope_repair_target_files", [])),
            *out_of_scope_repair_target_files,
        ]
        task_scope_filter_evidence = _task_boundary_scope_filter_evidence(
            task,
            target_files=merged_out_of_scope,
            reason="quality_repair_targets_outside_current_task_target_files",
            workspace=workspace_full,
            cache_root=cache_root_full,
        )
        semantic_exporter_owner_target_set = set(semantic_exporter_owner_targets)
        out_of_scope_exporter_owner_targets = [
            path for path in out_of_scope_repair_target_files if path in semantic_exporter_owner_target_set
        ]
        if out_of_scope_exporter_owner_targets:
            task_boundary_discrepancy_evidence = _semantic_exporter_scope_discrepancy_evidence(
                task=task,
                semantic_exporter_targets=out_of_scope_exporter_owner_targets,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
                task_scope_filter_evidence=task_scope_filter_evidence,
            )
            return [], {
                "stage": "task_boundary_semantic_exporter_scope_conflict",
                "attempted": True,
                "attempt": repair_attempt,
                "success": False,
                "success_reason": "task_boundary_interface_discrepancy_required",
                "tool_results": 0,
                "write_tool_evidence": False,
                "missing_target_files": missing_target_files[:12],
                "runtime_smoke_target_files": runtime_smoke_target_files[:12],
                "semantic_quality_target_files": semantic_quality_target_files[:12],
                "explicit_quality_target_files": explicit_quality_target_files[:12],
                "repair_target_files": repair_target_files[:12],
                "rotated_repair_targets": rotate_repair_targets,
                "task_boundary_scope_filter": task_scope_filter_evidence,
                "interface_discrepancy_evidence": task_boundary_discrepancy_evidence,
                "llm_fallback_blocked": True,
            }
        repair_target_files = in_scope_repair_target_files
    if task_scope_filter_evidence and not repair_target_files:
        return [], {
            "stage": "task_boundary_repair_targets_deferred",
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "success_reason": "repair_targets_outside_current_task_target_files",
            "tool_results": 0,
            "write_tool_evidence": False,
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": [],
            "rotated_repair_targets": False,
            "task_boundary_scope_filter": task_scope_filter_evidence,
            "llm_fallback_blocked": True,
        }
    missing_target_set = set(missing_target_files)
    missing_repair_target_files = [path for path in repair_target_files if path in missing_target_set]
    existing_repair_target_files = [path for path in repair_target_files if path not in missing_target_set]
    quality_repair_timeout = _resolve_quality_repair_timeout_seconds(llm_call_timeout)
    deadline_decision = _quality_repair_deadline_decision(context, quality_repair_timeout)
    if not bool(deadline_decision.get("can_start")):
        return [], {
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "error": str(deadline_decision.get("reason") or "quality_repair_deadline_insufficient"),
            "error_code": "quality_repair_deadline_insufficient",
            "tool_results": 0,
            "write_tool_evidence": False,
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": repair_target_files[:12],
            "rotated_repair_targets": rotate_repair_targets,
            "deadline_decision": deadline_decision,
        }
    deterministic_quality_tool_results: list[dict[str, Any]] = []
    deterministic_quality_summary: dict[str, Any] = {}
    # Missing declared Cargo [[bin]] entrypoints are plannable create-file repairs
    # (R71+). They must not be excluded from the runtime materialization schedule
    # just because they also appear in missing_repair_target_files (LLM write path).
    rust_missing_bin_present = any(_is_rust_missing_binary_quality_error(error, ()) for error in repair_quality_errors)
    if (not missing_repair_target_files or rust_missing_bin_present) and (
        _has_scaffold_marker_quality_error(repair_quality_errors)
        or package_attr("has_materialization_quality_runtime_repair_coverage")(repair_quality_errors)
        or rust_missing_bin_present
    ):
        deterministic_quality_tool_results, deterministic_quality_summary = package_attr(
            "_run_materialization_quality_public_boundary"
        )(
            adapter,
            task=task,
            task_id=target_task_id,
            artifact_quality_errors=repair_quality_errors,
            execution_attempt=_quality_repair_execution_attempt(context),
        )
    deterministic_quality_write_paths = _extract_successful_write_paths(deterministic_quality_tool_results)
    missing_targets_repaired_by_deterministic_quality = all(
        path in set(deterministic_quality_write_paths) for path in missing_repair_target_files
    )
    deterministic_quality_source_tools = {
        str(item or "")
        for item in (deterministic_quality_summary or {}).get("source_tools", [])
        if str(item or "").strip()
    }
    deterministic_quality_package_only = bool(deterministic_quality_source_tools) and all(
        "npm_script" in source_tool or "package" in source_tool or "manifest" in source_tool
        for source_tool in deterministic_quality_source_tools
    )
    deterministic_quality_left_source_targets = deterministic_quality_package_only and any(
        path not in {"package.json"} and not path.endswith("/package.json") for path in repair_target_files
    )
    deterministic_quality_can_short_circuit = not missing_repair_target_files or (
        missing_targets_repaired_by_deterministic_quality and bool(deterministic_quality_write_paths)
    )
    if deterministic_quality_left_source_targets:
        deterministic_quality_can_short_circuit = False
    if _materialization_plan_probe_requires_task_boundary_triage(deterministic_quality_summary):
        summary = dict(deterministic_quality_summary or {})
        task_boundary_discrepancy_evidence = _materialization_interface_discrepancy_evidence(
            task=task,
            plan_probe=dict(summary.get("plan_probe_preaudit") or {}),
            repair_target_files=repair_target_files,
            artifact_quality_errors=repair_quality_errors,
        )
        if _materialization_interface_discrepancy_retry_authorized(
            context=context,
            evidence=task_boundary_discrepancy_evidence,
        ):
            summary["task_boundary_interface_discrepancy_retry_authorized"] = True
            summary["interface_discrepancy_evidence"] = task_boundary_discrepancy_evidence
        else:
            summary.update(
                {
                    "stage": "runtime_plan_probe_unplannable",
                    "attempted": True,
                    "attempt": repair_attempt,
                    "success": False,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "tool_results": len(deterministic_quality_tool_results),
                    "write_tool_evidence": False,
                    "missing_target_files": missing_target_files[:12],
                    "runtime_smoke_target_files": runtime_smoke_target_files[:12],
                    "semantic_quality_target_files": semantic_quality_target_files[:12],
                    "explicit_quality_target_files": explicit_quality_target_files[:12],
                    "repair_target_files": repair_target_files[:12],
                    "rotated_repair_targets": rotate_repair_targets,
                    "interface_discrepancy_evidence": task_boundary_discrepancy_evidence,
                    "llm_fallback_blocked": True,
                }
            )
            return deterministic_quality_tool_results, summary
    if (
        deterministic_quality_tool_results
        and has_successful_write_tool(deterministic_quality_tool_results)
        and deterministic_quality_can_short_circuit
    ):
        summary = dict(deterministic_quality_summary or {})
        summary.update(
            {
                "stage": "deterministic_materialization_quality_repair",
                "attempted": True,
                "attempt": repair_attempt,
                "success": False,
                "success_reason": "repair_actions_require_quality_gate_rerun",
                "tool_results": len(deterministic_quality_tool_results),
                "write_tool_evidence": True,
                "missing_target_files": missing_target_files[:12],
                "runtime_smoke_target_files": runtime_smoke_target_files[:12],
                "semantic_quality_target_files": semantic_quality_target_files[:12],
                "explicit_quality_target_files": explicit_quality_target_files[:12],
                "repair_target_files": repair_target_files[:12],
                "rotated_repair_targets": rotate_repair_targets,
                "repair_kernel": project_repair_kernel_summary(
                    stage="deterministic_materialization_quality_repair",
                    tool_results=deterministic_quality_tool_results,
                    artifact_quality_errors=repair_quality_errors,
                ),
            }
        )
        return deterministic_quality_tool_results, summary
    prompt_artifact_quality_errors = _filter_materialization_quality_errors_for_repair_targets(
        artifact_quality_errors,
        repair_target_files,
    )
    prompt_safe_artifact_quality_errors = [
        _format_quality_error_for_repair_prompt(error) for error in prompt_artifact_quality_errors[:20]
    ]
    repair_message = _build_materialization_quality_repair_message(
        original_message=original_message,
        artifact_quality_errors=prompt_artifact_quality_errors,
        directive_artifact_quality_errors=artifact_quality_errors,
        changed_files=changed_files,
        missing_target_files=missing_repair_target_files,
        repair_target_files=existing_repair_target_files,
        workspace_full=workspace_full,
        interface_discrepancy_evidence=task_boundary_discrepancy_evidence,
    )
    failed_gate_evidence = _build_materialization_quality_failure_evidence_context(
        artifact_quality_errors=prompt_artifact_quality_errors,
        missing_target_files=missing_target_files,
        repair_target_files=repair_target_files,
        changed_files=changed_files,
        repair_attempt=repair_attempt,
    )
    workspace_quality_evidence = _build_materialization_quality_workspace_evidence_context(
        artifact_quality_errors=prompt_artifact_quality_errors,
        missing_target_files=missing_target_files,
        repair_target_files=repair_target_files,
        changed_files=changed_files,
        repair_attempt=repair_attempt,
    )
    repair_context = {
        **dict(context or {}),
        "run_id": run_id,
        "task_id": target_task_id,
        # Keep full task so dependency-artifact rebind can rebuild sibling exports
        # without relying on a non-serializable trusted token from the first turn.
        "task": dict(task) if isinstance(task, dict) else task,
        "delivery_mode": "materialize_changes",
        "failed_gate_evidence": failed_gate_evidence,
        "failure_evidence": failed_gate_evidence,
        "workspace_quality_evidence": workspace_quality_evidence,
        "director_quality_repair": {
            "artifact_quality_errors": prompt_safe_artifact_quality_errors,
            "changed_files": changed_files[:40],
            "missing_target_files": missing_target_files[:20],
            "runtime_smoke_target_files": runtime_smoke_target_files[:20],
            "semantic_quality_target_files": semantic_quality_target_files[:20],
            "explicit_quality_target_files": explicit_quality_target_files[:20],
            "repair_target_files": repair_target_files[:12],
            "failed_gate_evidence": failed_gate_evidence,
            "workspace_quality_evidence": workspace_quality_evidence,
        },
    }
    promote_task_contract = getattr(adapter, "_promote_task_contract_to_runtime_context", None)
    if callable(promote_task_contract):
        promote_task_contract(
            task=task,
            context=repair_context,
            workspace=workspace_full,
        )
    rebind_dependency_artifact = getattr(adapter, "_rebind_director_dependency_artifact_for_dialogue", None)
    if callable(rebind_dependency_artifact) and isinstance(task, dict):
        rebind_dependency_artifact(repair_context)
    if task_scope_filter_evidence:
        repair_context["director_quality_repair"]["task_boundary_scope_filter"] = task_scope_filter_evidence
    if task_boundary_discrepancy_evidence:
        repair_context["director_quality_repair"]["interface_discrepancy_evidence"] = task_boundary_discrepancy_evidence
        interface_retry_context = {
            "authorized": True,
            "recommended_owner": task_boundary_discrepancy_evidence.get("recommended_owner"),
            "recommended_route": task_boundary_discrepancy_evidence.get("recommended_route"),
            "reason": task_boundary_discrepancy_evidence.get("reason"),
            "interface_discrepancy_evidence": task_boundary_discrepancy_evidence,
        }
        repair_context["director_interface_discrepancy_retry"] = interface_retry_context
        repair_context["task_boundary_interface_discrepancy_retry"] = interface_retry_context
    if (
        task_boundary_discrepancy_evidence
        or isinstance(repair_context.get("director_interface_discrepancy_retry"), dict)
        or isinstance(repair_context.get("task_boundary_interface_discrepancy_retry"), dict)
    ):
        raw_required_evidence = repair_context.get("required_evidence")
        required_evidence_source = raw_required_evidence if isinstance(raw_required_evidence, list) else []
        required_evidence = [str(item) for item in required_evidence_source if str(item or "").strip()]
        if "interface_discrepancy_context" not in required_evidence:
            required_evidence.append("interface_discrepancy_context")
        repair_context["required_evidence"] = required_evidence
    if repair_target_files:
        repair_context["repair_target_files"] = repair_target_files[:12]
        # Provider-native tool schemas remain registry-faithful, so turn-local
        # path authority is projected as structured context instead of mutating
        # ToolSpecRegistry. Live L1-04 r51 emitted a valid ``edit_file`` with
        # the complete SEARCH/REPLACE body but targeted
        # ``/tmp/repair_engine_rules.go``. DEO correctly rejected it as
        # ``deo_path_scope_denied``; without this explicit contract, the next
        # attempt repeated the whole 23k-token repair context with no effect.
        repair_context["director_quality_repair"]["authorized_tool_target_files"] = repair_target_files[:12]
    repair_metadata = repair_context.get("metadata")
    if not isinstance(repair_metadata, dict):
        repair_metadata = {}
        repair_context["metadata"] = repair_metadata
    repair_metadata["delivery_mode"] = "materialize_changes"
    repair_metadata["task_id"] = target_task_id
    if repair_target_files:
        repair_metadata["tool_path_contract"] = {
            "schema_version": "director.quality_repair.tool_path_contract.v1",
            "allowed_target_files": repair_target_files[:12],
            "workspace_relative_only": True,
            "temporary_staging_forbidden": True,
            "path_scope_denial_is_not_mutation": True,
        }
    verifier_test_failure_requires_edit = bool(
        existing_repair_target_files
        and not missing_repair_target_files
        and _contains_verifier_test_failure(prompt_artifact_quality_errors)
    )
    if repair_target_files:
        if missing_repair_target_files and not existing_repair_target_files:
            # Missing-file repair is creation, so keep the historically narrow
            # write-only path. Existing-file compiler/test repair is different:
            # forcing whole-file writes steers weak models into destructive
            # shrink attempts, so that case uses edit-preferred tools below.
            repair_context["_transaction_kernel_forced_tool_choice"] = {
                "type": "function",
                "function": {"name": "write_file"},
            }
            repair_context["_transaction_kernel_forced_tool_definitions"] = [
                _quality_repair_write_file_tool_definition()
            ]
            repair_context["_transaction_kernel_force_exact_tools"] = True
        elif verifier_test_failure_requires_edit:
            # A verifier assertion against existing code is a mutation task,
            # not another exploration turn.  r46 exposed the full TAP failure
            # and current source bodies but the auto tool surface still let the
            # model read/run commands without editing; two no-op rounds then
            # tripped stagnation.  Force the smallest authoritative effect.
            # The prompt already carries the current UTF-8 bodies, so an exact
            # edit_file SEARCH/REPLACE can be emitted without a discovery call.
            repair_context["_transaction_kernel_forced_tool_definitions"] = [
                _quality_repair_edit_file_tool_definition(),
            ]
            repair_context["_transaction_kernel_forced_tool_choice"] = {
                "type": "function",
                "function": {"name": "edit_file"},
            }
            repair_context["_transaction_kernel_force_exact_tools"] = True
            repair_metadata["tool_contract"] = {
                **dict(repair_metadata.get("tool_contract") or {}),
                "required_tools": ["edit_file"],
                "mutation_required": True,
                "mutation_reason": "verifier_test_failure",
            }
            repair_context["director_quality_repair"]["edit_preferred_target_files"] = existing_repair_target_files[:12]
        elif _director_repair_force_existing_write_enabled():
            # Existing-file repair must MUTATE, not explore.  The repair prompt already
            # embeds the exact current UTF-8 file bodies, so a bootstrap read is neither
            # necessary nor useful in this single-batch path.  Force the smallest
            # authoritative mutation: ``edit_file``.  This avoids both the live r49
            # failure (two read-only rounds for a missing Python re-export) and the
            # destructive whole-file rewrites formerly encouraged by ``write_file``.
            # The provider only reliably honors the function-object tool_choice form.
            repair_context["_transaction_kernel_forced_tool_definitions"] = [
                _quality_repair_edit_file_tool_definition(),
            ]
            repair_context["_transaction_kernel_forced_tool_choice"] = {
                "type": "function",
                "function": {"name": "edit_file"},
            }
            repair_context["_transaction_kernel_force_exact_tools"] = True
            repair_metadata["tool_contract"] = {
                **dict(repair_metadata.get("tool_contract") or {}),
                "required_tools": ["edit_file"],
                "mutation_required": True,
                "mutation_reason": "existing_file_quality_repair",
            }
            repair_context["director_quality_repair"]["edit_preferred_target_files"] = existing_repair_target_files[:12]
        else:
            repair_context["_transaction_kernel_forced_tool_definitions"] = (
                _quality_repair_existing_target_tool_definitions()
            )
            repair_metadata["tool_contract"] = {
                **dict(repair_metadata.get("tool_contract") or {}),
                "required_tools": ["edit_file"],
            }
            repair_context["director_quality_repair"]["edit_preferred_target_files"] = existing_repair_target_files[:12]
        if len(missing_repair_target_files) == 1 and not existing_repair_target_files:
            # Single-missing: also name the specific target file in the
            # context, so any downstream code that special-cases a single
            # target can read it from director_quality_repair.
            repair_context["director_quality_repair"]["write_only_single_target"] = {
                "tool": "write_file",
                "target_file": missing_repair_target_files[0],
            }
    repair_context["director_quality_repair"]["deadline_decision"] = deadline_decision
    try:
        result = await adapter._invoke_role_dialogue_with_timeout(
            repair_message,
            context=repair_context,
            timeout_seconds=float(deadline_decision.get("timeout_seconds") or quality_repair_timeout),
            stage_label="quality_repair" if repair_attempt <= 1 else f"quality_repair_{repair_attempt}",
        )
    except Exception as exc:  # noqa: BLE001 - quality repair is a structured fallback boundary.
        repair_tool_results: list[dict[str, Any]] = []
        repair_tool_results.extend(
            _deterministic_single_missing_quality_repair_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
                context=context,
                base_file_candidates=changed_files,
            )
        )
        if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
            repair_tool_results.extend(
                _deterministic_single_missing_python_module_alias_to_write_file(
                    adapter,
                    task_id=target_task_id,
                    repair_target_files=repair_target_files,
                    artifact_quality_errors=repair_quality_errors,
                    context=context,
                    base_file_candidates=changed_files,
                )
            )
        summary = {
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "error": str(exc),
            "tool_results": len(repair_tool_results),
            "write_tool_evidence": has_successful_write_tool(repair_tool_results),
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": repair_target_files[:12],
            "rotated_repair_targets": rotate_repair_targets,
            "deadline_decision": deadline_decision,
        }
        if task_scope_filter_evidence:
            summary["task_boundary_scope_filter"] = task_scope_filter_evidence
        return repair_tool_results, summary

    content = str(result.get("content") or "")
    repair_tool_results = adapter._execution.extract_kernel_tool_results(result)
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        allowed_tool_names = None
        allow_patch_fallback = True
        if repair_target_files and not existing_repair_target_files:
            allowed_tool_names = {"write_file"}
            allow_patch_fallback = False
        elif verifier_test_failure_requires_edit or (
            repair_target_files and _director_repair_force_existing_write_enabled()
        ):
            allowed_tool_names = {"edit_file"}
            allow_patch_fallback = False
        elif repair_target_files:
            allowed_tool_names = {"edit_file", "write_file", "execute_command"}
        fallback_tool_results = await adapter._execution.execute_tools(
            content,
            target_task_id,
            adapter._update_task_progress,
            allowed_tool_names=allowed_tool_names,
            allow_patch_fallback=allow_patch_fallback,
        )
        if fallback_tool_results:
            repair_tool_results.extend(fallback_tool_results)
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        repair_tool_results.extend(
            _reject_raw_single_target_repair_body(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                content=content,
            )
        )
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        repair_tool_results.extend(
            _deterministic_single_missing_quality_repair_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
                context=context,
                base_file_candidates=changed_files,
            )
        )
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        repair_tool_results.extend(
            _deterministic_single_missing_python_module_alias_to_write_file(
                adapter,
                task_id=target_task_id,
                repair_target_files=repair_target_files,
                artifact_quality_errors=repair_quality_errors,
                context=context,
                base_file_candidates=changed_files,
            )
        )

    summary = _summarize_llm_stage_result(result, stage="quality_repair")
    summary.update(
        {
            "attempted": True,
            "attempt": repair_attempt,
            "tool_results": len(repair_tool_results),
            "write_tool_evidence": has_successful_write_tool(repair_tool_results),
            "missing_target_files": missing_target_files[:12],
            "runtime_smoke_target_files": runtime_smoke_target_files[:12],
            "semantic_quality_target_files": semantic_quality_target_files[:12],
            "explicit_quality_target_files": explicit_quality_target_files[:12],
            "repair_target_files": repair_target_files[:12],
            "rotated_repair_targets": rotate_repair_targets,
            "deadline_decision": deadline_decision,
        }
    )
    if task_boundary_discrepancy_evidence:
        summary["task_boundary_interface_discrepancy_retry_authorized"] = True
        summary["interface_discrepancy_evidence"] = task_boundary_discrepancy_evidence
    if task_scope_filter_evidence:
        summary["task_boundary_scope_filter"] = task_scope_filter_evidence
    guard_tool_results, guard_summary = _run_post_llm_materialization_runtime_guard(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        changed_files=changed_files,
        repair_tool_results=repair_tool_results,
    )
    if guard_tool_results:
        repair_tool_results.extend(guard_tool_results)
        summary["tool_results"] = len(repair_tool_results)
        summary["write_tool_evidence"] = has_successful_write_tool(repair_tool_results)
    if guard_summary.get("attempted"):
        summary["post_llm_materialization_runtime_guard"] = guard_summary
    return repair_tool_results, summary


_QUALITY_REPAIR_BASE_ATTEMPTS = 2


_QUALITY_REPAIR_ATTEMPT_HARD_CAP = 5


_QUALITY_REPAIR_TARGET_BATCH_LIMIT = 12


_DEFAULT_QUALITY_REPAIR_TIMEOUT_SECONDS = 180.0
_QUALITY_REPAIR_DEADLINE_MIN_LLM_SECONDS = 30.0
_QUALITY_REPAIR_DEADLINE_DEFAULT_SAFETY_SECONDS = 35.0


def _context_float_value(value: Any, key: str, *, depth: int = 0) -> float | None:
    if depth > 5:
        return None
    if isinstance(value, dict):
        if key in value:
            try:
                parsed = float(value[key])
            except (TypeError, ValueError):
                parsed = 0.0
            if parsed > 0:
                return parsed
        for item in value.values():
            found = _context_float_value(item, key, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _context_float_value(item, key, depth=depth + 1)
            if found is not None:
                return found
    return None


def _quality_repair_deadline_decision(context: dict[str, Any], requested_timeout_seconds: float) -> dict[str, Any]:
    # Factory publishes both the whole-run deadline and the tighter deadline for
    # the current Director dispatch wave.  Quality repair is part of that wave,
    # so it must honor whichever authority expires first.  Reading only the
    # whole-run field made live Director contexts report ``no_factory_deadline``
    # even though ``factory_director_execution_deadline_epoch_seconds`` was
    # present.  A late repair then started a full 180-second LLM call, consumed
    # the settlement/verifier reserve, and left TaskRuntime active.
    deadline_candidates = [
        (key, value)
        for key in (
            "factory_run_deadline_epoch_seconds",
            "factory_director_execution_deadline_epoch_seconds",
        )
        if (value := _context_float_value(context, key)) is not None
    ]
    if not deadline_candidates:
        return {
            "can_start": True,
            "timeout_seconds": requested_timeout_seconds,
            "reason": "no_factory_deadline",
        }
    deadline_source, deadline_epoch = min(deadline_candidates, key=lambda item: item[1])
    safety_seconds = (
        _context_float_value(context, "factory_run_deadline_safety_seconds")
        or _QUALITY_REPAIR_DEADLINE_DEFAULT_SAFETY_SECONDS
    )
    safety_seconds = max(_QUALITY_REPAIR_DEADLINE_DEFAULT_SAFETY_SECONDS, float(safety_seconds))
    remaining_seconds = max(0.0, deadline_epoch - time.time())
    available_seconds = remaining_seconds - safety_seconds
    if available_seconds < _QUALITY_REPAIR_DEADLINE_MIN_LLM_SECONDS:
        return {
            "can_start": False,
            "timeout_seconds": 0.0,
            "reason": "factory_deadline_insufficient",
            "remaining_seconds": round(remaining_seconds, 3),
            "safety_seconds": round(safety_seconds, 3),
            "minimum_llm_seconds": _QUALITY_REPAIR_DEADLINE_MIN_LLM_SECONDS,
            "deadline_source": deadline_source,
        }
    return {
        "can_start": True,
        "timeout_seconds": max(0.1, min(float(requested_timeout_seconds), available_seconds)),
        "reason": "factory_deadline_budgeted",
        "remaining_seconds": round(remaining_seconds, 3),
        "safety_seconds": round(safety_seconds, 3),
        "deadline_source": deadline_source,
    }


def _materialization_plan_probe_requires_task_boundary_triage(summary: dict[str, Any]) -> bool:
    if bool(summary.get("task_boundary_director_continuation_allowed")):
        return False
    plan_probe = summary.get("plan_probe_preaudit")
    if not isinstance(plan_probe, dict):
        return False
    if str(plan_probe.get("status") or "") != "coverage_matched_but_unplannable":
        return False
    if bool(plan_probe.get("plannable_source_tools")):
        return False
    covered_unplannable_source_tools = [
        str(item or "") for item in plan_probe.get("covered_unplannable_source_tools") or []
    ]
    return bool(covered_unplannable_source_tools or plan_probe.get("covered_unplannable_diagnostic_count"))


def _annotate_current_task_missing_target_continuation(
    summary: dict[str, Any],
    *,
    task: dict[str, Any],
    workspace_full: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Allow Director continuation for missing files owned by the current task.

    ``coverage_matched_but_unplannable`` is still a hard triage signal for
    interface discrepancies. Missing declared target files are different: they
    are incomplete materialization within the current task boundary, so the
    adapter should continue through the governed Director repair turn rather
    than treat the probe as a CE contract amendment.
    """

    if not _materialization_plan_probe_requires_task_boundary_triage(summary):
        return summary
    workspace = str(workspace_full or "").strip()
    if not workspace:
        return summary
    missing_targets = _missing_materialization_quality_repair_target_files(
        task,
        workspace,
        artifact_quality_errors,
        artifact_quality_issues,
    )
    in_scope_missing, out_of_scope_missing = _partition_paths_by_task_write_scope(missing_targets, task=task)
    if not in_scope_missing:
        return summary

    updated = dict(summary)
    updated["task_boundary_director_continuation_allowed"] = True
    updated["task_boundary_continuation_reason"] = "current_task_missing_targets"
    updated["task_boundary_continuation_route"] = "director_retry_with_missing_target_context"
    updated["task_boundary_continuation_target_files"] = in_scope_missing[:12]
    if out_of_scope_missing:
        updated["task_boundary_continuation_deferred_target_files"] = out_of_scope_missing[:12]
    return updated


def _materialization_interface_discrepancy_evidence(
    *,
    task: dict[str, Any],
    plan_probe: dict[str, Any],
    repair_target_files: list[str],
    artifact_quality_errors: list[str],
) -> dict[str, Any]:
    task_interface_contract = _extract_task_interface_contract(task)
    recommended_owner = "director" if task_interface_contract else "chief_engineer"
    recommended_route = (
        "director_retry_with_interface_discrepancy_context"
        if recommended_owner == "director"
        else "pending_design_interface_contract"
    )
    director_retry_allowed = recommended_owner == "director"
    interface_delta = {
        "schema_version": "director.interface_delta.v1",
        "contract_present": bool(task_interface_contract),
        "contract_keys": sorted(str(key) for key in task_interface_contract),
        "diagnostic_paths": repair_target_files[:12],
        "artifact_quality_errors": artifact_quality_errors[:8],
        "diagnostic_count": len(plan_probe.get("covered_unplannable_diagnostics") or []),
    }
    triage_summary = {
        "schema_version": "director.interface_discrepancy_triage.v1",
        "recommended_owner": recommended_owner,
        "recommended_route": recommended_route,
        "contract_present": bool(task_interface_contract),
        "director_retry_allowed": director_retry_allowed,
        "llm_fallback_blocked": not director_retry_allowed,
        "macro_blueprint_regeneration_allowed": False,
        "triage_policy": "ce_contract_if_missing_else_director_local_repair",
        "reason": "director_local_retry_with_interface_delta"
        if director_retry_allowed
        else "task_interface_contract_missing",
    }
    receipt = DirectorInterfaceDiscrepancyReceiptV1(
        task_id=str(task.get("id") or task.get("task_id") or task.get("external_task_id") or "materialization-task"),
        source="roles.adapters.materialization_quality_gate",
        plan_probe_status=str(plan_probe.get("status") or ""),
        diagnostics=tuple(
            item
            for item in list(plan_probe.get("covered_unplannable_diagnostics") or [])[:20]
            if isinstance(item, dict)
        ),
        source_tools=tuple(str(item) for item in plan_probe.get("covered_unplannable_source_tools") or []),
        recommended_owner=recommended_owner,
        recommended_route=recommended_route,
        task_interface_contract_present=bool(task_interface_contract),
        llm_fallback_blocked=not director_retry_allowed,
        director_retry_allowed=director_retry_allowed,
        interface_delta=interface_delta,
        triage_summary=triage_summary,
        metadata={
            "route": "task_boundary_quality_loop",
            "coverage_gap_count": int(plan_probe.get("coverage_gap_count") or 0),
            "repair_target_files": repair_target_files[:12],
            "artifact_quality_errors": artifact_quality_errors[:8],
            "task_interface_contract_keys": sorted(str(key) for key in task_interface_contract),
        },
    ).to_dict()
    receipt.update(
        {
            "route": "task_boundary_quality_loop",
            "coverage_gap_count": int(plan_probe.get("coverage_gap_count") or 0),
            "repair_target_files": repair_target_files[:12],
            "artifact_quality_errors": artifact_quality_errors[:8],
            "task_interface_contract_keys": sorted(str(key) for key in task_interface_contract),
        }
    )
    return receipt


def _materialization_interface_discrepancy_retry_authorized(
    *,
    context: dict[str, Any],
    evidence: dict[str, Any],
) -> bool:
    """Return true only when the caller explicitly routes this gap to Director.

    ``covered_unplannable`` means the runtime catalog recognized the diagnostic
    family but could not safely compose a patch for the current file state. That
    must stay fail-closed unless the task-boundary controller has already
    classified the discrepancy as an implementation-local Director retry.
    """

    raw_authorization = context.get("director_interface_discrepancy_retry")
    if not isinstance(raw_authorization, dict):
        raw_authorization = context.get("task_boundary_interface_discrepancy_retry")
    if not isinstance(raw_authorization, dict) or not bool(raw_authorization.get("authorized")):
        return False
    raw_authorized_evidence = raw_authorization.get("interface_discrepancy_evidence")
    authorized_evidence = raw_authorized_evidence if isinstance(raw_authorized_evidence, dict) else {}
    owner = str(
        raw_authorization.get("recommended_owner")
        or authorized_evidence.get("recommended_owner")
        or evidence.get("recommended_owner")
        or ""
    ).strip()
    route = str(
        raw_authorization.get("recommended_route")
        or authorized_evidence.get("recommended_route")
        or evidence.get("recommended_route")
        or ""
    ).strip()
    reason = str(
        raw_authorization.get("reason") or authorized_evidence.get("reason") or evidence.get("reason") or ""
    ).strip()
    if reason != "coverage_matched_but_unplannable":
        return False
    return owner == "director" and route == "director_retry_with_interface_discrepancy_context"


def _extract_task_interface_contract(task: dict[str, Any]) -> dict[str, Any]:
    candidates: list[Any] = [
        task.get("interface_contract"),
        task.get("task_interface_contract"),
        task.get("module_interface_contract"),
        task.get("execution_interface_contract"),
    ]
    metadata = task.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("interface_contract"),
                metadata.get("task_interface_contract"),
                metadata.get("module_interface_contract"),
                metadata.get("execution_interface_contract"),
            ]
        )
        public_symbols = metadata.get("public_symbols")
        consumes_symbols = metadata.get("consumes_symbols")
        if public_symbols or consumes_symbols:
            candidates.append(
                {
                    "public_symbols": public_symbols or [],
                    "consumes_symbols": consumes_symbols or [],
                }
            )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return dict(candidate)
    return {}


def _resolve_quality_repair_timeout_seconds(primary_timeout_seconds: float) -> float:
    raw_timeout = os.environ.get("KERNELONE_DIRECTOR_QUALITY_REPAIR_TIMEOUT_SECONDS")
    configured = _DEFAULT_QUALITY_REPAIR_TIMEOUT_SECONDS
    if raw_timeout is not None:
        try:
            parsed = float(raw_timeout)
        except (TypeError, ValueError):
            parsed = configured
        if parsed > 0:
            configured = parsed
    configured = max(30.0, min(configured, 300.0))
    try:
        primary = float(primary_timeout_seconds)
    except (TypeError, ValueError):
        primary = configured
    if primary <= 0:
        primary = configured
    return max(0.1, min(primary, configured))


def _select_materialization_quality_repair_target_batch(
    missing_target_files: list[str],
    *,
    repair_attempt: int = 1,
    rotate_after_first_attempt: bool = False,
    preserve_batch_after_first_attempt: bool = False,
) -> list[str]:
    """Select the missing targets to repair in a single LLM attempt."""

    if preserve_batch_after_first_attempt:
        return list(missing_target_files[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT])
    if repair_attempt > 1 and missing_target_files:
        if rotate_after_first_attempt:
            target_index = (repair_attempt - 1) % len(missing_target_files)
            return [missing_target_files[target_index]]
        return [missing_target_files[0]]
    return list(missing_target_files[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT])


def _ordered_materialization_quality_repair_target_candidates(
    *,
    missing_target_files: list[str],
    runtime_smoke_target_files: list[str],
    semantic_quality_target_files: list[str],
    explicit_quality_target_files: list[str],
    should_merge_missing_targets: bool,
) -> list[str]:
    missing_repair_candidates = missing_target_files if should_merge_missing_targets else []
    runtime_targets_precede_missing = _runtime_quality_targets_should_precede_missing(runtime_smoke_target_files)
    if semantic_quality_target_files or explicit_quality_target_files:
        source_missing_candidates = [
            path for path in missing_repair_candidates if not _is_generated_quality_repair_target(path)
        ]
        generated_missing_candidates = [
            path for path in missing_repair_candidates if _is_generated_quality_repair_target(path)
        ]
        return _dedupe_preserve_order(
            [
                *source_missing_candidates,
                *semantic_quality_target_files,
                *explicit_quality_target_files,
                *runtime_smoke_target_files,
                *generated_missing_candidates,
            ]
        )
    if runtime_targets_precede_missing:
        return _dedupe_preserve_order(
            [
                *runtime_smoke_target_files,
                *missing_repair_candidates,
                *semantic_quality_target_files,
                *explicit_quality_target_files,
            ]
        )
    return _dedupe_preserve_order(
        [
            *missing_repair_candidates,
            *runtime_smoke_target_files,
            *semantic_quality_target_files,
            *explicit_quality_target_files,
        ]
    )


def _runtime_quality_targets_should_precede_missing(runtime_smoke_target_files: list[str]) -> bool:
    return any(str(path or "").endswith(".go") for path in runtime_smoke_target_files)


def _filter_materialization_quality_errors_for_repair_targets(
    artifact_quality_errors: list[str],
    repair_target_files: list[str],
) -> list[str]:
    """Keep prompt feedback aligned with the currently leased repair scope."""

    normalized_targets = [
        target for target in (_normalize_declared_task_path(item) for item in repair_target_files) if target
    ]
    if not normalized_targets:
        return list(artifact_quality_errors)
    filtered = [
        error
        for error in artifact_quality_errors
        if any(target in str(error or "").replace("\\", "/") for target in normalized_targets)
    ]
    return filtered or list(artifact_quality_errors)


def _should_rotate_materialization_quality_repair_targets(artifact_quality_errors: list[str]) -> bool:
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    return any(
        hint in joined_errors
        for hint in (
            "typescript project typecheck failed",
            "tsc --noemit failed",
            "error ts",
        )
    )


def _should_preserve_materialization_quality_repair_batch(
    artifact_quality_errors: list[str],
    *,
    repair_target_candidates: list[str] | None = None,
) -> bool:
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if _looks_like_go_workspace_quality_error(joined_errors) and _GO_COMPILE_PATH_RE.search(joined_errors):
        return True
    if _artifact_quality_failed_test_count(artifact_quality_errors) >= 2:
        return True
    if "python runtime smoke" in joined_errors and (
        "assertregex" in joined_errors or "regex didn't match" in joined_errors
    ):
        return True
    if (
        "referenceerror: require is not defined" in joined_errors
        or "module is not defined in es module scope" in joined_errors
        or "is not defined in es module scope" in joined_errors
        or "cannot use import statement outside a module" in joined_errors
    ):
        return True
    if "unresolved import symbol" in joined_errors or "has no exported member" in joined_errors:
        return True
    if _looks_like_python_missing_module_failure(joined_errors) and _has_non_test_python_traceback_source(
        joined_errors
    ):
        return True
    if "ts18046" in joined_errors or "is of type 'unknown'" in joined_errors or 'is of type "unknown"' in joined_errors:
        return True
    if "ts2693" in joined_errors or "only refers to a type" in joined_errors:
        return True
    if repair_target_candidates and _should_preserve_python_cross_language_harness_repair_batch(
        joined_errors,
        repair_target_candidates,
    ):
        return True
    coupled_hints = (
        "unresolved import symbol",
        "typescript project typecheck failed",
        "npm package manifest script",
        "npm package manifest has test runner script",
    )
    return sum(1 for hint in coupled_hints if hint in joined_errors) >= 2


def _should_preserve_python_cross_language_harness_repair_batch(
    joined_errors: str,
    repair_target_candidates: list[str],
) -> bool:
    if not _looks_like_python_test_behavior_failure(joined_errors):
        return False
    if not _looks_like_python_test_harness_quality_failure(joined_errors):
        return False
    production_targets: list[str] = []
    for item in repair_target_candidates:
        rel = _normalize_declared_task_path(str(item or ""))
        if not rel:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in _SOURCE_REPAIR_EXTENSIONS:
            continue
        if _is_test_like_python_path(rel) or _is_test_like_javascript_path(rel):
            continue
        production_targets.append(rel)
    if len(_dedupe_preserve_order(production_targets)) < 2:
        return False
    return any(not target.endswith(".py") for target in production_targets)


def _has_scaffold_marker_quality_error(artifact_quality_errors: list[str]) -> bool:
    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    return "generic/placeholder content detected" in joined_errors or "deterministic scaffold marker" in joined_errors


_PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"python runtime smoke (?:crashed|timed out|was killed) for (?P<target>['\"`][^'\"`]+['\"`]|[^:\s;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"python runtime smoke could not launch (?P<target>['\"`][^'\"`]+['\"`]|[^:\s;]+)",
        re.IGNORECASE,
    ),
)

_PYTHON_TRACEBACK_FILE_RE = re.compile(r'File "(?P<path>[^"]+)", line \d+', re.IGNORECASE)
_MISSING_WORKSPACE_DIRECTORY_ALLOWLIST = frozenset({"test", "tests", "spec", "specs", "__tests__"})
_MISSING_WORKSPACE_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"No such file or directory:\s*(?P<path>['\"`][^'\"`]+['\"`]|[^;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"Cannot find module ['\"](?P<path>[^'\"]+)['\"]",
        re.IGNORECASE,
    ),
    re.compile(
        r"Could not find ['\"](?P<path>[^,'\"\s]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"Could not find ['\"](?P<path>[^'\"]+)['\"]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:compiled\s+)?entrypoint missing:\s*(?P<path>['\"`][^'\"`]+['\"`]|[^\s;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"must exist at\s+(?P<path>['\"`][^'\"`]+['\"`]|[^\s;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"missing or empty:\s*(?P<path>['\"`][^'\"`]+['\"`]|[^\s;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:c|cc|cfg|conf|cpp|css|cxx|env|go|h|hpp|html|ini|java|js|jsx|json|lock|md|py|rs|rst|toml|ts|tsx|txt|xml|yaml|yml))"
        r"\s+must\s+(?:be|contain|declare|exist|include|provide)\b",
        re.IGNORECASE,
    ),
)
_REQUIREMENTS_TXT_ASSERT_IN_DEP_RE = re.compile(
    r"assertIn\(\s*['\"](?P<package>[A-Za-z0-9][A-Za-z0-9_.-]*)['\"]",
    re.IGNORECASE,
)
_REQUIREMENTS_TXT_MUST_DECLARE_DEP_RE = re.compile(
    r"requirements\.txt\s+must\s+declare\s+(?P<package>[A-Za-z0-9][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)
_REQUIREMENTS_TXT_NON_PACKAGE_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "at",
        "dependency",
        "dependencies",
        "least",
        "one",
        "package",
        "packages",
    }
)
_MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "README.md",
        "README.rst",
        "app.py",
        "index.html",
        "main.py",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "tsconfig.json",
    }
)
_MISSING_WORKSPACE_FILE_ALLOWED_PREFIXES: tuple[str, ...] = (
    "config/",
    "configs/",
    "data/",
    "docs/",
    "scripts/",
    "src/",
    "test/",
    "tests/",
)
_PYTHON_MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError:\s+No module named ['\"](?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)['\"]",
    re.IGNORECASE,
)
_SEMANTIC_QUALITY_EXPLICIT_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx|go|h|hpp|html|java|js|jsx|json|md|py|rs|ts|tsx|css))(?=[:;\s(]|$)",
    re.IGNORECASE,
)
_RUST_COMPILE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\s:()'\"\n>]+?\.rs):\d+:\d+",
    re.IGNORECASE,
)
_FAILED_TEST_TITLE_RE = re.compile(
    r"^\s*(?:not\s+ok\s+\d+|failed|fail)\s*(?:[-:]\s*)?(?P<title>.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_TAP_FAILED_TEST_RE = re.compile(r"^\s*not\s+ok\s+\d+\b", re.IGNORECASE | re.MULTILINE)


def _contains_verifier_test_failure(errors: Iterable[str]) -> bool:
    """Use runtime-owned diagnostic semantics for every test framework.

    The quality-repair transaction is intentionally single-turn.  A failed
    verifier against an existing task-owned file therefore must request a real
    edit immediately; allowing another read-only turn returns control to QA
    with zero effects.  The former TAP-only regex made Node tests strict while
    Go/Python/JUnit failures silently stayed read-only.  Runtime diagnostic
    normalization is the cross-language authority for this decision.
    """

    from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

    normalized_errors = tuple(str(error or "") for error in errors if str(error or "").strip())
    try:
        diagnostics = normalize_director_repair_diagnostics(normalized_errors)
    except (ImportError, RuntimeError, TypeError, ValueError):
        # Keep the already-supported TAP behavior if diagnostic discovery is
        # temporarily unavailable; never widen the fallback into a pass.
        return any(_TAP_FAILED_TEST_RE.search(error) for error in normalized_errors)
    return any(str(diagnostic.code or "").strip() == "verifier_test_failure" for diagnostic in diagnostics)


def _verifier_test_failure_target_files(
    *,
    artifact_quality_errors: Iterable[str],
    workspace_full: str,
) -> list[str]:
    """Return concrete task-scope candidates from runtime verifier facts.

    Framework-specific parsers may split one command transcript into causal
    leaf diagnostics before the quality-repair target selector runs.  The leaf
    rows intentionally omit the original ``go test``/``pytest`` command
    wrapper, so language regexes cannot be the authority for path discovery.
    Use the runtime-owned normalized diagnostic path instead.  The caller's
    task-scope partition remains the write authority: an external/hidden test
    path is evidence only unless the current Director task explicitly owns it.
    """

    from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace_root is None or not workspace_root.is_dir():
        return []
    normalized_errors = tuple(str(error or "") for error in artifact_quality_errors if str(error or "").strip())
    try:
        diagnostics = normalize_director_repair_diagnostics(normalized_errors)
    except (ImportError, RuntimeError, TypeError, ValueError):
        return []
    targets: list[str] = []
    for diagnostic in diagnostics:
        if str(diagnostic.code or "").strip() != "verifier_test_failure":
            continue
        rel = _normalize_declared_task_path(str(diagnostic.path or ""))
        if not rel or Path(rel).suffix.lower() not in _SOURCE_REPAIR_EXTENSIONS:
            continue
        if _workspace_path_exists_case_insensitive(workspace_root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


_TEST_SUMMARY_FAIL_RE = re.compile(r"^\s*#?\s*fail\s+(?P<count>\d+)\s*$", re.IGNORECASE | re.MULTILINE)
_PYTHON_UNITTEST_RESULT_LINE_RE = re.compile(
    r"^\s*\S+\s+\((?P<module>[^)]+)\)"
    r"(?:\s+\.\.\.\s+(?:ERROR|FAIL|FAILED)\s*$|\s*\n[^\n]*\.\.\.\s+(?:ERROR|FAIL|FAILED)\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_TS_NO_EXPORTED_MEMBER_QUALITY_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*"
    r"error\s+TS2305:\s*Module\s+['\"](?P<module>.+?)['\"]\s+has no exported member",
    re.IGNORECASE,
)
_TS_DIAGNOSTIC_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*error\s+TS\d+:",
    re.IGNORECASE,
)
_TS_UNKNOWN_VALUE_QUALITY_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*"
    r"error\s+TS18046:\s*['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+of\s+type\s+"
    r"['\"]unknown['\"]",
    re.IGNORECASE,
)
_TS_TYPE_ONLY_VALUE_QUALITY_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.tsx?)\(\d+,\d+\):\s*"
    r"error\s+TS2693:\s*['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+only\s+refers\s+"
    r"to\s+a\s+type,\s+but\s+is\s+being\s+used\s+as\s+a\s+value\s+here",
    re.IGNORECASE,
)
_TS_EXPORTED_DECLARATION_TEMPLATE = (
    r"\bexport\s+(?:declare\s+)?(?:(?:const|let|var|function|class|interface|type)\s+)"
    r"{symbol}\b"
)

__all__ = [
    "_DEFAULT_QUALITY_REPAIR_TIMEOUT_SECONDS",
    "_FAILED_TEST_TITLE_RE",
    "_MISSING_WORKSPACE_DIRECTORY_ALLOWLIST",
    "_MISSING_WORKSPACE_FILE_ALLOWED_PREFIXES",
    "_MISSING_WORKSPACE_FILE_PATTERNS",
    "_MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST",
    "_PYTHON_MODULE_NOT_FOUND_RE",
    "_PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS",
    "_PYTHON_TRACEBACK_FILE_RE",
    "_PYTHON_UNITTEST_RESULT_LINE_RE",
    "_QUALITY_REPAIR_ATTEMPT_HARD_CAP",
    "_QUALITY_REPAIR_BASE_ATTEMPTS",
    "_QUALITY_REPAIR_DEADLINE_DEFAULT_SAFETY_SECONDS",
    "_QUALITY_REPAIR_DEADLINE_MIN_LLM_SECONDS",
    "_QUALITY_REPAIR_TARGET_BATCH_LIMIT",
    "_REQUIREMENTS_TXT_ASSERT_IN_DEP_RE",
    "_REQUIREMENTS_TXT_MUST_DECLARE_DEP_RE",
    "_REQUIREMENTS_TXT_NON_PACKAGE_WORDS",
    "_RUST_COMPILE_PATH_RE",
    "_SEMANTIC_QUALITY_EXPLICIT_PATH_RE",
    "_TAP_FAILED_TEST_RE",
    "_TEST_SUMMARY_FAIL_RE",
    "_TS_DIAGNOSTIC_PATH_RE",
    "_TS_EXPORTED_DECLARATION_TEMPLATE",
    "_TS_NO_EXPORTED_MEMBER_QUALITY_RE",
    "_TS_TYPE_ONLY_VALUE_QUALITY_RE",
    "_TS_UNKNOWN_VALUE_QUALITY_RE",
    "_annotate_current_task_missing_target_continuation",
    "_context_float_value",
    "_extract_task_interface_contract",
    "_filter_materialization_quality_errors_for_repair_targets",
    "_has_scaffold_marker_quality_error",
    "_materialization_interface_discrepancy_evidence",
    "_materialization_interface_discrepancy_retry_authorized",
    "_materialization_plan_probe_requires_task_boundary_triage",
    "_ordered_materialization_quality_repair_target_candidates",
    "_quality_repair_deadline_decision",
    "_resolve_quality_repair_timeout_seconds",
    "_run_materialization_quality_repair_retry",
    "_runtime_quality_targets_should_precede_missing",
    "_select_materialization_quality_repair_target_batch",
    "_should_preserve_materialization_quality_repair_batch",
    "_should_preserve_python_cross_language_harness_repair_batch",
    "_should_rotate_materialization_quality_repair_targets",
]
