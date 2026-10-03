from __future__ import annotations

import threading

import pytest
from nats.errors import NoServersError
from nats.js.api import StreamConfig
from nats.js.errors import NotFoundError, ServerError
from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher
from polaris.infrastructure.messaging.nats.client import NATSPayloadTooLargeError


def test_jetstream_publisher_skips_queue_when_nats_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "0")

    publisher = JetStreamPublisher()
    accepted = publisher.publish(
        subject="hp.runtime.workspace.system",
        payload={"message": "local disk write already succeeded"},
    )

    assert accepted is True
    assert publisher._thread is None


@pytest.mark.asyncio
async def test_oversized_payload_is_not_retried_or_reconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "1")
    publisher = JetStreamPublisher(max_attempts=6, retry_base_sec=0.05)
    publish_calls = 0
    reset_calls = 0

    class FakeClient:
        async def publish(self, subject: str, payload: dict[str, object]) -> bool:
            nonlocal publish_calls
            del subject, payload
            publish_calls += 1
            raise NATSPayloadTooLargeError("payload exceeds max_payload")

    async def fake_get_client() -> FakeClient:
        return FakeClient()

    async def fake_reset_client() -> None:
        nonlocal reset_calls
        reset_calls += 1

    monkeypatch.setattr(publisher, "_get_client", fake_get_client)
    monkeypatch.setattr(publisher, "_reset_client", fake_reset_client)

    from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublishRequest

    await publisher._publish_with_retry(JetStreamPublishRequest(subject="hp.runtime.test", payload={"x": "y"}))

    assert publish_calls == 1
    assert reset_calls == 0


def test_background_publisher_recovers_after_native_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A native NoServersError must not terminate the queue-consuming thread."""
    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "1")
    publisher = JetStreamPublisher(max_attempts=2, retry_base_sec=0.05)
    delivered = threading.Event()
    effects: list[tuple[str, dict[str, object]]] = []
    attempts = 0

    class Client:
        async def publish(self, subject: str, payload: dict[str, object]) -> bool:
            effects.append((subject, payload))
            delivered.set()
            return True

    async def connection() -> Client:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NoServersError()
        return Client()

    monkeypatch.setattr(publisher, "_get_client", connection)
    try:
        assert publisher.publish(subject="hp.runtime.private.test", payload={"sequence": 1})
        assert delivered.wait(2.0), "native connection failure killed publisher instead of bounded retry"
        assert effects == [("hp.runtime.private.test", {"sequence": 1})]
    finally:
        publisher.stop()


@pytest.mark.asyncio
async def test_native_missing_stream_creates_declared_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """NATS JS NotFoundError is the actual missing-stream exception."""
    publisher = JetStreamPublisher()
    created: list[StreamConfig] = []

    class JetStream:
        async def stream_info(self, name: str) -> None:
            assert name == "HP_RUNTIME"
            raise NotFoundError(code=404, err_code=10059)

        async def add_stream(self, config: StreamConfig) -> None:
            created.append(config)

    class Client:
        jetstream = JetStream()

    await publisher._ensure_runtime_stream(Client())
    assert len(created) == 1
    assert created[0].name == "HP_RUNTIME"


@pytest.mark.asyncio
async def test_connection_is_closed_when_stream_initialization_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.infrastructure.log_pipeline import jetstream_publisher as module

    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "1")
    clients: list[Client] = []

    class JetStream:
        async def stream_info(self, _name: str) -> None:
            raise ServerError(code=500)

    class Client:
        def __init__(self) -> None:
            self.is_connected = False
            self.jetstream = JetStream()
            clients.append(self)

        async def connect(self) -> None:
            self.is_connected = True

        async def disconnect(self) -> None:
            self.is_connected = False

    monkeypatch.setattr(module, "NATSClient", Client)
    publisher = JetStreamPublisher(max_attempts=1)
    await publisher._publish_with_retry(module.JetStreamPublishRequest("hp.runtime.private.test", {}))
    assert len(clients) == 1
    assert clients[0].is_connected is False
