from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.bootstrap.config import Settings
from polaris.config.nats_config import NATSConfig
from polaris.delivery.http.app_factory import lifespan


def _make_app(settings: Settings) -> Any:
    return SimpleNamespace(state=SimpleNamespace(settings=settings))


def _patch_lifespan_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_container() -> object:
        return object()

    async def stream_info(_name: str) -> Any:
        return SimpleNamespace()

    async def fake_get_default_client() -> Any:
        return SimpleNamespace(is_connected=True, jetstream=SimpleNamespace(stream_info=stream_info))

    async def fake_close_default_client() -> None:
        return None

    async def fake_shutdown_local_nats_runtime() -> None:
        return None

    async def fake_start_factory_settlement_runtime(
        workspace: str,
        *,
        enable_wake_bridge: bool,
        wake_bridge_required: bool,
    ) -> object:
        del workspace, enable_wake_bridge, wake_bridge_required
        return object()

    async def fake_stop_factory_settlement_runtime(_workspace: str) -> bool:
        return True

    monkeypatch.setattr("polaris.infrastructure.di.container.reset_container", lambda: None)
    monkeypatch.setattr("polaris.infrastructure.di.container.get_container", fake_get_container)
    monkeypatch.setattr("polaris.cells.resident.autonomy.public.service.reset_resident_services", lambda: None)
    monkeypatch.setattr("polaris.bootstrap.assembly.assemble_core_services", lambda container, settings: None)
    monkeypatch.setattr("polaris.kernelone.process.terminate_external_loop_pm_processes", lambda workspace: [])
    monkeypatch.setattr("polaris.infrastructure.messaging.get_default_client", fake_get_default_client)
    monkeypatch.setattr("polaris.infrastructure.messaging.close_default_client", fake_close_default_client)
    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.shutdown_log_jetstream_publisher",
        lambda: None,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.shutdown_local_nats_runtime",
        fake_shutdown_local_nats_runtime,
    )
    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.public.start_factory_settlement_runtime",
        fake_start_factory_settlement_runtime,
    )
    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.public.stop_factory_settlement_runtime",
        fake_stop_factory_settlement_runtime,
    )
    monkeypatch.setattr(
        "polaris.cells.events.fact_stream.public.bootstrap_fact_stream_workspace",
        lambda _command: None,
    )


