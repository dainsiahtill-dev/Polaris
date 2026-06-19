"""PM workflow timeout-floor math (extracted from orchestration_engine).

Pure helpers that derive a safe PM workflow wait floor from the Director
fan-out configuration. ``--director-result-timeout`` is a UI/process wait
budget, not a safe upper bound for the workflow handler itself, so these
functions scale the runtime budget from the actual contract and Director
phase limits.

These functions are lossless extractions: bodies are byte-for-byte identical
to the original ``orchestration_engine`` definitions and are re-exported from
that module to preserve the canonical import path.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

_DIRECTOR_WORKFLOW_TIMEOUT_MARGIN_SECONDS = 300


def _positive_timeout_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def _director_workflow_timeout_floor_seconds(
    director_config: dict[str, Any],
    *,
    task_count: int,
) -> float:
    """Return a PM workflow wait floor large enough for Director fan-out.

    ``--director-result-timeout`` is a UI/process wait budget, not a safe upper
    bound for the workflow handler itself. Real Codex-backed Director tasks can
    exceed a small UI timeout when PM emits dependent tasks, so the runtime
    budget must scale from the actual contract and Director phase limits.
    """
    normalized_task_count = max(1, int(task_count or 0))
    mode = str(director_config.get("execution_mode") or "parallel").strip().lower()
    if mode in {"serial", "sequential"}:
        parallel_limit = 1
    else:
        parallel_limit = _positive_timeout_int(director_config.get("max_parallel_tasks"), 3)

    dependency_depth = normalized_task_count
    estimated_batches = max(1, (normalized_task_count + parallel_limit - 1) // parallel_limit)
    batch_count = max(estimated_batches, dependency_depth if normalized_task_count > 1 else 1)

    ready_seconds = _positive_timeout_int(director_config.get("ready_timeout_seconds"), 30)
    claim_seconds = _positive_timeout_int(director_config.get("claim_timeout_seconds"), 30)
    phase_seconds = _positive_timeout_int(director_config.get("phase_timeout_seconds"), 900)
    complete_seconds = _positive_timeout_int(director_config.get("complete_timeout_seconds"), 30)
    task_seconds = _positive_timeout_int(director_config.get("task_timeout_seconds"), MAX_WORKFLOW_TIMEOUT_SECONDS)
    per_task_budget = min(
        task_seconds,
        claim_seconds + (5 * phase_seconds) + complete_seconds,
    )

    computed = ready_seconds + (batch_count * per_task_budget) + _DIRECTOR_WORKFLOW_TIMEOUT_MARGIN_SECONDS
    return float(min(MAX_WORKFLOW_TIMEOUT_SECONDS, computed))


def _pm_workflow_wait_timeout_seconds(
    requested_timeout_seconds: float | None,
    director_config: dict[str, Any],
    *,
    task_count: int,
) -> float | None:
    if requested_timeout_seconds is None:
        return None
    return max(
        float(requested_timeout_seconds),
        _director_workflow_timeout_floor_seconds(director_config, task_count=task_count),
    )
