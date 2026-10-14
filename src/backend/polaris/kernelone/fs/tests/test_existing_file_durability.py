"""A complete pre-crash file must be confirmed durable without another append."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from polaris.kernelone.fs import LockedRegularFileError, LockedRegularFileSetV1, locked_regular_file as module

_PATH = "runtime/events/.payloads/ab/blob.json"


def _acquire(tmp_path: Path, *, read_only: bool = False):
    root = tmp_path / "runtime"
    authority = tmp_path / "authority"
    LockedRegularFileSetV1.provision_authority(
        runtime_root=str(root), storage_identity_token="test-storage", platform_lock_root=str(authority)
    )
    LockedRegularFileSetV1.enroll_stream_lock_keys(
        runtime_root=str(root),
        storage_identity_token="test-storage",
        logical_paths=(_PATH,),
        platform_lock_root=str(authority),
    )
    return LockedRegularFileSetV1.acquire(
        runtime_root=str(root),
        storage_identity_token="test-storage",
        logical_paths=(_PATH,),
        platform_lock_root=str(authority),
        read_only=read_only,
    )


def test_existing_complete_bytes_are_fsynced_without_appending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _acquire(tmp_path) as files:
        lease = files.lease(_PATH)
        lease.append_bytes(b'{"payload":"complete-before-crash"}', fsync_file=False, fsync_parent_on_create=False)
        before = lease.read_bytes()
        file_identity = (tmp_path / "runtime/events/.payloads/ab/blob.json").stat().st_ino
        calls = []
        actual = module.os.fsync

        def fsync(fd: int) -> None:
            calls.append((os.fstat(fd).st_ino, os.fstat(fd).st_mode))
            actual(fd)

        monkeypatch.setattr(module.os, "fsync", fsync)
        confirm = getattr(lease, "confirm_durability", None)
        assert callable(confirm), "held complete file has no durable confirmation operation"
        confirm()
        assert lease.read_bytes() == before
        assert any(inode == file_identity for inode, _mode in calls)
        assert len(calls) >= 5  # File plus every held/identity-checked parent through runtime root.


@pytest.mark.parametrize("failure", ["readonly", "closed", "missing", "identity_drift"])
def test_confirmation_rejects_invalid_ownership_without_writes(tmp_path: Path, failure: str) -> None:
    files = _acquire(tmp_path)
    lease = files.lease(_PATH)
    if failure != "missing":
        lease.append_bytes(b"immutable", fsync_file=False, fsync_parent_on_create=False)
    target = tmp_path / "runtime/events/.payloads/ab/blob.json"
    if failure == "readonly":
        files.close()
        files = _acquire(tmp_path, read_only=True)
        lease = files.lease(_PATH)
        lease.open_existing()
    elif failure == "closed":
        files.close()
    elif failure == "identity_drift":
        target.rename(target.with_suffix(".old"))
        target.write_bytes(b"foreign replacement")
    before = target.read_bytes() if target.exists() else None
    try:
        with pytest.raises(LockedRegularFileError):
            lease.confirm_durability()
        assert (target.read_bytes() if target.exists() else None) == before
    finally:
        files.close()


def test_fsync_failure_stays_ambiguous_and_preserves_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _acquire(tmp_path) as files:
        lease = files.lease(_PATH)
        lease.append_bytes(b"immutable", fsync_file=False, fsync_parent_on_create=False)
        before = lease.read_bytes()

        def fail(_fd: int) -> None:
            raise OSError("fsync failed")

        monkeypatch.setattr(module.os, "fsync", fail)
        with pytest.raises(LockedRegularFileError) as error:
            lease.confirm_durability()
        assert error.value.code == "post_fsync_authority_reconciliation_required"
        assert lease.read_bytes() == before
