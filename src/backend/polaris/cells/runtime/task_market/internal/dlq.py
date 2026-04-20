"""Dead Letter Queue manager for ``runtime.task_market``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import TaskMarketError, TaskNotFoundError
from .models import TaskWorkItemRecord, now_iso

if TYPE_CHECKING:
    from .store import TaskMarketStore


class DLQManager:
    """Manages dead-lettered tasks and their lifecycle.

    A task enters the DLQ when:
    1. ``attempts >= max_attempts`` on fail or claim.
    2. An explicit ``MoveTaskToDeadLetterCommand`` is issued.
    3. A ``to_dead_letter=True`` flag is set on ``FailTaskStageCommand``.
    """

    __slots__ = ("_store",)

    def __init__(self, store: TaskMarketStore) -> None:
        self._store = store

    def move_to_dead_letter(
        self,
        item: TaskWorkItemRecord,
        reason: str,
        error_code: str,
        metadata: dict[str, Any],
    ) -> None:
        """Move a work item into the dead-letter state.

        This updates the item in-place and appends a DLQ record to the
        dead-letter store.
        """
        # Update item fields.
        item.stage = "dead_letter"
        item.status = "dead_letter"
        item.lease_token = ""
        item.lease_expires_at = 0.0
        item.claimed_by = ""
        item.claimed_role = ""
        item.version += 1
        item.updated_at = now_iso()

        # Persist the DLQ entry.
        dlq_entry: dict[str, Any] = {
            "task_id": item.task_id,
            "trace_id": item.trace_id,
            "run_id": item.run_id,
            "workspace": item.workspace,
            "reason": str(reason or "").strip(),
            "error_code": str(error_code or "").strip(),
            "attempts": item.attempts,
            "max_attempts": item.max_attempts,
            "metadata": dict(metadata),
            "dead_lettered_at": now_iso(),
        }
        self._store.append_dead_letter(dlq_entry)

    def load_dlq_items(
        self,
        workspace: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Load dead-letter entries for a workspace.

        Returns the most recent ``limit`` entries, ordered newest first.
        """
        return self._store.load_dead_letters(limit=limit)

    def replay_item(
        self,
        workspace: str,
        task_id: str,
        target_stage: str,
        reason: str,
    ) -> TaskWorkItemRecord:
        """Replay a dead-lettered item back into the active queue.

        The item's ``attempts`` counter is reset so it can be retried.
        """
        items = self._store.load_items()
        item = items.get(task_id)

        if item is None:
            raise TaskNotFoundError(
                f"Cannot replay: task {task_id} not found in store",
                task_id=task_id,
            )

        if item.status != "dead_letter":
            raise TaskMarketError(
                f"Cannot replay task {task_id}: status is {item.status}, not dead_letter",
                code="not_in_dead_letter",
                details={"task_id": task_id, "status": item.status},
            )

        # Reset attempts so it can be retried.
        item.attempts = 0
        item.stage = target_stage
        item.status = target_stage
        item.lease_token = ""
        item.lease_expires_at = 0.0
        item.claimed_by = ""
        item.claimed_role = ""
        item.metadata = dict(item.metadata)
        item.metadata["_replay_reason"] = str(reason or "").strip()
        item.metadata["_replayed_at"] = now_iso()
        item.version += 1
        item.updated_at = now_iso()

        items[item.task_id] = item
        self._store.save_items(items)

        return item

    def get_dlq_stats(self, workspace: str) -> dict[str, Any]:
        """Return aggregated DLQ statistics for a workspace."""
        items = self.load_dlq_items(workspace=workspace, limit=10_000)
        total = len(items)
        by_error_code: dict[str, int] = {}
        for entry in items:
            code = str(entry.get("error_code") or "unknown")
            by_error_code[code] = by_error_code.get(code, 0) + 1

        return {
            "total": total,
            "by_error_code": by_error_code,
        }


__all__ = ["DLQManager"]
