"""Post-execution deterministic repair runner bindings for Director adapter.

The Director runtime repair kernel owns scheduling, policy, and receipt
semantics. This module only binds language-specific post-execution source
collection to the policy-gated Director tool executor used by role adapters.

This package is the lossless successor of the former
``post_execution_repair_bridge`` module. It re-exports every previously-public
symbol from the same import path so that
``import ...director.post_execution_repair_bridge`` and
``from ...director.post_execution_repair_bridge import X`` keep resolving
identically for all external importers.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import tomllib
from polaris.cells.director.runtime.public.service import (
    DirectorRepairPostExecutionStepV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    query_director_repair_post_execution_schedule,
    query_director_repair_strategy_catalog,
    run_director_post_execution_repair_schedule_result,
    validate_director_repair_advisory,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

from ..repair_profile_projection import project_repair_kernel_summary
from ..runtime_repair_tool_adapter import run_runtime_repair_with_director_tools
from ._constants import (
    _ANSI_ESCAPE_RE,
    _CALLBACK_RECEIPT_MIGRATION_BLOCKER,
    _CPP_REPAIR_FILE_SUFFIXES,
    _GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS,
    _POST_EXECUTION_REPAIR_MAX_ROUNDS,
    _RUST_BASE_FILE_IGNORES,
    _RUST_E0583_HELP_LINE_RE,
    _RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE,
    _RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE,
    _RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
    _RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE,
    _RUST_MISSING_FIELDS_SOURCE_TOOL,
    _RUST_QUOTED_RS_PATH_RE,
    _RUST_TYPED_RECEIPT_CUTOVER_SOURCE_TOOLS,
    _RUST_TYPED_RECEIPT_CUTOVER_SUBCASES_BY_SOURCE_TOOL,
    _RUST_TYPED_RECEIPT_SOURCE_TOOL_BLOCKER,
    ConvergenceVerifier,
    RuntimeAdvisorNotes,
    StepRunner,
)
from ._helpers import (
    _actual_source_tools_for_payloads,
    _adapter_projection_repair_kernel_payload,
    _attach_payload_revalidation_to_projection,
    _bool_claim,
    _build_repair_kernel_migration_debt,
    _build_rust_typed_receipt_cutover_evidence,
    _build_scheduler_bridge_summary,
    _build_step_migration_debt,
    _callback_projection_claims_typed_receipt_path_available,
    _callback_projection_has_revalidation,
    _callback_receipt_authority_value,
    _callback_receipt_projection_to_dict,
    _callback_receipt_projections_from_payloads,
    _callback_receipt_projections_from_schedule_result,
    _canonical_repair_failure_to_tool_results,
    _canonical_repair_result_to_tool_results,
    _collect_cpp_base_files,
    _collect_go_base_files,
    _collect_java_base_files,
    _collect_java_test_base_files,
    _collect_rust_base_files,
    _count_by_payload_key,
    _explicit_callback_receipt_projections,
    _is_generated_build_path,
    _is_java_test_source_path,
    _looks_like_cpp_workspace,
    _max_int,
    _max_revalidation_int,
    _normalize_callback_receipt_projection,
    _normalize_resident_agi_repair_advisory_overlay,
    _normalize_rust_declared_binary_path,
    _normalize_rust_missing_module_create_path,
    _payload_has_callback_receipt_projection_annotation,
    _payload_has_non_authoritative_runtime_receipt,
    _payload_has_verifier_evidence,
    _payload_has_write_tool_evidence,
    _payload_is_adapter_projection_record,
    _payload_list_values,
    _payload_migration_blocker,
    _payload_receipt_authority,
    _payload_revalidation_evidence,
    _payload_typed_receipt_path_available,
    _policy_gated_adapter_missing_tool_result,
    _post_execution_artifact_quality_errors,
    _receipt_requires_revalidation,
    _record_payload,
    _record_to_tool_result,
    _result_payload,
    _runtime_advisor_notes_from_overlay,
    _runtime_executable_source_tools,
    _rust_declared_binary_paths,
    _rust_duplicate_module_candidate_paths_from_errors,
    _rust_missing_module_candidate_paths_from_errors,
    _rust_post_execution_artifact_quality_errors,
    _rust_typed_receipt_cutover_projection_fields,
    _rust_typed_receipt_remaining_source_tools,
    _rust_typed_receipt_remaining_subcases,
    _rust_typed_receipt_runtime_migrated_subcases,
    _rust_typed_receipt_unbound_source_tools,
    _schedule_summary_int,
    _sorted_unique,
    _source_tool_counts,
    _summary_only_callback_receipt_projection,
    _validate_resident_agi_advisor_notes,
    _write_tool_result,
)
from ._language import (
    _run_cpp_include_path_runtime_repair,
    _run_cpp_missing_private_members_runtime_repair,
    _run_cpp_placeholder_declaration_runtime_repair,
    _run_cpp_runtime_repair,
    _run_cpp_standard_include_runtime_repair,
    _run_cpp_struct_getter_field_access_runtime_repair,
    _run_go_post_repairs,
    _run_go_runtime_repair,
    _run_java_post_repairs,
    _run_rust_crate_import_rewrite_runtime_repair,
    _run_rust_dependency_repair,
    _run_rust_duplicate_module_file_runtime_repair,
    _run_rust_field_rename_suggestion_runtime_repair,
    _run_rust_incompatible_copy_derive_runtime_repair,
    _run_rust_lib_root_facade_runtime_repair,
    _run_rust_line_suggestion_runtime_repair,
    _run_rust_method_self_signature_runtime_repair,
    _run_rust_missing_binary_entrypoint_runtime_repair,
    _run_rust_missing_fields_runtime_repair,
    _run_rust_missing_module_file_runtime_repair,
    _run_rust_missing_trait_derive_runtime_repair,
    _run_rust_post_repairs,
    _run_rust_trait_import_runtime_repair,
    _run_rust_unresolved_pub_use_runtime_repair,
    _run_rust_unused_import_runtime_repair,
    _run_rust_wrong_crate_path_runtime_repair,
    run_cpp_post_repairs_as_tool_results,
)

_POST_EXECUTION_REPAIR_RUNNERS: dict[str, StepRunner] = {
    "go.module_import": lambda adapter, workspace, task_id: _run_go_post_repairs(adapter, task_id=task_id),
    "rust.dependency_resolution": lambda adapter, workspace, task_id: _run_rust_dependency_repair(
        adapter,
        task_id=task_id,
    ),
    "rust.post_execution_convergence": lambda adapter, workspace, task_id: _run_rust_post_repairs(
        adapter,
        workspace,
        task_id=task_id,
    ),
    "cpp.post_execution": lambda adapter, workspace, task_id: run_cpp_post_repairs_as_tool_results(
        workspace,
        adapter=adapter,
        task_id=task_id,
    ),
    "java.post_execution": lambda adapter, workspace, task_id: _run_java_post_repairs(
        adapter,
        workspace,
        task_id=task_id,
    ),
}


def run_post_execution_language_repairs(
    adapter: Any,
    *,
    task_id: str,
    resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run post-execution language repairs and return normalized tool results."""

    workspace = Path(str(getattr(adapter, "workspace", "") or ""))
    agi_advisory_overlay = _normalize_resident_agi_repair_advisory_overlay(
        resident_agi_repair_advisory_overlay,
    )
    runtime_advisor_notes = _runtime_advisor_notes_from_overlay(agi_advisory_overlay)

    def _run_step(step: DirectorRepairPostExecutionStepV1) -> list[dict[str, Any]]:
        runner = _runner_for_post_execution_step(step)
        execution_attempt_kwargs = (
            {"execution_attempt": execution_attempt}
            if type(execution_attempt) is TaskRuntimeExecutionAttemptIdentityV1
            else {}
        )
        if step.step_id == "cpp.post_execution":
            return run_cpp_post_repairs_as_tool_results(
                workspace,
                adapter=adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "go.module_import":
            return _run_go_post_repairs(
                adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "rust.dependency_resolution":
            return _run_rust_dependency_repair(
                adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "rust.post_execution_convergence":
            return _run_rust_post_repairs(
                adapter,
                workspace,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "java.post_execution":
            return _run_java_post_repairs(
                adapter,
                workspace,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        return runner(adapter, workspace, task_id)

    schedule_result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=tuple(_POST_EXECUTION_REPAIR_RUNNERS),
        runner=_run_step,
        # Deferred effects may plan one round only.  Later rounds require the
        # first round's lifecycle, receipt and revalidation facts (DEO-3).
        max_rounds=1,
    )
    tool_results = [dict(item) for item in schedule_result.tool_results]
    ordered_steps = schedule_result.ordered_steps
    if not tool_results:
        return [], None
    repair_kernel = project_repair_kernel_summary(
        stage="post_execution_language_repairs",
        tool_results=tool_results,
        artifact_quality_errors=(),
        mode="commit",
    )
    repair_kernel["agi_advisory"] = {
        **dict(repair_kernel.get("agi_advisory") or {}),
        **agi_advisory_overlay,
    }
    migration_debt = _build_repair_kernel_migration_debt(
        tool_results,
        ordered_steps=ordered_steps,
        convergence_verifier_present=convergence_verifier is not None,
    )
    repair_kernel["repair_kernel_migration_debt"] = migration_debt
    rust_typed_receipt_cutover_evidence = dict(migration_debt.get("rust_typed_receipt_cutover_evidence") or {})
    repair_kernel.update(_rust_typed_receipt_cutover_projection_fields(rust_typed_receipt_cutover_evidence))
    scheduler_bridge = _build_scheduler_bridge_summary(
        tool_results,
        repair_kernel=repair_kernel,
        ordered_steps=ordered_steps,
        schedule_summary=dict(schedule_result.summary),
        receipt_projections=[
            _callback_receipt_projection_to_dict(item) for item in schedule_result.receipt_projections
        ],
        resident_agi_repair_advisory_overlay=agi_advisory_overlay,
    )
    return tool_results, {
        "schema_version": "director.post_execution_repair_kernel.v1",
        "repair_kernel": repair_kernel,
        "scheduler_bridge": scheduler_bridge,
        "repair_kernel_migration_debt": migration_debt,
        "rust_typed_receipt_cutover_evidence": rust_typed_receipt_cutover_evidence,
        "resident_agi_repair_advisory_overlay": agi_advisory_overlay,
    }


def _runner_for_post_execution_step(step: DirectorRepairPostExecutionStepV1) -> StepRunner:
    runner = _POST_EXECUTION_REPAIR_RUNNERS.get(step.step_id)
    if runner is None:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {step.step_id}")
    return runner
