from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_task_queue_manager_is_runtime_import_surface() -> None:
    from polaris.cells.orchestration.workflow_runtime.public.runtime import TaskQueue, TaskQueueManager

    manager = TaskQueueManager()
    queue = await manager.get_queue("director")

    assert isinstance(queue, TaskQueue)
    assert await manager.list_queues() == ["director"]


@pytest.mark.asyncio
async def test_task_queue_batch_polls_existing_and_new_tasks_with_single_queue() -> None:
    from polaris.cells.orchestration.workflow_runtime.public.runtime import TaskQueue

    queue = TaskQueue("runtime")
    await queue.add_task("director", "task-1", {"step": 1})

    async def _delayed_put() -> None:
        await asyncio.sleep(0.05)
        await queue.add_task("director", "task-2", {"step": 2})

    producer = asyncio.create_task(_delayed_put())
    try:
        tasks = await queue.poll_tasks_batch("director", max_count=2, timeout=0.2)
    finally:
        await producer

    assert [task.task_id for task in tasks] == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_task_queue_manager_stats_sum_subqueues() -> None:
    from polaris.cells.orchestration.workflow_runtime.public.runtime import TaskQueueManager

    manager = TaskQueueManager()
    queue = await manager.get_queue("workflow")
    await queue.add_task("director", "task-a", {})
    await queue.add_task("director", "task-b", {})
    await queue.add_task("qa", "task-c", {})

    stats = await manager.get_queue_stats()

    assert stats["workflow"]["size"] == 3
    assert stats["workflow"]["subqueues"] == {"director": 2, "qa": 1}

