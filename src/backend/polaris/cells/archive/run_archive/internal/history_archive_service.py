"""History Archive Service - Unified service for archiving runtime data to history.

This module provides:
- Run-level archiving (workflow runs)
- Task snapshot archiving
- Factory run archiving
- Manifest generation with checksums
- Event compression (.jsonl -> .jsonl.zst)
- Index generation for history queries
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.archive.run_archive.internal.history_manifest_repository import HistoryManifestRepository
from polaris.cells.storage.layout.public.service import resolve_polaris_roots
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)

# Optional compression support
try:
    import zstandard as zstd

    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False
    logger.warning("zstandard not available, event compression disabled")


@dataclass
class ArchiveManifest:
    """Archive manifest - metadata for an archived run/snapshot."""

    # Identity
    scope: str  # "run", "task_snapshot", "factory_run"
    id: str  # run_id, snapshot_id, or factory_run_id

    # Timing
    archive_timestamp: float
    archive_datetime: str  # ISO format

    # Source
    source_runtime_root: str
    source_paths: list[str]

    # Target (relative to history root)
    target_path: str

    # Content metadata
    total_size_bytes: int
    file_count: int
    content_hash: str  # SHA256 of all files combined

    # Archive reason
    reason: str  # completed, failed, cancelled, blocked, timeout

    # Compression info
    compressed: bool = False
    compression_ratio: float = 1.0

    # Index entries
    run_index_entry: dict[str, Any] = field(default_factory=dict)
    task_index_entry: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveManifest:
        return cls(**data)


@dataclass
class HistoryRunIndex:
    """Entry in the runs index file."""

    run_id: str
    archive_timestamp: float
    archive_datetime: str
    reason: str
    target_path: str
    total_size_bytes: int
    file_count: int
    content_hash: str
    status: str = ""  # from original run status

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryRunIndex:
        return cls(**data)


class HistoryArchiveService:
    """Service for archiving runtime data to workspace/<metadata_dir>/history."""

    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())

        self._storage_roots = resolve_polaris_roots(str(self.workspace))
        self.history_root = Path(self._storage_roots.history_root)
        self.runtime_root = Path(self._storage_roots.runtime_root)

        # Ensure history root exists
        self.history_root.mkdir(parents=True, exist_ok=True)

        # Ensure index directory exists
        self.index_dir = self.history_root / "index"
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Use HistoryManifestRepository for atomic index writes
        self._manifest_repo = HistoryManifestRepository(str(self.workspace))

    def _workspace_rel(self, path: Path) -> str:
        return self._kernel_fs.to_workspace_relative_path(str(path))

    # =========================================================================
    # Core Archive Operations
    # =========================================================================

    def archive_run(
        self,
        run_id: str,
        reason: str = "completed",
        status: str = "",
    ) -> ArchiveManifest:
        """Archive a runtime run to history.

        Args:
            run_id: The run ID to archive
            reason: Archive reason (completed, failed, cancelled, blocked, timeout)
            status: Original run status (optional, for index)

        Returns:
            ArchiveManifest with metadata
        """
        # Determine source and target paths
        source_run_dir = self.runtime_root / "runs" / run_id
        target_run_dir = self.history_root / "runs" / run_id

        if not source_run_dir.exists():
            logger.warning(f"Source run directory does not exist: {source_run_dir}")
            # Create empty manifest
            return self._create_empty_manifest("run", run_id, reason)

        # Determine source paths to archive
        source_paths = self._collect_run_files(source_run_dir)

        # Copy files to target
        target_run_dir.parent.mkdir(parents=True, exist_ok=True)

        # Copy directory structure
        self._copy_directory(source_run_dir, target_run_dir)

        # Compress events if applicable
        self._compress_run_events(target_run_dir)

        # Calculate checksums
        total_size, file_count, content_hash = self._calculate_checksums(target_run_dir)

        # Calculate compression ratio
        original_size = sum((source_run_dir / p).stat().st_size for p in source_paths if (source_run_dir / p).exists())
        compression_ratio = total_size / original_size if original_size > 0 else 1.0

        # Generate manifest
        now = time.time()
        manifest = ArchiveManifest(
            scope="run",
            id=run_id,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.runtime_root),
            source_paths=source_paths,
            target_path=str(target_run_dir.relative_to(self.history_root)),
            total_size_bytes=total_size,
            file_count=file_count,
            content_hash=content_hash,
            reason=reason,
            compressed=ZSTD_AVAILABLE,
            compression_ratio=compression_ratio,
            run_index_entry=HistoryRunIndex(
                run_id=run_id,
                archive_timestamp=now,
                archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                reason=reason,
                target_path=str(target_run_dir.relative_to(self.history_root)),
                total_size_bytes=total_size,
                file_count=file_count,
                content_hash=content_hash,
                status=status,
            ).to_dict(),
        )

        # Write manifest
        manifest_path = target_run_dir / "manifest.json"
        self._kernel_fs.workspace_write_text(
            self._workspace_rel(manifest_path),
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Update index
        self._append_to_index("runs", manifest.run_index_entry)

        logger.info(f"Archived run {run_id} to {target_run_dir}")

        return manifest

    def archive_task_snapshot(
        self,
        snapshot_id: str,
        source_tasks_dir: str | None = None,
        source_plan_path: str | None = None,
        reason: str = "completed",
    ) -> ArchiveManifest:
        """Archive a task snapshot to history.

        Args:
            snapshot_id: The snapshot ID (e.g., "pm-00001-1234567890")
            source_tasks_dir: Path to tasks directory (defaults to runtime/tasks)
            source_plan_path: Path to plan.json (optional)
            reason: Archive reason

        Returns:
            ArchiveManifest with metadata
        """
        if source_tasks_dir is None:
            source_tasks_dir = str(self.runtime_root / "tasks")

        source_dir = Path(source_tasks_dir)
        target_dir = self.history_root / "tasks" / snapshot_id

        if not source_dir.exists():
            logger.warning(f"Source tasks directory does not exist: {source_dir}")
            return self._create_empty_manifest("task_snapshot", snapshot_id, reason)

        # Copy files
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        self._copy_directory(source_dir, target_dir)

        # Optionally copy plan
        if source_plan_path and Path(source_plan_path).exists():
            plan_target = target_dir / "plan.json"
            shutil.copy2(source_plan_path, plan_target)

        # Calculate checksums
        total_size, file_count, content_hash = self._calculate_checksums(target_dir)

        # Generate manifest
        now = time.time()
        manifest = ArchiveManifest(
            scope="task_snapshot",
            id=snapshot_id,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.runtime_root),
            source_paths=[str(source_dir.relative_to(self.runtime_root))],
            target_path=str(target_dir.relative_to(self.history_root)),
            total_size_bytes=total_size,
            file_count=file_count,
            content_hash=content_hash,
            reason=reason,
            compressed=False,
            compression_ratio=1.0,
        )

        # Write manifest
        manifest_path = target_dir / "manifest.json"
        self._kernel_fs.workspace_write_text(
            self._workspace_rel(manifest_path),
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Update task index
        task_index_entry = {
            "snapshot_id": snapshot_id,
            "archive_timestamp": now,
            "archive_datetime": manifest.archive_datetime,
            "reason": reason,
            "target_path": str(target_dir.relative_to(self.history_root)),
            "total_size_bytes": total_size,
            "file_count": file_count,
        }
        self._append_to_index("tasks", task_index_entry)

        logger.info(f"Archived task snapshot {snapshot_id} to {target_dir}")

        return manifest

    def archive_factory_run(
        self,
        factory_run_id: str,
        source_factory_dir: str | None = None,
        reason: str = "completed",
    ) -> ArchiveManifest:
        """Archive a factory run to history.

        Args:
            factory_run_id: The factory run ID
            source_factory_dir: Path to factory directory (defaults to workspace/<metadata_dir>/factory)
            reason: Archive reason

        Returns:
            ArchiveManifest with metadata
        """
        # Default source: workspace/<metadata_dir>/factory/<run_id>
        if source_factory_dir is None:
            source_factory_dir = str(Path(self._storage_roots.workspace_persistent_root) / "factory" / factory_run_id)

        source_dir = Path(source_factory_dir)
        target_dir = self.history_root / "factory" / factory_run_id

        if not source_dir.exists():
            logger.warning(f"Source factory directory does not exist: {source_dir}")
            return self._create_empty_manifest("factory_run", factory_run_id, reason)

        # Copy files
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        self._copy_directory(source_dir, target_dir)

        # Calculate checksums
        total_size, file_count, content_hash = self._calculate_checksums(target_dir)

        # Generate manifest
        now = time.time()
        manifest = ArchiveManifest(
            scope="factory_run",
            id=factory_run_id,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self._storage_roots.workspace_persistent_root),
            source_paths=[str(source_dir.relative_to(self._storage_roots.workspace_persistent_root))],
            target_path=str(target_dir.relative_to(self.history_root)),
            total_size_bytes=total_size,
            file_count=file_count,
            content_hash=content_hash,
            reason=reason,
            compressed=False,
            compression_ratio=1.0,
        )

        # Write manifest
        manifest_path = target_dir / "manifest.json"
        self._kernel_fs.workspace_write_text(
            self._workspace_rel(manifest_path),
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Update factory index
        factory_index_entry = {
            "factory_run_id": factory_run_id,
            "archive_timestamp": now,
            "archive_datetime": manifest.archive_datetime,
            "reason": reason,
            "target_path": str(target_dir.relative_to(self.history_root)),
            "total_size_bytes": total_size,
            "file_count": file_count,
        }
        self._append_to_index("factory", factory_index_entry)

        logger.info(f"Archived factory run {factory_run_id} to {target_dir}")

        return manifest

    # =========================================================================
    # Query Operations
    # =========================================================================

    def list_history_runs(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[HistoryRunIndex]:
        """List archived runs from history index.

        Args:
            limit: Maximum number of entries to return
            offset: Number of entries to skip

        Returns:
            List of HistoryRunIndex entries
        """
        safe_limit = max(0, int(limit))
        safe_offset = max(0, int(offset))
        entries = self._manifest_repo.read_runs_index(limit=safe_limit, offset=safe_offset)
        output: list[HistoryRunIndex] = []
        for entry in entries:
            output.append(
                HistoryRunIndex(
                    run_id=str(entry.id or "").strip(),
                    archive_timestamp=float(entry.archive_timestamp),
                    archive_datetime=str(entry.archive_datetime or "").strip(),
                    reason=str(entry.reason or "").strip(),
                    target_path=str(entry.target_path or "").strip(),
                    total_size_bytes=int(entry.total_size_bytes or 0),
                    file_count=int(entry.file_count or 0),
                    content_hash=str(entry.content_hash or "").strip(),
                    status=str(entry.status or "").strip(),
                )
            )
        return output

    def get_manifest(self, scope: str, id: str) -> ArchiveManifest | None:
        """Get manifest for an archived run/snapshot.

        Args:
            scope: "run", "task_snapshot", or "factory_run"
            id: The run_id, snapshot_id, or factory_run_id

        Returns:
            ArchiveManifest or None if not found
        """
        scope_map = {
            "run": "runs",
            "task_snapshot": "tasks",
            "factory_run": "factory",
        }

        dir_name = scope_map.get(scope, scope)
        manifest_path = self.history_root / dir_name / id / "manifest.json"

        if not manifest_path.exists():
            return None

        try:
            manifest_rel = self._workspace_rel(manifest_path)
            data = json.loads(self._kernel_fs.workspace_read_text(manifest_rel, encoding="utf-8"))
            return ArchiveManifest.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read manifest: {e}")
            return None

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Get events for an archived run (auto-decompresses .zst).

        Args:
            run_id: The run ID

        Returns:
            List of event dicts
        """
        # Try compressed first, then fallback to uncompressed
        compressed_path = self.history_root / "runs" / run_id / "events" / "runtime.events.jsonl.zst"
        uncompressed_path = self.history_root / "runs" / run_id / "events" / "runtime.events.jsonl"

        events = []

        if compressed_path.exists() and ZSTD_AVAILABLE:
            try:
                compressed_rel = self._workspace_rel(compressed_path)
                compressed_payload = self._kernel_fs.workspace_read_bytes(compressed_rel)
                dctx = zstd.ZstdDecompressor()
                with dctx.stream_reader(io.BytesIO(compressed_payload)) as reader:
                    decoded = reader.read().decode("utf-8")
                for line in decoded.splitlines():
                    text = line.strip()
                    if text:
                        events.append(json.loads(text))
                return events
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, zstd.ZstdError) as e:
                logger.warning(f"Failed to decompress events: {e}")

        if uncompressed_path.exists():
            try:
                uncompressed_rel = self._workspace_rel(uncompressed_path)
                for line in self._kernel_fs.workspace_read_text(
                    uncompressed_rel,
                    encoding="utf-8",
                ).splitlines():
                    text = line.strip()
                    if text:
                        events.append(json.loads(text))
            except (OSError, RuntimeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to read events: {e}")

        return events

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _collect_run_files(self, run_dir: Path) -> list[str]:
        """Collect all files in a run directory (relative paths)."""
        files = []
        for root, _, filenames in os.walk(run_dir):
            for filename in filenames:
                full_path = Path(root) / filename
                rel_path = full_path.relative_to(run_dir)
                files.append(str(rel_path))
        return files

    def _copy_directory(self, source: Path, target: Path) -> None:
        """Copy directory recursively using atomic operation (temp + replace).

        This ensures that if the copy fails, the original data is not lost.
        """
        if target.exists():
            shutil.rmtree(target)

        # Use temporary directory for atomic copy
        temp_target = target.parent / f"{target.name}.tmp"
        try:
            shutil.copytree(source, temp_target)
            # Atomic move
            os.replace(temp_target, target)
        except (OSError, shutil.Error):
            # Clean up temp directory if it exists
            if temp_target.exists():
                shutil.rmtree(temp_target)
            raise

    def _compress_run_events(self, run_dir: Path) -> int:
        """Compress event files in a run directory.

        Returns:
            Total size of compressed files
        """
        if not ZSTD_AVAILABLE:
            return 0

        events_dir = run_dir / "events"
        if not events_dir.exists():
            return 0

        compressed_size = 0

        for jsonl_file in events_dir.glob("*.jsonl"):
            compressed_path = jsonl_file.with_suffix(".jsonl.zst")
            jsonl_rel = self._workspace_rel(jsonl_file)
            compressed_rel = self._workspace_rel(compressed_path)

            try:
                source_payload = self._kernel_fs.workspace_read_bytes(jsonl_rel)
                compressed_payload = zstd.ZstdCompressor().compress(source_payload)
                self._kernel_fs.workspace_write_bytes(compressed_rel, compressed_payload)
                compressed_size += len(compressed_payload)

                # Remove original after successful compression
                self._kernel_fs.workspace_remove(jsonl_rel, missing_ok=True)

            except (OSError, RuntimeError, ValueError, zstd.ZstdError) as e:
                logger.warning(f"Failed to compress {jsonl_file}: {e}")
                # Keep original on failure
                if self._kernel_fs.workspace_exists(compressed_rel):
                    self._kernel_fs.workspace_remove(compressed_rel, missing_ok=True)

        return compressed_size

    def _calculate_checksums(self, directory: Path) -> tuple[int, int, str]:
        """Calculate total size, file count, and content hash.

        Returns:
            Tuple of (total_size_bytes, file_count, content_hash)
        """
        total_size = 0
        file_count = 0
        hash_obj = hashlib.sha256()

        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                filepath = Path(root) / filename
                if filepath.is_file():
                    file_count += 1
                    total_size += filepath.stat().st_size

                    rel = self._workspace_rel(filepath)
                    hash_obj.update(self._kernel_fs.workspace_read_bytes(rel))

        return total_size, file_count, hash_obj.hexdigest()

    def _append_to_index(self, index_name: str, entry: dict[str, Any]) -> None:
        """Append an entry to an index file using atomic write.

        Uses HistoryManifestRepository for safe concurrent writes.
        """
        archive_timestamp = float(entry.get("archive_timestamp") or time.time())
        archive_datetime = str(entry.get("archive_datetime") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        target_path = str(entry.get("target_path") or "").strip()
        total_size_bytes = int(entry.get("total_size_bytes") or 0)
        file_count = int(entry.get("file_count") or 0)

        # Convert entry dict to repository index type
        if index_name == "runs":
            from polaris.cells.archive.run_archive.internal.history_manifest_repository import RunIndexEntry

            run_entry = RunIndexEntry(
                id=str(entry.get("run_id") or entry.get("id") or "").strip(),
                archive_timestamp=archive_timestamp,
                archive_datetime=archive_datetime,
                reason=reason,
                target_path=target_path,
                total_size_bytes=total_size_bytes,
                file_count=file_count,
                content_hash=str(entry.get("content_hash") or "").strip(),
                status=str(entry.get("status") or "").strip(),
            )
            self._manifest_repo.append_run_entry(run_entry)
        elif index_name == "tasks":
            from polaris.cells.archive.run_archive.internal.history_manifest_repository import TaskIndexEntry

            task_entry = TaskIndexEntry(
                id=str(entry.get("snapshot_id") or entry.get("id") or "").strip(),
                snapshot_id=str(entry.get("snapshot_id") or entry.get("id") or "").strip(),
                archive_timestamp=archive_timestamp,
                archive_datetime=archive_datetime,
                reason=reason,
                target_path=target_path,
                total_size_bytes=total_size_bytes,
                file_count=file_count,
            )
            self._manifest_repo.append_task_entry(task_entry)
        elif index_name == "factory":
            from polaris.cells.archive.run_archive.internal.history_manifest_repository import FactoryIndexEntry

            factory_entry = FactoryIndexEntry(
                id=str(entry.get("factory_run_id") or entry.get("id") or "").strip(),
                factory_run_id=str(entry.get("factory_run_id") or entry.get("id") or "").strip(),
                archive_timestamp=archive_timestamp,
                archive_datetime=archive_datetime,
                reason=reason,
                target_path=target_path,
                total_size_bytes=total_size_bytes,
                file_count=file_count,
            )
            self._manifest_repo.append_factory_entry(factory_entry)
        else:
            # Fallback to original non-atomic write for unknown index
            logger.warning(f"Unknown index name: {index_name}, using non-atomic write")
            index_path = self.index_dir / f"{index_name}.index.jsonl"
            index_rel = self._workspace_rel(index_path)
            self._kernel_fs.workspace_append_text(
                index_rel,
                json.dumps(entry, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    def _create_empty_manifest(self, scope: str, id: str, reason: str) -> ArchiveManifest:
        """Create an empty manifest for non-existent sources."""
        now = time.time()
        return ArchiveManifest(
            scope=scope,
            id=id,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.runtime_root),
            source_paths=[],
            target_path="",
            total_size_bytes=0,
            file_count=0,
            content_hash="",
            reason=reason,
            compressed=False,
            compression_ratio=1.0,
        )


def create_history_archive_service(workspace: str) -> HistoryArchiveService:
    """Factory function to create a HistoryArchiveService.

    Args:
        workspace: Workspace root path

    Returns:
        Configured HistoryArchiveService instance
    """
    return HistoryArchiveService(workspace)


__all__ = [
    "ZSTD_AVAILABLE",
    "ArchiveManifest",
    "HistoryArchiveService",
    "HistoryRunIndex",
    "create_history_archive_service",
]
