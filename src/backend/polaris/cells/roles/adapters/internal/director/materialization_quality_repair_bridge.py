"""Materialization-quality deterministic repair bridge for Director adapter.

This module is the migration-time boundary between the legacy materialization
quality repair host and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.director.runtime.public.service import (
    CompareDirectorRepairShadowRunV1,
    DirectorRepairMaterializationQualityStepV1,
    QueryDirectorRepairCoverageV1,
    RepairReceiptV1,
    compare_director_repair_shadow_run,
    query_director_repair_coverage,
    run_director_materialization_quality_repair_schedule,
)

from .helpers import has_successful_write_tool
from .repair_profile_projection import project_repair_kernel_summary, summarize_deterministic_repair_source_tools

_MATERIALIZATION_QUALITY_REPAIR_RUNNERS = {
    "materialization.hygiene_scaffold": "_run_materialization_hygiene_scaffold",
    "materialization.typescript_scaffold": "_run_materialization_typescript_scaffold",
    "materialization.typescript_compiler": "_run_materialization_typescript_compiler",
    "materialization.node_manifest": "_run_materialization_node_manifest",
    "materialization.rust_compiler": "_run_materialization_rust_compiler",
    "materialization.target_runtime": "_run_materialization_target_runtime",
    "materialization.python_import": "_run_materialization_python_import",
    "materialization.go_import": "_run_materialization_go_import",
}


def run_materialization_quality_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run materialization-quality repairs through the migration bridge."""

    step_summaries: dict[str, dict[str, Any]] = {}
    coverage_preaudit = _project_coverage_preaudit(artifact_quality_errors)

    def _run_step(step: DirectorRepairMaterializationQualityStepV1) -> list[dict[str, Any]]:
        tool_results = _run_legacy_materialization_quality_repair_step(
            step.step_id,
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
        step_summaries[step.step_id] = _summarize_step_results(step, tool_results)
        return tool_results

    tool_results, ordered_steps = run_director_materialization_quality_repair_schedule(
        runner_step_ids=tuple(_MATERIALIZATION_QUALITY_REPAIR_RUNNERS),
        runner=_run_step,
    )
    bridged_summary = _annotate_materialization_quality_summary(
        step_summaries=step_summaries,
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
        ordered_steps=ordered_steps,
        coverage_preaudit=coverage_preaudit,
    )
    return tool_results, bridged_summary


def run_typescript_semantic_quality_repairs(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    """Run TypeScript semantic quality repairs behind the materialization bridge boundary."""

    from .deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_canvas_scale_return_type_repair,
        _apply_deterministic_typescript_missing_export_repair,
    )

    results: list[dict[str, Any]] = []
    for repair_fn in (
        _apply_deterministic_typescript_missing_export_repair,
        _apply_deterministic_typescript_canvas_scale_return_type_repair,
    ):
        results.extend(
            repair_fn(
                adapter,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
    return results


def _run_legacy_materialization_quality_repair_step(
    step_id: str,
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    if step_id == "materialization.hygiene_scaffold":
        return _run_materialization_hygiene_scaffold(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.typescript_scaffold":
        return _run_materialization_typescript_scaffold(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.typescript_compiler":
        return _run_materialization_typescript_compiler(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.node_manifest":
        return _run_materialization_node_manifest(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.rust_compiler":
        return _run_materialization_rust_compiler(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.target_runtime":
        return _run_materialization_target_runtime(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.python_import":
        return _run_materialization_python_import(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    if step_id == "materialization.go_import":
        return _run_materialization_go_import(adapter, task_id=task_id)
    raise RuntimeError(f"materialization quality repair step has no legacy runner: {step_id}")


def _run_materialization_hygiene_scaffold(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.generic_repairs import (
        _apply_deterministic_scaffold_marker_cleanup,
        _apply_deterministic_scaffold_marker_error_cleanup,
    )

    results: list[dict[str, Any]] = []
    results.extend(_apply_deterministic_scaffold_marker_cleanup(adapter, task=task, task_id=task_id))
    results.extend(
        _apply_deterministic_scaffold_marker_error_cleanup(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    return results


def _run_materialization_typescript_scaffold(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.npm_repairs import _apply_deterministic_typescript_scaffold_repair
    from .deterministic_repairs.typeorm_repairs import _apply_deterministic_typeorm_model_normalization_repair

    results: list[dict[str, Any]] = []
    results.extend(
        _apply_deterministic_typescript_scaffold_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_typeorm_model_normalization_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    return results


def _run_materialization_typescript_compiler(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.typescript_repairs import (
        _apply_deterministic_html_typescript_module_script_repair,
        _apply_deterministic_typescript_canvas_scale_return_type_repair,
        _apply_deterministic_typescript_duplicate_object_property_repair,
        _apply_deterministic_typescript_entrypoint_repair,
        _apply_deterministic_typescript_enum_member_separator_repair,
        _apply_deterministic_typescript_escaped_newline_repair,
        _apply_deterministic_typescript_member_alias_repair,
        _apply_deterministic_typescript_missing_closing_brace_repair,
        _apply_deterministic_typescript_missing_export_repair,
        _apply_deterministic_typescript_missing_member_repair,
        _apply_deterministic_typescript_nullable_canvas_context_repair,
        _apply_deterministic_typescript_number_to_string_argument_repair,
        _apply_deterministic_typescript_reexported_type_binding_repair,
        _apply_deterministic_typescript_relative_import_case_repair,
        _apply_deterministic_typescript_return_object_semicolon_repair,
        _apply_deterministic_typescript_sourcefile_diagnostics_repair,
        _apply_deterministic_typescript_too_few_arguments_repair,
        _apply_deterministic_typescript_tsconfig_lib_repair,
        _apply_deterministic_typescript_uninitialized_property_repair,
        _apply_deterministic_typescript_unresolved_identifier_repair,
        _apply_deterministic_typescript_vitest_globals_repair,
    )
    from .deterministic_repairs.zod_repairs import _apply_deterministic_typescript_zod_type_class_collision_repair

    step_runners = (
        _apply_deterministic_typescript_return_object_semicolon_repair,
        _apply_deterministic_typescript_enum_member_separator_repair,
        _apply_deterministic_typescript_unresolved_identifier_repair,
        _apply_deterministic_typescript_reexported_type_binding_repair,
        _apply_deterministic_typescript_escaped_newline_repair,
        _apply_deterministic_typescript_missing_closing_brace_repair,
        _apply_deterministic_typescript_zod_type_class_collision_repair,
        _apply_deterministic_typescript_relative_import_case_repair,
        _apply_deterministic_typescript_entrypoint_repair,
        _apply_deterministic_typescript_tsconfig_lib_repair,
        _apply_deterministic_typescript_duplicate_object_property_repair,
        _apply_deterministic_typescript_nullable_canvas_context_repair,
        _apply_deterministic_typescript_sourcefile_diagnostics_repair,
        _apply_deterministic_html_typescript_module_script_repair,
        _apply_deterministic_typescript_vitest_globals_repair,
        _apply_deterministic_typescript_missing_export_repair,
        _apply_deterministic_typescript_member_alias_repair,
        _apply_deterministic_typescript_missing_member_repair,
        _apply_deterministic_typescript_uninitialized_property_repair,
        _apply_deterministic_typescript_number_to_string_argument_repair,
        _apply_deterministic_typescript_canvas_scale_return_type_repair,
        _apply_deterministic_typescript_too_few_arguments_repair,
    )
    results: list[dict[str, Any]] = []
    for runner in step_runners:
        results.extend(
            runner(
                adapter,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
    return results


def _run_materialization_node_manifest(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.npm_repairs import (
        _apply_deterministic_npm_test_script_repair,
        _apply_deterministic_runtime_dependency_repair,
    )

    results: list[dict[str, Any]] = []
    results.extend(
        _apply_deterministic_npm_test_script_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_runtime_dependency_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    return results


def _run_materialization_rust_compiler(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.rust_repairs import (
        _apply_deterministic_rust_crate_import_repair,
        _apply_deterministic_rust_dependency_repair,
        _apply_deterministic_rust_derive_repair,
        _apply_deterministic_rust_lib_root_facade_repair,
        _apply_deterministic_rust_line_suggestion_repair,
        _apply_deterministic_rust_missing_lib_target_repair,
        _apply_deterministic_rust_trait_import_repair,
        _apply_deterministic_rust_unresolved_pub_use_repair,
    )

    step_runners = (
        _apply_deterministic_rust_crate_import_repair,
        _apply_deterministic_rust_dependency_repair,
        _apply_deterministic_rust_derive_repair,
        _apply_deterministic_rust_missing_lib_target_repair,
        _apply_deterministic_rust_lib_root_facade_repair,
        _apply_deterministic_rust_line_suggestion_repair,
        _apply_deterministic_rust_unresolved_pub_use_repair,
        _apply_deterministic_rust_trait_import_repair,
    )
    results: list[dict[str, Any]] = []
    for runner in step_runners:
        results.extend(
            runner(
                adapter,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
    return results


def _run_materialization_target_runtime(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.generic_repairs import _apply_deterministic_missing_declared_target_repair
    from .deterministic_repairs.javascript_repairs import (
        _apply_deterministic_javascript_esm_commonjs_entrypoint_repair,
        _apply_deterministic_javascript_missing_export_repair,
        _apply_deterministic_javascript_missing_method_runtime_repair,
        _apply_deterministic_javascript_test_missing_target_repair,
        _apply_deterministic_javascript_typescript_annotation_repair,
    )

    results: list[dict[str, Any]] = []
    results.extend(
        _apply_deterministic_missing_declared_target_repair(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_javascript_test_missing_target_repair(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_javascript_typescript_annotation_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_javascript_missing_export_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_javascript_esm_commonjs_entrypoint_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_javascript_missing_method_runtime_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    return results


def _run_materialization_python_import(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    from .deterministic_repairs.python_repairs import (
        _apply_deterministic_python_package_shadow_bridge_repair,
        _apply_deterministic_python_unittest_runtime_failure_repair,
        _apply_deterministic_unresolved_import_symbol_repair,
    )

    results: list[dict[str, Any]] = []
    results.extend(
        _apply_deterministic_python_unittest_runtime_failure_repair(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_python_package_shadow_bridge_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_unresolved_import_symbol_repair(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    return results


def _run_materialization_go_import(adapter: Any, *, task_id: str) -> list[dict[str, Any]]:
    from .deterministic_repairs.generic_repairs import (
        _apply_deterministic_go_module_import_repair,
    )

    return _apply_deterministic_go_module_import_repair(adapter, task_id=task_id)


def _annotate_materialization_quality_summary(
    *,
    step_summaries: dict[str, dict[str, Any]],
    tool_results: list[dict[str, Any]],
    artifact_quality_errors: list[str],
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    coverage_preaudit: dict[str, Any],
) -> dict[str, Any]:
    source_tools = _source_tools(tool_results)
    bridged_summary: dict[str, Any] = {
        "stage": "deterministic_quality_repair",
        "attempted": bool(tool_results),
        "success": False,
        "revalidated": False,
        "success_reason": "repair_actions_require_quality_gate_rerun",
        "tool_results": len(tool_results),
        "write_tool_evidence": has_successful_write_tool(tool_results),
        "source_tools": source_tools,
        "source_tool_profiles": summarize_deterministic_repair_source_tools(source_tools),
        "materialization_quality_step_summaries": step_summaries,
        "coverage_preaudit": coverage_preaudit,
    }
    repair_kernel = project_repair_kernel_summary(
        stage="materialization_quality_repairs",
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
        mode="commit",
    )
    bridged_summary["repair_kernel"] = repair_kernel
    bridged_summary["dark_launch_comparison"] = _project_dark_launch_self_check(
        tool_results=tool_results,
        repair_kernel=repair_kernel,
    )
    bridged_summary["materialization_quality_bridge"] = {
        "schema_version": "director.materialization_quality_repair_bridge.v1",
        "mode": "runtime_schedule_step_runner_adapter",
        "bridge_file": "roles.adapters.internal.director.materialization_quality_repair_bridge",
        "legacy_strategy_host": "roles.adapters.internal.director.deterministic_repairs",
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "ordered_step_ids": [step.step_id for step in ordered_steps],
        "runner_step_ids": [step.step_id for step in ordered_steps],
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
        "receipt_count": repair_kernel.get("receipt_count", 0),
        "coverage_preaudit_uncovered_diagnostic_count": coverage_preaudit.get("uncovered_diagnostic_count", 0),
        "coverage_preaudit_rule_discovery_required": coverage_preaudit.get("rule_discovery_required", False),
        "dark_launch_cutover_ready": bridged_summary["dark_launch_comparison"]["cutover_ready"],
        "dark_launch_cutover_blockers": bridged_summary["dark_launch_comparison"]["cutover_blockers"],
        "coverage_uncovered_diagnostic_count": dict(repair_kernel.get("coverage_report") or {}).get(
            "uncovered_diagnostic_count",
            0,
        ),
    }
    return bridged_summary


def _project_coverage_preaudit(artifact_quality_errors: list[str]) -> dict[str, Any]:
    """Project read-only rule coverage before any bridge runner writes."""

    return query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
        )
    ).to_dict()


def _project_dark_launch_self_check(
    *,
    tool_results: list[dict[str, Any]],
    repair_kernel: dict[str, Any],
) -> dict[str, Any]:
    receipts = tuple(
        receipt
        for receipt in (_repair_receipt_v1_from_payload(item) for item in repair_kernel.get("receipts") or ())
        if receipt is not None
    )
    comparison = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            comparison_mode="legacy_projection_self_check",
            legacy_tool_results=tuple(tool_results),
            kernel_receipts=receipts,
        )
    )
    payload = comparison.to_dict()
    return {
        "schema_version": payload["schema_version"],
        "source": payload["source"],
        "access": payload["access"],
        "comparison_mode": payload["comparison_mode"],
        "matched": payload["matched"],
        "cutover_ready": payload["cutover_ready"],
        "cutover_blockers": payload["cutover_blockers"],
        "independent_shadow_required": payload["independent_shadow_required"],
        "independent_shadow_satisfied": payload["independent_shadow_satisfied"],
        "execution_boundary": payload["execution_boundary"],
        "writes_allowed": payload["writes_allowed"],
        "legacy_source_tools": payload["legacy_source_tools"],
        "kernel_source_tools": payload["kernel_source_tools"],
        "missing_source_tools_in_kernel": payload["missing_source_tools_in_kernel"],
        "extra_source_tools_in_kernel": payload["extra_source_tools_in_kernel"],
        "missing_paths_in_kernel": payload["missing_paths_in_kernel"],
        "extra_paths_in_kernel": payload["extra_paths_in_kernel"],
        "metadata": payload["metadata"],
    }


def _repair_receipt_v1_from_payload(payload: Any) -> RepairReceiptV1 | None:
    if not isinstance(payload, dict):
        return None
    receipt_id = str(payload.get("receipt_id") or "").strip()
    plan_id = str(payload.get("plan_id") or "").strip()
    source_tool = str(payload.get("source_tool") or "").strip()
    status = str(payload.get("status") or "").strip()
    if not receipt_id or not plan_id or not source_tool or not status:
        return None
    return RepairReceiptV1(
        receipt_id=receipt_id,
        plan_id=plan_id,
        source_tool=source_tool,
        status=status,
        authoritative=bool(payload.get("authoritative")),
        files_changed=tuple(str(item) for item in payload.get("files_changed") or ()),
        before_hashes=dict(payload.get("before_hashes") or {}),
        after_hashes=dict(payload.get("after_hashes") or {}),
        round_number=payload.get("round_number"),
        errors_before=payload.get("errors_before"),
        errors_after=payload.get("errors_after"),
        net_error_reduction=payload.get("net_error_reduction"),
        revalidation_evidence=dict(payload.get("revalidation_evidence") or {}),
        metadata=dict(payload.get("metadata") or {}),
    )


def _summarize_step_results(
    step: DirectorRepairMaterializationQualityStepV1,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    source_tools = _source_tools(tool_results)
    return {
        "step_id": step.step_id,
        "language": step.language,
        "phase": step.phase,
        "priority": step.priority,
        "source_tool": step.source_tool,
        "result_count": len(tool_results),
        "write_tool_evidence": has_successful_write_tool(tool_results),
        "source_tools": source_tools,
        "source_tool_profiles": summarize_deterministic_repair_source_tools(source_tools),
    }


def _source_tools(tool_results: list[dict[str, Any]]) -> list[str]:
    source_tools: list[str] = []
    for item in tool_results:
        result = item.get("result")
        if isinstance(result, dict):
            source_tools.append(str(result.get("source_tool") or ""))
    return source_tools


__all__ = [
    "run_materialization_quality_repairs",
    "run_typescript_semantic_quality_repairs",
]
