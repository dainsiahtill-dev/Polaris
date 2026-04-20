"""Tests for HybridMemory three-layer storage."""

from __future__ import annotations

import pytest
from polaris.kernelone.akashic.hybrid_memory import (
    FullTextStoreBackend,
    GraphStoreBackend,
    HybridMemory,
    HybridMemoryConfig,
    HybridMemoryItem,
    SearchResult,
    VectorStoreBackend,
)


class TestVectorStoreBackend:
    """Tests for VectorStoreBackend."""

    @pytest.mark.asyncio
    async def test_add_and_search(self) -> None:
        """Test adding embeddings and searching."""
        backend = VectorStoreBackend()
        embedding = (0.1, 0.2, 0.3, 0.4, 0.5)

        # Add some embeddings
        await backend.add("id1", "text1", embedding)
        await backend.add("id2", "text2", (0.1, 0.2, 0.3, 0.4, 0.6))

        # Search with same embedding should find id1
        results = await backend.search(embedding, top_k=2)
        assert len(results) == 2
        assert results[0][0] == "id1"  # id1 is closer

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        """Test deleting embeddings."""
        backend = VectorStoreBackend()

        await backend.add("id1", "text1", (0.1, 0.2, 0.3))
        deleted = await backend.delete("id1")
        assert deleted is True

        # Search should not find deleted id
        results = await backend.search((0.1, 0.2, 0.3), top_k=10)
        assert not any(r[0] == "id1" for r in results)


class TestFullTextStoreBackend:
    """Tests for FullTextStoreBackend."""

    @pytest.mark.asyncio
    async def test_add_and_search(self) -> None:
        """Test adding items and keyword search."""
        backend = FullTextStoreBackend()

        await backend.add("id1", "Fixed the authentication bug in login.py", {})
        await backend.add("id2", "Added new feature to dashboard", {})

        # Search for "bug" should find id1
        results = await backend.search("bug fix", top_k=2)
        assert len(results) == 1
        assert results[0][0] == "id1"

        # Search for "feature" should find id2
        results = await backend.search("feature dashboard", top_k=2)
        assert len(results) == 1
        assert results[0][0] == "id2"

    def test_tokenize(self) -> None:
        """Test tokenization."""
        backend = FullTextStoreBackend()
        tokens = backend._tokenize("Fixed the login bug in auth.py")
        assert "fixed" in tokens
        assert "login" in tokens
        assert "bug" in tokens
        assert "auth" in tokens


class TestGraphStoreBackend:
    """Tests for GraphStoreBackend."""

    @pytest.mark.asyncio
    async def test_add_and_search_entities(self) -> None:
        """Test adding entities and searching."""
        backend = GraphStoreBackend()

        await backend.add(
            "id1",
            entities=("login.py", "auth"),
            relationships=(("login.py", "handles", "auth"),),
        )
        await backend.add(
            "id2",
            entities=("dashboard.py", "ui"),
            relationships=(("dashboard.py", "contains", "ui"),),
        )

        # Search for "auth" should find id1
        results = await backend.search(["auth"], top_k=2)
        assert len(results) == 1
        assert results[0][0] == "id1"

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        """Test deleting entities and relationships."""
        backend = GraphStoreBackend()

        await backend.add(
            "id1",
            entities=("login.py", "auth"),
            relationships=(("login.py", "handles", "auth"),),
        )

        deleted = await backend.delete("id1")
        assert deleted is True

        # Search should not find deleted id
        results = await backend.search(["auth"], top_k=10)
        assert not any(r[0] == "id1" for r in results)


class TestHybridMemoryConfig:
    """Tests for HybridMemoryConfig."""

    def test_default_config(self) -> None:
        """Test default configuration."""
        config = HybridMemoryConfig()
        assert config.enable_vector is True
        assert config.enable_fulltext is True
        assert config.enable_graph is True
        assert config.fusion_weights == (0.4, 0.3, 0.3)
        assert config.min_fusion_score == 0.1


