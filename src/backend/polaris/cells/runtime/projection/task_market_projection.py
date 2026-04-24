"""Task market projection service for dashboard and observability."""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.task_market.internal.models import (
    QUEUE_STAGES,
    TERMINAL_STATUSES,
)
from polaris.cells.runtime.task_market.internal.store import (
    TaskMarketStoreProtocol,
    get_store,
)


class TaskMarketProjection:
    """Projection view of the task market for dashboards."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._store: TaskMarketStoreProtocol = get_store(workspace)

    def get_queue_depth_by_stage(self) -> dict[str, int]:
        """Return count of items per stage (queue stages only)."""
        items = self._store.load_items()
        counts: dict[str, int] = {}
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.stage in QUEUE_STAGES:
                counts[item.stage] = counts.get(item.stage, 0) + 1
        return counts

    def get_in_progress_count(self) -> dict[str, int]:
        """Return count of in-progress items by active status."""
        items = self._store.load_items()
        counts: dict[str, int] = {}
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.status not in TERMINAL_STATUSES and item.status != item.stage:
                # This is an in-progress item
                active_status = item.active_status()
                if active_status:
                    counts[active_status] = counts.get(active_status, 0) + 1
        return counts

    def get_dead_letter_count(self) -> int:
        """Return total dead-lettered items."""
        dlq = self._store.load_dead_letters(limit=10_000)
        return len(dlq)

    def get_active_work_items(
        self,
        stage: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get active (non-terminal) work items, optionally filtered by stage."""
        items = self._store.load_items()
        result = []
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.status in TERMINAL_STATUSES:
                continue
            if stage and item.stage != stage:
                continue
            result.append(item.to_dict())
        # Sort by updated_at desc
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result[:limit]

    def get_worker_load(self) -> dict[str, dict[str, Any]]:
        """Get load per worker (claimed tasks count)."""
        items = self._store.load_items()
        loads: dict[str, dict[str, Any]] = {}
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.claimed_by:
                if item.claimed_by not in loads:
                    loads[item.claimed_by] = {"role": item.claimed_role, "task_count": 0}
                loads[item.claimed_by]["task_count"] += 1
        return loads

    def get_trace_timeline(self, trace_id: str) -> list[dict[str, Any]]:
        """Get all work items for a trace_id, sorted by created_at."""
        items = self._store.load_items()
        trace_items = []
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.trace_id == trace_id:
                trace_items.append(item.to_dict())
        trace_items.sort(key=lambda x: x.get("created_at", ""))
        return trace_items

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Get complete dashboard summary."""
        queue_depth = self.get_queue_depth_by_stage()
        in_progress = self.get_in_progress_count()
        dlq_count = self.get_dead_letter_count()
        worker_load = self.get_worker_load()
        active_items = self.get_active_work_items(limit=20)

        return {
            "workspace": self._workspace,
            "queue_depth": queue_depth,
            "in_progress": in_progress,
            "dead_letter_count": dlq_count,
            "worker_load": worker_load,
            "active_items": active_items,
            "total_active": sum(queue_depth.values()) + sum(in_progress.values()),
        }
