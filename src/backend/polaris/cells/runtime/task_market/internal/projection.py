"""Read-only task-market projections owned by the task-market cell."""

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
    """Build dashboard views without transferring task-market ownership."""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._store: TaskMarketStoreProtocol = get_store(workspace)

    def get_queue_depth_by_stage(self) -> dict[str, int]:
        """Return queued work-item counts grouped by stage."""

        items = self._store.load_items()
        counts: dict[str, int] = {}
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.stage in QUEUE_STAGES:
                counts[item.stage] = counts.get(item.stage, 0) + 1
        return counts

    def get_in_progress_count(self) -> dict[str, int]:
        """Return active work-item counts grouped by effective status."""

        items = self._store.load_items()
        counts: dict[str, int] = {}
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.status not in TERMINAL_STATUSES and item.status != item.stage:
                active_status = item.active_status()
                if active_status:
                    counts[active_status] = counts.get(active_status, 0) + 1
        return counts

    def get_dead_letter_count(self) -> int:
        """Return total dead-lettered work items."""

        return len(self._store.load_dead_letters(limit=10_000))

    def get_active_work_items(
        self,
        stage: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return active work items, optionally restricted to one stage."""

        items = self._store.load_items()
        result: list[dict[str, Any]] = []
        for item in items.values():
            if item.workspace != self._workspace:
                continue
            if item.status in TERMINAL_STATUSES:
                continue
            if stage and item.stage != stage:
                continue
            result.append(item.to_dict())
        result.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return result[:limit]

    def get_worker_load(self) -> dict[str, dict[str, Any]]:
        """Return claimed work-item counts grouped by worker."""

        items = self._store.load_items()
        loads: dict[str, dict[str, Any]] = {}
        for item in items.values():
            if item.workspace != self._workspace or not item.claimed_by:
                continue
            worker = loads.setdefault(
                item.claimed_by,
                {"role": item.claimed_role, "task_count": 0},
            )
            worker["task_count"] = int(worker["task_count"]) + 1
        return loads

    def get_trace_timeline(self, trace_id: str) -> list[dict[str, Any]]:
        """Return work items for one trace in creation order."""

        items = self._store.load_items()
        trace_items = [
            item.to_dict() for item in items.values() if item.workspace == self._workspace and item.trace_id == trace_id
        ]
        trace_items.sort(key=lambda item: str(item.get("created_at") or ""))
        return trace_items

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Return the complete task-market dashboard read model."""

        queue_depth = self.get_queue_depth_by_stage()
        in_progress = self.get_in_progress_count()
        return {
            "workspace": self._workspace,
            "queue_depth": queue_depth,
            "in_progress": in_progress,
            "dead_letter_count": self.get_dead_letter_count(),
            "worker_load": self.get_worker_load(),
            "active_items": self.get_active_work_items(limit=20),
            "total_active": sum(queue_depth.values()) + sum(in_progress.values()),
        }


__all__ = ["TaskMarketProjection"]
