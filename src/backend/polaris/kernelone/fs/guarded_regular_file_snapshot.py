"""Descriptor-safe, immutable snapshots of bounded regular files.

This module is deliberately platform-neutral and contains no Polaris business
semantics.  It walks the absolute root and requested relative path through
retained directory descriptors, rejects link-based ambiguity, and revalidates
every physical identity after the bounded read completes.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import unicodedata
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from os import PathLike
from typing import Any, Final, NoReturn

_READ_CHUNK_BYTES: Final[int] = 64 * 1024
_MAX_RELATIVE_PATH_UTF8_BYTES: Final[int] = 1024
_RENAME_NOREPLACE: Final[int] = 1
_RENAME_EXCHANGE: Final[int] = 2


class GuardedRegularFileSnapshotError(RuntimeError):
    """Typed failure raised by guarded regular-file snapshot reads."""

    def __init__(self, message: str, *, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class GuardedRegularFileSnapshotV1:
    """Immutable bytes and physical evidence for one guarded file read."""

    relative_path: str
    content: bytes
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    root_device: int
    root_inode: int


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    link_count: int


@dataclass(frozen=True, slots=True)
class _EntryIdentity:
    mode: int
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    link_count: int


@dataclass(frozen=True, slots=True)
class _DirectoryWitness:
    fd: int
    device: int
    inode: int
    parent_fd: int | None
    entry_name: str | None
    display_name: str


def _fail(code: str, message: str, **details: object) -> GuardedRegularFileSnapshotError:
    return GuardedRegularFileSnapshotError(message, code=code, details=details)


def _platform_name() -> str:
    """Return the interpreter platform through a testable module seam."""

    return os.name


def _has_required_descriptor_capabilities() -> bool:
    required_flags = ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_DIRECTORY")
    return (
        all(hasattr(os, name) for name in required_flags)
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )


def _require_descriptor_capabilities() -> None:
    if _platform_name() == "nt" or not _has_required_descriptor_capabilities():
        raise _fail(
            "guarded_snapshot_capability_unavailable",
            "descriptor-safe guarded regular-file snapshots are unavailable",
        )


def _require_replace_capabilities() -> None:
    _require_descriptor_capabilities()
    required_flags = ("O_EXCL", "O_CREAT", "O_RDWR")
    if (
        not all(hasattr(os, name) for name in required_flags)
        or os.unlink not in os.supports_dir_fd
        or _load_renameat2() is None
    ):
        raise _fail(
            "guarded_replace_capability_unavailable",
            "atomic-exchange guarded compare-and-replace is unavailable",
        )


def _load_renameat2() -> Any | None:
    """Load Linux/glibc renameat2 without inventing a non-atomic fallback."""

    if _platform_name() != "posix":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        return None
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    return renameat2


def _validated_max_bytes(max_bytes: int) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a finite positive integer")
    return max_bytes


def _validated_relative_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str):
        raise ValueError("relative_path must be a non-empty NFC string")
    if not relative_path or "\x00" in relative_path:
        raise ValueError("relative_path must be a non-empty NFC string")
    if unicodedata.normalize("NFC", relative_path) != relative_path:
        raise ValueError("relative_path must be NFC-normalized")
    if len(bytes(relative_path, encoding="utf-8")) > _MAX_RELATIVE_PATH_UTF8_BYTES:
        raise ValueError("relative_path must not exceed 1024 UTF-8 bytes")
    if relative_path.startswith("/") or "\\" in relative_path or ":" in relative_path.split("/", 1)[0]:
        raise ValueError("relative_path must be a canonical relative slash path")
    parts = tuple(relative_path.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("relative_path must not contain empty, '.' or '..' components")
    return parts


def _validated_root(root: str | PathLike[str]) -> tuple[str, tuple[str, ...]]:
    try:
        root_value = os.fspath(root)
    except TypeError as exc:
        raise ValueError("root must be an absolute canonical text path") from exc
    if not isinstance(root_value, str) or not root_value or "\x00" in root_value:
        raise ValueError("root must be an absolute canonical text path")
    if unicodedata.normalize("NFC", root_value) != root_value:
        raise ValueError("root must be NFC-normalized")
    if root_value.startswith("//"):
        raise ValueError("root must be an absolute canonical text path")
    if not os.path.isabs(root_value):
        raise ValueError("root must be an absolute canonical text path")
    if os.path.normpath(root_value) != root_value:
        raise ValueError("root must be an absolute canonical text path")
    raw_parts = root_value.split("/")
    if any(part in {".", ".."} for part in raw_parts):
        raise ValueError("root must not contain '.' or '..' components")
    return root_value, tuple(part for part in raw_parts if part)


def _open_flags(*, directory: bool) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if directory:
        flags |= os.O_DIRECTORY
    return flags


def _entry_stat(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise _fail("guarded_snapshot_missing", "guarded snapshot path entry is missing", name=name) from exc
    except OSError as exc:
        raise _fail(
            "guarded_snapshot_identity_drift",
            "guarded snapshot path entry could not be verified",
            name=name,
            errno=exc.errno,
        ) from exc


def _raise_open_failure(
    exc: OSError,
    *,
    parent_fd: int | None,
    name: str,
    directory: bool,
) -> NoReturn:
    if exc.errno == errno.ENOENT:
        raise _fail("guarded_snapshot_missing", "guarded snapshot path is missing", name=name) from exc
    if exc.errno == errno.ELOOP:
        raise _fail("guarded_snapshot_symlink_rejected", "symbolic links are not allowed", name=name) from exc
    if parent_fd is not None:
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            entry = None
        if entry is not None and stat.S_ISLNK(entry.st_mode):
            raise _fail("guarded_snapshot_symlink_rejected", "symbolic links are not allowed", name=name) from exc
        if entry is not None and directory and not stat.S_ISDIR(entry.st_mode):
            raise _fail("guarded_snapshot_not_directory", "path ancestor is not a directory", name=name) from exc
    raise _fail(
        "guarded_snapshot_open_failed",
        "guarded snapshot path could not be opened safely",
        name=name,
        errno=exc.errno,
    ) from exc


def _open_descriptor(
    stack: ExitStack,
    name: str,
    *,
    parent_fd: int | None,
    directory: bool,
) -> int:
    try:
        if parent_fd is None:
            fd = os.open(name, _open_flags(directory=directory))
        else:
            fd = os.open(name, _open_flags(directory=directory), dir_fd=parent_fd)
    except OSError as exc:
        _raise_open_failure(exc, parent_fd=parent_fd, name=name, directory=directory)
    stack.callback(os.close, fd)
    return fd


def _directory_identity(fd: int, *, name: str) -> tuple[int, int]:
    try:
        info = os.fstat(fd)
    except OSError as exc:
        raise _fail(
            "guarded_snapshot_identity_drift",
            "directory descriptor could not be verified",
            name=name,
            errno=exc.errno,
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise _fail("guarded_snapshot_not_directory", "guarded snapshot ancestor is not a directory", name=name)
    return info.st_dev, info.st_ino


def _verify_directory_witness(witness: _DirectoryWitness) -> None:
    identity = _directory_identity(witness.fd, name=witness.display_name)
    if identity != (witness.device, witness.inode):
        raise _fail(
            "guarded_snapshot_identity_drift",
            "retained directory descriptor identity changed",
            name=witness.display_name,
        )
    if witness.parent_fd is None or witness.entry_name is None:
        return
    entry = _entry_stat(witness.parent_fd, witness.entry_name)
    if stat.S_ISLNK(entry.st_mode):
        raise _fail(
            "guarded_snapshot_symlink_rejected",
            "symbolic links are not allowed",
            name=witness.display_name,
        )
    if not stat.S_ISDIR(entry.st_mode) or (entry.st_dev, entry.st_ino) != identity:
        raise _fail(
            "guarded_snapshot_identity_drift",
            "guarded snapshot directory entry identity changed",
            name=witness.display_name,
        )


def _retain_directory(
    stack: ExitStack,
    *,
    parent: _DirectoryWitness | None,
    entry_name: str,
    display_name: str,
) -> _DirectoryWitness:
    parent_fd = None if parent is None else parent.fd
    fd = _open_descriptor(stack, entry_name, parent_fd=parent_fd, directory=True)
    device, inode = _directory_identity(fd, name=display_name)
    witness = _DirectoryWitness(
        fd=fd,
        device=device,
        inode=inode,
        parent_fd=parent_fd,
        entry_name=None if parent is None else entry_name,
        display_name=display_name,
    )
    _verify_directory_witness(witness)
    return witness


def _retain_or_mkdir_directory(
    stack: ExitStack,
    *,
    parent: _DirectoryWitness,
    entry_name: str,
    display_name: str,
) -> _DirectoryWitness:
    """Open an existing directory under parent, or create it when missing.

    Used only for intermediate ancestors of a create target under an already
    validated workspace root. Symlinks and non-directory entries still fail
    closed via ``_retain_directory`` / ``_open_descriptor``.
    """

    try:
        return _retain_directory(
            stack,
            parent=parent,
            entry_name=entry_name,
            display_name=display_name,
        )
    except GuardedRegularFileSnapshotError as exc:
        if exc.code != "guarded_snapshot_missing":
            raise

    parent_fd = parent.fd
    try:
        os.mkdir(entry_name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        # Concurrent create: open and re-verify identity below.
        pass
    except OSError as exc:
        raise _fail(
            "guarded_create_mkdir_failed",
            "guarded create could not create intermediate directory",
            name=display_name,
            errno=exc.errno,
        ) from exc

    return _retain_directory(
        stack,
        parent=parent,
        entry_name=entry_name,
        display_name=display_name,
    )


def _file_fingerprint(
    fd: int,
    *,
    name: str,
    expected: _FileFingerprint | None = None,
) -> _FileFingerprint:
    try:
        info = os.fstat(fd)
    except OSError as exc:
        raise _fail(
            "guarded_snapshot_identity_drift",
            "regular-file descriptor could not be verified",
            name=name,
            errno=exc.errno,
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise _fail("guarded_snapshot_not_regular", "guarded snapshot leaf is not a regular file", name=name)
    observed = _FileFingerprint(
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        link_count=info.st_nlink,
    )
    if expected is not None and observed != expected:
        raise _fail(
            "guarded_snapshot_identity_drift",
            "guarded snapshot leaf changed during the read",
            name=name,
        )
    if info.st_nlink != 1:
        raise _fail(
            "guarded_snapshot_hard_link_rejected",
            "guarded snapshot leaf has multiple hard links",
            name=name,
            link_count=info.st_nlink,
        )
    return observed


def _verify_leaf_entry(parent_fd: int, name: str, expected: _FileFingerprint) -> None:
    entry = _entry_stat(parent_fd, name)
    if stat.S_ISLNK(entry.st_mode):
        raise _fail("guarded_snapshot_symlink_rejected", "symbolic links are not allowed", name=name)
    if not stat.S_ISREG(entry.st_mode):
        raise _fail("guarded_snapshot_not_regular", "guarded snapshot leaf is not a regular file", name=name)
    if entry.st_nlink != 1:
        raise _fail(
            "guarded_snapshot_hard_link_rejected",
            "guarded snapshot leaf has multiple hard links",
            name=name,
            link_count=entry.st_nlink,
        )
    observed = _FileFingerprint(
        device=entry.st_dev,
        inode=entry.st_ino,
        size=entry.st_size,
        mtime_ns=entry.st_mtime_ns,
        ctime_ns=entry.st_ctime_ns,
        link_count=entry.st_nlink,
    )
    if observed != expected:
        raise _fail(
            "guarded_snapshot_identity_drift",
            "guarded snapshot leaf entry changed during the read",
            name=name,
        )


def _exchange_fingerprint_matches(observed: _FileFingerprint, expected: _FileFingerprint) -> bool:
    """Compare fields stable across renameat2(RENAME_EXCHANGE).

    Linux updates ctime as a consequence of the exchange itself, so ctime is
    intentionally excluded.  Device/inode identity, bytes/size, mtime, and
    link count remain authoritative.
    """

    return (
        observed.device,
        observed.inode,
        observed.size,
        observed.mtime_ns,
        observed.link_count,
    ) == (
        expected.device,
        expected.inode,
        expected.size,
        expected.mtime_ns,
        expected.link_count,
    )


def _verify_exchange_leaf_entry(parent_fd: int, name: str, expected: _FileFingerprint) -> None:
    entry = _entry_stat(parent_fd, name)
    if not stat.S_ISREG(entry.st_mode):
        raise _fail("guarded_snapshot_not_regular", "guarded exchange leaf is not regular", name=name)
    observed = _FileFingerprint(
        device=entry.st_dev,
        inode=entry.st_ino,
        size=entry.st_size,
        mtime_ns=entry.st_mtime_ns,
        ctime_ns=entry.st_ctime_ns,
        link_count=entry.st_nlink,
    )
    if not _exchange_fingerprint_matches(observed, expected):
        raise _fail(
            "guarded_snapshot_identity_drift",
            "guarded exchange leaf identity changed",
            name=name,
        )


def _read_bounded(fd: int, *, max_bytes: int, name: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        amount = min(_READ_CHUNK_BYTES, max_bytes - total + 1)
        try:
            chunk = os.read(fd, amount)
        except OSError as exc:
            raise _fail(
                "guarded_snapshot_read_failed",
                "guarded snapshot leaf could not be read",
                name=name,
                errno=exc.errno,
            ) from exc
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise _fail(
                "guarded_snapshot_max_bytes_exceeded",
                "guarded snapshot leaf exceeds max_bytes",
                name=name,
                max_bytes=max_bytes,
            )
        chunks.append(chunk)


def _before_guarded_replace_revalidation(parent_fd: int, leaf_name: str) -> None:
    """Injectable no-op seam immediately before the final old-leaf CAS check."""

    del parent_fd, leaf_name


def _before_guarded_create_commit(parent_fd: int, leaf_name: str) -> None:
    """Injectable no-op seam immediately before the atomic absent-leaf create."""

    del parent_fd, leaf_name


def _before_guarded_remove_revalidation(parent_fd: int, leaf_name: str) -> None:
    """Injectable no-op seam before compare-and-remove captures commit state."""

    del parent_fd, leaf_name


def _after_guarded_replace(parent_fd: int, leaf_name: str) -> None:
    """Injectable no-op seam immediately before the replacement post-read."""

    del parent_fd, leaf_name


def _before_guarded_rollback_exchange(parent_fd: int, temp_name: str, leaf_name: str) -> None:
    """Injectable seam after rollback checks and before atomic exchange."""

    del parent_fd, temp_name, leaf_name


def _exchange_guarded_leaves(left_name: str, right_name: str, parent_fd: int) -> None:
    """Atomically exchange two same-parent entries with renameat2."""

    renameat2 = _load_renameat2()
    if renameat2 is None:
        raise _fail(
            "guarded_replace_capability_unavailable",
            "renameat2(RENAME_EXCHANGE) became unavailable",
        )
    ctypes.set_errno(0)
    result = renameat2(
        parent_fd,
        os.fsencode(left_name),
        parent_fd,
        os.fsencode(right_name),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        if error_number in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise _fail(
                "guarded_replace_capability_unavailable",
                "renameat2(RENAME_EXCHANGE) is unsupported by the running kernel or filesystem",
                errno=error_number,
            )
        raise OSError(error_number, os.strerror(error_number))


def _rename_guarded_leaf_noreplace(source_name: str, target_name: str, parent_fd: int) -> None:
    """Atomically move one same-parent entry only when the target is absent."""

    renameat2 = _load_renameat2()
    if renameat2 is None:
        raise _fail(
            "guarded_replace_capability_unavailable",
            "renameat2(RENAME_NOREPLACE) became unavailable",
        )
    ctypes.set_errno(0)
    result = renameat2(
        parent_fd,
        os.fsencode(source_name),
        parent_fd,
        os.fsencode(target_name),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        if error_number in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise _fail(
                "guarded_replace_capability_unavailable",
                "renameat2(RENAME_NOREPLACE) is unsupported by the running kernel or filesystem",
                errno=error_number,
            )
        raise OSError(error_number, os.strerror(error_number))


def _replace_guarded_leaf(temp_name: str, leaf_name: str, parent_fd: int) -> None:
    """Commit seam: exchange replacement and target without destroying either."""

    _exchange_guarded_leaves(temp_name, leaf_name, parent_fd)


def _open_guarded_replace_temp(parent_fd: int, leaf_name: str) -> tuple[str, int]:
    """Create one unpredictable same-parent temporary regular file O_EXCL."""

    safe_leaf_prefix = leaf_name[:64]
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _attempt in range(8):
        temp_name = f".{safe_leaf_prefix}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise _fail(
                "guarded_replace_temp_create_failed",
                "guarded replacement temporary file could not be created safely",
                errno=exc.errno,
            ) from exc
        return temp_name, fd
    raise _fail(
        "guarded_replace_temp_collision",
        "guarded replacement could not allocate a unique temporary entry",
    )


def _write_all(fd: int, payload: bytes, *, name: str) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short guarded replacement write")
            view = view[written:]
    except OSError as exc:
        raise _fail(
            "guarded_replace_write_failed",
            "guarded replacement bytes could not be written completely",
            name=name,
            errno=exc.errno,
        ) from exc


def _expected_fingerprint(snapshot: GuardedRegularFileSnapshotV1) -> _FileFingerprint:
    return _FileFingerprint(
        device=snapshot.device,
        inode=snapshot.inode,
        size=snapshot.size,
        mtime_ns=snapshot.mtime_ns,
        ctime_ns=snapshot.ctime_ns,
        link_count=1,
    )


def _verify_expected_leaf(
    fd: int,
    *,
    parent_fd: int,
    leaf_name: str,
    expected: GuardedRegularFileSnapshotV1,
) -> None:
    try:
        observed = _file_fingerprint(fd, name=expected.relative_path)
    except GuardedRegularFileSnapshotError as exc:
        raise _fail(
            "guarded_replace_expected_mismatch",
            "guarded replacement old leaf changed after its expected snapshot",
            name=expected.relative_path,
            source_error_code=exc.code,
        ) from exc
    expected_fingerprint = _expected_fingerprint(expected)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError as exc:
        raise _fail(
            "guarded_replace_expected_mismatch",
            "guarded replacement old leaf could not be rewound",
            errno=exc.errno,
        ) from exc
    try:
        observed_bytes = _read_bounded(fd, max_bytes=max(1, expected.size), name=expected.relative_path)
    except GuardedRegularFileSnapshotError as exc:
        raise _fail(
            "guarded_replace_expected_mismatch",
            "guarded replacement old leaf bytes changed after its expected snapshot",
            name=expected.relative_path,
            source_error_code=exc.code,
        ) from exc
    if observed != expected_fingerprint or observed_bytes != expected.content or len(observed_bytes) != expected.size:
        raise _fail(
            "guarded_replace_expected_mismatch",
            "guarded replacement old leaf changed after its expected snapshot",
            name=expected.relative_path,
        )
    try:
        _verify_leaf_entry(parent_fd, leaf_name, expected_fingerprint)
    except GuardedRegularFileSnapshotError as exc:
        raise _fail(
            "guarded_replace_expected_mismatch",
            "guarded replacement old leaf entry changed after its expected snapshot",
            name=expected.relative_path,
            source_error_code=exc.code,
        ) from exc


def _entry_identity(parent_fd: int, name: str) -> _EntryIdentity:
    info = _entry_stat(parent_fd, name)
    return _EntryIdentity(
        mode=info.st_mode,
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        link_count=info.st_nlink,
    )


def _verify_entry_identity(parent_fd: int, name: str, expected: _EntryIdentity) -> None:
    observed = _entry_identity(parent_fd, name)
    if (
        observed.mode,
        observed.device,
        observed.inode,
        observed.size,
        observed.mtime_ns,
        observed.link_count,
    ) != (
        expected.mode,
        expected.device,
        expected.inode,
        expected.size,
        expected.mtime_ns,
        expected.link_count,
    ):
        raise _fail(
            "guarded_replace_rollback_identity_drift",
            "guarded exchange entry identity changed during rollback",
            name=name,
        )


def _capture_guarded_regular_entry(
    stack: ExitStack,
    *,
    parent_fd: int,
    name: str,
    max_bytes: int,
) -> tuple[_FileFingerprint, bytes]:
    fd = _open_descriptor(stack, name, parent_fd=parent_fd, directory=False)
    before = _file_fingerprint(fd, name=name)
    _verify_leaf_entry(parent_fd, name, before)
    if before.size > max_bytes:
        raise _fail(
            "guarded_replace_displaced_max_bytes_exceeded",
            "guarded exchange displaced leaf exceeds max_bytes",
            name=name,
            size=before.size,
            max_bytes=max_bytes,
        )
    content = _read_bounded(fd, max_bytes=max_bytes, name=name)
    after = _file_fingerprint(fd, name=name, expected=before)
    _verify_leaf_entry(parent_fd, name, after)
    if len(content) != after.size:
        raise _fail(
            "guarded_replace_displaced_identity_drift",
            "guarded exchange displaced leaf changed during evidence capture",
            name=name,
        )
    return after, content


def _rollback_guarded_exchange(
    stack: ExitStack,
    *,
    parent_fd: int,
    temp_name: str,
    leaf_name: str,
    replacement_fingerprint: _FileFingerprint,
    displaced_identity: _EntryIdentity,
    displaced_content: bytes | None,
    max_bytes: int,
) -> None:
    """Atomically restore a displaced concurrent leaf or fail with both entries kept."""

    try:
        _verify_exchange_leaf_entry(parent_fd, leaf_name, replacement_fingerprint)
        _verify_entry_identity(parent_fd, temp_name, displaced_identity)
        _before_guarded_rollback_exchange(parent_fd, temp_name, leaf_name)
        _exchange_guarded_leaves(temp_name, leaf_name, parent_fd)
        _verify_entry_identity(parent_fd, leaf_name, displaced_identity)
        _verify_exchange_leaf_entry(parent_fd, temp_name, replacement_fingerprint)
        if displaced_content is not None:
            _restored_fingerprint, restored_content = _capture_guarded_regular_entry(
                stack,
                parent_fd=parent_fd,
                name=leaf_name,
                max_bytes=max_bytes,
            )
            if restored_content != displaced_content:
                raise _fail(
                    "guarded_replace_rollback_identity_drift",
                    "guarded rollback restored different concurrent bytes",
                    name=leaf_name,
                )
        os.fsync(parent_fd)
    except (GuardedRegularFileSnapshotError, OSError) as exc:
        raise _fail(
            "guarded_replace_rollback_failed",
            "guarded exchange rollback could not prove exact restoration; entries were retained",
            source_error_code=getattr(exc, "code", type(exc).__name__),
            errno=getattr(exc, "errno", None),
            recovery_temp_name=temp_name,
        ) from exc


def _cleanup_guarded_temp(parent_fd: int, temp_name: str) -> None:
    try:
        os.unlink(temp_name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _fail(
            "guarded_replace_temp_cleanup_failed",
            "guarded replacement temporary entry could not be removed",
            name=temp_name,
            errno=exc.errno,
        ) from exc


def read_guarded_regular_file_snapshot(
    root: str | PathLike[str],
    relative_path: str,
    max_bytes: int,
) -> GuardedRegularFileSnapshotV1:
    """Read one immutable, descriptor-verified regular-file snapshot.

    Args:
        root: Absolute canonical root traversed without following links.
        relative_path: NFC-normalized slash-separated path below ``root``.
        max_bytes: Strict finite positive integer read bound.

    Returns:
        Immutable bytes plus root and leaf physical identity evidence.

    Raises:
        ValueError: An input contract is invalid.
        GuardedRegularFileSnapshotError: The guarded read cannot be proven safe.
    """

    bound = _validated_max_bytes(max_bytes)
    path_parts = _validated_relative_parts(relative_path)
    root_path, root_parts = _validated_root(root)
    _require_descriptor_capabilities()

    with ExitStack() as stack:
        directories: list[_DirectoryWitness] = []
        current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
        directories.append(current)
        display_parts: list[str] = []
        for component in root_parts:
            display_parts.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name="/" + "/".join(display_parts),
            )
            directories.append(current)
        root_witness = current

        relative_display: list[str] = []
        for component in path_parts[:-1]:
            relative_display.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name=f"{root_path.rstrip('/')}/{'/'.join(relative_display)}",
            )
            directories.append(current)

        leaf_name = path_parts[-1]
        leaf_fd = _open_descriptor(stack, leaf_name, parent_fd=current.fd, directory=False)
        before = _file_fingerprint(leaf_fd, name=relative_path)
        _verify_leaf_entry(current.fd, leaf_name, before)
        if before.size > bound:
            raise _fail(
                "guarded_snapshot_max_bytes_exceeded",
                "guarded snapshot leaf exceeds max_bytes",
                name=relative_path,
                size=before.size,
                max_bytes=bound,
            )

        content = _read_bounded(leaf_fd, max_bytes=bound, name=relative_path)
        after = _file_fingerprint(leaf_fd, name=relative_path, expected=before)
        if after != before or len(content) != before.size:
            raise _fail(
                "guarded_snapshot_identity_drift",
                "guarded snapshot leaf changed during the read",
                name=relative_path,
            )
        _verify_leaf_entry(current.fd, leaf_name, after)
        for witness in directories:
            _verify_directory_witness(witness)

        return GuardedRegularFileSnapshotV1(
            relative_path=relative_path,
            content=content,
            size=after.size,
            device=after.device,
            inode=after.inode,
            mtime_ns=after.mtime_ns,
            ctime_ns=after.ctime_ns,
            root_device=root_witness.device,
            root_inode=root_witness.inode,
        )


def guarded_compare_and_replace_regular_file(
    root: str | PathLike[str],
    expected: GuardedRegularFileSnapshotV1,
    replacement: bytes,
    *,
    max_bytes: int,
) -> GuardedRegularFileSnapshotV1:
    """CAS-replace one exact guarded snapshot through retained descriptors.

    The old leaf, every ancestor, and the root remain descriptor-bound.  The
    replacement is written once to an O_EXCL/O_NOFOLLOW temporary file and
    file-fsynced, then renameat2(RENAME_EXCHANGE) atomically preserves the
    commit-time target under the temporary name.  Only an exact displaced-old
    validation commits; mismatch performs a verified exchange rollback.
    """

    bound = _validated_max_bytes(max_bytes)
    if type(expected) is not GuardedRegularFileSnapshotV1:
        raise ValueError("expected must be an exact GuardedRegularFileSnapshotV1")
    if type(replacement) is not bytes:
        raise ValueError("replacement must be exact immutable bytes")
    if len(replacement) > bound:
        raise _fail(
            "guarded_replace_max_bytes_exceeded",
            "guarded replacement exceeds max_bytes",
            max_bytes=bound,
            size=len(replacement),
        )
    if expected.size != len(expected.content) or expected.size > bound:
        raise _fail(
            "guarded_replace_expected_invalid",
            "expected snapshot size/content is internally inconsistent or unbounded",
        )

    path_parts = _validated_relative_parts(expected.relative_path)
    root_path, root_parts = _validated_root(root)
    _require_replace_capabilities()

    with ExitStack() as stack:
        directories: list[_DirectoryWitness] = []
        current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
        directories.append(current)
        display_parts: list[str] = []
        for component in root_parts:
            display_parts.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name="/" + "/".join(display_parts),
            )
            directories.append(current)
        root_witness = current
        if (root_witness.device, root_witness.inode) != (expected.root_device, expected.root_inode):
            raise _fail(
                "guarded_replace_expected_mismatch",
                "guarded replacement root differs from the expected snapshot root",
            )

        relative_display: list[str] = []
        for component in path_parts[:-1]:
            relative_display.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name=f"{root_path.rstrip('/')}/{'/'.join(relative_display)}",
            )
            directories.append(current)

        leaf_name = path_parts[-1]
        old_fd = _open_descriptor(stack, leaf_name, parent_fd=current.fd, directory=False)
        _verify_expected_leaf(
            old_fd,
            parent_fd=current.fd,
            leaf_name=leaf_name,
            expected=expected,
        )
        for witness in directories:
            _verify_directory_witness(witness)

        temp_name, temp_fd = _open_guarded_replace_temp(current.fd, leaf_name)
        stack.callback(os.close, temp_fd)
        temp_state = "replacement"
        try:
            _write_all(temp_fd, replacement, name=temp_name)
            try:
                os.fsync(temp_fd)
            except OSError as exc:
                raise _fail(
                    "guarded_replace_file_fsync_failed",
                    "guarded replacement file fsync failed",
                    errno=exc.errno,
                ) from exc

            temp_fingerprint = _file_fingerprint(temp_fd, name=temp_name)
            try:
                os.lseek(temp_fd, 0, os.SEEK_SET)
            except OSError as exc:
                raise _fail(
                    "guarded_replace_temp_verify_failed",
                    "guarded replacement temporary file could not be rewound",
                    errno=exc.errno,
                ) from exc
            temp_content = _read_bounded(temp_fd, max_bytes=max(1, bound), name=temp_name)
            if temp_content != replacement or temp_fingerprint.size != len(replacement):
                raise _fail(
                    "guarded_replace_temp_verify_failed",
                    "guarded replacement temporary bytes differ before commit",
                )
            _verify_leaf_entry(current.fd, temp_name, temp_fingerprint)

            _before_guarded_replace_revalidation(current.fd, leaf_name)
            _verify_expected_leaf(
                old_fd,
                parent_fd=current.fd,
                leaf_name=leaf_name,
                expected=expected,
            )
            for witness in directories:
                _verify_directory_witness(witness)

            try:
                _replace_guarded_leaf(temp_name, leaf_name, current.fd)
            except OSError as exc:
                raise _fail(
                    "guarded_replace_commit_failed",
                    "guarded replacement atomic exchange failed",
                    errno=exc.errno,
                ) from exc
            temp_state = "displaced"

            displaced_identity = _entry_identity(current.fd, temp_name)
            displaced_content: bytes | None = None
            mismatch: GuardedRegularFileSnapshotError | None = None
            try:
                _verify_exchange_leaf_entry(current.fd, leaf_name, temp_fingerprint)
                displaced_fingerprint, displaced_content = _capture_guarded_regular_entry(
                    stack,
                    parent_fd=current.fd,
                    name=temp_name,
                    max_bytes=bound,
                )
                if (
                    not _exchange_fingerprint_matches(displaced_fingerprint, _expected_fingerprint(expected))
                    or displaced_content != expected.content
                ):
                    mismatch = _fail(
                        "guarded_replace_expected_mismatch",
                        "atomic exchange displaced a leaf different from the expected snapshot",
                        name=expected.relative_path,
                    )
            except GuardedRegularFileSnapshotError as exc:
                mismatch = _fail(
                    "guarded_replace_expected_mismatch",
                    "atomic exchange could not prove the displaced expected snapshot",
                    name=expected.relative_path,
                    source_error_code=exc.code,
                )

            if mismatch is not None:
                temp_state = "uncertain"
                _rollback_guarded_exchange(
                    stack,
                    parent_fd=current.fd,
                    temp_name=temp_name,
                    leaf_name=leaf_name,
                    replacement_fingerprint=temp_fingerprint,
                    displaced_identity=displaced_identity,
                    displaced_content=displaced_content,
                    max_bytes=bound,
                )
                temp_state = "replacement"
                _cleanup_guarded_temp(current.fd, temp_name)
                temp_state = "absent"
                try:
                    os.fsync(current.fd)
                except OSError as exc:
                    raise _fail(
                        "guarded_replace_rollback_failed",
                        "guarded rollback cleanup could not be made durable",
                        errno=exc.errno,
                    ) from exc
                raise mismatch

            try:
                os.fsync(current.fd)
            except OSError as exc:
                durability_failure = _fail(
                    "guarded_replace_parent_fsync_failed",
                    "guarded replacement parent fsync failed after exchange",
                    errno=exc.errno,
                )
                temp_state = "uncertain"
                _rollback_guarded_exchange(
                    stack,
                    parent_fd=current.fd,
                    temp_name=temp_name,
                    leaf_name=leaf_name,
                    replacement_fingerprint=temp_fingerprint,
                    displaced_identity=displaced_identity,
                    displaced_content=displaced_content,
                    max_bytes=bound,
                )
                temp_state = "replacement"
                _cleanup_guarded_temp(current.fd, temp_name)
                temp_state = "absent"
                try:
                    os.fsync(current.fd)
                except OSError as rollback_fsync_exc:
                    raise _fail(
                        "guarded_replace_rollback_failed",
                        "guarded rollback cleanup could not be made durable",
                        errno=rollback_fsync_exc.errno,
                    ) from rollback_fsync_exc
                raise durability_failure from exc

            _cleanup_guarded_temp(current.fd, temp_name)
            temp_state = "absent"
            try:
                os.fsync(current.fd)
            except OSError as exc:
                raise _fail(
                    "guarded_replace_parent_fsync_failed",
                    "guarded replacement cleanup fsync failed after commit",
                    errno=exc.errno,
                ) from exc

            _after_guarded_replace(current.fd, leaf_name)
            try:
                new_fd = _open_descriptor(stack, leaf_name, parent_fd=current.fd, directory=False)
                new_fingerprint = _file_fingerprint(new_fd, name=expected.relative_path)
                new_content = _read_bounded(new_fd, max_bytes=max(1, bound), name=expected.relative_path)
                _verify_leaf_entry(current.fd, leaf_name, new_fingerprint)
            except GuardedRegularFileSnapshotError as exc:
                raise _fail(
                    "guarded_replace_postread_mismatch",
                    "guarded replacement could not be strictly re-read",
                    source_error_code=exc.code,
                ) from exc
            if new_content != replacement or new_fingerprint.size != len(replacement):
                raise _fail(
                    "guarded_replace_postread_mismatch",
                    "guarded replacement post-read bytes or size differ",
                )
            for witness in directories:
                _verify_directory_witness(witness)

            return GuardedRegularFileSnapshotV1(
                relative_path=expected.relative_path,
                content=new_content,
                size=new_fingerprint.size,
                device=new_fingerprint.device,
                inode=new_fingerprint.inode,
                mtime_ns=new_fingerprint.mtime_ns,
                ctime_ns=new_fingerprint.ctime_ns,
                root_device=root_witness.device,
                root_inode=root_witness.inode,
            )
        finally:
            if temp_state == "replacement":
                _cleanup_guarded_temp(current.fd, temp_name)


def guarded_compare_and_create_regular_file(
    root: str | PathLike[str],
    relative_path: str,
    content: bytes,
    *,
    max_bytes: int,
) -> GuardedRegularFileSnapshotV1:
    """Create one exact regular file only when its commit-time leaf is absent."""

    bound = _validated_max_bytes(max_bytes)
    if type(content) is not bytes:
        raise ValueError("content must be exact immutable bytes")
    if len(content) > bound:
        raise _fail(
            "guarded_create_max_bytes_exceeded",
            "guarded create content exceeds max_bytes",
            max_bytes=bound,
            size=len(content),
        )
    path_parts = _validated_relative_parts(relative_path)
    root_path, root_parts = _validated_root(root)
    _require_replace_capabilities()

    with ExitStack() as stack:
        directories: list[_DirectoryWitness] = []
        current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
        directories.append(current)
        display_parts: list[str] = []
        for component in root_parts:
            display_parts.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name="/" + "/".join(display_parts),
            )
            directories.append(current)
        root_witness = current

        relative_display: list[str] = []
        for component in path_parts[:-1]:
            relative_display.append(component)
            # Create missing intermediate directories under the validated root so
            # deferred DEO materialization (e.g. tests/verify.test.ts smoke) can
            # land without a separate mkdir tool. Root ancestors stay strict.
            current = _retain_or_mkdir_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name=f"{root_path.rstrip('/')}/{'/'.join(relative_display)}",
            )
            directories.append(current)

        leaf_name = path_parts[-1]
        temp_name, temp_fd = _open_guarded_replace_temp(current.fd, leaf_name)
        stack.callback(os.close, temp_fd)
        temp_present = True
        try:
            _write_all(temp_fd, content, name=temp_name)
            os.fsync(temp_fd)
            temp_fingerprint = _file_fingerprint(temp_fd, name=temp_name)
            os.lseek(temp_fd, 0, os.SEEK_SET)
            if _read_bounded(temp_fd, max_bytes=max(1, bound), name=temp_name) != content:
                raise _fail(
                    "guarded_create_temp_verify_failed",
                    "guarded create temporary bytes differ before commit",
                )
            _verify_leaf_entry(current.fd, temp_name, temp_fingerprint)
            for witness in directories:
                _verify_directory_witness(witness)

            _before_guarded_create_commit(current.fd, leaf_name)
            try:
                os.stat(leaf_name, dir_fd=current.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _fail(
                    "guarded_create_expected_mismatch",
                    "guarded create target exists at commit time",
                    name=relative_path,
                )
            try:
                _rename_guarded_leaf_noreplace(temp_name, leaf_name, current.fd)
            except FileExistsError as exc:
                raise _fail(
                    "guarded_create_expected_mismatch",
                    "guarded create target appeared at commit time",
                    name=relative_path,
                ) from exc
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise _fail(
                        "guarded_create_expected_mismatch",
                        "guarded create target appeared at commit time",
                        name=relative_path,
                    ) from exc
                raise _fail(
                    "guarded_create_commit_failed",
                    "guarded create atomic no-replace commit failed",
                    errno=exc.errno,
                ) from exc
            temp_present = False
            os.fsync(current.fd)

            new_fd = _open_descriptor(stack, leaf_name, parent_fd=current.fd, directory=False)
            new_fingerprint = _file_fingerprint(new_fd, name=relative_path)
            new_content = _read_bounded(new_fd, max_bytes=max(1, bound), name=relative_path)
            _verify_leaf_entry(current.fd, leaf_name, new_fingerprint)
            if new_content != content or new_fingerprint.size != len(content):
                raise _fail(
                    "guarded_create_postread_mismatch",
                    "guarded create post-read bytes or size differ",
                )
            for witness in directories:
                _verify_directory_witness(witness)
            return GuardedRegularFileSnapshotV1(
                relative_path=relative_path,
                content=new_content,
                size=new_fingerprint.size,
                device=new_fingerprint.device,
                inode=new_fingerprint.inode,
                mtime_ns=new_fingerprint.mtime_ns,
                ctime_ns=new_fingerprint.ctime_ns,
                root_device=root_witness.device,
                root_inode=root_witness.inode,
            )
        except OSError as exc:
            raise _fail(
                "guarded_create_io_failed",
                "guarded create I/O failed",
                errno=exc.errno,
            ) from exc
        finally:
            if temp_present:
                _cleanup_guarded_temp(current.fd, temp_name)


def guarded_compare_and_remove_regular_file(
    root: str | PathLike[str],
    expected: GuardedRegularFileSnapshotV1,
    *,
    max_bytes: int,
) -> None:
    """Remove one exact snapshot without deleting a commit-time replacement."""

    bound = _validated_max_bytes(max_bytes)
    if type(expected) is not GuardedRegularFileSnapshotV1:
        raise ValueError("expected must be an exact GuardedRegularFileSnapshotV1")
    if expected.size != len(expected.content) or expected.size > bound:
        raise _fail(
            "guarded_remove_expected_invalid",
            "expected snapshot size/content is internally inconsistent or unbounded",
        )

    path_parts = _validated_relative_parts(expected.relative_path)
    root_path, root_parts = _validated_root(root)
    _require_replace_capabilities()

    # Reuse the proven atomic exchange CAS. The unique tombstone is then moved
    # out of the logical path with RENAME_NOREPLACE before it is unlinked.
    tombstone = f"polaris-guarded-remove:{uuid.uuid4().hex}".encode("ascii")
    _before_guarded_remove_revalidation(-1, path_parts[-1])
    try:
        replacement = guarded_compare_and_replace_regular_file(
            root,
            expected,
            tombstone,
            max_bytes=max(bound, len(tombstone)),
        )
    except GuardedRegularFileSnapshotError as exc:
        if exc.code == "guarded_replace_expected_mismatch":
            raise _fail(
                "guarded_remove_expected_mismatch",
                "guarded remove target differs from the expected snapshot",
                source_error_code=exc.code,
            ) from exc
        raise

    with ExitStack() as stack:
        directories: list[_DirectoryWitness] = []
        current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
        directories.append(current)
        display_parts: list[str] = []
        for component in root_parts:
            display_parts.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name="/" + "/".join(display_parts),
            )
            directories.append(current)
        if (current.device, current.inode) != (replacement.root_device, replacement.root_inode):
            raise _fail(
                "guarded_remove_expected_mismatch",
                "guarded remove root differs after compare-and-replace",
            )
        relative_display: list[str] = []
        for component in path_parts[:-1]:
            relative_display.append(component)
            current = _retain_directory(
                stack,
                parent=current,
                entry_name=component,
                display_name=f"{root_path.rstrip('/')}/{'/'.join(relative_display)}",
            )
            directories.append(current)

        leaf_name = path_parts[-1]
        leaf_fd = _open_descriptor(stack, leaf_name, parent_fd=current.fd, directory=False)
        _verify_expected_leaf(
            leaf_fd,
            parent_fd=current.fd,
            leaf_name=leaf_name,
            expected=replacement,
        )
        for witness in directories:
            _verify_directory_witness(witness)

        removed_name = f".{leaf_name[:64]}.{uuid.uuid4().hex}.tmp"
        try:
            _rename_guarded_leaf_noreplace(leaf_name, removed_name, current.fd)
        except OSError as exc:
            raise _fail(
                "guarded_remove_commit_failed",
                "guarded remove atomic no-replace move failed",
                errno=exc.errno,
            ) from exc
        moved_identity = _entry_identity(current.fd, removed_name)
        try:
            moved_fd = _open_descriptor(stack, removed_name, parent_fd=current.fd, directory=False)
            moved_fingerprint = _file_fingerprint(moved_fd, name=removed_name)
            moved_content = _read_bounded(moved_fd, max_bytes=max(1, len(tombstone)), name=removed_name)
            if (
                not _exchange_fingerprint_matches(moved_fingerprint, _expected_fingerprint(replacement))
                or moved_content != tombstone
            ):
                raise _fail(
                    "guarded_remove_expected_mismatch",
                    "guarded remove moved a leaf different from its private tombstone",
                )
            _verify_entry_identity(current.fd, removed_name, moved_identity)
        except GuardedRegularFileSnapshotError:
            try:
                _rename_guarded_leaf_noreplace(removed_name, leaf_name, current.fd)
            except OSError as rollback_exc:
                raise _fail(
                    "guarded_remove_reconciliation_required",
                    "guarded remove preserved a concurrent leaf but could not restore its path",
                    errno=rollback_exc.errno,
                    preserved_name=removed_name,
                ) from rollback_exc
            raise

        _cleanup_guarded_temp(current.fd, removed_name)
        os.fsync(current.fd)
