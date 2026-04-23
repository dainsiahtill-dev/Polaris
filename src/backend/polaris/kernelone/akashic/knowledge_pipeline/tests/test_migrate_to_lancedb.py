"""Tests for migrate_to_lancedb module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.migrate_to_lancedb import (
    MigrationStats,
    SemanticMemoryToLanceDBMigrator,
)


class TestMigrationStats:
    """Tests for MigrationStats dataclass."""

    def test_migration_stats_default_values(self) -> None:
        """MigrationStats has correct defaults."""
        stats = MigrationStats()
        assert stats.total_items == 0
        assert stats.migrated_items == 0
        assert stats.skipped_items == 0
        assert stats.failed_items == 0
        assert stats.errors == []

    def test_migration_stats_with_values(self) -> None:
        """MigrationStats can be constructed with values."""
        stats = MigrationStats(
            total_items=100,
            migrated_items=95,
            skipped_items=3,
            failed_items=2,
            errors=["Error 1", "Error 2"],
        )
        assert stats.total_items == 100
        assert stats.migrated_items == 95
        assert stats.skipped_items == 3
        assert stats.failed_items == 2
        assert len(stats.errors) == 2

    def test_migration_stats_to_dict(self) -> None:
        """MigrationStats can be serialized to dict."""
        stats = MigrationStats(
            total_items=50,
            migrated_items=45,
            skipped_items=3,
            failed_items=2,
            errors=["Error 1"],
        )
        result = stats.to_dict()

        assert isinstance(result, dict)
        assert result["total_items"] == 50
        assert result["migrated_items"] == 45
        assert result["skipped_items"] == 3
        assert result["failed_items"] == 2
        assert result["errors"] == ["Error 1"]


class TestSemanticMemoryToLanceDBMigrator:
    """Tests for SemanticMemoryToLanceDBMigrator class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def memory_file(self, temp_dir):
        """Create a temporary memory JSONL file."""
        memory_path = temp_dir / "memory.jsonl"
        # Create test data
        items = [
            {
                "memory_id": "mem_001",
                "text": "First memory item",
                "importance": 7,
                "metadata": {"source_file": "test.py", "line_start": 1, "line_end": 10},
            },
            {
                "memory_id": "mem_002",
                "text": "Second memory item",
                "importance": 5,
                "metadata": {},
            },
            {
                "memory_id": "mem_003",
                "text": "Third memory item with embedding",
                "importance": 8,
                "embedding": [0.1] * 384,
                "metadata": {"source_file": "another.py", "line_start": 20, "line_end": 30},
            },
        ]
        with open(memory_path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item) + "\n")
        return memory_path

    @pytest.fixture
    def migrator(self, temp_dir, memory_file):
        """Create a migrator instance."""
        return SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )

    def test_init_with_workspace(self, temp_dir) -> None:
        """Migrator initializes with workspace path."""
        migrator = SemanticMemoryToLanceDBMigrator(workspace=str(temp_dir))
        assert migrator._workspace == Path(temp_dir)
        assert migrator._dry_run is False

    def test_init_with_memory_file(self, temp_dir, memory_file) -> None:
        """Migrator accepts explicit memory file path."""
        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )
        assert migrator._memory_file == Path(memory_file)

    def test_init_dry_run_mode(self, temp_dir) -> None:
        """Migrator respects dry_run flag."""
        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            dry_run=True,
        )
        assert migrator._dry_run is True

    def test_load_jsonl_items_success(self, migrator) -> None:
        """_load_jsonl_items parses JSONL correctly."""
        items = migrator._load_jsonl_items()
        assert len(items) == 3
        assert items[0]["memory_id"] == "mem_001"
        assert items[1]["text"] == "Second memory item"
        assert items[2]["importance"] == 8

    def test_load_jsonl_items_missing_file(self, temp_dir) -> None:
        """_load_jsonl_items handles missing file gracefully."""
        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(temp_dir / "nonexistent.jsonl"),
        )
        items = migrator._load_jsonl_items()
        assert items == []

    def test_load_jsonl_items_invalid_json(self, temp_dir) -> None:
        """_load_jsonl_items skips invalid JSON lines."""
        bad_file = temp_dir / "bad.jsonl"
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write('{"valid": true}\n')
            f.write('{"invalid: json\n')  # Invalid JSON
            f.write('{"another": "valid"}\n')

        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(bad_file),
        )
        items = migrator._load_jsonl_items()
        assert len(items) == 2

    def test_load_jsonl_items_empty_file(self, temp_dir) -> None:
        """_load_jsonl_items handles empty file."""
        empty_file = temp_dir / "empty.jsonl"
        empty_file.touch()

        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(empty_file),
        )
        items = migrator._load_jsonl_items()
        assert items == []

    @pytest.mark.asyncio
    async def test_migrate_item_success(self, migrator) -> None:
        """_migrate_item migrates valid item."""
        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(return_value="hash123")

        item = {
            "memory_id": "test_mem",
            "text": "Test content",
            "importance": 7,
            "metadata": {"source_file": "test.py", "line_start": 1, "line_end": 5},
        }

        success, message = await migrator._migrate_item(item, mock_lancedb)

        assert success is True
        assert message == "Migrated"
        mock_lancedb.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_migrate_item_empty_text(self, migrator) -> None:
        """_migrate_item skips items with empty text."""
        mock_lancedb = AsyncMock()

        item = {"memory_id": "test_mem", "text": "", "importance": 5}

        success, message = await migrator._migrate_item(item, mock_lancedb)

        assert success is False
        assert "Empty text" in message
        mock_lancedb.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_migrate_item_with_embedding(self, migrator) -> None:
        """_migrate_item uses provided embedding."""
        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(return_value="hash123")

        item = {
            "memory_id": "test_mem",
            "text": "Test content",
            "importance": 7,
            "embedding": [0.1] * 384,
        }

        success, _ = await migrator._migrate_item(item, mock_lancedb)

        assert success is True
        call_args = mock_lancedb.add.call_args
        assert call_args.kwargs["embedding"] == [0.1] * 384

    @pytest.mark.asyncio
    async def test_migrate_item_without_embedding(self, migrator) -> None:
        """_migrate_item uses zero vector when no embedding provided."""
        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(return_value="hash123")

        item = {
            "memory_id": "test_mem",
            "text": "Test content",
            "importance": 7,
        }

        success, _ = await migrator._migrate_item(item, mock_lancedb)

        assert success is True
        call_args = mock_lancedb.add.call_args
        assert call_args.kwargs["embedding"] == [0.0] * 384

    @pytest.mark.asyncio
    async def test_migrate_item_error_handling(self, migrator) -> None:
        """_migrate_item handles errors gracefully."""
        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(side_effect=RuntimeError("Database error"))

        item = {"memory_id": "test_mem", "text": "Test content", "importance": 5}

        success, message = await migrator._migrate_item(item, mock_lancedb)

        assert success is False
        assert "Error" in message

    @pytest.mark.asyncio
    async def test_run_no_items(self, migrator) -> None:
        """run() handles empty JSONL file."""
        # Point to empty file
        empty_file = migrator._memory_file.parent / "empty.jsonl"
        empty_file.touch()
        migrator._memory_file = empty_file

        stats = await migrator.run()

        assert stats.total_items == 0
        assert stats.migrated_items == 0

    @pytest.mark.asyncio
    async def test_run_dry_run(self, migrator) -> None:
        """run() in dry_run mode doesn't migrate."""
        migrator._dry_run = True

        stats = await migrator.run()

        assert stats.total_items == 3
        assert stats.skipped_items == 3
        assert stats.migrated_items == 0

    @pytest.mark.asyncio
    async def test_run_success(self, migrator) -> None:
        """run() migrates all items successfully."""
        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(return_value="hash123")
        migrator._lancedb = mock_lancedb

        stats = await migrator.run()

        assert stats.total_items == 3
        assert stats.migrated_items == 3
        assert stats.failed_items == 0

    @pytest.mark.asyncio
    async def test_run_partial_failure(self, migrator) -> None:
        """run() handles partial failures."""
        mock_lancedb = AsyncMock()
        # First call succeeds, second fails
        mock_lancedb.add = AsyncMock(side_effect=[True, RuntimeError("Error"), True])
        migrator._lancedb = mock_lancedb

        stats = await migrator.run()

        assert stats.total_items == 3
        assert stats.migrated_items == 2
        assert stats.failed_items == 1
        assert len(stats.errors) == 1

    @pytest.mark.asyncio
    async def test_verify_counts_match(self, migrator) -> None:
        """verify() returns match=True when counts match."""
        mock_lancedb = AsyncMock()
        mock_lancedb.get_stats = AsyncMock(return_value={"total_records": 3})
        migrator._lancedb = mock_lancedb

        result = await migrator.verify()

        assert result["match"] is True
        assert result["jsonl_item_count"] == 3
        assert result["lancedb_record_count"] == 3

    @pytest.mark.asyncio
    async def test_verify_counts_mismatch(self, migrator) -> None:
        """verify() returns match=False when counts differ."""
        mock_lancedb = AsyncMock()
        mock_lancedb.get_stats = AsyncMock(return_value={"total_records": 5})
        migrator._lancedb = mock_lancedb

        result = await migrator.verify()

        assert result["match"] is False
        assert result["jsonl_item_count"] == 3
        assert result["lancedb_record_count"] == 5

    def test_get_lancedb_lazy_init(self, migrator) -> None:
        """_get_lancedb creates adapter on first access."""
        assert migrator._lancedb is None

        # Get LanceDB adapter
        adapter = migrator._get_lancedb()
        # Should now be initialized
        assert migrator._lancedb is not None
        # Second call returns same instance
        adapter2 = migrator._get_lancedb()
        assert adapter is adapter2


