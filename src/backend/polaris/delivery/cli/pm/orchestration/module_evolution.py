"""Module evolution and Shangshuling PM integration."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def ensure_shangshuling_pm_initialized(workspace_full: str) -> bool:
    """Ensure Shangshuling PM system is initialized.

    This function initializes the Shangshuling PM data space if not already present.
    It integrates the new PM state management with the legacy orchestration.

    Args:
        workspace_full: Full path to workspace

    Returns:
        True if initialized successfully
    """
    try:
        from polaris.delivery.cli.pm.pm_integration import get_pm

        pm = get_pm(workspace_full)

        if not pm.is_initialized():
            pm.initialize()

        return True
    except (RuntimeError, ValueError) as e:
        # Log but don't fail - allow graceful degradation
        logger.info(f"[shangshuling] Initialization note: {e}")
        return False


def sync_tasks_to_shangshuling(
    workspace_full: str,
    tasks: list[dict[str, Any]],
) -> int:
    """Sync legacy tasks to Shangshuling PM registry.

    Args:
        workspace_full: Full path to workspace
        tasks: Legacy task list from contracts

    Returns:
        Number of tasks synced
    """
    try:
        from polaris.delivery.cli.pm.pm_integration import get_pm

        pm = get_pm(workspace_full)

        if not pm.is_initialized():
            ensure_shangshuling_pm_initialized(workspace_full)

        return pm.sync_from_legacy_tasks(tasks)
    except (RuntimeError, ValueError) as e:
        logger.info(f"[shangshuling] Task sync note: {e}")
        return 0


def get_shangshuling_ready_tasks(
    workspace_full: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Get tasks ready for execution from Shangshuling PM.

    This is the main interface for the orchestration engine to get tasks
    from the Shangshuling PM truth source.

    Args:
        workspace_full: Full path to workspace
        limit: Maximum number of tasks

    Returns:
        List of tasks ready for execution
    """
    try:
        from polaris.delivery.cli.pm.pm_integration import get_pm

        pm = get_pm(workspace_full)

        if not pm.is_initialized():
            return []

        return pm.get_ready_tasks_for_director(limit)
    except (RuntimeError, ValueError) as e:
        logger.info(f"[shangshuling] Get tasks note: {e}")
        return []


def record_shangshuling_task_completion(
    workspace_full: str,
    task_id: str,
    executor: str,
    success: bool,
    result: dict[str, Any],
) -> bool:
    """Record task completion in Shangshuling PM.

    Args:
        workspace_full: Full path to workspace
        task_id: Task ID
        executor: Executor ID
        success: Whether execution succeeded
        result: Execution result

    Returns:
        True if recorded successfully
    """
    try:
        from polaris.delivery.cli.pm.pm_integration import get_pm

        pm = get_pm(workspace_full)

        if not pm.is_initialized():
            return False

        return pm.record_task_completion(task_id, executor, success, result)
    except (RuntimeError, ValueError) as e:
        logger.info(f"[shangshuling] Record completion note: {e}")
        return False


__all__ = [
    "ensure_shangshuling_pm_initialized",
    "get_shangshuling_ready_tasks",
    "record_shangshuling_task_completion",
    "sync_tasks_to_shangshuling",
]
