"""KernelOne file system runtime exports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "FileWriteReceipt",
    "KernelFileSystem",
    "KernelFileSystemAdapter",
    "_atomic_write_json",
    "_atomic_write_text",
    "format_workspace_tree",
    "get_default_adapter",
    "set_default_adapter",
]


def __getattr__(name: str) -> Any:
    if name in {"KernelFileSystemAdapter", "_atomic_write_json", "_atomic_write_text"}:
        from polaris.kernelone.fs.contracts import (
            KernelFileSystemAdapter,
            _atomic_write_json,
            _atomic_write_text,
        )

        return {
            "KernelFileSystemAdapter": KernelFileSystemAdapter,
            "_atomic_write_json": _atomic_write_json,
            "_atomic_write_text": _atomic_write_text,
        }[name]
    if name in {"get_default_adapter", "set_default_adapter"}:
        from polaris.kernelone.fs.registry import get_default_adapter, set_default_adapter

        return {
            "get_default_adapter": get_default_adapter,
            "set_default_adapter": set_default_adapter,
        }[name]
    if name == "KernelFileSystem":
        from polaris.kernelone.fs.runtime import KernelFileSystem

        return KernelFileSystem
    if name == "format_workspace_tree":
        from polaris.kernelone.fs.tree import format_workspace_tree

        return format_workspace_tree
    if name == "FileWriteReceipt":
        from polaris.kernelone.fs.types import FileWriteReceipt

        return FileWriteReceipt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
