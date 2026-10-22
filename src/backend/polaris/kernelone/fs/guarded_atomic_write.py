"""Owner-bound ordinary atomic replacement through retained native descriptors.

This is not compare-and-swap. A successful commit replaces the current regular
leaf, with staging confined to the explicit owner's project runtime.
"""

from __future__ import annotations

import errno
import os
import stat
import uuid
from contextlib import ExitStack

from .guarded_regular_file_snapshot import (
    _DirectoryWitness,
    _file_fingerprint,
    _require_descriptor_capabilities,
    _retain_directory,
    _retain_or_mkdir_directory,
    _validated_relative_parts,
    _validated_root,
    _verify_directory_witness,
    _verify_leaf_entry,
    _write_all,
)
from .types import AtomicWriteBindingV1


class AtomicWriteEffectUnknownError(RuntimeError):
    """Replacement happened; later durability/identity evidence is incomplete."""

    def __init__(self, binding: AtomicWriteBindingV1, phase: str, staging_relative_path: str | None) -> None:
        super().__init__(f"atomic write effect unknown: {binding.relative_path!r} phase={phase}")
        self.owner_workspace = binding.runtime_owner_workspace
        self.target_workspace = binding.target_workspace
        self.relative_path = binding.relative_path
        self.phase = phase
        self.original_effect = "unknown"
        # Diagnostic location only: it may have been rebound. Never a cleanup
        # capability or proof that any entry at this name is still ours.
        self.staging_relative_path = staging_relative_path


def _require_atomic_capabilities() -> None:
    _require_descriptor_capabilities()
    if any(call not in os.supports_dir_fd for call in (os.rename, os.mkdir, os.rmdir, os.unlink)):
        raise NotImplementedError("managed cross-directory atomic replacement is unavailable")


def _verify_directories(directories: list[_DirectoryWitness]) -> None:
    for witness in directories:
        _verify_directory_witness(witness)


def _sync_directories(directories: list[_DirectoryWitness]) -> None:
    # Descendants first: persist new directory contents, then their entries in
    # retained parents. Existing ancestors are never chmod'ed.
    seen: set[tuple[int, int]] = set()
    for witness in reversed(directories):
        identity = (witness.device, witness.inode)
        if identity not in seen:
            os.fsync(witness.fd)
            seen.add(identity)


