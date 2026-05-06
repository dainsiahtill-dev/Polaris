from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from nats.js.errors import APIError as JSAPIError, NotFoundError as JSConsumerNotFoundError
from polaris.infrastructure.messaging.nats import ws_consumer_manager
from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager


class _FakeSubscription:
    async def next_msg(self) -> None:
        await asyncio.sleep(10)

    async def unsubscribe(self) -> None:
        return None


class _FakeJetStream:
    def __init__(
        self,
        *,
        delete_error: Exception | None = None,
        subscribe_error: Exception | None = None,
        stream_info_error: Exception | None = None,
    ) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.streams_added: list[str] = []
        self.delete_error = delete_error
        self.subscribe_error = subscribe_error
        self.stream_info_error = stream_info_error

    async def stream_info(self, stream_name: str) -> object:
        if self.stream_info_error is not None:
            raise self.stream_info_error
        return {"name": stream_name}

    async def add_stream(self, config: Any) -> object:
        self.streams_added.append(str(config.name))
        return {"name": config.name}

    async def delete_consumer(self, stream_name: str, durable_name: str) -> None:
        self.deleted.append((stream_name, durable_name))
        if self.delete_error is not None:
            raise self.delete_error
        raise JSConsumerNotFoundError(code=404, err_code=10014, description="consumer not found")

    async def subscribe(self, *_args: Any, **_kwargs: Any) -> _FakeSubscription:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        return _FakeSubscription()


class _FakeNATSClient:
    def __init__(self, jetstream: _FakeJetStream) -> None:
        self.jetstream = jetstream


class _FakeSequenceMetadata:
    def __init__(self, stream: int) -> None:
        self.stream = stream


class _FakeMetadata:
    def __init__(self, stream: int) -> None:
        self.sequence = _FakeSequenceMetadata(stream=stream)


class _FakeMessage:
    def __init__(self, payload: dict[str, Any], *, stream_seq: int) -> None:
        self.data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.metadata = _FakeMetadata(stream=stream_seq)
        self.ack_calls = 0
        self.acked = asyncio.Event()

    async def ack(self) -> None:
        self.ack_calls += 1
        self.acked.set()


class _OneShotSubscription:
    def __init__(self, msg: _FakeMessage) -> None:
        self._msg = msg
        self._sent = False

    async def next_msg(self) -> _FakeMessage:
        if not self._sent:
            self._sent = True
            return self._msg
        await asyncio.sleep(10)
        return self._msg

    async def unsubscribe(self) -> None:
        return None


@pytest.mark.asyncio
async def test_connect_treats_missing_stale_consumer_as_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    jetstream = _FakeJetStream()

    async def _get_default_client() -> _FakeNATSClient:
        return _FakeNATSClient(jetstream)

    monkeypatch.setattr(ws_consumer_manager, "get_default_client", _get_default_client)
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
    )

    try:
        connected = await manager.connect()
        assert connected is True
        assert manager.is_connected is True
        assert jetstream.deleted
    finally:
        await manager.disconnect()


@pytest.mark.asyncio
async def test_disconnect_treats_missing_consumer_as_best_effort() -> None:
    jetstream = _FakeJetStream()
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
    )
    manager._jetstream = jetstream

    await manager.disconnect()

    assert jetstream.deleted
    assert manager.is_connected is False


@pytest.mark.asyncio
async def test_connect_treats_nats_api_cleanup_error_as_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    jetstream = _FakeJetStream(delete_error=JSAPIError(code=503, err_code=10008, description="unavailable"))

    async def _get_default_client() -> _FakeNATSClient:
        return _FakeNATSClient(jetstream)

    monkeypatch.setattr(ws_consumer_manager, "get_default_client", _get_default_client)
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
    )

    try:
        connected = await manager.connect()
        assert connected is True
        assert jetstream.deleted
    finally:
        await manager.disconnect()


