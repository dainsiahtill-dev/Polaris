from __future__ import annotations

import asyncio
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


def test_nats_client_connect_uses_imported_nats_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
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

    asyncio.run(_run())


def test_nats_client_default_servers_follow_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_NATS_URL", "nats://127.0.0.1:4555")

    client = nats_client_module.NATSClient()

    assert client._config.servers == ["nats://127.0.0.1:4555"]


def test_nats_config_numeric_fields_follow_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_NATS_MAX_RECONNECT", "-1")
    monkeypatch.setenv("KERNELONE_NATS_RECONNECT_WAIT", "0.25")
    monkeypatch.setenv("KERNELONE_NATS_CONNECT_TIMEOUT", "0.5")

    config = nats_client_module.NATSConfig()

    assert config.max_reconnect_attempts == -1
    assert config.reconnect_time_wait == 0.25
    assert config.connect_timeout == 0.5


def test_default_client_lock_uncontended_does_not_queue_on_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        async def _unexpected_to_thread(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("uncontended default-client lock must not use the executor")

        monkeypatch.setattr(nats_client_module.asyncio, "to_thread", _unexpected_to_thread)
        async with nats_client_module._acquire_default_client_lock():
            assert nats_client_module._default_client_lock.locked() is True

        assert nats_client_module._default_client_lock.locked() is False

    asyncio.run(_run())


def test_default_nats_client_disabled_policy_does_not_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        await nats_client_module.close_default_client()
        monkeypatch.setattr(nats_client_module, "_last_connect_failure_error", None)
        monkeypatch.setattr(nats_client_module, "_last_connect_failure_at", 0.0)
        monkeypatch.setenv("KERNELONE_NATS_ENABLED", "0")
        connect_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeNATSConnection:
            connect_calls.append((args, kwargs))
            return _FakeNATSConnection()

        monkeypatch.setattr(nats_client_module.nats, "connect", _fake_connect)

        client = await nats_client_module.get_default_client()

        assert client.is_disabled is True
        assert client.is_connected is False
        assert client.state == nats_client_module.ConnectionState.DISABLED
        assert connect_calls == []

        await nats_client_module.close_default_client()

    asyncio.run(_run())


def test_nats_client_self_heal_does_not_delete_runtime_stream_after_publish_failure() -> None:
    async def _run() -> None:
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
        assert fake_js.deleted_streams == []
        assert len(fake_js.added_configs) == 1
        assert fake_js.added_configs[0].name == "HP_RUNTIME"
        assert fake_js.added_configs[0].subjects == ["hp.runtime.>"]
        assert len(fake_js.publish_calls) == 2

    asyncio.run(_run())
