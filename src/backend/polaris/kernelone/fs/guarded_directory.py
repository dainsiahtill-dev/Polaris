"""Descriptor-bound directory creation within an existing physical root."""

from __future__ import annotations

import os
from contextlib import ExitStack

from .guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    _require_descriptor_capabilities,
    _retain_directory,
    _validated_relative_parts,
    _validated_root,
    _verify_directory_witness,
)
from .types import DirectoryCreateReceipt


def guarded_create_directory(
    root: str,
    relative_path: str,
    *,
    parents: bool = False,
    exist_ok: bool = False,
) -> DirectoryCreateReceipt:
    """Create only requested descendants, preserving retained ancestor bindings.

    No root/ancestor provisioning or symlink fallback. A failed post-effect
    validation retains created directories and raises rather than claiming a
    receipt or deleting possibly rebound paths. This is a POSIX capability.
    """
    root_path, root_parts = _validated_root(root)
    path_parts = _validated_relative_parts(relative_path)
    if type(parents) is not bool or type(exist_ok) is not bool:
        raise ValueError("parents and exist_ok must be bool")
    _require_descriptor_capabilities()
    if os.mkdir not in os.supports_dir_fd:
        raise RuntimeError("guarded_directory_capability_unavailable")
    with ExitStack() as stack:
        current = _retain_directory(stack, parent=None, entry_name="/", display_name="/")
        witnesses = [current]
        display_parts: list[str] = []
        for part in root_parts:
            display_parts.append(part)
            current = _retain_directory(
                stack, parent=current, entry_name=part, display_name="/" + "/".join(display_parts)
            )
            witnesses.append(current)
        root_identity = (current.device, current.inode)
        created: list[str] = []
        for index, part in enumerate(path_parts):
            for witness in witnesses:
                _verify_directory_witness(witness)
            relative = "/".join(path_parts[: index + 1])
            display = root_path.rstrip("/") + "/" + relative
            is_leaf = index == len(path_parts) - 1
            try:
                child = _retain_directory(stack, parent=current, entry_name=part, display_name=display)
            except GuardedRegularFileSnapshotError as exc:
                if exc.code != "guarded_snapshot_missing" or (not is_leaf and not parents):
                    raise
                # Revalidate immediately before the physical effect, not only
                # before the failed open. mkdir is relative to retained parent.
                for witness in witnesses:
                    _verify_directory_witness(witness)
                try:
                    os.mkdir(part, 0o700, dir_fd=current.fd)
                except FileExistsError:
                    if is_leaf and not exist_ok:
                        raise
                else:
                    created.append(relative)
                child = _retain_directory(stack, parent=current, entry_name=part, display_name=display)
            else:
                if is_leaf and not exist_ok:
                    raise FileExistsError(display)
            current = child
            witnesses.append(current)
            for witness in witnesses:
                _verify_directory_witness(witness)
        return DirectoryCreateReceipt(
            relative_path=relative_path,
            created_paths=tuple(created),
            root_identity=root_identity,
            target_identity=(current.device, current.inode),
        )
