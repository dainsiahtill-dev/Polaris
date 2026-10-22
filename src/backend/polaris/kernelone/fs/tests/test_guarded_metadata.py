"""Native, read-only KFS observations reject stale or unsafe identities."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone import fs as public


@pytest.fixture
def fs(tmp_path: Path) -> Any:
    return public.KernelFileSystem(str(tmp_path), public.get_default_adapter())


def _fingerprint(info: Any) -> tuple[int, ...]:
    return (info.device, info.inode, info.mode, info.nlink, info.size, info.mtime_ns, info.ctime_ns)


def _native_fingerprint(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _error(code: str) -> Any:
    return pytest.raises(public.GuardedMetadataError, match=code)


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_public_metadata_api_and_existing_snapshot_export(fs: Any, tmp_path: Path) -> None:
    assert callable(getattr(public.KernelFileSystem, "workspace_lstat", None))
    (tmp_path / "file").write_text("中文", encoding="utf-8")
    metadata = fs.workspace_lstat("file")
    assert isinstance(metadata, public.FileMetadataV1)
    assert {f.name for f in fields(metadata)} == {
        "relative_path",
        "device",
        "inode",
        "mode",
        "nlink",
        "size",
        "mtime_ns",
        "ctime_ns",
        "root_device",
        "root_inode",
    }
    assert isinstance(fs.workspace_list_directory(".", max_entries=1), public.DirectoryListingV1)
    snapshot = public.read_guarded_regular_file_snapshot(str(tmp_path), "file", 64)
    assert isinstance(snapshot, public.GuardedRegularFileSnapshotV1)
    assert snapshot.content == bytes("中文", encoding="utf-8")
    assert not hasattr(snapshot, "mode") and not hasattr(snapshot, "nlink")


def test_metadata_root_regular_link_and_special_files_without_content_open(fs: Any, tmp_path: Path) -> None:
    (tmp_path / "file").write_text("正文", encoding="utf-8")
    (tmp_path / "link").symlink_to("missing")
    os.link(tmp_path / "file", tmp_path / "hard")
    os.mkfifo(tmp_path / "fifo")
    before = _fd_count()
    for name in (".", "file", "link", "hard", "fifo"):
        observed = fs.workspace_lstat(name)
        assert observed.relative_path == name
        assert _fingerprint(observed) == _native_fingerprint(tmp_path / name)
        assert (observed.root_device, observed.root_inode) == (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    assert fs.workspace_lstat(".") == fs.workspace_lstat(str(tmp_path))
    assert stat.S_ISFIFO(fs.workspace_lstat("fifo").mode)
    for name in ("hard", "fifo"):
        with pytest.raises(public.GuardedRegularFileSnapshotError):
            public.read_guarded_regular_file_snapshot(str(tmp_path), name, 64)
    assert _fd_count() == before


@pytest.mark.parametrize("count,bound,complete", [(0, 2, True), (2, 2, True), (3, 2, False)])
def test_listing_complete_exact_bound_and_truncated(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int, bound: int, complete: bool
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    for name in ("z", "a", "m")[:count]:
        (tmp_path / name).write_text(name, encoding="utf-8")
    native = os.scandir
    consumed: list[str] = []
    closed: list[bool] = []

    class Scanner:
        def __init__(self, fd: int) -> None:
            assert isinstance(fd, int)
            self.iterator = native(fd)

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self.iterator.close()
            closed.append(True)

        def __iter__(self) -> Any:
            return self

        def __next__(self) -> Any:
            entry = next(self.iterator)
            consumed.append(entry.name)
            return entry

    monkeypatch.setattr(module.os, "scandir", Scanner)
    monkeypatch.setattr(module.os, "supports_fd", os.supports_fd | {Scanner})
    before = _fd_count()
    listing = fs.workspace_list_directory(".", max_entries=bound)
    assert isinstance(listing.directory, public.FileMetadataV1)
    assert all(isinstance(entry, public.DirectoryEntryV1) for entry in listing.entries)
    assert listing.complete is complete
    assert len(listing.entries) == min(bound, count)
    assert len(consumed) <= bound + 1
    assert [e.name for e in listing.entries] == sorted(consumed[:bound])
    for entry in listing.entries:
        assert _fingerprint(entry.metadata) == _native_fingerprint(tmp_path / entry.name)
    assert closed == [True]
    assert _fd_count() == before


@pytest.mark.parametrize("bound", [False, True, 0, -1, 1.5, None])
def test_listing_rejects_invalid_bound_before_observation(fs: Any, bound: Any) -> None:
    with pytest.raises(ValueError):
        fs.workspace_list_directory("missing", max_entries=bound)


def test_readlink_expected_identity_and_no_dereference(fs: Any, tmp_path: Path) -> None:
    for index, target in enumerate(("不存在/中文", "/outside/secret", "../sibling")):
        name = f"link{index}"
        (tmp_path / name).symlink_to(target)
        metadata = fs.workspace_lstat(name)
        observed = fs.workspace_readlink(name, expected=metadata)
        assert isinstance(observed, public.SymlinkObservationV1)
        assert observed.target == target and observed.metadata == metadata
        for changed in (
            replace(metadata, inode=0),
            replace(metadata, root_inode=0),
            replace(metadata, relative_path="x"),
        ):
            with _error("guarded_metadata_identity_drift"):
                fs.workspace_readlink(name, expected=changed)
    (tmp_path / "regular").write_text("x", encoding="utf-8")
    for name in (".", "regular"):
        with _error("guarded_metadata_not_symlink"):
            fs.workspace_readlink(name)


def test_readlink_replacement_during_native_read_is_drift(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    link = tmp_path / "link"
    link.symlink_to("old")
    native = os.readlink

    def rebound(path: str, *, dir_fd: int) -> str:
        link.unlink()
        link.symlink_to("outside")
        return native(path, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "readlink", rebound)
    monkeypatch.setattr(module.os, "supports_dir_fd", os.supports_dir_fd | {rebound})
    before = _fd_count()
    with _error("guarded_metadata_identity_drift"):
        fs.workspace_readlink("link")
    assert _fd_count() == before


@pytest.mark.parametrize("level", ["root", "parent"])
@pytest.mark.parametrize("operation", ["lstat", "list_directory", "readlink"])
def test_retained_root_parent_rebinding_fails_closed(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str, operation: str
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "file").symlink_to("missing")
    native = module._retain_directory
    victim = tmp_path if level == "root" else parent
    moved = victim.with_name(victim.name + "-moved")

    def rebound(*args: Any, **kwargs: Any) -> Any:
        witness = native(*args, **kwargs)
        if kwargs["display_name"] == str(victim):
            victim.rename(moved)
            victim.mkdir()
        return witness

    monkeypatch.setattr(module, "_retain_directory", rebound)
    before = _fd_count()
    with _error("guarded_metadata_identity_drift"):
        if operation == "list_directory":
            fs.workspace_list_directory("parent", max_entries=3)
        else:
            getattr(fs, "workspace_" + operation)("parent/file")
    assert _fd_count() == before
    assert list(victim.iterdir()) == []


@pytest.mark.parametrize("operation", ["lstat", "list_directory", "readlink"])
@pytest.mark.parametrize("change", ["delete", "replace"])
def test_observed_leaf_disappearance_or_rebinding_is_drift(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, change: str
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    leaf = tmp_path / "leaf"
    leaf.symlink_to("first")
    native = module._stat_entry
    calls = 0

    def change_after_observation(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        result = native(*args, **kwargs)
        if args[1] == "leaf":
            calls += 1
            if calls == 1:
                leaf.unlink()
                if change == "replace":
                    leaf.symlink_to("different-long-target")
        return result

    monkeypatch.setattr(module, "_stat_entry", change_after_observation)
    before = _fd_count()
    with _error("guarded_metadata_identity_drift"):
        if operation == "list_directory":
            fs.workspace_list_directory(".", max_entries=3)
        else:
            getattr(fs, "workspace_" + operation)("leaf")
    assert _fd_count() == before


@pytest.mark.parametrize("path", ["", "..", "a/../b", "a//b", "a\\b", "a\x00b", "e\u0301"])
def test_path_errors_before_native_observation(fs: Any, path: str) -> None:
    for operation in (fs.workspace_lstat, fs.workspace_readlink):
        with pytest.raises(ValueError):
            operation(path)
    with pytest.raises(ValueError):
        fs.workspace_list_directory(path, max_entries=2)


def test_path_errors_capabilities_and_readonly_progress(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    (tmp_path / "file").write_text("数据", encoding="utf-8")
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        fs.workspace_lstat(str(tmp_path) + "-sibling/file")
    with _error("guarded_metadata_symlink_rejected"):
        fs.workspace_lstat("alias/file")
    with _error("guarded_metadata_symlink_rejected"):
        fs.workspace_list_directory("alias", max_entries=2)
    with _error("guarded_metadata_not_directory"):
        fs.workspace_list_directory("file", max_entries=2)
    with _error("guarded_metadata_missing"):
        fs.workspace_lstat("missing")
    before = {p.name: (_native_fingerprint(p), p.read_bytes() if p.is_file() else None) for p in tmp_path.iterdir()}
    fds = _fd_count()
    for _ in range(30):
        assert fs.workspace_lstat("file").size == 6
        assert fs.workspace_list_directory(".", max_entries=3).complete
    assert _fd_count() == fds
    assert before == {
        p.name: (_native_fingerprint(p), p.read_bytes() if p.is_file() else None) for p in tmp_path.iterdir()
    }
    assert not (tmp_path / ".polaris").exists()
    monkeypatch.setattr(module.os, "supports_dir_fd", os.supports_dir_fd - {os.readlink})
    with _error("guarded_metadata_capability_unavailable"):
        fs.workspace_readlink("alias")
    monkeypatch.setattr(module.os, "supports_fd", os.supports_fd - {os.scandir})
    with _error("guarded_metadata_capability_unavailable"):
        fs.workspace_list_directory(".", max_entries=3)


@pytest.mark.parametrize("name", ["e\u0301", "back\\slash", "bad:prefix"])
def test_listing_rejects_unrepresentable_names(fs: Any, tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("x", encoding="utf-8")
    with _error("guarded_metadata_invalid_name"):
        fs.workspace_list_directory(".", max_entries=3)


def test_permission_denial_distinct_from_missing(fs: Any, tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("real permission denial unavailable under root")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "file").write_text("x", encoding="utf-8")
    blocked.chmod(0)
    try:
        with _error("guarded_metadata_io_error") as error:
            fs.workspace_lstat("blocked/file")
        assert error.value.details["errno"] == 13
        assert error.value.__cause__ is not None
    finally:
        blocked.chmod(0o700)


def test_observations_only_open_directories(fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    (tmp_path / "file").write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to("/outside/secret")
    os.mkfifo(tmp_path / "fifo")
    native = os.open

    def directory_only(path: str, flags: int, *args: Any, **kwargs: Any) -> int:
        assert flags & os.O_DIRECTORY, "observation opened leaf content"
        return native(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", directory_only)
    monkeypatch.setattr(module.os, "supports_dir_fd", os.supports_dir_fd | {directory_only})
    for path in ("file", "link", "fifo"):
        assert fs.workspace_lstat(path).inode > 0
    listing = fs.workspace_list_directory(".", max_entries=5)
    assert listing.complete and len(listing.entries) == 3
    assert fs.workspace_readlink("link").target == "/outside/secret"


def test_listing_observed_directory_mode_drift_closes_iterator_and_fds(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    native = os.scandir
    closed: list[bool] = []
    original_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    changed_mode = original_mode ^ stat.S_IWUSR
    mode_changes: list[tuple[int, int]] = []

    class Scanner:
        def __init__(self, fd: int) -> None:
            self.fd = fd
            self.iterator = native(fd)

        def __enter__(self) -> Any:
            return self.iterator

        def __exit__(self, *args: Any) -> None:
            self.iterator.close()
            closed.append(True)
            (tmp_path / "new-after-eof").write_text("x", encoding="utf-8")
            # Insertion alone need not change any sampled directory field.
            # Change a compared field physically, without forcing timestamps.
            before_mode = os.fstat(self.fd).st_mode
            tmp_path.chmod(changed_mode)
            after_mode = os.fstat(self.fd).st_mode
            mode_changes.append((before_mode, after_mode))
            assert before_mode != after_mode
            assert stat.S_IMODE(after_mode) == changed_mode

    monkeypatch.setattr(module.os, "scandir", Scanner)
    monkeypatch.setattr(module.os, "supports_fd", os.supports_fd | {Scanner})
    before = _fd_count()
    try:
        with _error("guarded_metadata_identity_drift"):
            fs.workspace_list_directory(".", max_entries=3)
        assert len(mode_changes) == 1
        assert stat.S_IMODE(mode_changes[0][0]) == original_mode
    finally:
        tmp_path.chmod(original_mode)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == original_mode
    assert closed == [True] and _fd_count() == before


@pytest.mark.parametrize("operation", ["scandir", "readlink"])
def test_native_unsupported_observation_is_typed_and_closes_fds(
    fs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from polaris.kernelone.fs import guarded_metadata as module

    (tmp_path / "link").symlink_to("target")

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("native descriptor operation unsupported")

    monkeypatch.setattr(module.os, operation, unavailable)
    supported = "supports_fd" if operation == "scandir" else "supports_dir_fd"
    monkeypatch.setattr(module.os, supported, getattr(os, supported) | {unavailable})
    before = _fd_count()
    with _error("guarded_metadata_capability_unavailable") as error:
        if operation == "scandir":
            fs.workspace_list_directory(".", max_entries=3)
        else:
            fs.workspace_readlink("link")
    assert isinstance(error.value.__cause__, NotImplementedError)
    assert _fd_count() == before


def test_non_utf8_native_names_and_link_text_fail_explicitly(fs: Any, tmp_path: Path) -> None:
    raw_root = os.fsencode(tmp_path)
    os.symlink(b"bad\xfftext", raw_root + b"/link")
    with _error("guarded_metadata_invalid_name"):
        fs.workspace_readlink("link")
    fd = os.open(raw_root + b"/name\xff", os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    with _error("guarded_metadata_invalid_name") as error:
        fs.workspace_list_directory(".", max_entries=3)
    envelope = json.dumps({"code": error.value.code, "details": error.value.details}, ensure_ascii=False)
    envelope_path = tmp_path / "error-envelope.json"
    envelope_path.write_text(envelope, encoding="utf-8")
    persisted = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert bytes.fromhex(persisted["details"]["name_bytes_hex"]) == b"name\xff"
    assert error.value.__cause__ is not None


def test_root_alias_rejected_and_existing_root_write_rules_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    fs = public.KernelFileSystem(str(alias), public.get_default_adapter())
    for operation in (lambda: fs.workspace_lstat("."), lambda: fs.workspace_list_directory(".", max_entries=1)):
        with _error("guarded_metadata_symlink_rejected"):
            operation()
    fs = public.KernelFileSystem(str(root), public.get_default_adapter())
    assert fs.workspace_list_directory(str(root), max_entries=1).complete
    for path in (".", str(root)):
        with pytest.raises(ValueError):
            fs.workspace_mkdir(path, exist_ok=True)
    assert list(root.iterdir()) == []


def test_readlink_expected_link_replaced_by_regular_file_is_drift(fs: Any, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to("old")
    expected = fs.workspace_lstat("link")
    link.unlink()
    link.write_text("replacement", encoding="utf-8")
    with _error("guarded_metadata_identity_drift"):
        fs.workspace_readlink("link", expected=expected)


@pytest.mark.parametrize(
    "change,initial_code,expected_errno",
    [
        ("leaf_deleted", "missing", 2),
        ("parent_missing", "missing", 2),
        ("root_missing", "missing", 2),
        ("parent_not_directory", "not_directory", 20),
        ("root_not_directory", "not_directory", 20),
        ("parent_permission_denied", "io_error", 13),
    ],
)
def test_expected_readlink_preserves_prior_observation_across_structural_failure(
    tmp_path: Path, change: str, initial_code: str, expected_errno: int
) -> None:
    if change == "parent_permission_denied" and os.geteuid() == 0:
        pytest.skip("real permission denial unavailable under root")
    root = tmp_path / "workspace"
    parent = root / "parent"
    parent.mkdir(parents=True)
    link = parent / "link"
    link.symlink_to("target")
    fs = public.KernelFileSystem(str(root), public.get_default_adapter())
    expected = fs.workspace_lstat("parent/link")
    if change == "leaf_deleted":
        link.unlink()
    elif change == "parent_permission_denied":
        parent.chmod(0)
    else:
        victim = root if change.startswith("root_") else parent
        victim.rename(victim.with_name(victim.name + "-moved"))
        if change.endswith("not_directory"):
            victim.write_text("replacement", encoding="utf-8")
    before = _fd_count()
    try:
        for supplied, suffix in (
            (None, initial_code),
            (expected, "io_error" if expected_errno == 13 else "identity_drift"),
        ):
            with _error("guarded_metadata_" + suffix) as error:
                fs.workspace_readlink("parent/link", expected=supplied)
            assert error.value.details["errno"] == expected_errno
            assert error.value.__cause__ is not None
            assert _fd_count() == before
    finally:
        if change == "parent_permission_denied":
            parent.chmod(0o700)
