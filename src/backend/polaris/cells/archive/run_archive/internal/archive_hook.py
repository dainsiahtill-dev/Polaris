"""Archive Hook - Lightweight hooks for triggering history archiving.

This module provides async-safe hooks that can be called from various
services (PM, Factory, Workflow) to trigger archiving without blocking
the main flow. Archiving is performed asynchronously.

Task Tracking:
    All async tasks are tracked in _pending_tasks dict for:
    - Graceful cancellation on shutdown
    - Lifecycle management and cleanup
    - Test verification of task execution
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ArchiveHook:
    """Lightweight async hook for triggering history archiving.

    This class manages asynchronous archiving tasks with proper lifecycle
    tracking to enable graceful shutdown and task cancellation.

    Attributes:
        workspace: The workspace path for archive operations.
        _enabled: Whether archiving is currently enabled.
        _pending_tasks: Dict of task_key -> asyncio.Task for tracking.

    Task Key Format:
        - "run:{run_id}" for run archiving
        - "task_snapshot:{snapshot_id}" for task snapshot archiving
        - "factory:{factory_run_id}" for factory archiving

    Example:
        >>> hook = ArchiveHook("/path/to/workspace")
        >>> hook.trigger_run_archive("run-001", "completed")
        >>> await hook.shutdown()  # Cancel all pending tasks on shutdown
    """

    def __init__(self, workspace: str) -> None:
        """Initialize the ArchiveHook.

        Args:
            workspace: Path to the workspace directory.
        """
        self.workspace: str = workspace
        self._enabled: bool = True
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}

    def is_enabled(self) -> bool:
        """Check if archiving is enabled.

        Returns:
            True if archiving is enabled, False otherwise.
        """
        return self._enabled

    def disable(self) -> None:
        """Disable archiving (for testing or migration)."""
        self._enabled = False

    def enable(self) -> None:
        """Enable archiving."""
        self._enabled = True

    def get_pending_task_count(self) -> int:
        """Get the count of pending (not yet completed) tasks.

        Returns:
            Number of tasks currently pending.
        """
        return sum(1 for task in self._pending_tasks.values() if not task.done())

    def get_pending_task_ids(self) -> list[str]:
        """Get list of pending task identifiers.

        Returns:
            List of task keys for pending tasks.
        """
        return [key for key, task in self._pending_tasks.items() if not task.done()]

    def _create_task_with_tracking(
        self,
        coro,
        task_key: str,
    ) -> asyncio.Task[None]:
        """Create an async task with tracking and automatic cleanup.

        Args:
            coro: The coroutine to execute.
            task_key: Unique identifier for this task.

        Returns:
            The created asyncio.Task.
        """
        task = asyncio.create_task(coro)

        def _done_callback(_future: asyncio.Future[None]) -> None:
            """Clean up task reference when completed."""
            self._pending_tasks.pop(task_key, None)
            logger.debug("Task %s completed and removed from tracking", task_key)

        task.add_done_callback(_done_callback)
        self._pending_tasks[task_key] = task
        logger.debug("Task %s created and tracked (total pending: %d)", task_key, self.get_pending_task_count())

        return task

    def trigger_run_archive(
        self,
        run_id: str,
        reason: str = "completed",
        status: str = "",
    ) -> None:
        """Trigger async archiving of a runtime run.

        This method returns immediately - archiving happens in background.
        The task is tracked and can be cancelled via shutdown().

        Args:
            run_id: The run ID to archive.
            reason: Archive reason (completed, failed, cancelled, blocked, timeout).
            status: Original run status (for index).
        """
        if not self._enabled:
            logger.debug("Archive disabled, skipping run archive: %s", run_id)
            return

        task_key = f"run:{run_id}"
        self._create_task_with_tracking(
            self._archive_run_async(run_id, reason, status),
            task_key,
        )

    def trigger_task_snapshot_archive(
        self,
        snapshot_id: str,
        source_tasks_dir: str | None = None,
        source_plan_path: str | None = None,
        reason: str = "completed",
    ) -> None:
        """Trigger async archiving of a task snapshot.

        This method returns immediately - archiving happens in background.
        The task is tracked and can be cancelled via shutdown().

        Args:
            snapshot_id: The snapshot ID (e.g., "pm-00001-1234567890").
            source_tasks_dir: Path to tasks directory.
            source_plan_path: Path to plan.json.
            reason: Archive reason.
        """
        if not self._enabled:
            logger.debug("Archive disabled, skipping task snapshot: %s", snapshot_id)
            return

        task_key = f"task_snapshot:{snapshot_id}"
        self._create_task_with_tracking(
            self._archive_task_snapshot_async(snapshot_id, source_tasks_dir, source_plan_path, reason),
            task_key,
        )

    def trigger_factory_archive(
        self,
        factory_run_id: str,
        source_factory_dir: str | None = None,
        reason: str = "completed",
    ) -> None:
        """Trigger async archiving of a factory run.

        This method returns immediately - archiving happens in background.
        The task is tracked and can be cancelled via shutdown().

        Args:
            factory_run_id: The factory run ID.
            source_factory_dir: Path to factory directory.
            reason: Archive reason.
        """
        if not self._enabled:
            logger.debug("Archive disabled, skipping factory archive: %s", factory_run_id)
            return

        task_key = f"factory:{factory_run_id}"
        self._create_task_with_tracking(
            self._archive_factory_async(factory_run_id, source_factory_dir, reason),
            task_key,
        )

    async def shutdown(self, timeout: float = 5.0) -> list[str]:
        """Shutdown the hook and cancel all pending tasks.

        This method should be called during application shutdown to ensure
        all archiving tasks are properly cancelled.

        Args:
            timeout: Maximum seconds to wait for task cancellation.

        Returns:
            List of task keys that were cancelled.
        """
        if not self._pending_tasks:
            logger.debug("No pending tasks to cancel on shutdown")
            return []

        cancelled_keys = []
        pending_tasks = list(self._pending_tasks.items())

        logger.info("Shutting down ArchiveHook: cancelling %d pending tasks", len(pending_tasks))

        # Cancel all pending tasks
        for key, task in pending_tasks:
            if not task.done():
                task.cancel()
                cancelled_keys.append(key)

        # Wait for cancellation to complete (with timeout)
        if cancelled_keys:
            _done, pending = await asyncio.wait(
                [task for _, task in pending_tasks if not task.done()],
                timeout=timeout,
            )
            # Log any tasks that didn't complete within timeout
            for task in pending:
                logger.warning("Task %s did not complete within shutdown timeout", task)

        # Clean up any remaining references
        self._pending_tasks.clear()

        logger.info("Shutdown complete: cancelled %d tasks", len(cancelled_keys))
        return cancelled_keys

    async def _archive_run_async(
        self,
        run_id: str,
        reason: str,
        status: str,
    ) -> None:
        """Async implementation of run archiving."""
        try:
            from polaris.cells.archive.run_archive.internal.history_archive_service import (
                HistoryArchiveService,
            )

            service = HistoryArchiveService(self.workspace)
            manifest = service.archive_run(run_id, reason, status)

            logger.info(
                "Archived run %s to history (reason=%s, files=%d, size=%d)",
                run_id,
                reason,
                manifest.file_count,
                manifest.total_size_bytes,
            )
        except asyncio.CancelledError:
            logger.debug("Run archive cancelled: %s", run_id)
            raise
        except Exception as e:
            # Log error but don't block main flow
            logger.error(
                "Failed to archive run %s: %s",
                run_id,
                e,
                exc_info=True,
            )
            # Could add to retry queue here

    async def _archive_task_snapshot_async(
        self,
        snapshot_id: str,
        source_tasks_dir: str | None,
        source_plan_path: str | None,
        reason: str,
    ) -> None:
        """Async implementation of task snapshot archiving."""
        try:
            from polaris.cells.archive.task_snapshot_archive.public.service import (
                create_task_snapshot_archive_service,
            )

            service = create_task_snapshot_archive_service(self.workspace)
            manifest = service.archive_task_snapshot(
                snapshot_id,
                source_tasks_dir,
                source_plan_path,
                reason,
            )

            logger.info(
                "Archived task snapshot %s (files=%d, size=%d)",
                snapshot_id,
                manifest.file_count,
                manifest.total_size_bytes,
            )
        except asyncio.CancelledError:
            logger.debug("Task snapshot archive cancelled: %s", snapshot_id)
            raise
        except Exception as e:
            logger.error(
                "Failed to archive task snapshot %s: %s",
                snapshot_id,
                e,
                exc_info=True,
            )

    async def _archive_factory_async(
        self,
        factory_run_id: str,
        source_factory_dir: str | None,
        reason: str,
    ) -> None:
        """Async implementation of factory archiving."""
        try:
            from polaris.cells.archive.factory_archive.public.service import (
                create_factory_archive_service,
            )

            service = create_factory_archive_service(self.workspace)
            manifest = service.archive_factory_run(
                factory_run_id,
                source_factory_dir,
                reason,
            )

            logger.info(
                "Archived factory run %s (files=%d, size=%d)",
                factory_run_id,
                manifest.file_count,
                manifest.total_size_bytes,
            )
        except asyncio.CancelledError:
            logger.debug("Factory archive cancelled: %s", factory_run_id)
            raise
        except Exception as e:
            logger.error(
                "Failed to archive factory run %s: %s",
                factory_run_id,
                e,
                exc_info=True,
            )


# Global archive hook instance (lazy initialization)
_archive_hook: ArchiveHook | None = None


def get_archive_hook(workspace: str) -> ArchiveHook:
    """Get or create the global archive hook.

    Args:
        workspace: Workspace path.

    Returns:
        ArchiveHook instance.
    """
    global _archive_hook
    if _archive_hook is None:
        _archive_hook = ArchiveHook(workspace)
    return _archive_hook


def create_archive_hook(workspace: str) -> ArchiveHook:
    """Create a new archive hook (for testing or isolated use).

    Args:
        workspace: Workspace path.

    Returns:
        New ArchiveHook instance.
    """
    return ArchiveHook(workspace)


__all__ = [
    "ArchiveHook",
    "create_archive_hook",
    "get_archive_hook",
]
