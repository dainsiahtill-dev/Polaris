"""Shared Director/PM workflow timeout policy.

Single source of truth for the timeout-scaling helpers and constants that were
previously duplicated (byte-for-byte) across the ``workflow_runtime`` and
``workflow_activity`` cells.  Living in ``kernelone`` (the shared base layer)
lets both cells reuse it without a cross-cell import.

Extracted 2026-06-10 as the first, behavior-preserving step of the
``workflow_activity`` / ``workflow_runtime`` consolidation (see
``docs/governance/audits/WORKFLOW_ACTIVITY_DUPLICATE_FINDING_20260609.md``).
The bodies are unchanged; only ``_task_run_timeout_seconds``'s parameter
annotation was relaxed from the cell-local ``TaskContract`` to ``Any``
(annotation-only — runtime behavior is identical, and the body only relies on a
``.payload`` mapping via ``_task_payload_list``).
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

_DIRECTOR_TASK_ROUND_TIMEOUT_SECONDS = 600
_DIRECTOR_TASK_TIMEOUT_MARGIN_SECONDS = 300
_DIRECTOR_TASK_TIMEOUT_MAX_ROUNDS = 12
_DIRECTOR_TASK_TIMEOUT_MAX_SECONDS = 3600


def _director_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (RuntimeError, ValueError):
        return max(1, int(default))


def _task_payload_list(task: Any, key: str) -> list[str]:
    payload = task.payload if isinstance(getattr(task, "payload", None), dict) else {}
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _director_child_workflow_timeout_seconds(
    director_config: dict[str, Any],
    *,
    task_count: int,
) -> int:
    per_task_seconds = _director_positive_int(
        director_config.get("task_timeout_seconds"),
        MAX_WORKFLOW_TIMEOUT_SECONDS,
    )
    ready_seconds = _director_positive_int(director_config.get("ready_timeout_seconds"), 30)
    budget = ready_seconds + per_task_seconds * max(1, int(task_count or 0)) + 120
    return min(max(120, budget), MAX_WORKFLOW_TIMEOUT_SECONDS)


def _task_phase_timeout_seconds(task: Any, phase: str, base_timeout_seconds: int) -> int:
    """Scale implementation phase timeout for multi-round code generation."""
    phase_name = str(phase or "").strip().lower()
    if phase_name not in {"implement", "execution"}:
        return max(1, int(base_timeout_seconds))

    target_files = _task_payload_list(task, "target_files")
    scope_paths = _task_payload_list(task, "scope_paths")
    estimated_rounds = len(target_files) if target_files else len(scope_paths)
    estimated_rounds = max(1, min(_DIRECTOR_TASK_TIMEOUT_MAX_ROUNDS, estimated_rounds))
    floor_seconds = (
        30 + (estimated_rounds * _DIRECTOR_TASK_ROUND_TIMEOUT_SECONDS) + _DIRECTOR_TASK_TIMEOUT_MARGIN_SECONDS
    )
    return min(_DIRECTOR_TASK_TIMEOUT_MAX_SECONDS, max(int(base_timeout_seconds), floor_seconds))


def _task_run_timeout_seconds(task: Any, base_timeout_seconds: int) -> int:
    """Scale child workflow timeout for multi-round Director code generation."""
    target_files = _task_payload_list(task, "target_files")
    scope_paths = _task_payload_list(task, "scope_paths")
    estimated_rounds = len(target_files) if target_files else len(scope_paths)
    estimated_rounds = max(1, min(_DIRECTOR_TASK_TIMEOUT_MAX_ROUNDS, estimated_rounds))
    floor_seconds = (
        30 + (estimated_rounds * _DIRECTOR_TASK_ROUND_TIMEOUT_SECONDS) + _DIRECTOR_TASK_TIMEOUT_MARGIN_SECONDS
    )
    return min(MAX_WORKFLOW_TIMEOUT_SECONDS, max(int(base_timeout_seconds), floor_seconds))


__all__ = [
    "MAX_WORKFLOW_TIMEOUT_SECONDS",
    "_DIRECTOR_TASK_ROUND_TIMEOUT_SECONDS",
    "_DIRECTOR_TASK_TIMEOUT_MARGIN_SECONDS",
    "_DIRECTOR_TASK_TIMEOUT_MAX_ROUNDS",
    "_DIRECTOR_TASK_TIMEOUT_MAX_SECONDS",
    "_director_child_workflow_timeout_seconds",
    "_director_positive_int",
    "_task_payload_list",
    "_task_phase_timeout_seconds",
    "_task_run_timeout_seconds",
]
