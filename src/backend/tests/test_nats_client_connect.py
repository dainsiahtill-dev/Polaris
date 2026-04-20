from __future__ import annotations

from typing import Any

import pytest

from polaris.infrastructure.messaging.nats import client as nats_client_module


class _FakeNATSConnection:
    def __init__(self) -> None:
        self.is_connected = True
        self._jetstream = object()
        self.connected_url = "nats://localhost:4222"
        self.client_id = 123
        self.max_payload = 1048576

    def jetstream(self) -> object:
        return self._jetstream

    async def close(self) -> None:
        self.is_connected = False


class _FakeRepairingJetStream:
    def __init__(self) -> None:
        self.publish_calls: list[dict[str, Any]] = []
        self.deleted_streams: list[str] = []
        self.added_configs: list[Any] = []
        self._first_publish = True

    async def publish(
        self,
        subject: str,
        data: bytes,
        timeout: float | None = None,
        stream: str | None = None,
    ) -> dict[str, Any]:
        self.publish_calls.append(
            {
                "subject": subject,
                "timeout": timeout,
                "stream": stream,
                "payload": data.decode("utf-8"),
            }
        )
        if self._first_publish:
            self._first_publish = False
            raise RuntimeError("JetStream failed to store a msg block file")
        return {"stream": stream or "HP_RUNTIME", "seq": len(self.publish_calls)}

    async def delete_stream(self, stream_name: str) -> bool:
        self.deleted_streams.append(stream_name)
        return True

    async def add_stream(self, config: Any) -> Any:
        self.added_configs.append(config)
        return type("StreamInfo", (), {"config": config})()


@pytest.mark.asyncio
async def test_nats_client_connect_uses_imported_nats_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_connection = _FakeNATSConnection()
    captured_kwargs: dict[str, Any] = {}

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeNATSConnection:
        captured_kwargs["args"] = args
        captured_kwargs["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(nats_client_module.nats, "connect", _fake_connect)

    client = nats_client_module.NATSClient()
    await client.connect()

    assert client.is_connected is True
    assert client.jetstream is fake_connection._jetstream
    assert captured_kwargs["kwargs"]["name"] == "polaris"

    await client.disconnect()


def test_nats_client_default_servers_follow_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_NATS_URL", "nats://127.0.0.1:4555")

    client = nats_client_module.NATSClient()

    assert client._config.servers == ["nats://127.0.0.1:4555"]


@pytest.mark.asyncio
async def test_nats_client_repairs_runtime_stream_after_publish_failure() -> None:
    client = nats_client_module.NATSClient()
    fake_js = _FakeRepairingJetStream()
    fake_connection = _FakeNATSConnection()
    fake_connection._jetstream = fake_js
    client._nc = fake_connection
    client._js = fake_js

    published = await client.publish(
        "hp.runtime.demo.system",
        {"message": "ok"},
        timeout=1.0,
    )

    assert published is True
    assert fake_js.deleted_streams == ["HP_RUNTIME"]
    assert len(fake_js.added_configs) == 1
    assert fake_js.added_configs[0].name == "HP_RUNTIME"
    assert fake_js.added_configs[0].subjects == ["hp.runtime.>"]
    assert len(fake_js.publish_calls) == 2
