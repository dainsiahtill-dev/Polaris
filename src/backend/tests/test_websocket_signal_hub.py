"""Tests for realtime signal hub and event distribution.

This module tests:
1. RealtimeSignalHub notify/wait functionality
2. RuntimeEventFanout integration with MessageBus
3. Event distribution to WebSocket connections

Note: These tests use the new v2 architecture (RuntimeEventFanout) instead of
the deprecated _DirectorBusRealtimeBridge from app.routers.websocket.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from polaris.infrastructure.realtime.process_local.message_event_fanout import RuntimeEventFanout
from polaris.infrastructure.realtime.process_local.signal_hub import RealtimeSignalHub
from polaris.kernelone.events.message_bus import Message, MessageType


class _FakeBus:
    def __init__(self) -> None:
        self.subscriptions: dict[MessageType, list] = {}

    async def subscribe(self, message_type: MessageType, handler) -> bool:
        self.subscriptions.setdefault(message_type, []).append(handler)
        return True

    async def unsubscribe(self, message_type: MessageType, handler) -> bool:
        handlers = self.subscriptions.get(message_type, [])
        self.subscriptions[message_type] = [item for item in handlers if item != handler]
        return True


class _FakeDirectorService:
    def __init__(self) -> None:
        self._bus = _FakeBus()


class _FakeRealtimeHub:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self._signal = asyncio.Event()

    async def notify(self, **kwargs):
        self.calls.append({k: str(v) for k, v in kwargs.items()})
        self._signal.set()
        return len(self.calls)


@pytest.mark.asyncio
async def test_signal_hub_wakes_waiters_on_director_bus_event(monkeypatch) -> None:
    """Test that MessageBus events trigger RealtimeSignalHub notifications via RuntimeEventFanout."""
    service = _FakeDirectorService()

    fanout = RuntimeEventFanout()
    try:
        # Patch the fanout to use our fake bus
        fanout._bus = service._bus
        fanout._subscribed = True
        fanout._file_handler = fanout._make_file_written_handler(asyncio.get_event_loop())
        fanout._trace_handler = fanout._make_task_trace_handler(asyncio.get_event_loop())

        # Subscribe handlers
        await service._bus.subscribe(MessageType.FILE_WRITTEN, fanout._file_handler)
        await service._bus.subscribe(MessageType.TASK_TRACE, fanout._trace_handler)

        # Register a connection to receive events
        sink = await fanout.register_connection(
            connection_id="test-conn",
            workspace="/test",
            cache_root="/cache",
        )

        # Emit a FILE_WRITTEN message
        message = Message(
            type=MessageType.FILE_WRITTEN,
            sender="director",
            payload={"file_path": "/test/file.py", "operation": "modify"},
        )
        handlers = service._bus.subscriptions.get(MessageType.FILE_WRITTEN, [])
        assert handlers, "FILE_WRITTEN handler should be registered"

        for handler in handlers:
            handler(message)

        # Drain events from the sink
        await asyncio.sleep(0.05)  # Let handler process
        file_events, _, _, _ = await fanout.drain_events("test-conn")

        assert len(file_events) == 1
        assert file_events[0]["file_path"] == "/test/file.py"

    finally:
        await fanout.close()


@pytest.mark.asyncio
async def test_realtime_hub_wait_returns_after_notify() -> None:
    """Test that wait_for_update returns after notify is called."""
    hub = RealtimeSignalHub()
    try:
        waiter = asyncio.create_task(hub.wait_for_update(0, timeout_sec=1.0))

        await hub.notify(source="test", path="manual")

        next_seq = await waiter
        assert next_seq > 0
    finally:
        hub.close()


@pytest.mark.asyncio
async def test_event_fanout_single_subscription() -> None:
    """Test that RuntimeEventFanout only subscribes once per message type."""
    service = _FakeDirectorService()
    fanout = RuntimeEventFanout()

    async def mock_resolve(interface):
        return service

    try:
        with patch(
            "polaris.infrastructure.realtime.process_local.message_event_fanout.get_container"
        ) as mock_get_container:
            mock_container = MagicMock()
            mock_container.resolve_async = mock_resolve
            mock_get_container.return_value = mock_container

            # Multiple subscriptions
            await fanout.ensure_subscribed()
            await fanout.ensure_subscribed()
            await fanout.ensure_subscribed()

            # Should only have one subscription per type
            assert len(service._bus.subscriptions.get(MessageType.FILE_WRITTEN, [])) == 1
            assert len(service._bus.subscriptions.get(MessageType.TASK_TRACE, [])) == 1
    finally:
        await fanout.close()


@pytest.mark.asyncio
async def test_event_fanout_unsubscribe_on_close() -> None:
    """Test that close() properly unsubscribes handlers."""
    service = _FakeDirectorService()
    fanout = RuntimeEventFanout()

    async def mock_resolve(interface):
        return service

    with patch(
        "polaris.infrastructure.realtime.process_local.message_event_fanout.get_container"
    ) as mock_get_container:
        mock_container = MagicMock()
        mock_container.resolve_async = mock_resolve
        mock_get_container.return_value = mock_container

        await fanout.ensure_subscribed()

        # Verify subscribed
        assert len(service._bus.subscriptions.get(MessageType.FILE_WRITTEN, [])) == 1

        await fanout.close()

        # Verify unsubscribed
        assert len(service._bus.subscriptions.get(MessageType.FILE_WRITTEN, [])) == 0