@pytest.mark.asyncio
async def test_lifespan_skips_managed_nats_when_nats_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)
    calls: list[str] = []

    async def fake_ensure_local_nats_runtime(
        url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        calls.append(url)

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )

    async def forbidden_client() -> Any:
        pytest.fail("disabled NATS connected a managed client")

    monkeypatch.setattr("polaris.infrastructure.messaging.get_default_client", forbidden_client)
    settings = Settings(
        workspace=tmp_path,
        nats=NATSConfig(enabled=False, required=False, url="nats://127.0.0.1:4222"),
    )

    async with lifespan(_make_app(settings)):
        pass

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_connects_managed_publisher_before_first_engineering_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from polaris.delivery.ws import engineering_events

    _patch_lifespan_dependencies(monkeypatch)
    connected = False
    calls: list[str] = []
    published: list[dict[str, Any]] = []

    from nats.js.errors import NotFoundError

    class JetStream:
        ready = False

        async def stream_info(self, name: str) -> Any:
            assert name == "HP_RUNTIME"
            if not self.ready:
                raise NotFoundError()
            return SimpleNamespace()

        async def add_stream(self, config: Any) -> None:
            assert config.name == "HP_RUNTIME"
            assert config.subjects == ["hp.runtime.>"]
            assert config.max_age == 604800
            self.ready = True
            calls.append("stream_ready")

    class Client:
        is_connected = True
        jetstream = JetStream()

        async def publish_js(self, **kwargs: Any) -> bool:
            assert connected, "producer reached transport before managed startup"
            assert self.jetstream.ready, "producer reached transport before runtime stream declaration"
            published.append(kwargs)
            calls.append("publish")
            return True

    client = Client()

    async def ensure_server(*args: Any, **kwargs: Any) -> None:
        calls.append("server_ready")

    async def connect_client() -> Client:
        nonlocal connected
        if not connected:
            calls.append("client_connected")
            connected = True
        return client

    async def close_client() -> None:
        nonlocal connected
        calls.append("client_closed")
        connected = False

    async def cleanup_server() -> None:
        calls.append("server_cleanup")

    monkeypatch.setattr("polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime", ensure_server)
    monkeypatch.setattr("polaris.infrastructure.messaging.get_default_client", connect_client)
    monkeypatch.setattr("polaris.infrastructure.messaging.close_default_client", close_client)
    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.shutdown_local_nats_runtime", cleanup_server
    )
    monkeypatch.setattr(engineering_events, "get_default_client", connect_client)
    monkeypatch.setattr(engineering_events, "get_default_client_snapshot", lambda: {"is_connected": connected})
    settings = Settings(workspace=tmp_path, nats=NATSConfig(enabled=True, required=True))
    async with lifespan(_make_app(settings)):
        # No health request or WebSocket consumer may be needed to start the
        # producer. Exercise the actual first-event publisher and envelope.
        assert await engineering_events.publish_engineering_event(
            {
                "workspace": str(tmp_path),
                "session_id": "first-run",
                "seq": 1,
                "role": "director",
                "kind": "run_created",
                "payload": {"status": "pending"},
                "created_at": 1.0,
            }
        )
        assert calls == ["server_ready", "client_connected", "stream_ready", "publish"]
        assert len(published) == 1
        envelope = published[0]["payload"]
        assert envelope["channel"] == "event.engineering"
        assert envelope["payload"]["session_id"] == "first-run"
        assert envelope["payload"]["seq"] == 1
    assert calls[-2:] == ["client_closed", "server_cleanup"]
    assert not connected


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("failure", ["raises", "disconnected", "native"])
async def test_lifespan_managed_client_failure_obeys_required_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, required: bool, failure: str
) -> None:
    from nats.errors import Error as NATSError, NoServersError

    _patch_lifespan_dependencies(monkeypatch)
    body_entered = False

    async def ensure_server(*args: Any, **kwargs: Any) -> None:
        return None

    async def connect_client() -> Any:
        if failure == "raises":
            raise OSError("managed client connection unavailable")
        if failure == "native":
            raise NoServersError()
        return SimpleNamespace(is_connected=False)

    monkeypatch.setattr("polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime", ensure_server)
    monkeypatch.setattr("polaris.infrastructure.messaging.get_default_client", connect_client)
    settings = Settings(workspace=tmp_path, nats=NATSConfig(enabled=True, required=required))
    if required:
        diagnostic = "no servers available" if failure == "native" else "managed.*client"
        with pytest.raises((OSError, RuntimeError, NATSError), match=diagnostic):
            async with lifespan(_make_app(settings)):
                body_entered = True
        assert not body_entered
    else:
        async with lifespan(_make_app(settings)):
            body_entered = True
        assert body_entered


@pytest.mark.asyncio
async def test_lifespan_enables_log_pipeline_jetstream_publish_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)
    monkeypatch.delenv("KERNELONE_JETSTREAM_PUBLISH", raising=False)

    async def fake_ensure_local_nats_runtime(
        _url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        return None

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=tmp_path,
        nats=NATSConfig(enabled=True, required=True, url="nats://127.0.0.1:4222"),
    )

    async with lifespan(_make_app(settings)):
        assert os.environ["KERNELONE_JETSTREAM_PUBLISH"] == "1"


@pytest.mark.asyncio
async def test_lifespan_continues_when_nats_optional_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)

    async def fake_ensure_local_nats_runtime(
        _url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        raise OSError("nats socket is unavailable")

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=tmp_path,
        nats=NATSConfig(enabled=True, required=False, url="nats://127.0.0.1:4222"),
    )

    async with lifespan(_make_app(settings)):
        pass


@pytest.mark.asyncio
async def test_lifespan_fails_closed_when_required_nats_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)

    async def fake_ensure_local_nats_runtime(
        _url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        raise RuntimeError("nats-server executable not found for managed local runtime")

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=tmp_path,
        nats=NATSConfig(enabled=True, required=True, url="nats://127.0.0.1:4222"),
    )

    with pytest.raises(RuntimeError, match="nats-server executable not found"):
        async with lifespan(_make_app(settings)):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [{"user": "configured-user"}, {"password": "configured-password"}])
