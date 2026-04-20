"""Query API for task market projection."""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.projection.task_market_projection import TaskMarketProjection


def get_dashboard(workspace: str) -> dict[str, Any]:
    """Get complete dashboard summary."""
    proj = TaskMarketProjection(workspace)
    return proj.get_dashboard_summary()


def list_active_items(
    workspace: str,
    stage: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List active (non-terminal) work items."""
    proj = TaskMarketProjection(workspace)
    return proj.get_active_work_items(stage=stage, limit=limit)


def get_worker_load(workspace: str) -> dict[str, dict[str, Any]]:
    """Get worker load summary."""
    proj = TaskMarketProjection(workspace)
    return proj.get_worker_load()
