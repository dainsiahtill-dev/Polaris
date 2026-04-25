"""Unit tests for polaris.cells.archive.run_archive.internal.archive_sink."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from polaris.cells.archive.run_archive.internal.archive_sink import (
    ArchiveSink,
    _ARCHIVE_FLUSH_EVENTS,
)
from polaris.kernelone.events.message_bus import Message, MessageType


class TestArchiveSinkInit:
    """Tests for ArchiveSink initialization."""

    def test_init(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        assert sink._bus is bus
        assert sink._subscribed is False
        assert sink._buffers == {}
        assert sink._meta == {}


class TestArchiveSinkStartStop:
    """Tests for ArchiveSink start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        bus = MagicMock()
        bus.subscribe = AsyncMock()
        sink = ArchiveSink(bus)
        await sink.start()
        assert sink._subscribed is True
        bus.subscribe.assert_awaited_once_with(MessageType.RUNTIME_EVENT, sink._handle_message)

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        bus = MagicMock()
        bus.subscribe = AsyncMock()
        sink = ArchiveSink(bus)
        await sink.start()
        await sink.start()
        bus.subscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        bus = MagicMock()
        bus.subscribe = AsyncMock()
        bus.unsubscribe = AsyncMock()
        sink = ArchiveSink(bus)
        await sink.start()
        await sink.stop()
        assert sink._subscribed is False
        bus.unsubscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_not_subscribed(self) -> None:
        bus = MagicMock()
        bus.unsubscribe = AsyncMock()
        sink = ArchiveSink(bus)
        await sink.stop()
        bus.unsubscribe.assert_not_awaited()


class TestArchiveSinkHandleMessage:
    """Tests for ArchiveSink._handle_message."""

    @pytest.mark.asyncio
    async def test_non_dict_payload_ignored(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        message = MagicMock()
        message.payload = "not a dict"
        await sink._handle_message(message)
        assert sink._buffers == {}

    @pytest.mark.asyncio
    async def test_wrong_topic_ignored(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        message = MagicMock()
        message.payload = {"topic": "wrong_topic", "turn_id": "t1"}
        await sink._handle_message(message)
        assert sink._buffers == {}

    @pytest.mark.asyncio
    async def test_missing_turn_id_ignored(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        message = MagicMock()
        message.payload = {
            "topic": "runtime.stream",
            "turn_id": "",
            "workspace": "ws",
            "event_type": "chunk",
        }
        await sink._handle_message(message)
        assert sink._buffers == {}

    @pytest.mark.asyncio
    async def test_missing_workspace_ignored(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        message = MagicMock()
        message.payload = {
            "topic": "runtime.stream",
            "turn_id": "t1",
            "workspace": "",
            "event_type": "chunk",
        }
        await sink._handle_message(message)
        assert sink._buffers == {}

    @pytest.mark.asyncio
    async def test_valid_message_buffered(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        message = MagicMock()
        message.payload = {
            "topic": "runtime.stream",
            "turn_id": "t1",
            "workspace": "/tmp/ws",
            "run_id": "run-1",
            "event_type": "chunk",
            "payload": {"data": "hello"},
        }
        await sink._handle_message(message)
        assert "t1" in sink._buffers
        assert len(sink._buffers["t1"]) == 1
        assert sink._buffers["t1"][0]["type"] == "chunk"
        assert sink._meta["t1"]["workspace"] == "/tmp/ws"

    @pytest.mark.asyncio
    async def test_flush_event_triggers_flush(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)

        # Mock _flush_turn to capture calls
        flushed_turns: list[str] = []
        original_flush = sink._flush_turn

        async def mock_flush(turn_id: str) -> None:
            flushed_turns.append(turn_id)
            await original_flush(turn_id)

        sink._flush_turn = mock_flush  # type: ignore[method-assign]

        message = MagicMock()
        message.payload = {
            "topic": "runtime.stream",
            "turn_id": "t1",
            "workspace": "/tmp/ws",
            "run_id": "run-1",
            "event_type": "complete",
            "payload": {},
        }
        await sink._handle_message(message)
        assert "t1" in flushed_turns


class TestArchiveSinkFlushAll:
    """Tests for ArchiveSink._flush_all."""

    @pytest.mark.asyncio
    async def test_flush_all_empty(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        await sink._flush_all()
        assert sink._buffers == {}

    @pytest.mark.asyncio
    async def test_flush_all_with_buffers(self) -> None:
        bus = MagicMock()
        sink = ArchiveSink(bus)
        sink._buffers = {
            "t1": [{"type": "chunk"}],
            "t2": [{"type": "chunk"}],
        }
        sink._meta = {
            "t1": {"workspace": "/tmp/ws", "session_id": "s1"},
            "t2": {"workspace": "/tmp/ws", "session_id": "s2"},
        }
        await sink._flush_all()
        assert sink._buffers == {}


class TestArchiveConstants:
    """Tests for module-level constants."""

    def test_flush_events(self) -> None:
        assert "complete" in _ARCHIVE_FLUSH_EVENTS
        assert "error" in _ARCHIVE_FLUSH_EVENTS