class TestHybridMemoryItem:
    """Tests for HybridMemoryItem."""

    def test_creation(self) -> None:
        """Test creating a hybrid memory item."""
        from datetime import datetime, timezone

        item = HybridMemoryItem(
            memory_id="test_id",
            text="Test content",
            importance=8,
            created_at=datetime.now(timezone.utc),
            entities=("entity1",),
            relationships=(("entity1", "relates", "entity2"),),
            metadata={"key": "value"},
        )

        assert item.memory_id == "test_id"
        assert item.text == "Test content"
        assert item.importance == 8
        assert item.entities == ("entity1",)
        assert len(item.relationships) == 1


class TestSearchResult:
    """Tests for SearchResult."""

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        from datetime import datetime, timezone

        result = SearchResult(
            memory_id="test_id",
            text="Test content",
            importance=8,
            created_at=datetime.now(timezone.utc),
            vector_score=0.9,
            fulltext_score=0.7,
            graph_score=0.5,
            fused_score=0.7,
            metadata={"key": "value"},
        )

        d = result.to_dict()
        assert d["memory_id"] == "test_id"
        assert d["text"] == "Test content"
        assert d["importance"] == 8
        assert d["vector_score"] == 0.9
        assert d["fused_score"] == 0.7


class TestHybridMemory:
    """Tests for HybridMemory."""

    @pytest.mark.asyncio
    async def test_initialization(self, tmp_path: pytest.TempPathFactory) -> None:
        """Test hybrid memory initialization."""
        config = HybridMemoryConfig(enable_vector=False)
        memory = HybridMemory(workspace=str(tmp_path), config=config)
        assert memory._items == {}
        stats = memory.get_stats()
        assert stats["total_items"] == 0

    @pytest.mark.asyncio
    async def test_add_and_get(self, tmp_path: pytest.TempPathFactory) -> None:
        """Test adding and retrieving items."""
        config = HybridMemoryConfig(enable_vector=False)
        memory = HybridMemory(workspace=str(tmp_path), config=config)
        memory_id = await memory.add(
            text="Fixed the authentication bug",
            entities=("auth.py", "login"),
            relationships=(("auth.py", "handles", "login"),),
            importance=8,
        )

        assert memory_id.startswith("hybrid_")
        item = await memory.get(memory_id)
        assert item is not None
        assert item["text"] == "Fixed the authentication bug"
        assert item["importance"] == 8
        assert "auth.py" in item["entities"]

    @pytest.mark.asyncio
    async def test_search(self, tmp_path: pytest.TempPathFactory) -> None:
        """Test searching hybrid memory."""
        config = HybridMemoryConfig(enable_vector=False)
        memory = HybridMemory(workspace=str(tmp_path), config=config)

        # Add items with full-text and entity content
        await memory.add(
            text="Fixed the authentication bug in login.py",
            entities=("login.py", "auth"),
            importance=8,
        )
        await memory.add(
            text="Added new feature to dashboard",
            entities=("dashboard.py", "ui"),
            importance=5,
        )

        # Search should find the auth bug fix via full-text
        results = await memory.search("authentication bug", top_k=5)
        assert len(results) >= 1
        assert any("authentication" in r.text.lower() for r in results)

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path: pytest.TempPathFactory) -> None:
        """Test deleting items."""
        config = HybridMemoryConfig(enable_vector=False)
        memory = HybridMemory(workspace=str(tmp_path), config=config)
        memory_id = await memory.add(text="Test content", importance=5)

        # Delete should succeed
        deleted = await memory.delete(memory_id)
        assert deleted is True

        # Item should no longer be retrievable
        item = await memory.get(memory_id)
        assert item is None

    def test_extract_entities(self, tmp_path: pytest.TempPathFactory) -> None:
        """Test entity extraction from text."""
        config = HybridMemoryConfig(enable_vector=False)
        memory = HybridMemory(workspace=str(tmp_path), config=config)
        entities = memory._extract_entities("Fixed bug in login.py and dashboard.tsx")
        # File extensions are extracted
        assert "py" in entities or "login" in entities
        assert "tsx" in entities or "dashboard" in entities

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path: pytest.TempPathFactory) -> None:
        """Test getting memory statistics."""
        config = HybridMemoryConfig(enable_vector=False)
        memory = HybridMemory(workspace=str(tmp_path), config=config)
        await memory.add(text="Test content 1", importance=5)
        await memory.add(text="Test content 2", importance=8)

        stats = memory.get_stats()
        assert stats["total_items"] == 2
        assert stats["avg_importance"] == 6.5  # (5 + 8) / 2
