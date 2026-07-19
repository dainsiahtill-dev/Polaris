"""Tests for descriptor-safe immutable regular-file snapshots."""

from __future__ import annotations

import errno
import os
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.fs import guarded_regular_file_snapshot as snapshot_module
from polaris.kernelone.fs.guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    GuardedRegularFileSnapshotV1,
    guarded_compare_and_replace_regular_file,
    read_guarded_regular_file_snapshot,
)


def _assert_error(
    root: Path,
    relative_path: str,
    max_bytes: Any,
    code: str,
) -> GuardedRegularFileSnapshotError:
    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        read_guarded_regular_file_snapshot(root, relative_path, max_bytes)
    assert exc_info.value.code == code
    return exc_info.value


def test_reads_utf8_as_exact_bytes_and_exports_public_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "nested" / "资料.txt"
    target.parent.mkdir(parents=True)
    payload = bytes("Polaris 雪原\n", encoding="utf-8")
    target.write_bytes(payload)

    snapshot = read_guarded_regular_file_snapshot(root, "nested/资料.txt", 4096)
    target_info = target.stat(follow_symlinks=False)
    root_info = root.stat(follow_symlinks=False)

    assert isinstance(snapshot, GuardedRegularFileSnapshotV1)
    assert snapshot.relative_path == "nested/资料.txt"
    assert snapshot.content == payload
    assert snapshot.size == len(payload)
    assert (snapshot.device, snapshot.inode) == (target_info.st_dev, target_info.st_ino)
    assert snapshot.mtime_ns == target_info.st_mtime_ns
    assert snapshot.ctime_ns == target_info.st_ctime_ns
    assert (snapshot.root_device, snapshot.root_inode) == (root_info.st_dev, root_info.st_ino)

    import polaris.kernelone.fs as public_fs

    assert public_fs.GuardedRegularFileSnapshotV1 is GuardedRegularFileSnapshotV1
    assert public_fs.read_guarded_regular_file_snapshot is read_guarded_regular_file_snapshot


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        ".",
        "..",
        "../secret.txt",
        "nested/../secret.txt",
        "/absolute.txt",
        "nested//file.txt",
        "nested/",
        "C:\\absolute.txt",
        "nested\\file.txt",
        unicodedata.normalize("NFD", "café.txt"),
    ],
)
def test_rejects_noncanonical_or_escaping_relative_paths(tmp_path: Path, relative_path: str) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ValueError, match="relative_path"):
        read_guarded_regular_file_snapshot(root, relative_path, 64)


def test_relative_path_has_fixed_1024_utf8_byte_limit(tmp_path: Path) -> None:
    root = tmp_path / "root"
    components = ["a" * 204] * 5
    relative_path = "/".join(components)
    assert len(bytes(relative_path, encoding="utf-8")) == 1024
    target = root.joinpath(*components)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"ok")

    assert read_guarded_regular_file_snapshot(root, relative_path, 8).content == b"ok"

    too_long = "/".join(["a" * 204] * 4 + ["b" * 205])
    assert len(bytes(too_long, encoding="utf-8")) == 1025
    with pytest.raises(ValueError, match="1024 UTF-8 bytes"):
        read_guarded_regular_file_snapshot(root, too_long, 8)

    multibyte_too_long = "é" * 513
    assert len(multibyte_too_long) < 1024
    assert len(bytes(multibyte_too_long, encoding="utf-8")) > 1024
    with pytest.raises(ValueError, match="1024 UTF-8 bytes"):
        read_guarded_regular_file_snapshot(root, multibyte_too_long, 8)


def test_root_must_be_nfc_and_canonical_without_double_leading_slashes(tmp_path: Path) -> None:
    canonical_root = tmp_path / "root"
    canonical_root.mkdir()
    (canonical_root / "file.bin").write_bytes(b"x")
    non_nfc_root = tmp_path / unicodedata.normalize("NFD", "café")
    non_nfc_root.mkdir()
    (non_nfc_root / "file.bin").write_bytes(b"x")

    invalid_roots = (
        str(non_nfc_root),
        "//" + str(canonical_root).lstrip("/"),
        str(canonical_root) + "/",
        str(canonical_root / ".." / "root"),
    )
    for invalid_root in invalid_roots:
        with pytest.raises(ValueError, match="root"):
            read_guarded_regular_file_snapshot(invalid_root, "file.bin", 8)


