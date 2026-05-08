"""Tests for worker_pool_service execution_broker integration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.director.tasking.internal.worker_pool_service import (
    WorkerPoolConfig,
    WorkerService,
    _task_result_from_process,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessStatusV1,
    ExecutionProcessWaitResultV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.domain.entities import Task, TaskResult, TaskStatus, WorkerStatus
from polaris.kernelone.runtime import ExecutionLane, ExecutionSnapshot, ExecutionStatus

if TYPE_CHECKING:
    from pathlib import Path


def _process_snapshot(
    *,
    stdout_lines: tuple[str, ...] = (),
    stderr_lines: tuple[str, ...] = (),
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
) -> ExecutionSnapshot:
    """Build a minimal subprocess snapshot for result parsing tests."""
    return ExecutionSnapshot(
        execution_id="exec-test",
        name="director-task-test",
        lane=ExecutionLane.SUBPROCESS,
        status=status,
        submitted_at=datetime.now(timezone.utc),
        timeout_seconds=60.0,
        metadata={},
        result={
            "exit_code": 0,
            "stdout_lines": stdout_lines,
            "stderr_lines": stderr_lines,
        },
        pid=12345,
    )


@pytest.fixture
def mock_task_service():
    """Create a mock task service."""
    service = MagicMock()
    service.get_next_ready_task = AsyncMock(return_value=None)
    service.get_task = AsyncMock(return_value=None)
    service.on_task_claimed = AsyncMock(return_value=True)
    service.on_task_started = AsyncMock(return_value=True)
    service.on_task_completed = AsyncMock(return_value=True)
    service.on_task_failed = AsyncMock(return_value=True)
    return service


@pytest.fixture
def worker_service(mock_task_service, tmp_path: Path):
    """Create a WorkerService instance for testing."""
    config = WorkerPoolConfig(min_workers=1, max_workers=4)
    return WorkerService(
        config=config,
        workspace=str(tmp_path),
        task_service=mock_task_service,
    )


class TestWorkerServiceExecutionBroker:
    """Tests for execution_broker integration in WorkerService."""

    @pytest.mark.asyncio
    async def test_worker_service_initialization(self, worker_service: WorkerService) -> None:
        """Test that worker service initializes correctly."""
        assert worker_service is not None
        assert worker_service.config.min_workers == 1
        assert worker_service.config.max_workers == 4

    @pytest.mark.asyncio
    async def test_spawn_worker(self, worker_service: WorkerService) -> None:
        """Test that spawning a worker works."""
        worker = await worker_service.spawn_worker()
        assert worker is not None
        assert worker.id.startswith("worker-")

        # Cleanup
        await worker_service.shutdown()

    @pytest.mark.asyncio
    async def test_busy_worker_is_not_restarted_for_stale_loop_heartbeat(
        self,
        mock_task_service: MagicMock,
        tmp_path: Path,
    ) -> None:
        """An active task is bounded by task timeout, not the idle-loop heartbeat."""
        service = WorkerService(
            config=WorkerPoolConfig(min_workers=0, max_workers=1, heartbeat_timeout_seconds=1),
            workspace=str(tmp_path),
            task_service=mock_task_service,
        )
        worker = await service.spawn_worker()
        assert worker is not None

        worker.status = WorkerStatus.BUSY
        worker.current_task_id = "task-active"
        worker.health = worker.health.with_updates(
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=120),
        )

        try:
            assert await service.check_health() == [(worker.id, True, "")]
            assert await service.handle_failed_workers() == []
            assert await service.get_worker(worker.id) is worker
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_destroy_worker_tolerates_already_cleaned_worker_task(
        self,
        worker_service: WorkerService,
    ) -> None:
        """Worker cleanup may race with explicit destruction; destruction stays idempotent."""
        worker = await worker_service.spawn_worker()
        assert worker is not None
        task = worker_service._worker_tasks.pop(worker.id)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert await worker_service.destroy_worker(worker.id) is True
        assert await worker_service.get_worker(worker.id) is None

    def test_execution_broker_imports(self) -> None:
        """Test that execution_broker imports are available."""
        from polaris.cells.runtime.execution_broker.public.contracts import (
            LaunchExecutionProcessCommandV1,
        )
        from polaris.cells.runtime.execution_broker.public.service import get_execution_broker_service

        assert LaunchExecutionProcessCommandV1 is not None
        assert get_execution_broker_service is not None

    def test_worker_service_has_execution_broker_imports(self) -> None:
        """Test that worker_pool_service imports execution_broker correctly."""
        # This verifies the module can be imported with execution_broker
        from polaris.cells.director.tasking.internal.worker_pool_service import (
            LaunchExecutionProcessCommandV1,
            get_execution_broker_service,
        )

        assert LaunchExecutionProcessCommandV1 is not None
        assert get_execution_broker_service is not None

    @pytest.mark.asyncio
    async def test_execution_broker_command_building(self, tmp_path: Path) -> None:
        """Test that LaunchExecutionProcessCommandV1 is built correctly."""
        # This is a unit test for the command building logic
        task = Task(
            id="task-unit-test",
            subject="Unit test task",
            description="Testing command building",
            timeout_seconds=60,
        )

        # Simulate what the worker_loop does
        {
            "workspace": str(tmp_path),
            "worker_id": "worker-unit-test",
            "task": task.to_dict(),
        }

        command = LaunchExecutionProcessCommandV1(
            name=f"director-task-{task.id}",
            args=(
                sys.executable,
                "-m",
                "polaris.cells.director.tasking.internal.task_execution_runner",
            ),
            workspace=str(tmp_path),
            timeout_seconds=task.timeout_seconds or 300.0,
            env={"TEST_MODE": "1"},
            stdin_input=None,  # Would be json.dumps(task_input) in real code
            metadata={
                "cell": "director",
                "task_id": str(task.id),
                "worker_id": "worker-unit-test",
            },
        )

        assert command.name == "director-task-task-unit-test"
        assert command.timeout_seconds == 60.0
        assert command.metadata["cell"] == "director"
        assert command.metadata["task_id"] == "task-unit-test"
        assert command.metadata["worker_id"] == "worker-unit-test"

    @pytest.mark.asyncio
    async def test_task_to_dict_integration(self) -> None:
        """Test that Task.to_dict() produces valid serializable data."""
        task = Task(
            id="task-serialize-test",
            subject="Serialization test",
            description="Testing task serialization",
            status=TaskStatus.PENDING,
            timeout_seconds=120,
            metadata={"key": "value"},
        )

        task_dict = task.to_dict()
        assert isinstance(task_dict, dict)
        assert task_dict["id"] == "task-serialize-test"
        assert task_dict["subject"] == "Serialization test"
        assert task_dict["status"] == "pending"
        assert task_dict["timeout_seconds"] == 120
        assert task_dict["metadata"]["key"] == "value"

    @pytest.mark.asyncio
    async def test_task_result_creation(self) -> None:
        """Test that TaskResult can be created from execution_broker results."""
        # Simulate what worker_loop does with execution_broker results
        wait_result = ExecutionProcessWaitResultV1(
            handle=ExecutionProcessHandleV1(
                execution_id="exec-test",
                pid=12345,
                name="director-task-test",
                workspace=".",
            ),
            status=ExecutionProcessStatusV1.SUCCESS,
            success=True,
            exit_code=0,
        )

        result = TaskResult(
            success=wait_result.success,
            output="Generated 5 files",
            exit_code=wait_result.exit_code or 0,
            duration_ms=1500,
            evidence=(),
            error=None,
        )

        assert result.success is True
        assert result.exit_code == 0
        assert result.output == "Generated 5 files"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_task_result_from_failed_execution(self) -> None:
        """Test TaskResult creation from failed execution."""
        wait_result = ExecutionProcessWaitResultV1(
            handle=ExecutionProcessHandleV1(
                execution_id="exec-fail",
                pid=12345,
                name="director-task-fail",
                workspace=".",
            ),
            status=ExecutionProcessStatusV1.FAILED,
            success=False,
            exit_code=1,
            error_message="Subprocess failed",
        )

        result = TaskResult(
            success=wait_result.success,
            output="",
            exit_code=wait_result.exit_code or 0,
            duration_ms=100,
            evidence=(),
            error=wait_result.error_message,
        )

        assert result.success is False
        assert result.exit_code == 1
        assert result.error == "Subprocess failed"

    @pytest.mark.asyncio
    async def test_task_result_from_timeout(self) -> None:
        """Test TaskResult creation from timed out execution."""
        wait_result = ExecutionProcessWaitResultV1(
            handle=ExecutionProcessHandleV1(
                execution_id="exec-timeout",
                pid=12345,
                name="director-task-timeout",
                workspace=".",
            ),
            status=ExecutionProcessStatusV1.TIMED_OUT,
            success=False,
            exit_code=None,
            timed_out=True,
            error_message="Execution timed out",
        )

        result = TaskResult(
            success=wait_result.success,
            output="",
            exit_code=wait_result.exit_code or 0,
            duration_ms=0,
            evidence=(),
            error=wait_result.error_message,
        )

        assert result.success is False
        assert result.error == "Execution timed out"

    def test_task_result_from_process_preserves_stdout_business_failure(self) -> None:
        """A clean subprocess exit can still carry a failed Director task result."""
        wait_result = ExecutionProcessWaitResultV1(
            handle=ExecutionProcessHandleV1(
                execution_id="exec-business-failure",
                pid=12345,
                name="director-task-business-failure",
                workspace=".",
            ),
            status=ExecutionProcessStatusV1.SUCCESS,
            success=True,
            exit_code=0,
        )
        runner_result = TaskResult(
            success=False,
            output="code generation refused",
            exit_code=0,
            duration_ms=42,
            error="SECURITY POLICY VIOLATION",
        )

        result = _task_result_from_process(
            wait_result,
            _process_snapshot(
                stdout_lines=(
                    "runner booted",
                    json.dumps(runner_result.to_dict(), ensure_ascii=False),
                )
            ),
        )

        assert result.success is False
        assert result.output == "code generation refused"
        assert result.duration_ms == 42
        assert result.error == "SECURITY POLICY VIOLATION"

    def test_task_result_from_process_falls_back_to_broker_failure(self) -> None:
        """Missing TaskResult JSON still returns a concrete process failure."""
        wait_result = ExecutionProcessWaitResultV1(
            handle=ExecutionProcessHandleV1(
                execution_id="exec-process-failure",
                pid=12345,
                name="director-task-process-failure",
                workspace=".",
            ),
            status=ExecutionProcessStatusV1.FAILED,
            success=False,
            exit_code=1,
            error_message=None,
        )

        result = _task_result_from_process(
            wait_result,
            _process_snapshot(
                stdout_lines=("not json",),
                stderr_lines=("Traceback omitted", "subprocess exploded"),
                status=ExecutionStatus.FAILED,
            ),
        )

        assert result.success is False
        assert result.exit_code == 1
        assert result.output == "not json"
        assert result.error == "Traceback omitted\nsubprocess exploded"


class TestWorkerPoolConfig:
    """Tests for WorkerPoolConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = WorkerPoolConfig()
        assert config.min_workers == 1
        assert config.max_workers >= 4
        assert config.max_consecutive_failures == 3
        assert config.heartbeat_timeout_seconds == 60
        assert config.enable_auto_scaling is True

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = WorkerPoolConfig(
            min_workers=2,
            max_workers=16,
            max_consecutive_failures=5,
            heartbeat_timeout_seconds=120,
            enable_auto_scaling=False,
        )
        assert config.min_workers == 2
        assert config.max_workers == 16
        assert config.max_consecutive_failures == 5
        assert config.heartbeat_timeout_seconds == 120
        assert config.enable_auto_scaling is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
