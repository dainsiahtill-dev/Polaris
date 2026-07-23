"""Shared public-boundary adapter for materialization quality repair.

This module is intentionally small and stateless.  ``execute_method`` and
``quality_gate`` both keep compatibility wrappers for existing monkeypatch and
re-export surfaces, but the roles public-boundary call itself lives here so the
runtime schedule contract cannot drift between the two legacy call sites.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from polaris.cells.roles.adapters.public.contracts import RunDirectorMaterializationQualityRepairScheduleCommandV1
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1
from polaris.kernelone.quality import artifact_quality_issues_from_errors


def run_materialization_quality_public_boundary(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute materialization-quality repair through the typed public boundary."""

    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule_result,
    )

    result = run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            artifact_quality_issues=artifact_quality_issues
            or artifact_quality_issues_from_errors(artifact_quality_errors),
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    return [dict(item) for item in result.tool_results], dict(result.summary)