@pytest.mark.parametrize("max_bytes", [0, -1, False, 1.5, float("nan"), float("inf")])
def test_rejects_invalid_byte_bounds(tmp_path: Path, max_bytes: Any) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.bin").write_bytes(b"x")

    with pytest.raises(ValueError, match="finite positive integer"):
        read_guarded_regular_file_snapshot(root, "file.bin", max_bytes)


def test_rejects_missing_file_with_typed_error(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    _assert_error(root, "missing.bin", 64, "guarded_snapshot_missing")


def test_rejects_file_larger_than_strict_bound(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "large.bin").write_bytes(b"12345")

    _assert_error(root, "large.bin", 4, "guarded_snapshot_max_bytes_exceeded")


@pytest.mark.parametrize("link_location", ["root", "ancestor", "leaf"])
def test_rejects_symlink_at_root_ancestor_or_leaf(tmp_path: Path, link_location: str) -> None:
    actual_root = tmp_path / "actual-root"
    actual_root.mkdir()
    actual_dir = actual_root / "actual-dir"
    actual_dir.mkdir()
    (actual_dir / "file.bin").write_bytes(b"safe")

    if link_location == "root":
        root = tmp_path / "root-link"
        root.symlink_to(actual_root, target_is_directory=True)
        relative_path = "actual-dir/file.bin"
    elif link_location == "ancestor":
        root = actual_root
        (root / "dir-link").symlink_to(actual_dir, target_is_directory=True)
        relative_path = "dir-link/file.bin"
    else:
        root = actual_root
        (root / "leaf-link").symlink_to(actual_dir / "file.bin")
        relative_path = "leaf-link"

    _assert_error(root, relative_path, 64, "guarded_snapshot_symlink_rejected")


def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fifo = root / "stream"
    os.mkfifo(fifo)

    _assert_error(root, "stream", 64, "guarded_snapshot_not_regular")


def test_hard_linked_leaf_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.bin"
    source.write_bytes(b"data")
    os.link(source, root / "linked.bin")

    _assert_error(root, "linked.bin", 64, "guarded_snapshot_hard_link_rejected")


def test_growth_during_read_is_rejected_instead_of_truncated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "growing.bin"
    target.write_bytes(b"1234")
    real_read = snapshot_module.os.read
    mutated = False

    def grow_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal mutated
        chunk = real_read(fd, amount)
        if chunk and not mutated:
            mutated = True
            append_fd = os.open(target, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(append_fd, b"5")
                os.fsync(append_fd)
            finally:
                os.close(append_fd)
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", grow_after_first_read)

    _assert_error(root, "growing.bin", 4, "guarded_snapshot_max_bytes_exceeded")


def test_in_place_metadata_mutation_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "mutable.bin"
    target.write_bytes(b"stable")
    initial = target.stat(follow_symlinks=False)
    real_read = snapshot_module.os.read
    mutated = False

    def mutate_mtime_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal mutated
        chunk = real_read(fd, amount)
        if chunk and not mutated:
            mutated = True
            os.utime(
                target,
                ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000),
                follow_symlinks=False,
            )
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", mutate_mtime_after_first_read)

    _assert_error(root, "mutable.bin", 64, "guarded_snapshot_identity_drift")


def test_ancestor_identity_replacement_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    ancestor = root / "nested"
    ancestor.mkdir(parents=True)
    target = ancestor / "file.bin"
    target.write_bytes(b"stable")
    moved = root / "nested-original"
    real_read = snapshot_module.os.read
    replaced = False

    def replace_ancestor_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal replaced
        chunk = real_read(fd, amount)
        if chunk and not replaced:
            replaced = True
            ancestor.rename(moved)
            ancestor.mkdir()
            (ancestor / "file.bin").write_bytes(b"stable")
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", replace_ancestor_after_first_read)

    _assert_error(root, "nested/file.bin", 64, "guarded_snapshot_identity_drift")


def test_leaf_os_replace_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.bin"
    target.write_bytes(b"stable")
    replacement = root / "replacement.bin"
    replacement.write_bytes(b"stable")
    real_read = snapshot_module.os.read
    replaced = False

    def replace_leaf_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal replaced
        chunk = real_read(fd, amount)
        if chunk and not replaced:
            replaced = True
            os.replace(replacement, target)
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", replace_leaf_after_first_read)

    _assert_error(root, "file.bin", 64, "guarded_snapshot_identity_drift")


def test_leaf_gaining_hard_link_during_read_is_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.bin"
    target.write_bytes(b"stable")
    late_link = root / "late-link.bin"
    real_read = snapshot_module.os.read
    linked = False

    def add_hard_link_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal linked
        chunk = real_read(fd, amount)
        if chunk and not linked:
            linked = True
            os.link(target, late_link)
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", add_hard_link_after_first_read)

    _assert_error(root, "file.bin", 64, "guarded_snapshot_identity_drift")


def test_root_os_replace_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.bin"
    target.write_bytes(b"stable")
    moved_root = tmp_path / "root-original"
    replacement_root = tmp_path / "root-replacement"
    replacement_root.mkdir()
    (replacement_root / "file.bin").write_bytes(b"stable")
    real_read = snapshot_module.os.read
    replaced = False

    def replace_root_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal replaced
        chunk = real_read(fd, amount)
        if chunk and not replaced:
            replaced = True
            os.replace(root, moved_root)
            os.replace(replacement_root, root)
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", replace_root_after_first_read)

    _assert_error(root, "file.bin", 64, "guarded_snapshot_identity_drift")


@pytest.mark.parametrize("outcome", ["success", "read_error", "identity_drift"])
def test_all_opened_descriptors_close_for_terminal_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    target = root / "nested" / "file.bin"
    target.write_bytes(b"stable")
    initial = target.stat(follow_symlinks=False)
    real_open = snapshot_module.os.open
    real_close = snapshot_module.os.close
    real_read = snapshot_module.os.read
    opened: set[int] = set()
    closed: set[int] = set()
    mutated = False

    def tracking_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.add(fd)
        return fd

    def tracking_close(fd: int) -> None:
        closed.add(fd)
        real_close(fd)

    def terminal_read(fd: int, amount: int) -> bytes:
        nonlocal mutated
        if outcome == "read_error":
            raise OSError(errno.EIO, "injected guarded snapshot read failure")
        chunk = real_read(fd, amount)
        if outcome == "identity_drift" and chunk and not mutated:
            mutated = True
            os.utime(
                target,
                ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000),
                follow_symlinks=False,
            )
        return chunk

    monkeypatch.setattr(snapshot_module, "_has_required_descriptor_capabilities", lambda: True)
    monkeypatch.setattr(snapshot_module.os, "open", tracking_open)
    monkeypatch.setattr(snapshot_module.os, "close", tracking_close)
    monkeypatch.setattr(snapshot_module.os, "read", terminal_read)

    if outcome == "success":
        assert read_guarded_regular_file_snapshot(root, "nested/file.bin", 64).content == b"stable"
    else:
        expected_code = "guarded_snapshot_read_failed" if outcome == "read_error" else "guarded_snapshot_identity_drift"
        _assert_error(root, "nested/file.bin", 64, expected_code)

    assert opened
    assert closed == opened


