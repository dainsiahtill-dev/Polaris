"""Shared behavioral fixtures retained across TaskRuntime service-test splits."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.runtime.task_runtime.internal.execution_session import TaskExecutionSession


def _assert_task_row_read_model_fallback_coverage(
    coverage: dict[str, Any],
    *,
    coverage_ratio: float,
    transitional_file_fallback_required: bool,
    file_row_ids_without_execution_fact: list[str],
    fact_row_ids_without_file_row: list[str],
) -> None:
    assert coverage["coverage_ratio"] == pytest.approx(coverage_ratio)
    assert coverage["transitional_file_fallback_required"] is transitional_file_fallback_required
    assert coverage["file_row_ids_without_execution_fact"] == file_row_ids_without_execution_fact
    assert coverage["fact_row_ids_without_file_row"] == fact_row_ids_without_file_row


def _assert_task_row_read_model_projection_parity_coverage(
    coverage: dict[str, Any],
    *,
    parity_ratio: float,
    observable_projection_parity_ready: bool,
    transitional_only_row_ids: list[str],
    fact_only_row_ids: list[str],
    row_ids_with_projection_mismatch: list[str],
) -> None:
    assert coverage["parity_ratio"] == pytest.approx(parity_ratio)
    assert coverage["observable_projection_parity_ready"] is observable_projection_parity_ready
    assert coverage["transitional_only_row_ids"] == transitional_only_row_ids
    assert coverage["fact_only_row_ids"] == fact_only_row_ids
    assert coverage["row_ids_with_projection_mismatch"] == row_ids_with_projection_mismatch


def _runtime_execution_projected_row(task_id: int, *, subject: str | None = None) -> dict[str, Any]:
    session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id=f"run-projected-runtime-execution-{task_id}",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    return {
        "id": task_id,
        "task_id": str(task_id),
        "subject": subject or f"projected runtime execution {task_id}",
        "metadata": {"runtime_execution": session.to_dict()},
    }


def _assert_projected_runtime_execution_session_fallback_coverage(
    coverage: dict[str, Any],
    *,
    file_projected_session_rows_count: int,
    fact_projected_session_rows_count: int,
    coverage_ratio: float,
    projected_session_file_fallback_required: bool,
    file_projected_session_task_ids_without_execution_fact: list[str],
    fact_projected_session_task_ids_without_file_row: list[str],
) -> None:
    assert coverage["file_projected_session_rows_count"] == file_projected_session_rows_count
    assert coverage["fact_projected_session_rows_count"] == fact_projected_session_rows_count
    assert coverage["coverage_ratio"] == pytest.approx(coverage_ratio)
    assert coverage["projected_session_file_fallback_required"] is projected_session_file_fallback_required
    assert (
        coverage["file_projected_session_task_ids_without_execution_fact"]
        == file_projected_session_task_ids_without_execution_fact
    )
    assert (
        coverage["fact_projected_session_task_ids_without_file_row"] == fact_projected_session_task_ids_without_file_row
    )


def _assert_task_row_read_model_cutover_readiness(
    readiness: dict[str, Any],
    *,
    ready: bool,
    blocking_reasons: list[str],
) -> None:
    assert readiness["ready"] is ready
    assert readiness["blocking_reasons"] == blocking_reasons


def _projected_session_file_fallback_readiness(*, required: bool) -> dict[str, Any]:
    return {
        "ready": not required,
        "blocking_reasons": ["projected_session_file_fallback_required"] if required else [],
        "task_row_file_fallback_required": False,
        "projected_session_file_fallback_required": required,
        "observable_projection_parity_ready": True,
    }
