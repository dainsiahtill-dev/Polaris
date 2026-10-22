"""Native owner-bound atomic writes; synthetic faults mark exact effect boundaries."""

from __future__ import annotations

import errno
import os
import stat
import threading
from pathlib import Path

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import KernelFileSystem, guarded_atomic_write as native
from polaris.kernelone.fs.guarded_regular_file_snapshot import _FileFingerprint
from polaris.kernelone.fs.types import AtomicWriteBindingV1


def _fs(tmp_path: Path) -> KernelFileSystem:
    return KernelFileSystem(str(tmp_path), LocalFileSystemAdapter())


@pytest.mark.parametrize("content", ["你好\n", ""])
def test_native_workspace_write_stages_only_under_explicit_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """Catch target-parent temporary files and falsely non-atomic receipts."""
    fs = _fs(tmp_path)
    seen: list[Path] = []
    real_replace = os.replace

    def observe(source: str, target: str, **kwargs: int) -> None:
        if "src_dir_fd" in kwargs:
            seen.extend((tmp_path / ".polaris/runtime/fs/staging").rglob(source))
        else:
            seen.append(Path(source))
        real_replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", observe)
    receipt = fs.workspace_write_text_atomic("src/file.txt", content)
    assert (tmp_path / "src/file.txt").read_text(encoding="utf-8") == content
    assert receipt.bytes_written == len(content.encode("utf-8"))
    assert seen and all(path.is_relative_to(tmp_path / ".polaris/runtime/fs/staging") for path in seen)
    assert not list((tmp_path / ".polaris/runtime/fs/staging").iterdir())
    assert receipt.atomic is True


