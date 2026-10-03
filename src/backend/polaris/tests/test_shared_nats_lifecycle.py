"""Private native shared-service lifecycle tests; never use host NATS endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from polaris.infrastructure.messaging.nats import server_runtime


def free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


WORKER = """
import asyncio,json,sys
from polaris.infrastructure.messaging.nats import server_runtime as r
async def main():
    url=sys.argv[1]
    await r.ensure_local_nats_runtime(url,startup_timeout_seconds=5)
    print(json.dumps(r.get_managed_nats_runtime_snapshot(url)),flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    await r.shutdown_local_nats_runtime()
asyncio.run(main())
"""


def start_worker(home: Path, port: int, *identity_arguments: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, f"nats://127.0.0.1:{port}", *identity_arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "KERNELONE_HOME": str(home)},
        start_new_session=True,
    )


def ready(worker: subprocess.Popen[str]) -> dict:
    assert worker.stdout is not None
    line = worker.stdout.readline()
    assert line, worker.communicate(timeout=8)
    return json.loads(line)


def stop_exact(pidfd: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
    os.close(pidfd)


def close_worker(worker: subprocess.Popen[str]) -> None:
    if worker.poll() is None:
        worker.communicate("stop\n", timeout=8)


@pytest.mark.asyncio
async def test_backend_shutdown_does_not_destroy_shared_service(tmp_path: Path) -> None:
    if server_runtime.resolve_nats_server_executable() is None:
        pytest.skip("native NATS unavailable")
    port = free_port()
    owner = start_worker(tmp_path / "home", port)
    borrower = None
    pidfd = None
    try:
        state = ready(owner)
        pidfd = os.pidfd_open(state["process_pid"])
        borrower = start_worker(tmp_path / "home", port)
        ready(borrower)
        close_worker(owner)
        assert await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.3), (
            "backend shutdown destroyed shared writer"
        )
    finally:
        close_worker(owner)
        if borrower is not None:
            close_worker(borrower)
        if pidfd is not None:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            os.close(pidfd)


@pytest.mark.asyncio
async def test_instance_supervisor_stop_preserves_shared_writer(tmp_path: Path) -> None:
    from polaris.cells.instances.internal.service import InstanceRecord, InstanceRegistry, InstanceSupervisor

    port = free_port()
    backend_port = free_port()
    worker = start_worker(tmp_path / "home", port, "polaris.delivery.cli.backend", str(tmp_path), str(backend_port))
    state = ready(worker)
    descriptor = os.pidfd_open(state["process_pid"])
    try:
        # Full native stop_instance path, private registry and owned process fixture.
        registry = InstanceRegistry(tmp_path / "registry", publish_events=False)
        registry.save(
            InstanceRecord(
                instance_id="private-lifecycle",
                name="fixture",
                kind="project",
                polaris_root=str(tmp_path),
                workspace=str(tmp_path),
                runtime_root=str(tmp_path / ".polaris/runtime"),
                backend_port=backend_port,
                frontend_port=0,
                backend_url="",
                frontend_url="",
                token="private-test",
                backend_pid=worker.pid,
                start_frontend=False,
            )
        )
        stopped = await asyncio.to_thread(InstanceSupervisor(registry).stop_instance, "private-lifecycle")
        assert stopped["status"] == "stopped"
        worker.wait(timeout=3)
        assert await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.3)
    finally:
        close_worker(worker)
        stop_exact(descriptor)


def test_simultaneous_backends_share_one_native_writer(tmp_path: Path) -> None:
    port = free_port()
    workers = [start_worker(tmp_path / "home", port) for _ in range(4)]
    descriptors = {}
    try:
        states = [ready(worker) for worker in workers]
        for state in states:
            if state["process_pid"] not in descriptors:
                descriptors[state["process_pid"]] = os.pidfd_open(state["process_pid"])
        assert len({state["process_pid"] for state in states}) == 1
        assert len({state["service_generation"] for state in states}) == 1
    finally:
        for worker in workers:
            close_worker(worker)
        for descriptor in descriptors.values():
            stop_exact(descriptor)


@pytest.mark.asyncio
async def test_writer_crash_restarts_same_store_new_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.infrastructure.messaging.nats.shared_service_runtime import stop_shared_nats_runtime

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    port = free_port()
    url = f"nats://127.0.0.1:{port}"
    await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=3)
    old = dict(server_runtime._shared_attachment or {})
    descriptor = os.pidfd_open(old["pid"])
    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    os.close(descriptor)
    await asyncio.sleep(0.05)
    await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=3)
    current = dict(server_runtime._shared_attachment or {})
    try:
        assert current["generation"] != old["generation"]
        assert current["store_identity"] == old["store_identity"]
    finally:
        stop_shared_nats_runtime(current, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()


@pytest.mark.asyncio
async def test_malicious_identity_cannot_signal_unrelated_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.infrastructure.messaging.nats.shared_service_identity import SharedNATSError
    from polaris.infrastructure.messaging.nats.shared_service_runtime import stop_shared_nats_runtime

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=3)
    identity = dict(server_runtime._shared_attachment or {})
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        for forged in (
            {**identity, "pid": unrelated.pid},
            {**identity, "start_ticks": 1},
            {**identity, "store_identity": [0, 0]},
        ):
            with pytest.raises(SharedNATSError, match="identity_conflict"):
                stop_shared_nats_runtime(forged, allow_service_disruption=True)
            assert unrelated.poll() is None
        with pytest.raises(SharedNATSError, match="not_authorized"):
            stop_shared_nats_runtime(identity)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)
        stop_shared_nats_runtime(identity, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()


@pytest.mark.asyncio
async def test_credentials_in_environment_never_start_plaintext_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KERNELONE_NATS_USER", "private-test-user")
    with pytest.raises(RuntimeError, match="credentialed endpoint cannot autostart"):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=0.2)
    assert not (tmp_path / "home").exists()


@pytest.mark.asyncio
async def test_cancel_before_ready_drains_native_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    entered = __import__("threading").Event()
    native = shared._native_ready
    captured = []

    def slow_ready(identity, deadline, cancel):
        captured.append(dict(identity))
        entered.set()
        cancel.wait(2)
        return native(identity, deadline, cancel)

    monkeypatch.setattr(shared, "_native_ready", slow_ready)
    task = asyncio.create_task(
        server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=3)
    )
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured
    from polaris.infrastructure.messaging.nats.shared_service_identity import incarnation_dead

    assert incarnation_dead(captured[0])
    writer = shared._writer(shared._arguments())
    assert writer is not None
    writer.close()


@pytest.mark.asyncio
async def test_nonempty_legacy_store_requires_explicit_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    store = tmp_path / "home" / "runtime" / "nats" / "jetstream"
    store.mkdir(parents=True)
    marker = store / "unchanged"
    marker.write_text("legacy", encoding="utf-8")
    with pytest.raises(RuntimeError, match="migration_required"):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=1)
    assert marker.read_text(encoding="utf-8") == "legacy"


@pytest.mark.asyncio
async def test_offline_migration_retains_native_stream_and_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nats
    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared
    from polaris.infrastructure.messaging.nats.shared_service_identity import directory_identity, process_identity

    assert hasattr(shared, "migrate_shared_nats_runtime"), "explicit offline migration contract missing"
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    port = free_port()
    store = tmp_path / "home" / "runtime" / "nats" / "jetstream"
    store.mkdir(parents=True)
    executable = server_runtime.resolve_nats_server_executable()
    assert executable is not None
    legacy = subprocess.Popen(
        [str(executable), "-js", "-a", "127.0.0.1", "-p", str(port), "-sd", str(store)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    current = None
    try:
        deadline = time.monotonic() + 3
        while not await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.1):
            assert time.monotonic() < deadline
        old = {
            **process_identity(legacy.pid),
            "store": str(store),
            "store_identity": directory_identity(store),
            "host": "127.0.0.1",
            "port": port,
        }
        client = await nats.connect(f"nats://127.0.0.1:{port}")
        jetstream = client.jetstream()
        await jetstream.add_stream(name="PERSIST", subjects=["persist"])
        await jetstream.publish("persist", b"retained-native-message")
        await jetstream.pull_subscribe("persist", durable="survivor", stream="PERSIST")
        await client.close()
        with pytest.raises(RuntimeError, match="legacy_writer_alive"):
            await shared.migrate_shared_nats_runtime(
                old, executable, startup_timeout_seconds=3, allow_store_migration=True
            )
        legacy.terminate()
        legacy.wait(timeout=3)
        current = await shared.migrate_shared_nats_runtime(
            old, executable, startup_timeout_seconds=3, allow_store_migration=True
        )
        client = await nats.connect(f"nats://127.0.0.1:{port}")
        try:
            jetstream = client.jetstream()
            assert (await jetstream.stream_info("PERSIST")).state.messages == 1
            assert (await jetstream.consumer_info("PERSIST", "survivor")).name == "survivor"
            assert (await jetstream.get_msg("PERSIST", seq=1)).data == b"retained-native-message"
            assert current["store_identity"] == old["store_identity"]
        finally:
            await client.close()
    finally:
        if legacy.poll() is None:
            legacy.terminate()
            legacy.wait(timeout=3)
        if current:
            shared.stop_shared_nats_runtime(current, allow_service_disruption=True)


@pytest.mark.asyncio
async def test_cancel_waiting_control_lock_has_no_late_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    arguments = shared._arguments()
    held = shared._control(arguments, time.monotonic() + 2, threading.Event())
    task = asyncio.create_task(
        server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=2)
    )
    try:
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        held.close()
    await asyncio.sleep(0.08)
    assert not (tmp_path / "home" / "runtime" / "nats").exists()


@pytest.mark.asyncio
async def test_delayed_ready_publication_cannot_commit_after_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    entered, release = threading.Event(), threading.Event()
    native = shared._record

    def blocked_record(lease, state, identity):
        if state == "READY":
            entered.set()
            assert release.wait(2)
        return native(lease, state, identity)

    monkeypatch.setattr(shared, "_record", blocked_record)
    port = free_port()
    task = asyncio.create_task(
        server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=3)
    )
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done(), "canceled caller escaped while physical coordinator still active"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.03)
    assert not await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.1), (
        "late READY survived precommit cancellation"
    )


@pytest.mark.asyncio
async def test_incomplete_dead_identity_cannot_authorize_store_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    store = tmp_path / "home" / "runtime" / "nats" / "jetstream"
    store.mkdir(parents=True)
    (store / "retained").write_text("do not adopt", encoding="utf-8")
    with shared._control(shared._arguments(), time.monotonic() + 2, threading.Event()) as control:
        shared._record(control.lease(shared._CONTROL), "READY", {"pid": 1073741824})
    with pytest.raises(RuntimeError, match=r"identity_conflict|reconciliation_required"):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=2)


@pytest.mark.asyncio
async def test_shared_log_symlink_is_rejected_without_writing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    directory = tmp_path / "home" / "runtime" / "nats"
    directory.mkdir(parents=True)
    target = tmp_path / "protected.log"
    target.write_text("unchanged", encoding="utf-8")
    (directory / "nats-server.stderr.log").symlink_to(target)
    with pytest.raises((RuntimeError, OSError)):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=1)
    assert target.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.asyncio
async def test_symlink_store_alias_cannot_escape_managed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    directory = tmp_path / "home" / "runtime" / "nats"
    directory.mkdir(parents=True)
    target = tmp_path / "other-store"
    target.mkdir()
    (directory / "jetstream").symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="identity_conflict"):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=1)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("complete_identity", [True, False])
def test_creator_crash_window_preserves_fence_and_recovers_only_exact_identity(
    tmp_path: Path, complete_identity: bool
) -> None:
    port = free_port()
    if complete_identity:
        injection = """
from polaris.infrastructure.messaging.nats import shared_service_runtime as shared
def stalled(identity, deadline, cancel):
    print(json.dumps({"pid":identity["pid"]}),flush=True)
    __import__("time").sleep(30)
shared._native_ready=stalled
"""
    else:
        injection = """
from polaris.infrastructure.messaging.nats import shared_service_runtime as shared
original=shared.subprocess.Popen
def stalled(*args, **kwargs):
    child=original(*args, **kwargs)
    print(json.dumps({"pid":child.pid}),flush=True)
    __import__("time").sleep(30)
    return child
shared.subprocess.Popen=stalled
"""
    code = WORKER.replace("asyncio.run(main())", injection + "\nasyncio.run(main())")
    owner = subprocess.Popen(
        [sys.executable, "-c", code, f"nats://127.0.0.1:{port}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "KERNELONE_HOME": str(tmp_path / "home")},
        start_new_session=True,
    )
    borrower = None
    state = ready(owner)
    descriptor = os.pidfd_open(state["pid"])
    try:
        owner.kill()
        owner.wait(timeout=3)
        borrower = start_worker(tmp_path / "home", port)
        if complete_identity:
            recovered = ready(borrower)
            assert recovered["process_pid"] == state["pid"]
            assert recovered["ownership_mode"] == "shared_service"
        else:
            output, error = borrower.communicate(timeout=6)
            assert borrower.returncode != 0
            assert "shared_nats_reconciliation_required" in error
            assert output == ""
            # Unknown writer stays fenced/alive; no guessed adoption or replacement.
            from polaris.infrastructure.messaging.nats.shared_service_identity import probe_info

            assert probe_info("127.0.0.1", port, 0.2) is not None
    finally:
        close_worker(owner)
        if borrower is not None:
            close_worker(borrower)
        stop_exact(descriptor)


@pytest.mark.asyncio
async def test_cancel_during_real_spawn_waits_for_exact_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    entered, release = threading.Event(), threading.Event()
    original = shared.subprocess.Popen
    children = []

    def delayed_spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        entered.set()
        assert release.wait(2)
        return child

    monkeypatch.setattr(shared.subprocess, "Popen", delayed_spawn)
    port = free_port()
    task = asyncio.create_task(
        server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=3)
    )
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.03)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(children) == 1 and children[0].poll() is not None
    assert not await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.1)


@pytest.mark.asyncio
async def test_unsupported_managed_capability_fails_before_filesystem_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.infrastructure.messaging.nats import shared_service_identity

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    monkeypatch.delattr(shared_service_identity.os, "pidfd_open")
    with pytest.raises(RuntimeError, match="capability_unavailable"):
        await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=1)
    assert not (tmp_path / "home").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
async def test_provisional_recovery_cannot_publish_late_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: str
) -> None:
    import threading

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    port = free_port()
    url = f"nats://127.0.0.1:{port}"
    await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=3)
    identity = dict(server_runtime._shared_attachment or {})
    with shared._control(shared._arguments(), time.monotonic() + 2, threading.Event()) as control:
        shared._record(control.lease(shared._CONTROL), "STARTING", identity)
    entered, release = threading.Event(), threading.Event()
    native = shared._record

    def blocked_record(lease, state, proof):
        if state == "READY":
            entered.set()
            assert release.wait(2)
        return native(lease, state, proof)

    monkeypatch.setattr(shared, "_record", blocked_record)
    task = asyncio.create_task(server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=0.3))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if interrupt == "cancel":
            task.cancel()
            await asyncio.sleep(0.02)
        else:
            await asyncio.sleep(0.35)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError if interrupt == "cancel" else RuntimeError):
            await task
        with shared._control(shared._arguments(), time.monotonic() + 2, threading.Event()) as control:
            assert shared._read_record(control.lease(shared._CONTROL))["state"] == "STARTING"
        assert await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.2), (
            "borrowed writer must survive canceled recovery"
        )
    finally:
        release.set()
        shared.stop_shared_nats_runtime(identity, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()


@pytest.mark.asyncio
async def test_native_external_tls_service_is_not_adopted_or_stopped(tmp_path: Path) -> None:
    import ssl

    import nats

    key, certificate = tmp_path / "fixture.key", tmp_path / "fixture.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    executable = server_runtime.resolve_nats_server_executable()
    assert executable is not None
    port = free_port()
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
            "--tls",
            "--tlscert",
            str(certificate),
            "--tlskey",
            str(key),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while not await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.1):
            assert time.monotonic() < deadline
        context = ssl.create_default_context(cafile=str(certificate))
        context.check_hostname = False  # Private fixture certificate names localhost.
        client = await nats.connect(f"tls://127.0.0.1:{port}", tls=context)
        try:
            await server_runtime.ensure_local_nats_runtime(f"tls://127.0.0.1:{port}")
            await server_runtime.shutdown_local_nats_runtime()
            await client.flush(timeout=1)
            assert child.poll() is None and client.is_connected
        finally:
            await client.close()
    finally:
        child.terminate()
        child.wait(timeout=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_method", ["rename", "remove"])
async def test_recorded_store_disappearance_refuses_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_method: str
) -> None:
    import shutil

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    url = f"nats://127.0.0.1:{free_port()}"
    await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=3)
    identity = dict(server_runtime._shared_attachment or {})
    shared.stop_shared_nats_runtime(identity, allow_service_disruption=True)
    await server_runtime.shutdown_local_nats_runtime()
    store = Path(identity["store"])
    (store / "retained-native-store-marker").write_text("must remain original", encoding="utf-8")
    retained = store.with_name("retained-store")
    if missing_method == "rename":
        store.rename(retained)
    else:
        shutil.copytree(store, retained)
        shutil.rmtree(store)
    journal = store.parent / "shared-control.jsonl"
    before = journal.read_bytes()
    try:
        with pytest.raises(RuntimeError, match="identity_conflict"):
            await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=2)
        assert not store.exists(), "recorded store loss must not create a fresh directory"
        assert journal.read_bytes() == before, "failed restart must not publish a replacement generation"
        assert (retained / "retained-native-store-marker").read_text(encoding="utf-8") == "must remain original"
    finally:
        replacement = server_runtime._shared_attachment
        if replacement is not None:
            shared.stop_shared_nats_runtime(replacement, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_alive", [False, True])
@pytest.mark.parametrize("pidfd_available", [False, True])
async def test_unrelated_external_endpoint_remains_observe_only_with_home_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_alive: bool, pidfd_available: bool
) -> None:
    import errno

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    managed_port, external_port = free_port(), free_port()
    await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{managed_port}", startup_timeout_seconds=3)
    identity = dict(server_runtime._shared_attachment or {})
    if not managed_alive:
        shared.stop_shared_nats_runtime(identity, allow_service_disruption=True)
    await server_runtime.shutdown_local_nats_runtime()
    journal = Path(identity["store"]).parent / "shared-control.jsonl"
    before = journal.read_bytes()
    external = subprocess.Popen(
        [
            identity["executable"],
            "-js",
            "-a",
            "127.0.0.1",
            "-p",
            str(external_port),
            "-sd",
            str(tmp_path / "external-store"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while not await server_runtime._nats_protocol_ready("127.0.0.1", external_port, 0.1):
            assert time.monotonic() < deadline
        with monkeypatch.context() as admission:
            if not pidfd_available:

                def denied_pidfd(*args, **kwargs):
                    raise OSError(errno.ENOSYS, "private unavailable pidfd")

                admission.setattr(os, "pidfd_open", denied_pidfd)
            await server_runtime.ensure_local_nats_runtime(
                f"nats://127.0.0.1:{external_port}", startup_timeout_seconds=2
            )
        assert server_runtime._shared_attachment is None, "external endpoint must not gain managed ownership"
        await server_runtime.shutdown_local_nats_runtime()
        assert external.poll() is None
        assert journal.read_bytes() == before
        if managed_alive:
            assert await server_runtime._nats_protocol_ready("127.0.0.1", managed_port, 0.2)
    finally:
        external.terminate()
        external.wait(timeout=3)
        if managed_alive:
            shared.stop_shared_nats_runtime(identity, allow_service_disruption=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_alias", ["127.0.0.1", "localhost"])
async def test_ready_replacement_on_recorded_port_is_not_misclassified_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_alias: str
) -> None:
    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    port = free_port()
    await server_runtime.ensure_local_nats_runtime(f"nats://127.0.0.1:{port}", startup_timeout_seconds=3)
    original = dict(server_runtime._shared_attachment or {})
    shared.stop_shared_nats_runtime(original, allow_service_disruption=True)
    await server_runtime.shutdown_local_nats_runtime()
    journal = Path(original["store"]).parent / "shared-control.jsonl"
    before = journal.read_bytes()
    replacement = subprocess.Popen(
        [original["executable"], "-js", "-a", "127.0.0.1", "-p", str(port), "-sd", str(tmp_path / "external-store")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while not await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.1):
            assert time.monotonic() < deadline
        with pytest.raises(RuntimeError, match="shared_nats_external_service"):
            await server_runtime.ensure_local_nats_runtime(f"nats://{host_alias}:{port}", startup_timeout_seconds=2)
        assert journal.read_bytes() == before
        assert replacement.poll() is None
        assert server_runtime._shared_attachment is None
    finally:
        replacement.terminate()
        replacement.wait(timeout=3)


@pytest.mark.asyncio
async def test_ready_same_binding_keeps_native_identity_conflict_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from polaris.infrastructure.messaging.nats import shared_service_runtime as shared

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    port = free_port()
    url = f"nats://127.0.0.1:{port}"
    await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=3)
    original = dict(server_runtime._shared_attachment or {})
    forged = {**original, "start_ticks": original["start_ticks"] + 1}
    with shared._control(shared._arguments(), time.monotonic() + 2, threading.Event()) as control:
        shared._record(control.lease(shared._CONTROL), "READY", forged)
    journal = Path(original["store"]).parent / "shared-control.jsonl"
    before = journal.read_bytes()
    try:
        with pytest.raises(RuntimeError, match="identity_conflict"):
            await server_runtime.ensure_local_nats_runtime(url, startup_timeout_seconds=2)
        assert journal.read_bytes() == before
        assert await server_runtime._nats_protocol_ready("127.0.0.1", port, 0.2)
    finally:
        with shared._control(shared._arguments(), time.monotonic() + 2, threading.Event()) as control:
            shared._record(control.lease(shared._CONTROL), "READY", original)
        shared.stop_shared_nats_runtime(original, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_syscall", ["open_enosys", "open_eperm", "signal_eperm"])
async def test_unusable_pidfd_syscall_refuses_start_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_syscall: str
) -> None:
    import errno

    from polaris.infrastructure.messaging.nats import (
        shared_service_identity as native_identity,
        shared_service_runtime as shared,
    )

    home = tmp_path / "home"
    monkeypatch.setenv("KERNELONE_HOME", str(home))
    await server_runtime.shutdown_local_nats_runtime()
    actual_open, actual_signal = os.pidfd_open, signal.pidfd_send_signal
    opened = []

    def denied_open(pid, flags=0):
        if failed_syscall.startswith("open_"):
            raise OSError(errno.ENOSYS if failed_syscall == "open_enosys" else errno.EPERM, "fixture denied pidfd")
        descriptor = actual_open(pid, flags)
        opened.append(descriptor)
        return descriptor

    def denied_signal(descriptor, sig, *args):
        if sig == 0:
            raise PermissionError(errno.EPERM, "fixture denied signal probe")
        return actual_signal(descriptor, sig, *args)

    try:
        with monkeypatch.context() as injection:
            injection.setattr(native_identity.os, "pidfd_open", denied_open)
            if failed_syscall == "signal_eperm":
                injection.setattr(native_identity.signal, "pidfd_send_signal", denied_signal)
            with pytest.raises(RuntimeError, match="capability_unavailable"):
                await server_runtime.ensure_local_nats_runtime(
                    f"nats://127.0.0.1:{free_port()}", startup_timeout_seconds=2
                )
        assert not home.exists(), "capability denial must precede authority/store/log effects"
        assert server_runtime._shared_attachment is None
        for descriptor in opened:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        current = server_runtime._shared_attachment
        if current is not None:
            shared.stop_shared_nats_runtime(current, allow_service_disruption=True)
        await server_runtime.shutdown_local_nats_runtime()
