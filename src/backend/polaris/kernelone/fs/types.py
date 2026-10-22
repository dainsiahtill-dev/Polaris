"""KernelOne filesystem shared types.

This module contains shared types used by filesystem contracts and implementations.
It is separated to avoid circular imports between kernelone.fs and infrastructure.storage.
"""

from __future__ import annotations

from dataclasses import dataclass


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