async def test_config_only_credentials_deny_plaintext_autostart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, credentials: dict[str, Any]
) -> None:
    import socket

    from polaris.infrastructure.messaging.nats import server_runtime

    _patch_lifespan_dependencies(monkeypatch)
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "private-home"))
    monkeypatch.delenv("KERNELONE_NATS_USER", raising=False)
    monkeypatch.delenv("KERNELONE_NATS_PASSWORD", raising=False)
    native = server_runtime.ensure_local_nats_runtime

    async def ensure_with_security_intent(
        url: str, *, startup_timeout_seconds: float, allow_autostart: bool = True
    ) -> None:
        assert allow_autostart is False, "config credentials were not conveyed as autostart denial"
        await native(url, startup_timeout_seconds=startup_timeout_seconds, allow_autostart=allow_autostart)

    monkeypatch.setattr(server_runtime, "ensure_local_nats_runtime", ensure_with_security_intent)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    settings = Settings(workspace=tmp_path, nats=NATSConfig(url=f"nats://127.0.0.1:{port}", **credentials))
    with pytest.raises(RuntimeError, match="credentialed endpoint cannot autostart"):
        async with lifespan(_make_app(settings)):
            pass
    assert not (tmp_path / "private-home").exists()


@pytest.mark.asyncio
async def test_new_lifespan_never_starts_or_replays_legacy_project_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("new application touched a retired execution owner")

    monkeypatch.setattr("polaris.kernelone.process.terminate_external_loop_pm_processes", forbidden)
    monkeypatch.setattr("polaris.delivery.http.resident_autotick.maybe_start_resident_autotick", forbidden)
    monkeypatch.setattr("polaris.bootstrap.factory_run_driver_runtime.FactoryRunDriverRuntimeV1", forbidden)
    monkeypatch.setattr(
        "polaris.bootstrap.project_completion_convergence_runtime.configure_project_completion_convergence_runtime",
        forbidden,
    )
    monkeypatch.setattr("polaris.cells.factory.pipeline.public.start_factory_settlement_runtime", forbidden)
    monkeypatch.setattr("polaris.cells.instances.public.service.maybe_start_instance_watchdog", lambda: None)
    monkeypatch.setattr(
        "polaris.bootstrap.managed_process_receipt_owner.configure_managed_process_receipt_owner", lambda: None
    )
    monkeypatch.setattr(
        "polaris.bootstrap.project_verification_clients.configure_project_verification_clients", lambda: None
    )
    settings = Settings(workspace=tmp_path, nats=NATSConfig(enabled=False, required=False))
    async with lifespan(_make_app(settings)):
        assert not (tmp_path / ".polaris/runtime/factory").exists()
    assert not (tmp_path / ".polaris/runtime/factory").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "storage,retention,discard",
    [("file", "limits", "old"), ("memory", "workqueue", "new"), ("file", "interest", "old")],
)
async def test_runtime_stream_admin_uses_native_enum_and_seconds_contract(
    storage: str, retention: str, discard: str
) -> None:
    from nats.js.errors import NotFoundError
    from polaris.infrastructure.messaging.nats.jetstream_admin import JetStreamAdmin, JetStreamAdminConfig

    captured: list[dict[str, Any]] = []

    class JetStream:
        async def stream_info(self, _name: str) -> Any:
            raise NotFoundError()

        async def add_stream(self, config: Any) -> None:
            captured.append(config.as_dict())

    admin = JetStreamAdmin(
        SimpleNamespace(jetstream=JetStream()),
        JetStreamAdminConfig(
            stream_storage=storage, stream_retention=retention, stream_discard=discard, stream_max_age_seconds=42
        ),
    )
    assert await admin.ensure_stream_exists()
    assert len(captured) == 1
    assert captured[0]["storage"] == storage
    assert captured[0]["retention"] == retention
    assert captured[0]["discard"] == discard
    assert captured[0]["max_age"] == 42_000_000_000, "nats-py serializes seconds into wire nanoseconds exactly once"


