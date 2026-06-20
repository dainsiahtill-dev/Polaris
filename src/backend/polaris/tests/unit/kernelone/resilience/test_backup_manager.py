"""Tests for polaris.kernelone.resilience.backup_manager.

Covers the async create/restore/list/delete roundtrip plus the requirement
that blocking filesystem IO is offloaded off the event loop via
``asyncio.to_thread`` (ASYNC-8).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.kernelone.resilience.backup_manager import BackupManager


@pytest.fixture
def manager(tmp_path: Path) -> BackupManager:
    """Provide a BackupManager rooted in an isolated temp directory."""
    return BackupManager(backup_dir=str(tmp_path / "backups"))


class TestBackupRoundtrip:
    async def test_create_then_restore_returns_original_data(self, manager: BackupManager) -> None:
        # Arrange
        payload = {"alpha": 1, "nested": {"unicode": "汉字"}}

        # Act
        metadata = await manager.create_backup(payload, replica_id="r1")
        restored = await manager.restore_backup(metadata.backup_id)

        # Assert
        assert restored == payload
        assert metadata.replica_id == "r1"
        assert metadata.size_bytes > 0

    async def test_create_persists_backup_file_as_utf8(self, manager: BackupManager, tmp_path: Path) -> None:
        # Arrange
        payload = {"value": "汉字"}

        # Act
        metadata = await manager.create_backup(payload, replica_id="r1")

        # Assert
        backup_file = tmp_path / "backups" / f"{metadata.backup_id}.json"
        on_disk = json.loads(backup_file.read_text(encoding="utf-8"))
        assert on_disk == payload


class TestRestoreErrors:
    async def test_restore_invalid_uuid_raises_value_error(self, manager: BackupManager) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="Invalid backup_id format"):
            await manager.restore_backup("not-a-uuid")

    async def test_restore_unknown_backup_raises_file_not_found(self, manager: BackupManager) -> None:
        # Arrange
        unknown_id = "00000000-0000-4000-8000-000000000000"

        # Act / Assert
        with pytest.raises(FileNotFoundError):
            await manager.restore_backup(unknown_id)

    async def test_restore_corrupted_payload_raises_value_error(self, manager: BackupManager, tmp_path: Path) -> None:
        # Arrange
        metadata = await manager.create_backup({"k": "v"}, replica_id="r1")
        backup_file = tmp_path / "backups" / f"{metadata.backup_id}.json"
        backup_file.write_text("{ this is not json", encoding="utf-8")

        # Act / Assert
        with pytest.raises(ValueError, match="corrupted"):
            await manager.restore_backup(metadata.backup_id)


class TestListAndDelete:
    async def test_list_backups_filters_by_replica(self, manager: BackupManager) -> None:
        # Arrange
        await manager.create_backup({"a": 1}, replica_id="r1")
        await manager.create_backup({"b": 2}, replica_id="r2")

        # Act
        only_r1 = await manager.list_backups(replica_id="r1")
        every = await manager.list_backups()

        # Assert
        assert len(only_r1) == 1
        assert only_r1[0].replica_id == "r1"
        assert len(every) == 2

    async def test_delete_backup_removes_file_and_metadata(self, manager: BackupManager, tmp_path: Path) -> None:
        # Arrange
        metadata = await manager.create_backup({"k": "v"}, replica_id="r1")
        backup_file = tmp_path / "backups" / f"{metadata.backup_id}.json"
        assert backup_file.exists()

        # Act
        deleted = await manager.delete_backup(metadata.backup_id)

        # Assert
        assert deleted is True
        assert not backup_file.exists()
        with pytest.raises(FileNotFoundError):
            await manager.restore_backup(metadata.backup_id)

    async def test_delete_invalid_uuid_returns_false(self, manager: BackupManager) -> None:
        # Arrange / Act
        result = await manager.delete_backup("not-a-uuid")

        # Assert
        assert result is False


class TestBlockingIoIsOffloaded:
    """ASYNC-8: blocking filesystem IO must be dispatched via asyncio.to_thread."""

    async def test_create_backup_offloads_write_and_save(self, manager: BackupManager) -> None:
        # Arrange
        with patch(
            "polaris.kernelone.resilience.backup_manager.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as to_thread:
            # Act
            await manager.create_backup({"k": "v"}, replica_id="r1")

        # Assert: file write and metadata save are both offloaded.
        offloaded = {call.args[0].__name__ for call in to_thread.call_args_list}
        assert "_write_backup_file" in offloaded
        assert "_save_metadata" in offloaded

    async def test_restore_backup_offloads_read(self, manager: BackupManager) -> None:
        # Arrange
        metadata = await manager.create_backup({"k": "v"}, replica_id="r1")
        with patch(
            "polaris.kernelone.resilience.backup_manager.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as to_thread:
            # Act
            await manager.restore_backup(metadata.backup_id)

        # Assert
        offloaded = {call.args[0].__name__ for call in to_thread.call_args_list}
        assert "_read_backup_file" in offloaded
