from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from polaris.kernelone.resilience.backup_manager import (
    BackupManager,
    BackupMetadata,
)


@pytest.fixture
def temp_backup_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for backup tests."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def backup_manager(temp_backup_dir: Path) -> BackupManager:
    """Create a BackupManager with temporary directory."""
    return BackupManager(backup_dir=str(temp_backup_dir))


@pytest.fixture
def sample_data() -> dict[str, object]:
    """Create sample data for backup testing."""
    return {
        "users": [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ],
        "settings": {"theme": "dark", "language": "en"},
        "version": "1.0.0",
    }


class TestBackupCreation:
    """Test backup creation functionality."""

    @pytest.mark.asyncio
    async def test_create_backup(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test creating a backup."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")

        assert metadata.backup_id is not None
        assert metadata.replica_id == "replica-1"
        assert metadata.size_bytes > 0
        assert metadata.checksum is not None
        assert metadata.timestamp is not None

    @pytest.mark.asyncio
    async def test_create_backup_file_exists(
        self, backup_manager: BackupManager, sample_data: dict[str, object]
    ) -> None:
        """Test that backup file is created on disk."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")

        backup_file = backup_manager._backup_dir / f"{metadata.backup_id}.json"
        assert backup_file.exists()

    @pytest.mark.asyncio
    async def test_create_backup_content(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test that backup file contains correct data."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")

        backup_file = backup_manager._backup_dir / f"{metadata.backup_id}.json"
        with open(backup_file, encoding="utf-8") as f:
            stored_data = json.load(f)

        assert stored_data == sample_data

    @pytest.mark.asyncio
    async def test_create_multiple_backups(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test creating multiple backups."""
        meta1 = await backup_manager.create_backup(sample_data, "replica-1")
        meta2 = await backup_manager.create_backup(sample_data, "replica-2")

        assert meta1.backup_id != meta2.backup_id
        assert meta1.replica_id == "replica-1"
        assert meta2.replica_id == "replica-2"


class TestBackupRestoration:
    """Test backup restoration functionality."""

    @pytest.mark.asyncio
    async def test_restore_backup(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test restoring a backup."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")
        restored = await backup_manager.restore_backup(metadata.backup_id)

        assert restored == sample_data

    @pytest.mark.asyncio
    async def test_restore_backup_checksum_verification(
        self, backup_manager: BackupManager, sample_data: dict[str, object]
    ) -> None:
        """Test that checksum is verified on restore."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")

        # Corrupt the backup file
        backup_file = backup_manager._backup_dir / f"{metadata.backup_id}.json"
        with open(backup_file, "w", encoding="utf-8") as f:
            f.write("corrupted data")

        with pytest.raises(ValueError, match="is corrupted"):
            await backup_manager.restore_backup(metadata.backup_id)

    @pytest.mark.asyncio
    async def test_restore_nonexistent_backup(self, backup_manager: BackupManager) -> None:
        """Test restoring a nonexistent backup."""
        with pytest.raises(ValueError, match="Invalid backup_id format"):
            await backup_manager.restore_backup("nonexistent-id")

    @pytest.mark.asyncio
    async def test_restore_valid_uuid_nonexistent_backup(self, backup_manager: BackupManager) -> None:
        """Test restoring with valid UUID format but non-existent ID."""
        import uuid

        with pytest.raises(FileNotFoundError):
            await backup_manager.restore_backup(str(uuid.uuid4()))


class TestBackupListing:
    """Test backup listing functionality."""

    @pytest.mark.asyncio
    async def test_list_backups_all(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test listing all backups."""
        await backup_manager.create_backup(sample_data, "replica-1")
        await backup_manager.create_backup(sample_data, "replica-2")
        await backup_manager.create_backup(sample_data, "replica-1")

        backups = await backup_manager.list_backups()
        assert len(backups) == 3

    @pytest.mark.asyncio
    async def test_list_backups_by_replica(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test listing backups filtered by replica."""
        await backup_manager.create_backup(sample_data, "replica-1")
        await backup_manager.create_backup(sample_data, "replica-1")
        await backup_manager.create_backup(sample_data, "replica-2")

        backups = await backup_manager.list_backups(replica_id="replica-1")
        assert len(backups) == 2
        assert all(b.replica_id == "replica-1" for b in backups)

    @pytest.mark.asyncio
    async def test_list_backups_empty(self, backup_manager: BackupManager) -> None:
        """Test listing backups when none exist."""
        backups = await backup_manager.list_backups()
        assert len(backups) == 0


class TestBackupDeletion:
    """Test backup deletion functionality."""

    @pytest.mark.asyncio
    async def test_delete_backup(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test deleting a backup."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")
        result = await backup_manager.delete_backup(metadata.backup_id)

        assert result is True
        backups = await backup_manager.list_backups()
        assert len(backups) == 0

    @pytest.mark.asyncio
    async def test_delete_backup_file_removed(
        self, backup_manager: BackupManager, sample_data: dict[str, object]
    ) -> None:
        """Test that backup file is actually removed."""
        metadata = await backup_manager.create_backup(sample_data, "replica-1")
        backup_file = backup_manager._backup_dir / f"{metadata.backup_id}.json"
        assert backup_file.exists()

        await backup_manager.delete_backup(metadata.backup_id)
        assert not backup_file.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_backup(self, backup_manager: BackupManager) -> None:
        """Test deleting a nonexistent backup."""
        result = await backup_manager.delete_backup("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_updates_metadata(self, backup_manager: BackupManager, sample_data: dict[str, object]) -> None:
        """Test that metadata is updated after deletion."""
        meta1 = await backup_manager.create_backup(sample_data, "replica-1")
        meta2 = await backup_manager.create_backup(sample_data, "replica-1")

        await backup_manager.delete_backup(meta1.backup_id)

        backups = await backup_manager.list_backups()
        assert len(backups) == 1
        assert backups[0].backup_id == meta2.backup_id


class TestBackupMetadata:
    """Test backup metadata functionality."""

    @pytest.mark.asyncio
    async def test_metadata_persistence(self, temp_backup_dir: Path, sample_data: dict[str, object]) -> None:
        """Test that metadata persists across manager instances."""
        manager1 = BackupManager(backup_dir=str(temp_backup_dir))
        metadata = await manager1.create_backup(sample_data, "replica-1")

        # Create a new manager instance with same directory
        manager2 = BackupManager(backup_dir=str(temp_backup_dir))
        backups = await manager2.list_backups()

        assert len(backups) == 1
        assert backups[0].backup_id == metadata.backup_id

    def test_backup_metadata_immutable(self) -> None:
        """Test that BackupMetadata is immutable."""
        metadata = BackupMetadata(
            backup_id="test-id",
            timestamp="1234567890",
            size_bytes=100,
            checksum="abc123",
            replica_id="replica-1",
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            metadata.backup_id = "changed"  # type: ignore
