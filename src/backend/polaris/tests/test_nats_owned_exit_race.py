"""Owned process exit must interrupt pending native NATS readiness probes."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from polaris.infrastructure.messaging.nats import server_runtime


def _owned_child() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1); sys.exit(7)"],
        stdin=subprocess.PIPE,
    )


def _release_child(child: subprocess.Popen[bytes]) -> None:
    assert child.stdin is not None
    child.stdin.write(b"x")
    child.stdin.flush()


async def _dispose_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        child.kill()
    await asyncio.to_thread(child.wait)
    if child.stdin is not None:
        child.stdin.close()


def _managed_server(tmp_path: Path, port: int, child: subprocess.Popen[bytes]) -> server_runtime.ManagedNATSServer:
    return server_runtime.ManagedNATSServer(
        executable=Path(sys.executable),
        host="127.0.0.1",
        port=port,
        storage_root=tmp_path / "store",
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
        process=child,
    )


@pytest.mark.asyncio
async def test_owned_exit_interrupts_pending_native_greeting(tmp_path: Path) -> None:
    # Break caught: awaiting INFO for the full budget hides exact owned exit.
    accepted = asyncio.Event()
    closed = asyncio.Event()

    async def silent_service(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.set()
        try:
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    endpoint = await asyncio.start_server(silent_service, "127.0.0.1", 0)
    child = _owned_child()
    server = _managed_server(tmp_path, int(endpoint.sockets[0].getsockname()[1]), child)
    task = asyncio.create_task(server.ensure_running(startup_timeout_seconds=2.0))
    try:
        await asyncio.wait_for(accepted.wait(), timeout=1.0)
        start = asyncio.get_running_loop().time()
        _release_child(child)
        with pytest.raises(RuntimeError, match="process_exited returncode=7"):
            await task
        assert asyncio.get_running_loop().time() - start < 0.8
        await asyncio.wait_for(closed.wait(), timeout=0.5)
        assert child.poll() == 7
        assert server.process is None
        assert endpoint.is_serving(), "unowned pending service was stopped"
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(RuntimeError, asyncio.CancelledError):
            await task
        await server.stop()
        await _dispose_child(child)
        endpoint.close()
        await endpoint.wait_closed()


@pytest.mark.asyncio
async def test_owned_exit_interrupts_pending_native_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: a saturated native TCP connect hides the owned child's exit.
    entered = asyncio.Event()
    connected = asyncio.Event()
    native_connect = asyncio.open_connection

    async def tracked_connect(host: str, port: int, *, limit: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        entered.set()
        result = await native_connect(host, port, limit=limit)
        connected.set()
        return result

    monkeypatch.setattr(server_runtime.asyncio, "open_connection", tracked_connect)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(0)
        port = int(listener.getsockname()[1])
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            child = _owned_child()
            server = _managed_server(tmp_path, port, child)
            task = asyncio.create_task(server.ensure_running(startup_timeout_seconds=2.0))
            try:
                await asyncio.wait_for(entered.wait(), timeout=1.0)
                await asyncio.sleep(0)
                assert not task.done() and not connected.is_set()
                start = asyncio.get_running_loop().time()
                _release_child(child)
                with pytest.raises(RuntimeError, match="process_exited returncode=7"):
                    await task
                assert asyncio.get_running_loop().time() - start < 0.8
                assert child.poll() == 7
                assert server.process is None
            finally:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                    await task
                await server.stop()
                await _dispose_child(child)


@pytest.mark.asyncio
async def test_owned_exit_wins_simultaneous_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the physical exit before returning readiness in the same probe turn.
    child = _owned_child()

    async def ready_after_exit(_host: str, _port: int, _timeout: float) -> str:
        _release_child(child)
        await asyncio.to_thread(child.wait)
        return "ready"

    monkeypatch.setattr(server_runtime, "_probe_nats_endpoint", ready_after_exit)
    try:
        with pytest.raises(RuntimeError, match="process_exited returncode=7"):
            await server_runtime._wait_until_nats_accepts("127.0.0.1", 0, 2.0, process=child)
    finally:
        await _dispose_child(child)


@pytest.mark.asyncio
async def test_repeated_cancellation_awaits_probe_cleanup_before_owned_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: repeated cancellation abandons the losing readiness Task.
    entered = asyncio.Event()
    cleanup_entered = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_finished = asyncio.Event()
    before = asyncio.all_tasks()
    child = _owned_child()
    server = _managed_server(tmp_path, 0, child)

    async def pending_probe(_host: str, _port: int, _timeout: float) -> str:
        entered.set()
        try:
            await asyncio.Event().wait()
            return "pending"
        finally:
            cleanup_entered.set()
            await cleanup_release.wait()
            cleanup_finished.set()

    monkeypatch.setattr(server_runtime, "_probe_nats_endpoint", pending_probe)
    task = asyncio.create_task(server.ensure_running(startup_timeout_seconds=2.0))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        task.cancel()
        await asyncio.wait_for(cleanup_entered.wait(), timeout=1.0)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done(), "owned lifecycle abandoned pending probe cleanup"
        assert server.process is child and child.poll() is None
        assert not cleanup_finished.is_set()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup_finished.is_set()
        assert child.poll() is not None and server.process is None
        assert not (asyncio.all_tasks() - before), "startup left a readiness/exit monitor pending"
    finally:
        cleanup_release.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await server.stop()
        await _dispose_child(child)


@pytest.mark.asyncio
async def test_pending_native_greeting_uses_one_total_owned_budget(tmp_path: Path) -> None:
    # Break caught: racing probes resets budget or returns without native close.
    closed = asyncio.Event()

    async def silent_service(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    endpoint = await asyncio.start_server(silent_service, "127.0.0.1", 0)
    child = _owned_child()
    server = _managed_server(tmp_path, int(endpoint.sockets[0].getsockname()[1]), child)
    start = asyncio.get_running_loop().time()
    try:
        with pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
            await server.ensure_running(startup_timeout_seconds=0.15)
        assert asyncio.get_running_loop().time() - start < 0.6
        await asyncio.wait_for(closed.wait(), timeout=0.5)
        assert child.poll() is not None and server.process is None
    finally:
        await server.stop()
        await _dispose_child(child)
        endpoint.close()
        await endpoint.wait_closed()
