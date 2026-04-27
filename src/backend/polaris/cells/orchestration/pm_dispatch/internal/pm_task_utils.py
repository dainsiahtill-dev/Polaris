"""Pure task utility functions and ports for PM Dispatch Cell.

This module provides:
- Pure functions (no I/O, no side-effects) for task normalisation and status
  aggregation, copied verbatim from their delivery.cli.pm counterparts to
  eliminate the upward dependency.
- A ``ShangshulingPort`` Protocol that the delivery layer must implement and
  inject; the Cell itself never imports any delivery module.

Design invariant: this file MUST NOT contain any import of
``polaris.delivery.*``.  Validated by
``tests/test_pm_dispatch_no_delivery_import.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Protocol, runtime_checkable

__all__ = [
    # Constants
    "PM_SPIN_GUARD_STATUS",
    # Null-object implementation
    "NoopShangshulingPort",
    # Port protocol
    "ShangshulingPort",
    "append_pm_report",
    "get_director_task_status_summary",
    "get_task_signature",
    # Pure functions
    "normalize_task_status",
    "to_bool",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PM_SPIN_GUARD_STATUS: str = "PM_SPIN_GUARD_ACTIVE"

# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def normalize_task_status(value: Any) -> str:
    """Normalise a raw task status string to a canonical token.

    Kept intentionally identical to
    ``polaris.delivery.cli.pm.tasks.normalize_task_status`` so that both
    layers agree on the canonical set.

    Returns one of:
    ``todo | in_progress | review | needs_continue | done | failed | blocked``
    """
    token = str(value or "").strip().lower()
    if token in ("todo", "to_do", "pending"):
        return "todo"
    if token in ("in_progress", "in-progress", "doing", "active"):
        return "in_progress"
    if token in ("review", "in_review"):
        return "review"
    if token in ("needs_continue", "need_continue", "continue", "retry_same_task"):
        return "needs_continue"
    if token in ("done", "success", "completed"):
        return "done"
    if token in ("failed", "fail", "error"):
        return "failed"
    if token in ("blocked", "block"):
        return "blocked"
    return "todo"


def get_task_signature(tasks: Any) -> str:
    """Return a short, stable fingerprint for a task list for spin detection.

    Uses the first task's ``fingerprint`` or ``id`` field as the primary
    signal (matching ``polaris.delivery.cli.pm.tasks_utils.get_task_signature``).
    Falls back to a SHA-256 digest of the sorted JSON representation when
    neither field is present.

    Args:
        tasks: Any value; list of task dicts is expected.

    Returns:
        A short string suitable for equality comparison across iterations.
    """
    if not isinstance(tasks, list) or not tasks:
        return ""
    primary = tasks[0] if isinstance(tasks[0], dict) else {}
    sig = str(primary.get("fingerprint") or primary.get("id") or "").strip()
    if sig:
        return sig
    # Deterministic fallback for task lists without id/fingerprint fields
    try:
        serialised = json.dumps(tasks, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(serialised.encode("utf-8")).hexdigest()[:16]
    except (RuntimeError, ValueError):
        return ""


def get_director_task_status_summary(tasks: Any) -> dict[str, int]:
    """Return a status-count dictionary for all Director-assigned tasks.

    Args:
        tasks: Any value; list of task dicts is expected.

    Returns:
        Dict with keys: total, todo, in_progress, review, needs_continue,
        done, failed, blocked.
    """
    summary: dict[str, int] = {
        "total": 0,
        "todo": 0,
        "in_progress": 0,
        "review": 0,
        "needs_continue": 0,
        "done": 0,
        "failed": 0,
        "blocked": 0,
    }
    if not isinstance(tasks, list):
        return summary

    for item in tasks:
        if not isinstance(item, dict):
            continue
        assignee = str(item.get("assigned_to") or "").strip().lower()
        if assignee != "director":
            continue
        summary["total"] += 1
        status = normalize_task_status(item.get("status"))
        if status in summary:
            summary[status] += 1

    return summary


def to_bool(value: Any, default: bool = True) -> bool:
    """Convert an arbitrary value to a boolean with an explicit default.

    Args:
        value: The raw value to convert.
        default: Returned when ``value`` cannot be mapped to True/False.

    Returns:
        Boolean interpretation of ``value``, or ``default``.
    """
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def append_pm_report(path: str, content: str) -> None:
    """Append *content* to a PM report file at *path*.

    Creates parent directories if absent.  All I/O uses UTF-8.
    Silently no-ops when *path* is empty.

    Args:
        path: Filesystem path to the report file.
        content: Text to append; a trailing newline is added if absent.
    """
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(content)
        if not content.endswith("\n"):
            fh.write("\n")


# ---------------------------------------------------------------------------
# ShangshulingPort – abstract boundary for task-registry operations
# ---------------------------------------------------------------------------


@runtime_checkable
class ShangshulingPort(Protocol):
    """Port for PM (shangshuling) task-registry operations.

    The Cell depends on this abstract interface; the concrete implementation
    lives in the delivery layer and is injected at runtime.

    All implementations MUST be safe to call without raising – failures
    should be logged and a sensible zero/empty/False value returned.
    """

    def sync_tasks_to_shangshuling(
        self,
        workspace_full: str,
        tasks: list[dict[str, Any]],
    ) -> int:
        """Sync a task list to the shangshuling registry.

        Args:
            workspace_full: Absolute workspace path.
            tasks: Task dicts to synchronise.

        Returns:
            Number of tasks successfully synced (0 on any failure).
        """
        ...

    def get_shangshuling_ready_tasks(
        self,
        workspace_full: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Return tasks ready for Director execution.

        Args:
            workspace_full: Absolute workspace path.
            limit: Maximum number of tasks to return.

        Returns:
            List of task dicts; empty list on any failure.
        """
        ...

    def record_shangshuling_task_completion(
        self,
        workspace_full: str,
        task_id: str,
        success: bool,
        metadata: dict[str, Any],
    ) -> bool:
        """Record that a task has been completed or failed.

        Args:
            workspace_full: Absolute workspace path.
            task_id: Identifier of the completed task.
            success: True if the task succeeded, False otherwise.
            metadata: Arbitrary metadata to attach to the completion record.

        Returns:
            True if the record was persisted successfully.
        """
        ...

    def archive_task_history(
        self,
        workspace_full: str,
        cache_root_full: str,
        run_id: str,
        iteration: int,
        normalized: dict[str, Any],
        director_result: Any,
        timestamp: str,
    ) -> None:
        """Archive iteration task history to durable storage.

        Args:
            workspace_full: Absolute workspace path.
            cache_root_full: Cache root path.
            run_id: Run identifier string.
            iteration: Current iteration number.
            normalized: Normalised PM payload dict.
            director_result: Optional Director result dict or None.
            timestamp: ISO-format timestamp string.
        """
        ...


class NoopShangshulingPort:
    """Null-object implementation of ``ShangshulingPort``.

    Used as the default when no delivery adapter has been injected (e.g.
    during isolated unit tests that do not need shangshuling behaviour).
    Every method is a no-op that returns a safe zero/empty value.
    """

    def sync_tasks_to_shangshuling(
        self,
        workspace_full: str,
        tasks: list[dict[str, Any]],
    ) -> int:
        return 0

    def get_shangshuling_ready_tasks(
        self,
        workspace_full: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        return []

    def record_shangshuling_task_completion(
        self,
        workspace_full: str,
        task_id: str,
        success: bool,
        metadata: dict[str, Any],
    ) -> bool:
        return False

    def archive_task_history(
        self,
        workspace_full: str,
        cache_root_full: str,
        run_id: str,
        iteration: int,
        normalized: dict[str, Any],
        director_result: Any,
        timestamp: str,
    ) -> None:
        return
