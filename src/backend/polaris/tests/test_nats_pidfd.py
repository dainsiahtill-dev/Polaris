"""Real owned-process pidfd compatibility checks; no shared host service."""

from __future__ import annotations

import ctypes
import errno
import os
import select
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.infrastructure.messaging.nats import pidfd, shared_service_identity, shared_service_runtime


def native_open(pid: int, flags: int = 0) -> int:
    function = ctypes.CDLL(None, use_errno=True).pidfd_open
    function.argtypes = [ctypes.c_int, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = int(function(pid, flags))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def native_signal(descriptor: int, sig: int, info: None = None, flags: int = 0) -> None:
    function = ctypes.CDLL(None, use_errno=True).pidfd_send_signal
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(descriptor, sig, info, flags) < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def require_kernel_signal_permission() -> None:
    """Independent libc probe distinguishes platform denial from adapter failure."""
    descriptor = native_open(os.getpid())
    try:
        try:
            native_signal(descriptor, 0)
        except OSError as exc:
            if exc.errno in {errno.EPERM, errno.ENOSYS}:
                pytest.skip(f"independent real self signal 0 denied: errno={exc.errno}")
            raise
    finally:
        os.close(descriptor)


def test_project_interpreter_has_real_pidfd_capability() -> None:
    """Missing Python wrappers must not reject a working kernel/libc pidfd pair."""
    require_kernel_signal_permission()
    shared_service_identity.require_capability()


@pytest.mark.parametrize("native_open_present", [False, True])
@pytest.mark.parametrize("native_signal_present", [False, True])
def test_real_descriptor_and_signal_dispatch(
    monkeypatch: pytest.MonkeyPatch, native_open_present: bool, native_signal_present: bool
) -> None:
    """C ABI shims exercise native delegation and mixed/missing wrapper routes."""
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_open", native_open if native_open_present else None)
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_send_signal", native_signal if native_signal_present else None)
    descriptor = pidfd.pidfd_open(os.getpid())
    try:
        assert os.readlink(f"/proc/self/fd/{descriptor}") == "anon_inode:[pidfd]"
        assert not os.get_inheritable(descriptor)
        try:
            native_signal(descriptor, 0)
        except OSError as expected:
            with pytest.raises(OSError) as result:
                pidfd.pidfd_send_signal(descriptor, 0)
            assert result.value.errno == expected.errno
        else:
            pidfd.pidfd_send_signal(descriptor, 0)
    finally:
        os.close(descriptor)
    with pytest.raises(OSError) as closed:
        os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("operation", ["open", "signal"])
@pytest.mark.parametrize("error", [errno.EPERM, errno.ENOSYS])
def test_native_denial_never_retries_through_libc(monkeypatch: pytest.MonkeyPatch, operation: str, error: int) -> None:
    def denied(*args: object) -> int:
        raise OSError(error, "controlled native denial")

    def no_fallback(*args: object) -> None:
        pytest.fail("denied native syscall retried through libc")

    monkeypatch.setattr(pidfd, "_libc_function", no_fallback)
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_open", denied if operation == "open" else native_open)
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_send_signal", denied)
    with pytest.raises(OSError) as result:
        if operation == "open":
            pidfd.pidfd_open(os.getpid())
        else:
            descriptor = pidfd.pidfd_open(os.getpid())
            try:
                pidfd.pidfd_send_signal(descriptor, 0)
            finally:
                os.close(descriptor)
    assert result.value.errno == error


@pytest.mark.parametrize("error", [errno.EPERM, errno.ENOSYS, errno.ESRCH])
@pytest.mark.parametrize("operation", ["open", "signal"])
def test_libc_errno_is_preserved(monkeypatch: pytest.MonkeyPatch, error: int, operation: str) -> None:
    def denied(*args: object) -> int:
        ctypes.set_errno(error)
        return -1

    monkeypatch.setattr(pidfd, "_stdlib_pidfd_open", None)
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_send_signal", None)
    monkeypatch.setattr(pidfd, "_libc", lambda: SimpleNamespace(pidfd_open=denied, pidfd_send_signal=denied))
    with pytest.raises(OSError) as result:
        if operation == "open":
            pidfd.pidfd_open(os.getpid())
        else:
            pidfd.pidfd_send_signal(999, 0)
    assert result.value.errno == error


def test_missing_libc_symbols_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_open", None)
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_send_signal", None)
    monkeypatch.setattr(pidfd, "_libc", SimpleNamespace)
    with pytest.raises(shared_service_identity.SharedNATSError) as result:
        shared_service_identity.require_capability()
    assert isinstance(result.value.__cause__, OSError)
    assert result.value.__cause__.errno == errno.ENOSYS
    with pytest.raises(OSError) as send_result:
        pidfd.pidfd_send_signal(999, 0)
    assert send_result.value.errno == errno.ENOSYS


def test_capability_signal_denial_closes_every_opened_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object) -> None:
        raise PermissionError(errno.EPERM, "controlled signal denial")

    monkeypatch.setattr(pidfd, "_stdlib_pidfd_open", native_open)
    monkeypatch.setattr(pidfd, "_stdlib_pidfd_send_signal", denied)
    before = set(os.listdir("/proc/self/fd"))
    for _ in range(40):
        with pytest.raises(shared_service_identity.SharedNATSError) as result:
            shared_service_identity.require_capability()
        assert result.value.code == "shared_nats_capability_unavailable"
        assert isinstance(result.value.__cause__, PermissionError)
    assert set(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("value", [1 << 32, -(1 << 32)])
def test_libc_integer_overflow_never_targets_truncated_pid_or_fd(value: int) -> None:
    with pytest.raises(OverflowError):
        pidfd.pidfd_open(value)
    with pytest.raises(OverflowError):
        pidfd.pidfd_send_signal(value, 0)
    with pytest.raises(OverflowError):
        pidfd.pidfd_send_signal(999, value)


def owned_child() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])


