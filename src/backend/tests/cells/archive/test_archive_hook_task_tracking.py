"""Tests for ArchiveHook task tracking and lifecycle management.

This module tests:
- Task creation and tracking
- Task cancellation on shutdown
- Automatic cleanup via done_callback
- Graceful handling of disabled state

Note: Tests use _create_task_with_tracking directly to avoid complex mocking
of dynamically imported modules within archive methods.
"""

from __future__ import annotations

import asyncio
import tempfile
from unittest.mock import MagicMock

import pytest


class TestArchiveHookTaskTracking:
    """Test suite for ArchiveHook task tracking functionality."""

    @pytest.fixture
    def workspace(self) -> str:
        """Create a temporary workspace for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def hook(self, workspace: str):
        """Create an ArchiveHook instance for testing."""
        from polaris.cells.archive.run_archive.internal.archive_hook import ArchiveHook

        return ArchiveHook(workspace)

    @pytest.mark.asyncio
    async def test_task_is_tracked_on_trigger(self, hook) -> None:
        """Verify that triggered tasks are tracked in _pending_tasks."""
        task_started = asyncio.Event()

        async def mock_archive(*args, **kwargs):
            task_started.set()
            await asyncio.sleep(0.5)

        hook._create_task_with_tracking(mock_archive(), "run:run-001")

        await task_started.wait()
        assert hook.get_pending_task_count() == 1
        assert "run:run-001" in hook.get_pending_task_ids()

    @pytest.mark.asyncio
    async def test_task_auto_cleanup_on_completion(self, hook) -> None:
        """Verify that completed tasks are automatically removed from tracking."""
        async def fast_archive(*args, **kwargs):
            await asyncio.sleep(0.05)

        hook._create_task_with_tracking(fast_archive(), "run:run-001")
        await asyncio.sleep(0.1)

        assert hook.get_pending_task_count() == 0
        assert "run:run-001" not in hook.get_pending_task_ids()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_tasks(self, hook) -> None:
        """Verify that shutdown() cancels all pending tasks."""
        task_started = asyncio.Event()
        cancelled = []

        async def slow_archive(*args, **kwargs):
            task_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        hook._create_task_with_tracking(slow_archive(), "run:run-001")
        hook._create_task_with_tracking(slow_archive(), "run:run-002")

        await task_started.wait()
        assert hook.get_pending_task_count() == 2

        cancelled_keys = await hook.shutdown(timeout=1.0)

        assert len(cancelled_keys) == 2
        assert "run:run-001" in cancelled_keys
        assert "run:run-002" in cancelled_keys
        assert hook.get_pending_task_count() == 0
        assert len(cancelled) == 2

    @pytest.mark.asyncio
    async def test_shutdown_with_no_pending_tasks(self, hook) -> None:
        """Verify shutdown() handles empty task queue gracefully."""
        cancelled_keys = await hook.shutdown(timeout=1.0)

        assert cancelled_keys == []
        assert hook.get_pending_task_count() == 0

    @pytest.mark.asyncio
    async def test_disabled_hook_does_not_create_tasks(self, hook) -> None:
        """Verify that disabled hook does not create any tasks."""
        hook.disable()

        hook.trigger_run_archive("run-001", "completed", "")
        hook.trigger_task_snapshot_archive("snapshot-001")
        hook.trigger_factory_archive("factory-001")

        assert hook.get_pending_task_count() == 0
        assert hook.get_pending_task_ids() == []

    @pytest.mark.asyncio
    async def test_task_key_uniqueness_per_type(self, hook) -> None:
        """Verify task keys are unique per archive type."""
        task_started = asyncio.Event()
        task_count = 0

        async def slow_archive(*args, **kwargs):
            nonlocal task_count
            task_count += 1
            if task_count == 3:
                task_started.set()
            await asyncio.sleep(0.5)

        hook._create_task_with_tracking(slow_archive(), "run:same-id")
        hook._create_task_with_tracking(slow_archive(), "task_snapshot:same-id")
        hook._create_task_with_tracking(slow_archive(), "factory:same-id")

        await task_started.wait()
        task_ids = hook.get_pending_task_ids()

        assert "run:same-id" in task_ids
        assert "task_snapshot:same-id" in task_ids
        assert "factory:same-id" in task_ids

    @pytest.mark.asyncio
    async def test_shutdown_returns_cancelled_keys(self, hook) -> None:
        """Verify shutdown() returns the correct list of cancelled task keys."""
        async def slow_task(*args, **kwargs):
            await asyncio.sleep(10)

        hook._create_task_with_tracking(slow_task(), "run:run-a")
        hook._create_task_with_tracking(slow_task(), "run:run-b")

        await asyncio.sleep(0.01)

        cancelled = await hook.shutdown(timeout=0.5)

        assert set(cancelled) == {"run:run-a", "run:run-b"}

    @pytest.mark.asyncio
    async def test_concurrent_trigger_and_shutdown(self, hook) -> None:
        """Verify concurrent triggering and shutdown is safe."""
        async def slow_task(*args, **kwargs):
            await asyncio.sleep(1.0)

        for i in range(5):
            hook._create_task_with_tracking(slow_task(), f"run:run-{i:03d}")

        await asyncio.sleep(0.01)
        cancelled = await hook.shutdown(timeout=0.5)

        assert len(cancelled) == 5
        assert hook.get_pending_task_count() == 0

    @pytest.mark.asyncio
    async def test_multiple_tasks_are_tracked(self, hook) -> None:
        """Verify that multiple triggered tasks are all tracked before completion."""
        task_started = asyncio.Event()
        task_count = 0

        async def slow_archive(*args, **kwargs):
            nonlocal task_count
            task_count += 1
            if task_count == 3:
                task_started.set()
            await asyncio.sleep(0.5)

        hook._create_task_with_tracking(slow_archive(), "run:run-001")
        hook._create_task_with_tracking(slow_archive(), "run:run-002")
        hook._create_task_with_tracking(slow_archive(), "run:run-003")

        await task_started.wait()
        task_ids = hook.get_pending_task_ids()

        assert hook.get_pending_task_count() == 3
        assert "run:run-001" in task_ids
        assert "run:run-002" in task_ids
        assert "run:run-003" in task_ids


class TestArchiveHookCreateFunction:
    """Test suite for ArchiveHook factory functions."""

    def test_create_archive_hook_returns_new_instance(self) -> None:
        """Verify create_archive_hook returns a new instance each time."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            create_archive_hook,
        )

        hook1 = create_archive_hook("/workspace1")
        hook2 = create_archive_hook("/workspace2")

        assert hook1 is not hook2
        assert hook1.workspace == "/workspace1"
        assert hook2.workspace == "/workspace2"

    def test_get_archive_hook_returns_same_instance(self) -> None:
        """Verify get_archive_hook returns singleton."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            get_archive_hook,
        )

        hook1 = get_archive_hook("/workspace")
        hook2 = get_archive_hook("/workspace")

        assert hook1 is hook2

    def test_get_pending_task_count_initially_zero(self) -> None:
        """Verify get_pending_task_count returns 0 for new instance."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            create_archive_hook,
        )

        hook = create_archive_hook("/workspace")
        assert hook.get_pending_task_count() == 0

    def test_get_pending_task_ids_initially_empty(self) -> None:
        """Verify get_pending_task_ids returns empty list for new instance."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            create_archive_hook,
        )

        hook = create_archive_hook("/workspace")
        assert hook.get_pending_task_ids() == []


class TestArchiveHookErrorHandling:
    """Test suite for ArchiveHook error handling."""

    @pytest.mark.asyncio
    async def test_already_disabled_on_init(self) -> None:
        """Verify default enabled state is True."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            create_archive_hook,
        )

        hook = create_archive_hook("/workspace")
        assert hook.is_enabled() is True

    @pytest.mark.asyncio
    async def test_disable_enable_cycle(self) -> None:
        """Verify disable/enable cycle works correctly."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            create_archive_hook,
        )

        hook = create_archive_hook("/workspace")

        assert hook.is_enabled() is True
        hook.disable()
        assert hook.is_enabled() is False
        hook.enable()
        assert hook.is_enabled() is True

    @pytest.mark.asyncio
    async def test_error_in_task_does_not_crash(self) -> None:
        """Verify that errors in tasks don't crash the hook."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            ArchiveHook,
        )

        hook = ArchiveHook("/workspace")

        async def error_task():
            raise RuntimeError("Test error")

        hook._create_task_with_tracking(error_task(), "run:error-task")
        await asyncio.sleep(0.1)

        # Hook should still be functional
        assert hook.get_pending_task_count() == 0
        assert hook.is_enabled()


