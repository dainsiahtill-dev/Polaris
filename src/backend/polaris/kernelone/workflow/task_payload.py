"""Shared task-payload accessors for Director/PM workflows.

Canonical, behavior-preserving home for the byte-identical helpers that read
structured data out of a workflow task's ``payload`` mapping, previously
duplicated across the ``workflow_runtime`` and ``workflow_activity`` cells.

Extracted 2026-06-10 as the second step of the workflow consolidation (see
``docs/governance/audits/WORKFLOW_ACTIVITY_DUPLICATE_FINDING_20260609.md``).
``_task_dependencies`` previously diverged across the two cells only in a local
variable name (``payload`` vs ``task_payload``) — behaviorally identical for all
inputs — so unifying it is risk-free.  Both helpers use ``getattr`` for the
``payload`` lookup so they are safe for any task-like object.
"""

from __future__ import annotations

from typing import Any


def _task_payload_list(task: Any, key: str) -> list[str]:
    payload = task.payload if isinstance(getattr(task, "payload", None), dict) else {}
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _task_dependencies(task: Any) -> set[str]:
    payload = task.payload if isinstance(getattr(task, "payload", None), dict) else {}
    raw_dependencies: list[Any] = []
    if isinstance(payload.get("depends_on"), list):
        raw_dependencies.extend(payload.get("depends_on") or [])
    if isinstance(payload.get("dependencies"), list):
        raw_dependencies.extend(payload.get("dependencies") or [])
    if isinstance(payload.get("blocked_by"), list):
        raw_dependencies.extend(payload.get("blocked_by") or [])
    return {str(item).strip() for item in raw_dependencies if str(item).strip()}


__all__ = ["_task_dependencies", "_task_payload_list"]
