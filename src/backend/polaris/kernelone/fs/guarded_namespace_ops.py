"""Descriptor-bound operations over one logical namespace root.

This module contains no Polaris business semantics.  It expresses *how* a
logical namespace leaf is reached and mutated once a caller has already
selected the namespace root:

* every path component is retained as a no-follow directory descriptor, so a
  symbolic link in the chain is refused instead of followed;
* the leaf is opened without following a link, so a linked leaf cannot reach
  a file outside the namespace;
* a multiply-linked leaf is refused, because writing through such a
  descriptor would mutate every alias of that inode;
* the write path is descriptor-relative, so the effect lands on the entry
  the retained parent actually holds.

The primitives reuse the existing guarded snapshot machinery
(``_retain_directory``, ``_open_descriptor``, ``_file_fingerprint``,
``_verify_leaf_entry``, ``_write_all``) and its error *codes*, so that this
boundary does not introduce a second filesystem implementation or a second
error dialect.

Refusal semantics
-----------------
``NamespaceBoundaryError`` is the single refusal type of this boundary.  It
derives from both ``OSError`` and ``RuntimeError`` so that callers which
treat "the filesystem refused" (``OSError``) and callers which treat "the
filesystem could not do this" (``RuntimeError``) keep their current
behaviour, while the concrete type and its ``code`` stay distinguishable.

A refusal happens *before* any physical effect.  A missing component of the
namespace chain cannot be a symbolic link, so a link is only ever observed
in a chain that already existed; the refusal therefore leaves the filesystem
byte-identical.

Unknown effect
--------------
``NamespaceOperationEffectUnknownError`` is raised when the effect may
already have happened.  It is never reported as a success and the operation
is never replayed.

Threat boundary
---------------
These primitives bind the *effect* to the retained namespace chain.  They do
not provide absolute OS-level confinement: a hostile actor that can rename a
retained directory out of the namespace between validation and the effect can
still land the effect outside the namespace.  Such a case is reported as an
explicit unknown effect, never as a success and never as a prevented escape.

Platform support
----------------
Everything here requires POSIX descriptor-relative operations.  When the
platform cannot provide them the module refuses explicitly with
``NamespaceOperationUnsupportedError`` instead of silently degrading to a
path-based operation.
"""

from __future__ import annotations

import errno
import os
import stat
import uuid
from contextlib import ExitStack, contextmanager
from typing import Final, Iterator

from .guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    _DirectoryWitness,
    _file_fingerprint,
    _open_descriptor,
    _retain_directory,
    _retain_or_mkdir_directory,
    _verify_directory_witness,
    _verify_leaf_entry,
    _write_all,
)
from .types import FileMetadataV1, NamespaceBindingV1, NamespaceCreateReceiptV1

_READ_CHUNK_BYTES: Final[int] = 64 * 1024
_LEAF_CREATE_MODE: Final[int] = 0o600
_DIR_CREATE_MODE: Final[int] = 0o700
_STAGE_NAME_PREFIX: Final[str] = ".polaris-namespace-stage"
_STAGE_NAME_ATTEMPTS: Final[int] = 8


