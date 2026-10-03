"""Native protocol/process regressions; no shared NATS or target workspace I/O."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from polaris.bootstrap.config import Settings
from polaris.config.nats_config import NATSConfig
from polaris.infrastructure.messaging.nats import server_runtime
from pydantic import ValidationError


def _server(tmp_path: Path, port: int) -> server_runtime.ManagedNATSServer:
    return server_runtime.ManagedNATSServer(
        executable=Path(sys.executable),
        host="127.0.0.1",
        port=port,
        storage_root=tmp_path / "store",
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
    )


def _living_child() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])


def _free_loopback_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


async def _greeting_server(greeting: bytes, delay: float = 0.0) -> asyncio.Server:
    async def greet(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.sleep(delay)
            writer.write(greeting)
            await writer.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    return await asyncio.start_server(greet, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_living_child_without_service_is_not_ready(tmp_path: Path) -> None:
    child = _living_child()
    server = _server(tmp_path, 0)
    server.process = child
    try:
        with pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
            await server.ensure_running(startup_timeout_seconds=0.08)
        assert server.process is None
        assert child.poll() is not None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_delayed_native_greeting_is_awaited_for_living_child(tmp_path: Path) -> None:
    endpoint = await _greeting_server(b'INFO {"jetstream":true}\r\n', delay=0.15)
    child = _living_child()
    server = _server(tmp_path, int(endpoint.sockets[0].getsockname()[1]))
    server.process = child
    start = time.monotonic()
    try:
        await server.ensure_running(startup_timeout_seconds=0.8)
        assert time.monotonic() - start >= 0.14
        assert child.poll() is None
    finally:
        await server.stop()
        endpoint.close()
        await endpoint.wait_closed()


@pytest.mark.asyncio
async def test_http_greeting_does_not_qualify_existing_endpoint(tmp_path: Path) -> None:
    endpoint = await _greeting_server(b"HTTP/1.1 200 OK\r\n\r\n")
    port = int(endpoint.sockets[0].getsockname()[1])
    try:
        with pytest.raises(RuntimeError, match="not a NATS JetStream"):
            await server_runtime.ensure_local_nats_runtime(
                f"nats://127.0.0.1:{port}",
                startup_timeout_seconds=0.1,
            )
        assert endpoint.is_serving()
    finally:
        endpoint.close()
        await endpoint.wait_closed()


@pytest.mark.asyncio
async def test_owned_early_exit_is_reported_before_startup_deadline(tmp_path: Path) -> None:
    server = _server(tmp_path, 0)
    start = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="process_exited"):
            await server.ensure_running(startup_timeout_seconds=2.0)
        assert time.monotonic() - start < 1.0
        assert server.process is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_startup_cancellation_drains_owned_living_child(tmp_path: Path) -> None:
    child = _living_child()
    server = _server(tmp_path, 0)
    server.process = child
    task = asyncio.create_task(server.ensure_running(startup_timeout_seconds=2.0))
    try:
        await asyncio.sleep(0.02)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.poll() is not None
        assert server.process is None
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await server.stop()


@pytest.mark.parametrize("budget", [0.0, -1.0, float("inf"), float("nan"), 91.0])
def test_startup_budget_rejects_unbounded_or_nonpositive_config(budget: float) -> None:
    with pytest.raises(ValidationError):
        NATSConfig(startup_timeout_sec=budget)


def test_startup_budget_environment_reaches_native_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_NATS_STARTUP_TIMEOUT", "17.5")
    assert Settings.from_env().nats.startup_timeout_sec == 17.5


def test_tls_loopback_endpoint_is_not_managed_as_plain_nats() -> None:
    assert server_runtime.should_manage_local_nats("tls://localhost:4222") is False


@pytest.mark.asyncio
async def test_startup_ownership_wait_obeys_same_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / ".polaris"))
    lock = asyncio.Lock()
    await lock.acquire()
    monkeypatch.setattr(server_runtime, "_managed_server_lock", lock)
    task = asyncio.create_task(
        server_runtime.ensure_local_nats_runtime(
            f"nats://127.0.0.1:{_free_loopback_port()}",
            startup_timeout_seconds=0.04,
        )
    )
    try:
        await asyncio.sleep(0.15)
        assert task.done(), "startup lock wait escaped invocation deadline"
        with pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
            await task
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        lock.release()


@pytest.mark.asyncio
async def test_real_native_nats_startup_and_owned_shutdown(tmp_path: Path) -> None:
    executable = server_runtime.resolve_nats_server_executable()
    if executable is None:
        pytest.skip("native NATS executable unavailable")
    server = _server(tmp_path, _free_loopback_port())
    server.executable = executable
    child: subprocess.Popen[bytes] | None = None
    try:
        await server.ensure_running(startup_timeout_seconds=5.0)
        child = server.process
        assert child is not None and child.poll() is None
        assert await server_runtime._nats_protocol_ready("127.0.0.1", server.port, 0.5)
    finally:
        await server.stop()
    assert child is not None and child.poll() is not None
    assert server.process is None


@pytest.mark.asyncio
async def test_auth_required_native_service_is_not_replaced(tmp_path: Path) -> None:
    executable = server_runtime.resolve_nats_server_executable()
    if executable is None:
        pytest.skip("native NATS executable unavailable")
    port = _free_loopback_port()
    server = _server(tmp_path, port)
    with (tmp_path / "auth.log").open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            [
                str(executable),
                "-js",
                "-a",
                "127.0.0.1",
                "-p",
                str(port),
                "-sd",
                str(tmp_path / "store"),
                "--user",
                "test-only-user",
                "--pass",
                "test-only-password",
            ],
            stdout=log,
            stderr=log,
        )
        server.process = child
        try:
            assert await server_runtime._wait_until_nats_accepts("127.0.0.1", port, 5.0, process=child)
            await server_runtime.ensure_local_nats_runtime(
                f"nats://test-only-user:test-only-password@127.0.0.1:{port}",
                startup_timeout_seconds=0.5,
            )
            assert child.poll() is None
        finally:
            await server.stop()


@pytest.mark.asyncio
async def test_slow_existing_greeting_uses_remaining_startup_budget(tmp_path: Path) -> None:
    endpoint = await _greeting_server(b'INFO {"jetstream":true}\r\n', delay=0.6)
    port = int(endpoint.sockets[0].getsockname()[1])
    try:
        await server_runtime.ensure_local_nats_runtime(
            f"nats://127.0.0.1:{port}",
            startup_timeout_seconds=2.0,
        )
        assert endpoint.is_serving()
        assert not (tmp_path / "store").exists()
    finally:
        endpoint.close()
        await endpoint.wait_closed()


@pytest.mark.asyncio
async def test_saturated_tcp_backlog_cannot_escape_startup_deadline() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(0)
        port = int(listener.getsockname()[1])
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            start = time.monotonic()
            with pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
                await server_runtime.ensure_local_nats_runtime(
                    f"nats://127.0.0.1:{port}",
                    startup_timeout_seconds=0.03,
                )
            assert time.monotonic() - start < 0.2


@pytest.mark.asyncio
async def test_transient_existing_socket_close_is_retried_without_replacement(tmp_path: Path) -> None:
    connections = 0

    async def greet(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections
        connections += 1
        if connections > 1:
            writer.write(b'INFO {"jetstream":true}\r\n')
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    endpoint = await asyncio.start_server(greet, "127.0.0.1", 0)
    port = int(endpoint.sockets[0].getsockname()[1])
    try:
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=0.8)
        assert connections == 2
        assert endpoint.is_serving()
        assert not (tmp_path / "store").exists()
    finally:
        endpoint.close()
        await endpoint.wait_closed()


@pytest.mark.asyncio
async def test_repeated_cancellation_during_cleanup_keeps_ownership_until_reaped(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(20)",
        ],
        stdout=subprocess.PIPE,
    )
    assert child.stdout is not None and child.stdout.readline().strip() == b"ready"
    server = _server(tmp_path, 0)
    server.process = child
    task = asyncio.create_task(server.ensure_running(startup_timeout_seconds=0.02))
    try:
        await asyncio.sleep(0.06)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.poll() is not None, "cleanup cancelled after ownership was discarded"
        assert server.process is None
    finally:
        if child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.wait)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        child.stdout.close()