class TestArchiveHookInternalMethods:
    """Test suite for ArchiveHook internal methods."""

    @pytest.mark.asyncio
    async def test_create_task_with_tracking(self) -> None:
        """Verify _create_task_with_tracking creates and tracks tasks."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            ArchiveHook,
        )

        hook = ArchiveHook("/workspace")
        task_started = asyncio.Event()

        async def tracked_coro():
            task_started.set()
            await asyncio.sleep(0.1)

        task = hook._create_task_with_tracking(tracked_coro(), "test:task-001")

        await task_started.wait()
        assert hook.get_pending_task_count() == 1
        assert "test:task-001" in hook.get_pending_task_ids()

        await asyncio.sleep(0.2)
        assert hook.get_pending_task_count() == 0

    @pytest.mark.asyncio
    async def test_shutdown_clears_all_tasks(self) -> None:
        """Verify shutdown clears all task references."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            ArchiveHook,
        )

        hook = ArchiveHook("/workspace")

        async def slow_task():
            await asyncio.sleep(10)

        hook._create_task_with_tracking(slow_task(), "test:task-001")
        hook._create_task_with_tracking(slow_task(), "test:task-002")

        assert hook.get_pending_task_count() == 2

        await hook.shutdown(timeout=0.1)

        assert hook.get_pending_task_count() == 0
        assert hook._pending_tasks == {}

    @pytest.mark.asyncio
    async def test_internal_pending_tasks_dict_initialized(self) -> None:
        """Verify _pending_tasks is properly initialized."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            ArchiveHook,
        )

        hook = ArchiveHook("/workspace")
        assert hook._pending_tasks == {}
        assert isinstance(hook._pending_tasks, dict)

    @pytest.mark.asyncio
    async def test_done_callback_removes_task(self) -> None:
        """Verify done_callback properly removes completed tasks."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            ArchiveHook,
        )

        hook = ArchiveHook("/workspace")

        async def quick_task():
            pass

        hook._create_task_with_tracking(quick_task(), "run:quick")
        await asyncio.sleep(0.05)

        # Task should be automatically removed after completion
        assert hook.get_pending_task_count() == 0

    @pytest.mark.asyncio
    async def test_shutdown_with_mixed_task_states(self) -> None:
        """Verify shutdown handles mix of running and completed tasks."""
        from polaris.cells.archive.run_archive.internal.archive_hook import (
            ArchiveHook,
        )

        hook = ArchiveHook("/workspace")

        async def slow_task():
            await asyncio.sleep(10)

        async def quick_task():
            await asyncio.sleep(0.01)

        # Add one slow task and one quick task
        hook._create_task_with_tracking(slow_task(), "run:slow")
        hook._create_task_with_tracking(quick_task(), "run:quick")

        # Wait for quick task to complete
        await asyncio.sleep(0.1)

        # Shutdown should only cancel remaining tasks
        cancelled = await hook.shutdown(timeout=0.1)

        assert hook.get_pending_task_count() == 0
        # Only the slow task should be in cancelled list
        assert "run:slow" in cancelled