def test_receipt_path_failure_occurs_before_native_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catch path conversion performed after a successful physical rename."""
    fs = _fs(tmp_path)
    fs.workspace_write_text("target.txt", "old")

    def reject(_path: str) -> str:
        raise ValueError("synthetic receipt path rejection")

    monkeypatch.setattr(fs, "to_workspace_relative_path", reject)
    with pytest.raises(ValueError, match="synthetic receipt"):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "old"


def test_native_replace_preserves_parent_modes_and_ordinary_overwrite(tmp_path: Path) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text("existing/target.txt", "old")
    (tmp_path / "existing").chmod(0o750)
    before = (tmp_path / "existing/target.txt").stat().st_ino
    previous = os.umask(0o027)
    try:
        fs.workspace_write_text_atomic("existing/target.txt", "new")
        fs.workspace_write_text_atomic("new/child/target.txt", "new")
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "existing").stat().st_mode) == 0o750
    assert stat.S_IMODE((tmp_path / "new/child").stat().st_mode) == 0o750
    assert stat.S_IMODE((tmp_path / "existing/target.txt").stat().st_mode) == 0o600
    assert (tmp_path / "existing/target.txt").stat().st_ino != before
    assert (tmp_path / "existing/target.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_leaf_cannot_change_external_bytes(tmp_path: Path, kind: str) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text("outside.txt", "keep")
    if kind == "symlink":
        (tmp_path / "target.txt").symlink_to(tmp_path / "outside.txt")
    elif kind == "hardlink":
        os.link(tmp_path / "outside.txt", tmp_path / "target.txt")
    else:
        os.mkfifo(tmp_path / "target.txt")
    with pytest.raises((RuntimeError, ValueError, PathSecurityError)):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("component", ["parent", "owner", "staging"])
def test_retained_directory_rebinding_rejects_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    owner, outside = tmp_path / "owner", tmp_path / "outside"
    owner.mkdir()
    outside.mkdir()
    fs = _fs(owner)
    fs.workspace_write_text("src/target.txt", "old")
    original = native._sync_directories
    rebound = False

    def rebind(directories: list[native._DirectoryWitness]) -> None:
        nonlocal rebound
        original(directories)
        if rebound:
            return
        rebound = True
        path = {"parent": owner / "src", "owner": owner, "staging": owner / ".polaris/runtime/fs/staging"}[component]
        path.rename(path.with_name(path.name + "-retained"))
        path.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(native, "_sync_directories", rebind)
    with pytest.raises(RuntimeError):
        fs.workspace_write_text_atomic("src/target.txt", "new")
    assert not list(outside.iterdir())
    old_path = (
        owner.with_name("owner-retained") / "src/target.txt"
        if component == "owner"
        else owner / ("src-retained/target.txt" if component == "parent" else "src/target.txt")
    )
    assert old_path.read_text(encoding="utf-8") == "old"


def test_stage_collision_does_not_cleanup_someone_elses_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text_atomic("target.txt", "old")
    collision = tmp_path / ".polaris/runtime/fs/staging/atomic-collision"
    collision.mkdir()
    fs.workspace_write_text(str(collision / "keep"), "unrelated")

    class Identity:
        hex = "collision"

    monkeypatch.setattr(native.uuid, "uuid4", Identity)
    with pytest.raises(FileExistsError):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert (collision / "keep").read_text(encoding="utf-8") == "unrelated"
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("phase", ["stage_write", "file_fsync", "rename"])
def test_failure_before_rename_keeps_old_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text_atomic("target.txt", "old")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "synthetic pre-effect failure")

    if phase == "stage_write":
        monkeypatch.setattr(native, "_write_all", fail)
    elif phase == "file_fsync":
        monkeypatch.setattr(native.os, "fsync", fail)
    else:
        monkeypatch.setattr(native.os, "replace", fail)
    with pytest.raises(OSError, match="synthetic pre-effect"):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "old"
    assert not list((tmp_path / ".polaris/runtime/fs/staging").iterdir())


@pytest.mark.parametrize("phase", ["commit_directory_fsync", "stage_cleanup", "committed_identity"])
def test_post_rename_failure_keeps_complete_bytes_and_explicit_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text_atomic("target.txt", "old")
    real_sync = native._sync_directories
    calls = 0

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "synthetic post-effect failure")

    def sync(directories: list[native._DirectoryWitness]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            fail()
        real_sync(directories)

    if phase == "commit_directory_fsync":
        monkeypatch.setattr(native, "_sync_directories", sync)
    elif phase == "stage_cleanup":
        monkeypatch.setattr(native, "_clean_stage", fail)
    else:
        real_verify = native._verify_leaf_entry

        def verify(parent_fd: int, name: str, expected: _FileFingerprint) -> None:
            if name == "target.txt":
                fail()
            real_verify(parent_fd, name, expected)

        monkeypatch.setattr(native, "_verify_leaf_entry", verify)
    with pytest.raises(native.AtomicWriteEffectUnknownError) as captured:
        fs.workspace_write_text_atomic("target.txt", "完整 new")
    assert captured.value.phase == phase
    assert isinstance(captured.value.__cause__, OSError)
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "完整 new"
    if phase == "stage_cleanup":
        retained = captured.value.staging_relative_path
        assert retained is not None and (tmp_path / retained).is_dir()
    else:
        assert captured.value.staging_relative_path is None


def test_atomic_readers_only_see_whole_old_or_new_payloads(tmp_path: Path) -> None:
    fs = _fs(tmp_path)
    first, second = "旧" * 20000, "新" * 30000
    fs.workspace_write_text_atomic("target.txt", first)
    stop = threading.Event()
    seen: list[str] = []

    def read() -> None:
        while not stop.is_set():
            seen.append(fs.workspace_read_text("target.txt"))

    thread = threading.Thread(target=read)
    thread.start()
    try:
        for index in range(8):
            fs.workspace_write_text_atomic("target.txt", second if index % 2 == 0 else first)
    finally:
        stop.set()
        thread.join(timeout=3)
    assert seen and set(seen) <= {first, second}
    assert not thread.is_alive()


@pytest.mark.parametrize("kind", ["capability", "EXDEV"])
def test_unsupported_native_route_has_no_unmanaged_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text("target.txt", "old")

    def reject(*_args: object, **_kwargs: object) -> None:
        if kind == "EXDEV":
            raise OSError(errno.EXDEV, "synthetic EXDEV")
        raise NotImplementedError("synthetic missing descriptor capability")

    monkeypatch.setattr(
        native.os if kind == "EXDEV" else native,
        "replace" if kind == "EXDEV" else "_require_atomic_capabilities",
        reject,
    )
    with pytest.raises((OSError, NotImplementedError)):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "old"


def test_adapter_without_managed_capability_fails_before_effect(tmp_path: Path) -> None:
    class LegacyOnly:
        def write_text(self, *_args: object, **_kwargs: object) -> int:
            raise AssertionError("unmanaged fallback bypassed capability check")

    fs = KernelFileSystem(str(tmp_path), LegacyOnly())  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert not list(tmp_path.iterdir())


def test_managed_typeerror_is_not_retried(tmp_path: Path) -> None:
    class RejectManaged(LocalFileSystemAdapter):
        def write_managed_text_atomic(
            self, binding: AtomicWriteBindingV1, content: str, *, encoding: str = "utf-8"
        ) -> int:
            raise TypeError("synthetic adapter failure")

    fs = KernelFileSystem(str(tmp_path), RejectManaged())
    with pytest.raises(TypeError, match="synthetic adapter"):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert not list(tmp_path.iterdir())


def test_inherited_managed_method_cannot_bypass_legacy_adapter_policy(tmp_path: Path) -> None:
    class PolicyAdapter(LocalFileSystemAdapter):
        def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
            raise PermissionError("write policy denied")

    fs = KernelFileSystem(str(tmp_path), PolicyAdapter())
    with pytest.raises(NotImplementedError, match="explicit managed"):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert not list(tmp_path.iterdir())


def test_explicit_managed_adapter_opt_in_retains_native_progress(tmp_path: Path) -> None:
    class PolicyAdapter(LocalFileSystemAdapter):
        def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
            raise PermissionError("legacy route denied")

        def write_managed_text_atomic(
            self, binding: AtomicWriteBindingV1, content: str, *, encoding: str = "utf-8"
        ) -> int:
            return super().write_managed_text_atomic(binding, content, encoding=encoding)

    fs = KernelFileSystem(str(tmp_path), PolicyAdapter())
    receipt = fs.workspace_write_text_atomic("target.txt", "explicitly allowed")
    assert receipt.atomic and (tmp_path / "target.txt").read_text(encoding="utf-8") == "explicitly allowed"


@pytest.mark.parametrize("failure", [False, True])
def test_retained_file_and_directory_descriptors_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    descriptors: set[int] = set()
    retain = native._retain_directory
    write = native._write_all

    def capture(*args: object, **kwargs: object) -> native._DirectoryWitness:
        witness = retain(*args, **kwargs)  # type: ignore[arg-type]
        descriptors.add(witness.fd)
        return witness

    def capture_write(fd: int, payload: bytes, *, name: str) -> None:
        descriptors.add(fd)
        if failure:
            raise OSError(errno.EIO, "synthetic failure for closure")
        write(fd, payload, name=name)

    monkeypatch.setattr(native, "_retain_directory", capture)
    monkeypatch.setattr(native, "_write_all", capture_write)
    if failure:
        with pytest.raises(OSError):
            _fs(tmp_path).workspace_write_text_atomic("target.txt", "new")
    else:
        _fs(tmp_path).workspace_write_text_atomic("target.txt", "new")
    assert descriptors
    for fd in descriptors:
        with pytest.raises(OSError) as captured:
            os.fstat(fd)
        assert captured.value.errno == errno.EBADF


def test_stage_leaf_rebinding_before_commit_never_replaces_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text_atomic("target.txt", "old")
    fs.workspace_write_text("unrelated.txt", "unrelated")
    real_sync = native._sync_directories
    replaced = False

    def rebind(directories: list[native._DirectoryWitness]) -> None:
        nonlocal replaced
        real_sync(directories)
        if not replaced:
            replaced = True
            payload = next((tmp_path / ".polaris/runtime/fs/staging").glob("*/payload"))
            payload.unlink()
            payload.symlink_to(tmp_path / "unrelated.txt")

    monkeypatch.setattr(native, "_sync_directories", rebind)
    with pytest.raises(RuntimeError):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert not (tmp_path / "target.txt").is_symlink()
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "old"
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == "unrelated"


def test_success_syncs_new_parent_entries_and_both_rename_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[tuple[int, int]] = []
    original_sync = os.fsync

    def record(fd: int) -> None:
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
        original_sync(fd)

    monkeypatch.setattr(os, "fsync", record)
    fs = _fs(tmp_path)
    fs.workspace_write_text_atomic("new/child/target.txt", "new")
    required = [
        tmp_path / path
        for path in (
            "new",
            "new/child",
            ".polaris",
            ".polaris/runtime",
            ".polaris/runtime/fs",
            ".polaris/runtime/fs/staging",
        )
    ]
    for path in required:
        info = path.stat()
        assert (info.st_dev, info.st_ino) in synced
    parent = (tmp_path / "new").stat()
    child = (tmp_path / "new/child").stat()
    assert synced.index((child.st_dev, child.st_ino)) < synced.index((parent.st_dev, parent.st_ino))


def test_final_directory_sync_rebinding_cannot_return_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _fs(tmp_path)
    fs.workspace_write_text_atomic("src/target.txt", "old")
    (tmp_path / "outside").mkdir()
    real_sync = native._sync_directories
    calls = 0

    def rebind(directories: list[native._DirectoryWitness]) -> None:
        nonlocal calls
        calls += 1
        real_sync(directories)
        if calls == 2:
            (tmp_path / "src").rename(tmp_path / "src-retained")
            (tmp_path / "src").symlink_to(tmp_path / "outside", target_is_directory=True)

    monkeypatch.setattr(native, "_sync_directories", rebind)
    with pytest.raises(native.AtomicWriteEffectUnknownError):
        fs.workspace_write_text_atomic("src/target.txt", "new")
    assert (tmp_path / "src-retained/target.txt").read_text(encoding="utf-8") == "new"
    assert not list((tmp_path / "outside").iterdir())


def test_explicit_runtime_owner_never_widens_target_namespace(tmp_path: Path) -> None:
    namespace, owner = tmp_path / "namespace", tmp_path / "owner"
    namespace.mkdir()
    owner.mkdir()
    fs = KernelFileSystem(str(namespace), LocalFileSystemAdapter(), runtime_owner_workspace=str(owner))
    receipt = fs.workspace_write_text_atomic("allowed.txt", "allowed")
    assert receipt.absolute_path == str(namespace / "allowed.txt")
    assert (namespace / "allowed.txt").read_text(encoding="utf-8") == "allowed"
    assert (owner / ".polaris/runtime/fs/staging").is_dir()
    assert not (namespace / ".polaris").exists()
    for path in (str(owner / "escape.txt"), "../owner/escape.txt"):
        with pytest.raises(ValueError):
            fs.workspace_write_text_atomic(path, "not authorized")
    assert not (owner / "escape.txt").exists()


def test_managed_stage_consumes_canonical_runtime_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.storage import layout

    monkeypatch.setattr(layout, "canonical_workspace_runtime_root", lambda _owner: str(tmp_path / ".custom/runtime"))
    _fs(tmp_path).workspace_write_text_atomic("target.txt", "new")
    assert (tmp_path / ".custom/runtime/fs/staging").is_dir()
    assert not (tmp_path / ".polaris").exists()


def test_canonical_stage_outside_runtime_owner_is_rejected_before_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.storage import layout

    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setattr(layout, "canonical_workspace_runtime_root", lambda _owner: str(tmp_path / "escaped/runtime"))
    with pytest.raises(ValueError):
        _fs(owner).workspace_write_text_atomic("target.txt", "new")
    assert not list(owner.iterdir())
    assert not (tmp_path / "escaped").exists()


def test_distinct_runtime_owner_rebinding_rejects_before_target_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace, owner, outside = (tmp_path / name for name in ("namespace", "owner", "outside"))
    for path in (namespace, owner, outside):
        path.mkdir()
    fs = KernelFileSystem(str(namespace), LocalFileSystemAdapter(), runtime_owner_workspace=str(owner))
    fs.workspace_write_text("target.txt", "old")
    real_sync = native._sync_directories
    rebound = False

    def rebind(directories: list[native._DirectoryWitness]) -> None:
        nonlocal rebound
        real_sync(directories)
        if not rebound:
            rebound = True
            owner.rename(tmp_path / "owner-retained")
            owner.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(native, "_sync_directories", rebind)
    with pytest.raises(RuntimeError):
        fs.workspace_write_text_atomic("target.txt", "new")
    assert (namespace / "target.txt").read_text(encoding="utf-8") == "old"
    assert not list(outside.iterdir())
    assert not (namespace / ".polaris").exists()


def test_distinct_owner_postrename_unknown_retains_correct_recovery_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace, owner = tmp_path / "namespace", tmp_path / "owner"
    namespace.mkdir()
    owner.mkdir()
    fs = KernelFileSystem(str(namespace), LocalFileSystemAdapter(), runtime_owner_workspace=str(owner))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "synthetic retained staging fault")

    monkeypatch.setattr(native, "_clean_stage", fail)
    with pytest.raises(native.AtomicWriteEffectUnknownError) as captured:
        fs.workspace_write_text_atomic("target.txt", "complete")
    error = captured.value
    assert error.owner_workspace == str(owner) and error.target_workspace == str(namespace)
    assert error.staging_relative_path is not None and (owner / error.staging_relative_path).is_dir()
    assert (namespace / "target.txt").read_text(encoding="utf-8") == "complete"
    assert not (namespace / ".polaris").exists()
