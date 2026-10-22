"""Read-only metadata observations using existing retained no-follow witnesses.

These bounded pre/post observations are not atomic tree snapshots. No leaf
content is opened, no path-based fallback is permitted, and no directories are
provisioned. The existing guarded regular-file snapshot owns byte reads.
"""

from __future__ import annotations

import errno
import os
import stat
from contextlib import ExitStack

from .guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    _DirectoryWitness,
    _require_descriptor_capabilities,
    _retain_directory,
    _validated_relative_parts,
    _validated_root,
    _verify_directory_witness,
)
from .types import DirectoryEntryV1, DirectoryListingV1, FileMetadataV1, SymlinkObservationV1


class GuardedMetadataError(RuntimeError):
    """Typed missing, drift, denial or unavailable observation."""

    def __init__(self, message: str, *, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = details or {}


def _fail(suffix: str, message: str, **details: object) -> GuardedMetadataError:
    return GuardedMetadataError(message, code="guarded_metadata_" + suffix, details=details)


def _native_failure(exc: OSError, *, observed: bool, name: str) -> GuardedMetadataError:
    suffix = "io_error"
    if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
        suffix = "identity_drift" if observed else "missing"
    return _fail(suffix, "native observation failed", name=name, errno=exc.errno)


def _shared_failure(exc: GuardedRegularFileSnapshotError, *, observed: bool) -> GuardedMetadataError:
    suffix = exc.code.removeprefix("guarded_snapshot_")
    details = dict(exc.details)
    if isinstance(exc.__cause__, OSError):
        details.setdefault("errno", exc.__cause__.errno)
        if exc.__cause__.errno not in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            suffix = "io_error"
    if observed and suffix in {"missing", "not_directory", "symlink_rejected"}:
        suffix = "identity_drift"
    if suffix not in {
        "missing",
        "identity_drift",
        "not_directory",
        "symlink_rejected",
        "capability_unavailable",
        "io_error",
    }:
        suffix = "io_error"
    return GuardedMetadataError(
        "retained directory observation failed", code="guarded_metadata_" + suffix, details=details
    )


def _validate(root: str, relative_path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    _, root_parts = _validated_root(root)
    parts = () if relative_path == "." else _validated_relative_parts(relative_path)
    try:
        _require_descriptor_capabilities()
    except GuardedRegularFileSnapshotError as exc:
        raise _shared_failure(exc, observed=False) from exc
    return root_parts, parts


def _verify(witnesses: list[_DirectoryWitness]) -> None:
    for witness in witnesses:
        try:
            _verify_directory_witness(witness)
        except GuardedRegularFileSnapshotError as exc:
            raise _shared_failure(exc, observed=True) from exc


def _retained_parent(
    stack: ExitStack, root_parts: tuple[str, ...], parent_parts: tuple[str, ...], *, observed: bool = False
) -> tuple[list[_DirectoryWitness], _DirectoryWitness]:
    """Compose existing retention helper; preserve every physical ancestor."""
    witnesses: list[_DirectoryWitness] = []
    current: _DirectoryWitness | None = None
    display: list[str] = []
    for part in ("/", *root_parts, *parent_parts):
        _verify(witnesses)
        if part != "/":
            display.append(part)
        try:
            current = _retain_directory(stack, parent=current, entry_name=part, display_name="/" + "/".join(display))
        except GuardedRegularFileSnapshotError as exc:
            # Revalidate already observed ancestors before classifying a new
            # missing child, so a rebound parent never masquerades as absence.
            _verify(witnesses)
            raise _shared_failure(exc, observed=observed) from exc
        witnesses.append(current)
    _verify(witnesses)
    return witnesses, witnesses[len(root_parts)]


def _stat_entry(parent_fd: int, name: str, *, observed: bool = False) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise _native_failure(exc, observed=observed, name=name) from exc


def _metadata(info: os.stat_result, relative: str, root: _DirectoryWitness) -> FileMetadataV1:
    return FileMetadataV1(
        relative_path=relative,
        device=info.st_dev,
        inode=info.st_ino,
        mode=info.st_mode,
        nlink=info.st_nlink,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        root_device=root.device,
        root_inode=root.inode,
    )


def _fstat(witness: _DirectoryWitness) -> os.stat_result:
    try:
        return os.fstat(witness.fd)
    except OSError as exc:
        raise _native_failure(exc, observed=True, name=witness.display_name) from exc


def _same(before: FileMetadataV1, after: FileMetadataV1) -> None:
    if before != after:
        raise _fail("identity_drift", "observed metadata changed", relative_path=before.relative_path)


def read_guarded_metadata(root: str, relative_path: str) -> FileMetadataV1:
    """Observe seven lstat fields without opening any leaf content."""
    root_parts, parts = _validate(root, relative_path)
    with ExitStack() as stack:
        witnesses, root_witness = _retained_parent(stack, root_parts, parts[:-1])
        parent = witnesses[-1]
        before = _metadata(_stat_entry(parent.fd, parts[-1]) if parts else _fstat(parent), relative_path, root_witness)
        _same(
            before,
            _metadata(
                _stat_entry(parent.fd, parts[-1], observed=True) if parts else _fstat(parent),
                relative_path,
                root_witness,
            ),
        )
        _verify(witnesses)
        return before


def list_guarded_directory(root: str, relative_path: str, *, max_entries: int) -> DirectoryListingV1:
    """Scan at most N+1 entries, sort only emitted subset, verify before return."""
    if type(max_entries) is not int or max_entries <= 0:
        raise ValueError("max_entries must be a positive int")
    root_parts, parts = _validate(root, relative_path)
    if os.scandir not in os.supports_fd:
        raise _fail("capability_unavailable", "fd-based scandir is unavailable")
    with ExitStack() as stack:
        witnesses, root_witness = _retained_parent(stack, root_parts, parts[:-1])
        parent = witnesses[-1]
        before = _metadata(_stat_entry(parent.fd, parts[-1]) if parts else _fstat(parent), relative_path, root_witness)
        if stat.S_ISLNK(before.mode):
            raise _fail("symlink_rejected", "listing target is a symlink", relative_path=relative_path)
        if not stat.S_ISDIR(before.mode):
            raise _fail("not_directory", "listing target is not a directory", relative_path=relative_path)
        if parts:
            try:
                directory = _retain_directory(
                    stack, parent=parent, entry_name=parts[-1], display_name=root.rstrip("/") + "/" + relative_path
                )
            except GuardedRegularFileSnapshotError as exc:
                raise _shared_failure(exc, observed=True) from exc
            witnesses.append(directory)
        else:
            directory = parent
        _same(before, _metadata(_fstat(directory), relative_path, root_witness))
        entries: list[DirectoryEntryV1] = []
        complete = False
        try:
            with os.scandir(directory.fd) as iterator:
                for _ in range(max_entries + 1):
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        complete = True
                        break
                    if len(entries) == max_entries:
                        break
                    child_relative = entry.name if not parts else relative_path + "/" + entry.name
                    try:
                        _validated_relative_parts(child_relative)
                    except (ValueError, UnicodeError) as exc:
                        raise _fail(
                            "invalid_name",
                            "directory name is not a canonical UTF-8 path",
                            name=ascii(entry.name),
                            name_bytes_hex=os.fsencode(entry.name).hex(),
                        ) from exc
                    child = _metadata(
                        _stat_entry(directory.fd, entry.name, observed=True), child_relative, root_witness
                    )
                    entries.append(DirectoryEntryV1(entry.name, child))
        except NotImplementedError as exc:
            raise _fail("capability_unavailable", "fd-based scandir is unavailable") from exc
        except OSError as exc:
            raise _native_failure(exc, observed=True, name=relative_path) from exc
        for emitted in entries:
            _same(
                emitted.metadata,
                _metadata(
                    _stat_entry(directory.fd, emitted.name, observed=True), emitted.metadata.relative_path, root_witness
                ),
            )
        _verify(witnesses)
        _same(before, _metadata(_fstat(directory), relative_path, root_witness))
        return DirectoryListingV1(before, tuple(sorted(entries, key=lambda entry: entry.name)), complete)


def read_guarded_symlink(
    root: str, relative_path: str, *, expected: FileMetadataV1 | None = None
) -> SymlinkObservationV1:
    """Observe link text, never follow it; optionally bind prior metadata."""
    if expected is not None and not isinstance(expected, FileMetadataV1):
        raise ValueError("expected must be FileMetadataV1 or None")
    root_parts, parts = _validate(root, relative_path)
    if not parts:
        raise _fail("not_symlink", "workspace root is not a symlink")
    if os.readlink not in os.supports_dir_fd:
        raise _fail("capability_unavailable", "dirfd-based readlink is unavailable")
    with ExitStack() as stack:
        witnesses, root_witness = _retained_parent(stack, root_parts, parts[:-1], observed=expected is not None)
        parent = witnesses[-1]
        before = _metadata(
            _stat_entry(parent.fd, parts[-1], observed=expected is not None), relative_path, root_witness
        )
        if expected is not None:
            _same(expected, before)
        if not stat.S_ISLNK(before.mode):
            raise _fail("not_symlink", "target is not a symlink", relative_path=relative_path)
        try:
            target = os.readlink(parts[-1], dir_fd=parent.fd)
        except NotImplementedError as exc:
            raise _fail("capability_unavailable", "dirfd-based readlink is unavailable") from exc
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise _fail(
                    "identity_drift", "observed symlink changed kind", relative_path=relative_path, errno=exc.errno
                ) from exc
            raise _native_failure(exc, observed=True, name=relative_path) from exc
        try:
            target.encode("utf-8")
        except UnicodeError as exc:
            raise _fail("invalid_name", "link text is not representable as UTF-8", relative_path=relative_path) from exc
        _same(before, _metadata(_stat_entry(parent.fd, parts[-1], observed=True), relative_path, root_witness))
        _verify(witnesses)
        return SymlinkObservationV1(before, target)
