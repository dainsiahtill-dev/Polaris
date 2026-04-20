"""Akashic Nexus: Semantic Memory Implementation.

Implements SemanticMemoryPort for long-term vector-based memory storage.
This module provides persistent semantic memory with embedding-based search.

Architecture:
    - JSONL-based persistence for memory items
    - Embedding-based similarity search via KernelEmbeddingPort
    - Importance-weighted retrieval
    - Optional LanceDB integration for vector storage
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.storage import resolve_runtime_path

from .protocols import SemanticMemoryPort

logger = logging.getLogger(__name__)


@dataclass
class SemanticMemoryItem:
    """A single item in semantic memory."""

    memory_id: str
    text: str
    importance: int
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None  # Cached embedding vector


class AkashicSemanticMemory:
    """Semantic memory with embedding-based similarity search.

    This implementation:
    - Stores memory items in JSONL for persistence
    - Computes embeddings via KernelEmbeddingPort
    - Supports similarity-based retrieval
    - Falls back to keyword matching when embeddings unavailable

    Usage::

        semantic = AkashicSemanticMemory(workspace=".")
        memory_id = await semantic.add(
            "Fixed the login bug in auth.py",
            importance=8,
        )
        results = await semantic.search("login authentication", top_k=5)
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        memory_file: str | None = None,
        embedding_model: str | None = None,
        enable_vector_search: bool = True,
        lancedb: Any | None = None,
    ) -> None:
        """Initialize semantic memory.

        Args:
            workspace: Workspace root path for deriving storage paths
            memory_file: Optional explicit path to JSONL memory file
            embedding_model: Optional embedding model name
            enable_vector_search: Whether to compute/search embeddings
            lancedb: Optional KnowledgeLanceDB instance for vector storage.
                When provided, search operations will use LanceDB for
                improved performance. JSONL is still used for persistence
                if lancedb is not the primary store.
        """
        self._workspace = str(workspace or ".")
        self._memory_file = memory_file or resolve_runtime_path(self._workspace, "runtime/semantic/memory.jsonl")
        self._embedding_model = embedding_model
        self._enable_vector = enable_vector_search
        self._lancedb = lancedb

        # In-memory cache
        self._items: dict[str, SemanticMemoryItem] = {}
        self._embedding_cache: dict[str, list[float]] = {}

        # Deleted items set (for JSONL cleanup on next load)
        self._deleted_ids: set[str] = set()

        # Thread safety for async access
        self._lock: asyncio.Lock = asyncio.Lock()

        # Lazy-loaded embedding port
        self._embedding_port: Any = None

        # Ensure directory exists
        memory_dir = os.path.dirname(self._memory_file) or "."
        os.makedirs(memory_dir, exist_ok=True)

        # Load existing items
        self._load()

    def _get_embedding_port(self) -> Any:
        """Get the embedding port (lazy initialization)."""
        if self._embedding_port is None:
            try:
                from polaris.kernelone.llm.embedding import get_default_embedding_port

                self._embedding_port = get_default_embedding_port()
            except (RuntimeError, ValueError) as exc:
                logger.debug("Could not get default embedding port: %s", exc)
        return self._embedding_port

    def _compute_embedding(self, text: str) -> list[float] | None:
        """Compute embedding for text."""
        if not self._enable_vector:
            return None

        # Check cache first
        if text in self._embedding_cache:
            return self._embedding_cache[text]

        port = self._get_embedding_port()
        if port is None:
            return None

        try:
            model = self._embedding_model or "nomic-embed-text"
            embedding = port.get_embedding(text, model=model)
            if embedding:
                self._embedding_cache[text] = embedding
            return embedding
        except (RuntimeError, ValueError) as exc:
            logger.debug("Embedding computation failed: %s", exc)
            return None

    def _load(self) -> None:
        """Load items from JSONL file."""
        if not os.path.exists(self._memory_file):
            return
        try:
            with open(self._memory_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if isinstance(data.get("created_at"), str):
                        data["created_at"] = datetime.fromisoformat(data["created_at"])
                    item = SemanticMemoryItem(**data)
                    # Skip items that were deleted in a previous session
                    if item.memory_id not in self._deleted_ids:
                        self._items[item.memory_id] = item
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load semantic memory from %s: %s", self._memory_file, exc)

    def _persist(self, item: SemanticMemoryItem) -> None:
        """Append item to JSONL file."""
        os.makedirs(os.path.dirname(self._memory_file) or ".", exist_ok=True)
        lock_path = f"{self._memory_file}.lock"
        data = asdict(item)
        # Convert datetime to isoformat string
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat()
        # Remove None embedding from serialized form
        if data.get("embedding") is None:
            data.pop("embedding", None)

        with file_lock(lock_path, timeout_sec=5.0), open(self._memory_file, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    async def _compact_jsonl(self) -> None:
        """Rewrite JSONL file to remove deleted items.

        This is called asynchronously after delete() to clean up the file.
        """
        if not self._deleted_ids:
            return

        try:
            lock_path = f"{self._memory_file}.lock"
            with file_lock(lock_path, timeout_sec=10.0):
                # Read all items
                items_to_keep: list[dict[str, Any]] = []
                if os.path.exists(self._memory_file):
                    with open(self._memory_file, encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                if data.get("memory_id") not in self._deleted_ids:
                                    items_to_keep.append(data)
                            except json.JSONDecodeError:
                                continue

                # Rewrite file
                with open(self._memory_file, "w", encoding="utf-8", newline="\n") as f:
                    for data in items_to_keep:
                        f.write(json.dumps(data, ensure_ascii=False) + "\n")

            logger.debug("Compacted JSONL: removed %d items", len(self._deleted_ids))
            self._deleted_ids.clear()
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to compact semantic memory JSONL: %s", exc)

    def _tokenize(self, text: str) -> set[str]:
        """Simple tokenizer for keyword matching."""
        if not text:
            return set()
        # Split on alphanumeric boundaries and normalize
        tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text.lower())
        return set(tokens)

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b) or not a or not b:
            return 0.0

        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        """Add a memory item to semantic storage."""
        memory_id = f"sem_{uuid.uuid4().hex[:16]}"

        # Compute embedding
        embedding = self._compute_embedding(text)

        item = SemanticMemoryItem(
            memory_id=memory_id,
            text=text,
            importance=max(1, min(10, importance)),
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
            embedding=embedding,
        )

        async with self._lock:
            self._items[memory_id] = item
        self._persist(item)

        # Also store in LanceDB if available (for fast vector search)
        if self._lancedb is not None and embedding is not None:
            try:
                await self._lancedb.add(
                    chunk_id=memory_id,
                    text=text,
                    embedding=embedding,
                    importance=importance,
                    source_file=metadata.get("source_file") if metadata else None,
                    line_start=metadata.get("line_start") if metadata else None,
                    line_end=metadata.get("line_end") if metadata else None,
                )
                logger.debug("Stored in LanceDB: %s", memory_id[:12])
            except (RuntimeError, ValueError) as exc:
                # Don't fail the add if LanceDB storage fails
                logger.warning("Failed to store in LanceDB: %s", exc)

        logger.debug("Added semantic memory item: %s (importance=%d)", memory_id[:12], importance)
        return memory_id

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:
        """Search semantic memory by query text.

        Returns list of (memory_id, similarity_score) tuples sorted by relevance.
        Uses LanceDB vector search when available, falls back to in-memory
        embedding similarity or keyword matching.
        """
        # Try LanceDB vector search first if available
        if self._lancedb is not None:
            query_embedding = self._compute_embedding(query)
            if query_embedding is not None:
                try:
                    results = await self._lancedb.search(
                        query=query,
                        embedding=query_embedding,
                        top_k=top_k,
                        min_importance=min_importance,
                    )
                    # Convert to (memory_id, score) format
                    lancedb_results: list[tuple[str, float]] = [(r["chunk_id"], r["score"]) for r in results]
                    if lancedb_results:
                        logger.debug(
                            "LanceDB search returned %d results for '%s'",
                            len(lancedb_results),
                            query[:50],
                        )
                        return lancedb_results
                except (RuntimeError, ValueError) as exc:
                    logger.warning("LanceDB search failed, falling back: %s", exc)

        # Fall back to in-memory search
        async with self._lock:
            items_snapshot = dict(self._items)

        if not items_snapshot:
            return []

        # Compute query embedding
        query_embedding = self._compute_embedding(query)
        query_tokens = self._tokenize(query)

        scored_items: list[tuple[float, SemanticMemoryItem]] = []

        for item in items_snapshot.values():
            # Filter by minimum importance
            if item.importance < min_importance:
                continue

            score = 0.0

            # Try embedding similarity first
            if query_embedding is not None and item.embedding:
                score = self._cosine_similarity(query_embedding, item.embedding)

            # Fall back to keyword matching
            if score == 0.0 and query_tokens:
                item_tokens = self._tokenize(item.text)
                if item_tokens:
                    # Jaccard similarity
                    intersection = query_tokens & item_tokens
                    union = query_tokens | item_tokens
                    if union:
                        score = len(intersection) / len(union)

                    # Boost score by importance
                    score *= item.importance / 10.0

            if score > 0.0:
                scored_items.append((score, item))

        # Sort by score descending
        scored_items.sort(key=lambda x: x[0], reverse=True)

        # Return top-k results
        return [(item.memory_id, score) for score, item in scored_items[:top_k]]

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a specific memory item by ID."""
        async with self._lock:
            if memory_id not in self._items:
                return None
            item = self._items[memory_id]

        return {
            "memory_id": item.memory_id,
            "text": item.text,
            "importance": item.importance,
            "created_at": item.created_at.isoformat(),
            "metadata": item.metadata,
        }

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory item by ID.

        This method guarantees that the deletion is persisted to JSONL
        before returning. The item is removed from memory immediately,
        and the JSONL file is compacted synchronously within the lock
        to ensure durability and prevent concurrent compaction races.

        Args:
            memory_id: The ID of the memory item to delete.

        Returns:
            True if the item was deleted, False if it didn't exist.
        """
        async with self._lock:
            if memory_id not in self._items:
                return False
            del self._items[memory_id]
            self._deleted_ids.add(memory_id)

            # Perform compaction within lock to prevent race conditions
            # with concurrent delete() calls. This ensures atomicity:
            # - All pending deletions are compacted together
            # - No other delete() can interleave and cause missed cleanup
            if self._deleted_ids:
                await self._compact_jsonl()

        # Also delete from LanceDB if available
        if self._lancedb is not None:
            try:
                await self._lancedb.delete(content_hash=memory_id)
                logger.debug("Deleted from LanceDB: %s", memory_id[:12])
            except (RuntimeError, ValueError) as exc:
                logger.warning("Failed to delete from LanceDB: %s", exc)

        logger.debug("Deleted semantic memory item: %s", memory_id[:12])
        return True

    def get_stats(self) -> dict[str, Any]:
        """Get semantic memory statistics.

        Note: This is a sync method that makes a best-effort snapshot.
        For strict consistency, use async methods with the lock.
        """
        # Make a copy to avoid iteration issues during concurrent deletes
        items_snapshot = list(self._items.values())
        total_items = len(items_snapshot)
        avg_importance = sum(i.importance for i in items_snapshot) / total_items if total_items > 0 else 0.0

        stats: dict[str, Any] = {
            "size": total_items,
            "avg_importance": round(avg_importance, 2),
            "vector_search_enabled": self._enable_vector,
            "embedding_model": self._embedding_model,
            "embedding_port_available": self._get_embedding_port() is not None,
        }

        # Include LanceDB stats if available
        if self._lancedb is not None:
            try:
                lancedb_stats = asyncio.get_event_loop().run_until_complete(self._lancedb.get_stats())
                stats["lancedb"] = lancedb_stats
                stats["lancedb_enabled"] = True
            except (RuntimeError, ValueError) as exc:
                logger.warning("Failed to get LanceDB stats: %s", exc)
                stats["lancedb_enabled"] = False
        else:
            stats["lancedb_enabled"] = False

        return stats


# Type annotation
AkashicSemanticMemory.__protocol__ = SemanticMemoryPort  # type: ignore[attr-defined]


__all__ = [
    "AkashicSemanticMemory",
    "SemanticMemoryItem",
]
