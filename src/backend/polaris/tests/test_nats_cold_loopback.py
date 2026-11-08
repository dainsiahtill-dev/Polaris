"""Cold loopback admission regressions using private ports and native owner fences."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import nats
import pytest
from polaris.infrastructure.messaging.nats import server_runtime, shared_service_runtime as shared


@pytest.fixture
def private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("KERNELONE_HOME", str(home))
    monkeypatch.delenv("KERNELONE_NATS_USER", raising=False)
    monkeypatch.delenv("KERNELONE_NATS_PASSWORD", raising=False)
    monkeypatch.setattr(server_runtime, "_shared_attachment", None)
    monkeypatch.setattr(server_runtime, "_managed_server_lock", asyncio.Lock())
    return home


def _free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


@contextlib.contextmanager
def _connect_timeout(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Only asyncio TCP connection is stalled. Native shared coordinator and
    # OS socket binding remain real, including their storage and owner checks.
    async def stalled(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Future[None]()

    with monkeypatch.context() as transport:
        transport.setattr(asyncio, "open_connection", stalled)
        yield


@pytest.mark.asyncio
async def test_bindable_cold_connect_timeout_reaches_real_shared_owner(
    private_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled connect must not spend the whole budget before native admission."""
    if server_runtime.resolve_nats_server_executable() is None:
        pytest.skip("native NATS executable unavailable")
    port = _free_port()
    url = f"nats://127.0.0.1:{port}"
    try:
        with _connect_timeout(monkeypatch):
            await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=3.0)
        identity = dict(server_runtime._shared_attachment or {})
        assert identity["port"] == port
        assert Path(identity["store"]) == private_home / "runtime/nats/jetstream"
        assert identity["server_id"]
        client = await nats.connect(url, allow_reconnect=False, connect_timeout=1.0)
        try:
            await client.flush(timeout=1.0)
            await client.jetstream().add_stream(name="COLD", subjects=["cold.proof"])
            ack = await client.jetstream().publish("cold.proof", b"native readiness")
            assert ack.stream == "COLD" and ack.seq == 1
        finally:
            await client.close()
    finally:
        identity = server_runtime._shared_attachment
        if identity is not None:
            shared.stop_shared_nats_runtime(identity, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()


@pytest.mark.asyncio
async def test_occupied_connect_timeout_never_starts_shared_writer(
    private_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connect timeout plus a real listening socket must remain unknown readiness."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(0)
        port = listener.getsockname()[1]
        with _connect_timeout(monkeypatch), pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
            await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=0.2)
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
    assert server_runtime._shared_attachment is None
    assert not private_home.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("greeting", "delay", "error"),
    [
        (b'INFO {"jetstream":true}\r\n', 0.0, None),
        (b'INFO {"jetstream":true}\r\n', 0.4, None),
        (b"HTTP/1.1 200 OK\r\n", 0.0, "not a NATS JetStream service"),
    ],
)
async def test_listening_service_keeps_protocol_and_full_greeting_budget(
    private_home: Path, greeting: bytes, delay: float, error: str | None
) -> None:
    """Bounding connect must neither cap INFO wait nor adopt foreign protocols."""

    async def greet(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.sleep(delay)
            writer.write(greeting)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    endpoint = await asyncio.start_server(greet, "127.0.0.1", 0)
    port = endpoint.sockets[0].getsockname()[1]
    try:
        if error is None:
            await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=0.7)
        else:
            with pytest.raises(RuntimeError, match=error):
                await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=0.7)
        assert endpoint.is_serving()
        assert server_runtime._shared_attachment is None
        assert not private_home.exists()
    finally:
        endpoint.close()
        await endpoint.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["disabled", "url_credentials", "env_user", "env_password"])
async def test_cold_timeout_without_autostart_authority_has_no_effects(
    private_home: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    """A free port cannot turn credentialed/observe-only configuration into startup."""

    def forbidden_bind(*args: Any, **kwargs: Any) -> bool:
        pytest.fail("observe-only endpoint attempted an exclusive socket bind")

    monkeypatch.setattr(server_runtime, "_loopback_address_is_bindable", forbidden_bind)
    prefix = "user:password@" if policy == "url_credentials" else ""
    if policy == "env_user":
        monkeypatch.setenv("KERNELONE_NATS_USER", "test-user")
    if policy == "env_password":
        monkeypatch.setenv("KERNELONE_NATS_PASSWORD", "test-password")
    url = f"nats://{prefix}127.0.0.1:{_free_port()}"
    with _connect_timeout(monkeypatch), pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
        await server_runtime.ensure_local_nats_runtime(
            url, startup_timeout_seconds=0.15, allow_autostart=policy != "disabled"
        )
    assert server_runtime._shared_attachment is None
    assert not private_home.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("store_change", ["missing", "replacement"])
async def test_cold_connect_timeout_preserves_recorded_store_incarnation(
    private_home: Path, monkeypatch: pytest.MonkeyPatch, store_change: str
) -> None:
    """Bind proof must reach, never bypass, the native recorded-store fence."""
    executable = server_runtime.resolve_nats_server_executable()
    if executable is None:
        pytest.skip("native NATS executable unavailable")
    port = _free_port()
    store = private_home / "runtime/nats/jetstream"
    identity = await shared.ensure_shared_nats_runtime("127.0.0.1", port, executable, store, time.monotonic() + 3.0)
    shared.stop_shared_nats_runtime(identity, allow_service_disruption=True)
    marker = store / "retained-marker"
    marker.write_text("original bytes", encoding="utf-8")
    retained = store.with_name("retained-store")
    store.rename(retained)
    if store_change == "replacement":
        store.mkdir()
    journal = store.parent / "shared-control.jsonl"
    before = journal.read_bytes()
    with _connect_timeout(monkeypatch), pytest.raises(RuntimeError, match="shared_nats_identity_conflict"):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=1.5)
    assert journal.read_bytes() == before
    assert (retained / "retained-marker").read_text(encoding="utf-8") == "original bytes"
    assert store.exists() is (store_change == "replacement")
    assert server_runtime._shared_attachment is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("family", "host"), [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")])
async def test_availability_uses_native_address_family_and_releases_socket(
    monkeypatch: pytest.MonkeyPatch, family: int, host: str
) -> None:
    """IPv6 must use AF_INET6; an advisory reservation must not retain the port."""
    try:
        with socket.socket(family, socket.SOCK_STREAM) as reservation:
            reservation.bind((host, 0))
            port = reservation.getsockname()[1]
    except OSError:
        pytest.skip("requested loopback address family unavailable")
    with _connect_timeout(monkeypatch):
        state = await server_runtime._probe_nats_endpoint(host, port, 0.2, allow_autostart=True)
    assert state == "absent"
    with socket.socket(family, socket.SOCK_STREAM) as reusable:
        reusable.bind((host, port))


def test_failed_exclusive_bind_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permission/OS bind errors must never become absence or ownership proof."""

    class DeniedSocket(socket.socket):
        def bind(self, address: Any) -> None:
            raise PermissionError("private fixture denies availability probe")

    monkeypatch.setattr(socket, "socket", DeniedSocket)
    assert not server_runtime._loopback_address_is_bindable(socket.AF_INET, socket.SOCK_STREAM, 0, ("127.0.0.1", 1))