def test_real_owned_child_termination_and_exited_pidfd() -> None:
    child = owned_child()
    descriptor = pidfd.pidfd_open(child.pid)
    try:
        try:
            pidfd.pidfd_send_signal(descriptor, 0)
        except OSError as exc:
            if exc.errno in {errno.EPERM, errno.ENOSYS}:
                pytest.skip(f"real owned child signal denied: errno={exc.errno}; child exits naturally")
            raise
        pidfd.pidfd_send_signal(descriptor, signal.SIGTERM)
        assert child.wait(timeout=2) == -signal.SIGTERM
        with pytest.raises(ProcessLookupError) as exited:
            pidfd.pidfd_send_signal(descriptor, 0)
        assert exited.value.errno == errno.ESRCH
    finally:
        os.close(descriptor)
        child.wait(timeout=2)


def test_real_exited_process_descriptor_becomes_readable_and_cannot_reopen() -> None:
    child = owned_child()
    descriptor = pidfd.pidfd_open(child.pid)
    try:
        child.wait(timeout=2)
        waiter = select.poll()
        waiter.register(descriptor, select.POLLIN)
        assert waiter.poll(1000) == [(descriptor, select.POLLIN)]
        with pytest.raises(ProcessLookupError) as result:
            pidfd.pidfd_open(child.pid)
        assert result.value.errno == errno.ESRCH
    finally:
        os.close(descriptor)
        child.wait(timeout=2)


def test_drain_exit_between_poll_and_open_is_reaped(monkeypatch: pytest.MonkeyPatch) -> None:
    child = owned_child()
    original_open = shared_service_runtime.pidfd_open

    def exited_before_open(pid: int) -> int:
        child.wait(timeout=2)
        return original_open(pid)

    monkeypatch.setattr(shared_service_runtime, "pidfd_open", exited_before_open)
    shared_service_runtime._drain(child)
    assert child.returncode == 0


def test_drain_denied_signal_closes_fd_and_preserves_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object) -> None:
        raise PermissionError(errno.EPERM, "controlled drain denial")

    monkeypatch.setattr(shared_service_runtime, "pidfd_send_signal", denied)
    before = set(os.listdir("/proc/self/fd"))
    child = owned_child()
    try:
        with pytest.raises(PermissionError) as result:
            shared_service_runtime._drain(child)
        assert result.value.errno == errno.EPERM
        assert child.poll() is None
        assert set(os.listdir("/proc/self/fd")) == before
    finally:
        child.wait(timeout=2)


def test_drain_uses_already_pinned_descriptor_without_reopening_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_reopen(*args: object) -> int:
        pytest.fail("startup cleanup reopened PID instead of retaining its pinned descriptor")

    def denied(*args: object) -> None:
        raise PermissionError(errno.EPERM, "controlled signal denial")

    child = owned_child()
    descriptor = pidfd.pidfd_open(child.pid)
    try:
        monkeypatch.setattr(shared_service_runtime, "pidfd_open", forbid_reopen)
        monkeypatch.setattr(shared_service_runtime, "pidfd_send_signal", denied)
        with pytest.raises(PermissionError):
            shared_service_runtime._drain(child, descriptor=descriptor)
        assert os.readlink(f"/proc/self/fd/{descriptor}") == "anon_inode:[pidfd]"
        assert child.poll() is None
    finally:
        os.close(descriptor)
        child.wait(timeout=2)


