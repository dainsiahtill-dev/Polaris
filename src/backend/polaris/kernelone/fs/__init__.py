"""KernelOne file system runtime exports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "FileWriteReceipt",
    "GuardedRegularFileSnapshotError",
    "GuardedRegularFileSnapshotV1",
    "KernelFileSystem",
    "KernelFileSystemAdapter",
    "LockAuthorityBindingV1",
    "LockFileIdentityV1",
    "LockKeyMaintenanceProofV1",
    "LockMaintenanceProofV1",
    "LockedRegularFileError",
    "LockedRegularFileSetV1",
    "StreamLeaseV1",
    "_atomic_write_json",
    "_atomic_write_text",
    "default_platform_lock_root",
    "format_workspace_tree",
    "get_default_adapter",
    "guarded_compare_and_create_regular_file",
    "guarded_compare_and_remove_regular_file",
    "guarded_compare_and_replace_regular_file",
    "read_guarded_regular_file_snapshot",
    "set_default_adapter",
]


def __getattr__(name: str) -> Any:
    if name in {
        "GuardedRegularFileSnapshotError",
        "GuardedRegularFileSnapshotV1",
        "guarded_compare_and_create_regular_file",
        "guarded_compare_and_remove_regular_file",
        "guarded_compare_and_replace_regular_file",
        "read_guarded_regular_file_snapshot",
    }:
        from polaris.kernelone.fs.guarded_regular_file_snapshot import (
            GuardedRegularFileSnapshotError,
            GuardedRegularFileSnapshotV1,
            guarded_compare_and_create_regular_file,
            guarded_compare_and_remove_regular_file,
            guarded_compare_and_replace_regular_file,
            read_guarded_regular_file_snapshot,
        )

        return {
            "GuardedRegularFileSnapshotError": GuardedRegularFileSnapshotError,
            "GuardedRegularFileSnapshotV1": GuardedRegularFileSnapshotV1,
            "guarded_compare_and_create_regular_file": guarded_compare_and_create_regular_file,
            "guarded_compare_and_remove_regular_file": guarded_compare_and_remove_regular_file,
            "guarded_compare_and_replace_regular_file": guarded_compare_and_replace_regular_file,
            "read_guarded_regular_file_snapshot": read_guarded_regular_file_snapshot,
        }[name]
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
    if name in {
        "LockAuthorityBindingV1",
        "LockFileIdentityV1",
        "LockKeyMaintenanceProofV1",
        "LockMaintenanceProofV1",
        "LockedRegularFileError",
        "LockedRegularFileSetV1",
        "StreamLeaseV1",
        "default_platform_lock_root",
    }:
        from polaris.kernelone.fs.locked_regular_file import (
            LockAuthorityBindingV1,
            LockedRegularFileError,
            LockedRegularFileSetV1,
            LockFileIdentityV1,
            LockKeyMaintenanceProofV1,
            LockMaintenanceProofV1,
            StreamLeaseV1,
            default_platform_lock_root,
        )

        return {
            "LockAuthorityBindingV1": LockAuthorityBindingV1,
            "LockFileIdentityV1": LockFileIdentityV1,
            "LockKeyMaintenanceProofV1": LockKeyMaintenanceProofV1,
            "LockMaintenanceProofV1": LockMaintenanceProofV1,
            "LockedRegularFileError": LockedRegularFileError,
            "LockedRegularFileSetV1": LockedRegularFileSetV1,
            "StreamLeaseV1": StreamLeaseV1,
            "default_platform_lock_root": default_platform_lock_root,
        }[name]
    if name == "format_workspace_tree":
        from polaris.kernelone.fs.tree import format_workspace_tree

        return format_workspace_tree
    if name == "FileWriteReceipt":
        from polaris.kernelone.fs.types import FileWriteReceipt

        return FileWriteReceipt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
