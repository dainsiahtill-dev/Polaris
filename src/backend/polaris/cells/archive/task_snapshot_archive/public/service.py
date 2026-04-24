"""Public service exports for `archive.task_snapshot_archive` cell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.archive.run_archive.public.service import (
    HistoryManifestRepository,
    TaskIndexEntry,
)
from polaris.cells.archive.task_snapshot_archive.public.contracts import (
    ArchiveManifestV1,
    ArchiveTaskSnapshotCommandV1,
    GetTaskSnapshotManifestQueryV1,
    TaskSnapshotArchivedEventV1,
    TaskSnapshotArchiveError,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.storage import resolve_storage_roots

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)


@dataclass
class TaskSnapshotArchiveManifest:
    """Manifest for one archived task snapshot."""

    scope: str
    id: str
    archive_timestamp: float
    archive_datetime: str
    source_runtime_root: str
    source_paths: list[str]
    target_path: str
    total_size_bytes: int
    file_count: int
    content_hash: str
    reason: str
    compressed: bool = False
    compression_ratio: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskSnapshotArchiveManifest:
        return cls(**payload)


class TaskSnapshotArchiveService:
    """Cell-local archival service for runtime task snapshots."""

    def __init__(self, workspace: str) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        self.workspace = Path(workspace_token).resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())
        roots = resolve_storage_roots(str(self.workspace))
        self.runtime_root = Path(roots.runtime_root)
        self.history_root = Path(roots.history_root)
        self.index_dir = self.history_root / "index"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_repo = HistoryManifestRepository(str(self.workspace))

    def archive_task_snapshot(
        self,
        snapshot_id: str,
        source_tasks_dir: str | None = None,
        source_plan_path: str | None = None,
        reason: str = "completed",
    ) -> TaskSnapshotArchiveManifest:
        """Archive task snapshot files into ``workspace/history/tasks``."""
        snapshot_token = str(snapshot_id or "").strip()
        if not snapshot_token:
            raise ValueError("snapshot_id is required")
        source_dir = Path(source_tasks_dir).resolve() if source_tasks_dir else self.runtime_root / "tasks"
        target_dir = self.history_root / "tasks" / snapshot_token

        if not source_dir.exists():
            logger.warning("Task snapshot source missing: %s", source_dir)
            return self._create_empty_manifest(snapshot_token, reason)

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        self._copy_directory(source_dir, target_dir)

        plan_token = str(source_plan_path or "").strip()
        if plan_token:
            plan_src = Path(plan_token).resolve()
            if plan_src.exists() and plan_src.is_file():
                shutil.copy2(plan_src, target_dir / "plan.json")

        total_size, file_count, content_hash = self._calculate_checksums(target_dir)
        now = time.time()
        manifest = TaskSnapshotArchiveManifest(
            scope="task_snapshot",
            id=snapshot_token,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.runtime_root),
            source_paths=[self._safe_rel(source_dir, self.runtime_root)],
            target_path=self._safe_rel(target_dir, self.history_root),
            total_size_bytes=total_size,
            file_count=file_count,
            content_hash=content_hash,
            reason=str(reason or "").strip() or "completed",
            compressed=False,
            compression_ratio=1.0,
        )

        manifest_path = target_dir / "manifest.json"
        self._kernel_fs.workspace_write_text(
            self._workspace_rel(manifest_path),
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self._manifest_repo.append_task_entry(
            TaskIndexEntry(
                id=snapshot_token,
                snapshot_id=snapshot_token,
                archive_timestamp=manifest.archive_timestamp,
                archive_datetime=manifest.archive_datetime,
                reason=manifest.reason,
                target_path=manifest.target_path,
                total_size_bytes=manifest.total_size_bytes,
                file_count=manifest.file_count,
            )
        )
        logger.info("Archived task snapshot %s into %s", snapshot_token, target_dir)
        return manifest

    def get_manifest(self, snapshot_id: str) -> TaskSnapshotArchiveManifest | None:
        """Load task snapshot manifest by snapshot ID."""
        snapshot_token = str(snapshot_id or "").strip()
        if not snapshot_token:
            return None
        manifest_path = self.history_root / "tasks" / snapshot_token / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            payload = json.loads(
                self._kernel_fs.workspace_read_text(
                    self._workspace_rel(manifest_path),
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            logger.warning("Failed to load task snapshot manifest for %s: %s", snapshot_token, exc)
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return TaskSnapshotArchiveManifest.from_dict(payload)
        except OSError as exc:
            logger.warning("Invalid task snapshot manifest for %s: %s", snapshot_token, exc)
            return None

    def list_task_snapshots(self, limit: int = 50, offset: int = 0) -> list[TaskIndexEntry]:
        """List archived task snapshots from canonical index."""
        safe_limit = max(0, int(limit))
        safe_offset = max(0, int(offset))
        return self._manifest_repo.read_tasks_index(limit=safe_limit, offset=safe_offset)

    def _workspace_rel(self, path: Path) -> str:
        return self._kernel_fs.to_workspace_relative_path(str(path))

    @staticmethod
    def _safe_rel(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except OSError:
            return str(path)

    @staticmethod
    def _copy_directory(source: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target)
        temp_target = target.parent / f"{target.name}.tmp"
        try:
            shutil.copytree(source, temp_target)
            os.replace(temp_target, target)
        finally:
            if temp_target.exists():
                shutil.rmtree(temp_target, ignore_errors=True)

    def _calculate_checksums(self, directory: Path) -> tuple[int, int, str]:
        total_size = 0
        file_count = 0
        hash_obj = hashlib.sha256()
        for root_dir, _, filenames in os.walk(directory):
            for filename in filenames:
                file_path = Path(root_dir) / filename
                if not file_path.is_file():
                    continue
                file_count += 1
                total_size += file_path.stat().st_size
                hash_obj.update(self._kernel_fs.workspace_read_bytes(self._workspace_rel(file_path)))
        return total_size, file_count, hash_obj.hexdigest()

    def _create_empty_manifest(self, snapshot_id: str, reason: str) -> TaskSnapshotArchiveManifest:
        now = time.time()
        return TaskSnapshotArchiveManifest(
            scope="task_snapshot",
            id=snapshot_id,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.runtime_root),
            source_paths=[],
            target_path="",
            total_size_bytes=0,
            file_count=0,
            content_hash="",
            reason=str(reason or "").strip() or "completed",
            compressed=False,
            compression_ratio=1.0,
        )


def _run_background(coro: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine in background, handling both running loop and no-loop contexts."""
    try:
        loop = asyncio.get_running_loop()
        _ = loop.create_task(coro)  # noqa: RUF006
        return
    except RuntimeError:
        pass

    def _runner() -> None:
        try:
            asyncio.run(coro)
        except OSError:
            logger.warning("Background task snapshot archive failed", exc_info=True)

    threading.Thread(target=_runner, daemon=True).start()


