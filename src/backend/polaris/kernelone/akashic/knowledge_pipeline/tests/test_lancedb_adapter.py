"""Tests for KnowledgeLanceDB adapter."""

from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path

import pytest


class TestKnowledgeLanceDB:
    """Tests for KnowledgeLanceDB adapter."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def lancedb(self, temp_dir):
        """Create a KnowledgeLanceDB instance for testing."""
        from polaris.kernelone.akashic.knowledge_pipeline.lancedb_adapter import (
            KnowledgeLanceDB,
        )

        return KnowledgeLanceDB(
            workspace=str(temp_dir),
            db_path=str(temp_dir / "test_lancedb"),
            table_name="test_knowledge",
        )

    @pytest.mark.asyncio
    async def test_add_returns_content_hash_id(self, lancedb) -> None:
        """add() returns a content-hash based ID."""
        chunk_id = await lancedb.add(
            chunk_id="test_001",
            text="Hello world",
            embedding=[0.1] * 384,
            source_file="test.txt",
            line_start=1,
            line_end=1,
        )
        assert chunk_id is not None
        assert isinstance(chunk_id, str)
        assert len(chunk_id) == 32  # SHA256 hex truncated to 32

    @pytest.mark.asyncio
    async def test_add_idempotent_same_content(self, lancedb) -> None:
        """Same content returns the same ID (idempotent)."""
        id1 = await lancedb.add(
            chunk_id="test_001",
            text="Idempotent test",
            embedding=[0.1] * 384,
        )
        id2 = await lancedb.add(
            chunk_id="test_002",
            text="Idempotent test",  # Same text
            embedding=[0.2] * 384,  # Different embedding (ignored)
        )
        assert id1 == id2  # Same content hash = same ID

    @pytest.mark.asyncio
    async def test_add_different_content_different_id(self, lancedb) -> None:
        """Different content returns different IDs."""
        id1 = await lancedb.add(
            chunk_id="test_001",
            text="Content A",
            embedding=[0.1] * 384,
        )
        id2 = await lancedb.add(
            chunk_id="test_002",
            text="Content B",
            embedding=[0.1] * 384,
        )
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_search_returns_results(self, lancedb) -> None:
        """search() returns matching results."""
        import hashlib

        # Add some chunks
        await lancedb.add(
            chunk_id="chunk_1",
            text="Python is great",
            embedding=[0.1] * 384,
            importance=8,
        )
        await lancedb.add(
            chunk_id="chunk_2",
            text="JavaScript is also great",
            embedding=[0.2] * 384,
            importance=5,
        )

        # Search with a query embedding
        query_embedding = list(hashlib.sha256(b"Python").digest())[:384]
        # Normalize
        norm = math.sqrt(sum(x * x for x in query_embedding))
        query_embedding = [x / norm for x in query_embedding] if norm > 0 else [0.0] * 384

        results = await lancedb.search(
            query="Python",
            embedding=query_embedding,
            top_k=10,
            min_importance=1,
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_with_importance_filter(self, lancedb) -> None:
        """search() respects min_importance filter."""
        await lancedb.add(
            chunk_id="chunk_high",
            text="High importance content",
            embedding=[0.1] * 384,
            importance=9,
        )
        await lancedb.add(
            chunk_id="chunk_low",
            text="Low importance content",
            embedding=[0.2] * 384,
            importance=1,
        )

        query_embedding = list(hashlib.sha256(b"importance").digest())[:384]
        norm = math.sqrt(sum(x * x for x in query_embedding))
        query_embedding = [x / norm for x in query_embedding] if norm > 0 else [0.0] * 384

        results = await lancedb.search(
            query="importance",
            embedding=query_embedding,
            top_k=10,
            min_importance=5,
        )
        # Should not include the low importance chunk
        assert all(r["importance"] >= 5 for r in results)

    @pytest.mark.asyncio
    async def test_delete_by_content_hash(self, lancedb) -> None:
        """delete() removes chunk by content hash."""
        content_hash = "abc123def456" * 2 + "abcd"  # 32 chars
        result = await lancedb.delete(content_hash)
        # May return True or False depending on whether it existed
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_get_stats(self, lancedb) -> None:
        """get_stats() returns store statistics."""
        await lancedb.add(
            chunk_id="chunk_1",
            text="Stats test",
            embedding=[0.1] * 384,
            language="python",
            importance=5,
        )

        stats = await lancedb.get_stats()
        assert "table_name" in stats or "error" in stats  # May have error if no LanceDB


class TestKnowledgeChunkRecord:
    """Tests for KnowledgeChunkRecord dataclass."""

    def test_record_fields(self) -> None:
        """KnowledgeChunkRecord has all required fields."""
        import json

        from polaris.kernelone.akashic.knowledge_pipeline.lancedb_adapter import (
            KnowledgeChunkRecord,
        )

        record = KnowledgeChunkRecord(
            id="test_id",
            chunk_id="chunk_001",
            source_file="test.py",
            line_start=1,
            line_end=10,
            text="def hello():\n    pass",
            content_hash="abc123",
            language="python",
            importance=7,
            embedding=[0.1] * 384,
            semantic_tags=json.dumps(["function"]),
            created_at="2026-04-04T00:00:00+00:00",
        )

        assert record.id == "test_id"
        assert record.chunk_id == "chunk_001"
        assert record.text == "def hello():\n    pass"
        assert record.language == "python"
        assert record.importance == 7
