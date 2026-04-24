"""Tests for worker_pool_service execution_broker integration."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.director.tasking.internal.worker_pool_service import (
    WorkerPoolConfig,
    WorkerService,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessStatusV1,
    ExecutionProcessWaitResultV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.domain.entities import Task, TaskResult, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path


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
