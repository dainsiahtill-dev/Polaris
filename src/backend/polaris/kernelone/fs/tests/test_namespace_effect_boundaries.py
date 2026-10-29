"""Real effects survive failed acknowledgements; invalid leaves cannot block I/O."""

from __future__ import annotations

import multiprocessing
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import (
    KernelFileSystem,
    guarded_namespace_ops as operations,
    guarded_regular_file_snapshot as snapshots,
)
from polaris.kernelone.fs.guarded_namespace_ops import NamespaceOperationEffectUnknownError


def _filesystem(tmp_path: Path) -> tuple[Path, KernelFileSystem]:
    workspace = tmp_path / "workspace"
    (workspace / ".polaris" / "runtime").mkdir(parents=True)
    return workspace, KernelFileSystem(
        str(workspace),
        LocalFileSystemAdapter(),
        read_only_paths=True,
        runtime_owner_workspace=str(workspace),
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX FIFO and owned fork")
@pytest.mark.parametrize("operation", ["write_text", "append_text"])
def test_fifo_is_refused_without_waiting_for_a_reader(tmp_path: Path, operation: str) -> None:
    workspace, fs = _filesystem(tmp_path)
    pipe_path = workspace / ".polaris" / "runtime" / "pipe"
    os.mkfifo(pipe_path)
    receiver, sender = multiprocessing.get_context("fork").Pipe(duplex=False)

    def invoke() -> None:
        sender.send("ready")
        try:
            getattr(fs, operation)("runtime/pipe", "must not enter pipe", encoding="utf-8")
        except (OSError, RuntimeError) as exc:
            sender.send({"error": type(exc).__name__})
        else:
            sender.send({"returned": True})

    process = multiprocessing.get_context("fork").Process(target=invoke)
    process.start()
    try:
        assert receiver.poll(5), "child did not reach the filesystem call"
        assert receiver.recv() == "ready"
        assert receiver.poll(2), "filesystem open blocked waiting for a FIFO reader"
        result = receiver.recv()
        assert "error" in result
        assert stat.S_ISFIFO(pipe_path.lstat().st_mode)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        receiver.close()
        sender.close()
        assert not process.is_alive(), "owned diagnostic child was not reaped"


def test_failed_post_unlink_verification_reports_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, fs = _filesystem(tmp_path)
    fs.write_text("runtime/target", "before", encoding="utf-8")
    verify = operations._verify
    calls: list[bool] = []

    def fail_after_effect(witnesses: Any) -> None:
        verify(witnesses)
        calls.append(True)
        if len(calls) == 2:
            raise snapshots.GuardedRegularFileSnapshotError(
                "synthetic observation failure after actual unlink", code="guarded_snapshot_identity_drift"
            )

    monkeypatch.setattr(operations, "_verify", fail_after_effect)
    with pytest.raises(NamespaceOperationEffectUnknownError):
        fs.remove("runtime/target")
    assert not (workspace / ".polaris" / "runtime" / "target").exists()
    assert len(calls) == 2


def test_lost_replace_ack_keeps_committed_bytes_and_reports_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, fs = _filesystem(tmp_path)
    fs.write_text("runtime/target", "before", encoding="utf-8")
    replace = os.replace
    calls: list[bool] = []

    def commit_then_fail(*args: Any, **kwargs: Any) -> None:
        calls.append(True)
        replace(*args, **kwargs)
        raise OSError("synthetic acknowledgement lost after actual replace")

    monkeypatch.setattr(os, "replace", commit_then_fail)
    with pytest.raises(NamespaceOperationEffectUnknownError):
        fs.write_text_atomic("runtime/target", "committed", encoding="utf-8")
    assert (workspace / ".polaris" / "runtime" / "target").read_text(encoding="utf-8") == "committed"
    assert len(calls) == 1
    assert not list((workspace / ".polaris" / "runtime").glob(".polaris-namespace-stage*"))


def test_failed_directory_fsync_cannot_return_a_durable_append_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, fs = _filesystem(tmp_path)
    fsync = os.fsync

    def fail_directory(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic required parent fsync failure")
        fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(NamespaceOperationEffectUnknownError):
        fs.append_text("runtime/new", "committed", encoding="utf-8", durability="fsync")
    assert (workspace / ".polaris" / "runtime" / "new").read_text(encoding="utf-8") == "committed"


def test_create_lost_commit_ack_is_not_projected_as_pre_effect_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, fs = _filesystem(tmp_path)
    rename = snapshots._rename_guarded_leaf_noreplace
    calls: list[bool] = []

    def commit_then_fail(*args: Any, **kwargs: Any) -> None:
        calls.append(True)
        rename(*args, **kwargs)
        raise OSError("synthetic acknowledgement lost after create commit")

    monkeypatch.setattr(snapshots, "_rename_guarded_leaf_noreplace", commit_then_fail)
    with pytest.raises(NamespaceOperationEffectUnknownError):
        fs.workspace_create_text("new/leaf.txt", "committed", encoding="utf-8")
    assert (workspace / "new" / "leaf.txt").read_text(encoding="utf-8") == "committed"
    assert len(calls) == 1
