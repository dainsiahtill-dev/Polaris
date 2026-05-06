"""runtime.v2 protocol lifecycle contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.delivery.ws.endpoints import protocol


class _FakeConsumerManager:
    instances: list[_FakeConsumerManager] = []

    def __init__(
        self,
        *,
        workspace_key: str,
        client_id: str,
        channels: list[str],
        initial_cursor: int = 0,
        tail: int = 200,
        durable_token: str | None = None,
    ) -> None:
        self.workspace_key = workspace_key
        self.client_id = client_id
        self.channels = channels
        self.initial_cursor = initial_cursor
        self.tail = tail
        self.durable_token = durable_token
        self.connected = False
        self.disconnected = False
        self.acked: list[int] = []
        _FakeConsumerManager.instances.append(self)

    @property
    def is_connected(self) -> bool:
        return self.connected and not self.disconnected

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def ack_cursor(self, cursor: int) -> None:
        self.acked.append(cursor)

    def get_current_cursor(self) -> int:
        return max(self.acked or [self.initial_cursor])


@pytest.mark.asyncio
async def test_repeated_subscribe_disconnects_previous_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection can own only one runtime.v2 JetStream consumer at a time."""
    _FakeConsumerManager.instances = []
    sent_payloads: list[dict[str, Any]] = []

    async def _send_json_safe(*_args: Any, **_kwargs: Any) -> bool:
        sent_payloads.append(_args[1])
        return True

    monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeConsumerManager)
    monkeypatch.setattr(protocol, "send_json_safe", _send_json_safe)

    tail_ref = [200]
    consumer_ref: list[Any] = [None]
    client_id_ref = [""]
    channels_ref: list[list[str]] = [[]]
    cursor_ref = [0]
    common = {
        "websocket": object(),
        "status_sig": "",
        "connection_id": "abc123",
        "client": "test-client",
        "workspace": "C:/workspace",
        "cache_root": "C:/runtime",
        "roles_filter": set(),
        "tail_lines_ref": tail_ref,
        "consumer_manager_ref": consumer_ref,
        "client_id_ref": client_id_ref,
        "channels_ref": channels_ref,
        "cursor_ref": cursor_ref,
        "state": object(),
        "handle_event_query_func": None,
    }

    await protocol.handle_v2_message(
        message={"type": "SUBSCRIBE", "protocol": "runtime.v2", "client_id": "same", "channels": ["llm"]},
        **common,
    )
    first = consumer_ref[0]

    await protocol.handle_v2_message(
        message={"type": "SUBSCRIBE", "protocol": "runtime.v2", "client_id": "same", "channels": ["director"]},
        **common,
    )
    second = consumer_ref[0]

    assert first is not second
    assert first.disconnected is True
    assert second.is_connected is True
    assert second.durable_token == "abc123-same"
    assert sent_payloads[-1]["payload"]["jetstream"] is True


@pytest.mark.asyncio
async def test_partial_unsubscribe_keeps_consumer_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial channel unsubscribe should not disconnect runtime.v2 consumer."""
    _FakeConsumerManager.instances = []
    sent_payloads: list[dict[str, Any]] = []

    async def _send_json_safe(*_args: Any, **_kwargs: Any) -> bool:
        sent_payloads.append(_args[1])
        return True

    monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeConsumerManager)
    monkeypatch.setattr(protocol, "send_json_safe", _send_json_safe)

    tail_ref = [200]
    consumer_ref: list[Any] = [None]
    client_id_ref = [""]
    channels_ref: list[list[str]] = [[]]
    cursor_ref = [0]
    common = {
        "websocket": object(),
        "status_sig": "",
        "connection_id": "abc123",
        "client": "test-client",
        "workspace": "C:/workspace",
        "cache_root": "C:/runtime",
        "roles_filter": set(),
        "tail_lines_ref": tail_ref,
        "consumer_manager_ref": consumer_ref,
        "client_id_ref": client_id_ref,
        "channels_ref": channels_ref,
        "cursor_ref": cursor_ref,
        "state": object(),
        "handle_event_query_func": None,
    }

    await protocol.handle_v2_message(
        message={"type": "SUBSCRIBE", "protocol": "runtime.v2", "client_id": "same", "channels": ["llm", "director"]},
        **common,
    )
    consumer = consumer_ref[0]

    await protocol.handle_v2_message(
        message={"type": "UNSUBSCRIBE", "protocol": "runtime.v2", "channels": ["llm"]},
        **common,
    )

    assert consumer_ref[0] is consumer
    assert consumer.disconnected is False
    assert consumer.is_connected is True
    assert channels_ref[0] == ["director"]
    assert consumer.channels == ["director"]
    assert sent_payloads[-1]["type"] == "UNSUBSCRIBED"
    assert sent_payloads[-1]["payload"]["channels"] == ["director"]


@pytest.mark.asyncio
async def test_full_unsubscribe_disconnects_consumer_for_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full unsubscribe remains backward compatible and disconnects consumer."""
    _FakeConsumerManager.instances = []
    sent_payloads: list[dict[str, Any]] = []

    async def _send_json_safe(*_args: Any, **_kwargs: Any) -> bool:
        sent_payloads.append(_args[1])
        return True

    monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeConsumerManager)
    monkeypatch.setattr(protocol, "send_json_safe", _send_json_safe)

    tail_ref = [200]
    consumer_ref: list[Any] = [None]
    client_id_ref = [""]
    channels_ref: list[list[str]] = [[]]
    cursor_ref = [0]
    common = {
        "websocket": object(),
        "status_sig": "",
        "connection_id": "abc123",
        "client": "test-client",
        "workspace": "C:/workspace",
        "cache_root": "C:/runtime",
        "roles_filter": set(),
        "tail_lines_ref": tail_ref,
        "consumer_manager_ref": consumer_ref,
        "client_id_ref": client_id_ref,
        "channels_ref": channels_ref,
        "cursor_ref": cursor_ref,
        "state": object(),
        "handle_event_query_func": None,
    }

    await protocol.handle_v2_message(
        message={"type": "SUBSCRIBE", "protocol": "runtime.v2", "client_id": "same", "channels": ["llm", "director"]},
        **common,
    )
    consumer = consumer_ref[0]

    await protocol.handle_v2_message(
        message={"type": "UNSUBSCRIBE", "protocol": "runtime.v2"},
        **common,
    )

    assert consumer.disconnected is True
    assert consumer_ref[0] is None
    assert channels_ref[0] == []
    assert sent_payloads[-1]["type"] == "UNSUBSCRIBED"
    assert sent_payloads[-1]["payload"]["channels"] == []


