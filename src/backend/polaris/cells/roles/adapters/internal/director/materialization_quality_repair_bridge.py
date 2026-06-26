"""Materialization-quality deterministic repair bridge for Director adapter.

This module is the migration-time boundary between the legacy materialization
quality repair host and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import (
    CompareDirectorRepairShadowRunV1,
    DirectorRepairMaterializationQualityStepV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairReceiptV1,
    compare_director_repair_shadow_run,
    query_director_repair_coverage,
    query_director_repair_materialization_quality_schedule,
    query_director_repair_strategy_catalog,
    run_director_materialization_quality_repair_schedule_result,
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
_CALLBACK_RECEIPT_MIGRATION_BLOCKER = "callback runners still return tool_results instead of RepairReceipt"
_MATERIALIZATION_NATIVE_RECEIPT_STANDARDIZATION_STEP_IDS = ("materialization.hygiene_scaffold",)
_NON_AUTHORITATIVE_CALLBACK_RECEIPT_AUTHORITIES = {
    "non_authoritative_callback_projection",
    "non_authoritative_callback_receipt_projection",
    "non_authoritative_callback_tool_result_projection",
}
_MATERIALIZATION_RUST_RUNTIME_SOURCE_TOOLS = (
    "deterministic_rust_crate_import_rewrite_repair",
    "deterministic_rust_dependency_repair",
    "deterministic_rust_serde_derive_repair",
    "deterministic_rust_line_suggestion_repair",
    "deterministic_rust_unresolved_pub_use_repair",
    "deterministic_rust_trait_import_repair",
)


def run_materialization_quality_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run materialization-quality repairs through the migration bridge."""

    coverage_preaudit = _project_coverage_preaudit(artifact_quality_errors)
    convergence_verifier_present = convergence_verifier is not None
    runtime_schedule_steps = _runtime_materialization_quality_schedule_steps()
    runner_step_ids = tuple(_MATERIALIZATION_QUALITY_REPAIR_RUNNERS)
    _require_materialization_schedule_reconciliation(
        runtime_steps=runtime_schedule_steps,
        runner_step_ids=runner_step_ids,
    )

    def _run_step(step: DirectorRepairMaterializationQualityStepV1) -> list[dict[str, Any]]:
        tool_results = _run_legacy_materialization_quality_repair_step(
            step.step_id,
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
        return tool_results

    schedule_result = run_director_materialization_quality_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=_run_step,
    )
    ordered_steps = schedule_result.ordered_steps
    schedule_reconciliation = _require_materialization_schedule_reconciliation(
        runtime_steps=runtime_schedule_steps,
        runner_step_ids=runner_step_ids,
        result_steps=ordered_steps,
    )
    receipt_projections = [
        _materialization_callback_receipt_projection_to_dict(item) for item in schedule_result.receipt_projections
    ]
    tool_results = _project_materialization_tool_results_with_runtime_metadata(
        [dict(item) for item in schedule_result.tool_results],
        ordered_steps=ordered_steps,
    )
    step_summaries = _summarize_materialization_schedule_steps(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    bridged_summary = _annotate_materialization_quality_summary(
        step_summaries=step_summaries,
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
        ordered_steps=ordered_steps,
        coverage_preaudit=coverage_preaudit,
        schedule_summary=dict(schedule_result.summary),
        receipt_projections=receipt_projections,
        schedule_reconciliation=schedule_reconciliation,
        convergence_verifier_present=convergence_verifier_present,
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
    convergence_verifier: Callable[[Any], Any] | None = None,
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
        return _run_materialization_go_import(
            adapter,
            task_id=task_id,
            convergence_verifier=convergence_verifier,
        )
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
        _apply_deterministic_rust_lib_root_facade_repair,
        _apply_deterministic_rust_missing_lib_target_repair,
    )

    legacy_step_runners = (
        _apply_deterministic_rust_missing_lib_target_repair,
        _apply_deterministic_rust_lib_root_facade_repair,
    )
    results: list[dict[str, Any]] = []
    for source_tool in _MATERIALIZATION_RUST_RUNTIME_SOURCE_TOOLS:
        results.extend(
            _run_materialization_rust_runtime_repair(
                adapter,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
                source_tool=source_tool,
            )
        )
    for runner in legacy_step_runners:
        results.extend(
            runner(
                adapter,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
    return results


def _run_materialization_rust_runtime_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
    source_tool: str,
) -> list[dict[str, Any]]:
    from .deterministic_repairs._runtime_bridge import run_runtime_repair_with_director_tools
    from .execution_tools import DirectorToolExecutor

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    base_files = _collect_materialization_rust_base_files(workspace_path)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _collect_materialization_rust_base_files(workspace_path: Path) -> dict[str, str]:
    if not workspace_path.is_dir():
        return {}
    base_files: dict[str, str] = {}
    cargo_path = workspace_path / "Cargo.toml"
    if cargo_path.is_file():
        with suppress(OSError, UnicodeDecodeError):
            base_files["Cargo.toml"] = cargo_path.read_text(encoding="utf-8")
    for rust_file in sorted(workspace_path.rglob("*.rs")):
        try:
            relative = rust_file.relative_to(workspace_path).as_posix()
        except ValueError:
            continue
        if "target" in relative.split("/"):
            continue
        try:
            base_files[relative] = rust_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


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


def _run_materialization_go_import(
    adapter: Any,
    *,
    task_id: str,
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    from .deterministic_repairs.generic_repairs import (
        _apply_deterministic_go_module_import_repair,
    )

    return _apply_deterministic_go_module_import_repair(
        adapter,
        task_id=task_id,
        convergence_verifier=convergence_verifier,
    )


def _runtime_materialization_quality_schedule_steps() -> tuple[DirectorRepairMaterializationQualityStepV1, ...]:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    return tuple(schedule.items)


def _require_materialization_schedule_reconciliation(
    *,
    runtime_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    runner_step_ids: tuple[str, ...],
    result_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...] | None = None,
) -> dict[str, Any]:
    reconciliation = _materialization_schedule_reconciliation(
        runtime_steps=runtime_steps,
        runner_step_ids=runner_step_ids,
        result_steps=result_steps,
    )
    if reconciliation["exact_match"]:
        return reconciliation
    raise RuntimeError(f"materialization quality repair runner bindings drift from runtime schedule: {reconciliation}")


def _materialization_schedule_reconciliation(
    *,
    runtime_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    runner_step_ids: tuple[str, ...],
    result_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...] | None = None,
) -> dict[str, Any]:
    runtime_step_ids = [step.step_id for step in runtime_steps]
    runner_ids = [str(step_id or "").strip() for step_id in runner_step_ids if str(step_id or "").strip()]
    result_step_ids = [step.step_id for step in result_steps] if result_steps is not None else []
    runtime_id_set = set(runtime_step_ids)
    runner_id_set = set(runner_ids)
    result_id_set = set(result_step_ids)
    runtime_has_unique_steps = len(runtime_step_ids) == len(runtime_id_set)
    runner_has_unique_steps = len(runner_ids) == len(runner_id_set)
    result_matches_runtime = result_steps is None or result_step_ids == runtime_step_ids
    return {
        "schema_version": "director.materialization_quality_schedule_reconciliation.v1",
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "runtime_step_ids": runtime_step_ids,
        "runner_step_ids": runner_ids,
        "schedule_result_step_ids": result_step_ids,
        "runtime_step_count": len(runtime_step_ids),
        "runner_step_count": len(runner_ids),
        "schedule_result_step_count": len(result_step_ids) if result_steps is not None else None,
        "runtime_has_unique_steps": runtime_has_unique_steps,
        "runner_has_unique_steps": runner_has_unique_steps,
        "runner_key_set_matches_runtime": runner_id_set == runtime_id_set,
        "runner_order_matches_runtime": runner_ids == runtime_step_ids,
        "schedule_result_matches_runtime": result_matches_runtime,
        "missing_runner_step_ids": sorted(runtime_id_set - runner_id_set),
        "extra_runner_step_ids": sorted(runner_id_set - runtime_id_set),
        "missing_schedule_result_step_ids": sorted(runtime_id_set - result_id_set) if result_steps is not None else [],
        "extra_schedule_result_step_ids": sorted(result_id_set - runtime_id_set) if result_steps is not None else [],
        "exact_match": (
            runtime_has_unique_steps
            and runner_has_unique_steps
            and runner_ids == runtime_step_ids
            and result_matches_runtime
        ),
    }


def _project_materialization_tool_results_with_runtime_metadata(
    tool_results: list[dict[str, Any]],
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
) -> list[dict[str, Any]]:
    steps_by_id = {step.step_id: step for step in ordered_steps}
    projected: list[dict[str, Any]] = []
    for item in tool_results:
        copied = dict(item)
        result = copied.get("result")
        payload = dict(result) if isinstance(result, dict) else None
        step = None
        if payload is not None:
            step = steps_by_id.get(_payload_step_id(payload))
            if step is None and len(ordered_steps) == 1:
                step = ordered_steps[0]
            if step is not None:
                _apply_runtime_step_metadata(payload, step)
                copied["result"] = payload
        if step is not None:
            copied.setdefault("runtime_step_id", step.step_id)
            copied.setdefault("scheduler_step_id", step.step_id)
            copied.setdefault("bridge_step_id", step.step_id)
            copied.setdefault("phase", step.phase)
            copied.setdefault("priority", step.priority)
            copied.setdefault("depends_on", list(step.depends_on))
            copied["evidence_status"] = _tool_result_evidence_status(copied)
        else:
            copied.setdefault("evidence_status", "missing_evidence")
        projected.append(copied)
    return projected


def _apply_runtime_step_metadata(
    payload: dict[str, Any],
    step: DirectorRepairMaterializationQualityStepV1,
) -> None:
    payload["runtime_step_id"] = step.step_id
    payload["scheduler_step_id"] = step.step_id
    payload["bridge_step_id"] = step.step_id
    payload["language"] = step.language
    payload["phase"] = step.phase
    payload["priority"] = step.priority
    payload["depends_on"] = list(step.depends_on)
    payload["evidence_status"] = _payload_evidence_status(payload)


def _annotate_materialization_quality_summary(
    *,
    step_summaries: dict[str, dict[str, Any]],
    tool_results: list[dict[str, Any]],
    artifact_quality_errors: list[str],
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    coverage_preaudit: dict[str, Any],
    schedule_summary: dict[str, Any] | None = None,
    receipt_projections: list[dict[str, Any]] | None = None,
    schedule_reconciliation: dict[str, Any] | None = None,
    convergence_verifier_present: bool = False,
) -> dict[str, Any]:
    source_tools = _source_tools(tool_results)
    payloads = [_result_payload(item) for item in tool_results if isinstance(item, dict)]
    callback_receipt_projections = _selected_materialization_callback_receipt_projections(
        payloads=payloads,
        receipt_projections=receipt_projections or [],
    )
    native_receipts_by_step = _native_repair_kernel_receipts_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    migration_debt = _project_materialization_quality_migration_debt(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
        callback_receipt_projections=callback_receipt_projections,
        native_receipts_by_step=native_receipts_by_step,
        convergence_verifier_present=convergence_verifier_present,
    )
    receipt_lifecycle_by_step = _materialization_receipt_lifecycle_by_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
        callback_receipt_projections=callback_receipt_projections,
        native_receipts_by_step=native_receipts_by_step,
        migration_debt=migration_debt,
    )
    step_summaries = _annotate_materialization_step_summaries_with_receipt_lifecycle(
        step_summaries=step_summaries,
        receipt_lifecycle_by_step=receipt_lifecycle_by_step,
    )
    bridged_summary: dict[str, Any] = {
        "stage": "deterministic_quality_repair",
        "attempted": bool(tool_results),
        "success": False,
        "revalidated": False,
        "convergence_verifier_present": convergence_verifier_present,
        "success_reason": "repair_actions_require_quality_gate_rerun",
        "tool_results": len(tool_results),
        "write_tool_evidence": has_successful_write_tool(tool_results),
        "source_tools": source_tools,
        "source_tool_profiles": summarize_deterministic_repair_source_tools(source_tools),
        "materialization_quality_step_summaries": step_summaries,
        "coverage_preaudit": coverage_preaudit,
        "repair_kernel_migration_debt": migration_debt,
        "legacy_callback_debt": migration_debt["legacy_callback_debt"],
    }
    repair_kernel = project_repair_kernel_summary(
        stage="materialization_quality_repairs",
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
        mode="commit",
    )
    bridged_summary["repair_kernel"] = repair_kernel
    bridged_summary["scheduler_bridge"] = _build_materialization_scheduler_bridge_summary(
        tool_results=tool_results,
        repair_kernel=repair_kernel,
        ordered_steps=ordered_steps,
        migration_debt=migration_debt,
        schedule_summary=schedule_summary,
        receipt_projections=receipt_projections,
        schedule_reconciliation=schedule_reconciliation,
    )
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
        "runner_step_ids": list((schedule_reconciliation or {}).get("runner_step_ids") or ()),
        "runner_binding_reconciliation": dict(schedule_reconciliation or {}),
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
        "convergence_verifier_present": convergence_verifier_present,
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