class TestMigratorEdgeCases:
    """Edge case tests for migrator."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_memory_file_with_empty_lines(self, temp_dir) -> None:
        """Handles JSONL with empty lines."""
        memory_file = temp_dir / "memory.jsonl"
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write('{"memory_id": "a", "text": "First"}\n')
            f.write("\n")  # Empty line
            f.write('{"memory_id": "b", "text": "Second"}\n')
            f.write("   \n")  # Whitespace-only line

        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )
        items = migrator._load_jsonl_items()
        assert len(items) == 2

    def test_memory_file_with_special_characters(self, temp_dir) -> None:
        """Handles JSONL with special characters in text."""
        memory_file = temp_dir / "memory.jsonl"
        special_text = 'Text with "quotes" and\nnewlines and\ttabs'
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"memory_id": "a", "text": special_text}) + "\n")

        migrator = SemanticMemoryToLanceDBMigrator(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )
        items = migrator._load_jsonl_items()
        assert len(items) == 1
        assert items[0]["text"] == special_text

    @pytest.mark.asyncio
    async def test_migrate_item_with_missing_metadata(self, temp_dir) -> None:
        """Handles items with missing metadata field."""
        migrator = SemanticMemoryToLanceDBMigrator(workspace=str(temp_dir))

        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(return_value="hash123")

        # Item with no metadata field
        item = {
            "memory_id": "test_mem",
            "text": "Test content",
            "importance": 7,
            # No "metadata" key
        }

        success, _ = await migrator._migrate_item(item, mock_lancedb)

        assert success is True
        # Should not raise when metadata is missing
        call_kwargs = mock_lancedb.add.call_args.kwargs
        assert call_kwargs["source_file"] is None
        assert call_kwargs["line_start"] is None
        assert call_kwargs["line_end"] is None

    @pytest.mark.asyncio
    async def test_migrate_item_without_memory_id(self, temp_dir) -> None:
        """Handles items without memory_id (uses text hash)."""
        migrator = SemanticMemoryToLanceDBMigrator(workspace=str(temp_dir))

        mock_lancedb = AsyncMock()
        mock_lancedb.add = AsyncMock(return_value="hash123")

        # Item without memory_id
        item = {
            "text": "Unique content",
            "importance": 5,
        }

        success, _ = await migrator._migrate_item(item, mock_lancedb)

        assert success is True
        call_kwargs = mock_lancedb.add.call_args.kwargs
        assert call_kwargs["chunk_id"] is not None
        # chunk_id starts with "migrated_" and is truncated to 32 chars
        assert call_kwargs["chunk_id"].startswith("migrated_")
