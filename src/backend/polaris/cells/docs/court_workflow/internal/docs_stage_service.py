"""Docs Stage Service Module.

This module handles docs-stage orchestration and related utilities.
Designed to be testable with minimal dependencies.
"""

from __future__ import annotations

import json
import os
from typing import Any

from polaris.kernelone.storage.io_paths import resolve_artifact_path

_ARCHITECT_DOCS_PIPELINE_REL = "runtime/contracts/architect.docs.pipeline.json"


def resolve_docs_stage_context(
    *,
    workspace_full: str,
    cache_root_full: str,
    iteration: int,
    last_tasks: Any,
    requirements: str,
    plan_text: str,
) -> tuple[str, str, dict[str, Any]]:
    """Resolve docs stage context from pipeline.

    Args:
        workspace_full: Workspace path
        cache_root_full: Cache root path
        iteration: Current iteration
        last_tasks: Previous tasks for resume
        requirements: Requirements text
        plan_text: Plan text

    Returns:
        Tuple of (requirements, plan_text, docs_stage_dict)
    """
    mode = _resolve_pm_doc_stage_mode()
    if mode == "off":
        return requirements, plan_text, {"enabled": False, "mode": mode}

    pipeline_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        _ARCHITECT_DOCS_PIPELINE_REL,
    )
    pipeline_payload = _read_json_file(pipeline_full)
    if not isinstance(pipeline_payload, dict):
        return requirements, plan_text, {"enabled": False, "mode": mode}

    raw_stages = pipeline_payload.get("stages")
    if not isinstance(raw_stages, list):
        return requirements, plan_text, {"enabled": False, "mode": mode}

    stages: list[dict[str, Any]] = []
    for item in raw_stages:
        if not isinstance(item, dict):
            continue
        stage_id = str(item.get("id") or "").strip()
        stage_title = str(item.get("title") or "").strip()
        doc_path = str(item.get("doc_path") or "").strip()
        if stage_id:
            stages.append(
                {
                    "id": stage_id,
                    "title": stage_title,
                    "doc_path": doc_path,
                }
            )

    if not stages:
        return requirements, plan_text, {"enabled": False, "mode": mode}

    current_stage_index = 0
    if last_tasks:
        last_task_list = last_tasks if isinstance(last_tasks, list) else []
        for task in last_task_list:
            if not isinstance(task, dict):
                continue
            stage_idx = task.get("docs_stage_index")
            if isinstance(stage_idx, int) and stage_idx >= current_stage_index:
                current_stage_index = stage_idx + 1

    if current_stage_index >= len(stages):
        return requirements, plan_text, {"enabled": False, "mode": mode, "completed": True}

    current_stage = stages[current_stage_index]
    active_doc_path = current_stage.get("doc_path", "")

    docs_stage: dict[str, Any] = {
        "enabled": True,
        "mode": mode,
        "total_stages": len(stages),
        "active_stage_index": current_stage_index,
        "active_stage_id": current_stage.get("id", ""),
        "active_stage_title": current_stage.get("title", ""),
        "active_doc_path": active_doc_path,
    }

    return requirements, plan_text, docs_stage


def get_docs_stage_for_task(
    docs_stage: dict[str, Any] | None,
    task_index: int,
) -> dict[str, Any] | None:
    """Get docs stage metadata for a specific task.

    Args:
        docs_stage: Docs stage configuration
        task_index: Task index in the list

    Returns:
        Docs stage metadata for task
    """
    if not docs_stage or not docs_stage.get("enabled"):
        return None

    return {
        "enabled": True,
        "stage_index": docs_stage.get("active_stage_index", 0),
        "stage_id": docs_stage.get("active_stage_id", ""),
        "stage_title": docs_stage.get("active_stage_title", ""),
        "doc_path": docs_stage.get("active_doc_path", ""),
        "total_stages": docs_stage.get("total_stages", 0),
    }


def annotate_tasks_with_docs_stage(
    tasks: list[dict[str, Any]],
    stage_context: dict[str, Any],
) -> None:
    """Annotate tasks with docs stage information.

    In-place mutation of tasks list.

    Args:
        tasks: List of tasks to annotate
        stage_context: Docs stage context
    """
    if not stage_context.get("enabled"):
        return

    stage_meta = {
        "docs_stage_enabled": True,
        "docs_stage_index": stage_context.get("active_stage_index", 0),
        "docs_stage_id": stage_context.get("active_stage_id", ""),
        "docs_stage_title": stage_context.get("active_stage_title", ""),
    }

    for task in tasks:
        if not isinstance(task, dict):
            continue
        if "metadata" not in task:
            task["metadata"] = {}
        if isinstance(task["metadata"], dict):
            task["metadata"].update(stage_meta)


def is_docs_stage_complete(
    docs_stage: dict[str, Any],
    completed_task_count: int,
) -> bool:
    """Check if docs stage is complete.

    Args:
        docs_stage: Docs stage configuration
        completed_task_count: Number of completed tasks

    Returns:
        True if docs stage is complete
    """
    if not docs_stage.get("enabled"):
        return True

    total = docs_stage.get("total_stages", 0)
    current = docs_stage.get("active_stage_index", 0)

    return current >= total - 1 and completed_task_count > 0


def _resolve_pm_doc_stage_mode() -> str:
    """Resolve docs stage mode from environment."""
    import os

    return str(os.environ.get("KERNELONE_PM_DOC_STAGE_MODE", "off")).strip().lower()


def _read_json_file(path: str) -> dict[str, Any] | None:
    """Read JSON file safely.

    Returns None for "file does not exist" so that callers can gracefully fall
    back to defaults.  All other errors (permissions, encoding, malformed JSON)
    are logged at ERROR level so they surface in monitoring without crashing
    the orchestration flow.
    """
    import logging

    logger = logging.getLogger(__name__)

    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse JSON from %s: %s (line %s, col %s)",
            path,
            exc.msg,
            exc.lineno,
            exc.colno,
        )
        return None
    except PermissionError as exc:
        logger.error("Permission denied reading %s: %s", path, exc)
        return None
    except OSError as exc:
        logger.error("OS error reading %s: %s", path, exc)
        return None
    except (RuntimeError, ValueError) as exc:
        logger.error("Unexpected error reading %s: %s", path, exc, exc_info=True)
        return None
