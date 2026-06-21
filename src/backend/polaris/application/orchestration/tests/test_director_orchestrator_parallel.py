"""Tests for DirectorOrchestrator parallel execution mode."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.application.orchestration.director_orchestrator import (
    DirectorExecutionConfig,
    DirectorOrchestrator,
)


class _FakeTask:
    """Fake task object with to_dict method."""

    def __init__(self, task_id: str, subject: str, title: str = "") -> None:
        self.id = task_id
        self.subject = subject
        self.title = title or subject

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "title": self.title,
        }


class _FakeTaskBoard:
    """Fake task board that returns configurable ready tasks."""

    def __init__(self, ready_tasks: list[dict[str, Any]] | None = None) -> None:
        self._ready_tasks = [_FakeTask(**t) for t in (ready_tasks or [])]
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def get_ready_tasks(self) -> list[_FakeTask]:
        return self._ready_tasks

    def update(self, task_id: str, **kwargs: Any) -> None:
        self.updates.append((task_id, dict(kwargs)))


class _FakeDirectorAdapter:
    """Fake director adapter with configurable delay and results."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        success: bool = True,
        error: str = "",
    ) -> None:
        self.delay = delay
        self.success = success
        self.error = error
        self.call_count = 0
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.call_count += 1
        self.calls.append((task_id, input_data, context))
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if not self.success:
            return {
                "success": False,
                "task_id": task_id,
                "error": self.error or "fake error",
                "error_code": "FAKE_ERROR",
            }
        return {
            "success": True,
            "task_id": task_id,
            "changed_files": ["src/test.py"],
            "qa_required_for_final_verdict": True,
        }


@pytest.fixture
def sample_tasks() -> list[dict[str, Any]]:
    """Return a list of sample tasks for testing."""
    return [
        {"task_id": "task-1", "subject": "Task 1", "title": "Task 1"},
        {"task_id": "task-2", "subject": "Task 2", "title": "Task 2"},
        {"task_id": "task-3", "subject": "Task 3", "title": "Task 3"},
    ]


@pytest.mark.asyncio
async def test_director_orchestrator_parallel_execution(
    tmp_path: Any,
    sample_tasks: list[dict[str, Any]],
) -> None:
    """Test that parallel mode executes tasks concurrently."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="parallel",
        max_workers=3,
    )
    orchestrator = DirectorOrchestrator(config=config)

    fake_board = _FakeTaskBoard(ready_tasks=sample_tasks)
    fake_adapter = _FakeDirectorAdapter(delay=0.1)

    with (
        patch.object(orchestrator, "_get_task_board", return_value=fake_board),
        patch(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            return_value=fake_adapter,
        ),
    ):
        result = await orchestrator.run_iteration(iteration=1)

    assert result.success is True
    assert result.tasks_processed == 3
    assert result.tasks_succeeded == 3
    assert result.tasks_failed == 0
    assert fake_adapter.call_count == 3


@pytest.mark.asyncio
async def test_director_orchestrator_serial_execution(
    tmp_path: Any,
    sample_tasks: list[dict[str, Any]],
) -> None:
    """Test that serial mode executes tasks one by one."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="serial",
        max_workers=3,
    )
    orchestrator = DirectorOrchestrator(config=config)

    fake_board = _FakeTaskBoard(ready_tasks=sample_tasks)
    fake_adapter = _FakeDirectorAdapter(delay=0.01)

    with (
        patch.object(orchestrator, "_get_task_board", return_value=fake_board),
        patch(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            return_value=fake_adapter,
        ),
    ):
        result = await orchestrator.run_iteration(iteration=1)

    assert result.success is True
    assert result.tasks_processed == 1  # serial mode processes 1 task at a time
    assert result.tasks_succeeded == 1
    assert result.tasks_failed == 0
    assert fake_adapter.call_count == 1