@pytest.mark.asyncio
async def test_lifespan_private_jetstream_first_publish_late_tail_and_borrowed_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio
    import contextlib
    import shutil
    import signal
    import socket
    import subprocess
    import time

    from polaris.delivery.ws import engineering_events
    from polaris.infrastructure.messaging.nats import client as nats_client_module, server_runtime
    from polaris.infrastructure.messaging.nats.client import NATSClient, NATSConfig as TransportConfig
    from polaris.infrastructure.messaging.nats.jetstream_admin import JetStreamAdmin
    from polaris.infrastructure.messaging.nats.ws_consumer_manager import JetStreamConsumerManager

    executable = shutil.which("nats-server")
    if executable is None:
        pytest.skip("private native JetStream proof requires nats-server")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    address = f"nats://127.0.0.1:{port}"
    client = NATSClient(
        TransportConfig(servers=[address], allow_reconnect=False, max_reconnect_attempts=0, connect_timeout=0.2)
    )
    shutdown_server_attachment = server_runtime.shutdown_local_nats_runtime
    _patch_lifespan_dependencies(monkeypatch)
    # Keep real managed connection, stream bootstrap, server discovery and
    # shutdown; substitute only expensive unrelated application assembly.
    monkeypatch.setattr(nats_client_module, "_default_client", client)
    monkeypatch.setattr(nats_client_module, "_last_connect_failure_at", 0.0)
    monkeypatch.setattr(nats_client_module, "_last_connect_failure_error", None)
    monkeypatch.setattr("polaris.infrastructure.messaging.get_default_client", nats_client_module.get_default_client)
    monkeypatch.setattr(
        "polaris.infrastructure.messaging.close_default_client", nats_client_module.close_default_client
    )

    monkeypatch.setattr(server_runtime, "shutdown_local_nats_runtime", shutdown_server_attachment)
    settings = Settings(workspace=tmp_path, nats=NATSConfig(enabled=True, required=True, url=address))
    with (tmp_path / "private-nats.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            ["rtk", "proxy", executable, "-a", "127.0.0.1", "-p", str(port), "-js", "-sd", str(tmp_path / "jetstream")],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            encoding="utf-8",
        )
        manager: JetStreamConsumerManager | None = None
        try:
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "private JetStream never started"
                    await asyncio.sleep(0.02)
            assert not client.is_connected
            async with lifespan(_make_app(settings)):
                assert client.is_connected and client.jetstream is not None
                stream = await client.jetstream.stream_info("HP_RUNTIME")
                assert stream.state.messages == 0 and stream.config.max_age == 604800
                event = {
                    "workspace": str(tmp_path),
                    "session_id": "startup-before-websocket",
                    "seq": 1,
                    "role": "director",
                    "kind": "run_created",
                    "payload": {"status": "pending"},
                    "created_at": 1.0,
                }
                assert await engineering_events.publish_engineering_event(event)
                _, envelope = engineering_events.engineering_event_envelope(event)
                # A late subscriber must read the already persisted first fact.
                manager = JetStreamConsumerManager(
                    client_id="late-startup-consumer",
                    workspace_key=envelope.workspace_key,
                    channels=["event.engineering"],
                    tail=1,
                )
                assert await manager.connect()
                received = await manager.next_message(timeout=3)
                assert received is not None and received.channel == "event.engineering"
                assert received.payload["session_id"] == "startup-before-websocket"
                assert received.payload["seq"] == 1
                await manager.ack_cursor(received.cursor)
                await manager.disconnect()
                manager = None
                # Idempotent public bootstrap preserves existing stream data.
                assert await JetStreamAdmin(client).ensure_stream_exists()
                stream = await client.jetstream.stream_info("HP_RUNTIME")
                assert stream.state.messages == 1 and stream.config.max_age == 604800
            assert not client.is_connected and process.poll() is None
            borrower = NATSClient(
                TransportConfig(servers=[address], allow_reconnect=False, max_reconnect_attempts=0, connect_timeout=0.2)
            )
            try:
                await borrower.connect()
                assert borrower.jetstream is not None
                stream = await borrower.jetstream.stream_info("HP_RUNTIME")
                assert stream.state.messages == 1, "app shutdown destroyed the borrowed server/stream"
            finally:
                await borrower.disconnect()
        finally:
            if manager is not None:
                await manager.disconnect()
            await client.disconnect()
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            deadline = time.monotonic() + 3
            while True:
                with socket.socket() as closed:
                    closed.settimeout(0.1)
                    if closed.connect_ex(("127.0.0.1", port)) != 0:
                        break
                assert time.monotonic() < deadline, "test-owned NATS port did not close after process-group drain"
                await asyncio.sleep(0.02)
