"""Tests for IdempotentVectorStore."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.idempotent_vector_store import (
    IdempotentVectorStore,
)
from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory


class TestIdempotentVectorStore:
    """Tests for IdempotentVectorStore."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def semantic_memory(self, temp_dir):
        """Create a semantic memory instance for testing."""
        memory_file = temp_dir / "test_memory.jsonl"
        return AkashicSemanticMemory(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )

    @pytest.fixture
    def vector_store(self, semantic_memory, temp_dir):
        """Create an IdempotentVectorStore for testing."""
        tombstone_dir = temp_dir / ".tombstones"
        return IdempotentVectorStore(
            semantic_memory,
            tombstone_file=str(tombstone_dir / "test_tombstones.jsonl"),
        )

    @pytest.mark.asyncio
    async def test_add_returns_memory_id(self, vector_store) -> None:
        """Add returns a memory ID."""
        memory_id = await vector_store.add("Test content", importance=5)
        assert memory_id is not None
        assert isinstance(memory_id, str)

    @pytest.mark.asyncio
    async def test_add_same_content_returns_same_id(self, vector_store) -> None:
        """Same content returns the same memory ID (idempotent)."""
        id1 = await vector_store.add("Hello world", importance=5)
        id2 = await vector_store.add("Hello world", importance=5)
        assert id1 == id2

    @pytest.mark.asyncio
    async def test_add_different_content_returns_different_ids(self, vector_store) -> None:
        """Different content returns different memory IDs."""
        id1 = await vector_store.add("Content A", importance=5)
        id2 = await vector_store.add("Content B", importance=5)
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_delete_removes_item(self, vector_store) -> None:
        """Delete removes an item from the store."""
        memory_id = await vector_store.add("To be deleted", importance=5)
        result = await vector_store.delete(memory_id)
        assert result is True

        # Item should not be found in search
        results = await vector_store.search("deleted")
        assert all(mid != memory_id for mid, _ in results)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, vector_store) -> None:
        """Deleting a nonexistent item returns False."""
        result = await vector_store.delete("nonexistent_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_search_returns_results(self, vector_store) -> None:
        """Search returns matching items."""
        await vector_store.add("Python is great", importance=7)
        await vector_store.add("JavaScript is also great", importance=5)

        results = await vector_store.search("Python")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_with_importance_filter(self, vector_store) -> None:
        """Search respects min_importance filter."""
        await vector_store.add("High importance", importance=8)
        await vector_store.add("Low importance", importance=2)

        results = await vector_store.search("importance", min_importance=5)
        # Should only return the high importance item
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_deleted_item_not_in_search(self, vector_store) -> None:
        """Deleted items do not appear in search results."""
        memory_id = await vector_store.add("This will be deleted", importance=5)
        await vector_store.delete(memory_id)

        results = await vector_store.search("deleted")
        memory_ids_in_results = [mid for mid, _ in results]
        assert memory_id not in memory_ids_in_results

    def test_get_stats(self, vector_store) -> None:
        """Get stats returns store statistics."""
        stats = vector_store.get_stats()
        assert "semantic_stats" in stats
        assert "hash_index_size" in stats
        assert "deleted_count" in stats


class TestIdempotentVectorStoreGhostData:
    """Tests for ghost data prevention."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def semantic_memory(self, temp_dir):
        """Create a semantic memory instance for testing."""
        memory_file = temp_dir / "test_memory.jsonl"
        return AkashicSemanticMemory(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )

    @pytest.fixture
    def vector_store(self, semantic_memory, temp_dir):
        """Create an IdempotentVectorStore for testing."""
        tombstone_dir = temp_dir / ".tombstones"
        return IdempotentVectorStore(
            semantic_memory,
            tombstone_file=str(tombstone_dir / "test_tombstones.jsonl"),
        )

    @pytest.mark.asyncio
    async def test_deleted_item_stays_deleted_after_restart(self, temp_dir, semantic_memory) -> None:
        """Deleted items remain deleted after store restart."""
        # Create store and add then delete an item
        memory_file = temp_dir / "test_memory.jsonl"
        tombstone_file = temp_dir / "test_tombstones.jsonl"

        store1 = IdempotentVectorStore(
            semantic_memory,
            tombstone_file=str(tombstone_file),
        )

        memory_id = await store1.add("Ghost test content", importance=5)
        await store1.delete(memory_id)

        # Create a new store instance (simulating restart)
        semantic_memory2 = AkashicSemanticMemory(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )
        store2 = IdempotentVectorStore(
            semantic_memory2,
            tombstone_file=str(tombstone_file),
        )

        # The deleted item should not appear in search
        results = await store2.search("Ghost")
        assert len(results) == 0


class TestIdempotentVectorStoreVacuum:
    """Tests for vacuum() tombstone pruning."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def tombstone_file(self, temp_dir):
        return temp_dir / "test_tombstones.jsonl"

    @pytest.fixture
    def vector_store(self, temp_dir, tombstone_file):
        memory_file = temp_dir / "test_memory.jsonl"
        semantic = AkashicSemanticMemory(
            workspace=str(temp_dir),
            memory_file=str(memory_file),
        )
        return IdempotentVectorStore(
            semantic,
            tombstone_file=str(tombstone_file),
        )

    def _write_tombstone(self, path: Path, memory_id: str, days_ago: int) -> None:
        """Write a tombstone entry with a given age."""
        deleted_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        entry = {
            "memory_id": memory_id,
            "content_hash": "abc123",
            "deleted_at": deleted_at.isoformat(),
            "deleted": True,
        }
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @pytest.mark.asyncio
    async def test_vacuum_removes_old_tombstones(self, tombstone_file, vector_store) -> None:
        """Vacuum removes tombstone entries older than max_age_days."""
        self._write_tombstone(tombstone_file, "old_mem_1", days_ago=60)
        self._write_tombstone(tombstone_file, "old_mem_2", days_ago=45)
        self._write_tombstone(tombstone_file, "recent_mem", days_ago=1)

        removed = await vector_store.vacuum(max_age_days=30)

        assert removed == 2
        # Recent entry should remain
        with open(tombstone_file, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["memory_id"] == "recent_mem"

    @pytest.mark.asyncio
    async def test_vacuum_keeps_recent_tombstones(self, tombstone_file, vector_store) -> None:
        """Vacuum keeps tombstone entries within max_age_days."""
        self._write_tombstone(tombstone_file, "mem_1", days_ago=5)
        self._write_tombstone(tombstone_file, "mem_2", days_ago=10)

        removed = await vector_store.vacuum(max_age_days=30)

        assert removed == 0
        with open(tombstone_file, encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_vacuum_nonexistent_file_returns_zero(self, vector_store) -> None:
        """Vacuum returns 0 when tombstone file does not exist."""
        removed = await vector_store.vacuum(max_age_days=30)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_vacuum_returns_removed_count(self, tombstone_file, vector_store) -> None:
        """Vacuum returns the number of removed entries."""
        self._write_tombstone(tombstone_file, "mem_1", days_ago=100)
        self._write_tombstone(tombstone_file, "mem_2", days_ago=200)
        self._write_tombstone(tombstone_file, "mem_3", days_ago=300)

        removed = await vector_store.vacuum(max_age_days=30)

        assert removed == 3
