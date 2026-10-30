"""REST API for DLQ operations."""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.task_market.internal.dlq import DLQManager
from polaris.cells.runtime.task_market.internal.store import get_store

VALID_TARGET_STAGES = frozenset({"pending_design", "pending_exec"})


def replay_dlq_item(workspace: str, task_id: str, target_stage: str) -> dict[str, Any]:
    """Replay a DLQ item back to active queue at target_stage.

    Args:
        workspace: The workspace identifier.
        task_id: The task to replay.
        target_stage: Either "pending_design" or "pending_exec".

    Returns:
        A result dict with ``ok`` flag and either the replayed item details
        or an error reason.
    """
    if target_stage not in VALID_TARGET_STAGES:
        return {
            "ok": False,
            "task_id": task_id,
            "reason": f"Invalid target_stage: {target_stage!r}. Must be one of: {', '.join(sorted(VALID_TARGET_STAGES))}",
        }

    store = get_store(workspace)
    dlq = DLQManager(store)

    try:
        item = dlq.replay_item(
            workspace=workspace,
            task_id=task_id,
            target_stage=target_stage,
            reason="manual_replay",
        )
        return {
            "ok": True,
            "task_id": task_id,
            "target_stage": item.stage,
            "status": item.status,
            "version": item.version,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "task_id": task_id, "reason": str(exc)}


def get_dlq_stats(workspace: str) -> dict[str, Any]:
    """Get DLQ statistics for workspace.

    Args:
        workspace: The workspace identifier.

    Returns:
        A dict with ``total`` count and ``by_error_code`` breakdown.
    """
    store = get_store(workspace)
    dlq = DLQManager(store)
    return dlq.get_dlq_stats(workspace)


def list_dlq_items(workspace: str, limit: int = 200) -> list[dict[str, Any]]:
    """List DLQ items for workspace.

    Args:
        workspace: The workspace identifier.
        limit: Maximum number of entries to return (default 200).

    Returns:
        A list of dead-letter entry dicts, newest first.
    """
    store = get_store(workspace)
    dlq = DLQManager(store)
    return dlq.load_dlq_items(workspace=workspace, limit=limit)


__all__ = [
    "VALID_TARGET_STAGES",
    "get_dlq_stats",
    "list_dlq_items",
    "replay_dlq_item",
]
