"""Materialization-quality runtime port facade for the Director adapter.

Production callers import this module only. Callback execution lives in
``materialization_quality_callback_ports``; read-only summary and receipt
projection lives in ``materialization_quality_evidence_ports``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from polaris.cells.director.runtime.public.service import DirectorRepairMaterializationQualityStepV1

from . import (
    materialization_quality_callback_ports as callback_ports,
    materialization_quality_evidence_ports as evidence_ports,
)

has_materialization_quality_runtime_repair_coverage = (
    callback_ports.has_materialization_quality_runtime_repair_coverage
)
project_materialization_quality_facade_summary = evidence_ports.project_materialization_quality_facade_summary


def build_materialization_quality_step_runner(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None,
    task_id: str,
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[Any] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> Callable[[DirectorRepairMaterializationQualityStepV1], list[dict[str, Any]]]:
    """Return the adapter-owned materialization step runner port."""

    task_payload = dict(task or {})
    quality_errors = [str(item) for item in artifact_quality_errors]
    advisory_notes = tuple(advisor_notes or ())

    def _run_step(step: DirectorRepairMaterializationQualityStepV1) -> list[dict[str, Any]]:
        return callback_ports._run_materialization_quality_repair_step(
            step.step_id,
            adapter,
            task=task_payload,
            task_id=task_id,
            artifact_quality_errors=quality_errors,
            advisor_notes=advisory_notes,
            convergence_verifier=convergence_verifier,
        )

    return _run_step


def project_materialization_quality_plan_probe_preaudit(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: Sequence[str],
    artifact_quality_issues: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Project materialization plan-probe evidence for the runtime facade."""

    return callback_ports._project_materialization_plan_probe_preaudit(
        adapter,
        task=task,
        artifact_quality_errors=[str(item) for item in artifact_quality_errors],
        artifact_quality_issues=[dict(item) for item in artifact_quality_issues],
        coverage_preaudit={},
    )


__all__ = [
    "build_materialization_quality_step_runner",
    "has_materialization_quality_runtime_repair_coverage",
    "project_materialization_quality_facade_summary",
    "project_materialization_quality_plan_probe_preaudit",
]
