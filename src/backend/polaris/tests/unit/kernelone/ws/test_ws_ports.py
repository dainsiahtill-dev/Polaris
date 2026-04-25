"""Tests for polaris.kernelone.ws.ports."""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.kernelone.ws.ports import (
    ConnectionState,
    SessionId,
    WsMessage,
    WsSession,
)


class TestConnectionState:
    def test_values(self) -> None:
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.OPEN == "open"
        assert ConnectionState.CLOSING == "closing"
        assert ConnectionState.CLOSED == "closed"
        assert ConnectionState.ERROR == "error"


class TestWsMessage:
    def test_defaults(self) -> None:
        msg = WsMessage(session_id="s1", type="text", payload="hello")
        assert msg.session_id == "s1"
        assert msg.type == "text"
        assert msg.payload == "hello"
        assert isinstance(msg.timestamp, datetime)
        assert msg.metadata == {}

    def test_to_dict(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg = WsMessage(
            session_id="s1",
            type="text",
            payload="hello",
            timestamp=ts,
            metadata={"key": "val"},
        )
        d = msg.to_dict()
        assert d["session_id"] == "s1"
        assert d["type"] == "text"
        assert d["payload"] == "hello"
        assert d["timestamp"] == ts.isoformat()
        assert d["metadata"] == {"key": "val"}


class TestWsSession:
    def test_defaults(self) -> None:
        session = WsSession(session_id="s1")
        assert session.session_id == "s1"
        assert session.state == ConnectionState.CONNECTING
        assert session.message_count == 0
        assert session.peer_addr is None
        assert session.metadata == {}
        assert isinstance(session.created_at, datetime)
        assert session.last_message_at is None

    def test_to_dict(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        session = WsSession(
            session_id="s1",
            state=ConnectionState.OPEN,
            created_at=ts,
            last_message_at=ts,
            message_count=5,
            peer_addr="127.0.0.1",
            metadata={"user": "u1"},
        )
        d = session.to_dict()
        assert d["session_id"] == "s1"
        assert d["state"] == "open"
        assert d["created_at"] == ts.isoformat()
        assert d["last_message_at"] == ts.isoformat()
        assert d["message_count"] == 5
        assert d["peer_addr"] == "127.0.0.1"
        assert d["metadata"] == {"user": "u1"}

    def test_to_dict_no_last_message(self) -> None:
        session = WsSession(session_id="s1")
        d = session.to_dict()
        assert d["last_message_at"] is None


class TestSessionId:
    def test_is_str(self) -> None:
        sid: SessionId = "session-123"
        assert isinstance(sid, str)
