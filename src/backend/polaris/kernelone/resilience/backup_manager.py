from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name


def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


def _safe_backup_path(backup_dir: Path, backup_id: str) -> Path:
    resolved_dir = backup_dir.resolve()
    backup_file = (backup_dir / f"{backup_id}.json").resolve()
    if not str(backup_file).startswith(str(resolved_dir)):
        raise ValueError(f"Invalid backup_id: {backup_id}")
    return backup_file


@dataclass(frozen=True)
class BackupMetadata:
    """Metadata for a backup."""

    backup_id: str
    timestamp: str
    size_bytes: int
    checksum: str
    replica_id: str


@dataclass
class BackupManager:
    """Manages backup creation and restoration."""

    def __init__(self, backup_dir: str | None = None) -> None:
        if backup_dir is None:
            metadata_dir = get_workspace_metadata_dir_name()
            backup_dir = f"{metadata_dir}/backups"
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_file = self._backup_dir / "metadata.json"
        self._backups: dict[str, BackupMetadata] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load backup metadata from disk."""
        if self._metadata_file.exists():
            try:
                with open(self._metadata_file, encoding="utf-8") as f:
                    data = json.load(f)
                    for backup_id, meta in data.items():
                        if _is_valid_uuid(backup_id):
                            self._backups[backup_id] = BackupMetadata(**meta)
            except (json.JSONDecodeError, TypeError, KeyError):
                self._backups = {}

    def _save_metadata(self) -> None:
        """Save backup metadata to disk."""
        data = {
            backup_id: {
                "backup_id": meta.backup_id,
                "timestamp": meta.timestamp,
                "size_bytes": meta.size_bytes,
                "checksum": meta.checksum,
                "replica_id": meta.replica_id,
            }
            for backup_id, meta in self._backups.items()
        }
        with open(self._metadata_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _compute_checksum(self, data: bytes) -> str:
        """Compute SHA256 checksum of data."""
        return hashlib.sha256(data).hexdigest()

    async def create_backup(
        self,
        data: dict[str, Any],
        replica_id: str,
    ) -> BackupMetadata:
        """Create a backup of the given data."""
        json_data = json.dumps(data, ensure_ascii=False, sort_keys=True)
        json_bytes = json_data.encode("utf-8")

        backup_id = str(uuid.uuid4())
        timestamp = str(__import__("time").time())
        size_bytes = len(json_bytes)
        checksum = self._compute_checksum(json_bytes)

        backup_file = self._backup_dir / f"{backup_id}.json"
        with open(backup_file, "w", encoding="utf-8") as f:
            f.write(json_data)

        metadata = BackupMetadata(
            backup_id=backup_id,
            timestamp=timestamp,
            size_bytes=size_bytes,
            checksum=checksum,
            replica_id=replica_id,
        )
        self._backups[backup_id] = metadata
        self._save_metadata()

        return metadata

    async def restore_backup(
        self,
        backup_id: str,
    ) -> dict[str, Any]:
        """Restore data from a backup."""
        if not _is_valid_uuid(backup_id):
            raise ValueError(f"Invalid backup_id format: {backup_id}")
        if backup_id not in self._backups:
            raise FileNotFoundError(f"Backup {backup_id} not found")

        backup_file = _safe_backup_path(self._backup_dir, backup_id)
        if not backup_file.exists():
            raise FileNotFoundError(f"Backup file {backup_id}.json not found")

        try:
            with open(backup_file, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Backup {backup_id} is corrupted: {e}") from e

        json_data = json.dumps(data, ensure_ascii=False, sort_keys=True)
        json_bytes = json_data.encode("utf-8")
        computed_checksum = self._compute_checksum(json_bytes)
        expected_checksum = self._backups[backup_id].checksum

        if computed_checksum != expected_checksum:
            raise ValueError(f"Backup {backup_id} checksum mismatch")

        return data

    async def list_backups(
        self,
        replica_id: str | None = None,
    ) -> list[BackupMetadata]:
        """List available backups."""
        if replica_id is None:
            return list(self._backups.values())

        return [meta for meta in self._backups.values() if meta.replica_id == replica_id]

    async def delete_backup(self, backup_id: str) -> bool:
        """Delete a backup."""
        if not _is_valid_uuid(backup_id):
            return False
        if backup_id not in self._backups:
            return False

        backup_file = _safe_backup_path(self._backup_dir, backup_id)
        if backup_file.exists():
            backup_file.unlink()

        del self._backups[backup_id]
        self._save_metadata()
        return True
