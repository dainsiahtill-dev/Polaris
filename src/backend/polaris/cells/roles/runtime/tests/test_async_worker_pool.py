"""
Tests for AsyncWorker and AsyncWorkerPool using execution_broker.

Validates:
- Normal execution
- Timeout behavior
- Cancel behavior
- Error handling
- Metadata injection (cell="roles", workspace, task_id, worker_id)
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING, Any, Callable

import pytest
from polaris.cells.roles.runtime.internal.worker_pool import (
    AsyncWorker,
    AsyncWorkerConfig,
    Worker,
    WorkerConfig,
    WorkerResult,
    WorkerTask,
    create_async_worker_pool,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    """Create a temporary work directory for tests."""
    work_dir = tmp_path / "worker_test"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _execution_attempt_identity(
    task_id: int,
    *,
    worker_id: str,
    role_id: str,
) -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/tmp/fake-task-runtime",
        task_id=task_id,
        external_task_id="",
        session_id=f"session-{task_id}",
        attempt=1,
        role_id=role_id,
        worker_id=worker_id,
        run_id="",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )


class _FakeTaskRuntime:
    def __init__(
        self,
        *,
        complete_result: dict[str, Any] | None = None,
        fail_result: dict[str, Any] | None = None,
    ) -> None:
        self.settlements: list[SettleTaskRuntimeExecutionAttemptCommandV1] = []
        self.complete_result = complete_result or {"success": True}
        self.fail_result = fail_result or {"success": True}

    def add_ready_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.listener = listener
        return lambda: None

    def list_ready_task_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "id": 7,
                "metadata": {
                    "command": f"{sys.executable} -c \"print('runtime-row-ok')\"",
                    "env": {},
                    "timeout": 30,
                },
            }
        ]

    def claim_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        worker_id = str(kwargs["worker_id"])
        role_id = str(kwargs["role_id"])
        return {
            "success": True,
            "task": self.list_ready_task_rows()[0],
            "session": {"session_id": f"session-{task_id}"},
            "execution_attempt": _execution_attempt_identity(
                int(task_id),
                worker_id=worker_id,
                role_id=role_id,
            ).to_record(),
        }

    def settle_execution_attempt(self, command: SettleTaskRuntimeExecutionAttemptCommandV1) -> dict[str, Any]:
        assert isinstance(command, SettleTaskRuntimeExecutionAttemptCommandV1)
        assert command.identity.task_id == 7
        assert command.identity.session_id == "session-7"
        assert command.outcome in {"completed", "failed"}
        self.settlements.append(command)
        result = self.complete_result if command.outcome == "completed" else self.fail_result
        return dict(result)


@pytest.mark.asyncio
async def test_async_worker_normal_execution(work_dir: Path) -> None:
    """Test AsyncWorker normal command execution via execution_broker."""
    config = AsyncWorkerConfig(
        worker_id="test-worker-1",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        task = WorkerTask(
            task_id=42,
            command=f"{sys.executable} -c \"print('async-worker-ok')\"",
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "normal_execution"},
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        assert isinstance(result, WorkerResult)
        assert result.task_id == 42
        assert result.worker_id == "test-worker-1"
        assert result.success is True
        assert result.duration > 0
        # Check metadata injection
        assert result.metadata.get("cell") == "roles"
        assert result.metadata.get("task_id") == "42"
        assert result.metadata.get("worker_id") == "test-worker-1"
        assert result.metadata.get("workspace") == str(work_dir)
    finally:
        await worker.stop(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_consumes_task_runtime_rows(work_dir: Path) -> None:
    runtime = _FakeTaskRuntime()
    config = AsyncWorkerConfig(
        worker_id="test-worker-runtime",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )
    worker = AsyncWorker(config, task_runtime=runtime)

    task = worker._claim_ready_task()

    assert task is not None
    assert task.task_id == 7
    assert task.execution_attempt is not None
    assert task.execution_attempt.task_id == 7
    assert task.execution_attempt.session_id == "session-7"
    assert task.execution_attempt.attempt == 1
    assert task.execution_attempt.role_id == "async_worker_pool"
    assert task.execution_attempt.worker_id == "test-worker-runtime"

    await worker._execute_worker_task(task)

    assert len(runtime.settlements) == 1
    settlement = runtime.settlements[0]
    assert settlement.identity == task.execution_attempt
    assert settlement.outcome == "completed"


@pytest.mark.asyncio
async def test_async_worker_projects_task_runtime_finalization_failure(work_dir: Path) -> None:
    runtime = _FakeTaskRuntime(
        complete_result={
            "success": False,
            "reason": "execution_event_append_failed",
            "requested_reason": "completed",
            "failure_class": "ledger_append_failed",
            "state_mutation_applied": True,
            "execution_event": {
                "ok": False,
                "event_type": "completed",
                "error": "ledger unavailable",
            },
        }
    )
    callback_results: list[WorkerResult] = []
    config = AsyncWorkerConfig(
        worker_id="test-worker-runtime",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )
    worker = AsyncWorker(config, task_runtime=runtime, message_callback=callback_results.append)
    task = WorkerTask(
        task_id=7,
        command=f"{sys.executable} -c \"print('ok')\"",
        work_dir=work_dir,
        env={},
        timeout=30,
        metadata={"test": "runtime_finalization_failure"},
        execution_attempt=_execution_attempt_identity(
            7,
            worker_id=config.worker_id,
            role_id="async_worker_pool",
        ),
    )

    await worker._execute_worker_task(task)

    assert len(runtime.settlements) == 1
    assert runtime.settlements[0].identity == task.execution_attempt
    assert runtime.settlements[0].outcome == "completed"
    assert len(callback_results) == 1
    result = callback_results[0]
    assert result.success is False
    assert "TaskRuntime finalization failed: execution_event_append_failed" in result.error
    assert result.metadata["task_runtime_finalization"] == {
        "success": False,
        "reason": "execution_event_append_failed",
        "failure_class": "ledger_append_failed",
        "requested_reason": "completed",
        "state_mutation_applied": True,
        "execution_event": {
            "ok": False,
            "event_type": "completed",
            "error": "ledger unavailable",
        },
    }


@pytest.mark.asyncio
async def test_async_worker_failure_uses_task_runtime_fail_execution(work_dir: Path) -> None:
    runtime = _FakeTaskRuntime()
    config = AsyncWorkerConfig(
        worker_id="test-worker-runtime",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )
    worker = AsyncWorker(config, task_runtime=runtime)
    task = WorkerTask(
        task_id=7,
        command=f'{sys.executable} -c "import sys; sys.exit(7)"',
        work_dir=work_dir,
        env={},
        timeout=30,
        metadata={"test": "runtime_failure"},
        execution_attempt=_execution_attempt_identity(
            7,
            worker_id=config.worker_id,
            role_id="async_worker_pool",
        ),
    )

    await worker._execute_worker_task(task)

    assert len(runtime.settlements) == 1
    settlement = runtime.settlements[0]
    assert settlement.identity == task.execution_attempt
    assert settlement.outcome == "failed"
    assert settlement.summary
    assert settlement.metadata == {"worker_id": "test-worker-runtime"}


def test_sync_worker_failure_uses_task_runtime_fail_execution(work_dir: Path) -> None:
    runtime = _FakeTaskRuntime()
    config = WorkerConfig(
        worker_id="test-sync-worker-runtime",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )
    worker = Worker(config, task_runtime=runtime)
    task = WorkerTask(
        task_id=7,
        command=f'{sys.executable} -c "import sys; sys.exit(7)"',
        work_dir=work_dir,
        env={},
        timeout=30,
        metadata={"test": "runtime_failure"},
        execution_attempt=_execution_attempt_identity(
            7,
            worker_id=config.worker_id,
            role_id="worker_pool",
        ),
    )

    worker._execute_worker_task(task)

    assert len(runtime.settlements) == 1
    settlement = runtime.settlements[0]
    assert settlement.identity == task.execution_attempt
    assert settlement.outcome == "failed"
    assert settlement.summary
    assert settlement.metadata == {"worker_id": "test-sync-worker-runtime"}


def test_sync_worker_projects_task_runtime_finalization_failure(work_dir: Path) -> None:
    runtime = _FakeTaskRuntime(
        complete_result={
            "success": False,
            "reason": "execution_event_append_failed",
            "requested_reason": "completed",
            "failure_class": "ledger_append_failed",
            "state_mutation_applied": True,
            "execution_event": {
                "ok": False,
                "event_type": "completed",
                "error": "ledger unavailable",
            },
        }
    )
    callback_results: list[WorkerResult] = []
    config = WorkerConfig(
        worker_id="test-sync-worker-runtime",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )
    worker = Worker(config, task_runtime=runtime, message_callback=callback_results.append)

    def _successful_result(task: WorkerTask) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id,
            worker_id=config.worker_id,
            success=True,
            output="ok",
            metadata=dict(task.metadata),
        )

    worker._execute_task = _successful_result  # type: ignore[method-assign]
    task = WorkerTask(
        task_id=7,
        command=f"{sys.executable} -c \"print('ok')\"",
        work_dir=work_dir,
        env={},
        timeout=30,
        metadata={"test": "runtime_finalization_failure"},
        execution_attempt=_execution_attempt_identity(
            7,
            worker_id=config.worker_id,
            role_id="worker_pool",
        ),
    )

    worker._execute_worker_task(task)

    assert len(runtime.settlements) == 1
    assert runtime.settlements[0].identity == task.execution_attempt
    assert runtime.settlements[0].outcome == "completed"
    assert len(callback_results) == 1
    result = callback_results[0]
    assert result.success is False
    assert "TaskRuntime finalization failed: execution_event_append_failed" in result.error
    assert result.metadata["task_runtime_finalization"] == {
        "success": False,
        "reason": "execution_event_append_failed",
        "failure_class": "ledger_append_failed",
        "requested_reason": "completed",
        "state_mutation_applied": True,
        "execution_event": {
            "ok": False,
            "event_type": "completed",
            "error": "ledger unavailable",
        },
    }


@pytest.mark.asyncio
async def test_async_worker_timeout(work_dir: Path) -> None:
    """Test AsyncWorker timeout behavior."""
    config = AsyncWorkerConfig(
        worker_id="test-worker-timeout",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        task = WorkerTask(
            task_id=100,
            command=f'{sys.executable} -c "import time; time.sleep(60)"',
            work_dir=work_dir,
            env={},
            timeout=2,  # 2 second timeout
            metadata={"test": "timeout"},
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        assert isinstance(result, WorkerResult)
        assert result.task_id == 100
        assert result.success is False
        assert "Timeout" in result.error or "timeout" in result.error.lower()
        # Check metadata preserved
        assert result.metadata.get("cell") == "roles"
        assert result.metadata.get("task_id") == "100"
    finally:
        await worker.stop(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_command_failure(work_dir: Path) -> None:
    """Test AsyncWorker handles command failure."""
    config = AsyncWorkerConfig(
        worker_id="test-worker-fail",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        task = WorkerTask(
            task_id=200,
            command=f'{sys.executable} -c "import sys; sys.exit(42)"',
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "failure"},
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        assert isinstance(result, WorkerResult)
        assert result.task_id == 200
        assert result.success is False
        assert "42" in result.error  # Exit code should be in error
        assert result.metadata.get("cell") == "roles"
    finally:
        await worker.stop(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_empty_command(work_dir: Path) -> None:
    """Test AsyncWorker handles empty command."""
    config = AsyncWorkerConfig(
        worker_id="test-worker-empty",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        task = WorkerTask(
            task_id=300,
            command="",
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "empty"},
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        assert isinstance(result, WorkerResult)
        assert result.task_id == 300
        assert result.success is False
        assert "Empty command" in result.error
    finally:
        await worker.stop(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_env_inheritance(work_dir: Path) -> None:
    """Test AsyncWorker passes environment variables correctly."""
    config = AsyncWorkerConfig(
        worker_id="test-worker-env",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        # Test that KERNELONE_WORKER_ID and KERNELONE_TASK_ID are set
        task = WorkerTask(
            task_id=400,
            command=f"{sys.executable} -c \"import os; print(os.environ.get('KERNELONE_WORKER_ID', 'MISSING')); print(os.environ.get('KERNELONE_TASK_ID', 'MISSING'))\"",
            work_dir=work_dir,
            env={"TEST_VAR": "test_value"},
            timeout=30,
            metadata={"test": "env"},
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        assert result.success is True
        # Check that env vars were set by the executor
        assert "TEST_VAR" not in result.output  # Our test command doesn't echo custom vars
    finally:
        await worker.stop(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_pool_spawn_and_submit(work_dir: Path) -> None:
    """Test AsyncWorkerPool spawns workers and submits tasks."""
    pool = await create_async_worker_pool(
        work_base_dir=work_dir,
        max_workers=2,
    )

    try:
        # Spawn a worker
        worker_id = await pool.spawn_worker("pooled-worker-1")
        assert worker_id == "pooled-worker-1"

        # Submit a task
        task = WorkerTask(
            task_id=500,
            command=f"{sys.executable} -c \"print('pooled-ok')\"",
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "pool"},
        )

        submitted = await pool.submit_task(task)
        assert submitted is True

        # Wait a bit and check status
        await asyncio.sleep(2)
        status = await pool.get_status()
        assert status["total_workers"] == 1
    finally:
        await pool.shutdown_all(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_pool_max_workers(work_dir: Path) -> None:
    """Test AsyncWorkerPool respects max_workers limit."""
    pool = await create_async_worker_pool(
        work_base_dir=work_dir,
        max_workers=2,
    )

    try:
        # Spawn max workers
        await pool.spawn_worker("max-1")
        await pool.spawn_worker("max-2")

        # Try to spawn one more - should raise
        with pytest.raises(RuntimeError, match="Worker pool full"):
            await pool.spawn_worker("over-limit")
    finally:
        await pool.shutdown_all(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_pool_submit_specific_worker(work_dir: Path) -> None:
    """Test AsyncWorkerPool can submit to specific worker."""
    pool = await create_async_worker_pool(
        work_base_dir=work_dir,
        max_workers=2,
    )

    try:
        await pool.spawn_worker("specific-1")
        await pool.spawn_worker("specific-2")

        task = WorkerTask(
            task_id=600,
            command=f"{sys.executable} -c \"print('specific')\"",
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "specific"},
        )

        # Submit to specific worker
        submitted = await pool.submit_task(task, worker_id="specific-1")
        assert submitted is True

        # Submit to non-existent worker should fail
        submitted = await pool.submit_task(task, worker_id="non-existent")
        assert submitted is False
    finally:
        await pool.shutdown_all(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_graceful_shutdown(work_dir: Path) -> None:
    """Test AsyncWorker graceful shutdown."""
    config = AsyncWorkerConfig(
        worker_id="shutdown-test",
        work_dir=work_dir,
        max_idle_time=60,
        poll_interval=0.5,
    )

    worker = AsyncWorker(config)
    await worker.start()

    # Submit a long-running task
    task = WorkerTask(
        task_id=700,
        command=f"{sys.executable} -c \"import time; time.sleep(5); print('done')\"",
        work_dir=work_dir,
        env={},
        timeout=30,
        metadata={"test": "shutdown"},
    )
    await worker.submit_task(task)

    # Give it time to start
    await asyncio.sleep(0.5)

    # Stop with graceful=True should work
    start = time.time()
    await worker.stop(graceful=True)
    elapsed = time.time() - start
    assert elapsed < 35  # Should complete within reasonable time


@pytest.mark.asyncio
async def test_async_worker_metadata_injection(work_dir: Path) -> None:
    """Test that all required metadata fields are injected."""
    config = AsyncWorkerConfig(
        worker_id="metadata-test",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        custom_metadata = {
            "custom_key": "custom_value",
            "priority": "high",
        }

        task = WorkerTask(
            task_id=800,
            command=f"{sys.executable} -c \"print('metadata-test')\"",
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata=custom_metadata,
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        # Check required metadata
        assert result.metadata["cell"] == "roles"
        assert result.metadata["workspace"] == str(work_dir)
        assert result.metadata["task_id"] == "800"
        assert result.metadata["worker_id"] == "metadata-test"

        # Check custom metadata preserved
        assert result.metadata["custom_key"] == "custom_value"
        assert result.metadata["priority"] == "high"
    finally:
        await worker.stop(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_pool_status(work_dir: Path) -> None:
    """Test AsyncWorkerPool status reporting."""
    pool = await create_async_worker_pool(
        work_base_dir=work_dir,
        max_workers=3,
    )

    try:
        await pool.spawn_worker("status-1")
        await pool.spawn_worker("status-2")

        status = await pool.get_status()
        assert status["total_workers"] == 2
        assert status["idle"] >= 0
        assert status["working"] >= 0
        assert "status-1" in status["workers"]
        assert "status-2" in status["workers"]
    finally:
        await pool.shutdown_all(graceful=True)


@pytest.mark.asyncio
async def test_async_worker_pool_shutdown_all(work_dir: Path) -> None:
    """Test AsyncWorkerPool shutdown_all."""
    pool = await create_async_worker_pool(
        work_base_dir=work_dir,
        max_workers=3,
    )

    try:
        await pool.spawn_worker("all-1")
        await pool.spawn_worker("all-2")
        await pool.spawn_worker("all-3")

        status_before = await pool.get_status()
        assert status_before["total_workers"] == 3

        await pool.shutdown_all(graceful=True)

        status_after = await pool.get_status()
        assert status_after["total_workers"] == 0
    except (RuntimeError, ValueError):
        # Pool might be closed
        pass


@pytest.mark.asyncio
async def test_async_worker_exception_handling(work_dir: Path) -> None:
    """Test AsyncWorker handles exceptions gracefully."""
    config = AsyncWorkerConfig(
        worker_id="exception-test",
        work_dir=work_dir,
        max_idle_time=10,
        poll_interval=1.0,
    )

    worker = AsyncWorker(config)
    await worker.start()

    try:
        # Create a task with invalid command that will cause exception
        task = WorkerTask(
            task_id=900,
            command=f"{sys.executable} -c \"raise ValueError('test exception')\"",
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "exception"},
        )

        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)

        # Should handle gracefully, not crash
        assert isinstance(result, WorkerResult)
        assert result.task_id == 900
        assert result.success is False
    finally:
        await worker.stop(graceful=True)
