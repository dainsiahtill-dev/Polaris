"""Materialization-quality runtime port facade for the Director adapter.

Production callers import this module only. Callback execution lives in
``materialization_quality_callback_ports``; read-only summary and receipt
projection lives in ``materialization_quality_evidence_ports``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from polaris.cells.director.runtime.public.service import DirectorRepairMaterializationQualityStepV1

from .materialization_quality_callback_ports import (
    _collect_materialization_go_base_files,
    _collect_materialization_hygiene_base_files,
    _collect_materialization_node_manifest_base_files,
    _collect_materialization_python_base_files,
    _collect_materialization_runtime_base_files,
    _collect_materialization_rust_base_files,
    _collect_materialization_target_runtime_base_files,
    _coverage_has_materialization_runtime_source_tool,
    _materialization_allowed_paths_from_runtime_public_plan,
    _materialization_plannable_runtime_source_tools,
    _materialization_plannable_runtime_source_tools_from_base_files,
    _materialization_runtime_coverage_source_tools,
    _materialization_runtime_source_tools_for_step,
    _project_coverage_preaudit,
    _project_materialization_plan_probe_preaudit,
    _run_materialization_go_import,
    _run_materialization_go_import_repairs,
    _run_materialization_html_entrypoint,
    _run_materialization_hygiene_scaffold,
    _run_materialization_node_manifest,
    _run_materialization_python_import,
    _run_materialization_quality_repair_step,
    _run_materialization_rust_compiler,
    _run_materialization_rust_runtime_repair,
    _run_materialization_target_runtime,
    _run_materialization_typescript_compiler,
    _run_materialization_typescript_runtime_repair,
    _run_materialization_typescript_scaffold,
    has_materialization_quality_runtime_repair_coverage,
)
from .materialization_quality_evidence_ports import (
    _annotate_materialization_quality_summary,
    _collect_materialization_scheduler_bridge_evidence,
    _materialization_callback_receipt_projection_to_dict,
    _materialization_callback_receipt_projections_from_payloads,
    _materialization_callback_receipt_projections_from_schedule_result,
    _materialization_receipt_lifecycle_by_step,
    _project_materialization_quality_migration_debt,
    _selected_materialization_callback_receipt_projections,
    project_materialization_quality_facade_summary,
)


def build_materialization_quality_step_runner(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None,
    task_id: str,
    artifact_quality_errors: Sequence[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> Callable[[DirectorRepairMaterializationQualityStepV1], list[dict[str, Any]]]:
    """Return the adapter-owned materialization step runner port."""

    task_payload = dict(task or {})
    quality_errors = [str(item) for item in artifact_quality_errors]

    def _run_step(step: DirectorRepairMaterializationQualityStepV1) -> list[dict[str, Any]]:
        return _run_materialization_quality_repair_step(
            step.step_id,
            adapter,
            task=task_payload,
            task_id=task_id,
            artifact_quality_errors=quality_errors,
            convergence_verifier=convergence_verifier,
        )

    return _run_step


def project_materialization_quality_plan_probe_preaudit(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: Sequence[str],
) -> dict[str, Any]:
    """Project materialization plan-probe evidence for the runtime facade."""

    return _project_materialization_plan_probe_preaudit(
        adapter,
        task=task,
        artifact_quality_errors=[str(item) for item in artifact_quality_errors],
        coverage_preaudit={},
    )


__all__ = [
    "_annotate_materialization_quality_summary",
    "_collect_materialization_go_base_files",
    "_collect_materialization_hygiene_base_files",
    "_collect_materialization_node_manifest_base_files",
    "_collect_materialization_python_base_files",
    "_collect_materialization_runtime_base_files",
    "_collect_materialization_rust_base_files",
    "_collect_materialization_scheduler_bridge_evidence",
    "_collect_materialization_target_runtime_base_files",
    "_coverage_has_materialization_runtime_source_tool",
    "_materialization_allowed_paths_from_runtime_public_plan",
    "_materialization_callback_receipt_projection_to_dict",
    "_materialization_callback_receipt_projections_from_payloads",
    "_materialization_callback_receipt_projections_from_schedule_result",
    "_materialization_plannable_runtime_source_tools",
    "_materialization_plannable_runtime_source_tools_from_base_files",
    "_materialization_receipt_lifecycle_by_step",
    "_materialization_runtime_coverage_source_tools",
    "_materialization_runtime_source_tools_for_step",
    "_project_coverage_preaudit",
    "_project_materialization_plan_probe_preaudit",
    "_project_materialization_quality_migration_debt",
    "_run_materialization_go_import",
    "_run_materialization_go_import_repairs",
    "_run_materialization_html_entrypoint",
    "_run_materialization_hygiene_scaffold",
    "_run_materialization_node_manifest",
    "_run_materialization_python_import",
    "_run_materialization_quality_repair_step",
    "_run_materialization_rust_compiler",
    "_run_materialization_rust_runtime_repair",
    "_run_materialization_target_runtime",
    "_run_materialization_typescript_compiler",
    "_run_materialization_typescript_runtime_repair",
    "_run_materialization_typescript_scaffold",
    "_selected_materialization_callback_receipt_projections",
    "build_materialization_quality_step_runner",
    "has_materialization_quality_runtime_repair_coverage",
    "project_materialization_quality_facade_summary",
    "project_materialization_quality_plan_probe_preaudit",
]
