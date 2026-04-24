from __future__ import annotations

import asyncio

import pytest
from polaris.cells.director.execution.internal.task_lifecycle_service import TaskQueueConfig, TaskService
from polaris.cells.director.execution.internal.worker_pool_service import WorkerPoolConfig, WorkerService
from polaris.domain.entities import TaskResult


class _BlockingExecutor:
    def __init__(self, workspace: str, message_bus=None, worker_id: str = "") -> None:
        self.workspace = workspace
        self._bus = message_bus
        self._worker_id = worker_id

    async def execute(self, task):
        await asyncio.sleep(0.2)
        return TaskResult(
            success=True,
            output="ok",
            duration_ms=0,
        )


@pytest.mark.asyncio
async def test_worker_execution_does_not_block_event_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "polaris.cells.director.execution.internal.worker_executor.WorkerExecutor",
        _BlockingExecutor,
    )

    task_service = TaskService(
        TaskQueueConfig(default_timeout_seconds=30),
        workspace=str(tmp_path),
    )
    worker_service = WorkerService(
        WorkerPoolConfig(min_workers=1, max_workers=1),
        workspace=str(tmp_path),
        task_service=task_service,
    )

    await worker_service.initialize()

    try:
        marker = asyncio.create_task(asyncio.sleep(0.05))
        task = await task_service.create_task(
            subject="Block if run on main loop",
            description="Regression guard for Director responsiveness",
        )

        await asyncio.wait_for(marker, timeout=0.1)

        await asyncio.wait_for(
            _wait_for_task_completion(task_service, task.id),
            timeout=2,
        )
    finally:
        await worker_service.shutdown()


@pytest.mark.asyncio
async def test_auto_scale_scales_up_without_lock_deadlock(tmp_path) -> None:
    task_service = TaskService(
        TaskQueueConfig(default_timeout_seconds=30),
        workspace=str(tmp_path),
    )
    worker_service = WorkerService(
        WorkerPoolConfig(min_workers=1, max_workers=2),
        workspace=str(tmp_path),
        task_service=task_service,
    )
    await worker_service.initialize()

    try:
        actions = await asyncio.wait_for(worker_service.auto_scale(pending_task_count=3), timeout=1.0)
        assert actions["scaled_up"] == 1
        workers = await asyncio.wait_for(worker_service.get_workers(), timeout=1.0)
        assert len(workers) == 2
    finally:
        await worker_service.shutdown()


@pytest.mark.asyncio
async def test_auto_scale_scales_down_without_lock_deadlock(tmp_path) -> None:
    task_service = TaskService(
        TaskQueueConfig(default_timeout_seconds=30),
        workspace=str(tmp_path),
    )
    worker_service = WorkerService(
        WorkerPoolConfig(min_workers=1, max_workers=3),
        workspace=str(tmp_path),
        task_service=task_service,
    )
    await worker_service.initialize()
    await worker_service.spawn_worker()
    await worker_service.spawn_worker()

    try:
        actions = await asyncio.wait_for(worker_service.auto_scale(pending_task_count=0), timeout=1.5)
        assert actions["scaled_down"] >= 1
        workers = await asyncio.wait_for(worker_service.get_workers(), timeout=1.0)
        assert len(workers) >= worker_service.config.min_workers
        assert len(workers) <= 2
    finally:
        await worker_service.shutdown()


async def _wait_for_task_completion(task_service: TaskService, task_id: str) -> None:
    while True:
        task = await task_service.get_task(task_id)
        if task and task.result is not None:
            return
        await asyncio.sleep(0.01)
