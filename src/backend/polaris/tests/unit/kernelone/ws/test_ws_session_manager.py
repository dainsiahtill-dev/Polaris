"""Tests for polaris.kernelone.ws.session_manager."""

from __future__ import annotations

import pytest
from polaris.kernelone.ws.ports import ConnectionState, WsMessage
from polaris.kernelone.ws.session_manager import InMemorySessionManager


class TestInMemorySessionManager:
    async def test_create_session(self) -> None:
        manager = InMemorySessionManager()
        session = await manager.create_session("s1", metadata={"user": "u1"})
        assert session.session_id == "s1"
        assert session.state == ConnectionState.CONNECTING
        assert session.metadata == {"user": "u1"}

    async def test_create_session_empty_id_raises(self) -> None:
        manager = InMemorySessionManager()
        with pytest.raises(ValueError, match="session_id must be non-empty"):
            await manager.create_session("")

    async def test_create_session_duplicate_raises(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        with pytest.raises(ValueError, match="already exists"):
            await manager.create_session("s1")

    async def test_get_session(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        session = await manager.get_session("s1")
        assert session is not None
        assert session.session_id == "s1"

    async def test_get_session_missing(self) -> None:
        manager = InMemorySessionManager()
        assert await manager.get_session("missing") is None

    async def test_list_sessions(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.create_session("s2")
        sessions = await manager.list_sessions()
        assert len(sessions) == 2
        ids = {s.session_id for s in sessions}
        assert ids == {"s1", "s2"}

    async def test_update_session(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        updated = await manager.update_session("s1", state="open", message_count=5, peer_addr="127.0.0.1")
        assert updated is not None
        assert updated.state == ConnectionState.OPEN
        assert updated.message_count == 5
        assert updated.peer_addr == "127.0.0.1"

    async def test_update_session_metadata(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1", metadata={"a": 1})
        updated = await manager.update_session("s1", metadata={"b": 2})
        assert updated is not None
        assert updated.metadata == {"a": 1, "b": 2}

    async def test_update_session_missing(self) -> None:
        manager = InMemorySessionManager()
        assert await manager.update_session("missing", state="open") is None

    async def test_remove_session(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        removed = await manager.remove_session("s1")
        assert removed is True
        assert await manager.get_session("s1") is None

    async def test_remove_session_missing(self) -> None:
        manager = InMemorySessionManager()
        assert await manager.remove_session("missing") is False

    async def test_broadcast_to_open_sessions(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.create_session("s2")
        await manager.update_session("s1", state="open")
        await manager.update_session("s2", state="open")

        sent = await manager.broadcast("hello")
        assert sent == 2

    async def test_broadcast_to_specific_sessions(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.create_session("s2")
        await manager.update_session("s1", state="open")
        await manager.update_session("s2", state="open")

        sent = await manager.broadcast("hello", session_ids=["s1"])
        assert sent == 1

    async def test_broadcast_skips_non_open(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.create_session("s2")
        await manager.update_session("s1", state="open")

        sent = await manager.broadcast("hello")
        assert sent == 1

    async def test_register_and_dispatch_handler(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.update_session("s1", state="open")

        received: list[WsMessage] = []

        def handler(msg: WsMessage) -> None:
            received.append(msg)

        manager.register_handler("text", handler)
        await manager.dispatch_message("s1", "hello", msg_type="text")
        assert len(received) == 1
        assert received[0].payload == "hello"
        assert received[0].type == "text"

    async def test_dispatch_async_handler(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.update_session("s1", state="open")

        received: list[WsMessage] = []

        async def handler(msg: WsMessage) -> None:
            received.append(msg)

        manager.register_handler("text", handler)
        await manager.dispatch_message("s1", "hello", msg_type="text")
        assert len(received) == 1

    async def test_dispatch_handler_error(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.update_session("s1", state="open")

        def handler(msg: WsMessage) -> None:
            raise ValueError("boom")

        manager.register_handler("text", handler)
        await manager.dispatch_message("s1", "hello", msg_type="text")

    async def test_dispatch_unknown_session(self) -> None:
        manager = InMemorySessionManager()
        await manager.dispatch_message("missing", "hello")

    async def test_broadcast_with_metadata(self) -> None:
        manager = InMemorySessionManager()
        await manager.create_session("s1")
        await manager.update_session("s1", state="open")

        received: list[WsMessage] = []

        def handler(msg: WsMessage) -> None:
            received.append(msg)

        manager.register_handler("broadcast", handler)
        await manager.broadcast("hello", metadata={"type": "ping"})
        assert len(received) == 1
        assert received[0].metadata == {"type": "ping"}
