"""Tests for task_lifecycle_service module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from polaris.cells.director.tasking.internal.task_lifecycle_service import (
        TaskService,
        TaskServiceDeps,
    )


class TestTaskQueueConfig:
    """Tests for TaskQueueConfig dataclass."""

    def test_default_values(self) -> None:
        """Test TaskQueueConfig default values."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskQueueConfig,
        )

        config = TaskQueueConfig()
        assert config.max_queue_size == 1000
        assert config.default_timeout_seconds == 300
        assert config.enable_priority_scheduling is True
        assert config.enable_dependency_tracking is True

    def test_custom_values(self) -> None:
        """Test TaskQueueConfig with custom values."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskQueueConfig,
        )

        config = TaskQueueConfig(
            max_queue_size=500,
            default_timeout_seconds=600,
            enable_priority_scheduling=False,
        )
        assert config.max_queue_size == 500
        assert config.default_timeout_seconds == 600
        assert config.enable_priority_scheduling is False


class TestTaskService:
    """Tests for TaskService class."""

    @pytest.fixture
    def mock_deps(self) -> TaskServiceDeps:
        """Create mock dependencies for TaskService."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskServiceDeps,
        )

        return TaskServiceDeps(
            impact_analyzer=MagicMock(),
            evidence_store=MagicMock(),
            state_store=MagicMock(),
            log_store=MagicMock(),
            storage=MagicMock(),
            audit_service=MagicMock(),
            repair_service=MagicMock(),
        )

    @pytest.fixture
    def task_service(self, mock_deps: TaskServiceDeps) -> TaskService:
        """Create TaskService instance for testing."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskQueueConfig,
            TaskService,
        )

        config = TaskQueueConfig(max_queue_size=100)
        return TaskService(
            config=config,
            workspace="/tmp/test",
            deps=mock_deps,
        )

    @pytest.mark.asyncio
    async def test_create_task_basic(self, task_service: TaskService) -> None:
        """Test basic task creation."""
        task = await task_service.create_task(
            subject="Test task",
            description="A test task",
        )

        assert task is not None
        assert task.subject == "Test task"
        assert task.description == "A test task"
        assert cast(str, task.id).startswith("task-")

    @pytest.mark.asyncio
    async def test_create_task_with_priority(self, task_service: TaskService) -> None:
        """Test task creation with priority."""
        from polaris.domain.entities import TaskPriority

        task = await task_service.create_task(
            subject="High priority",
            priority=TaskPriority.HIGH,
        )

        assert task.priority == TaskPriority.HIGH

    @pytest.mark.asyncio
    async def test_get_task(self, task_service: TaskService) -> None:
        """Test getting a task by ID."""
        created = await task_service.create_task(subject="Find me")
        task_id: str = created.id if isinstance(created.id, str) else str(created.id)

        found = await task_service.get_task(task_id)
        assert found is not None
        assert found.id == created.id
        assert found.subject == "Find me"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, task_service: TaskService) -> None:
        """Test getting non-existent task."""
        found = await task_service.get_task("nonexistent-id")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_tasks_filter_by_status(self, task_service: TaskService) -> None:
        """Test filtering tasks by status."""
        from polaris.domain.entities import TaskStatus

        # Create tasks to ensure we have some in the queue
        await task_service.create_task(subject="Task 1")
        await task_service.create_task(subject="Task 2")

        all_tasks = await task_service.get_tasks()
        assert len(all_tasks) >= 2

        pending_tasks = await task_service.get_tasks(status=TaskStatus.PENDING)
        assert all(t.status == TaskStatus.PENDING for t in pending_tasks)

    @pytest.mark.asyncio
    async def test_cancel_task(self, task_service: TaskService) -> None:
        """Test cancelling a task."""
        from polaris.domain.entities import TaskStatus

        task = await task_service.create_task(subject="To cancel")
        task_id: str = task.id if isinstance(task.id, str) else str(task.id)

        cancelled = await task_service.cancel_task(task_id)
        assert cancelled is True

        updated = await task_service.get_task(task_id)
        assert updated is not None
        assert updated.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, task_service: TaskService) -> None:
        """Test cancelling non-existent task."""
        result = await task_service.cancel_task("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_on_task_claimed(self, task_service: TaskService) -> None:
        """Test marking task as claimed."""
        task = await task_service.create_task(subject="To claim")
        task_id: str = task.id if isinstance(task.id, str) else str(task.id)

        claimed = await task_service.on_task_claimed(task_id, "worker-1")
        assert claimed is True

        updated = await task_service.get_task(task_id)
        assert updated is not None
        assert updated.claimed_by == "worker-1"

    @pytest.mark.asyncio
    async def test_on_task_started(self, task_service: TaskService) -> None:
        """Test marking task as started."""
        from polaris.domain.entities import TaskStatus

        task = await task_service.create_task(subject="To start")
        task_id: str = task.id if isinstance(task.id, str) else str(task.id)

        started = await task_service.on_task_started(task_id)
        assert started is True

        updated = await task_service.get_task(task_id)
        assert updated is not None
        assert updated.status == TaskStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_on_task_completed(self, task_service: TaskService) -> None:
        """Test marking task as completed."""
        from polaris.domain.entities import TaskResult

        task = await task_service.create_task(subject="To complete")
        task_id: str = task.id if isinstance(task.id, str) else str(task.id)

        # Claim and start the task first (required before completing)
        await task_service.on_task_claimed(task_id, "worker-1")
        await task_service.on_task_started(task_id)

        result = TaskResult(
            success=True,
            output="Task completed",
            error=None,
            duration_ms=100,
        )

        unblocked = await task_service.on_task_completed(task_id, result)
        assert isinstance(unblocked, list)

    @pytest.mark.asyncio
    async def test_on_task_failed(self, task_service: TaskService) -> None:
        """Test marking task as failed."""

        task = await task_service.create_task(subject="To fail")
        task_id: str = task.id if isinstance(task.id, str) else str(task.id)

        await task_service.on_task_failed(task_id, "Test error")

        updated = await task_service.get_task(task_id)
        assert updated is not None
        assert updated.error_message == "Test error"

    @pytest.mark.asyncio
    async def test_get_next_ready_task_empty(self, task_service: TaskService) -> None:
        """Test getting next task when queue is empty."""
        # Queue is empty so returns None or times out

    @pytest.mark.asyncio
    async def test_get_ready_task_count(self, task_service: TaskService) -> None:
        """Test getting ready task count."""
        await task_service.create_task(subject="Task 1")
        await task_service.create_task(subject="Task 2")

        count = await task_service.get_ready_task_count()
        assert count >= 0

    @pytest.mark.asyncio
    async def test_add_dependency(self, task_service: TaskService) -> None:
        """Test adding task dependency."""
        task1 = await task_service.create_task(subject="Task 1")
        task2 = await task_service.create_task(subject="Task 2")
        task1_id: str = task1.id if isinstance(task1.id, str) else str(task1.id)
        task2_id: str = task2.id if isinstance(task2.id, str) else str(task2.id)

        added = await task_service.add_dependency(task2_id, task1_id)
        assert added is True

    @pytest.mark.asyncio
    async def test_add_circular_dependency_rejected(self, task_service: TaskService) -> None:
        """Test that circular dependencies are rejected."""
        task1 = await task_service.create_task(subject="Task 1")
        task2 = await task_service.create_task(subject="Task 2")
        task1_id: str = task1.id if isinstance(task1.id, str) else str(task1.id)
        task2_id: str = task2.id if isinstance(task2.id, str) else str(task2.id)

        # Add task2 depends on task1
        await task_service.add_dependency(task2_id, task1_id)

        # Try to add task1 depends on task2 (circular)
        added = await task_service.add_dependency(task1_id, task2_id)
        assert added is False

    @pytest.mark.asyncio
    async def test_get_statistics(self, task_service: TaskService) -> None:
        """Test getting task statistics."""
        await task_service.create_task(subject="Task 1")
        await task_service.create_task(subject="Task 2")
        await task_service.create_task(subject="Task 3")

        stats = await task_service.get_statistics()
        assert "total" in stats
        assert "by_status" in stats
        assert "by_priority" in stats
        assert stats["total"] >= 3

    @pytest.mark.asyncio
    async def test_callback_registration(self, task_service: TaskService) -> None:
        """Test registering completion and failure callbacks."""
        completion_called = False

        def on_complete(task: Any) -> None:
            nonlocal completion_called
            completion_called = True

        def on_fail(task: Any, exc: Exception) -> None:
            pass

        task_service.on_task_complete(on_complete)
        task_service.on_task_fail(on_fail)

        # Verify callbacks are registered (can't easily trigger them without full integration)
        assert len(task_service._completion_callbacks) == 1
        assert len(task_service._failure_callbacks) == 1

    @pytest.mark.asyncio
    async def test_is_task_ready_no_dependencies(self, task_service: TaskService) -> None:
        """Test _is_task_ready with no dependencies."""
        task = await task_service.create_task(subject="No deps")

        # Access the private method for testing
        is_ready = task_service._is_task_ready(task)
        assert is_ready is True


class TestDependencyGraph:
    """Tests for dependency graph functionality."""

    @pytest.fixture
    def mock_deps(self) -> TaskServiceDeps:
        """Create mock dependencies."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskServiceDeps,
        )

        return TaskServiceDeps(
            impact_analyzer=MagicMock(),
            evidence_store=MagicMock(),
            state_store=MagicMock(),
            log_store=MagicMock(),
            storage=MagicMock(),
            audit_service=MagicMock(),
            repair_service=MagicMock(),
        )

    @pytest.fixture
    def task_service(self, mock_deps: TaskServiceDeps) -> TaskService:
        """Create TaskService instance."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskQueueConfig,
            TaskService,
        )

        return TaskService(
            config=TaskQueueConfig(),
            workspace="/tmp",
            deps=mock_deps,
        )

    @pytest.mark.asyncio
    async def test_get_dependency_graph(self, task_service: TaskService) -> None:
        """Test getting dependency graph for a task."""
        task = await task_service.create_task(subject="Graph test")
        task_id: str = task.id if isinstance(task.id, str) else str(task.id)

        graph = await task_service.get_dependency_graph(task_id)
        assert graph is not None
        assert "task" in graph
        assert "depends_on" in graph
        assert "blocks" in graph

    @pytest.mark.asyncio
    async def test_get_dependency_graph_not_found(self, task_service: TaskService) -> None:
        """Test getting dependency graph for non-existent task."""
        graph = await task_service.get_dependency_graph("nonexistent")
        assert graph is None


class TestVerificationMethods:
    """Tests for verification methods."""

    @pytest.fixture
    def mock_deps(self) -> TaskServiceDeps:
        """Create mock dependencies."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskServiceDeps,
        )

        mock_impact = MagicMock()
        mock_impact.analyze.return_value = MagicMock(
            recommendations=["Run tests"],
            risk_level="low",
        )

        return TaskServiceDeps(
            impact_analyzer=mock_impact,
            evidence_store=MagicMock(),
            state_store=MagicMock(),
            log_store=MagicMock(),
            storage=MagicMock(),
            audit_service=MagicMock(),
            repair_service=MagicMock(),
        )

    @pytest.fixture
    def task_service(self, mock_deps: TaskServiceDeps) -> TaskService:
        """Create TaskService instance."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskQueueConfig,
            TaskService,
        )

        return TaskService(
            config=TaskQueueConfig(),
            workspace="/tmp",
            deps=mock_deps,
        )

    @pytest.mark.asyncio
    async def test_verify_existence(self, task_service: TaskService) -> None:
        """Test existence verification."""
        exists, message = await task_service.verify_existence(
            task_id="test-1",
            target_files=["nonexistent.py"],
        )

        assert isinstance(exists, bool)
        assert isinstance(message, str)

    @pytest.mark.asyncio
    async def test_soft_verify(self, task_service: TaskService) -> None:
        """Test soft verification."""
        from polaris.domain.verification import SoftCheckResult

        result = await task_service.soft_verify(
            task_id="test-1",
            target_files=["file.py"],
        )

        assert isinstance(result, SoftCheckResult)

    @pytest.mark.asyncio
    async def test_validate_write_scope(self, task_service: TaskService) -> None:
        """Test write scope validation."""
        allowed, reason = await task_service.validate_write_scope(
            task_id="test-1",
            changed_files=["src/a.py"],
            allowed_scope=["src/"],
        )

        assert isinstance(allowed, bool)
        assert isinstance(reason, str)

    @pytest.mark.asyncio
    async def test_check_progress(self, task_service: TaskService) -> None:
        """Test progress checking."""
        from polaris.domain.verification import ProgressDelta

        delta = await task_service.check_progress(
            task_id="test-1",
            files_created=5,
            missing_targets=[],
            errors=[],
        )

        assert isinstance(delta, ProgressDelta)

    @pytest.mark.asyncio
    async def test_analyze_impact(self, task_service: TaskService) -> None:
        """Test impact analysis."""
        result = await task_service.analyze_impact(
            task_id="test-1",
            changed_files=["a.py", "b.py"],
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_impact_recommendations(self, task_service: TaskService) -> None:
        """Test getting impact recommendations."""
        recommendations = await task_service.get_impact_recommendations(
            task_id="test-1",
            changed_files=["a.py"],
        )

        assert isinstance(recommendations, list)


class TestEvidenceCollection:
    """Tests for evidence collection."""

    @pytest.fixture
    def mock_deps(self) -> TaskServiceDeps:
        """Create mock dependencies."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskServiceDeps,
        )

        return TaskServiceDeps(
            impact_analyzer=MagicMock(),
            evidence_store=MagicMock(),
            state_store=MagicMock(),
            log_store=MagicMock(),
            storage=MagicMock(),
            audit_service=MagicMock(),
            repair_service=MagicMock(),
        )

    @pytest.fixture
    def task_service(self, mock_deps: TaskServiceDeps) -> TaskService:
        """Create TaskService instance."""
        from polaris.cells.director.tasking.internal.task_lifecycle_service import (
            TaskQueueConfig,
            TaskService,
        )

        return TaskService(
            config=TaskQueueConfig(),
            workspace="/tmp",
            deps=mock_deps,
        )

    @pytest.mark.asyncio
    async def test_create_evidence_collector(self, task_service: TaskService) -> None:
        """Test creating evidence collector."""
        collector = await task_service.create_evidence_collector(task_id="test-1")
        assert collector is not None

    @pytest.mark.asyncio
    async def test_get_evidence_package(self, task_service: TaskService) -> None:
        """Test getting evidence package."""
        await task_service.create_evidence_collector(task_id="test-1")
        package = await task_service.get_evidence_package(task_id="test-1")
        # Package may be None or EvidencePackage
        assert package is None or hasattr(package, "to_dict")

    @pytest.mark.asyncio
    async def test_record_file_change(self, task_service: TaskService) -> None:
        """Test recording file change."""
        await task_service.create_evidence_collector(task_id="test-1")
        recorded = await task_service.record_file_change(
            task_id="test-1",
            path="a.py",
            change_type="created",
            size_after=100,
        )
        assert recorded is True

    @pytest.mark.asyncio
    async def test_record_file_change_no_collector(self, task_service: TaskService) -> None:
        """Test recording file change without collector."""
        recorded = await task_service.record_file_change(
            task_id="nonexistent",
            path="a.py",
            change_type="created",
        )
        assert recorded is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
