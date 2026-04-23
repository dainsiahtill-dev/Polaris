"""Completion lock state management module for Polaris engine.

This module handles task completion state persistence and deduplication,
preventing re-execution of already completed tasks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.pm.engine.helpers import (
    _collect_completed_task_ids,
    _env_positive_int,
    _now_timestamp,
    _task_dependency_ids,
    _task_identity_key,
)
from polaris.delivery.cli.pm.utils import normalize_path_list, read_json_file
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def _completion_lock_state_path(workspace_full: str) -> str:
    """Resolve completion lock state file path."""
    return resolve_artifact_path(
        workspace_full,
        "",
        "runtime/state/director.completed_tasks.lock.json",
    )


def _load_completion_lock_state(path: str) -> dict[str, Any]:
    """Load completion lock state from file."""
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        payload = {}
    fingerprints = payload.get("fingerprints")
    keys = payload.get("keys")
    touch_counts = payload.get("touch_counts")
    return {
        "fingerprints": {str(item).strip() for item in (fingerprints or []) if str(item).strip()},
        "keys": {str(item).strip() for item in (keys or []) if str(item).strip()},
        "touch_counts": {
            str(k).strip(): int(v)
            for k, v in (touch_counts or {}).items()
            if str(k).strip() and isinstance(v, (int, float))
        },
    }


def _save_completion_lock_state(path: str, state: dict[str, Any]) -> None:
    """Save completion lock state to file."""
    payload = {
        "schema_version": 1,
        "updated_at": _now_timestamp(),
        "fingerprints": sorted(str(item).strip() for item in (state.get("fingerprints") or set()) if str(item).strip()),
        "keys": sorted(str(item).strip() for item in (state.get("keys") or set()) if str(item).strip()),
        "touch_counts": {str(k): int(v) for k, v in (state.get("touch_counts") or {}).items() if str(k).strip()},
    }
    write_json_atomic(path, payload)


def _select_dependency_closed_tasks(
    candidates: Sequence[dict[str, Any]],
    *,
    max_tasks: int,
    satisfied_task_ids: set[str],
) -> tuple[list[dict[str, Any]], int, int]:
    """Select tasks whose dependencies are satisfied."""
    pending: list[dict[str, Any]] = [item for item in candidates if isinstance(item, dict)]
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    while pending and len(selected) < max_tasks:
        progressed = False
        for task in list(pending):
            deps = _task_dependency_ids(task)
            if all(dep in satisfied_task_ids or dep in selected_ids for dep in deps if dep):
                pending.remove(task)
                selected.append(task)
                task_id = str(task.get("id") or "").strip()
                if task_id:
                    selected_ids.add(task_id)
                progressed = True
                if len(selected) >= max_tasks:
                    break
        if not progressed:
            break

    budget_limited = 0
    dependency_blocked = 0
    if pending:
        if len(selected) >= max_tasks:
            budget_limited = len(pending)
        else:
            dependency_blocked = len(pending)
    return selected, int(budget_limited), int(dependency_blocked)


def _apply_task_stability_filters(
    tasks: Sequence[dict[str, Any]],
    *,
    pm_payload: dict[str, Any],
    completion_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply stability filters to prevent re-execution and over-touching."""
    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_fingerprints: set[str] = set()
    locked = 0
    deduped = 0
    budget_skipped = 0
    dependency_blocked = 0

    completed_keys = completion_state.get("keys") if isinstance(completion_state, dict) else set()
    completed_fps = completion_state.get("fingerprints") if isinstance(completion_state, dict) else set()
    if not isinstance(completed_keys, set):
        completed_keys = set()
    if not isinstance(completed_fps, set):
        completed_fps = set()

    max_tasks = _env_positive_int("KERNELONE_ENGINE_MAX_TASKS_PER_ITERATION", 3)
    max_touches_per_file = _env_positive_int("KERNELONE_ENGINE_MAX_TOUCHES_PER_FILE", 3)
    touch_counts = completion_state.get("touch_counts")
    if not isinstance(touch_counts, dict):
        touch_counts = {}
        completion_state["touch_counts"] = touch_counts

    for task in tasks:
        if not isinstance(task, dict):
            continue
        fingerprint = str(task.get("fingerprint") or "").strip()
        key = _task_identity_key(task)
        if fingerprint and fingerprint in completed_fps:
            task["status"] = "done"
            task["completion_lock_reason"] = "fingerprint_already_completed"
            locked += 1
            continue
        if key and key in completed_keys:
            task["status"] = "done"
            task["completion_lock_reason"] = "task_identity_already_completed"
            locked += 1
            continue
        if fingerprint and fingerprint in seen_fingerprints:
            deduped += 1
            continue
        if key and key in seen_keys:
            deduped += 1
            continue

        target_files = normalize_path_list(task.get("target_files") or [])
        over_budget = False
        for rel in target_files:
            norm = str(rel or "").replace("\\", "/").strip()
            if not norm:
                continue
            if int(touch_counts.get(norm, 0) or 0) >= max_touches_per_file:
                over_budget = True
                break
        if over_budget:
            budget_skipped += 1
            continue

        candidates.append(task)
        if fingerprint:
            seen_fingerprints.add(fingerprint)
        if key:
            seen_keys.add(key)

    completed_task_ids = _collect_completed_task_ids(pm_payload, completion_state)
    selected, budget_overflow, dependency_unready = _select_dependency_closed_tasks(
        candidates,
        max_tasks=max_tasks,
        satisfied_task_ids=completed_task_ids,
    )
    budget_skipped += int(budget_overflow)
    dependency_blocked += int(dependency_unready)

    # keep PM payload task list sorted by status updates for downstream persistence
    pm_tasks = pm_payload.get("tasks")
    if isinstance(pm_tasks, list):
        for task in pm_tasks:
            if not isinstance(task, dict):
                continue
            if str(task.get("status") or "").strip().lower() == "done":
                task.setdefault("completion_lock", True)

    return selected, {
        "locked": int(locked),
        "deduped": int(deduped),
        "budget_limited": int(budget_skipped),
        "dependency_blocked": int(dependency_blocked),
        "selected": len(selected),
    }


def _update_completion_lock_state(
    state: dict[str, Any],
    records: Sequence[dict[str, Any]],
) -> None:
    """Update completion lock state from task records."""
    if not isinstance(state, dict):
        return
    fingerprints = state.get("fingerprints")
    keys = state.get("keys")
    touch_counts = state.get("touch_counts")
    if not isinstance(fingerprints, set):
        fingerprints = set()
        state["fingerprints"] = fingerprints
    if not isinstance(keys, set):
        keys = set()
        state["keys"] = keys
    if not isinstance(touch_counts, dict):
        touch_counts = {}
        state["touch_counts"] = touch_counts

    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("pm_status") or "").strip().lower() != "done":
            continue
        payload = record.get("result_payload")
        if not isinstance(payload, dict):
            continue
        fingerprint = str(payload.get("task_fingerprint") or "").strip()
        if fingerprint:
            fingerprints.add(fingerprint)
        task_id = str(record.get("task_id") or "").strip()
        if task_id:
            keys.add(f"id:{task_id}")
        changed_files = normalize_path_list(payload.get("changed_files") or [])
        for rel in changed_files:
            norm = str(rel or "").replace("\\", "/").strip()
            if not norm:
                continue
            touch_counts[norm] = int(touch_counts.get(norm, 0) or 0) + 1


__all__ = [
    "_apply_task_stability_filters",
    "_completion_lock_state_path",
    "_load_completion_lock_state",
    "_save_completion_lock_state",
    "_select_dependency_closed_tasks",
    "_update_completion_lock_state",
]
