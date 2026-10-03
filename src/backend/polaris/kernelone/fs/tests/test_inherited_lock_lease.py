"""Native inherited-lock custody; subprocesses use only temporary paths."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from polaris.kernelone.fs.locked_regular_file import LockedRegularFileError, LockedRegularFileSetV1


def provision(tmp_path: Path) -> dict:
    arguments = {
        "runtime_root": str(tmp_path / "home"),
        "platform_lock_root": str(tmp_path / "authority"),
        "storage_identity_token": "native-transfer",
    }
    LockedRegularFileSetV1.provision_authority(**arguments)
    LockedRegularFileSetV1.enroll_stream_lock_keys(**arguments, logical_paths=("runtime/service/writer",))
    return arguments


def test_child_retains_lock_after_parent_ticket_close(tmp_path: Path) -> None:
    arguments = provision(tmp_path)
    locks = LockedRegularFileSetV1.acquire(**arguments, logical_paths=("runtime/service/writer",))
    assert hasattr(locks, "detach_for_inheritance"), "missing safe descriptor transfer API"
    ticket = locks.detach_for_inheritance()
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"], stdin=subprocess.PIPE, pass_fds=ticket.pass_fds
    )
    try:
        assert len(ticket.pass_fds) == 2
        assert all(not os.path.isdir(f"/proc/{child.pid}/fd/{fd}") for fd in ticket.pass_fds)
        ticket.validate_parent_binding()
        locks.close()
        ticket.close()
        with pytest.raises(LockedRegularFileError, match="deadline"):
            LockedRegularFileSetV1.acquire(**arguments, logical_paths=("runtime/service/writer",), timeout_seconds=0.02)
    finally:
        child.communicate(b"x", timeout=5)
        ticket.close()
    with LockedRegularFileSetV1.acquire(**arguments, logical_paths=("runtime/service/writer",), timeout_seconds=0.2):
        pass


def test_open_stream_cannot_be_transferred(tmp_path: Path) -> None:
    arguments = provision(tmp_path)
    with LockedRegularFileSetV1.acquire(**arguments, logical_paths=("runtime/service/writer",)) as locks:
        locks.lease("runtime/service/writer").append_bytes(b"hello\n", fsync_file=True, fsync_parent_on_create=True)
        assert hasattr(locks, "detach_for_inheritance"), "missing guarded descriptor transfer API"
        with pytest.raises(LockedRegularFileError, match="open stream"):
            locks.detach_for_inheritance()
        assert locks.lease("runtime/service/writer").read_bytes() == b"hello\n"


def test_contended_acquisition_closes_unheld_descriptor(tmp_path: Path) -> None:
    arguments = provision(tmp_path)
    with LockedRegularFileSetV1.acquire(**arguments, logical_paths=("runtime/service/writer",)):
        before = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(5):
            with pytest.raises(LockedRegularFileError, match="deadline"):
                LockedRegularFileSetV1.acquire(
                    **arguments, logical_paths=("runtime/service/writer",), timeout_seconds=0.01
                )
        assert len(list(Path("/proc/self/fd").iterdir())) == before, "contention leaked pre-append key descriptors"


def test_closed_transfer_cannot_export_reusable_descriptor_numbers(tmp_path: Path) -> None:
    arguments = provision(tmp_path)
    locks = LockedRegularFileSetV1.acquire(**arguments, logical_paths=("runtime/service/writer",))
    ticket = locks.detach_for_inheritance()
    ticket.close()
    with pytest.raises(LockedRegularFileError, match="closed"):
        _ = ticket.pass_fds
