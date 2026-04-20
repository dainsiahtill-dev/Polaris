"""Tests for KnowledgeSync bidirectional synchronization."""

from __future__ import annotations

import logging
from typing import NoReturn
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.knowledge_sync import (
    KnowledgeSync,
    SyncStats,
)

logger = logging.getLogger(__name__)


class MockEmbeddingComputer:
    """Mock embedding computer for testing."""

    async def compute_batch(self, texts, *, model=None):
        # Return deterministic fake embeddings
        return [[0.0] * 384 for _ in texts]


class MockJsonlStore:
    """Mock IdempotentVectorStore for testing sync to LanceDB."""

    def __init__(self) -> None:
        # Internal storage: text -> content_hash (for content-hash based ops)
        self._texts: list[str] = []  # ordered list of texts in the store
        self._semantic = MagicMock()
        # Map from content_hash -> text (populated by _setup_from_texts)
        self._hash_to_text: dict[str, str] = {}

    def _content_hash(self, text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def setup_texts(self, texts: list[str]) -> None:
        """Set up the mock store with a list of texts."""
        self._texts = texts
        self._hash_to_text = {self._content_hash(t): t for t in texts}
        # Set up semantic._items mock: memory_id -> MagicMock with .text attr
        mock_items = {}
        for i, text in enumerate(texts):
            mock_item = MagicMock()
            mock_item.text = text
            mock_items[f"mem_{i}"] = mock_item
        self._semantic._items = mock_items

    def _get_jsonl_content_hashes(self) -> set[str]:
        """Return content hashes for all items in the JSONL store."""
        # Mirror the real implementation: iterate _semantic._items
        try:
            semantic = self._semantic  # type: ignore[attr-defined]
            hashes: set[str] = set()
            for item in semantic._items.values():
                hashes.add(self._content_hash(item.text))
            return hashes
        except (RuntimeError, ValueError, AttributeError):
            return set()

    def _get_text_by_content_hash(self, content_hash: str) -> str | None:
        # Mirror real implementation: iterate _semantic._items
        try:
            semantic = self._semantic  # type: ignore[attr-defined]
            for item in semantic._items.values():
                if self._content_hash(item.text) == content_hash:
                    return item.text
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to get text by content hash: %s", e)
        return None

    async def add(self, text, *, importance=5):
        return self._content_hash(text)


class MockLanceDBAdapter:
    """Mock LanceDB adapter for testing sync operations."""

    def __init__(self, pre_populated: dict[str, dict] | None = None) -> None:
        self._records: dict[str, dict] = pre_populated or {}  # content_hash -> record
        self._get_all_called = False

    def _ensure_table(self) -> None:
        pass

    async def get_all_content_hashes(self) -> set[str]:
        self._get_all_called = True
        return set(self._records.keys())

    async def add(
        self,
        chunk_id,
        text,
        embedding,
        *,
        source_file="",
        line_start=1,
        line_end=1,
        importance=5,
        semantic_tags=None,
        language="",
    ):
        self._records[chunk_id] = {
            "chunk_id": chunk_id,
            "text": text,
            "embedding": embedding,
        }
        return chunk_id

    async def delete(self, content_hash: str) -> bool:
        if content_hash in self._records:
            del self._records[content_hash]
            return True
        return False

    @property
    def _table(self):
        """Return a mock arrow table backed by _records."""
        return _MockArrowTable(self._records)


class _MockArrowTable:
    """Minimal mock of a PyArrow table backed by a records dict."""

    def __init__(self, records: dict[str, dict]) -> None:
        self._records = records

    def to_arrow(self):
        return self

    def to_pydict(self):
        return {
            "content_hash": list(self._records.keys()),
            "text": [r["text"] for r in self._records.values()],
        }

    def __len__(self) -> int:
        return len(self._records)


class TestSyncStats:
    """Tests for SyncStats dataclass."""

    def test_sync_stats_default_values(self) -> None:
        """SyncStats has correct defaults."""
        stats = SyncStats(direction="jsonl→lancedb")
        assert stats.direction == "jsonl→lancedb"
        assert stats.jsonl_total == 0
        assert stats.lancedb_total == 0
        assert stats.items_added_to_lancedb == 0
        assert stats.items_added_to_jsonl == 0
        assert stats.items_removed_from_lancedb == 0
        assert stats.duration_ms == 0.0
        assert stats.errors == []

    def test_sync_stats_with_values(self) -> None:
        """SyncStats can be constructed with values."""
        stats = SyncStats(
            direction="bidirectional",
            jsonl_total=100,
            lancedb_total=80,
            items_added_to_lancedb=20,
            items_added_to_jsonl=0,
            items_removed_from_lancedb=0,
            duration_ms=150.5,
            errors=["test error"],
        )
        assert stats.direction == "bidirectional"
        assert stats.jsonl_total == 100
        assert stats.items_added_to_lancedb == 20
        assert stats.duration_ms == 150.5
        assert stats.errors == ["test error"]


class TestKnowledgeSyncToLancedb:
    """Tests for sync_to_lancedb operation."""

    @pytest.fixture
    def jsonl_store(self):
        store = MockJsonlStore()
        store.setup_texts(["Hello world", "Python code", "Another document"])
        return store

    @pytest.fixture
    def lancedb_adapter(self):
        return MockLanceDBAdapter()

    @pytest.fixture
    def embedding_computer(self):
        return MockEmbeddingComputer()

    @pytest.mark.asyncio
    async def test_sync_to_lancedb_empty_jsonl(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """Empty JSONL store produces empty sync."""
        jsonl_store.setup_texts([])

        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_to_lancedb()

        assert stats.items_added_to_lancedb == 0
        assert stats.jsonl_total == 0

    @pytest.mark.asyncio
    async def test_sync_to_lancedb_adds_new_items(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_to_lancedb adds JSONL-only items to LanceDB."""
        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_to_lancedb()

        # All 3 JSONL items should be added
        assert stats.items_added_to_lancedb == 3
        assert stats.jsonl_total == 3
        assert len(lancedb_adapter._records) == 3

    @pytest.mark.asyncio
    async def test_sync_to_lancedb_skips_existing(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_to_lancedb skips items already in LanceDB."""
        # Pre-populate LanceDB with one item (maps to "Hello world" in JSONL)
        pre_text = "Hello world"
        pre_hash = jsonl_store._content_hash(pre_text)

        lancedb_adapter = MockLanceDBAdapter(
            pre_populated={
                pre_hash: {
                    "chunk_id": pre_hash,
                    "text": pre_text,
                    "embedding": [0.0] * 384,
                }
            }
        )

        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_to_lancedb()

        # Should add only 2 new items (not "Hello world")
        assert stats.items_added_to_lancedb == 2
        assert stats.lancedb_total == 1

    @pytest.mark.asyncio
    async def test_sync_to_lancedb_overwrite_mode(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_to_lancedb with overwrite=True re-embeds all JSONL items."""
        # Pre-populate LanceDB
        pre_text = "Hello world"
        pre_hash = jsonl_store._content_hash(pre_text)

        lancedb_adapter = MockLanceDBAdapter(
            pre_populated={
                pre_hash: {
                    "chunk_id": pre_hash,
                    "text": pre_text,
                    "embedding": [0.0] * 384,
                }
            }
        )

        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_to_lancedb(overwrite=True)

        # All 3 should be re-added
        assert stats.items_added_to_lancedb == 3


class TestKnowledgeSyncFromLancedb:
    """Tests for sync_from_lancedb operation."""

    @pytest.fixture
    def jsonl_store(self):
        store = MockJsonlStore()
        store.setup_texts(["Existing document"])
        return store

    @pytest.fixture
    def lancedb_adapter(self):
        adapter = MockLanceDBAdapter(
            pre_populated={
                "orphan_hash": {
                    "chunk_id": "orphan_hash",
                    "text": "Orphan content",
                    "embedding": [0.0] * 384,
                }
            }
        )
        return adapter

    @pytest.fixture
    def embedding_computer(self):
        return MockEmbeddingComputer()

    @pytest.mark.asyncio
    async def test_sync_from_lancedb_no_orphans(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """No orphans means nothing to sync."""
        lancedb_adapter._records = {}

        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_from_lancedb()

        assert stats.items_added_to_jsonl == 0
        assert stats.items_removed_from_lancedb == 0

    @pytest.mark.asyncio
    async def test_sync_from_lancedb_imports_orphans(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_from_lancedb imports LanceDB-only items into JSONL."""
        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_from_lancedb()

        # The orphan should be added to JSONL
        assert stats.items_added_to_jsonl == 1
        assert stats.items_removed_from_lancedb == 0

    @pytest.mark.asyncio
    async def test_sync_from_lancedb_delete_orphans(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_from_lancedb with delete_orphan_lancedb=True removes orphans."""
        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_from_lancedb(delete_orphan_lancedb=True)

        # Orphan should be deleted from LanceDB
        assert stats.items_removed_from_lancedb == 1
        assert stats.items_added_to_jsonl == 0


class TestKnowledgeSyncBidirectional:
    """Tests for sync_bidirectional operation."""

    @pytest.fixture
    def jsonl_store(self):
        store = MockJsonlStore()
        store.setup_texts(["Doc A", "Doc B"])
        return store

    @pytest.fixture
    def lancedb_adapter(self):
        return MockLanceDBAdapter()

    @pytest.fixture
    def embedding_computer(self):
        return MockEmbeddingComputer()

    @pytest.mark.asyncio
    async def test_sync_bidirectional_full(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_bidirectional runs both directions."""
        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_bidirectional()

        assert stats.direction == "bidirectional"
        assert stats.jsonl_total == 2
        assert stats.items_added_to_lancedb == 2
        # LanceDB had 0 orphans so nothing removed
        assert stats.duration_ms > 0

    @pytest.mark.asyncio
    async def test_sync_bidirectional_collects_errors(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_bidirectional aggregates errors from both directions."""
        # Make LanceDB.add raise on first call
        call_count = 0

        async def failing_add(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated failure")
            return kwargs.get("chunk_id", args[0] if args else "unknown")

        lancedb_adapter.add = failing_add

        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=embedding_computer,
        )
        stats = await sync.sync_bidirectional()

        assert len(stats.errors) >= 1


class TestKnowledgeSyncEdgeCases:
    """Edge case tests for KnowledgeSync."""

    @pytest.fixture
    def jsonl_store(self):
        store = MockJsonlStore()
        store.setup_texts(["Test content"])
        return store

    @pytest.fixture
    def lancedb_adapter(self):
        return MockLanceDBAdapter()

    @pytest.fixture
    def embedding_computer(self):
        return MockEmbeddingComputer()

    @pytest.mark.asyncio
    async def test_sync_handles_embedding_failure(self, jsonl_store, lancedb_adapter, embedding_computer) -> None:
        """sync_to_lancedb degrades gracefully when embedding fails."""

        class FailingComputer:
            async def compute_batch(self, texts, *, model=None) -> NoReturn:
                raise RuntimeError("Embedding service unavailable")

        sync = KnowledgeSync(
            jsonl_store=jsonl_store,
            lancedb_adapter=lancedb_adapter,
            embedding_computer=FailingComputer(),
        )
        stats = await sync.sync_to_lancedb()

        # Should fall back to zero embeddings and record the error
        assert stats.jsonl_total == 1
        assert len(stats.errors) == 1
        assert "Embedding" in stats.errors[0]

    @pytest.mark.asyncio
    async def test_sync_jsonl_content_hashes_none_on_error(
        self, jsonl_store, lancedb_adapter, embedding_computer
    ) -> None:
        """_get_jsonl_content_hashes returns empty set on error."""
        jsonl_store._semantic = None  # type: ignore[assignment]

        # Verify method doesn't raise even when _semantic is None
        hashes = jsonl_store._get_jsonl_content_hashes()

        # Should not raise, returns empty set
        assert isinstance(hashes, set)
        assert len(hashes) == 0
