"""KernelOne filesystem shared types.

This module contains shared types used by filesystem contracts and implementations.
It is separated to avoid circular imports between kernelone.fs and infrastructure.storage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AtomicWriteBindingV1:
    """Target namespace and host runtime owner have separate physical scopes."""

    target_workspace: str
    relative_path: str
    runtime_owner_workspace: str


@dataclass(frozen=True, slots=True)
class NamespaceBindingV1:
    """One bound physical namespace root plus the logical path below it.

    ``namespace_root`` is the physical root that the logical prefix is bound
    to.  ``relative_path`` is the slash-separated path below that root.
    ``logical_prefix`` is the logical namespace name ("runtime", "workspace"
    or "config") and is carried for evidence only.

    A binding never carries a pre-resolved leaf path: the consumer walks the
    namespace itself with retained no-follow descriptors, so a symbolic link
    anywhere in the chain is refused instead of followed.
    """

    namespace_root: str
    relative_path: str
    logical_prefix: str

    def physical_path(self) -> str:
        """Return the lexical absolute path of the bound leaf.

        The value is evidence only.  It is never used to open the leaf: a
        lexical path cannot prove that no component was rebound.
        """
        if not self.relative_path:
            return self.namespace_root
        return os.path.join(self.namespace_root, *self.relative_path.split("/"))


@dataclass(frozen=True, slots=True)
class NamespaceCreateReceiptV1:
    """Evidence that one create-only effect either happened or did not.

    ``created`` is True only when this call created the leaf.  When the leaf
    already existed the content was preserved and ``created`` is False: the
    existing file is never overwritten and never removed.

    ``absolute_path`` is the lexical path of the leaf for evidence only; the
    effect itself was performed through retained no-follow descriptors.
    """

    absolute_path: str
    relative_path: str
    created: bool
    bytes_written: int


@dataclass(frozen=True, slots=True)
class FileWriteReceipt:
    """Immutable receipt for a file write operation."""

    logical_path: str
    absolute_path: str
    bytes_written: int
    atomic: bool = False


@dataclass(frozen=True, slots=True)
class DirectoryCreateReceipt:
    """Physical directory-effect evidence; never project execution authority."""

    relative_path: str
    created_paths: tuple[str, ...]
    root_identity: tuple[int, int]
    target_identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class FileMetadataV1:
    """No-follow physical metadata, bound to one observed workspace root."""

    relative_path: str
    device: int
    inode: int
    mode: int
    nlink: int
    size: int
    mtime_ns: int
    ctime_ns: int
    root_device: int
    root_inode: int


@dataclass(frozen=True, slots=True)
class DirectoryEntryV1:
    """One exact basename and its no-follow observation; no live entry handle."""

    name: str
    metadata: FileMetadataV1


@dataclass(frozen=True, slots=True)
class DirectoryListingV1:
    """Bounded membership; incomplete subsets are not globally first N names.

    ``complete`` means real enumeration EOF, not an atomic directory snapshot.
    """

    directory: FileMetadataV1
    entries: tuple[DirectoryEntryV1, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class SymlinkObservationV1:
    """Exact link text as data; grants no authority over its target."""

    metadata: FileMetadataV1
    target: str
