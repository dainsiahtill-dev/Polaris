"""Materialization-quality deterministic repair bridge for Director adapter.

This module is the migration-time boundary between the legacy materialization
quality repair host and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.director.runtime.public.service import (
    ProjectDirectorRepairKernelSummaryV1,
    project_director_repair_kernel_summary,
)


def run_materialization_quality_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run materialization-quality repairs through the migration bridge."""

    from .deterministic_repairs.generic_repairs import (
        _apply_deterministic_materialization_quality_repairs,
    )

    tool_results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
    )
    bridged_summary = _annotate_materialization_quality_summary(
        summary,
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
    )
    return tool_results, bridged_summary


def _annotate_materialization_quality_summary(
    summary: dict[str, Any] | None,
    *,
    tool_results: list[dict[str, Any]],
    artifact_quality_errors: list[str],
) -> dict[str, Any]:
    bridged_summary = dict(summary or {})
    repair_kernel = dict(
        project_director_repair_kernel_summary(
            ProjectDirectorRepairKernelSummaryV1(
                stage="materialization_quality_repairs",
                tool_results=tuple(tool_results),
                artifact_quality_errors=tuple(artifact_quality_errors),
                mode="commit",
            )
        ).summary
    )
    bridged_summary["repair_kernel"] = repair_kernel
    bridged_summary["materialization_quality_bridge"] = {
        "schema_version": "director.materialization_quality_repair_bridge.v1",
        "mode": "legacy_strategy_host_wrapper",
        "bridge_file": "roles.adapters.internal.director.materialization_quality_repair_bridge",
        "legacy_strategy_host": "roles.adapters.internal.director.deterministic_repairs.generic_repairs",
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
        "receipt_count": repair_kernel.get("receipt_count", 0),
        "coverage_uncovered_diagnostic_count": dict(repair_kernel.get("coverage_report") or {}).get(
            "uncovered_diagnostic_count",
            0,
        ),
    }
    return bridged_summary


__all__ = ["run_materialization_quality_repairs"]
