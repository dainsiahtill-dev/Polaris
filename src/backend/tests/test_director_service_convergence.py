"""Tests for DirectorService convergence — auto-stop, deadlock, and self-heal."""

from __future__ import annotations

import asyncio

import pytest
from polaris.cells.director.execution.public.service import DirectorConfig, DirectorService, DirectorState
from polaris.domain.entities import TaskResult, TaskStatus


class _FastWorkerExecutor:
    def __init__(self, workspace: str, message_bus=None, worker_id: str = ""):
        self.workspace = workspace
        self._bus = message_bus
        self._worker_id = worker_id

    async def execute(self, task):
        if bool(task.metadata.get("force_fail")):
            raise RuntimeError("forced failure")
        await asyncio.sleep(0.01)
        return TaskResult(
            success=True,
            output="ok",
            duration_ms=1,
        )


async def _wait_for_state(service: DirectorService, expected: DirectorState, timeout: float = 8.0) -> None:
    async def _poll() -> bool:
        return service.state == expected

    await asyncio.wait_for(_wait_until(_poll), timeout=timeout)


async def _wait_for_task_status(
    service: DirectorService,
    task_id: str,
    expected: TaskStatus,
    timeout: float = 8.0,
) -> None:
    async def _poll() -> bool:
        task = await service.get_task(task_id)
        return bool(task and task.status == expected)

    await asyncio.wait_for(_wait_until(_poll), timeout=timeout)


async def _wait_until(predicate, interval: float = 0.05) -> None:
    while True:
        if await predicate():
            return
        await asyncio.sleep(interval)


# Shared patch targets for tests that exercise worker-loop task execution.
# DirectorService uses two execution paths:
#   Path A: _schedule_tasks() -> _execute_task() -> _run_command()  (tasks with commands)
#   Path B: WorkerPoolService._worker_loop -> WorkerExecutor.execute() (all tasks)
# All submitted tasks go through Path B.  WorkerPoolService caches the executor class
# as a module-level variable `_WorkerExecutor` at import time — workers reference this
# cached variable when spawned, NOT the class directly.  Both the class AND the cached
# variable must be patched simultaneously, otherwise workers still use the real class.
_WORKER_EXECUTOR_MODULE = "polaris.cells.director.tasking.internal.worker_pool_service"
_WORKER_EXECUTOR_CLASS = "polaris.cells.director.tasking.internal.worker_executor.WorkerExecutor"


@pytest.mark.asyncio
async def test_director_auto_stops_after_all_tasks_terminal(monkeypatch, tmp_path) -> None:
    # Patch both the class and the cached module-level variable that workers use.
    monkeypatch.setattr(_WORKER_EXECUTOR_CLASS, _FastWorkerExecutor)
    monkeypatch.setattr(f"{_WORKER_EXECUTOR_MODULE}._WorkerExecutor", _FastWorkerExecutor)

    service = DirectorService(
        DirectorConfig(
            workspace=str(tmp_path),
            max_workers=2,
            task_poll_interval=0.05,
        )
    )
    await service.start()

    try:
        await service.submit_task(
            subject="complete quickly",
            description="regression guard for auto convergence",
            metadata={"pm_task_id": "PM-00001-1"},
        )

        await _wait_for_state(service, DirectorState.IDLE, timeout=10.0)

        status = await service.get_status()
        assert status["state"] == "IDLE"
        assert status["tasks"]["by_status"]["COMPLETED"] >= 1
        assert status["metrics"]["auto_stopped_runs"] >= 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_director_auto_stops_when_no_tasks_submitted(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_WORKER_EXECUTOR_CLASS, _FastWorkerExecutor)
    monkeypatch.setattr(f"{_WORKER_EXECUTOR_MODULE}._WorkerExecutor", _FastWorkerExecutor)
    monkeypatch.setattr(
        "polaris.cells.director.execution.service.EMPTY_QUEUE_STALL_TIMEOUT_SECONDS",
        0.25,
    )

    service = DirectorService(
        DirectorConfig(
            workspace=str(tmp_path),
            max_workers=1,
            task_poll_interval=0.05,
        )
    )
    await service.start()

    try:
        await _wait_for_state(service, DirectorState.IDLE, timeout=4.0)

        status = await service.get_status()
        assert status["state"] == "IDLE"
        assert status["tasks"]["total"] == 0
        assert status["metrics"]["auto_stopped_runs"] >= 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_director_fails_deadlocked_pending_tasks_and_converges(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_WORKER_EXECUTOR_CLASS, _FastWorkerExecutor)
    monkeypatch.setattr(f"{_WORKER_EXECUTOR_MODULE}._WorkerExecutor", _FastWorkerExecutor)

    service = DirectorService(
        DirectorConfig(
            workspace=str(tmp_path),
            max_workers=1,
            task_poll_interval=0.05,
        )
    )
    await service.start()

    try:
        blocker = await service.submit_task(
            subject="force fail",
            description="upstream dependency failure",
            metadata={"pm_task_id": "PM-00001-1", "force_fail": True},
        )
        dependent = await service.submit_task(
            subject="dependent task",
            description="should fail when blocker fails",
            blocked_by=[blocker.id],
            metadata={"pm_task_id": "PM-00001-2"},
        )

        await _wait_for_task_status(service, blocker.id, TaskStatus.FAILED, timeout=8.0)
        await _wait_for_task_status(service, dependent.id, TaskStatus.FAILED, timeout=12.0)
        await _wait_for_state(service, DirectorState.IDLE, timeout=12.0)

        status = await service.get_status()
        assert status["tasks"]["by_status"]["FAILED"] >= 2
        assert status["metrics"]["deadlock_breaks"] >= 1
        assert status["metrics"]["auto_stopped_runs"] >= 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_director_start_rolls_back_state_when_worker_init_fails(monkeypatch, tmp_path) -> None:
    async def _raise_initialize(self) -> None:
        raise RuntimeError("init failed")

    # WorkerService migrated from director.execution.internal.worker_pool_service
    # to director.tasking.internal.worker_pool_service (Phase 3).
    monkeypatch.setattr(
        "polaris.cells.director.tasking.internal.worker_pool_service.WorkerService.initialize",
        _raise_initialize,
    )

    service = DirectorService(
        DirectorConfig(
            workspace=str(tmp_path),
            max_workers=1,
            task_poll_interval=0.05,
        )
    )

    with pytest.raises(RuntimeError, match="init failed"):
        await service.start()

    status = await service.get_status()
    assert service.state == DirectorState.IDLE
    assert status["state"] == "IDLE"
    assert status["workers"]["total"] == 0


@pytest.mark.asyncio
async def test_director_status_self_heals_stale_running_without_loop_or_workers(tmp_path) -> None:
    service = DirectorService(
        DirectorConfig(
            workspace=str(tmp_path),
            max_workers=1,
            task_poll_interval=0.05,
        )
    )
    service.state = DirectorState.RUNNING
    service._main_loop_task = None

    # _try_finalize_idle() is the self-healing path that transitions RUNNING->IDLE.
    # get_status() is a CQS query and must not mutate state.
    await service._try_finalize_idle()

    assert service.state == DirectorState.IDLE
