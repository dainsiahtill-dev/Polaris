"""Descriptor-anchored cleanup of an explicitly owned directory tree."""

from __future__ import annotations

import os
import shutil
import stat
from contextlib import ExitStack

from .guarded_metadata import _retained_parent, _stat_entry, _verify
from .guarded_regular_file_snapshot import _require_descriptor_capabilities, _validated_relative_parts, _validated_root
from .types import FileMetadataV1


def guarded_remove_tree(root: str, relative_path: str, *, expected: FileMetadataV1) -> None:
    """Remove an owned tree after physical drain, rejecting rebound identities.

    Caller must exclusively own the parent namespace and drain all writers
    before removal; arbitrary concurrent hostile host replacement is outside
    this resource cleanup contract. Root and ancestry are retained without
    following links, and the leaf's original identity is checked. Directory
    child writes may change timestamps and link counts, which are deliberately
    outside the identity match. Symlink children are unlinked, never traversed.
    Errors are reported; partial cleanup never becomes a success receipt.
    """
    _, root_parts = _validated_root(root)
    parts = _validated_relative_parts(relative_path)
    if type(expected) is not FileMetadataV1 or expected.relative_path != relative_path:
        raise ValueError("directory cleanup requires the original relative metadata binding")
    if not stat.S_ISDIR(expected.mode):
        raise ValueError("directory cleanup expected binding must describe a directory")
    _require_descriptor_capabilities()
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise RuntimeError("guarded_remove_tree_capability_unavailable")
    with ExitStack() as stack:
        witnesses, root_witness = _retained_parent(stack, root_parts, parts[:-1], observed=True)
        if (root_witness.device, root_witness.inode) != (expected.root_device, expected.root_inode):
            raise RuntimeError("guarded_remove_tree_root_identity_drift")
        parent = witnesses[-1]
        info = _stat_entry(parent.fd, parts[-1], observed=True)
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (expected.device, expected.inode):
            raise RuntimeError("guarded_remove_tree_directory_identity_drift")
        _verify(witnesses)
        # CPython's fd-based implementation verifies lstat/open/fstat before
        # descending and uses unlink/rmdir relative to retained descriptors.
        shutil.rmtree(parts[-1], dir_fd=parent.fd)
        _verify(witnesses)
        try:
            os.stat(parts[-1], dir_fd=parent.fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise RuntimeError("guarded_remove_tree_directory_reappeared")


__all__ = ["guarded_remove_tree"]
