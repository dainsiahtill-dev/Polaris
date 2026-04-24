"""Tests for polaris.infrastructure.realtime.process_local.signal_hub."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.realtime.process_local.signal_hub import (
    REALTIME_SIGNAL_HUB,
    RealtimeSignalHub,
    WatchState,
    _WatchHandler,
)


class TestWatchState:
    """Test WatchState enum values."""

    def test_watch_state_values(self) -> None:
        assert WatchState.STARTING.name == "STARTING"
        assert WatchState.RUNNING.name == "RUNNING"
        assert WatchState.FAILED.name == "FAILED"
        assert WatchState.STOPPING.name == "STOPPING"
        assert WatchState.STOPPED.name == "STOPPED"


class TestRealtimeSignalHub:
    """Test RealtimeSignalHub core functionality."""

    def test_init(self) -> None:
        hub = RealtimeSignalHub()
        assert hub._closed is False
        assert hub._sequence == 0
        assert hub._registry == {}

    def test_normalize_root(self) -> None:
        hub = RealtimeSignalHub()
        result = hub._normalize_root("/path/to/workspace")
        # os.path.abspath normalizes paths for the platform
        import os

        assert result == os.path.abspath("/path/to/workspace")

    def test_normalize_root_with_whitespace(self) -> None:
        hub = RealtimeSignalHub()
        result = hub._normalize_root("  /path/to/workspace  ")
        import os

        assert result == os.path.abspath("/path/to/workspace")

    def test_normalize_root_empty(self) -> None:
        hub = RealtimeSignalHub()
        result = hub._normalize_root("")
        import os

        assert result == os.getcwd()

    def test_close(self) -> None:
        hub = RealtimeSignalHub()
        hub.close()
        assert hub._closed is True

    def test_close_idempotent(self) -> None:
        hub = RealtimeSignalHub()
        hub.close()
        hub.close()  # Should not raise
        assert hub._closed is True

    def test_get_watch_info_not_found(self) -> None:
        hub = RealtimeSignalHub()
        result = hub.get_watch_info("/nonexistent/path")
        assert result is None

    def test_list_watches_empty(self) -> None:
        hub = RealtimeSignalHub()
        result = hub.list_watches()
        assert result == []


class TestRealtimeSignalHubEnsureWatch:
    """Test ensure_watch functionality with mocks."""

    @pytest.mark.asyncio
    async def test_ensure_watch_empty_root(self) -> None:
        hub = RealtimeSignalHub()
        result = await hub.ensure_watch("")
        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_watch_whitespace_root(self) -> None:
        hub = RealtimeSignalHub()
        result = await hub.ensure_watch("   ")
        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_watch_closed_hub(self) -> None:
        hub = RealtimeSignalHub()
        hub.close()
        result = await hub.ensure_watch("/path/to/workspace")
        assert result is False


class TestRealtimeSignalHubReleaseWatch:
    """Test release_watch functionality."""

    def test_release_watch_empty_root(self) -> None:
        hub = RealtimeSignalHub()
        hub.release_watch("")
        # Should not raise

    def test_release_watch_not_registered(self) -> None:
        hub = RealtimeSignalHub()
        hub.release_watch("/nonexistent/path")
        # Should not raise


class TestRealtimeSignalHubNotify:
    """Test notify functionality."""

    @pytest.mark.asyncio
    async def test_notify_basic(self) -> None:
        hub = RealtimeSignalHub()
        seq = await hub.notify(source="test", path="/path/to/file", root="/workspace")
        assert seq == 1
        assert hub._sequence == 1

    @pytest.mark.asyncio
    async def test_notify_increments_sequence(self) -> None:
        hub = RealtimeSignalHub()
        await hub.notify(source="test")
        seq2 = await hub.notify(source="test")
        assert seq2 == 2
        assert hub._sequence == 2

    @pytest.mark.asyncio
    async def test_notify_with_workspace(self) -> None:
        hub = RealtimeSignalHub()
        import os

        workspace = os.path.abspath("/workspace")
        seq = await hub.notify(source="test", root=workspace)
        assert seq == 1
        assert hub._last_signal_workspace == workspace


class TestRealtimeSignalHubWaitForUpdate:
    """Test wait_for_update functionality."""

    @pytest.mark.asyncio
    async def test_wait_for_update_immediate_return(self) -> None:
        hub = RealtimeSignalHub()
        await hub.notify(source="test")
        result = await hub.wait_for_update(0, timeout_sec=0.1)
        assert result == 1

    @pytest.mark.asyncio
    async def test_wait_for_update_timeout(self) -> None:
        hub = RealtimeSignalHub()
        result = await hub.wait_for_update(0, timeout_sec=0.05)
        # Timeout returns current sequence (0)
        assert result == 0


class TestRealtimeSignalHubConditionLoopReset:
    """Test condition rebinding when event loop changes."""

    @pytest.mark.asyncio
    async def test_ensure_condition_different_loop(self) -> None:
        hub = RealtimeSignalHub()
        await hub.notify(source="test")

        # Simulate different loop by manually resetting
        hub._loop = None
        hub._condition = asyncio.Condition()

        await hub.notify(source="test")


class TestWatchHandler:
    """Test _WatchHandler filesystem event handler."""

    def test_on_any_event_directory(self) -> None:
        hub = RealtimeSignalHub()
        handler = _WatchHandler(hub, "/workspace")
        event = MagicMock()
        event.is_directory = True
        handler.on_any_event(event)
        # Should return early without notifying

    def test_on_any_event_no_path(self) -> None:
        hub = RealtimeSignalHub()
        hub.notify = MagicMock()  # type: ignore[assignment]
        handler = _WatchHandler(hub, "/workspace")
        event = MagicMock()
        event.is_directory = False
        event.src_path = ""
        handler.on_any_event(event)
        hub.notify.assert_not_called()

    def test_on_any_event_valid(self) -> None:
        hub = RealtimeSignalHub()
        handler = _WatchHandler(hub, "/workspace")
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/workspace/file.py"
        handler.on_any_event(event)


class TestGlobalSingleton:
    """Test REALTIME_SIGNAL_HUB singleton."""

    def test_singleton_exists(self) -> None:
        assert REALTIME_SIGNAL_HUB is not None
        assert isinstance(REALTIME_SIGNAL_HUB, RealtimeSignalHub)
