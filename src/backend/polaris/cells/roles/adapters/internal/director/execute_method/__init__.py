"""Director execute 方法实现

包含 execute 方法及其辅助函数。此模块提供 Director 任务执行的核心逻辑。

This package is the lossless successor of the former ``execute_method`` module.
It re-exports every previously-public symbol from the same import path so that
``import ...director.execute_method`` and ``from ...director.execute_method import X``
keep resolving identically for all external importers. Deferred sibling re-exports
that previously ran at module bottom for circular-import safety still run here,
exactly once, at package import.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / typing / sibling names
# that were module-level attributes of the former ``execute_method`` module.
import asyncio
import contextlib
import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
import types as _types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_claimed_materialization_without_tool_lifecycle_receipt,
    build_verified_existing_artifact_lifecycle_receipt,
    evaluate_task_boundary_verdict,
    is_failure_class,
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    summarize_tool_lifecycle_events,
    tool_call_lifecycle_receipts_from_metadata,
)
from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.director.runtime.public.service import (
    AttachDirectorRepairRevalidationEvidenceV1,
    project_director_repair_revalidation_evidence,
)
from polaris.cells.runtime.execution_broker.public import (
    RecordProjectArtifactCommandV1,
    record_project_artifact,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    create_task_runtime_execution_attempt_authority,
)
from polaris.kernelone.fs.materialization import materialized_file_paths

# ``scan_workspace_artifact_quality`` MUST stay a name on THIS module: the test
# suite monkeypatches ``execute_method.scan_workspace_artifact_quality`` and the
# moved quality/repair callers resolve it through this module namespace (``_em``)
# at call time, so the patch still takes effect.
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

from ..contract_verify import resolve_contract_step_verify_command
from ..dependency_artifact_evidence import (
    DirectorDependencyArtifactEvidenceError,
    build_current_task_project_artifact_receipt_evidence,
)
from ..helpers import (
    has_successful_write_tool,
    taskboard_snapshot_brief,
)
from ..materialization_quality_boundary import run_materialization_quality_public_boundary
from ..post_execution_repair_bridge import run_post_execution_language_repairs
from ..repair_convergence_verifier import (
    build_artifact_quality_convergence_verifier,
    build_step_verify_convergence_verifier,
)
from ..repair_profile_projection import summarize_deterministic_repair_source_tools
from ._claim import (
    _append_receipt_bound_preflight_task_boundary,
    _attach_dependency_artifact_receipt_evidence,
    _canonical_task_owner_identity,
    _claim_task_with_retry,
    _commit_deferred_materialization_quality_results,
    _emit_director_adapter_cognitive_receipt,
    _execution_attempt_authority_from_context,
    _execution_attempt_identity_from_context,
    _extract_resident_agi_repair_advisory_overlay,
    _finalize_claimed_execution,
    _handle_claim_required,
    _project_deferred_followup_receipts_as_tool_results,
    _project_dependency_artifact_tool_results,
    _record_project_artifacts_before_settlement,
    _resolve_claim_external_task_id,
    _suspend_claimed_execution_for_cancellation,
    _task_completion_projection_from_context,
    _task_runtime_finalization_failed_result,
    _task_runtime_finalize_failed_signal,
    _task_runtime_heartbeat_exception_signal,
    _task_runtime_heartbeat_failed_signal,
    _with_decision_signals,
    _with_task_runtime_finalize_evidence,
)
from ._entry import (
    execute_director_task,
)
from ._helpers import (
    _DIAG_WRITE_TOOL_NAMES,
    _NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES,
    _NO_WRITE_MULTI_TARGET_RETRY_TOOL_NAMES,
    _POST_EXECUTION_STEP_VERIFY_ERROR_PREFIXES,
    _QUALITY_REPAIR_STAGNATION_LIMIT,
    _TRANSIENT_LLM_PROVIDER_ERROR_MARKERS,
    MaterializationState,
    _adapter_materialized_file_paths,
    _annotate_quality_repair_progress,
    _artifact_quality_error_signature,
    _attach_current_task_project_receipt_evidence,
    _build_empty_write_content_retry_message,
    _build_no_write_materialization_retry_message,
    _build_post_execution_artifact_quality_convergence_verifier,
    _build_post_execution_repair_convergence_verifier,
    _declared_write_retry_target_files,
    _deterministic_repair_profile_summary_from_tool_results,
    _deterministic_repair_source_tools_from_tool_results,
    _diag_write_results_summary,
    _empty_write_content_retry_needed,
    _empty_write_retry_tool_definition,
    _invoke_role_dialogue_with_transient_provider_retry,
    _is_transient_llm_provider_exception,
    _no_write_materialization_retry_needed,
    _no_write_materialization_retry_tool_definitions,
    _no_write_retry_strict_write_only,
    _pin_file_schema_to_declared_targets,
    _pin_materialize_context_delivery_mode,
    _pin_materialize_delivery_mode,
    _post_execution_convergence_error_is_step_verify,
    _post_execution_convergence_prefers_step_verify,
    _post_execution_convergence_relative_paths,
    _post_execution_convergence_step_verify_command,
    _quality_repair_progress_evidence,
    _registered_tool_definition,
    _run_empty_write_content_materialization_retry,
    _run_materialization_quality_public_boundary,
    _run_no_write_materialization_retry,
    _select_empty_write_content_retry_tool_name,
    _task_targets_missing_in_workspace,
)
from ._llm_flow import (
    _execute_standard_llm_flow,
)
from ._phases_failure import (
    _attach_director_file_event_bus,
    _cross_artifact_llm_escalation_enabled,
    _lifecycle_tool_dispatch_failure_from_summary,
    _materialization_failure_evidence_row,
    _phase_cross_artifact_unplannable_llm_escalation,
    _phase_existing_scope_verified,
    _phase_missing_write_receipt,
    _phase_no_materialized_changes,
    _phase_quality_failed,
    _phase_semantic_quality_failed,
    _primary_llm_provider_failure_payload,
    _primary_llm_summary_text,
    _primary_llm_tool_dispatch_failure,
    _seal_claimed_materialization_without_tool_lifecycle,
    _summary_field_matches_failure_class,
    _tool_dispatch_dropped_failure_payload,
)
from ._phases_materialization import (
    _mark_nested_repair_kernel_summaries_revalidated,
    _mark_quality_repair_summary_revalidated,
    _phase_deterministic_cleanup,
    _phase_direct_fallback,
    _phase_empty_write_retry,
    _phase_existing_scope_preflight,
    _phase_finalize_materialization,
    _phase_first_llm_call,
    _phase_no_write_materialization_retry,
    _phase_pre_materialization_quality,
    _phase_pre_materialization_target_repair,
    _phase_python_unittest_repair,
    _phase_typescript_reexport_repair,
    _project_repair_revalidation_summary,
)
from ._phases_quality import (
    _materialization_task_boundary_triage_summary,
    _phase_quality_repair_loop,
    _phase_semantic_quality_repair_loop,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lossless helper re-export surface (module decomposition boundary)
#
# ``execute_method`` stays the canonical import path. The bodies below were
# moved verbatim into sibling modules; non-repair helpers are re-imported here
# so the public + test-import surface resolves on this module exactly as
# before.
# ---------------------------------------------------------------------------
from ..artifact_quality_diagnostics import (  # noqa: E402  (deferred for circular-import safety)
    _filter_satisfied_declared_target_missing_errors as _filter_satisfied_declared_target_missing_errors,
    _parse_missing_declared_target_files as _parse_missing_declared_target_files,
)
from ..execute_method_repair_bridge import (  # noqa: E402  (deferred for circular-import safety)
    run_declared_target_contract_repairs as run_declared_target_contract_repairs,
    run_node_test_script_contract_repair as run_node_test_script_contract_repair,
    run_patch_residue_cleanup as run_patch_residue_cleanup,
    run_pre_materialization_declared_target_repairs as run_pre_materialization_declared_target_repairs,
    run_python_runtime_smoke as run_python_runtime_smoke,
    run_python_static_smoke as run_python_static_smoke,
    run_python_unittest_missing_target_repair as run_python_unittest_missing_target_repair,
    run_scaffold_marker_cleanup as run_scaffold_marker_cleanup,
    run_typescript_reexport_repair as run_typescript_reexport_repair,
)
from ..quality_gate import (  # noqa: E402  (deferred for circular-import safety)
    _ACCEPTANCE_VERIFY_EXISTS_RE as _ACCEPTANCE_VERIFY_EXISTS_RE,
    _QUALITY_REPAIR_ATTEMPT_HARD_CAP as _QUALITY_REPAIR_ATTEMPT_HARD_CAP,
    _build_existing_workspace_task_evidence as _build_existing_workspace_task_evidence,
    _build_materialization_quality_repair_message as _build_materialization_quality_repair_message,
    _can_accept_existing_workspace_scope as _can_accept_existing_workspace_scope,
    _case_insensitive_file_match as _case_insensitive_file_match,
    _collect_materialization_quality_errors as _collect_materialization_quality_errors,
    _collect_step_verify_errors as _collect_step_verify_errors,
    _collect_workspace_code_diff as _collect_workspace_code_diff,
    _collect_workspace_out_of_scope_diff as _collect_workspace_out_of_scope_diff,
    _declared_target_file_quality_errors as _declared_target_file_quality_errors,
    _director_direct_text_patch_only_enabled as _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled as _director_existing_scope_preflight_enabled,
    _evaluate_acceptance_verify_exists as _evaluate_acceptance_verify_exists,
    _extract_successful_write_paths as _extract_successful_write_paths,
    _filter_materialization_quality_errors_for_repair_targets as _filter_materialization_quality_errors_for_repair_targets,
    _is_node_runtime_source_path as _is_node_runtime_source_path,
    _is_recoverable_no_write_mutation_contract_error_text as _is_recoverable_no_write_mutation_contract_error_text,
    _is_recoverable_no_write_mutation_contract_exception as _is_recoverable_no_write_mutation_contract_exception,
    _materialization_plan_probe_requires_task_boundary_triage as _materialization_plan_probe_requires_task_boundary_triage,
    _materialization_quality_scan_paths as _materialization_quality_scan_paths,
    _materialization_quality_scan_paths_with_package_manifest as _materialization_quality_scan_paths_with_package_manifest,
    _merge_successful_write_paths as _merge_successful_write_paths,
    _missing_declared_target_files as _missing_declared_target_files,
    _missing_materialization_quality_repair_target_files as _missing_materialization_quality_repair_target_files,
    _node_package_manifest_should_be_rescanned_for_test_files as _node_package_manifest_should_be_rescanned_for_test_files,
    _quality_repair_cache_root as _quality_repair_cache_root,
    _run_materialization_quality_repair_retry as _run_materialization_quality_repair_retry,
    _safe_int as _safe_int,
    _select_materialization_quality_repair_target_batch as _select_materialization_quality_repair_target_batch,
    _single_file_step_target as _single_file_step_target,
    _stage_summary_has_recoverable_no_write_mutation_contract_exception as _stage_summary_has_recoverable_no_write_mutation_contract_exception,
    _summarize_llm_stage_result as _summarize_llm_stage_result,
    _task_requires_fresh_materialization as _task_requires_fresh_materialization,
)
from ..task_scope_paths import (  # noqa: E402  (deferred for circular-import safety)
    _BRACKETED_SCOPE_RE as _BRACKETED_SCOPE_RE,
    _LINE_SCOPE_RE as _LINE_SCOPE_RE,
    _coerce_path_candidate_list as _coerce_path_candidate_list,
    _dedupe_preserve_order as _dedupe_preserve_order,
    _extract_scope_markers_from_text as _extract_scope_markers_from_text,
    _extract_task_path_candidates as _extract_task_path_candidates,
    _extract_task_target_path_candidates as _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths as _filter_diff_to_task_declared_paths,
    _glob_path_matches as _glob_path_matches,
    _looks_like_task_path_candidate as _looks_like_task_path_candidate,
    _normalize_declared_task_path as _normalize_declared_task_path,
    _path_candidate_exists_in_file_set as _path_candidate_exists_in_file_set,
    _path_matches_any_declared_candidate as _path_matches_any_declared_candidate,
    _path_matches_declared_candidate as _path_matches_declared_candidate,
    _strip_path_candidate_label as _strip_path_candidate_label,
    _task_has_declared_target_files as _task_has_declared_target_files,
    _task_text_blob as _task_text_blob,
    _workspace_path_exists_case_insensitive as _workspace_path_exists_case_insensitive,
)

# Monkeypatch bridge: tests setattr on this package; function bodies live in
# submodules and resolve bare names via their own globals. Propagate attribute
# writes/deletes into every package submodule so call-time lookups stay lossless.
from . import (  # noqa: E402
    _claim as _claim_mod,
    _entry as _entry_mod,
    _helpers as _helpers_mod,
    _llm_flow as _llm_flow_mod,
    _phases_failure as _phases_failure_mod,
    _phases_materialization as _phases_materialization_mod,
    _phases_quality as _phases_quality_mod,
)

_PACKAGE_SUBMODULES = (
    _helpers_mod,
    _claim_mod,
    _phases_materialization_mod,
    _phases_quality_mod,
    _phases_failure_mod,
    _llm_flow_mod,
    _entry_mod,
)


class _ExecuteMethodPackage(_types.ModuleType):
    """Package module type that mirrors setattr/delattr into submodules."""

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name.startswith("__") and name.endswith("__"):
            return
        for mod in _PACKAGE_SUBMODULES:
            mod.__dict__[name] = value

    def __delattr__(self, name: str) -> None:
        super().__delattr__(name)
        if name.startswith("__") and name.endswith("__"):
            return
        for mod in _PACKAGE_SUBMODULES:
            if name in mod.__dict__:
                del mod.__dict__[name]


sys.modules[__name__].__class__ = _ExecuteMethodPackage
