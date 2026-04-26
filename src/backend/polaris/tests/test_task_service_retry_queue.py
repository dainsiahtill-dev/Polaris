from __future__ import annotations

import pytest
from polaris.cells.director.execution.internal.task_lifecycle_service import TaskQueueConfig, TaskService
from polaris.domain.entities import TaskResult, TaskStatus


@pytest.mark.asyncio
async def test_on_task_completed_requeues_auto_retry_task(tmp_path) -> None:
    service = TaskService(
        TaskQueueConfig(default_timeout_seconds=30),
        workspace=str(tmp_path),
    )
    task = await service.create_task(
        subject="Retry task",
        description="Should retry on first failure",
    )

    queued_task_id = await service.get_next_ready_task(timeout=0)
    assert queued_task_id == task.id
    assert await service.on_task_claimed(task.id, "worker-1")
    assert await service.on_task_started(task.id)

    retry_signal = await service.on_task_completed(
        task.id,
        TaskResult(success=False, output="", error="boom"),
    )
    assert retry_signal == [task.id]

    task_after = await service.get_task(task.id)
    assert task_after is not None
    assert task_after.status == TaskStatus.READY

    requeued_task_id = await service.get_next_ready_task(timeout=0)
    assert requeued_task_id == task.id
