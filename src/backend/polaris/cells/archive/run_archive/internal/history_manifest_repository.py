"""History Manifest Repository - Repository for managing history index files.

This module provides a repository pattern for managing history index files:
- workspace/<metadata_dir>/history/index/runs.index.jsonl
- workspace/<metadata_dir>/history/index/tasks.index.jsonl
- workspace/<metadata_dir>/history/index/factory.index.jsonl

All writes use atomic operations (temp file + rename) to ensure data integrity.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from polaris.cells.storage.layout.public.service import resolve_polaris_roots
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.text_ops import write_text_atomic

logger = logging.getLogger(__name__)


class IndexType(Enum):
    """Type of history index."""

    RUNS = "runs"
    TASKS = "tasks"
    FACTORY = "factory"


@dataclass
class IndexEntry:
    """Base class for index entries."""

    id: str
    archive_timestamp: float
    archive_datetime: str
    reason: str
    target_path: str
    total_size_bytes: int
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndexEntry:
        return cls(**data)


@dataclass
class RunIndexEntry(IndexEntry):
    """Entry in the runs index."""

    content_hash: str = ""
    status: str = ""


@dataclass
class TaskIndexEntry(IndexEntry):
    """Entry in the tasks index."""

    snapshot_id: str = ""


@dataclass
class FactoryIndexEntry(IndexEntry):
    """Entry in the factory index."""

    factory_run_id: str = ""


class HistoryManifestRepository:
    """Repository for managing history index files.

    Provides atomic write operations and query capabilities for history indices.

    Thread safety: each instance serializes writes via ``_write_lock``.
    """

    def __init__(self, workspace: str) -> None:
        """Initialize the repository.

        Args:
            workspace: The workspace root path
        """
        self.workspace = Path(workspace).resolve()

        roots = resolve_polaris_roots(str(self.workspace))
        self.history_root = Path(roots.history_root)
        self.index_dir = self.history_root / "index"

        # Serialize concurrent writers for this repository instance.
        import threading as _threading

        self._write_lock = _threading.Lock()

        # Ensure index directory exists
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def _get_index_path(self, index_type: IndexType) -> Path:
        """Get the path for an index file.

        Args:
            index_type: The type of index

        Returns:
            Path to the index file
        """
        return self.index_dir / f"{index_type.value}.index.jsonl"

    def _atomic_write(self, index_path: Path, entry: dict[str, Any]) -> None:
        """Append an entry to an index file with TOCTOU-safe serialization.

        The write lock serializes concurrent writers for this instance.
        Uses KernelOne write_text_atomic for atomic durability.

        Args:
            index_path: Path to the index file
            entry: The entry to write (as dict)
        """
        with self._write_lock:
            fs = KernelFileSystem(str(index_path.parent), get_default_adapter())
            rel_path = index_path.name

            # Read existing content while holding the lock so no other
            # writer can interleave between the read and the write.
            lines: list[str] = []
            if fs.workspace_exists(rel_path):
                try:
                    existing_content = fs.workspace_read_text(rel_path, encoding="utf-8")
                    if existing_content:
                        lines.append(existing_content.rstrip("\n"))
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.debug("Failed to read existing index content from %s: %s", rel_path, exc)

            # Append new entry
            lines.append(json.dumps(entry, ensure_ascii=False))
            content = "\n".join(lines) + "\n"
            write_text_atomic(str(index_path), content, encoding="utf-8")

    def append_run_entry(self, entry: RunIndexEntry) -> None:
        """Append a run entry to the runs index.

        Args:
            entry: The run index entry
        """
        index_path = self._get_index_path(IndexType.RUNS)
        self._atomic_write(index_path, entry.to_dict())
        logger.debug(f"Appended run entry: {entry.id}")

    def append_task_entry(self, entry: TaskIndexEntry) -> None:
        """Append a task entry to the tasks index.

        Args:
            entry: The task index entry
        """
        index_path = self._get_index_path(IndexType.TASKS)
        self._atomic_write(index_path, entry.to_dict())
        logger.debug(f"Appended task entry: {entry.snapshot_id}")

    def append_factory_entry(self, entry: FactoryIndexEntry) -> None:
        """Append a factory entry to the factory index.

        Args:
            entry: The factory index entry
        """
        index_path = self._get_index_path(IndexType.FACTORY)
        self._atomic_write(index_path, entry.to_dict())
        logger.debug(f"Appended factory entry: {entry.factory_run_id}")

    def read_runs_index(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunIndexEntry]:
        """Read entries from the runs index.

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip

        Returns:
            List of run index entries, sorted by timestamp descending
        """
        return cast("list[RunIndexEntry]", self._read_index(IndexType.RUNS, RunIndexEntry, limit, offset))

    def read_tasks_index(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskIndexEntry]:
        """Read entries from the tasks index.

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip

        Returns:
            List of task index entries, sorted by timestamp descending
        """
        return cast("list[TaskIndexEntry]", self._read_index(IndexType.TASKS, TaskIndexEntry, limit, offset))

    def read_factory_index(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FactoryIndexEntry]:
        """Read entries from the factory index.

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip

        Returns:
            List of factory index entries, sorted by timestamp descending
        """
        return cast("list[FactoryIndexEntry]", self._read_index(IndexType.FACTORY, FactoryIndexEntry, limit, offset))

    def _read_index(
        self,
        index_type: IndexType,
        entry_class: type[IndexEntry],
        limit: int,
        offset: int,
    ) -> list[IndexEntry]:
        """Generic index reader.

        Args:
            index_type: The type of index
            entry_class: The entry class to instantiate
            limit: Maximum number of entries
            offset: Number of entries to skip

        Returns:
            List of index entries
        """
        index_path = self._get_index_path(index_type)

        if not index_path.exists():
            return []

        entries: list[IndexEntry] = []

        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(entry_class.from_dict(data))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse index entry: {e}")
                    continue

        # Sort by timestamp descending
        entries.sort(key=lambda x: x.archive_timestamp, reverse=True)

        return entries[offset : offset + limit]

    def get_run_entry(self, run_id: str) -> RunIndexEntry | None:
        """Get a specific run entry by ID.

        Args:
            run_id: The run ID

        Returns:
            The run entry or None if not found
        """
        index_path = self._get_index_path(IndexType.RUNS)

        if not index_path.exists():
            return None

        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") == run_id:
                        return cast("RunIndexEntry", RunIndexEntry.from_dict(data))
                except (json.JSONDecodeError, TypeError):
                    continue

        return None

    def get_task_entry(self, snapshot_id: str) -> TaskIndexEntry | None:
        """Get a specific task entry by snapshot ID.

        Args:
            snapshot_id: The snapshot ID

        Returns:
            The task entry or None if not found
        """
        index_path = self._get_index_path(IndexType.TASKS)

        if not index_path.exists():
            return None

        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("snapshot_id") == snapshot_id:
                        return cast("TaskIndexEntry", TaskIndexEntry.from_dict(data))
                except (json.JSONDecodeError, TypeError):
                    continue

        return None

    def get_factory_entry(self, factory_run_id: str) -> FactoryIndexEntry | None:
        """Get a specific factory entry by ID.

        Args:
            factory_run_id: The factory run ID

        Returns:
            The factory entry or None if not found
        """
        index_path = self._get_index_path(IndexType.FACTORY)

        if not index_path.exists():
            return None

        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("factory_run_id") == factory_run_id:
                        return cast("FactoryIndexEntry", FactoryIndexEntry.from_dict(data))
                except (json.JSONDecodeError, TypeError):
                    continue

        return None

    def count_entries(self, index_type: IndexType) -> int:
        """Count entries in an index.

        Args:
            index_type: The type of index

        Returns:
            Number of entries
        """
        index_path = self._get_index_path(index_type)

        if not index_path.exists():
            return 0

        count = 0
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1

        return count

    def clear_index(self, index_type: IndexType) -> None:
        """Clear all entries from an index.

        Args:
            index_type: The type of index to clear
        """
        index_path = self._get_index_path(index_type)

        if index_path.exists():
            index_path.unlink()
            logger.info(f"Cleared index: {index_path}")

    def list_indices(self) -> dict[str, int]:
        """List all indices and their entry counts.

        Returns:
            Dictionary mapping index type to entry count
        """
        return {
            "runs": self.count_entries(IndexType.RUNS),
            "tasks": self.count_entries(IndexType.TASKS),
            "factory": self.count_entries(IndexType.FACTORY),
        }


def create_history_manifest_repository(workspace: str) -> HistoryManifestRepository:
    """Factory function to create a HistoryManifestRepository.

    Args:
        workspace: Workspace root path

    Returns:
        Configured HistoryManifestRepository instance
    """
    return HistoryManifestRepository(workspace)


__all__ = [
    "FactoryIndexEntry",
    "HistoryManifestRepository",
    "IndexEntry",
    "IndexType",
    "RunIndexEntry",
    "TaskIndexEntry",
    "create_history_manifest_repository",
]