def _build_materialization_scheduler_bridge_summary(
    *,
    tool_results: list[dict[str, Any]],
    repair_kernel: dict[str, Any],
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    migration_debt: dict[str, Any],
    schedule_summary: dict[str, Any] | None = None,
    receipt_projections: list[dict[str, Any]] | None = None,
    schedule_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = [_result_payload(item) for item in tool_results if isinstance(item, dict)]
    schedule_summary_payload = dict(schedule_summary or {})
    step_evidence_statuses = _materialization_step_evidence_statuses(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    receipts = repair_kernel.get("receipts")
    receipt_payloads = (
        [receipt for receipt in receipts if isinstance(receipt, dict)] if isinstance(receipts, list) else []
    )
    callback_receipt_projections = _selected_materialization_callback_receipt_projections(
        payloads=payloads,
        receipt_projections=receipt_projections or [],
    )
    native_receipts_by_step = _native_repair_kernel_receipts_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    native_receipt_payloads = [
        receipt for receipts_for_step in native_receipts_by_step.values() for receipt in receipts_for_step
    ]
    receipt_lifecycle_by_step = _materialization_receipt_lifecycle_by_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
        callback_receipt_projections=callback_receipt_projections,
        native_receipts_by_step=native_receipts_by_step,
        migration_debt=migration_debt,
    )
    selected_step_native_cutover_evidence = {
        step_id: dict(receipt_lifecycle_by_step.get(step_id, {}).get("native_cutover_evidence") or {})
        for step_id in _MATERIALIZATION_NATIVE_RECEIPT_STANDARDIZATION_STEP_IDS
        if step_id in receipt_lifecycle_by_step
    }
    selected_step_native_cutover_blockers = _ordered_unique(
        blocker
        for evidence in selected_step_native_cutover_evidence.values()
        for blocker in evidence.get("cutover_blockers", [])
    )
    selected_step_native_cutover_ready = bool(selected_step_native_cutover_evidence) and all(
        bool(evidence.get("cutover_ready")) for evidence in selected_step_native_cutover_evidence.values()
    )
    return {
        "schema_version": "director.materialization_quality_scheduler_bridge.v1",
        "mode": "legacy_callback_bridge",
        "target_scheduler": "director.runtime.repair_kernel.scheduler",
        "schedule_source": "director.runtime.public.query_director_repair_materialization_quality_schedule",
        "runner_binding_owner": "roles.adapters",
        "runner_binding_reconciliation": dict(schedule_reconciliation or {}),
        "step_order": [step.to_dict() for step in ordered_steps],
        "active_step_ids": _sorted_unique(_payload_step_id(payload) for payload in payloads),
        "step_evidence_statuses": step_evidence_statuses,
        "evidence_status_counts": _count_values(step_evidence_statuses.values()),
        "missing_evidence_step_ids": [
            step_id for step_id, status in step_evidence_statuses.items() if status == "missing_evidence"
        ],
        "observed_max_round": max(
            _max_int(payloads, "round_number"),
            _max_int(payloads, "scheduler_round_number"),
            _max_int(callback_receipt_projections, "round_number"),
        ),
        "configured_max_rounds": max(
            _schedule_summary_int(schedule_summary_payload, "max_rounds"),
            _max_configured_rounds(payloads, callback_receipt_projections),
        ),
        "tool_result_count": len(tool_results),
        "source_tools": _sorted_unique(str(payload.get("source_tool") or "") for payload in payloads),
        "phases": _count_by_payload_key(payloads, "phase", default="materialization_quality"),
        "priorities": _count_by_payload_key(payloads, "priority", default="0"),
        "rounds": _count_by_payload_key(payloads, "round_number", default="0"),
        "receipt_count": len(receipt_payloads),
        "repair_kernel_receipt_count": len(receipt_payloads),
        "native_repair_kernel_receipt_count": len(native_receipt_payloads),
        "native_receipt_evidence_status_counts": _count_values(
            _payload_evidence_status(receipt) for receipt in native_receipt_payloads
        ),
        "receipts_with_revalidation": sum(
            1 for receipt in receipt_payloads if _mapping_has_verifier_evidence(receipt.get("revalidation_evidence"))
        ),
        "callback_receipt_projection_count": len(callback_receipt_projections),
        "callback_projection_only_count": sum(
            1 for projection in callback_receipt_projections if bool(projection.get("projection_only", True))
        ),
        "callback_authoritative_receipt_count": 0,
        "callback_receipts_authoritative": False,
        "callback_receipt_authority_values": _sorted_unique(
            _callback_receipt_authority_value(projection) for projection in callback_receipt_projections
        ),
        "callback_receipts_with_revalidation": sum(
            1 for projection in callback_receipt_projections if _callback_projection_has_revalidation(projection)
        ),
        "callback_receipt_evidence_statuses": [
            str(projection.get("evidence_status") or "missing_evidence") for projection in callback_receipt_projections
        ],
        "callback_receipt_evidence_status_counts": _count_values(
            str(projection.get("evidence_status") or "missing_evidence") for projection in callback_receipt_projections
        ),
        "callback_projection_claimed_typed_receipt_path_count": sum(
            1
            for projection in callback_receipt_projections
            if _callback_projection_claims_typed_receipt_path_available(projection)
        ),
        "typed_receipt_path_available": False,
        "native_typed_receipt_path_available": bool(native_receipt_payloads),
        "native_typed_receipt_path_step_ids": [
            step_id for step_id, receipts_for_step in native_receipts_by_step.items() if receipts_for_step
        ],
        "native_receipt_standardization_step_ids": list(_MATERIALIZATION_NATIVE_RECEIPT_STANDARDIZATION_STEP_IDS),
        "selected_step_native_cutover_evidence": selected_step_native_cutover_evidence,
        "selected_step_native_path_available_step_ids": [
            step_id
            for step_id, evidence in selected_step_native_cutover_evidence.items()
            if bool(evidence.get("native_path_available"))
        ],
        "selected_step_native_cutover_ready_step_ids": [
            step_id
            for step_id, evidence in selected_step_native_cutover_evidence.items()
            if bool(evidence.get("cutover_ready"))
        ],
        "selected_step_native_cutover_ready": selected_step_native_cutover_ready,
        "selected_step_native_cutover_blockers": selected_step_native_cutover_blockers,
        "selected_step_native_cutover_blockers_by_step": {
            step_id: list(evidence.get("cutover_blockers") or ())
            for step_id, evidence in selected_step_native_cutover_evidence.items()
        },
        "authoritative_receipts_allowed": False,
        "receipt_lifecycle_by_step": receipt_lifecycle_by_step,
        "receipt_lifecycle_status_counts": _count_values(
            lifecycle.get("receipt_lifecycle_evidence_status") for lifecycle in receipt_lifecycle_by_step.values()
        ),
        "remaining_callback_only_step_ids": list(migration_debt.get("remaining_callback_only_step_ids") or []),
        "callback_only_step_count": int(migration_debt.get("callback_only_step_count") or 0),
        "native_receipt_step_ids": list(migration_debt.get("native_receipt_step_ids") or []),
        "callback_projection_step_ids": list(migration_debt.get("callback_projection_step_ids") or []),
        "cutover_blockers_by_step": {
            step_id: list(lifecycle.get("cutover_blockers") or ())
            for step_id, lifecycle in receipt_lifecycle_by_step.items()
        },
        "migration_blocker": _CALLBACK_RECEIPT_MIGRATION_BLOCKER,
        "repair_kernel_migration_debt": migration_debt,
        "legacy_callback_debt": list(migration_debt.get("legacy_callback_debt") or []),
    }


def _selected_materialization_callback_receipt_projections(
    *,
    payloads: list[dict[str, Any]],
    receipt_projections: list[Any],
) -> list[dict[str, Any]]:
    schedule_receipt_projections = _materialization_callback_receipt_projections_from_schedule_result(
        receipt_projections
    )
    if schedule_receipt_projections:
        return schedule_receipt_projections
    return _materialization_callback_receipt_projections_from_payloads(payloads)


def _materialization_callback_receipt_projections_from_schedule_result(
    receipt_projections: list[Any],
) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for projection in receipt_projections:
        normalized = _materialization_callback_receipt_projection_to_dict(projection)
        if not normalized:
            continue
        normalized["authoritative"] = False
        normalized["projection_only"] = True
        normalized.setdefault("projection_source", "schedule_result.receipt_projections")
        normalized.setdefault("summary_only", False)
        normalized.setdefault("typed_receipt_path_available", False)
        normalized.setdefault("migration_blocker", _CALLBACK_RECEIPT_MIGRATION_BLOCKER)
        normalized["evidence_status"] = _callback_projection_evidence_status(normalized)
        if "bridge_step_id" not in normalized:
            step_id = str(normalized.get("step_id") or normalized.get("scheduler_step_id") or "").strip()
            if step_id:
                normalized["bridge_step_id"] = step_id
        normalized["receipt_authority"] = _non_authoritative_callback_receipt_authority(
            _callback_receipt_authority_value(normalized),
            default="non_authoritative_callback_projection",
        )
        projections.append(normalized)
    return projections


def _materialization_callback_receipt_projection_to_dict(projection: Any) -> dict[str, Any]:
    to_dict = getattr(projection, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
        except (RuntimeError, TypeError, ValueError) as exc:
            return {
                "projection_error": "projection_to_dict_failed",
                "projection_type": type(projection).__name__,
                "error": str(exc),
            }
        if isinstance(value, Mapping):
            return dict(value)
        return {
            "projection_error": "projection_to_dict_returned_non_mapping",
            "projection_type": type(projection).__name__,
            "value_type": type(value).__name__,
        }
    if isinstance(projection, Mapping):
        return dict(projection)
    return {
        "projection_error": "unsupported_projection_type",
        "projection_type": type(projection).__name__,
    }


def _materialization_callback_receipt_projections_from_payloads(
    payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for payload in payloads:
        explicit_projections = _explicit_materialization_callback_receipt_projections(payload)
        if explicit_projections:
            projections.extend(
                _normalize_materialization_callback_receipt_projection(
                    projection,
                    payload=payload,
                    source=source,
                )
                for projection, source in explicit_projections
            )
            continue
        if _payload_has_callback_receipt_projection_annotation(payload):
            projections.append(_summary_only_materialization_callback_receipt_projection(payload))
    return projections


def _explicit_materialization_callback_receipt_projections(
    payload: dict[str, Any],
) -> list[tuple[dict[str, Any], str]]:
    projections: list[tuple[dict[str, Any], str]] = []
    for source_payload, source_prefix in (
        (payload, "payload"),
        (payload.get("repair_kernel"), "payload.repair_kernel"),
    ):
        if not isinstance(source_payload, dict):
            continue
        for key in (
            "callback_receipt_projection",
            "callback_receipt_projections",
            "receipt_projection",
            "receipt_projections",
        ):
            raw_projection = source_payload.get(key)
            source = f"{source_prefix}.{key}"
            if isinstance(raw_projection, dict):
                projections.append((dict(raw_projection), source))
                continue
            if isinstance(raw_projection, list):
                projections.extend((dict(item), source) for item in raw_projection if isinstance(item, dict))
    return projections


def _normalize_materialization_callback_receipt_projection(
    projection: dict[str, Any],
    *,
    payload: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    normalized = dict(projection)
    normalized["authoritative"] = False
    normalized["projection_only"] = True
    normalized.setdefault("projection_source", source)
    normalized.setdefault("summary_only", False)
    normalized.setdefault("source_tool", payload.get("source_tool"))
    normalized.setdefault("bridge_step_id", _payload_step_id(payload))
    normalized.setdefault("typed_receipt_path_available", _payload_typed_receipt_path_available(payload))
    normalized.setdefault("migration_blocker", _payload_migration_blocker(payload))
    normalized["receipt_authority"] = _non_authoritative_callback_receipt_authority(
        _callback_receipt_authority_value(normalized) or _payload_receipt_authority(payload),
        default="non_authoritative_callback_receipt_projection",
    )
    _attach_payload_revalidation_to_projection(normalized, payload)
    normalized["evidence_status"] = _callback_projection_evidence_status(normalized)
    return normalized


def _summary_only_materialization_callback_receipt_projection(payload: dict[str, Any]) -> dict[str, Any]:
    projection = {
        "projection_source": "summary_only_payload_annotation",
        "summary_only": True,
        "source_tool": payload.get("source_tool"),
        "bridge_step_id": _payload_step_id(payload),
        "authoritative": False,
        "projection_only": True,
        "receipt_authority": (
            _non_authoritative_callback_receipt_authority(
                _payload_receipt_authority(payload),
                default="non_authoritative_callback_tool_result_projection",
            )
        ),
        "typed_receipt_path_available": _payload_typed_receipt_path_available(payload),
        "migration_blocker": _payload_migration_blocker(payload),
    }
    _attach_payload_revalidation_to_projection(projection, payload)
    projection["evidence_status"] = _callback_projection_evidence_status(projection)
    return projection


def _payload_has_callback_receipt_projection_annotation(payload: dict[str, Any]) -> bool:
    if any(
        bool(payload.get(key))
        for key in (
            "legacy_callback_bridge",
            "produces_tool_results_only",
            "callback_migration_envelope",
            "migration_callback_envelope",
            "convergence_scheduler_required",
            "callback_receipt_projection_available",
        )
    ):
        return True
    if payload.get("typed_receipt_path") == "unavailable_in_callback_bridge":
        return True
    if payload.get("typed_receipt_path_available") is False and payload.get("preferred_typed_receipt_entrypoint"):
        return True
    revalidation = payload.get("revalidation")
    return isinstance(revalidation, dict) and any(
        bool(revalidation.get(key))
        for key in (
            "callback_migration_envelope",
            "convergence_scheduler_required",
            "callback_receipt_projection_available",
        )
    )


def _attach_payload_revalidation_to_projection(
    projection: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    revalidation = payload.get("revalidation")
    if "revalidation" not in projection and isinstance(revalidation, dict) and revalidation:
        projection["revalidation"] = dict(revalidation)
    revalidation_evidence = _payload_revalidation_evidence(payload)
    if "revalidation_evidence" not in projection and revalidation_evidence:
        projection["revalidation_evidence"] = revalidation_evidence


def _payload_revalidation_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("revalidation_evidence", "revalidation"):
        evidence = payload.get(key)
        if isinstance(evidence, dict) and evidence:
            return dict(evidence)
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        kernel_evidence = repair_kernel.get("revalidation_evidence")
        if isinstance(kernel_evidence, dict) and kernel_evidence:
            return dict(kernel_evidence)
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, list):
            for receipt in receipts:
                if not isinstance(receipt, dict):
                    continue
                receipt_evidence = receipt.get("revalidation_evidence")
                if isinstance(receipt_evidence, dict) and receipt_evidence:
                    return dict(receipt_evidence)
    return {}


def _payload_receipt_authority(payload: dict[str, Any]) -> str:
    for source in (payload, payload.get("repair_kernel")):
        if not isinstance(source, dict):
            continue
        for key in ("receipt_authority", "receipt_authority_value", "authority", "authority_value"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _callback_receipt_authority_value(projection: dict[str, Any]) -> str:
    for key in ("receipt_authority", "receipt_authority_value", "authority", "authority_value"):
        value = str(projection.get(key) or "").strip()
        if value:
            return value
    return ""


def _non_authoritative_callback_receipt_authority(
    value: Any,
    *,
    default: str,
) -> str:
    token = str(value or "").strip()
    if token in _NON_AUTHORITATIVE_CALLBACK_RECEIPT_AUTHORITIES:
        return token
    return default


def _callback_projection_has_revalidation(projection: dict[str, Any]) -> bool:
    if bool(projection.get("revalidation_evidence_present")):
        return True
    for key in ("revalidation", "revalidation_evidence"):
        value = projection.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _callback_projection_has_verifier_evidence(projection: dict[str, Any]) -> bool:
    return _callback_projection_evidence_status(projection) != "missing_evidence"


def _callback_projection_evidence_status(projection: dict[str, Any]) -> str:
    for key in ("revalidation_evidence", "revalidation"):
        evidence = projection.get(key)
        status = _evidence_mapping_status(evidence)
        if status != "missing_evidence":
            return status
    if not bool(projection.get("revalidation_evidence_present")):
        claimed = _claimed_evidence_status(projection)
        return claimed if claimed in {"missing_evidence", "failed_evidence"} else "missing_evidence"
    exit_code = _optional_int(projection.get("revalidation_exit_code"))
    if exit_code is None:
        return "missing_evidence"
    residual_count = _optional_int(projection.get("revalidation_residual_count"))
    if exit_code != 0 or (residual_count is not None and residual_count > 0):
        return "failed_evidence"
    return "resolved_evidence"


def _payload_evidence_status(payload: dict[str, Any]) -> str:
    claimed = _claimed_evidence_status(payload)
    if claimed in {"missing_evidence", "failed_evidence"}:
        return claimed
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        claimed = _claimed_evidence_status(repair_kernel)
        if claimed in {"missing_evidence", "failed_evidence"}:
            return claimed
    status = _evidence_mapping_status(_payload_revalidation_evidence(payload))
    if status != "missing_evidence":
        return status
    if isinstance(repair_kernel, dict):
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, list):
            receipt_statuses = [_payload_evidence_status(receipt) for receipt in receipts if isinstance(receipt, dict)]
            return _aggregate_evidence_status(receipt_statuses)
    return "missing_evidence"


def _tool_result_evidence_status(tool_result: dict[str, Any]) -> str:
    result = tool_result.get("result")
    if isinstance(result, dict):
        status = _payload_evidence_status(result)
        if status != "missing_evidence":
            return status
    claimed = _claimed_evidence_status(tool_result)
    return claimed if claimed in {"missing_evidence", "failed_evidence"} else "missing_evidence"


def _evidence_mapping_status(evidence: Any) -> str:
    if not _mapping_has_verifier_evidence(evidence):
        return "missing_evidence"
    if not isinstance(evidence, dict):
        return "missing_evidence"
    command = evidence.get("command") or evidence.get("verifier_command")
    exit_code = _optional_int(evidence.get("exit_code"))
    if exit_code is None:
        exit_code = _optional_int(evidence.get("revalidation_exit_code"))
    if not command or exit_code is None:
        return "missing_evidence"
    residual_count = _evidence_residual_count(evidence)
    errors_after = _optional_int(evidence.get("errors_after"))
    if errors_after is None:
        errors_after = _optional_int(evidence.get("errors_after_count"))
    if exit_code != 0:
        return "failed_evidence"
    if residual_count is not None and residual_count > 0:
        return "failed_evidence"
    if errors_after is not None and errors_after > 0:
        return "failed_evidence"
    return "resolved_evidence"


def _evidence_residual_count(evidence: dict[str, Any]) -> int | None:
    residual_count = _optional_int(evidence.get("revalidation_residual_count"))
    if residual_count is None:
        residual_count = _optional_int(evidence.get("residual_count"))
    if residual_count is None:
        residual_count = _optional_int(evidence.get("residual_diagnostic_count"))
    if residual_count is not None:
        return residual_count
    residual_ids = evidence.get("residual_diagnostic_ids")
    if isinstance(residual_ids, list | tuple | set):
        return len(residual_ids)
    return None


def _claimed_evidence_status(payload: dict[str, Any]) -> str:
    value = str(payload.get("evidence_status") or "").strip()
    if value in {"missing_evidence", "failed_evidence", "resolved_evidence"}:
        return value
    return ""


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aggregate_evidence_status(statuses: Any) -> str:
    normalized = [str(status or "").strip() for status in statuses if str(status or "").strip()]
    if not normalized:
        return "missing_evidence"
    if "failed_evidence" in normalized:
        return "failed_evidence"
    if "missing_evidence" in normalized:
        return "missing_evidence"
    if all(status == "resolved_evidence" for status in normalized):
        return "resolved_evidence"
    return "missing_evidence"


def _callback_projection_claims_typed_receipt_path_available(projection: dict[str, Any]) -> bool:
    if _bool_claim(projection.get("typed_receipt_path_available")):
        return True
    metadata = projection.get("metadata")
    if isinstance(metadata, dict):
        return _bool_claim(metadata.get("claimed_typed_receipt_path_available"))
    return False


def _payload_typed_receipt_path_available(payload: dict[str, Any]) -> bool:
    if "typed_receipt_path_available" in payload:
        return _bool_claim(payload.get("typed_receipt_path_available"))
    revalidation = payload.get("revalidation")
    if isinstance(revalidation, dict):
        return _bool_claim(revalidation.get("typed_receipt_path_available"))
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        return _bool_claim(repair_kernel.get("typed_receipt_path_available"))
    return False


def _payload_migration_blocker(payload: dict[str, Any]) -> str:
    blocker = str(payload.get("migration_blocker") or "").strip()
    return blocker or _CALLBACK_RECEIPT_MIGRATION_BLOCKER


def _payload_step_id(payload: dict[str, Any]) -> str:
    for key in ("bridge_step_id", "step_id", "scheduler_step_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _result_payload(tool_result: dict[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return result if isinstance(result, dict) else {}


def _count_by_payload_key(
    payloads: list[dict[str, Any]],
    key: str,
    *,
    default: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in payloads:
        value = str(payload.get(key) if payload.get(key) is not None else default).strip() or default
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _sorted_unique(values: Any) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        token = str(value or "").strip()
        if not token:
            continue
        counts[token] = counts.get(token, 0) + 1
    return dict(sorted(counts.items()))


def _callback_receipt_projections_by_materialization_step(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    callback_receipt_projections: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_step: dict[str, list[dict[str, Any]]] = {step.step_id: [] for step in ordered_steps}
    untagged: list[dict[str, Any]] = []
    for projection in callback_receipt_projections:
        if not isinstance(projection, dict):
            continue
        step_id = _payload_step_id(projection)
        if step_id in by_step:
            by_step[step_id].append(projection)
        else:
            untagged.append(projection)
    if len(ordered_steps) == 1 and untagged:
        by_step[ordered_steps[0].step_id].extend(untagged)
    return by_step


def _native_repair_kernel_receipts_by_materialization_step(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    tool_results: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    tool_results_by_step = _tool_results_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    by_step: dict[str, list[dict[str, Any]]] = {step.step_id: [] for step in ordered_steps}
    for step_id, items in tool_results_by_step.items():
        for item in items:
            payload = _result_payload(item)
            repair_kernel = payload.get("repair_kernel")
            if not isinstance(repair_kernel, dict):
                continue
            receipts = repair_kernel.get("receipts")
            if isinstance(receipts, list | tuple):
                for receipt in receipts:
                    if isinstance(receipt, dict):
                        copied = dict(receipt)
                        copied.setdefault("bridge_step_id", step_id)
                        by_step[step_id].append(copied)
                continue
            if repair_kernel.get("receipt_id") or repair_kernel.get("plan_id"):
                copied = dict(repair_kernel)
                copied.setdefault("bridge_step_id", step_id)
                by_step[step_id].append(copied)
    return by_step


def _annotate_materialization_step_summaries_with_receipt_lifecycle(
    *,
    step_summaries: dict[str, dict[str, Any]],
    receipt_lifecycle_by_step: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    annotated: dict[str, dict[str, Any]] = {}
    for step_id, summary in step_summaries.items():
        copied = dict(summary)
        lifecycle = dict(receipt_lifecycle_by_step.get(step_id) or {})
        copied["receipt_lifecycle"] = lifecycle
        for key in (
            "typed_receipt_path_available",
            "native_path_available",
            "native_receipt_path_available",
            "selected_for_native_receipt_standardization",
            "native_cutover_ready",
            "native_cutover_evidence",
            "authoritative_receipts_allowed",
            "native_receipt_present",
            "callback_projection_present",
            "callback_only",
            "projection_only",
            "verifier_evidence_present",
            "native_verifier_evidence_present",
            "callback_verifier_evidence_present",
            "native_repair_kernel_receipt_count",
            "callback_receipt_projection_count",
            "callback_projection_only_count",
            "native_receipt_evidence_status_counts",
            "callback_receipt_evidence_status_counts",
            "receipt_lifecycle_evidence_status_counts",
            "receipt_lifecycle_evidence_status",
            "cutover_blockers",
        ):
            if key in lifecycle:
                copied[key] = lifecycle[key]
        annotated[step_id] = copied
    return annotated


def _materialization_receipt_lifecycle_by_step(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    tool_results: list[dict[str, Any]],
    callback_receipt_projections: list[dict[str, Any]],
    native_receipts_by_step: dict[str, list[dict[str, Any]]],
    migration_debt: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    tool_results_by_step = _tool_results_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    callback_projections_by_step = _callback_receipt_projections_by_materialization_step(
        ordered_steps=ordered_steps,
        callback_receipt_projections=callback_receipt_projections,
    )
    debt_by_step = {
        str(item.get("step_id") or ""): dict(item)
        for item in migration_debt.get("legacy_callback_debt") or ()
        if isinstance(item, dict)
    }
    lifecycle_by_step: dict[str, dict[str, Any]] = {}
    for step in ordered_steps:
        native_receipts = list(native_receipts_by_step.get(step.step_id) or [])
        callback_projections = list(callback_projections_by_step.get(step.step_id) or [])
        step_tool_results = list(tool_results_by_step.get(step.step_id) or [])
        native_statuses = [_payload_evidence_status(receipt) for receipt in native_receipts]
        callback_statuses = [
            str(projection.get("evidence_status") or "missing_evidence") for projection in callback_projections
        ]
        tool_result_statuses = [_tool_result_evidence_status(item) for item in step_tool_results]
        native_verifier_evidence_present = _has_verifier_evidence_in_mappings(native_receipts)
        callback_verifier_evidence_present = any(
            _callback_projection_has_verifier_evidence(projection) for projection in callback_projections
        )
        native_receipt_present = bool(native_receipts)
        callback_projection_present = bool(callback_projections)
        callback_only = callback_projection_present and not native_receipt_present
        native_cutover_evidence = _materialization_native_receipt_cutover_evidence(
            step=step,
            native_receipts=native_receipts,
            callback_projections=callback_projections,
            native_statuses=native_statuses,
            native_verifier_evidence_present=native_verifier_evidence_present,
        )
        lifecycle_statuses = native_statuses + callback_statuses
        if not lifecycle_statuses:
            lifecycle_statuses = tool_result_statuses
        cutover_blockers = _materialization_step_cutover_blockers(
            debt=debt_by_step.get(step.step_id, {}),
            tool_results=step_tool_results,
            native_receipts=native_receipts,
            callback_projections=callback_projections,
            native_statuses=native_statuses,
            callback_statuses=callback_statuses,
            tool_result_statuses=tool_result_statuses,
        )
        lifecycle_by_step[step.step_id] = {
            "step_id": step.step_id,
            "typed_receipt_path_available": native_receipt_present,
            "native_path_available": native_receipt_present,
            "native_receipt_path_available": native_receipt_present,
            "selected_for_native_receipt_standardization": bool(
                native_cutover_evidence.get("selected_for_standardization")
            ),
            "callback_typed_receipt_path_available": False,
            "authoritative_receipts_allowed": False,
            "native_receipt_present": native_receipt_present,
            "callback_projection_present": callback_projection_present,
            "callback_only": callback_only,
            "projection_only": callback_only,
            "verifier_evidence_present": (
                native_verifier_evidence_present
                or callback_verifier_evidence_present
                or _has_native_verifier_evidence(step_tool_results)
            ),
            "native_verifier_evidence_present": native_verifier_evidence_present,
            "callback_verifier_evidence_present": callback_verifier_evidence_present,
            "native_repair_kernel_receipt_count": len(native_receipts),
            "callback_receipt_projection_count": len(callback_projections),
            "callback_projection_only_count": sum(
                1 for projection in callback_projections if bool(projection.get("projection_only", True))
            ),
            "callback_authoritative_receipt_count": 0,
            "native_receipt_evidence_status_counts": _count_values(native_statuses),
            "callback_receipt_evidence_status_counts": _count_values(callback_statuses),
            "tool_result_evidence_status_counts": _count_values(tool_result_statuses),
            "receipt_lifecycle_evidence_status_counts": _count_values(lifecycle_statuses),
            "receipt_lifecycle_evidence_status": _aggregate_evidence_status(lifecycle_statuses),
            "cutover_ready": False,
            "cutover_blockers": cutover_blockers,
            "native_cutover_ready": bool(native_cutover_evidence.get("cutover_ready")),
            "native_cutover_evidence": native_cutover_evidence,
            "migration_blocker": _CALLBACK_RECEIPT_MIGRATION_BLOCKER,
        }
    return lifecycle_by_step


def _materialization_native_receipt_cutover_evidence(
    *,
    step: DirectorRepairMaterializationQualityStepV1,
    native_receipts: list[dict[str, Any]],
    callback_projections: list[dict[str, Any]],
    native_statuses: list[str],
    native_verifier_evidence_present: bool,
) -> dict[str, Any]:
    selected_for_standardization = step.step_id in _MATERIALIZATION_NATIVE_RECEIPT_STANDARDIZATION_STEP_IDS
    native_path_available = bool(native_receipts)
    native_evidence_status = _aggregate_evidence_status(native_statuses)
    native_evidence_failed = "failed_evidence" in native_statuses
    native_evidence_resolved = bool(native_statuses) and all(
        status == "resolved_evidence" for status in native_statuses
    )
    callback_projection_present = bool(callback_projections)
    required_evidence = (
        [
            "native_repair_kernel.receipts",
            "native_revalidation_evidence",
            "resolved_native_evidence_status",
            "callback_projection_absent",
        ]
        if selected_for_standardization
        else []
    )
    missing_required_evidence: list[str] = []
    blockers: list[str] = []
    if not selected_for_standardization:
        blockers.append("step_not_selected_for_native_receipt_standardization")
    else:
        if not native_path_available:
            missing_required_evidence.append("native_repair_kernel.receipts")
            blockers.append("missing_native_repair_receipt")
        if not native_verifier_evidence_present:
            missing_required_evidence.append("native_revalidation_evidence")
            blockers.append("missing_native_revalidation_evidence")
        if native_evidence_failed:
            missing_required_evidence.append("resolved_native_evidence_status")
            blockers.append("failed_revalidation_evidence")
        elif not native_evidence_resolved:
            missing_required_evidence.append("resolved_native_evidence_status")
            blockers.append("missing_native_revalidation_evidence")
        if callback_projection_present:
            missing_required_evidence.append("callback_projection_absent")
            blockers.append("callback_projection_still_present")
    missing_required_evidence = _ordered_unique(missing_required_evidence)
    cutover_blockers = _ordered_unique(blockers)
    return {
        "schema_version": "director.materialization_native_receipt_cutover_evidence.v1",
        "step_id": step.step_id,
        "selected_step_id": step.step_id if selected_for_standardization else "",
        "selected_for_standardization": selected_for_standardization,
        "native_path_required": selected_for_standardization,
        "native_path_available": native_path_available,
        "native_receipt_path_available": native_path_available,
        "native_repair_kernel_receipt_count": len(native_receipts),
        "callback_projection_present": callback_projection_present,
        "callback_receipt_projection_count": len(callback_projections),
        "native_verifier_evidence_required": selected_for_standardization,
        "native_verifier_evidence_present": native_verifier_evidence_present,
        "native_revalidation_evidence_status": native_evidence_status,
        "native_revalidation_evidence_status_counts": _count_values(native_statuses),
        "native_revalidation_evidence_missing": native_evidence_status == "missing_evidence",
        "native_revalidation_evidence_failed": native_evidence_failed,
        "native_revalidation_evidence_resolved": native_evidence_resolved,
        "native_evidence_resolved": native_evidence_resolved,
        "native_receipt_evidence_status_counts": _count_values(native_statuses),
        "required_evidence": required_evidence,
        "missing_required_evidence": missing_required_evidence,
        "all_required_evidence_present": selected_for_standardization and not missing_required_evidence,
        "cutover_ready": selected_for_standardization and not cutover_blockers,
        "cutover_blockers": cutover_blockers,
    }


def _materialization_step_cutover_blockers(
    *,
    debt: dict[str, Any],
    tool_results: list[dict[str, Any]],
    native_receipts: list[dict[str, Any]],
    callback_projections: list[dict[str, Any]],
    native_statuses: list[str],
    callback_statuses: list[str],
    tool_result_statuses: list[str],
) -> list[str]:
    blockers = list(debt.get("blockers") or [])
    if callback_projections:
        blockers.append("callback_projection_only")
    if (tool_results or callback_projections) and not native_receipts:
        blockers.append("missing_native_repair_receipt")
    statuses = native_statuses + callback_statuses
    if not statuses:
        statuses = tool_result_statuses
    if "missing_evidence" in statuses:
        blockers.append("missing_revalidation_evidence")
    if "failed_evidence" in statuses:
        blockers.append("failed_revalidation_evidence")
    return _ordered_unique(blockers)


def _max_configured_rounds(
    payloads: list[dict[str, Any]],
    callback_receipt_projections: list[dict[str, Any]],
) -> int:
    return max(
        _max_int(payloads, "max_rounds"),
        _max_int(payloads, "scheduler_max_rounds"),
        _max_revalidation_int(payloads, "max_rounds"),
        _max_int(callback_receipt_projections, "max_rounds"),
        _max_int(callback_receipt_projections, "scheduler_max_rounds"),
    )


def _max_int(payloads: list[dict[str, Any]], key: str) -> int:
    maximum = 0
    for payload in payloads:
        try:
            maximum = max(maximum, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return maximum


def _max_revalidation_int(payloads: list[dict[str, Any]], key: str) -> int:
    maximum = 0
    for payload in payloads:
        revalidation = payload.get("revalidation")
        if not isinstance(revalidation, dict):
            continue
        try:
            maximum = max(maximum, int(revalidation.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return maximum


def _bool_claim(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _schedule_summary_int(schedule_summary: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(schedule_summary.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _project_materialization_quality_migration_debt(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    tool_results: list[dict[str, Any]],
    callback_receipt_projections: list[dict[str, Any]] | None = None,
    native_receipts_by_step: dict[str, list[dict[str, Any]]] | None = None,
    convergence_verifier_present: bool = False,
) -> dict[str, Any]:
    """Project callback migration debt without claiming runtime cutover."""

    catalog = _repair_strategy_catalog_by_source_tool()
    tool_results_by_step = _tool_results_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    callback_projections_by_step = _callback_receipt_projections_by_materialization_step(
        ordered_steps=ordered_steps,
        callback_receipt_projections=callback_receipt_projections or [],
    )
    native_receipts_by_step = {
        step.step_id: list((native_receipts_by_step or {}).get(step.step_id) or []) for step in ordered_steps
    }
    legacy_callback_debt = [
        _project_legacy_callback_debt_for_step(
            step,
            tool_results_by_step.get(step.step_id, []),
            callback_projections=callback_projections_by_step.get(step.step_id, []),
            native_receipts=native_receipts_by_step.get(step.step_id, []),
            catalog=catalog,
            convergence_verifier_present=convergence_verifier_present,
        )
        for step in ordered_steps
    ]
    blockers = _ordered_unique(blocker for item in legacy_callback_debt for blocker in item.get("blockers", []))
    blocked_steps = [item for item in legacy_callback_debt if not item.get("cutover_ready")]
    remaining_callback_only_step_ids = [
        str(item.get("step_id") or "") for item in legacy_callback_debt if bool(item.get("callback_only"))
    ]
    native_receipt_step_ids = [
        str(item.get("step_id") or "") for item in legacy_callback_debt if bool(item.get("native_receipt_present"))
    ]
    callback_projection_step_ids = [
        str(item.get("step_id") or "") for item in legacy_callback_debt if bool(item.get("callback_projection_present"))
    ]
    return {
        "schema_version": "director.materialization_quality_repair_migration_debt.v1",
        "owner_cell": "roles.adapters",
        "runtime_schedule_owner": "director.runtime",
        "legacy_strategy_host": "roles.adapters.internal.director.deterministic_repairs",
        "bridge_mode": "runtime_schedule_step_runner_adapter",
        "legacy_callback_bridge": True,
        "convergence_verifier_present": convergence_verifier_present,
        "authoritative_receipts_allowed": False,
        "cutover_ready": not blocked_steps and bool(legacy_callback_debt),
        "step_count": len(legacy_callback_debt),
        "blocked_step_count": len(blocked_steps),
        "cutover_ready_step_count": len(legacy_callback_debt) - len(blocked_steps),
        "native_receipt_present_step_count": len(native_receipt_step_ids),
        "callback_projection_present_step_count": len(callback_projection_step_ids),
        "callback_only_step_count": len(remaining_callback_only_step_ids),
        "native_receipt_step_ids": native_receipt_step_ids,
        "callback_projection_step_ids": callback_projection_step_ids,
        "remaining_callback_only_step_ids": remaining_callback_only_step_ids,
        "cutover_blockers_by_step": {
            str(item.get("step_id") or ""): list(item.get("cutover_blockers") or ()) for item in legacy_callback_debt
        },
        "blockers": blockers,
        "legacy_callback_debt": legacy_callback_debt,
    }


def _materialization_step_evidence_statuses(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    tool_results: list[dict[str, Any]],
) -> dict[str, str]:
    tool_results_by_step = _tool_results_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    return {
        step.step_id: _aggregate_evidence_status(
            _tool_result_evidence_status(item) for item in tool_results_by_step.get(step.step_id, [])
        )
        for step in ordered_steps
    }


def _summarize_materialization_schedule_steps(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    tool_results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    tool_results_by_step = _tool_results_by_materialization_step(
        ordered_steps=ordered_steps,
        tool_results=tool_results,
    )
    return {
        step.step_id: _summarize_step_results(step, tool_results_by_step.get(step.step_id, []))
        for step in ordered_steps
    }


def _project_legacy_callback_debt_for_step(
    step: DirectorRepairMaterializationQualityStepV1,
    tool_results: list[dict[str, Any]],
    *,
    callback_projections: list[dict[str, Any]],
    native_receipts: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    convergence_verifier_present: bool = False,
) -> dict[str, Any]:
    actual_source_tools = _ordered_unique(_source_tools(tool_results))
    runtime_executable_source_tools = [
        source_tool
        for source_tool in actual_source_tools
        if _repair_source_tool_status(source_tool, catalog) == "executable_runtime"
    ]
    legacy_only_source_tools = [
        source_tool for source_tool in actual_source_tools if source_tool not in runtime_executable_source_tools
    ]
    native_receipt_count = len(native_receipts)
    callback_projection_count = len(callback_projections)
    native_receipt_present = native_receipt_count > 0
    callback_projection_present = callback_projection_count > 0
    callback_only = callback_projection_present and not native_receipt_present
    native_verifier_evidence_present = _has_verifier_evidence_in_mappings(native_receipts)
    callback_verifier_evidence_present = any(
        _callback_projection_has_verifier_evidence(projection) for projection in callback_projections
    )
    verifier_evidence_required = True
    verifier_evidence_present = (
        _has_native_verifier_evidence(tool_results)
        or native_verifier_evidence_present
        or callback_verifier_evidence_present
    )
    evidence_status = _aggregate_evidence_status(_tool_result_evidence_status(item) for item in tool_results)
    convergence_path_available = bool(runtime_executable_source_tools)
    blockers = ["legacy_callback_runner", "independent_shadow_required"]
    if _repair_source_tool_status(step.source_tool, catalog) != "executable_runtime":
        blockers.append("declared_source_tool_not_runtime_executable")
    if not convergence_path_available:
        blockers.append("missing_native_convergence_path")
    if legacy_only_source_tools:
        blockers.append("legacy_only_source_tools")
    if callback_only:
        blockers.append("callback_projection_only")
    if (tool_results or callback_projection_present) and not native_receipt_present:
        blockers.append("missing_native_repair_receipt")
    if verifier_evidence_required and not verifier_evidence_present:
        blockers.append("missing_revalidation_evidence")
    cutover_blockers = _ordered_unique(blockers)
    return {
        "step_id": step.step_id,
        "language": step.language,
        "phase": step.phase,
        "priority": step.priority,
        "depends_on": list(step.depends_on),
        "runtime_step_id": step.step_id,
        "declared_source_tool": step.source_tool,
        "actual_source_tools": actual_source_tools,
        "runtime_executable_source_tools": runtime_executable_source_tools,
        "legacy_only_source_tools": legacy_only_source_tools,
        "native_receipt_present": native_receipt_present,
        "native_repair_kernel_receipt_count": native_receipt_count,
        "callback_projection_present": callback_projection_present,
        "callback_receipt_projection_count": callback_projection_count,
        "callback_only": callback_only,
        "projection_only": callback_only,
        "authoritative_receipts_allowed": False,
        "write_tool_evidence": has_successful_write_tool(tool_results),
        "convergence_path_available": convergence_path_available,
        "convergence_verifier_present": convergence_verifier_present,
        "verifier_evidence_required": verifier_evidence_required,
        "verifier_evidence_present": verifier_evidence_present,
        "native_verifier_evidence_present": native_verifier_evidence_present,
        "callback_verifier_evidence_present": callback_verifier_evidence_present,
        "evidence_status": evidence_status,
        "cutover_ready": False,
        "cutover_blockers": cutover_blockers,
        "blockers": cutover_blockers,
    }


def _repair_strategy_catalog_by_source_tool() -> dict[str, dict[str, Any]]:
    result = query_director_repair_strategy_catalog(
        QueryDirectorRepairStrategyCatalogV1(include_items=True, max_items=1000)
    )
    catalog = {str(item.get("source_tool") or ""): dict(item) for item in result.items}
    for binding in dict(result.summary).get("executable_runtime_bindings") or ():
        if not isinstance(binding, dict):
            continue
        source_tool = str(binding.get("source_tool") or "").strip()
        if not source_tool:
            continue
        profile = catalog.setdefault(source_tool, {})
        profile.setdefault("source_tool", source_tool)
        profile.setdefault("language", binding.get("language"))
        profile.setdefault("rule_id", binding.get("rule_id"))
        profile["implementation_status"] = "executable_runtime"
        profile["execution_owner"] = "director.runtime"
        profile["bench_driven_migration_required"] = False
    return catalog


def _tool_results_by_materialization_step(
    *,
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...],
    tool_results: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_step: dict[str, list[dict[str, Any]]] = {step.step_id: [] for step in ordered_steps}
    untagged: list[dict[str, Any]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        payload = result if isinstance(result, dict) else {}
        step_id = str(payload.get("bridge_step_id") or "").strip()
        if step_id in by_step:
            by_step[step_id].append(item)
        else:
            untagged.append(item)
    if len(ordered_steps) == 1 and untagged:
        by_step[ordered_steps[0].step_id].extend(untagged)
    return by_step


def _repair_source_tool_status(source_tool: str, catalog: dict[str, dict[str, Any]]) -> str:
    profile = catalog.get(str(source_tool or "").strip())
    if not profile:
        return "unregistered"
    return str(profile.get("implementation_status") or "").strip() or "unknown"


def _has_native_verifier_evidence(tool_results: list[dict[str, Any]]) -> bool:
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        payload = result if isinstance(result, dict) else {}
        if _mapping_has_verifier_evidence(payload.get("revalidation_evidence")):
            return True
        if _mapping_has_verifier_evidence(payload.get("revalidation")):
            return True
        repair_kernel = payload.get("repair_kernel")
        if isinstance(repair_kernel, dict) and _mapping_has_verifier_evidence(
            repair_kernel.get("revalidation_evidence")
        ):
            return True
        if isinstance(repair_kernel, dict):
            receipts = repair_kernel.get("receipts")
            if isinstance(receipts, list):
                for receipt in receipts:
                    if isinstance(receipt, dict) and _mapping_has_verifier_evidence(
                        receipt.get("revalidation_evidence")
                    ):
                        return True
    return False


def _has_verifier_evidence_in_mappings(payloads: list[dict[str, Any]]) -> bool:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if _mapping_has_verifier_evidence(payload.get("revalidation_evidence")):
            return True
        if _mapping_has_verifier_evidence(payload.get("revalidation")):
            return True
    return False


def _mapping_has_verifier_evidence(payload: Any) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    evidence_keys = {
        "command",
        "verifier_command",
        "exit_code",
        "raw_output_ref",
        "residual_diagnostics",
        "diagnostics_after",
        "errors_after",
        "net_error_reduction",
    }
    return any(key in payload for key in evidence_keys)


def _ordered_unique(values: Any) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


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
    evidence_statuses = [_tool_result_evidence_status(item) for item in tool_results]
    return {
        "step_id": step.step_id,
        "runtime_step_id": step.step_id,
        "language": step.language,
        "phase": step.phase,
        "priority": step.priority,
        "depends_on": list(step.depends_on),
        "source_tool": step.source_tool,
        "result_count": len(tool_results),
        "write_tool_evidence": has_successful_write_tool(tool_results),
        "evidence_status": _aggregate_evidence_status(evidence_statuses),
        "evidence_status_counts": _count_values(evidence_statuses),
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
