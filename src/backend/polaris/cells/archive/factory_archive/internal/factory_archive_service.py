"""Factory archive service.

Owns factory run archival writes for ``archive.factory_archive`` cell.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.archive.run_archive.public.service import (
    FactoryIndexEntry,
    HistoryManifestRepository,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)


@dataclass
class FactoryArchiveManifest:
    """Manifest for one archived factory run."""

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
    def from_dict(cls, payload: dict[str, Any]) -> FactoryArchiveManifest:
        return cls(**payload)


class FactoryArchiveService:
    """Cell-local archival service for factory runs."""

    def __init__(self, workspace: str) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        self.workspace = Path(workspace_token).resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())

        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(str(self.workspace))
        self.history_root = Path(roots.history_root)
        self.workspace_persistent_root = Path(roots.workspace_persistent_root)
        self.index_dir = self.history_root / "index"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_repo = HistoryManifestRepository(str(self.workspace))

    def archive_factory_run(
        self,
        factory_run_id: str,
        source_factory_dir: str | None = None,
        reason: str = "completed",
    ) -> FactoryArchiveManifest:
        """Archive factory run files into ``workspace/history/factory``."""
        run_token = str(factory_run_id or "").strip()
        if not run_token:
            raise ValueError("factory_run_id is required")
        source_dir = (
            Path(source_factory_dir).resolve()
            if source_factory_dir
            else self.workspace_persistent_root / "factory" / run_token
        )
        target_dir = self.history_root / "factory" / run_token

        if not source_dir.exists():
            logger.warning("Factory archive source missing: %s", source_dir)
            return self._create_empty_manifest(run_token, reason)

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        self._copy_directory(source_dir, target_dir)
        total_size, file_count, content_hash = self._calculate_checksums(target_dir)
        now = time.time()

        manifest = FactoryArchiveManifest(
            scope="factory_run",
            id=run_token,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.workspace_persistent_root),
            source_paths=[self._safe_rel(source_dir, self.workspace_persistent_root)],
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

        self._manifest_repo.append_factory_entry(
            FactoryIndexEntry(
                id=run_token,
                factory_run_id=run_token,
                archive_timestamp=manifest.archive_timestamp,
                archive_datetime=manifest.archive_datetime,
                reason=manifest.reason,
                target_path=manifest.target_path,
                total_size_bytes=manifest.total_size_bytes,
                file_count=manifest.file_count,
            )
        )
        logger.info("Archived factory run %s into %s", run_token, target_dir)
        return manifest

    def get_manifest(self, factory_run_id: str) -> FactoryArchiveManifest | None:
        """Load factory archive manifest by run ID."""
        run_token = str(factory_run_id or "").strip()
        if not run_token:
            return None
        manifest_path = self.history_root / "factory" / run_token / "manifest.json"
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
            logger.warning("Failed to load factory manifest for %s: %s", run_token, exc)
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return FactoryArchiveManifest.from_dict(payload)
        except OSError as exc:
            logger.warning("Invalid factory manifest for %s: %s", run_token, exc)
            return None

    def list_factory_runs(self, limit: int = 50, offset: int = 0) -> list[FactoryIndexEntry]:
        """List archived factory runs from canonical index."""
        safe_limit = max(0, int(limit))
        safe_offset = max(0, int(offset))
        return self._manifest_repo.read_factory_index(limit=safe_limit, offset=safe_offset)

    def _workspace_rel(self, path: Path) -> str:
        return self._kernel_fs.to_workspace_relative_path(str(path))

    @staticmethod
    def _safe_rel(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except (ValueError, OSError):
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
        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                file_path = Path(root) / filename
                if not file_path.is_file():
                    continue
                file_count += 1
                total_size += file_path.stat().st_size
                hash_obj.update(self._kernel_fs.workspace_read_bytes(self._workspace_rel(file_path)))
        return total_size, file_count, hash_obj.hexdigest()

    def _create_empty_manifest(
        self,
        factory_run_id: str,
        reason: str,
    ) -> FactoryArchiveManifest:
        now = time.time()
        return FactoryArchiveManifest(
            scope="factory_run",
            id=factory_run_id,
            archive_timestamp=now,
            archive_datetime=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            source_runtime_root=str(self.workspace_persistent_root),
            source_paths=[],
            target_path="",
            total_size_bytes=0,
            file_count=0,
            content_hash="",
            reason=str(reason or "").strip() or "completed",
            compressed=False,
            compression_ratio=1.0,
        )


__all__ = [
    "FactoryArchiveManifest",
    "FactoryArchiveService",
]
