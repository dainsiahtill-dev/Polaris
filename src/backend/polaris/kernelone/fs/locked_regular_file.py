"""Provisioned platform lock authorities and descriptor-bound stream leases."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import stat
import threading
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final, Literal

from polaris.kernelone.storage import kernelone_home

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


LOCK_AUTHORITY_FORMAT_REVISION: Final[str] = "polaris.lock-authority.v1"
_POLL_SECONDS: Final[float] = 0.01
_PROVISION_LOCK_MARKER: Final[bytes] = b"polaris.provision-lock.v1\n"
_DEFAULT_MAINTENANCE_TIMEOUT_SECONDS: Final[float] = 5.0


class LockedRegularFileError(RuntimeError):
    """Typed locked-regular-file capability failure."""

    def __init__(self, message: str, *, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def default_platform_lock_root() -> str:
    """Return the platform-owned, non-runtime authority root."""

    return os.path.join(kernelone_home(), "lock_authorities", "v1")


def _fail(code: str, message: str, **details: object) -> LockedRegularFileError:
    return LockedRegularFileError(message, code=code, details=details)


def _deadline_from_timeout(timeout_seconds: float) -> float:
    value = float(timeout_seconds)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    return time.monotonic() + value


def _post_fsync_reconciliation(
    message: str,
    cause: LockedRegularFileError,
    **details: object,
) -> LockedRegularFileError:
    """Project a post-durability failure without losing its concrete cause."""

    return _fail(
        "post_fsync_authority_reconciliation_required",
        message,
        cause_code=cause.code,
        cause_details=dict(cause.details),
        **details,
    )


def _platform_name() -> str:
    """Return the active interpreter platform name for capability checks.

    This module-owned seam keeps production detection bound to ``os.name``
    while allowing tests to exercise unavailable-platform behavior without
    mutating process-global stdlib state.
    """

    return os.name


def _require_posix() -> None:
    if _platform_name() == "nt" or fcntl is None:
        raise _fail("guarded_fs_capability_unavailable", "descriptor-safe guarded filesystem support is unavailable")
    if any(not hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_DIRECTORY")):
        raise _fail("guarded_fs_capability_unavailable", "required descriptor-safe filesystem flags are unavailable")


def _flags(*, writable: bool = False, directory: bool = False, append: bool = False) -> int:
    _require_posix()
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if directory:
        flags |= os.O_DIRECTORY
    if append:
        if not writable:
            raise ValueError("append descriptors must be writable")
        flags |= os.O_APPEND
    return flags


def _identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _regular(fd: int, *, code: str, name: str) -> tuple[int, int]:
    try:
        info = os.fstat(fd)
    except OSError as exc:
        raise _fail(code, "regular-file descriptor could not be verified", name=name, errno=exc.errno) from exc
    if not stat.S_ISREG(info.st_mode):
        raise _fail(code, "authority or stream object is not a regular file", name=name)
    if info.st_nlink != 1:
        raise _fail("hard_link_rejected", "authority or stream object has multiple links", name=name)
    return info.st_dev, info.st_ino


def _directory(fd: int, *, name: str) -> tuple[int, int]:
    try:
        info = os.fstat(fd)
    except OSError as exc:
        raise _fail(
            "stream_identity_drift",
            "directory descriptor could not be verified",
            errno=exc.errno,
            name=name,
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise _fail("stream_identity_drift", "required directory identity is unsafe", name=name)
    return info.st_dev, info.st_ino


def _verify_directory_entry(
    directory_fd: int,
    name: str,
    fd: int,
    *,
    code: str,
    regular: bool,
) -> tuple[int, int]:
    """Prove that a descriptor still names the expected safe directory entry."""

    try:
        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise _fail(code, "authority directory entry could not be verified", name=name, errno=exc.errno) from exc
    if regular:
        if not stat.S_ISREG(entry.st_mode):
            raise _fail(code, "authority directory entry is not a regular file", name=name)
        if entry.st_nlink != 1:
            raise _fail("hard_link_rejected", "authority directory entry has multiple links", name=name)
    elif not stat.S_ISDIR(entry.st_mode):
        raise _fail(code, "authority directory entry is not a directory", name=name)
    entry_identity = entry.st_dev, entry.st_ino
    if entry_identity != _identity(fd):
        raise _fail(code, "authority directory entry identity changed", name=name)
    return entry_identity


def _logical_path(value: str) -> tuple[str, tuple[str, ...]]:
    canonical = unicodedata.normalize("NFC", str(value or "").strip().replace("\\", "/"))
    path = PurePosixPath(canonical)
    if not canonical.startswith("runtime/") or path.is_absolute() or ".." in path.parts:
        raise ValueError("stream lock paths must be runtime-relative logical paths")
    parts = path.parts[1:]
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("stream lock path must name a leaf below runtime/")
    return canonical, tuple(parts)


def _key(storage_identity: str, logical_path: str) -> str:
    material = f"{storage_identity}\x00{unicodedata.normalize('NFC', logical_path).casefold()}".encode()
    return hashlib.sha256(material).hexdigest() + ".lock"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(slots=True)
class _CreatedAuthorityDirectory:
    """Descriptor-owned durability evidence for one successful authority mkdir."""

    path: str
    fd: int
    parent_path: str
    parent_fd: int


@dataclass(slots=True)
class _AuthorityDirectoryDurability:
    """Track authority directory creation and fsync it without path reopening."""

    _created: list[_CreatedAuthorityDirectory] = field(default_factory=list)
    _completed_fsync_order: list[str] = field(default_factory=list)
    _boundary_crossed: bool = False

    @property
    def created_directories(self) -> tuple[str, ...]:
        """Return the successfully created authority directories in creation order."""

        return tuple(item.path for item in self._created)

    @property
    def completed_fsync_order(self) -> tuple[str, ...]:
        """Return the completed file and directory fsync operations in order."""

        return tuple(self._completed_fsync_order)

    @property
    def boundary_crossed(self) -> bool:
        """Whether a durability outcome has completed or become ambiguous."""

        return self._boundary_crossed

    def record_created(
        self,
        *,
        path: str,
        fd: int,
        parent_path: str,
        parent_fd: int,
    ) -> None:
        """Retain descriptor duplicates for a directory created by this provision."""

        _directory(fd, name=path)
        _directory(parent_fd, name=parent_path)
        self._created.append(
            _CreatedAuthorityDirectory(
                path=path,
                fd=os.dup(fd),
                parent_path=parent_path,
                parent_fd=os.dup(parent_fd),
            )
        )

    def _fsync(self, fd: int, *, path: str, cause_code: str) -> None:
        """Fsync one retained descriptor and retain ordered durability evidence."""

        self._boundary_crossed = True
        try:
            os.fsync(fd)
        except OSError as exc:
            raise _fail(
                cause_code,
                "authority directory fsync outcome is ambiguous",
                errno=exc.errno,
                path=path,
                completed_fsync_order=list(self._completed_fsync_order),
            ) from exc
        self._completed_fsync_order.append(path)

    def fsync_created_directories(self) -> None:
        """Fsync created directories and parents in descendant-to-ancestor order."""

        candidates: dict[tuple[int, int], tuple[int, str, str]] = {}
        parent_by_child: dict[tuple[int, int], tuple[int, int]] = {}
        ordering: list[tuple[int, int]] = []
        for item in reversed(self._created):
            child_identity = _directory(item.fd, name=item.path)
            parent_identity = _directory(item.parent_fd, name=item.parent_path)
            parent_by_child[child_identity] = parent_identity
            for identity, fd, path, cause_code in (
                (child_identity, item.fd, item.path, "directory_fsync_failed"),
                (parent_identity, item.parent_fd, item.parent_path, "parent_directory_fsync_failed"),
            ):
                existing = candidates.get(identity)
                if existing is None or cause_code == "directory_fsync_failed":
                    candidates[identity] = (fd, path, cause_code)
                if identity not in ordering:
                    ordering.append(identity)

        remaining_children = dict.fromkeys(candidates, 0)
        for _child_identity, parent_identity in parent_by_child.items():
            if parent_identity in remaining_children:
                remaining_children[parent_identity] += 1
        ready = [identity for identity in ordering if remaining_children[identity] == 0]
        while ready:
            identity = ready.pop(0)
            fd, path, cause_code = candidates[identity]
            self._fsync(fd, path=path, cause_code=cause_code)
            next_parent_identity = parent_by_child.get(identity)
            if next_parent_identity is not None and next_parent_identity in remaining_children:
                remaining_children[next_parent_identity] -= 1
                if remaining_children[next_parent_identity] == 0:
                    ready.append(next_parent_identity)

    def record_file_fsync(self, path: str) -> None:
        """Record a completed authority-file fsync in the shared proof ordering."""

        self._completed_fsync_order.append(path)

    def begin_file_fsync(self) -> None:
        """Mark the authority-file durability boundary before invoking fsync."""

        self._boundary_crossed = True

    def fsync_existing_directory(self, fd: int, *, path: str) -> None:
        """Durably publish a new entry inside an authority directory that already existed."""

        self._fsync(fd, path=path, cause_code="directory_fsync_failed")

    def reconciliation_details(self) -> dict[str, object]:
        """Return detached, diagnostic-only durability progress evidence."""

        return {
            "created_directories": list(self.created_directories),
            "completed_fsync_order": list(self.completed_fsync_order),
        }

    def close(self) -> None:
        """Release retained descriptor duplicates after provision completes or fails."""

        for item in self._created:
            os.close(item.fd)
            os.close(item.parent_fd)
        self._created.clear()


@dataclass(slots=True)
class _CreatedStreamDirectory:
    """Descriptor-owned evidence for one logical stream ancestor creation."""

    path: str
    identity: tuple[int, int]
    fd: int
    parent_path: str
    parent_identity: tuple[int, int]
    parent_fd: int


@dataclass(slots=True)
class _StreamDirectoryDurability:
    """Retain stream-ancestor descriptors until the owning lease is released."""

    _created: list[_CreatedStreamDirectory] = field(default_factory=list)
    _operation_start: int = 0
    _completed_fsync_order: list[str] = field(default_factory=list)
    _boundary_crossed: bool = False

    @property
    def boundary_crossed(self) -> bool:
        """Whether the current append has entered a durability boundary."""

        return self._boundary_crossed

    @property
    def created_for_append(self) -> bool:
        """Whether this append created at least one logical-path ancestor."""

        return len(self._created) > self._operation_start

    def begin_append(self) -> None:
        """Reset per-append proof while retaining lease-owned descriptors."""

        self._operation_start = len(self._created)
        self._completed_fsync_order.clear()
        self._boundary_crossed = False

    def record_created(
        self,
        *,
        path: str,
        fd: int,
        parent_path: str,
        parent_fd: int,
    ) -> None:
        """Duplicate and validate a newly-created directory with its parent."""

        identity = _directory(fd, name=path)
        parent_identity = _directory(parent_fd, name=parent_path)
        self._created.append(
            _CreatedStreamDirectory(
                path=path,
                identity=identity,
                fd=os.dup(fd),
                parent_path=parent_path,
                parent_identity=parent_identity,
                parent_fd=os.dup(parent_fd),
            )
        )

    def begin_file_fsync(self) -> None:
        """Mark the current append durable before the leaf fsync call."""

        self._boundary_crossed = True

    def record_file_fsync(self, path: str) -> None:
        """Record the completed leaf fsync in the append durability proof."""

        self._completed_fsync_order.append(path)

    def _fsync(self, fd: int, *, path: str, cause_code: str) -> None:
        """Fsync a retained descriptor after revalidating its identity."""

        self._boundary_crossed = True
        try:
            os.fsync(fd)
        except OSError as exc:
            raise _fail(
                cause_code,
                "stream directory fsync outcome is ambiguous",
                errno=exc.errno,
                path=path,
                completed_fsync_order=list(self._completed_fsync_order),
            ) from exc
        self._completed_fsync_order.append(path)

    def fsync_created_directories(self) -> None:
        """Fsync created stream ancestors and parents descendant-to-ancestor."""

        candidates: dict[tuple[int, int], tuple[int, str, str]] = {}
        parent_by_child: dict[tuple[int, int], tuple[int, int]] = {}
        ordering: list[tuple[int, int]] = []
        for item in reversed(self._created[self._operation_start :]):
            if _directory(item.fd, name=item.path) != item.identity:
                raise _fail("stream_identity_drift", "created stream ancestor identity changed", path=item.path)
            if _directory(item.parent_fd, name=item.parent_path) != item.parent_identity:
                raise _fail(
                    "stream_identity_drift",
                    "created stream ancestor parent identity changed",
                    path=item.parent_path,
                )
            parent_by_child[item.identity] = item.parent_identity
            for identity, fd, path, cause_code in (
                (item.identity, item.fd, item.path, "stream_directory_fsync_failed"),
                (item.parent_identity, item.parent_fd, item.parent_path, "stream_parent_directory_fsync_failed"),
            ):
                existing = candidates.get(identity)
                if existing is None or cause_code == "stream_directory_fsync_failed":
                    candidates[identity] = (fd, path, cause_code)
                if identity not in ordering:
                    ordering.append(identity)

        remaining_children = dict.fromkeys(candidates, 0)
        for parent_identity in parent_by_child.values():
            if parent_identity in remaining_children:
                remaining_children[parent_identity] += 1
        ready = [identity for identity in ordering if remaining_children[identity] == 0]
        while ready:
            identity = ready.pop(0)
            fd, path, cause_code = candidates[identity]
            self._fsync(fd, path=path, cause_code=cause_code)
            next_parent_identity = parent_by_child.get(identity)
            if next_parent_identity is not None and next_parent_identity in remaining_children:
                remaining_children[next_parent_identity] -= 1
                if remaining_children[next_parent_identity] == 0:
                    ready.append(next_parent_identity)

    def reconciliation_details(self) -> dict[str, object]:
        """Return exact descriptor-validated progress evidence for this append."""

        return {
            "completed_fsync_order": list(self._completed_fsync_order),
            "created_directories": [
                {
                    "identity": {"device": item.identity[0], "inode": item.identity[1]},
                    "parent_identity": {"device": item.parent_identity[0], "inode": item.parent_identity[1]},
                    "parent_path": item.parent_path,
                    "path": item.path,
                }
                for item in self._created[self._operation_start :]
            ],
        }

    def detach(self) -> tuple[int, ...]:
        """Detach all retained descriptor duplicates for owner-managed closing."""

        descriptors = tuple(fd for item in self._created for fd in (item.fd, item.parent_fd))
        self._created.clear()
        self._operation_start = 0
        self._completed_fsync_order.clear()
        self._boundary_crossed = False
        return descriptors


def _open_absolute_directory(
    path: str,
    *,
    create: bool,
    durability: _AuthorityDirectoryDurability | None = None,
) -> int:
    if not os.path.isabs(path):
        raise ValueError("authority paths must be absolute")
    fd = os.open(os.sep, _flags(directory=True))
    try:
        parts = tuple(item for item in path.split(os.sep) if item)
        for index, part in enumerate(parts):
            created = False
            try:
                next_fd = os.open(part, _flags(directory=True), dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                next_fd = os.open(part, _flags(directory=True), dir_fd=fd)
            current_path = os.path.join(os.sep, *parts[: index + 1])
            parent_path = os.path.join(os.sep, *parts[:index]) if index else os.sep
            _directory(next_fd, name=current_path)
            if created and durability is not None:
                durability.record_created(
                    path=current_path,
                    fd=next_fd,
                    parent_path=parent_path,
                    parent_fd=fd,
                )
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


@dataclass(frozen=True, slots=True)
class LockAuthorityBindingV1:
    """Immutable anchor binding for one storage identity."""

    storage_identity: str
    runtime_root: str
    root_device: int
    root_inode: int
    realm_device: int
    realm_inode: int
    format_revision: str = LOCK_AUTHORITY_FORMAT_REVISION

    def to_record(self) -> dict[str, object]:
        return {
            "format_revision": self.format_revision,
            "realm_device": self.realm_device,
            "realm_inode": self.realm_inode,
            "root_device": self.root_device,
            "root_inode": self.root_inode,
            "runtime_root": self.runtime_root,
            "storage_identity": self.storage_identity,
        }


@dataclass(frozen=True, slots=True)
class LockFileIdentityV1:
    """Stable typed device/inode identity used by maintenance evidence."""

    device: int
    inode: int

    def to_record(self) -> dict[str, int]:
        """Return a deterministic serialization of this physical identity."""

        return {"device": self.device, "inode": self.inode}


@dataclass(frozen=True, slots=True)
class LockKeyMaintenanceProofV1:
    """Final evidence for one canonical logical-path lock key."""

    logical_path: str
    lock_key: str
    verdict: Literal["created", "already_present"]
    identity: LockFileIdentityV1

    def to_record(self) -> dict[str, object]:
        """Return a deterministic serialization of this lock-key result."""

        return {
            "identity": self.identity.to_record(),
            "lock_key": self.lock_key,
            "logical_path": self.logical_path,
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class LockMaintenanceProofV1:
    """Immutable proof emitted by every successful authority maintenance call."""

    operation: Literal["provision_authority", "enroll_stream_lock_keys"]
    verdict: Literal["created", "already_present"]
    storage_identity: str
    runtime_root: str
    format_revision: str
    root_identity: LockFileIdentityV1
    anchor_identity: LockFileIdentityV1
    realm_identity: LockFileIdentityV1
    lock_keys: tuple[LockKeyMaintenanceProofV1, ...]
    final_validation: Literal[True]
    created_directories: tuple[str, ...] = ()
    completed_fsync_order: tuple[str, ...] = ()

    def to_record(self) -> dict[str, object]:
        """Return stable UTF-8/JSON-compatible maintenance evidence."""

        return {
            "anchor_identity": self.anchor_identity.to_record(),
            "completed_fsync_order": list(self.completed_fsync_order),
            "created_directories": list(self.created_directories),
            "final_validation": self.final_validation,
            "format_revision": self.format_revision,
            "lock_keys": [item.to_record() for item in self.lock_keys],
            "operation": self.operation,
            "realm_identity": self.realm_identity.to_record(),
            "root_identity": self.root_identity.to_record(),
            "runtime_root": self.runtime_root,
            "storage_identity": self.storage_identity,
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class _ValidatedAuthority:
    root_identity: LockFileIdentityV1
    anchor_identity: LockFileIdentityV1
    realm_identity: LockFileIdentityV1


def _binding_from_fd(anchor_fd: int) -> LockAuthorityBindingV1:
    try:
        os.lseek(anchor_fd, 0, os.SEEK_SET)
        raw = b""
        while True:
            chunk = os.read(anchor_fd, 64 * 1024)
            if not chunk:
                break
            raw += chunk
        record = json.loads(raw.decode("utf-8"))
        if not isinstance(record, dict):
            raise ValueError("binding is not an object")
        return LockAuthorityBindingV1(
            storage_identity=str(record["storage_identity"]),
            runtime_root=str(record["runtime_root"]),
            root_device=int(record["root_device"]),
            root_inode=int(record["root_inode"]),
            realm_device=int(record["realm_device"]),
            realm_inode=int(record["realm_inode"]),
            format_revision=str(record["format_revision"]),
        )
    except OSError as exc:
        raise _fail("lock_anchor_invalid", "anchor binding could not be read", errno=exc.errno) from exc
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("lock_anchor_invalid", "anchor binding is malformed") from exc


def _flock(fd: int, operation: int, *, deadline: float | None = None) -> None:
    assert fcntl is not None
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise _fail("lock_acquisition_timeout", "advisory lock exceeded monotonic deadline")
        try:
            fcntl.flock(fd, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if deadline is not None and time.monotonic() >= deadline:
                raise _fail("lock_acquisition_timeout", "advisory lock exceeded monotonic deadline") from None
            time.sleep(_POLL_SECONDS if deadline is None else min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        except OSError as exc:
            raise _fail("lock_acquisition_failed", "advisory lock acquisition failed", errno=exc.errno) from exc


def _open_stream_exclusive(parent_fd: int, name: str) -> int:
    """Create one stream leaf through the injectable exclusive-create seam."""

    return os.open(
        name,
        _flags(writable=True, append=True) | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=parent_fd,
    )


@dataclass(slots=True)
class StreamLeaseV1:
    """One owner-serialized descriptor lease for an enrolled stream file."""

    _owner: LockedRegularFileSetV1
    logical_path: str
    parts: tuple[str, ...]
    _parent_fd: int | None = None
    _parent_identity: tuple[int, int] | None = None
    _ancestor_identities: tuple[tuple[str, tuple[int, int]], ...] = ()
    _directory_durability: _StreamDirectoryDurability = field(default_factory=_StreamDirectoryDurability)
    _file_fd: int | None = None
    _file_identity: tuple[int, int] | None = None

    @property
    def exists(self) -> bool:
        return self._file_fd is not None

    def open_existing(self, *, writable: bool = False) -> bool:
        with self._owner._operation():
            try:
                self._ensure_parent(create=False)
            except LockedRegularFileError as exc:
                if exc.code == "stream_parent_missing":
                    return False
                raise
            assert self._parent_fd is not None
            self._verify_path()
            try:
                fd = os.open(self.parts[-1], _flags(writable=writable, append=writable), dir_fd=self._parent_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise _fail("unsafe_stream_object", "stream leaf could not be opened safely", errno=exc.errno) from exc
            try:
                identity = _regular(fd, code="unsafe_stream_object", name=self.logical_path)
                self._verify_leaf(identity)
            except Exception:
                os.close(fd)
                raise
            self._close_file()
            self._file_fd, self._file_identity = fd, identity
            return True

    def read_bytes(self) -> bytes:
        with self._owner._operation():
            if self._file_fd is None:
                return b""
            self._verify_file()
            try:
                os.lseek(self._file_fd, 0, os.SEEK_SET)
                chunks: list[bytes] = []
                while chunk := os.read(self._file_fd, 1024 * 1024):
                    chunks.append(chunk)
                self._verify_file()
                return b"".join(chunks)
            except OSError as exc:
                raise _fail("stream_identity_drift", "stream descriptor read failed", errno=exc.errno) from exc

    def append_bytes(self, payload: bytes, *, fsync_file: bool, fsync_parent_on_create: bool) -> None:
        with self._owner._operation():
            if not payload:
                raise ValueError("append payload must not be empty")
            durability = self._directory_durability
            durability.begin_append()
            created = False
            try:
                if self._file_fd is None:
                    self._ensure_parent(create=True)
                    self._verify_path()
                    assert self._parent_fd is not None
                    try:
                        fd = _open_stream_exclusive(self._parent_fd, self.parts[-1])
                    except FileExistsError:
                        self.open_existing(writable=True)
                    except OSError as exc:
                        raise _fail("unsafe_stream_object", "stream creation failed safely", errno=exc.errno) from exc
                    else:
                        try:
                            identity = _regular(fd, code="unsafe_stream_object", name=self.logical_path)
                            self._verify_leaf(identity)
                        except Exception:
                            os.close(fd)
                            raise
                        self._file_fd, self._file_identity, created = fd, identity, True
                assert self._file_fd is not None
                self._verify_file()
                view = memoryview(payload)
                while view:
                    count = os.write(self._file_fd, view)
                    if count <= 0:
                        raise OSError(errno.EIO, "short append write")
                    view = view[count:]
                if fsync_file:
                    durability.begin_file_fsync()
                    try:
                        os.fsync(self._file_fd)
                    except OSError as exc:
                        raise _fail(
                            "file_fsync_reconciliation_required",
                            "file fsync outcome is ambiguous",
                            errno=exc.errno,
                        ) from exc
                    durability.record_file_fsync(self.logical_path)
                    self._verify_after_durable_append()
                else:
                    self._verify_file()
                if (created or durability.created_for_append) and fsync_parent_on_create:
                    durability.fsync_created_directories()
                if fsync_file:
                    self._verify_after_durable_append()
                else:
                    self._verify_file()
            except LockedRegularFileError as exc:
                if durability.boundary_crossed:
                    raise _post_fsync_reconciliation(
                        "stream append requires reconciliation after a durability boundary",
                        exc,
                        **durability.reconciliation_details(),
                    ) from exc
                raise
            except OSError as exc:
                cause = _fail("append_write_failed", "stream append write failed", errno=exc.errno)
                if durability.boundary_crossed:
                    raise _post_fsync_reconciliation(
                        "stream append requires reconciliation after a durability boundary",
                        cause,
                        **durability.reconciliation_details(),
                    ) from exc
                raise cause from exc

    def _ensure_parent(self, *, create: bool) -> None:
        if self._parent_fd is not None:
            return
        fd = os.dup(self._owner._root_fd_required())
        ancestors: list[tuple[str, tuple[int, int]]] = []
        try:
            for index, part in enumerate(self.parts[:-1]):
                created = False
                try:
                    next_fd = os.open(part, _flags(directory=True), dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        created = True
                    except FileExistsError:
                        pass
                    next_fd = os.open(part, _flags(directory=True), dir_fd=fd)
                identity = _directory(next_fd, name=self.logical_path)
                if created:
                    path = "runtime/" + "/".join(self.parts[: index + 1])
                    parent_path = "runtime" if index == 0 else "runtime/" + "/".join(self.parts[:index])
                    self._directory_durability.record_created(
                        path=path,
                        fd=next_fd,
                        parent_path=parent_path,
                        parent_fd=fd,
                    )
                os.close(fd)
                fd = next_fd
                ancestors.append((part, identity))
            self._parent_fd, self._parent_identity = fd, _identity(fd)
            self._ancestor_identities = tuple(ancestors)
        except FileNotFoundError:
            os.close(fd)
            raise _fail("stream_parent_missing", "stream parent is absent", logical_path=self.logical_path) from None
        except OSError as exc:
            os.close(fd)
            raise _fail("stream_identity_drift", "stream parent traversal failed", errno=exc.errno) from exc

    def _verify_path(self) -> None:
        self._owner._validate_authority()
        root_fd = self._owner._root_fd_required()
        if _identity(root_fd) != self._owner._root_identity_required():
            raise _fail("stream_identity_drift", "trusted runtime root descriptor changed")
        try:
            current_root_fd = _open_absolute_directory(self._owner.runtime_root, create=False)
        except OSError as exc:
            raise _fail("stream_identity_drift", "runtime root name traversal failed", errno=exc.errno) from exc
        try:
            if _identity(current_root_fd) != self._owner._root_identity_required():
                raise _fail("stream_identity_drift", "runtime root identity changed")
        finally:
            os.close(current_root_fd)
        if self._parent_fd is None:
            return
        fd = os.dup(root_fd)
        try:
            for part, expected in self._ancestor_identities:
                next_fd = os.open(part, _flags(directory=True), dir_fd=fd)
                os.close(fd)
                fd = next_fd
                if _directory(fd, name=self.logical_path) != expected:
                    raise _fail("stream_identity_drift", "stream ancestor identity changed")
            if self._parent_identity is None or _identity(fd) != self._parent_identity:
                raise _fail("stream_identity_drift", "stream parent identity changed")
        except OSError as exc:
            raise _fail("stream_identity_drift", "stream ancestor traversal failed", errno=exc.errno) from exc
        finally:
            os.close(fd)

    def _verify_leaf(self, expected: tuple[int, int]) -> None:
        assert self._parent_fd is not None
        try:
            info = os.stat(self.parts[-1], dir_fd=self._parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise _fail("stream_identity_drift", "stream leaf identity changed", errno=exc.errno) from exc
        if not stat.S_ISREG(info.st_mode):
            raise _fail("unsafe_stream_object", "stream leaf is unsafe")
        if info.st_nlink != 1:
            raise _fail("hard_link_rejected", "stream leaf has multiple links")
        if (info.st_dev, info.st_ino) != expected:
            raise _fail("stream_identity_drift", "stream leaf identity changed")

    def _verify_file(self) -> None:
        self._verify_path()
        assert self._file_fd is not None and self._file_identity is not None
        if _regular(self._file_fd, code="unsafe_stream_object", name=self.logical_path) != self._file_identity:
            raise _fail("stream_identity_drift", "held stream descriptor changed")
        self._verify_leaf(self._file_identity)

    def _verify_after_durable_append(self) -> None:
        """Fence every final path or authority drift after a durable append."""

        self._verify_file()

    def _close_file(self) -> None:
        if self._file_fd is not None:
            os.close(self._file_fd)
            self._file_fd, self._file_identity = None, None

    def _release_reconciliation_details(self) -> dict[str, object] | None:
        """Return final append evidence when descriptor release can revoke success."""

        if not self._directory_durability.boundary_crossed:
            return None
        return self._directory_durability.reconciliation_details()

    def _detach_descriptors(self) -> tuple[int | None, ...]:
        """Atomically detach lease descriptors while the owner mutex is held."""

        file_fd, parent_fd = self._file_fd, self._parent_fd
        self._file_fd = None
        self._file_identity = None
        self._parent_fd = None
        self._parent_identity = None
        self._ancestor_identities = ()
        return file_fd, parent_fd, *self._directory_durability.detach()


@dataclass(slots=True)
class LockedRegularFileSetV1:
    """Provisioned-authority lease set with a single lifecycle mutex."""

    runtime_root: str
    storage_identity_token: str
    logical_paths: tuple[str, ...]
    platform_lock_root: str = ""
    timeout_seconds: float = 2.0
    format_revision: str = LOCK_AUTHORITY_FORMAT_REVISION
    _state: str = field(default="ACTIVE", init=False)
    _mutex: threading.RLock = field(default_factory=threading.RLock, init=False)
    _anchor_fd: int | None = field(default=None, init=False)
    _realm_fd: int | None = field(default=None, init=False)
    _root_fd: int | None = field(default=None, init=False)
    _root_identity: tuple[int, int] | None = field(default=None, init=False)
    _binding: LockAuthorityBindingV1 | None = field(default=None, init=False)
    _lock_fds: list[int] = field(default_factory=list, init=False)
    _leases: dict[str, StreamLeaseV1] = field(default_factory=dict, init=False)
    _close_complete: threading.Event = field(default_factory=threading.Event, init=False)

    @classmethod
    def provision_authority(
        cls,
        *,
        platform_lock_root: str,
        storage_identity_token: str,
        runtime_root: str,
        format_revision: str = LOCK_AUTHORITY_FORMAT_REVISION,
        timeout_seconds: float = _DEFAULT_MAINTENANCE_TIMEOUT_SECONDS,
    ) -> LockMaintenanceProofV1:
        """Create or validate one authority and return final physical proof."""

        _require_posix()
        deadline = _deadline_from_timeout(timeout_seconds)
        durability = _AuthorityDirectoryDurability()
        authority_fd = cls._open_authority_dir(
            platform_lock_root,
            storage_identity_token,
            create=True,
            durability=durability,
        )
        try:
            authority_path = os.path.join(os.path.abspath(platform_lock_root), storage_identity_token)
            provision_fd = cls._open_provision_lock(authority_fd)
            provision_locked = False
            try:
                _flock(provision_fd, fcntl.LOCK_EX, deadline=deadline)
                provision_locked = True
                try:
                    anchor_fd = cls._open_anchor(authority_fd, writable=True)
                except LockedRegularFileError as exc:
                    if exc.code != "lock_authority_missing":
                        raise
                    proof = cls._initial_provision(
                        authority_fd,
                        storage_identity_token,
                        runtime_root,
                        format_revision,
                        durability,
                        authority_path,
                        provision_fd,
                        deadline=deadline,
                    )
                else:
                    try:
                        _flock(anchor_fd, fcntl.LOCK_EX, deadline=deadline)
                        binding = _binding_from_fd(anchor_fd)
                        cls._validate_binding(
                            binding,
                            storage_identity_token,
                            runtime_root,
                            format_revision,
                        )
                        cls._verify_authority_binding(
                            authority_fd,
                            anchor_fd,
                            binding,
                            runtime_root,
                            root_identity_mismatch_code="lock_anchor_binding_mismatch",
                        )
                        cls._initialize_provision_lock(
                            authority_fd,
                            provision_fd,
                            durability=durability,
                            authority_path=authority_path,
                        )
                        validation = cls._verify_authority_binding(
                            authority_fd,
                            anchor_fd,
                            binding,
                            runtime_root,
                            root_identity_mismatch_code="lock_anchor_binding_mismatch",
                        )
                        proof = cls._build_maintenance_proof(
                            operation="provision_authority",
                            verdict="already_present",
                            binding=binding,
                            validation=validation,
                            created_directories=durability.created_directories,
                            completed_fsync_order=durability.completed_fsync_order,
                        )
                    finally:
                        fcntl.flock(anchor_fd, fcntl.LOCK_UN)
                        os.close(anchor_fd)
                cls._verify_provision_lock(authority_fd, provision_fd)
                return proof
            except LockedRegularFileError as exc:
                if durability.boundary_crossed and exc.code != "post_fsync_authority_reconciliation_required":
                    raise _post_fsync_reconciliation(
                        "authority provision requires reconciliation after a durability boundary",
                        exc,
                        **durability.reconciliation_details(),
                    ) from exc
                raise
            finally:
                if provision_locked:
                    fcntl.flock(provision_fd, fcntl.LOCK_UN)
                os.close(provision_fd)
        finally:
            os.close(authority_fd)
            durability.close()

    @classmethod
    def enroll_stream_lock_keys(
        cls,
        *,
        platform_lock_root: str,
        storage_identity_token: str,
        runtime_root: str,
        logical_paths: Iterable[str],
        format_revision: str = LOCK_AUTHORITY_FORMAT_REVISION,
        timeout_seconds: float = _DEFAULT_MAINTENANCE_TIMEOUT_SECONDS,
    ) -> LockMaintenanceProofV1:
        """Enroll canonical keys and return proof from post-fsync validation."""

        _require_posix()
        deadline = _deadline_from_timeout(timeout_seconds)
        canonical_paths = {_logical_path(path)[0] for path in logical_paths}
        canonical = tuple(
            sorted(
                ((path, _key(storage_identity_token, path)) for path in canonical_paths),
                key=lambda item: (item[1], item[0]),
            )
        )
        authority_fd = cls._open_authority_dir(platform_lock_root, storage_identity_token, create=False)
        try:
            anchor_fd = cls._open_anchor(authority_fd, writable=True)
            try:
                _flock(anchor_fd, fcntl.LOCK_EX, deadline=deadline)
                binding = _binding_from_fd(anchor_fd)
                cls._validate_binding(binding, storage_identity_token, runtime_root, format_revision)
                cls._verify_authority_binding(authority_fd, anchor_fd, binding, runtime_root)
                realm_fd = cls._open_realm(authority_fd)
                try:
                    if _identity(realm_fd) != (binding.realm_device, binding.realm_inode):
                        raise _fail("lock_realm_binding_mismatch", "realm differs from anchor binding")
                    created_any = False
                    lock_key_proofs: list[LockKeyMaintenanceProofV1] = []
                    for path, name in canonical:
                        try:
                            fd = os.open(name, _flags(writable=True) | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=realm_fd)
                            verdict: Literal["created", "already_present"] = "created"
                            created_any = True
                        except FileExistsError:
                            verdict = "already_present"
                            try:
                                fd = os.open(name, _flags(writable=True), dir_fd=realm_fd)
                            except OSError as exc:
                                raise _fail(
                                    "stream_lock_invalid",
                                    "existing stream lock key could not be opened safely",
                                    errno=exc.errno,
                                    name=name,
                                ) from exc
                        except OSError as exc:
                            raise _fail(
                                "stream_lock_invalid",
                                "stream lock key could not be created safely",
                                errno=exc.errno,
                                name=name,
                            ) from exc
                        try:
                            key_identity = LockFileIdentityV1(*_regular(fd, code="stream_lock_invalid", name=name))
                            _verify_directory_entry(
                                realm_fd,
                                name,
                                fd,
                                code="stream_lock_invalid",
                                regular=True,
                            )
                        finally:
                            os.close(fd)
                        lock_key_proofs.append(
                            LockKeyMaintenanceProofV1(
                                logical_path=path,
                                lock_key=name,
                                verdict=verdict,
                                identity=key_identity,
                            )
                        )
                    if created_any:
                        try:
                            os.fsync(realm_fd)
                        except OSError as exc:
                            raise _fail(
                                "post_fsync_authority_reconciliation_required",
                                "realm fsync outcome is ambiguous",
                                cause_code="realm_fsync_failed",
                                cause_details={"errno": exc.errno},
                            ) from exc
                    try:
                        validation = cls._verify_authority_binding(
                            authority_fd,
                            anchor_fd,
                            binding,
                            runtime_root,
                        )
                        if LockFileIdentityV1(*_identity(realm_fd)) != validation.realm_identity:
                            raise _fail("lock_realm_binding_mismatch", "held realm identity changed")
                        for key_proof in lock_key_proofs:
                            cls._verify_lock_key(realm_fd, key_proof.lock_key, key_proof.identity)
                    except LockedRegularFileError as exc:
                        if created_any:
                            raise _post_fsync_reconciliation(
                                "lock authority drifted after durable stream enrollment",
                                exc,
                            ) from exc
                        raise
                    return cls._build_maintenance_proof(
                        operation="enroll_stream_lock_keys",
                        verdict="created" if created_any else "already_present",
                        binding=binding,
                        validation=validation,
                        lock_keys=tuple(lock_key_proofs),
                    )
                finally:
                    os.close(realm_fd)
            finally:
                fcntl.flock(anchor_fd, fcntl.LOCK_UN)
                os.close(anchor_fd)
        finally:
            os.close(authority_fd)

    @classmethod
    def acquire(
        cls,
        *,
        runtime_root: str,
        storage_identity_token: str,
        logical_paths: tuple[str, ...] | list[str],
        platform_lock_root: str | None = None,
        timeout_seconds: float = 2.0,
        format_revision: str = LOCK_AUTHORITY_FORMAT_REVISION,
    ) -> LockedRegularFileSetV1:
        """Acquire only a previously provisioned authority and enrolled stream locks."""

        instance = cls(
            os.path.abspath(runtime_root),
            str(storage_identity_token),
            tuple(logical_paths),
            platform_lock_root or default_platform_lock_root(),
            float(timeout_seconds),
            format_revision,
        )
        instance._acquire()
        return instance

    def __enter__(self) -> LockedRegularFileSetV1:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def lease(self, logical_path: str) -> StreamLeaseV1:
        canonical, _ = _logical_path(logical_path)
        with self._operation():
            try:
                return self._leases[canonical]
            except KeyError as exc:
                raise KeyError(f"logical path was not leased: {logical_path!r}") from exc

    def close(self) -> None:
        """Detach descriptors once, then close them after all I/O has quiesced."""

        with self._mutex:
            if self._state == "CLOSED":
                return
            if self._state == "CLOSING":
                close_complete = self._close_complete
                wait_for_close = True
            else:
                self._state = "CLOSING"
                wait_for_close = False
                leases = tuple(self._leases.values())
                lease_reconciliation = tuple(
                    evidence for lease in leases if (evidence := lease._release_reconciliation_details()) is not None
                )
                lease_fds = tuple(lease._detach_descriptors() for lease in leases)
                locks = tuple(self._lock_fds)
                root_fd, realm_fd, anchor_fd = self._root_fd, self._realm_fd, self._anchor_fd
                self._leases.clear()
                self._lock_fds.clear()
                self._root_fd = self._realm_fd = self._anchor_fd = None
                self._root_identity = None
                self._binding = None
        if wait_for_close:
            close_complete.wait()
            return
        errors: list[OSError] = []
        try:
            for descriptors in reversed(lease_fds):
                for fd in descriptors:
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError as exc:
                            errors.append(exc)
            for fd in reversed(locks):
                try:
                    assert fcntl is not None
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError as exc:
                    errors.append(exc)
            for fd in (root_fd, realm_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError as exc:
                        errors.append(exc)
            if anchor_fd is not None:
                try:
                    assert fcntl is not None
                    fcntl.flock(anchor_fd, fcntl.LOCK_UN)
                    os.close(anchor_fd)
                except OSError as exc:
                    errors.append(exc)
        finally:
            with self._mutex:
                self._state = "CLOSED"
                self._close_complete.set()
        if errors:
            cause = _fail(
                "stream_lease_close_failed",
                "lease close encountered descriptor cleanup failures",
                count=len(errors),
                errnos=[error.errno for error in errors],
            )
            if lease_reconciliation:
                raise _post_fsync_reconciliation(
                    "stream descriptor release requires reconciliation after a durability boundary",
                    cause,
                    stream_durability=list(lease_reconciliation),
                )
            raise cause

    def _operation(self) -> _OperationLock:
        self._mutex.acquire()
        if self._state == "CLOSING":
            self._mutex.release()
            raise _fail("stream_lease_closing", "stream lease set is closing")
        if self._state != "ACTIVE":
            self._mutex.release()
            raise _fail("stream_lease_closed", "stream lease set is closed")
        return _OperationLock(self._mutex)

    def _acquire(self) -> None:
        _require_posix()
        deadline = _deadline_from_timeout(self.timeout_seconds)
        paths = [_logical_path(path) for path in self.logical_paths]
        if len({path for path, _ in paths}) != len(paths):
            raise ValueError("locked stream paths must be distinct")
        authority_fd = self._open_authority_dir(self.platform_lock_root, self.storage_identity_token, create=False)
        try:
            self._anchor_fd = self._open_anchor(authority_fd, writable=False)
            self._acquire_lock(
                self._anchor_fd,
                fcntl.LOCK_SH,
                deadline=deadline,
            )
            self._binding = _binding_from_fd(self._anchor_fd)
            self._validate_binding(self._binding, self.storage_identity_token, self.runtime_root, self.format_revision)
            self._realm_fd = self._open_realm(authority_fd)
            if _identity(self._realm_fd) != (self._binding.realm_device, self._binding.realm_inode):
                raise _fail("lock_realm_binding_mismatch", "realm differs from anchor binding")
            self._root_fd = _open_absolute_directory(self.runtime_root, create=False)
            self._root_identity = _identity(self._root_fd)
            if self._root_identity != (self._binding.root_device, self._binding.root_inode):
                raise _fail("stream_identity_drift", "runtime root differs from enrolled authority identity")
            for path, _ in sorted(paths, key=lambda item: _key(self.storage_identity_token, item[0])):
                self._validate_authority()
                name = _key(self.storage_identity_token, path)
                try:
                    fd = os.open(name, _flags(writable=True), dir_fd=self._realm_fd)
                except FileNotFoundError:
                    raise _fail("stream_lock_missing", "stream lock key is not enrolled", logical_path=path) from None
                except OSError as exc:
                    raise _fail("stream_lock_invalid", "stream lock key is unsafe", errno=exc.errno) from exc
                _regular(fd, code="stream_lock_invalid", name=name)
                self._acquire_lock(fd, fcntl.LOCK_EX, deadline=deadline)
                self._lock_fds.append(fd)
            for path, parts in paths:
                self._leases[path] = StreamLeaseV1(self, path, parts)
            self._validate_authority()
        except (LockedRegularFileError, OSError, ValueError):
            self.close()
            raise
        finally:
            os.close(authority_fd)

    def _validate_authority(self) -> None:
        assert self._anchor_fd is not None and self._realm_fd is not None and self._binding is not None
        authority_fd = self._open_authority_dir(self.platform_lock_root, self.storage_identity_token, create=False)
        try:
            self._verify_authority_binding(
                authority_fd,
                self._anchor_fd,
                self._binding,
                self.runtime_root,
            )
            if _identity(self._realm_fd) != (self._binding.realm_device, self._binding.realm_inode):
                raise _fail("lock_realm_binding_mismatch", "held realm identity changed")
            anchor_fd = self._open_anchor(authority_fd, writable=False)
            try:
                if _identity(anchor_fd) != _identity(self._anchor_fd):
                    raise _fail("lock_anchor_binding_mismatch", "anchor identity changed")
                self._verify_authority_binding(
                    authority_fd,
                    anchor_fd,
                    self._binding,
                    self.runtime_root,
                )
            finally:
                os.close(anchor_fd)
        finally:
            os.close(authority_fd)

    def _root_fd_required(self) -> int:
        if self._root_fd is None:
            raise _fail("stream_lease_closed", "runtime root descriptor is detached")
        return self._root_fd

    def _root_identity_required(self) -> tuple[int, int]:
        if self._root_identity is None:
            raise _fail("stream_lease_closed", "runtime root identity is detached")
        return self._root_identity

    @staticmethod
    def _open_authority_dir(
        platform_root: str,
        token: str,
        *,
        create: bool,
        durability: _AuthorityDirectoryDurability | None = None,
    ) -> int:
        try:
            canonical_root = os.path.abspath(platform_root)
            root_fd = _open_absolute_directory(canonical_root, create=create, durability=durability)
        except FileNotFoundError:
            raise _fail("lock_authority_missing", "platform lock root is absent") from None
        try:
            created = False
            try:
                authority_fd = os.open(token, _flags(directory=True), dir_fd=root_fd)
            except FileNotFoundError:
                if not create:
                    raise _fail("lock_authority_missing", "storage authority is absent") from None
                try:
                    os.mkdir(token, 0o700, dir_fd=root_fd)
                    created = True
                except FileExistsError:
                    pass
                authority_fd = os.open(token, _flags(directory=True), dir_fd=root_fd)
            authority_path = os.path.join(canonical_root, token)
            _directory(authority_fd, name=authority_path)
            _verify_directory_entry(
                root_fd,
                token,
                authority_fd,
                code="lock_authority_missing",
                regular=False,
            )
            if created and durability is not None:
                durability.record_created(
                    path=authority_path,
                    fd=authority_fd,
                    parent_path=canonical_root,
                    parent_fd=root_fd,
                )
            return authority_fd
        finally:
            os.close(root_fd)

    @staticmethod
    def _acquire_lock(
        fd: int,
        operation: int,
        *,
        deadline: float | None = None,
    ) -> None:
        """Acquire one advisory lock through the injectable lock seam."""

        _flock(fd, operation, deadline=deadline)

    @staticmethod
    def _open_anchor(authority_fd: int, *, writable: bool) -> int:
        try:
            fd = os.open("anchor.lock", _flags(writable=writable), dir_fd=authority_fd)
        except FileNotFoundError:
            raise _fail("lock_authority_missing", "authority anchor is absent") from None
        except OSError as exc:
            raise _fail("lock_anchor_invalid", "authority anchor could not be opened safely", errno=exc.errno) from exc
        try:
            _regular(fd, code="lock_anchor_invalid", name="anchor.lock")
            _verify_directory_entry(
                authority_fd,
                "anchor.lock",
                fd,
                code="lock_anchor_invalid",
                regular=True,
            )
        except LockedRegularFileError:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _open_provision_lock(authority_fd: int) -> int:
        """Open or atomically create the stable cross-process provision mutex."""

        try:
            fd = os.open(
                "provision.lock",
                _flags(writable=True) | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=authority_fd,
            )
        except FileExistsError:
            try:
                fd = os.open("provision.lock", _flags(writable=True), dir_fd=authority_fd)
            except OSError as exc:
                raise _fail(
                    "lock_provision_invalid",
                    "authority provision lock could not be opened safely",
                    errno=exc.errno,
                ) from exc
        except OSError as exc:
            raise _fail(
                "lock_provision_invalid",
                "authority provision lock could not be created safely",
                errno=exc.errno,
            ) from exc
        try:
            _regular(fd, code="lock_provision_invalid", name="provision.lock")
            _verify_directory_entry(
                authority_fd,
                "provision.lock",
                fd,
                code="lock_provision_invalid",
                regular=True,
            )
        except LockedRegularFileError:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _verify_provision_lock(authority_fd: int, provision_fd: int) -> None:
        """Prove the held provision mutex still names its stable initialized entry."""

        _regular(provision_fd, code="lock_provision_invalid", name="provision.lock")
        _verify_directory_entry(
            authority_fd,
            "provision.lock",
            provision_fd,
            code="lock_provision_invalid",
            regular=True,
        )
        try:
            os.lseek(provision_fd, 0, os.SEEK_SET)
            marker = os.read(provision_fd, len(_PROVISION_LOCK_MARKER) + 1)
        except OSError as exc:
            raise _fail(
                "lock_provision_invalid",
                "authority provision lock marker could not be read",
                errno=exc.errno,
            ) from exc
        if marker != _PROVISION_LOCK_MARKER:
            raise _fail("lock_provision_invalid", "authority provision lock marker is malformed")

    @classmethod
    def _initialize_provision_lock(
        cls,
        authority_fd: int,
        provision_fd: int,
        *,
        durability: _AuthorityDirectoryDurability,
        authority_path: str,
    ) -> None:
        """Initialize an empty provision mutex while holding its exclusive flock."""

        _regular(provision_fd, code="lock_provision_invalid", name="provision.lock")
        _verify_directory_entry(
            authority_fd,
            "provision.lock",
            provision_fd,
            code="lock_provision_invalid",
            regular=True,
        )
        try:
            os.lseek(provision_fd, 0, os.SEEK_SET)
            current = os.read(provision_fd, len(_PROVISION_LOCK_MARKER) + 1)
        except OSError as exc:
            raise _fail(
                "lock_provision_invalid",
                "authority provision lock marker could not be read",
                errno=exc.errno,
            ) from exc
        if current == _PROVISION_LOCK_MARKER:
            return
        if current:
            raise _fail("lock_provision_invalid", "authority provision lock marker is malformed")
        try:
            os.lseek(provision_fd, 0, os.SEEK_SET)
            payload = memoryview(_PROVISION_LOCK_MARKER)
            while payload:
                written = os.write(provision_fd, payload)
                if written <= 0:
                    raise OSError(errno.EIO, "short authority provision lock write")
                payload = payload[written:]
            os.ftruncate(provision_fd, len(_PROVISION_LOCK_MARKER))
            durability.begin_file_fsync()
            try:
                os.fsync(provision_fd)
            except OSError as exc:
                raise _fail(
                    "provision_lock_fsync_failed",
                    "authority provision lock fsync outcome is ambiguous",
                    errno=exc.errno,
                ) from exc
            durability.record_file_fsync(os.path.join(authority_path, "provision.lock"))
            if authority_path not in durability.created_directories:
                durability.fsync_existing_directory(authority_fd, path=authority_path)
        except LockedRegularFileError:
            raise
        except OSError as exc:
            raise _fail(
                "lock_provision_invalid",
                "authority provision lock marker could not be initialized",
                errno=exc.errno,
            ) from exc
        cls._verify_provision_lock(authority_fd, provision_fd)

    @staticmethod
    def _open_realm(authority_fd: int) -> int:
        try:
            fd = os.open("realm", _flags(directory=True), dir_fd=authority_fd)
        except FileNotFoundError:
            raise _fail("lock_realm_missing", "bound authority realm is absent") from None
        except OSError as exc:
            raise _fail(
                "lock_realm_binding_mismatch", "bound authority realm could not be opened safely", errno=exc.errno
            ) from exc
        try:
            _directory(fd, name="realm")
            _verify_directory_entry(
                authority_fd,
                "realm",
                fd,
                code="lock_realm_binding_mismatch",
                regular=False,
            )
        except LockedRegularFileError:
            os.close(fd)
            raise
        return fd

    @classmethod
    def _verify_authority_binding(
        cls,
        authority_fd: int,
        anchor_fd: int,
        binding: LockAuthorityBindingV1,
        runtime_root: str,
        *,
        root_identity_mismatch_code: Literal[
            "lock_anchor_binding_mismatch", "stream_identity_drift"
        ] = "stream_identity_drift",
    ) -> _ValidatedAuthority:
        """Validate the full physical authority and current runtime-root binding."""

        anchor_identity = _regular(anchor_fd, code="lock_anchor_invalid", name="anchor.lock")
        _verify_directory_entry(
            authority_fd,
            "anchor.lock",
            anchor_fd,
            code="lock_anchor_invalid",
            regular=True,
        )
        if _binding_from_fd(anchor_fd) != binding:
            raise _fail("lock_anchor_binding_mismatch", "anchor binding changed")
        realm_fd = cls._open_realm(authority_fd)
        try:
            realm_identity = _identity(realm_fd)
            if realm_identity != (binding.realm_device, binding.realm_inode):
                raise _fail("lock_realm_binding_mismatch", "realm differs from anchor binding")
        finally:
            os.close(realm_fd)
        root_identity = cls._verify_runtime_root(
            binding,
            runtime_root,
            identity_mismatch_code=root_identity_mismatch_code,
        )
        return _ValidatedAuthority(
            root_identity=LockFileIdentityV1(*root_identity),
            anchor_identity=LockFileIdentityV1(*anchor_identity),
            realm_identity=LockFileIdentityV1(*realm_identity),
        )

    @staticmethod
    def _verify_runtime_root(
        binding: LockAuthorityBindingV1,
        runtime_root: str,
        *,
        identity_mismatch_code: Literal[
            "lock_anchor_binding_mismatch", "stream_identity_drift"
        ] = "stream_identity_drift",
    ) -> tuple[int, int]:
        canonical_root = os.path.abspath(runtime_root)
        try:
            root_fd = _open_absolute_directory(canonical_root, create=False)
        except OSError as exc:
            raise _fail(
                "stream_identity_drift",
                "bound runtime root could not be reopened safely",
                errno=exc.errno,
                runtime_root=canonical_root,
            ) from exc
        try:
            actual = _identity(root_fd)
        finally:
            os.close(root_fd)
        expected = binding.root_device, binding.root_inode
        if actual != expected:
            raise _fail(
                identity_mismatch_code,
                "runtime root identity differs from authority binding",
                actual_device=actual[0],
                actual_inode=actual[1],
                expected_device=expected[0],
                expected_inode=expected[1],
                runtime_root=canonical_root,
            )
        return actual

    @staticmethod
    def _build_maintenance_proof(
        *,
        operation: Literal["provision_authority", "enroll_stream_lock_keys"],
        verdict: Literal["created", "already_present"],
        binding: LockAuthorityBindingV1,
        validation: _ValidatedAuthority,
        lock_keys: tuple[LockKeyMaintenanceProofV1, ...] = (),
        created_directories: tuple[str, ...] = (),
        completed_fsync_order: tuple[str, ...] = (),
    ) -> LockMaintenanceProofV1:
        return LockMaintenanceProofV1(
            operation=operation,
            verdict=verdict,
            storage_identity=binding.storage_identity,
            runtime_root=binding.runtime_root,
            format_revision=binding.format_revision,
            root_identity=validation.root_identity,
            anchor_identity=validation.anchor_identity,
            realm_identity=validation.realm_identity,
            lock_keys=lock_keys,
            final_validation=True,
            created_directories=created_directories,
            completed_fsync_order=completed_fsync_order,
        )

    @staticmethod
    def _verify_lock_key(realm_fd: int, name: str, expected_identity: LockFileIdentityV1) -> None:
        """Reopen a lock key and prove it is the pre-fsync physical object."""

        try:
            fd = os.open(name, _flags(writable=True), dir_fd=realm_fd)
        except OSError as exc:
            raise _fail(
                "stream_lock_invalid",
                "stream lock key could not be reopened safely",
                errno=exc.errno,
                name=name,
            ) from exc
        try:
            actual_identity = LockFileIdentityV1(*_regular(fd, code="stream_lock_invalid", name=name))
            _verify_directory_entry(
                realm_fd,
                name,
                fd,
                code="stream_lock_invalid",
                regular=True,
            )
            if actual_identity != expected_identity:
                raise _fail(
                    "stream_lock_invalid",
                    "stream lock key identity changed after verification",
                    name=name,
                    expected_device=expected_identity.device,
                    expected_inode=expected_identity.inode,
                    actual_device=actual_identity.device,
                    actual_inode=actual_identity.inode,
                )
        finally:
            os.close(fd)

    @classmethod
    def _initial_provision(
        cls,
        authority_fd: int,
        token: str,
        runtime_root: str,
        revision: str,
        durability: _AuthorityDirectoryDurability,
        authority_path: str,
        provision_fd: int,
        *,
        deadline: float,
    ) -> LockMaintenanceProofV1:
        """Create an authority once while the stable provision mutex is held."""

        root_fd = _open_absolute_directory(os.path.abspath(runtime_root), create=True, durability=durability)
        try:
            try:
                os.mkdir("realm", 0o700, dir_fd=authority_fd)
                realm_created = True
            except FileExistsError:
                realm_fd = cls._open_realm(authority_fd)
                os.close(realm_fd)
                raise _fail(
                    "lock_authority_provision_conflict",
                    "authority realm exists without a durable anchor while provision lock is held",
                ) from None
            realm_fd = cls._open_realm(authority_fd)
            try:
                if realm_created:
                    durability.record_created(
                        path=os.path.join(authority_path, "realm"),
                        fd=realm_fd,
                        parent_path=authority_path,
                        parent_fd=authority_fd,
                    )
                binding = LockAuthorityBindingV1(
                    token,
                    os.path.abspath(runtime_root),
                    *_identity(root_fd),
                    *_identity(realm_fd),
                    revision,
                )
                try:
                    fd = os.open(
                        "anchor.lock",
                        _flags(writable=True) | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=authority_fd,
                    )
                except FileExistsError:
                    raise _fail(
                        "lock_authority_provision_conflict",
                        "authority anchor appeared while provision lock was held",
                    ) from None
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise _fail(
                            "lock_anchor_invalid",
                            "authority anchor could not be created safely",
                            errno=exc.errno,
                        ) from exc
                    raise
                try:
                    _flock(fd, fcntl.LOCK_EX, deadline=deadline)
                    payload = memoryview(_canonical_bytes(binding.to_record()))
                    while payload:
                        written = os.write(fd, payload)
                        if written <= 0:
                            raise OSError(errno.EIO, "short authority anchor write")
                        payload = payload[written:]
                    durability.begin_file_fsync()
                    try:
                        os.fsync(fd)
                    except OSError as exc:
                        raise _fail(
                            "anchor_fsync_failed",
                            "authority anchor fsync outcome is ambiguous",
                            errno=exc.errno,
                        ) from exc
                    durability.record_file_fsync(os.path.join(authority_path, "anchor.lock"))
                    durability.fsync_created_directories()
                    validation = cls._verify_authority_binding(
                        authority_fd,
                        fd,
                        binding,
                        runtime_root,
                    )
                    cls._initialize_provision_lock(
                        authority_fd,
                        provision_fd,
                        durability=durability,
                        authority_path=authority_path,
                    )
                    validation = cls._verify_authority_binding(
                        authority_fd,
                        fd,
                        binding,
                        runtime_root,
                    )
                    return cls._build_maintenance_proof(
                        operation="provision_authority",
                        verdict="created",
                        binding=binding,
                        validation=validation,
                        created_directories=durability.created_directories,
                        completed_fsync_order=durability.completed_fsync_order,
                    )
                except LockedRegularFileError as exc:
                    if durability.boundary_crossed and exc.code != "post_fsync_authority_reconciliation_required":
                        raise _post_fsync_reconciliation(
                            "authority provision requires reconciliation after a durability boundary",
                            exc,
                            **durability.reconciliation_details(),
                        ) from exc
                    raise
                finally:
                    os.close(fd)
            finally:
                os.close(realm_fd)
        except LockedRegularFileError as exc:
            if durability.boundary_crossed and exc.code != "post_fsync_authority_reconciliation_required":
                raise _post_fsync_reconciliation(
                    "authority provision requires reconciliation after a durability boundary",
                    exc,
                    **durability.reconciliation_details(),
                ) from exc
            raise
        except OSError as exc:
            cause = _fail(
                "lock_authority_provision_conflict",
                "authority initial provision failed",
                errno=exc.errno,
            )
            if durability.boundary_crossed:
                raise _post_fsync_reconciliation(
                    "authority provision requires reconciliation after a durability boundary",
                    cause,
                    **durability.reconciliation_details(),
                ) from exc
            raise _fail(
                "lock_authority_provision_conflict",
                "authority initial provision failed",
                errno=exc.errno,
            ) from exc
        finally:
            os.close(root_fd)

    @staticmethod
    def _validate_binding(binding: LockAuthorityBindingV1, token: str, root: str, revision: str) -> None:
        if (
            binding.storage_identity != token
            or binding.runtime_root != os.path.abspath(root)
            or binding.format_revision != revision
        ):
            raise _fail("lock_anchor_binding_mismatch", "anchor binding does not match acquire request")


class _OperationLock:
    def __init__(self, lock: threading.RLock) -> None:
        self._lock = lock

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._lock.release()


__all__ = [
    "LOCK_AUTHORITY_FORMAT_REVISION",
    "LockAuthorityBindingV1",
    "LockFileIdentityV1",
    "LockKeyMaintenanceProofV1",
    "LockMaintenanceProofV1",
    "LockedRegularFileError",
    "LockedRegularFileSetV1",
    "StreamLeaseV1",
    "default_platform_lock_root",
]
