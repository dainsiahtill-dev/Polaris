"""Tests for DirectorService convergence — auto-stop, deadlock, and self-heal."""

from __future__ import annotations

import asyncio

import pytest
from polaris.cells.director.execution.public.service import DirectorConfig, DirectorService, DirectorState
from polaris.domain.entities import TaskResult, TaskStatus, WorkerStatus


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


# Phase 4 architecture note (2026-03-27):
# WorkerService._worker_loop now delegates to execution_broker subprocesses.
# Tests that need fast deterministic execution must bypass the subprocess path.
# Strategy: make _worker_loop passive (heartbeats only) and patch
# DirectorService._execute_task for fast in-process completion.


async def _passive_worker_loop(self, worker) -> None:
    """Heartbeat-only worker loop that does not claim or execute tasks."""
    try:
        while worker.status not in (WorkerStatus.STOPPED, WorkerStatus.FAILED):
            worker.update_heartbeat()
            if worker.status == WorkerStatus.STOPPING and not worker.current_task_id:
                worker.status = WorkerStatus.STOPPED
                break
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        worker.status = WorkerStatus.STOPPED
        raise
    finally:
        asyncio.get_running_loop().call_soon(self._cleanup_worker_task, worker.id)


async def _fast_execute_task(self, task, worker) -> None:
    """Fast deterministic task execution for convergence tests."""
    from datetime import datetime, timezone

    task_id_str = str(task.id)
    await self._task_service.on_task_started(task_id_str)
    start_time = datetime.now(timezone.utc)
    try:
        if bool(task.metadata.get("force_fail")):
            raise RuntimeError("forced failure")
        result = TaskResult(success=True, output="ok", duration_ms=1)
        await self._task_service.on_task_completed(task_id_str, result)
        self._metrics["tasks_completed"] += 1
    except Exception as e:  # noqa: BLE001
        result = TaskResult(
            success=False,
            output="",
            error=str(e),
            duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
        )
        await self._task_service.on_task_failed(task_id_str, str(e), recoverable=False)
        self._metrics["tasks_failed"] += 1
    finally:
        worker.release_task(result)


_WORKER_POOL_MODULE = "polaris.cells.director.tasking.internal.worker_pool_service"
_DIRECTOR_EXECUTION_MODULE = "polaris.cells.director.execution.service"


@pytest.mark.asyncio
async def test_director_auto_stops_after_all_tasks_terminal(monkeypatch, tmp_path) -> None:
    # Make workers passive so DirectorService._schedule_tasks is the only execution path.
    monkeypatch.setattr(f"{_WORKER_POOL_MODULE}.WorkerService._worker_loop", _passive_worker_loop)
    monkeypatch.setattr(f"{_DIRECTOR_EXECUTION_MODULE}.DirectorService._execute_task", _fast_execute_task)

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
    monkeypatch.setattr(f"{_WORKER_POOL_MODULE}.WorkerService._worker_loop", _passive_worker_loop)
    monkeypatch.setattr(f"{_DIRECTOR_EXECUTION_MODULE}.DirectorService._execute_task", _fast_execute_task)
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
    monkeypatch.setattr(f"{_WORKER_POOL_MODULE}.WorkerService._worker_loop", _passive_worker_loop)
    monkeypatch.setattr(f"{_DIRECTOR_EXECUTION_MODULE}.DirectorService._execute_task", _fast_execute_task)

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
async def test_director_no_command_task_result_is_failure(tmp_path) -> None:
    service = DirectorService(
        DirectorConfig(
            workspace=str(tmp_path),
            max_workers=1,
            task_poll_interval=0.05,
        )
    )

    result = await service._run_command(None, timeout=1)

    assert result.success is False
    assert "command is required" in str(result.error)


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
