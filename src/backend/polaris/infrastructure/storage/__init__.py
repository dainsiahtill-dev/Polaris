"""Storage infrastructure package."""

from __future__ import annotations

from typing import Any

__all__ = [
    "FileWriteReceipt",
    "LocalFileSystemAdapter",
    "StorageAdapter",
    "clear_adapter_cache",
    "get_storage_adapter",
]


def __getattr__(name: str) -> Any:
    if name == "LocalFileSystemAdapter":
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter
    if name in {"StorageAdapter", "clear_adapter_cache", "get_storage_adapter"}:
        from polaris.infrastructure.storage.adapter import (
            StorageAdapter,
            clear_adapter_cache,
            get_storage_adapter,
        )

        return {
            "StorageAdapter": StorageAdapter,
            "clear_adapter_cache": clear_adapter_cache,
            "get_storage_adapter": get_storage_adapter,
        }[name]
    if name == "FileWriteReceipt":
        from polaris.kernelone.fs.types import FileWriteReceipt

        return FileWriteReceipt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