def test_all_opened_descriptors_close_after_mid_traversal_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    real_open = snapshot_module.os.open
    real_close = snapshot_module.os.close
    opened: set[int] = set()
    closed: set[int] = set()

    def tracking_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.add(fd)
        return fd

    def tracking_close(fd: int) -> None:
        closed.add(fd)
        real_close(fd)

    monkeypatch.setattr(snapshot_module, "_has_required_descriptor_capabilities", lambda: True)
    monkeypatch.setattr(snapshot_module.os, "open", tracking_open)
    monkeypatch.setattr(snapshot_module.os, "close", tracking_close)

    _assert_error(root, "nested/missing.bin", 64, "guarded_snapshot_missing")

    assert opened
    assert closed == opened


def test_windows_and_missing_descriptor_capabilities_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.bin").write_bytes(b"x")

    monkeypatch.setattr(snapshot_module, "_platform_name", lambda: "nt")
    _assert_error(root, "file.bin", 64, "guarded_snapshot_capability_unavailable")

    monkeypatch.setattr(snapshot_module, "_platform_name", lambda: "posix")
    monkeypatch.setattr(snapshot_module, "_has_required_descriptor_capabilities", lambda: False)
    _assert_error(root, "file.bin", 64, "guarded_snapshot_capability_unavailable")