def create_task_snapshot_archive_service(workspace: str) -> TaskSnapshotArchiveService:
    return TaskSnapshotArchiveService(workspace)


def archive_task_snapshot(
    workspace: str,
    snapshot_id: str,
    *,
    source_tasks_dir: str | None = None,
    source_plan_path: str | None = None,
    reason: str = "completed",
) -> dict[str, Any]:
    service = create_task_snapshot_archive_service(workspace)
    manifest = service.archive_task_snapshot(
        snapshot_id=snapshot_id,
        source_tasks_dir=source_tasks_dir,
        source_plan_path=source_plan_path,
        reason=reason,
    )
    return manifest.to_dict()


def trigger_task_snapshot_archive(
    workspace: str,
    snapshot_id: str,
    *,
    source_tasks_dir: str | None = None,
    source_plan_path: str | None = None,
    reason: str = "completed",
) -> None:
    async def _do_archive() -> None:
        archive_task_snapshot(
            workspace=workspace,
            snapshot_id=snapshot_id,
            source_tasks_dir=source_tasks_dir,
            source_plan_path=source_plan_path,
            reason=reason,
        )

    _run_background(_do_archive())


def get_task_snapshot_manifest(workspace: str, snapshot_id: str) -> dict[str, Any] | None:
    service = create_task_snapshot_archive_service(workspace)
    manifest = service.get_manifest(snapshot_id)
    return manifest.to_dict() if manifest else None


def list_task_snapshots(
    workspace: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    service = create_task_snapshot_archive_service(workspace)
    return [entry.to_dict() for entry in service.list_task_snapshots(limit=limit, offset=offset)]


__all__ = [
    "ArchiveManifestV1",
    "ArchiveTaskSnapshotCommandV1",
    "GetTaskSnapshotManifestQueryV1",
    "TaskSnapshotArchiveError",
    "TaskSnapshotArchiveManifest",
    "TaskSnapshotArchiveService",
    "TaskSnapshotArchivedEventV1",
    "archive_task_snapshot",
    "create_task_snapshot_archive_service",
    "get_task_snapshot_manifest",
    "list_task_snapshots",
    "trigger_task_snapshot_archive",
]