def _validate_target(parent: _DirectoryWitness, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("managed atomic target must be a regular file with one link")


def _clean_stage(
    parent: _DirectoryWitness,
    operation: _DirectoryWitness,
    name: str,
    stage_fd: int | None,
    *,
    committed: bool,
) -> None:
    # Only remove the exact objects owned by this call. A rebound operation
    # name or file is retained for diagnosis, never recursively traversed.
    _verify_directory_witness(parent)
    _verify_directory_witness(operation)
    if stage_fd is not None and not committed:
        fingerprint = _file_fingerprint(stage_fd, name=name)
        _verify_leaf_entry(operation.fd, name, fingerprint)
        os.unlink(name, dir_fd=operation.fd)
    os.rmdir(str(operation.entry_name), dir_fd=parent.fd)
    os.fsync(parent.fd)


def _retain_root(
    stack: ExitStack, root_parts: tuple[str, ...], directories: list[_DirectoryWitness]
) -> _DirectoryWitness:
    current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
    directories.append(current)
    display = ""
    for component in root_parts:
        display += "/" + component
        current = _retain_directory(stack, parent=current, entry_name=component, display_name=display)
        directories.append(current)
    return current


def guarded_atomic_write(binding: AtomicWriteBindingV1, payload: bytes) -> int:
    """Write complete bytes, atomically replace, and confirm native durability.

    Pre-commit errors leave the old target unchanged (created ancestors may
    remain). Post-commit errors keep complete new bytes and raise explicit
    uncertainty; no rollback, second write, or fallback is attempted.
    """
    # Keep this import lazy: storage layout shares the existing KFS boundary.
    from polaris.kernelone.storage.layout import canonical_workspace_runtime_root

    root, root_parts = _validated_root(binding.target_workspace)
    owner_root, owner_parts = _validated_root(binding.runtime_owner_workspace)
    stage_root, _ = _validated_root(os.path.join(canonical_workspace_runtime_root(owner_root), "fs", "staging"))
    if os.path.commonpath((owner_root, stage_root)) != owner_root:
        raise ValueError("canonical staging root must remain under its bound runtime owner")
    stage_parts = _validated_relative_parts(os.path.relpath(stage_root, owner_root))
    parts = _validated_relative_parts(binding.relative_path)
    _require_atomic_capabilities()
    committed = False
    operation_name: str | None = None
    cleanup_complete = False
    phase = "prepare"
    try:
        with ExitStack() as stack:
            directories: list[_DirectoryWitness] = []
            namespace = _retain_root(stack, root_parts, directories)
            owner = namespace if root == owner_root else _retain_root(stack, owner_parts, directories)
            owned = [namespace, owner]
            destination = namespace
            for component in parts[:-1]:
                destination = _retain_or_mkdir_directory(
                    stack,
                    parent=destination,
                    entry_name=component,
                    display_name=root + "/" + component,
                    mode=0o777,
                )
                directories.append(destination)
                owned.append(destination)
            _validate_target(destination, parts[-1])

            stage_parent = owner
            stage_display = owner_root
            for component in stage_parts:
                stage_display += "/" + component
                stage_parent = _retain_or_mkdir_directory(
                    stack,
                    parent=stage_parent,
                    entry_name=component,
                    display_name=stage_display,
                    mode=0o700,
                )
                directories.append(stage_parent)
                owned.append(stage_parent)
            operation_name = "atomic-" + uuid.uuid4().hex
            os.mkdir(operation_name, 0o700, dir_fd=stage_parent.fd)
            operation = _retain_directory(
                stack, parent=stage_parent, entry_name=operation_name, display_name=operation_name
            )
            directories.append(operation)
            owned.append(operation)
            stage_fd: int | None = None
            original_error: BaseException | None = None
            try:
                phase = "stage_write"
                stage_fd = os.open(
                    "payload",
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=operation.fd,
                )
                stack.callback(os.close, stage_fd)
                _write_all(stage_fd, payload, name=binding.relative_path)
                phase = "file_fsync"
                os.fsync(stage_fd)
                fingerprint = _file_fingerprint(stage_fd, name="payload")
                _verify_leaf_entry(operation.fd, "payload", fingerprint)
                phase = "prepare_directory_fsync"
                _sync_directories(owned)
                _verify_directories(directories)
                _verify_leaf_entry(operation.fd, "payload", fingerprint)
                _validate_target(destination, parts[-1])
                if operation.device != destination.device:
                    raise OSError(errno.EXDEV, "managed staging and target are on different filesystems")
                phase = "rename"
                os.replace("payload", parts[-1], src_dir_fd=operation.fd, dst_dir_fd=destination.fd)
                committed = True
                phase = "committed_identity"
                after = _file_fingerprint(stage_fd, name=binding.relative_path)
                _verify_leaf_entry(destination.fd, parts[-1], after)
                _verify_directories(directories)
                phase = "commit_directory_fsync"
                _sync_directories(owned)
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                original_error = exc
                raise
            finally:
                if original_error is None:
                    phase = "stage_cleanup"
                    _clean_stage(stage_parent, operation, "payload", stage_fd, committed=committed)
                    cleanup_complete = True
                else:
                    try:
                        _clean_stage(stage_parent, operation, "payload", stage_fd, committed=committed)
                        cleanup_complete = True
                    except (OSError, RuntimeError, ValueError) as cleanup_error:
                        original_error.add_note(f"owned staging retained: {operation_name}: {cleanup_error}")
            phase = "final_binding"
            _verify_directories(directories[:-1])  # The owned operation directory was removed above.
            _verify_leaf_entry(destination.fd, parts[-1], after)
            phase = "descriptor_close"
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        if committed:
            staging_path = None if cleanup_complete else "/".join((*stage_parts, str(operation_name)))
            raise AtomicWriteEffectUnknownError(binding, phase, staging_path) from exc
        raise
    return len(payload)