def test_guarded_compare_replace_success_is_exact_durable_and_public(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "nested" / "plan.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"old":true}\n')
    expected = read_guarded_regular_file_snapshot(root, "nested/plan.json", 4096)

    replaced = guarded_compare_and_replace_regular_file(
        root,
        expected,
        b'{"new":true}\n',
        max_bytes=4096,
    )

    assert replaced.content == b'{"new":true}\n'
    assert target.read_bytes() == replaced.content
    assert replaced.relative_path == expected.relative_path
    assert (replaced.device, replaced.inode) != (expected.device, expected.inode)
    assert list(target.parent.glob(".plan.json.*.tmp")) == []

    import polaris.kernelone.fs as public_fs

    assert public_fs.guarded_compare_and_replace_regular_file is guarded_compare_and_replace_regular_file


def test_guarded_compare_replace_rejects_old_snapshot_mismatch_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    target.write_bytes(b"external")

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_expected_mismatch"
    assert target.read_bytes() == b"external"
    assert list(root.glob(".plan.json.*.tmp")) == []


def test_guarded_compare_replace_detects_identity_drift_before_commit_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    external = root / "external.json"
    external.write_bytes(b"external")

    def replace_before_final_revalidation(parent_fd: int, leaf_name: str) -> None:
        del parent_fd, leaf_name
        os.replace(external, target)

    monkeypatch.setattr(
        snapshot_module,
        "_before_guarded_replace_revalidation",
        replace_before_final_revalidation,
    )

    with pytest.raises(GuardedRegularFileSnapshotError, match="changed"):
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert target.read_bytes() == b"external"
    assert list(root.glob(".plan.json.*.tmp")) == []


def test_guarded_compare_replace_swap_window_preserves_concurrent_leaf_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target swap at the commit entrance must not be overwritten as CAS success."""

    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    concurrent = root / "concurrent.json"
    concurrent.write_bytes(b"concurrent")
    concurrent_inode = concurrent.stat().st_ino
    real_replace = snapshot_module._replace_guarded_leaf

    def swap_target_then_replace(temp_name: str, leaf_name: str, parent_fd: int) -> None:
        os.replace(concurrent, target)
        real_replace(temp_name, leaf_name, parent_fd)

    monkeypatch.setattr(snapshot_module, "_replace_guarded_leaf", swap_target_then_replace)

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_expected_mismatch"
    assert target.read_bytes() == b"concurrent"
    assert target.stat().st_ino == concurrent_inode
    assert list(root.glob(".plan.json.*.tmp")) == []


def test_guarded_compare_replace_rollback_secondary_race_preserves_both_concurrent_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    first = root / "first.json"
    first.write_bytes(b"concurrent-one")
    first_inode = first.stat().st_ino
    second = root / "second.json"
    second.write_bytes(b"concurrent-two")
    second_inode = second.stat().st_ino
    real_replace = snapshot_module._replace_guarded_leaf

    def first_race(temp_name: str, leaf_name: str, parent_fd: int) -> None:
        os.replace(first, target)
        real_replace(temp_name, leaf_name, parent_fd)

    def second_race(parent_fd: int, temp_name: str, leaf_name: str) -> None:
        del parent_fd, temp_name, leaf_name
        os.replace(second, target)

    monkeypatch.setattr(snapshot_module, "_replace_guarded_leaf", first_race)
    monkeypatch.setattr(snapshot_module, "_before_guarded_rollback_exchange", second_race)

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_rollback_failed"
    assert target.read_bytes() == b"concurrent-one"
    assert target.stat().st_ino == first_inode
    recovery_entries = list(root.glob(".plan.json.*.tmp"))
    assert len(recovery_entries) == 1
    assert recovery_entries[0].read_bytes() == b"concurrent-two"
    assert recovery_entries[0].stat().st_ino == second_inode


def test_guarded_compare_replace_rollback_syscall_failure_retains_displaced_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    concurrent = root / "concurrent.json"
    concurrent.write_bytes(b"concurrent")
    concurrent_inode = concurrent.stat().st_ino
    real_replace = snapshot_module._replace_guarded_leaf
    real_exchange = snapshot_module._exchange_guarded_leaves
    exchanges = 0

    def commit_race(temp_name: str, leaf_name: str, parent_fd: int) -> None:
        os.replace(concurrent, target)
        real_replace(temp_name, leaf_name, parent_fd)

    def fail_second_exchange(left_name: str, right_name: str, parent_fd: int) -> None:
        nonlocal exchanges
        exchanges += 1
        if exchanges == 2:
            raise OSError(errno.EIO, "rollback exchange")
        real_exchange(left_name, right_name, parent_fd)

    monkeypatch.setattr(snapshot_module, "_replace_guarded_leaf", commit_race)
    monkeypatch.setattr(snapshot_module, "_exchange_guarded_leaves", fail_second_exchange)

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_rollback_failed"
    assert target.read_bytes() == b"new"
    recovery_entries = list(root.glob(".plan.json.*.tmp"))
    assert len(recovery_entries) == 1
    assert recovery_entries[0].read_bytes() == b"concurrent"
    assert recovery_entries[0].stat().st_ino == concurrent_inode


def test_guarded_compare_replace_missing_exchange_capability_fails_before_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    monkeypatch.setattr(snapshot_module, "_load_renameat2", lambda: None)

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_capability_unavailable"
    assert target.read_bytes() == b"old"
    assert list(root.glob(".plan.json.*.tmp")) == []


@pytest.mark.parametrize("failure", ["file_fsync", "replace"])
def test_guarded_compare_replace_precommit_failures_preserve_old_and_cleanup_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    if failure == "file_fsync":
        monkeypatch.setattr(snapshot_module.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, fd)))
    else:
        monkeypatch.setattr(
            snapshot_module,
            "_replace_guarded_leaf",
            lambda temp_name, leaf_name, parent_fd: (_ for _ in ()).throw(OSError(errno.EIO, leaf_name)),
        )

    with pytest.raises(GuardedRegularFileSnapshotError):
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert target.read_bytes() == b"old"
    assert list(root.glob(".plan.json.*.tmp")) == []


def test_guarded_compare_replace_parent_fsync_failure_is_typed_and_has_no_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    real_fsync = snapshot_module.os.fsync
    calls = 0

    def fail_parent_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "parent fsync")
        real_fsync(fd)

    monkeypatch.setattr(snapshot_module.os, "fsync", fail_parent_fsync)

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_parent_fsync_failed"
    assert target.read_bytes() == b"old"
    assert list(root.glob(".plan.json.*.tmp")) == []


def test_guarded_compare_replace_cleanup_failure_retains_displaced_old_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)
    monkeypatch.setattr(
        snapshot_module,
        "_cleanup_guarded_temp",
        lambda parent_fd, temp_name: (_ for _ in ()).throw(
            GuardedRegularFileSnapshotError(
                f"cleanup failed: {parent_fd}:{temp_name}",
                code="guarded_replace_temp_cleanup_failed",
            )
        ),
    )

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_temp_cleanup_failed"
    assert target.read_bytes() == b"new"
    recovery_entries = list(root.glob(".plan.json.*.tmp"))
    assert len(recovery_entries) == 1
    assert recovery_entries[0].read_bytes() == b"old"


def test_guarded_compare_replace_postread_detects_external_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "plan.json"
    target.write_bytes(b"old")
    expected = read_guarded_regular_file_snapshot(root, "plan.json", 64)

    def corrupt_after_replace(parent_fd: int, leaf_name: str) -> None:
        del parent_fd, leaf_name
        target.write_bytes(b"corrupt")

    monkeypatch.setattr(snapshot_module, "_after_guarded_replace", corrupt_after_replace)

    with pytest.raises(GuardedRegularFileSnapshotError) as exc_info:
        guarded_compare_and_replace_regular_file(root, expected, b"new", max_bytes=64)

    assert exc_info.value.code == "guarded_replace_postread_mismatch"
    assert target.read_bytes() == b"corrupt"
    assert list(root.glob(".plan.json.*.tmp")) == []