@pytest.mark.asyncio
async def test_unsubscribe_all_keyword_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ALL` keyword should keep backward-compatible full-unsubscribe semantics."""
    _FakeConsumerManager.instances = []
    sent_payloads: list[dict[str, Any]] = []

    async def _send_json_safe(*_args: Any, **_kwargs: Any) -> bool:
        sent_payloads.append(_args[1])
        return True

    monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeConsumerManager)
    monkeypatch.setattr(protocol, "send_json_safe", _send_json_safe)

    tail_ref = [200]
    consumer_ref: list[Any] = [None]
    client_id_ref = [""]
    channels_ref: list[list[str]] = [[]]
    cursor_ref = [0]
    common = {
        "websocket": object(),
        "status_sig": "",
        "connection_id": "abc123",
        "client": "test-client",
        "workspace": "C:/workspace",
        "cache_root": "C:/runtime",
        "roles_filter": set(),
        "tail_lines_ref": tail_ref,
        "consumer_manager_ref": consumer_ref,
        "client_id_ref": client_id_ref,
        "channels_ref": channels_ref,
        "cursor_ref": cursor_ref,
        "state": object(),
        "handle_event_query_func": None,
    }

    await protocol.handle_v2_message(
        message={"type": "SUBSCRIBE", "protocol": "runtime.v2", "client_id": "same", "channels": ["llm", "director"]},
        **common,
    )
    consumer = consumer_ref[0]

    await protocol.handle_v2_message(
        message={"type": "UNSUBSCRIBE", "protocol": "runtime.v2", "channels": ["ALL"]},
        **common,
    )

    assert consumer.disconnected is True
    assert consumer_ref[0] is None
    assert channels_ref[0] == []
    assert sent_payloads[-1]["type"] == "UNSUBSCRIBED"
    assert sent_payloads[-1]["payload"]["channels"] == []


@pytest.mark.asyncio
async def test_subscribe_handles_previous_consumer_disconnect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A previous consumer disconnect failure should not break new subscription."""
    _FakeConsumerManager.instances = []
    sent_payloads: list[dict[str, Any]] = []

    async def _send_json_safe(*_args: Any, **_kwargs: Any) -> bool:
        sent_payloads.append(_args[1])
        return True

    class _FailingConsumer:
        async def disconnect(self) -> None:
            raise RuntimeError("disconnect failed")

    monkeypatch.setattr(protocol, "JetStreamConsumerManager", _FakeConsumerManager)
    monkeypatch.setattr(protocol, "send_json_safe", _send_json_safe)

    tail_ref = [200]
    consumer_ref: list[Any] = [_FailingConsumer()]
    client_id_ref = [""]
    channels_ref: list[list[str]] = [[]]
    cursor_ref = [0]
    common = {
        "websocket": object(),
        "status_sig": "",
        "connection_id": "abc123",
        "client": "test-client",
        "workspace": "C:/workspace",
        "cache_root": "C:/runtime",
        "roles_filter": set(),
        "tail_lines_ref": tail_ref,
        "consumer_manager_ref": consumer_ref,
        "client_id_ref": client_id_ref,
        "channels_ref": channels_ref,
        "cursor_ref": cursor_ref,
        "state": object(),
        "handle_event_query_func": None,
    }

    await protocol.handle_v2_message(
        message={"type": "SUBSCRIBE", "protocol": "runtime.v2", "client_id": "next", "channels": ["llm"]},
        **common,
    )

    assert isinstance(consumer_ref[0], _FakeConsumerManager)
    assert consumer_ref[0].is_connected is True
    assert sent_payloads[-1]["type"] == "SUBSCRIBED"
