"""Unit tests for polaris.cells.archive.run_archive.internal.archive_hook."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.archive.run_archive.internal.archive_hook import (
    ArchiveHook,
    create_archive_hook,
    get_archive_hook,
)


class TestArchiveHookInit:
    """Tests for ArchiveHook initialization."""

    def test_init(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        assert hook.workspace == "/tmp/ws"
        assert hook._enabled is True
        assert hook._pending_tasks == {}


class TestArchiveHookEnableDisable:
    """Tests for ArchiveHook enable/disable."""

    def test_is_enabled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        assert hook.is_enabled() is True

    def test_disable(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        hook.disable()
        assert hook.is_enabled() is False

    def test_enable(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        hook.disable()
        hook.enable()
        assert hook.is_enabled() is True


class TestArchiveHookTaskTracking:
    """Tests for ArchiveHook task tracking."""

    def test_get_pending_task_count_empty(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        assert hook.get_pending_task_count() == 0

    def test_get_pending_task_ids_empty(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        assert hook.get_pending_task_ids() == []

    def test_create_task_with_tracking(self) -> None:
        hook = ArchiveHook("/tmp/ws")

        async def dummy() -> None:
            pass

        task = hook._create_task_with_tracking(dummy(), "test:1")
        assert task.done() is False
        assert "test:1" in hook._pending_tasks
        # Wait for completion
        asyncio.get_event_loop().run_until_complete(task)
        assert task.done() is True


class TestArchiveHookTriggerMethods:
    """Tests for ArchiveHook trigger methods."""

    def test_trigger_run_archive_disabled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        hook.disable()
        hook.trigger_run_archive("run-1")
        assert hook.get_pending_task_count() == 0

    def test_trigger_task_snapshot_archive_disabled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        hook.disable()
        hook.trigger_task_snapshot_archive("snap-1")
        assert hook.get_pending_task_count() == 0

    def test_trigger_factory_archive_disabled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        hook.disable()
        hook.trigger_factory_archive("factory-1")
        assert hook.get_pending_task_count() == 0

    def test_trigger_run_archive_creates_task(self) -> None:
        hook = ArchiveHook("/tmp/ws")

        async def mock_archive() -> None:
            pass

        hook._archive_run_async = mock_archive  # type: ignore[method-assign]
        hook.trigger_run_archive("run-1")
        assert hook.get_pending_task_count() == 1
        assert "run:run-1" in hook.get_pending_task_ids()

    def test_trigger_task_snapshot_archive_creates_task(self) -> None:
        hook = ArchiveHook("/tmp/ws")

        async def mock_archive() -> None:
            pass

        hook._archive_task_snapshot_async = mock_archive  # type: ignore[method-assign]
        hook.trigger_task_snapshot_archive("snap-1")
        assert hook.get_pending_task_count() == 1
        assert "task_snapshot:snap-1" in hook.get_pending_task_ids()

    def test_trigger_factory_archive_creates_task(self) -> None:
        hook = ArchiveHook("/tmp/ws")

        async def mock_archive() -> None:
            pass

        hook._archive_factory_async = mock_archive  # type: ignore[method-assign]
        hook.trigger_factory_archive("factory-1")
        assert hook.get_pending_task_count() == 1
        assert "factory:factory-1" in hook.get_pending_task_ids()


class TestArchiveHookShutdown:
    """Tests for ArchiveHook.shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_empty(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        cancelled = await hook.shutdown()
        assert cancelled == []

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending(self) -> None:
        hook = ArchiveHook("/tmp/ws")

        async def slow_task() -> None:
            await asyncio.sleep(100)

        hook._create_task_with_tracking(slow_task(), "run:r1")
        assert hook.get_pending_task_count() == 1

        cancelled = await hook.shutdown(timeout=0.1)
        assert "run:r1" in cancelled
        assert hook.get_pending_task_count() == 0


class TestArchiveHookArchiveAsync:
    """Tests for ArchiveHook archive async implementations."""

    @pytest.mark.asyncio
    async def test_archive_run_async_cancelled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        with pytest.raises(asyncio.CancelledError):
            # Create a task and cancel it
            task = asyncio.create_task(hook._archive_run_async("r1", "completed", ""))
            task.cancel()
            await task

    @pytest.mark.asyncio
    async def test_archive_task_snapshot_async_cancelled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        with pytest.raises(asyncio.CancelledError):
            task = asyncio.create_task(hook._archive_task_snapshot_async("s1", None, None, "completed"))
            task.cancel()
            await task

    @pytest.mark.asyncio
    async def test_archive_factory_async_cancelled(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        with pytest.raises(asyncio.CancelledError):
            task = asyncio.create_task(hook._archive_factory_async("f1", None, "completed"))
            task.cancel()
            await task

    @pytest.mark.asyncio
    async def test_archive_run_async_import_error(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        # Should not raise - catches ImportError
        with patch("builtins.__import__", side_effect=ImportError):
            await hook._archive_run_async("r1", "completed", "")

    @pytest.mark.asyncio
    async def test_archive_task_snapshot_async_import_error(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        with patch("builtins.__import__", side_effect=ImportError):
            await hook._archive_task_snapshot_async("s1", None, None, "completed")

    @pytest.mark.asyncio
    async def test_archive_factory_async_import_error(self) -> None:
        hook = ArchiveHook("/tmp/ws")
        with patch("builtins.__import__", side_effect=ImportError):
            await hook._archive_factory_async("f1", None, "completed")


class TestGetArchiveHook:
    """Tests for get_archive_hook."""

    def test_returns_same_instance(self) -> None:
        hook1 = get_archive_hook("/tmp/ws")
        hook2 = get_archive_hook("/tmp/ws")
        assert hook1 is hook2


class TestCreateArchiveHook:
    """Tests for create_archive_hook."""

    def test_creates_new_instance(self) -> None:
        hook1 = create_archive_hook("/tmp/ws")
        hook2 = create_archive_hook("/tmp/ws")
        assert hook1 is not hook2
        assert isinstance(hook1, ArchiveHook)
