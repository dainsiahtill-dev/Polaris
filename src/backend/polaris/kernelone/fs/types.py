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