def dead_service_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child: subprocess.Popen[bytes] | None = None
) -> dict:
    """A retired owned process record; only dead-record settlement is exercised."""
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
    store = tmp_path / "home" / "runtime" / "nats" / "jetstream"
    store.mkdir(parents=True)
    reap = child is None
    if child is None:
        child = owned_child()
    try:
        identity = shared_service_identity.process_identity(child.pid)
        identity.update(
            generation="polaris-shared-" + uuid.uuid4().hex,
            store=str(store),
            store_identity=shared_service_identity.directory_identity(store),
            host="127.0.0.1",
            port=4222,
            lock_descriptors=[[11, 1, 1], [12, 1, 2]],
        )
    finally:
        if reap:
            child.wait(timeout=2)
        else:
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
    with shared_service_runtime._control(
        shared_service_runtime._arguments(), time.monotonic() + 2, threading.Event()
    ) as control:
        shared_service_runtime._record(control.lease(shared_service_runtime._CONTROL), "READY", identity)
    return identity


def test_stop_retired_incarnation_requires_store_proof_and_free_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = dead_service_record(tmp_path, monkeypatch)
    # Isolate the post-admission lifecycle stage; real admission is tested above.
    monkeypatch.setattr(shared_service_runtime, "require_capability", lambda: None)
    shared_service_runtime.stop_shared_nats_runtime(identity, allow_service_disruption=True)
    with shared_service_runtime._control(
        shared_service_runtime._arguments(), time.monotonic() + 2, threading.Event()
    ) as control:
        assert shared_service_runtime._read_record(control.lease(shared_service_runtime._CONTROL))["state"] == "STOPPED"


def test_stop_retired_incarnation_rejects_replaced_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identity = dead_service_record(tmp_path, monkeypatch)
    store = Path(identity["store"])
    store.rename(store.with_name("retained-original"))
    store.mkdir()
    monkeypatch.setattr(shared_service_runtime, "require_capability", lambda: None)
    with pytest.raises(shared_service_identity.SharedNATSError) as result:
        shared_service_runtime.stop_shared_nats_runtime(identity, allow_service_disruption=True)
    assert result.value.code == "shared_nats_identity_conflict"


def test_stop_retired_record_with_busy_writer_cannot_settle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identity = dead_service_record(tmp_path, monkeypatch)
    monkeypatch.setattr(shared_service_runtime, "require_capability", lambda: None)
    held = shared_service_runtime._writer(shared_service_runtime._arguments())
    assert held is not None
    try:
        with pytest.raises(shared_service_identity.SharedNATSError) as result:
            shared_service_runtime.stop_shared_nats_runtime(identity, allow_service_disruption=True)
        assert result.value.code == "shared_nats_reconciliation_required"
        with shared_service_runtime._control(
            shared_service_runtime._arguments(), time.monotonic() + 2, threading.Event()
        ) as control:
            assert (
                shared_service_runtime._read_record(control.lease(shared_service_runtime._CONTROL))["state"] == "READY"
            )
    finally:
        held.close()


def test_stop_exited_unreaped_process_settles_without_signaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = owned_child()
    try:
        identity = dead_service_record(tmp_path, monkeypatch, child)
        monkeypatch.setattr(shared_service_runtime, "require_capability", lambda: None)

        def forbid_signal(*args: object) -> None:
            pytest.fail("stop tried to signal an exited incarnation")

        monkeypatch.setattr(shared_service_runtime, "pidfd_send_signal", forbid_signal)
        before = set(os.listdir("/proc/self/fd"))
        shared_service_runtime.stop_shared_nats_runtime(identity, allow_service_disruption=True)
        assert set(os.listdir("/proc/self/fd")) == before
        with shared_service_runtime._control(
            shared_service_runtime._arguments(), time.monotonic() + 2, threading.Event()
        ) as control:
            assert (
                shared_service_runtime._read_record(control.lease(shared_service_runtime._CONTROL))["state"]
                == "STOPPED"
            )
    finally:
        child.wait(timeout=2)
