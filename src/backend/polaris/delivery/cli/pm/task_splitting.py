"""Task splitting and merging helpers for loop-pm."""

import hashlib
import json
from typing import Any

from polaris.delivery.cli.pm.task_helpers import _auto_assign_role, normalize_assigned_to
from polaris.delivery.cli.pm.utils import _is_docs_path, normalize_path_list


def split_director_tasks(
    tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split tasks into director, docs-only, and non-director tasks."""
    director_tasks: list[dict[str, Any]] = []
    docs_only_tasks: list[dict[str, Any]] = []
    non_director_tasks: list[dict[str, Any]] = []

    for task in tasks or []:
        if not isinstance(task, dict):
            continue

        task_copy = dict(task)
        raw_assignee = str(task.get("assigned_to") or "").strip()

        if not raw_assignee or raw_assignee.lower() == "auto":
            task_copy["assigned_to"] = _auto_assign_role(task)
        else:
            task_copy["assigned_to"] = normalize_assigned_to(task.get("assigned_to"))

        if task_copy["assigned_to"] != "Director":
            non_director_tasks.append(task_copy)
            continue

        target_files = task_copy.get("target_files") or []
        scope_paths = task_copy.get("scope_paths") or []

        normalized_targets = normalize_path_list(target_files)
        normalized_scope_paths = normalize_path_list(scope_paths)

        if normalized_scope_paths and all(_is_docs_path(path) for path in normalized_scope_paths):
            docs_only_tasks.append(task_copy)
            continue

        if normalized_targets and all(_is_docs_path(path) for path in normalized_targets):
            docs_only_tasks.append(task_copy)
            continue

        docs_in_scope = any(_is_docs_path(path) for path in normalized_scope_paths)
        docs_in_targets = any(_is_docs_path(path) for path in normalized_targets)
        if docs_in_scope or docs_in_targets:
            filtered_scope_paths = [path for path in normalized_scope_paths if not _is_docs_path(path)]
            filtered_targets = [path for path in normalized_targets if not _is_docs_path(path)]
            if not filtered_scope_paths and not filtered_targets:
                docs_only_tasks.append(task_copy)
                continue
            task_copy["scope_paths"] = filtered_scope_paths
            task_copy["target_files"] = filtered_targets
            constraints_raw = task_copy.get("constraints")
            constraints: list[Any] = constraints_raw if isinstance(constraints_raw, list) else []
            task_copy["constraints"] = [*constraints, "Do not modify docs/ (PM-only)."]
            director_tasks.append(task_copy)
            continue

        director_tasks.append(task_copy)

    return director_tasks, docs_only_tasks, non_director_tasks


def merge_director_tasks(
    generated_tasks: list[dict[str, Any]],
    existing_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge generated tasks with existing tasks, deduplicating."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for task in [*(generated_tasks or []), *(existing_tasks or [])]:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        dedupe_key = (
            task_id or hashlib.sha1(json.dumps(task, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        merged.append(task)

    return merged


def persist_pm_payloads(
    *,
    normalized: dict[str, Any],
    director_payload: dict[str, Any],
    pm_out_full: str,
    run_pm_tasks: str,
) -> None:
    """Persist PM payloads to files."""
    from polaris.infrastructure.compat.io_utils import write_json_atomic

    write_json_atomic(pm_out_full, normalized)
    write_json_atomic(run_pm_tasks, director_payload)


__all__ = [
    "merge_director_tasks",
    "persist_pm_payloads",
    "split_director_tasks",
]
