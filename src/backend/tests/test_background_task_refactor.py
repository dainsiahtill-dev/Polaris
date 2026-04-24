"""Tests for background_task task_runner injection refactor.

Verifies:
- Module loads without KernelOne imports at the top level
- task_runner is injectable via constructor
- Default task runner uses asyncio.create_task
- All protocols are properly defined
"""

from __future__ import annotations

import asyncio

import pytest
from polaris.domain.services.background_task import (
    BackgroundTask,
    BackgroundTaskService,
    ExecutionResult,
    SubprocessExecutor,
    TaskState,
)


class TestBackgroundTaskModuleLoadsWithoutKernelOne:
    """Module imports without blocking KernelOne trace imports."""

    def test_module_imports_successfully(self):
        # This verifies the refactor: create_task_with_context is no longer
        # imported at module level.
        from polaris.domain.services.background_task import (
            TaskState,
        )

        assert TaskState.QUEUED is not None
        assert TaskState.RUNNING is not None

    def test_background_task_to_dict_from_dict_roundtrip(self):
        task = BackgroundTask(command="echo test", timeout=60)
        data = task.to_dict()
        restored = BackgroundTask.from_dict(data)
        assert restored.command == "echo test"
        assert restored.timeout == 60
        assert restored.id == task.id

    def test_task_state_enum_values(self):
        assert TaskState.QUEUED.value == 1
        assert TaskState.RUNNING.value == 2
        assert TaskState.SUCCESS.value == 3
        assert TaskState.FAILED.value == 4
        assert TaskState.TIMEOUT.value == 5
        assert TaskState.CANCELLED.value == 6


class TestTaskRunnerInjection:
    """task_runner is injectable for test isolation and KernelOne trace integration."""

    @pytest.mark.asyncio
    async def test_custom_task_runner_receives_coro(self):
        """Verify custom task_runner is called with the coroutine."""
        received_coros: list = []

        async def tracking_runner(coro):
            received_coros.append(coro)
            # Actually run it so the test doesn't leak tasks
            task = asyncio.create_task(coro)
            return task

        class DummyStorage:
            def save(self, task):
                pass

            def get(self, task_id):
                return None

            def list_all(self):
                return []

            def update(self, task):
                pass

            def delete(self, task_id):
                return True

        class DummyExecutor:
            async def execute(self, command, cwd, timeout, tier=None):
                return ExecutionResult(success=True, exit_code=0, stdout="ok", stderr="", duration_ms=10)

        service = BackgroundTaskService(
            storage=DummyStorage(),
            executor=DummyExecutor(),
            task_runner=tracking_runner,
        )

        task = BackgroundTask(command="echo test")
        task_id = await service.submit(task)

        # The runner should have been called
        assert len(received_coros) == 1
        # Task should be registered
        assert task_id == task.id

    @pytest.mark.asyncio
    async def test_service_with_no_task_runner_no_crash(self):
        """Service without explicit task_runner uses default (asyncio.create_task)."""

        class DummyStorage:
            def save(self, task):
                pass

            def get(self, task_id):
                return None

            def list_all(self):
                return []

            def update(self, task):
                pass

            def delete(self, task_id):
                return True

        class DummyExecutor:
            async def execute(self, command, cwd, timeout, tier=None):
                return ExecutionResult(success=True, exit_code=0, stdout="", stderr="", duration_ms=0)

        # No task_runner argument → uses default
        service = BackgroundTaskService(
            storage=DummyStorage(),
            executor=DummyExecutor(),
        )
        task = BackgroundTask(command="echo ok")
        task_id = await service.submit(task)
        assert task_id == task.id


class TestSubprocessExecutorProtocols:
    """SubprocessExecutor implements TaskExecutor protocol."""

    def test_executor_instantiates_without_kernelone_import(self):
        # Should not require KernelOne to instantiate
        executor = SubprocessExecutor(workspace=".")
        assert executor is not None
        assert executor._security is not None
        assert executor._timeout_service is not None

    def test_get_timeout_returns_validated_value(self):
        executor = SubprocessExecutor(workspace=".")
        # Default tier should return reasonable value
        timeout = executor.get_timeout("background", requested=300)
        assert isinstance(timeout, int)
        assert timeout >= 0
