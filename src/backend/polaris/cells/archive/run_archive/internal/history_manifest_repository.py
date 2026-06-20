"""History Manifest Repository - thin re-export shim.

The implementation now lives in KernelOne at
``polaris.kernelone.storage.history_manifest_repository`` so that the shared
history-manifest repository can be reused by sibling archive cells
(``factory_archive``, ``task_snapshot_archive``) without creating a cross-cell
back-edge into ``archive.run_archive`` (CYCLE-04/05).

This module is retained as a stable import path for ``run_archive``'s own
internal users (e.g. ``history_archive_service``). It re-exports the full
public surface verbatim.
"""

from __future__ import annotations

from polaris.kernelone.storage.history_manifest_repository import (
    FactoryIndexEntry,
    HistoryManifestRepository,
    IndexEntry,
    IndexType,
    RunIndexEntry,
    TaskIndexEntry,
    create_history_manifest_repository,
)

__all__ = [
    "FactoryIndexEntry",
    "HistoryManifestRepository",
    "IndexEntry",
    "IndexType",
    "RunIndexEntry",
    "TaskIndexEntry",
    "create_history_manifest_repository",
]