@pytest.mark.asyncio
async def test_connect_returns_false_for_nats_api_subscribe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    jetstream = _FakeJetStream(subscribe_error=JSAPIError(code=503, err_code=10008, description="unavailable"))

    async def _get_default_client() -> _FakeNATSClient:
        return _FakeNATSClient(jetstream)

    monkeypatch.setattr(ws_consumer_manager, "get_default_client", _get_default_client)
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
    )

    connected = await manager.connect()

    assert connected is False
    assert manager.is_connected is False
    assert manager._jetstream is None
    assert manager._subscription is None


@pytest.mark.asyncio
async def test_connect_creates_missing_runtime_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    jetstream = _FakeJetStream(
        stream_info_error=JSConsumerNotFoundError(code=404, err_code=10059, description="stream not found")
    )

    async def _get_default_client() -> _FakeNATSClient:
        return _FakeNATSClient(jetstream)

    monkeypatch.setattr(ws_consumer_manager, "get_default_client", _get_default_client)
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
    )

    try:
        connected = await manager.connect()
        assert connected is True
        assert jetstream.streams_added == [ws_consumer_manager.JetStreamConstants.STREAM_NAME]
        assert manager.is_connected is True
    finally:
        await manager.disconnect()


def test_durable_token_can_prevent_client_id_collision() -> None:
    first = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="same-client",
        channels=["llm"],
        durable_token="connection-a-same-client",
    )
    second = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="same-client",
        channels=["llm"],
        durable_token="connection-b-same-client",
    )

    assert first._durable_name != second._durable_name


@pytest.mark.asyncio
async def test_consume_dropped_reports_jetstream_queue_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
    )
    manager._message_queue = asyncio.Queue(maxsize=1)
    manager._message_queue.put_nowait(
        ws_consumer_manager.RuntimeEventEnvelope(
            workspace_key="workspace",
            channel="llm",
            kind="task.updated",
            cursor=1,
            payload={"task_id": "prefill"},
        )
    )

    msg = _FakeMessage(
        payload={
            "schema_version": "runtime.v2",
            "workspace_key": "workspace",
            "channel": "llm",
            "kind": "task.updated",
            "payload": {"task_id": "task-1"},
        },
        stream_seq=42,
    )
    manager._subscription = _OneShotSubscription(msg)
    manager._closed = False

    original_wait_for = ws_consumer_manager.asyncio.wait_for

    async def _patched_wait_for(awaitable: Any, timeout: float) -> Any:
        if timeout == 5.0:
            close_awaitable = getattr(awaitable, "close", None)
            if callable(close_awaitable):
                close_awaitable()
            raise asyncio.TimeoutError
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(ws_consumer_manager.asyncio, "wait_for", _patched_wait_for)

    manager._consumer_task = asyncio.create_task(manager._consume_messages_loop())
    try:
        await asyncio.wait_for(msg.acked.wait(), timeout=1.0)
        assert msg.ack_calls == 1
        assert manager.consume_dropped() == 1
        assert manager.consume_dropped() == 0
        assert manager._pending_acks == {}
    finally:
        await manager.disconnect()


@pytest.mark.asyncio
async def test_ack_cursor_does_not_advance_when_no_pending_ack_matches() -> None:
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
        initial_cursor=7,
    )

    await manager.ack_cursor(99)

    assert manager.get_current_cursor() == 7


@pytest.mark.asyncio
async def test_ack_cursor_advances_only_to_acknowledged_pending_cursor() -> None:
    manager = JetStreamConsumerManager(
        workspace_key="workspace",
        client_id="client-1",
        channels=["llm"],
        initial_cursor=7,
    )
    msg = _FakeMessage(
        payload={
            "schema_version": "runtime.v2",
            "workspace_key": "workspace",
            "channel": "llm",
            "kind": "task.updated",
            "payload": {"task_id": "task-1"},
        },
        stream_seq=42,
    )
    manager._pending_acks = {42: msg}

    await manager.ack_cursor(99)

    assert msg.ack_calls == 1
    assert manager._pending_acks == {}
    assert manager.get_current_cursor() == 42