@pytest.mark.asyncio
async def test_director_orchestrator_parallel_execution_with_failure(
    tmp_path: Any,
    sample_tasks: list[dict[str, Any]],
) -> None:
    """Test that parallel mode handles task failures correctly."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="parallel",
        max_workers=3,
    )
    orchestrator = DirectorOrchestrator(config=config)

    fake_board = _FakeTaskBoard(ready_tasks=sample_tasks)
    fake_adapter = _FakeDirectorAdapter(success=False, error="test error")

    with (
        patch.object(orchestrator, "_get_task_board", return_value=fake_board),
        patch(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            return_value=fake_adapter,
        ),
    ):
        result = await orchestrator.run_iteration(iteration=1)

    assert result.success is True
    assert result.tasks_processed == 3
    assert result.tasks_succeeded == 0
    assert result.tasks_failed == 3


@pytest.mark.asyncio
async def test_director_orchestrator_parallel_execution_with_exception(
    tmp_path: Any,
    sample_tasks: list[dict[str, Any]],
) -> None:
    """Test that parallel mode handles exceptions correctly."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="parallel",
        max_workers=3,
    )
    orchestrator = DirectorOrchestrator(config=config)

    fake_board = _FakeTaskBoard(ready_tasks=sample_tasks)

    call_count = 0

    async def failing_execute(
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Test exception")
        return {
            "success": True,
            "task_id": task_id,
            "changed_files": ["src/test.py"],
        }

    fake_adapter = MagicMock()
    fake_adapter.execute = failing_execute

    with (
        patch.object(orchestrator, "_get_task_board", return_value=fake_board),
        patch(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            return_value=fake_adapter,
        ),
    ):
        result = await orchestrator.run_iteration(iteration=1)

    assert result.success is True
    assert result.tasks_processed == 3
    assert result.tasks_succeeded == 2
    assert result.tasks_failed == 1


@pytest.mark.asyncio
async def test_director_orchestrator_no_ready_tasks(
    tmp_path: Any,
) -> None:
    """Test that orchestrator handles no ready tasks correctly."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="parallel",
        max_workers=3,
    )
    orchestrator = DirectorOrchestrator(config=config)

    fake_board = _FakeTaskBoard(ready_tasks=[])

    with patch.object(orchestrator, "_get_task_board", return_value=fake_board):
        result = await orchestrator.run_iteration(iteration=1)

    assert result.success is True
    assert result.tasks_processed == 0
    assert result.tasks_succeeded == 0
    assert result.tasks_failed == 0
    assert result.notes == "No ready tasks"


@pytest.mark.asyncio
async def test_director_orchestrator_parallel_batch_size_limit(
    tmp_path: Any,
    sample_tasks: list[dict[str, Any]],
) -> None:
    """Test that parallel mode respects max_workers batch size."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="parallel",
        max_workers=2,
    )
    orchestrator = DirectorOrchestrator(config=config)

    fake_board = _FakeTaskBoard(ready_tasks=sample_tasks)
    fake_adapter = _FakeDirectorAdapter()

    with (
        patch.object(orchestrator, "_get_task_board", return_value=fake_board),
        patch(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            return_value=fake_adapter,
        ),
    ):
        result = await orchestrator.run_iteration(iteration=1)

    assert result.success is True
    assert result.tasks_processed == 2  # limited by max_workers
    assert result.tasks_succeeded == 2
    assert result.tasks_failed == 0
    assert fake_adapter.call_count == 2


@pytest.mark.asyncio
async def test_director_orchestrator_execution_mode_default(
    tmp_path: Any,
) -> None:
    """Test that default execution mode is parallel."""
    config = DirectorExecutionConfig(workspace=str(tmp_path))
    assert config.execution_mode == "parallel"


@pytest.mark.asyncio
async def test_director_orchestrator_execution_mode_validation(
    tmp_path: Any,
) -> None:
    """Test that invalid execution mode defaults to parallel."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        execution_mode="invalid",
    )
    assert config.execution_mode == "parallel"


@pytest.mark.asyncio
async def test_director_orchestrator_max_workers_validation(
    tmp_path: Any,
) -> None:
    """Test that max_workers is validated to be at least 1."""
    config = DirectorExecutionConfig(
        workspace=str(tmp_path),
        max_workers=0,
    )
    assert config.max_workers == 1
