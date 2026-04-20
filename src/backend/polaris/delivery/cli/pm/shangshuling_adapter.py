"""Delivery-layer adapter implementing ShangshulingPort for pm_dispatch Cell.

This module lives in the delivery layer and is allowed to import from
``polaris.delivery.cli.pm.*``.  The Cell's internal code imports
``ShangshulingPort`` (the abstract protocol) and loads this adapter lazily
via ``_get_shangshuling_port()``.

The Cell never imports this module directly, preserving the architectural
invariant that ``polaris.cells.*`` MUST NOT depend on ``polaris.delivery.*``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DeliveryShangshulingPort:
    """Concrete ShangshulingPort backed by delivery.cli.pm implementations.

    All methods are safe to call without raising; failures are logged and a
    sensible zero/empty/False value is returned.
    """

    def sync_tasks_to_shangshuling(
        self,
        workspace_full: str,
        tasks: list[dict[str, Any]],
    ) -> int:
        """Sync tasks to 尚书令PM registry.

        Args:
            workspace_full: Absolute workspace path.
            tasks: Task dicts to synchronise.

        Returns:
            Number of tasks successfully synced, 0 on any failure.
        """
        try:
            from polaris.delivery.cli.pm.orchestration_core import (
                sync_tasks_to_shangshuling,
            )

            return int(sync_tasks_to_shangshuling(workspace_full, tasks) or 0)
        except (RuntimeError, ValueError) as exc:
            logger.warning("[shangshuling_adapter] sync_tasks failed: %s", exc)
            return 0

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
        try:
            from polaris.delivery.cli.pm.orchestration_core import (
                get_shangshuling_ready_tasks,
            )

            return get_shangshuling_ready_tasks(workspace_full, limit=limit) or []
        except (RuntimeError, ValueError) as exc:
            logger.warning("[shangshuling_adapter] get_ready_tasks failed: %s", exc)
            return []

    def record_shangshuling_task_completion(
        self,
        workspace_full: str,
        task_id: str,
        success: bool,
        metadata: dict[str, Any],
    ) -> bool:
        """Record task completion in shangshuling registry.

        Note: ``record_shangshuling_task_completion`` in orchestration_core has
        a different signature (``executor``, ``result``).  This adapter bridges
        the Cell's port signature to the concrete function.

        Args:
            workspace_full: Absolute workspace path.
            task_id: Identifier of the completed task.
            success: True if the task succeeded.
            metadata: Arbitrary metadata; mapped to the ``result`` parameter.

        Returns:
            True if recorded successfully.
        """
        try:
            from polaris.delivery.cli.pm.orchestration_core import (
                record_shangshuling_task_completion,
            )

            return bool(
                record_shangshuling_task_completion(
                    workspace_full,
                    task_id=task_id,
                    executor="pm_dispatch",
                    success=success,
                    result=metadata if isinstance(metadata, dict) else {},
                )
            )
        except (RuntimeError, ValueError) as exc:
            logger.error("[shangshuling_adapter] record_completion failed: %s", exc)
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
        """Archive iteration task history.

        Args:
            workspace_full: Absolute workspace path.
            cache_root_full: Cache root path.
            run_id: Run identifier.
            iteration: Current iteration number.
            normalized: Normalised PM payload dict.
            director_result: Optional Director result dict or None.
            timestamp: ISO-format timestamp string.
        """
        try:
            from polaris.delivery.cli.pm.orchestration_core import archive_task_history

            archive_task_history(
                workspace_full,
                cache_root_full,
                run_id,
                iteration,
                normalized,
                director_result,
                timestamp,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("[shangshuling_adapter] archive_task_history failed: %s", exc)
