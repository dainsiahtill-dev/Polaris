"""Task execution runner for subprocess-based execution via execution_broker.

This module provides a subprocess entry point for executing WorkerExecutor tasks
in isolated processes. It reads task data from stdin as JSON and writes results
to stdout.

Usage:
    python -m polaris.cells.director.tasking.internal.task_execution_runner < stdin

Input (JSON via stdin):
    {
        "workspace": "/path/to/workspace",
        "worker_id": "worker-abc123",
        "task": {
            "id": "task-1",
            "subject": "Implement login",
            "description": "...",
            "timeout_seconds": 300,
            "metadata": {...}
        }
    }

Output (JSON via stdout):
    {
        "success": true,
        "output": "Generated 5 files",
        "error": null,
        "duration_ms": 1500,
        "evidence": [...]
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure logging for subprocess execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


async def _run_task_execution(task_data: dict[str, Any]) -> dict[str, Any]:
    """Execute the task using WorkerExecutor.

    Args:
        task_data: Task data including workspace, worker_id, and task details.

    Returns:
        TaskResult dict serialized for JSON output.
    """
    from polaris.cells.director.tasking.internal.worker_executor import WorkerExecutor
    from polaris.domain.entities import Task

    workspace = task_data.get("workspace", ".")
    worker_id = task_data.get("worker_id", "")
    task_dict = task_data.get("task", {})

    # Reconstruct Task entity from dict
    from polaris.domain.entities import TaskPriority, TaskStatus

    raw_status = task_dict.get("status", "pending")
    if isinstance(raw_status, str):
        try:
            status = TaskStatus(raw_status)
        except ValueError:
            status = TaskStatus.PENDING
    else:
        status = TaskStatus.PENDING

    raw_priority = task_dict.get("priority", "medium")
    if isinstance(raw_priority, str):
        try:
            priority = TaskPriority(raw_priority.lower())
        except ValueError:
            priority = TaskPriority.MEDIUM
    else:
        priority = TaskPriority.MEDIUM

    task = Task(
        id=task_dict.get("id", "unknown"),
        subject=task_dict.get("subject", ""),
        description=task_dict.get("description", ""),
        status=status,
        priority=priority,
        blocked_by=task_dict.get("blocked_by", []),
        blocks=task_dict.get("blocks", []),
        owner=task_dict.get("owner", ""),
        assignee=task_dict.get("assignee", ""),
        claimed_by=task_dict.get("claimed_by"),
        role=task_dict.get("role", ""),
        constraints=task_dict.get("constraints", []),
        acceptance_criteria=task_dict.get("acceptance_criteria", []),
        command=task_dict.get("command"),
        working_directory=task_dict.get("working_directory"),
        timeout_seconds=task_dict.get("timeout_seconds", 300),
        max_retries=task_dict.get("max_retries", 3),
        retry_count=task_dict.get("retry_count", 0),
        created_at=task_dict.get("created_at", 0.0),
        started_at=task_dict.get("started_at"),
        completed_at=task_dict.get("completed_at"),
        claimed_at=task_dict.get("claimed_at"),
        result_summary=task_dict.get("result_summary", ""),
        error_message=task_dict.get("error_message"),
        evidence_refs=task_dict.get("evidence_refs", []),
        tags=task_dict.get("tags", []),
        metadata=task_dict.get("metadata", {}),
    )

    # Execute the task
    executor = WorkerExecutor(workspace=workspace, worker_id=worker_id)
    result = await executor.execute(task)

    # Serialize TaskResult to dict
    return result.to_dict()


def main() -> int:
    """Main entry point for subprocess execution."""
    _configure_logging()

    try:
        # Read task data from stdin
        input_data = json.load(sys.stdin)
        logger.info("Received task execution request: %s", input_data.get("task", {}).get("id"))

        # Run async execution
        result = asyncio.run(_run_task_execution(input_data))

        # Write result to stdout
        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()

        logger.info("Task execution completed successfully")
        return 0

    except json.JSONDecodeError as e:
        logger.error("Invalid JSON input: %s", e)
        error_result = {
            "success": False,
            "output": "",
            "error": f"Invalid JSON input: {e}",
            "duration_ms": 0,
            "evidence": [],
        }
        json.dump(error_result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 1

    except Exception as e:
        logger.error("Task execution failed: %s", e, exc_info=True)
        error_result = {
            "success": False,
            "output": "",
            "error": str(e),
            "duration_ms": 0,
            "evidence": [],
        }
        json.dump(error_result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    sys.exit(main())