class NamespaceBoundaryError(OSError, RuntimeError):
    """A namespace-bound operation was refused before any physical effect.

    The ``code`` values are the existing guarded snapshot codes, so the
    refusal reason is reported with the vocabulary the platform already uses:
    ``guarded_snapshot_symlink_rejected``,
    ``guarded_snapshot_hard_link_rejected``, ``guarded_snapshot_missing``,
    ``guarded_snapshot_not_regular`` and
    ``guarded_snapshot_identity_drift``.
    """

    def __init__(self, message: str, *, code: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class NamespaceOperationUnsupportedError(NamespaceBoundaryError):
    """The bound platform cannot perform this operation safely.

    The refusal happens *before* any effect, so an unsupported platform
    never produces a partially applied write.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="guarded_snapshot_capability_unavailable")


class NamespaceOperationEffectUnknownError(OSError, RuntimeError):
    """The physical effect may already have happened; the state is unknown.

    Raised instead of a success receipt whenever the effect state cannot be
    proven after the fact.  The write may already be committed, so the caller
    must not retry automatically.
    """

    def __init__(self, binding: NamespaceBindingV1, phase: str) -> None:
        super().__init__(f"namespace operation effect unknown: {binding.relative_path!r} phase={phase}")
        self.binding = binding
        self.phase = phase
        self.original_effect = "unknown"


@contextmanager
def _boundary_errors() -> Iterator[None]:
    """Translate the reused guarded primitives into this boundary's vocabulary."""
    try:
        yield
    except (NamespaceBoundaryError, NamespaceOperationEffectUnknownError):
        raise
    except GuardedRegularFileSnapshotError as exc:
        raise NamespaceBoundaryError(str(exc), code=exc.code, details=exc.details) from exc


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validated_namespace_root(root: str) -> tuple[str, tuple[str, ...]]:
    """Validate a bound namespace root.

    Unlike the bounded snapshot root validator this does not require NFC
    normalization: a workspace directory may legitimately carry a decomposed
    name and the logical API already accepts it.  Imposing the snapshot
    API's stricter rule here would be a legal-behaviour regression.
    """
    if not isinstance(root, str) or not root or "\x00" in root:
        raise ValueError("namespace root must be an absolute canonical text path")
    if not os.path.isabs(root):
        raise ValueError("namespace root must be an absolute canonical text path")
    if root.startswith("//"):
        raise ValueError("namespace root must be an absolute canonical text path")
    if os.path.normpath(root) != root:
        raise ValueError("namespace root must be an absolute canonical text path")
    if any(part in {".", ".."} for part in root.split("/")):
        raise ValueError("namespace root must not contain '.' or '..' components")
    return root, tuple(part for part in root.split("/") if part)


def _validated_namespace_relative_parts(relative_path: str) -> tuple[str, ...]:
    """Validate a logical relative path below a namespace root.

    Mirrors the lexical contract of ``normalize_logical_rel_path``: no
    absolute path, no ``..``, no empty component.  The NFC rule and the
    1024-byte cap of the bounded snapshot API are deliberately *not* imposed,
    because the current logical API accepts both and imposing them would be a
    legal-behaviour regression.
    """
    if not isinstance(relative_path, str):
        raise ValueError("relative_path must be a non-empty string")
    if not relative_path or "\x00" in relative_path:
        raise ValueError("relative_path must be a non-empty string")
    if relative_path.startswith("/") or "\\" in relative_path:
        raise ValueError("relative_path must be a canonical relative slash path")
    if ":" in relative_path.split("/", 1)[0]:
        raise ValueError("relative_path must be a canonical relative slash path")
    parts = tuple(relative_path.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("relative_path must not contain empty, '.' or '..' components")
    return parts


def _require_descriptor_capabilities() -> None:
    """Refuse when the platform cannot issue descriptor-relative effects."""
    required_flags = ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_DIRECTORY")
    if os.name == "nt" or not all(hasattr(os, name) for name in required_flags):
        raise NamespaceOperationUnsupportedError(
            "descriptor-safe namespace operations are unavailable on this platform"
        )
    if os.open not in os.supports_dir_fd:
        raise NamespaceOperationUnsupportedError("dir_fd-relative open is unavailable")
    if os.stat not in os.supports_dir_fd:
        raise NamespaceOperationUnsupportedError("dir_fd-relative stat is unavailable")
    if os.unlink not in os.supports_dir_fd:
        raise NamespaceOperationUnsupportedError("dir_fd-relative unlink is unavailable")
    if os.mkdir not in os.supports_dir_fd:
        raise NamespaceOperationUnsupportedError("dir_fd-relative mkdir is unavailable")
    if os.rename not in os.supports_dir_fd:
        # The commit below uses ``os.replace`` with descriptor-relative
        # arguments; the capability probe follows the production
        # ``guarded_atomic_write`` convention and checks ``os.rename``.
        raise NamespaceOperationUnsupportedError("dir_fd-relative rename is unavailable")


# ---------------------------------------------------------------------------
# Descriptor traversal
# ---------------------------------------------------------------------------


def _retain_root(stack: ExitStack, root_parts: tuple[str, ...], *, create: bool) -> list[_DirectoryWitness]:
    """Retain the bound root and every ancestor below it, without following links.

    With ``create`` a missing component is created.  A component that exists
    as a symbolic link is refused: the retained descriptor can only be a real
    directory.
    """
    witnesses: list[_DirectoryWitness] = []
    current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
    witnesses.append(current)
    display = ""
    for component in root_parts:
        display += "/" + component
        if create:
            current = _retain_or_mkdir_directory(
                stack, parent=current, entry_name=component, display_name=display, mode=_DIR_CREATE_MODE
            )
        else:
            current = _retain_directory(stack, parent=current, entry_name=component, display_name=display)
        witnesses.append(current)
    return witnesses


def _verify(witnesses: list[_DirectoryWitness]) -> None:
    for witness in witnesses:
        _verify_directory_witness(witness)


def _walk_namespace(
    stack: ExitStack,
    binding: NamespaceBindingV1,
    *,
    create_missing: bool,
) -> tuple[list[_DirectoryWitness], _DirectoryWitness, _DirectoryWitness]:
    """Retain the bound root, then the parent directory of the bound leaf.

    With ``create_missing`` the missing components are created.  A component
    that exists as a symbolic link is refused by the traversal itself.

    Creating a missing component is safe with respect to links: a component
    that does not exist cannot be a symbolic link, so a link is only ever
    observed in a chain that already existed.  A refusal therefore leaves the
    filesystem byte-identical.
    """
    root_path, root_parts = _validated_namespace_root(binding.namespace_root)
    parts = _validated_namespace_relative_parts(binding.relative_path)
    witnesses = _retain_root(stack, root_parts, create=create_missing)
    root_witness = witnesses[-1]
    current = root_witness
    display = root_path.rstrip("/")
    for component in parts[:-1]:
        display += "/" + component
        if create_missing:
            current = _retain_or_mkdir_directory(
                stack, parent=current, entry_name=component, display_name=display, mode=_DIR_CREATE_MODE
            )
        else:
            current = _retain_directory(stack, parent=current, entry_name=component, display_name=display)
        witnesses.append(current)
    _verify(witnesses)
    return witnesses, current, root_witness


def _entry_stat(parent_fd: int, name: str) -> os.stat_result | None:
    """Observe one entry without following it.  ``None`` when it is absent."""
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise NamespaceBoundaryError(
            "namespace entry could not be observed",
            code="guarded_snapshot_identity_drift",
            details={"name": name, "errno": exc.errno},
        ) from exc


def _leaf_name(binding: NamespaceBindingV1) -> str:
    return binding.relative_path.rsplit("/", 1)[-1]


def _require_single_link_regular(fd: int, *, name: str) -> os.stat_result:
    """Refuse a leaf that is not a regular file with exactly one link.

    Writing through a descriptor of a multiply-linked inode would mutate
    every alias of that inode, including aliases outside the namespace.
    """
    try:
        info = os.fstat(fd)
    except OSError as exc:
        raise NamespaceBoundaryError(
            "namespace leaf could not be verified",
            code="guarded_snapshot_identity_drift",
            details={"name": name, "errno": exc.errno},
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise NamespaceBoundaryError(
            "namespace leaf is not a regular file",
            code="guarded_snapshot_not_regular",
            details={"name": name},
        )
    if info.st_nlink != 1:
        raise NamespaceBoundaryError(
            "namespace leaf has multiple hard links",
            code="guarded_snapshot_hard_link_rejected",
            details={"name": name, "link_count": info.st_nlink},
        )
    return info


def _open_leaf_for_write(stack: ExitStack, parent_fd: int, name: str) -> int:
    """Open the leaf for writing without following a leaf link."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(name, flags, _LEAF_CREATE_MODE, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise NamespaceBoundaryError(
                "symbolic links are not allowed",
                code="guarded_snapshot_symlink_rejected",
                details={"name": name},
            ) from exc
        if exc.errno == errno.EISDIR:
            raise NamespaceBoundaryError(
                "namespace leaf is not a regular file",
                code="guarded_snapshot_not_regular",
                details={"name": name},
            ) from exc
        if exc.errno == errno.ENOENT:
            raise NamespaceBoundaryError(
                "namespace path is missing",
                code="guarded_snapshot_missing",
                details={"name": name},
            ) from exc
        raise NamespaceBoundaryError(
            "namespace leaf could not be opened",
            code="guarded_snapshot_open_failed",
            details={"name": name, "errno": exc.errno},
        ) from exc
    stack.callback(os.close, fd)
    return fd


def _read_unbounded(fd: int, *, name: str) -> bytes:
    """Read the whole descriptor without an artificial size cap.

    The bounded snapshot API caps reads on purpose; the generic logical read
    has no such contract today, so it must not silently gain one.
    """
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
        except OSError as exc:
            raise NamespaceBoundaryError(
                "namespace leaf could not be read",
                code="guarded_snapshot_open_failed",
                details={"name": name, "errno": exc.errno},
            ) from exc
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _fsync_dir(fd: int) -> None:
    """Complete the requested durability stage or let the owner report failure."""
    os.fsync(fd)


# ---------------------------------------------------------------------------
# Effect operations (adapter-owned)
# ---------------------------------------------------------------------------


def guarded_namespace_stat(binding: NamespaceBindingV1) -> FileMetadataV1 | None:
    """Observe the bound leaf without following it.  ``None`` when absent."""
    _require_descriptor_capabilities()
    with _boundary_errors(), ExitStack() as stack:
        witnesses, parent, root_witness = _walk_namespace(stack, binding, create_missing=False)
        leaf = _leaf_name(binding)
        entry = _entry_stat(parent.fd, leaf)
        if entry is None:
            _verify(witnesses)
            return None
        observed = FileMetadataV1(
            relative_path=binding.relative_path,
            device=entry.st_dev,
            inode=entry.st_ino,
            mode=entry.st_mode,
            nlink=entry.st_nlink,
            size=entry.st_size,
            mtime_ns=entry.st_mtime_ns,
            ctime_ns=entry.st_ctime_ns,
            root_device=root_witness.device,
            root_inode=root_witness.inode,
        )
        _verify(witnesses)
        return observed


def guarded_namespace_read_bytes(binding: NamespaceBindingV1) -> bytes:
    """Read the bound leaf through descriptor-relative, no-follow traversal."""
    _require_descriptor_capabilities()
    with _boundary_errors(), ExitStack() as stack:
        witnesses, parent, _root = _walk_namespace(stack, binding, create_missing=False)
        leaf = _leaf_name(binding)
        try:
            leaf_fd = _open_descriptor(stack, leaf, parent_fd=parent.fd, directory=False)
        except GuardedRegularFileSnapshotError as exc:
            if exc.code == "guarded_snapshot_missing":
                raise FileNotFoundError(binding.physical_path()) from exc
            raise
        before = _file_fingerprint(leaf_fd, name=binding.relative_path)
        _verify_leaf_entry(parent.fd, leaf, before)
        content = _read_unbounded(leaf_fd, name=binding.relative_path)
        after = _file_fingerprint(leaf_fd, name=binding.relative_path, expected=before)
        if len(content) != after.size:
            raise NamespaceBoundaryError(
                "namespace leaf changed during the read",
                code="guarded_snapshot_identity_drift",
                details={"name": binding.relative_path},
            )
        _verify_leaf_entry(parent.fd, leaf, after)
        _verify(witnesses)
        return content


def guarded_namespace_write_bytes(binding: NamespaceBindingV1, payload: bytes) -> int:
    """Replace the bound leaf's content through a retained parent descriptor.

    The leaf is opened without following a link, its identity is verified as
    a single-link regular file *before* any byte is written, and the content
    is written through that descriptor.  A later verification failure reports
    an unknown effect instead of a false success.
    """
    _require_descriptor_capabilities()
    data = bytes(payload)
    with _boundary_errors(), ExitStack() as stack:
        witnesses, parent, _root = _walk_namespace(stack, binding, create_missing=True)
        leaf = _leaf_name(binding)
        fd = _open_leaf_for_write(stack, parent.fd, leaf)
        _require_single_link_regular(fd, name=binding.relative_path)
        phase = "truncate"
        try:
            os.ftruncate(fd, 0)
            phase = "write"
            _write_all(fd, data, name=binding.relative_path)
            phase = "verify"
            info = os.fstat(fd)
            if info.st_size != len(data):
                raise NamespaceOperationEffectUnknownError(binding, phase)
            # The identity revalidation happens *after* the write.  A parent
            # that was moved out of the namespace during the write is
            # detected here and reported as an unknown effect, never as a
            # success.
            _verify(witnesses)
        except NamespaceOperationEffectUnknownError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise NamespaceOperationEffectUnknownError(binding, phase) from exc
        return len(data)


def _open_unique_stage(parent_fd: int) -> tuple[str, int]:
    """Create one unpredictable same-parent stage entry ``O_EXCL``.

    The name carries a ``uuid4`` suffix so two concurrent calls in the same
    parent directory never contend for the same entry.  ``O_EXCL`` still
    guarantees that a pre-existing entry is never reused, and a name that is
    somehow already taken is retried with a fresh name rather than adopted.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    for _attempt in range(_STAGE_NAME_ATTEMPTS):
        stage_name = f"{_STAGE_NAME_PREFIX}.{uuid.uuid4().hex}"
        try:
            fd = os.open(stage_name, flags, _LEAF_CREATE_MODE, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise NamespaceBoundaryError(
                "namespace stage entry could not be created",
                code="guarded_snapshot_open_failed",
                details={"name": stage_name, "errno": exc.errno},
            ) from exc
        return stage_name, fd
    raise NamespaceBoundaryError(
        "namespace stage entry could not be allocated a unique name",
        code="guarded_snapshot_identity_drift",
        details={"prefix": _STAGE_NAME_PREFIX},
    )


def _cleanup_stage(parent_fd: int, stage_name: str, stage_fd: int) -> bool:
    """Remove only the stage entry this call still owns.

    The removal is proved by identity: the entry observed under ``stage_name``
    must still be the inode this call opened through ``stage_fd``.  If the
    name was rebound to a different entry, the rebound entry is left in place
    and the stage file of this call is reported as retained rather than
    destroyed.

    Returns True when the owned stage entry was removed.
    """
    try:
        fingerprint = _file_fingerprint(stage_fd, name=stage_name)
    except (OSError, RuntimeError, ValueError):
        return False
    try:
        _verify_leaf_entry(parent_fd, stage_name, fingerprint)
    except (OSError, RuntimeError, ValueError):
        # The name was rebound to a foreign entry.  Removing it would destroy
        # an entry this call does not own, so it is retained.
        return False
    try:
        os.unlink(stage_name, dir_fd=parent_fd)
    except OSError:
        return False
    return True


def guarded_namespace_write_bytes_atomic(binding: NamespaceBindingV1, payload: bytes) -> int:
    """Atomically replace the bound leaf inside the bound namespace.

    Staging happens in the *same* parent directory, so the replacement is a
    same-filesystem rename issued against the retained parent descriptor.  The
    staged file is created with ``O_EXCL`` and a no-follow link check, so a
    pre-existing entry at the stage name can never be reused.

    The stage name is unique per call, so concurrent atomic writes in one
    parent directory never contend and a failure of one writer can never
    destroy another writer's staged bytes.
    """
    _require_descriptor_capabilities()
    data = bytes(payload)
    with _boundary_errors(), ExitStack() as stack:
        witnesses, parent, _root = _walk_namespace(stack, binding, create_missing=True)
        leaf = _leaf_name(binding)
        entry = _entry_stat(parent.fd, leaf)
        if entry is not None and not stat.S_ISREG(entry.st_mode):
            raise NamespaceBoundaryError(
                "managed atomic target must be a regular file",
                code="guarded_snapshot_not_regular",
                details={"name": leaf},
            )
        if entry is not None and entry.st_nlink != 1:
            raise NamespaceBoundaryError(
                "namespace leaf has multiple hard links",
                code="guarded_snapshot_hard_link_rejected",
                details={"name": leaf, "link_count": entry.st_nlink},
            )
        stage_name: str | None = None
        stage_fd: int | None = None
        committed = False
        commit_attempted = False
        phase = "stage_create"
        try:
            stage_name, stage_fd = _open_unique_stage(parent.fd)
            stack.callback(os.close, stage_fd)
            phase = "stage_write"
            _write_all(stage_fd, data, name=binding.relative_path)
            phase = "stage_fsync"
            os.fsync(stage_fd)
            fingerprint = _file_fingerprint(stage_fd, name=stage_name)
            _verify_leaf_entry(parent.fd, stage_name, fingerprint)
            phase = "replace"
            commit_attempted = True
            os.replace(stage_name, leaf, src_dir_fd=parent.fd, dst_dir_fd=parent.fd)
            committed = True
            phase = "committed_identity"
            after = _file_fingerprint(stage_fd, name=binding.relative_path)
            _verify_leaf_entry(parent.fd, leaf, after)
            phase = "parent_fsync"
            _fsync_dir(parent.fd)
            _verify(witnesses)
        except (OSError, RuntimeError, ValueError) as exc:
            if commit_attempted:
                raise NamespaceOperationEffectUnknownError(binding, phase) from exc
            raise
        finally:
            # Single-shot cleanup, and only for an uncommitted call.  The
            # committed path renamed the stage entry onto the leaf, so no
            # stage entry of this call exists any more.
            if not committed and stage_name is not None and stage_fd is not None:
                _cleanup_stage(parent.fd, stage_name, stage_fd)
        return len(data)


def guarded_namespace_append_bytes(
    binding: NamespaceBindingV1,
    payload: bytes,
    *,
    durability: str = "buffered",
) -> int:
    """Append to the bound leaf through a retained parent descriptor."""
    if durability not in {"buffered", "flush", "fsync"}:
        raise ValueError("durability must be one of: buffered, flush, fsync")
    _require_descriptor_capabilities()
    data = bytes(payload)
    with _boundary_errors(), ExitStack() as stack:
        witnesses, parent, _root = _walk_namespace(stack, binding, create_missing=True)
        leaf = _leaf_name(binding)
        existed = _entry_stat(parent.fd, leaf) is not None
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            fd = os.open(leaf, flags, _LEAF_CREATE_MODE, dir_fd=parent.fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise NamespaceBoundaryError(
                    "symbolic links are not allowed",
                    code="guarded_snapshot_symlink_rejected",
                    details={"name": leaf},
                ) from exc
            raise NamespaceBoundaryError(
                "namespace leaf could not be opened",
                code="guarded_snapshot_open_failed",
                details={"name": leaf, "errno": exc.errno},
            ) from exc
        stack.callback(os.close, fd)
        _require_single_link_regular(fd, name=binding.relative_path)
        phase = "write"
        try:
            _write_all(fd, data, name=binding.relative_path)
            if durability in {"flush", "fsync"}:
                phase = "flush"
                if durability == "fsync":
                    os.fsync(fd)
            phase = "verify"
            _verify(witnesses)
            if durability == "fsync" and not existed:
                phase = "parent_fsync"
                _fsync_dir(parent.fd)
        except (OSError, RuntimeError, ValueError) as exc:
            raise NamespaceOperationEffectUnknownError(binding, phase) from exc
        return len(data)


def guarded_namespace_create_bytes(
    binding: NamespaceBindingV1,
    payload: bytes,
    *,
    max_bytes: int,
) -> NamespaceCreateReceiptV1:
    """Create the bound leaf only when it is absent, never overwriting it.

    The physical commit reuses the existing guarded create primitive
    (``guarded_compare_and_create_regular_file``), which stages the bytes in
    the bound parent directory and commits them with an atomic *no-replace*
    rename.  A leaf that already exists is therefore preserved: the commit
    refuses and the receipt reports ``created=False``.

    The traversal never follows a symbolic link, so a linked parent or leaf
    is refused instead of reaching outside the namespace.
    """
    _require_descriptor_capabilities()
    if type(payload) is not bytes:
        raise ValueError("content must be exact immutable bytes")
    data = bytes(payload)
    if max_bytes <= 0:
        raise ValueError("max_bytes must be a positive int")
    if len(data) > max_bytes:
        raise NamespaceBoundaryError(
            "namespace create content exceeds max_bytes",
            code="guarded_create_max_bytes_exceeded",
            details={"size": len(data), "max_bytes": max_bytes},
        )

    from .guarded_regular_file_snapshot import guarded_compare_and_create_regular_file

    # Walk the namespace chain first so a linked component is refused
    # before the commit primitive is reached.  Missing intermediate
    # directories are created by the commit primitive itself, so the walk
    # tolerates an absent component here.
    with _boundary_errors(), ExitStack() as stack:
        try:
            _walk_namespace(stack, binding, create_missing=False)
        except GuardedRegularFileSnapshotError as exc:
            if exc.code != "guarded_snapshot_missing":
                raise

    try:
        snapshot = guarded_compare_and_create_regular_file(
            binding.namespace_root,
            binding.relative_path,
            data,
            max_bytes=max_bytes,
        )
    except GuardedRegularFileSnapshotError as exc:
        if exc.details.get("original_effect") == "unknown":
            raise NamespaceOperationEffectUnknownError(
                binding, str(exc.details.get("phase") or "create_commit")
            ) from exc
        if exc.code == "guarded_create_expected_mismatch":
            # The leaf already existed: its content is preserved and this
            # call created nothing.
            return NamespaceCreateReceiptV1(
                absolute_path=binding.physical_path(),
                relative_path=binding.relative_path,
                created=False,
                bytes_written=0,
            )
        raise NamespaceBoundaryError(str(exc), code=exc.code, details=exc.details) from exc
    except OSError as exc:
        # The commit may have happened before the failure was observed.
        raise NamespaceOperationEffectUnknownError(binding, "create_commit") from exc
    return NamespaceCreateReceiptV1(
        absolute_path=binding.physical_path(),
        relative_path=binding.relative_path,
        created=True,
        bytes_written=snapshot.size,
    )


def guarded_namespace_remove(binding: NamespaceBindingV1, *, missing_ok: bool = True) -> bool:
    """Remove the bound leaf entry through a retained parent descriptor.

    Removing a *leaf* link removes the link entry itself and never touches
    the link target.  A *parent* link is refused by the traversal itself,
    because the parent descriptor can only be retained through a real
    directory.
    """
    _require_descriptor_capabilities()
    with _boundary_errors(), ExitStack() as stack:
        try:
            witnesses, parent, _root = _walk_namespace(stack, binding, create_missing=False)
        except GuardedRegularFileSnapshotError as exc:
            if exc.code == "guarded_snapshot_missing":
                # A missing parent directory means the leaf is absent too.
                return bool(missing_ok)
            raise
        leaf = _leaf_name(binding)
        entry = _entry_stat(parent.fd, leaf)
        if entry is None:
            _verify(witnesses)
            return bool(missing_ok)
        if stat.S_ISDIR(entry.st_mode):
            raise NamespaceBoundaryError(
                "namespace leaf is a directory",
                code="guarded_snapshot_not_regular",
                details={"name": leaf},
            )
        phase = "unlink"
        try:
            try:
                os.unlink(leaf, dir_fd=parent.fd)
            except FileNotFoundError:
                _verify(witnesses)
                return bool(missing_ok)
            phase = "verify"
            _verify(witnesses)
        except (OSError, RuntimeError, ValueError) as exc:
            raise NamespaceOperationEffectUnknownError(binding, phase) from exc
        return True
