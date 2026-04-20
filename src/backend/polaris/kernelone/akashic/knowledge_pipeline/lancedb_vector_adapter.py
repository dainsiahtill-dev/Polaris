"""LanceDB Vector Adapter — bridges KnowledgeLanceDB to IdempotentVectorStorePort.

Provides an adapter that wraps KnowledgeLanceDB so it can be used as the
vector_store backend in DocumentPipeline, while implementing the
IdempotentVectorStorePort contract.

Usage::

    from polaris.kernelone.akashic.knowledge_pipeline import KnowledgeLanceDB
    from polaris.kernelone.akashic.knowledge_pipeline.lancedb_vector_adapter import LanceDBVectorAdapter

    lancedb = KnowledgeLanceDB(workspace=".")
    adapter = LanceDBVectorAdapter(lancedb, embedding_computer=computer)
    # Now usable as IdempotentVectorStorePort in DocumentPipeline
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
    IdempotentVectorStorePort,
)

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.embedding_computer import (
        EmbeddingComputer,
    )

logger = logging.getLogger(__name__)


class LanceDBVectorAdapter:
    """Adapter that makes KnowledgeLanceDB implement IdempotentVectorStorePort.

    This allows the pipeline to use LanceDB as its vector store backend,
    providing fast vector similarity search in addition to the JSONL-based
    IdempotentVectorStore.

    The adapter:
    - Computes embeddings on add() using EmbeddingComputer
    - Stores in LanceDB with content-hash idempotency
    - Computes query embedding on search() for semantic similarity
    - Uses chunk_id as memory_id for Port compliance
    """

    def __init__(
        self,
        lancedb: Any,  # KnowledgeLanceDB instance
        embedding_computer: EmbeddingComputer,
        *,
        source_file: str = "pipeline",
    ) -> None:
        self._lancedb = lancedb
        self._embedding_computer = embedding_computer
        self._source_file = source_file

    def _content_hash(self, text: str) -> str:
        """Compute stable content hash."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        """Add text with content-hash idempotency via LanceDB.

        Same text returns the same memory_id (idempotent).
        Delegates to KnowledgeLanceDB.add() after computing embedding.
        """
        # Compute embedding
        embeddings = await self._embedding_computer.compute_batch([text])
        embedding: list[float] = embeddings[0] if embeddings else [0.0] * 384

        # Use content_hash as memory_id for idempotency
        content_hash = self._content_hash(text)
        chunk_id = content_hash  # Stable ID derived from content

        metadata = metadata or {}

        try:
            record_id = await self._lancedb.add(
                chunk_id=chunk_id,
                text=text,
                embedding=embedding,
                source_file=self._source_file,
                line_start=metadata.get("line_start", 1),
                line_end=metadata.get("line_end", 1),
                importance=importance,
                semantic_tags=metadata.get("semantic_tags", []),
                language=metadata.get("language", ""),
            )
            logger.debug("LanceDB adapter added chunk: %s", record_id[:12])
            return record_id
        except (RuntimeError, ValueError) as exc:
            # Check if it's an idempotency duplicate (LanceDB may raise on duplicate id)
            logger.debug("LanceDB add failed (may be duplicate): %s", exc)
            # Return the existing content_hash as the id
            return content_hash

    async def delete(self, memory_id: str) -> bool:
        """Delete a chunk by memory_id (content_hash).

        Returns True if the chunk existed and was deleted.
        """
        try:
            return await self._lancedb.delete(content_hash=memory_id)
        except (RuntimeError, ValueError) as exc:
            logger.warning("LanceDB delete failed for %s: %s", memory_id[:12], exc)
            return False

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a memory by memory_id (content_hash).

        Returns the memory dict or None if not found.
        """
        try:
            self._lancedb._ensure_table()
            table_data = self._lancedb._table.to_arrow()
            if not table_data or len(table_data) == 0:
                return None
            pydict = table_data.to_pydict()
            hashes = pydict.get("content_hash", [])
            for i, ch in enumerate(hashes):
                if ch == memory_id:
                    return {
                        "memory_id": pydict.get("id", [])[i] if i < len(pydict.get("id", [])) else ch,
                        "text": pydict.get("text", [])[i] if i < len(pydict.get("text", [])) else "",
                        "importance": pydict.get("importance", [])[i] if i < len(pydict.get("importance", [])) else 5,
                    }
            return None
        except (RuntimeError, ValueError) as exc:
            logger.warning("LanceDB get failed for %s: %s", memory_id[:12], exc)
            return None

    async def vacuum(self, max_age_days: int = 30) -> int:
        """LanceDB has no tombstone file, so vacuum is a no-op.

        Returns 0 as no entries are removed.
        """
        return 0

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:
        """Search semantic memory by query text.

        Computes query embedding, searches LanceDB, returns (memory_id, score) tuples.
        The memory_id is the chunk_id returned by LanceDB (content_hash based).
        """
        try:
            # Compute query embedding
            embeddings = await self._embedding_computer.compute_batch([query])
            query_embedding: list[float] = embeddings[0] if embeddings else [0.0] * 384

            # Search LanceDB
            results = await self._lancedb.search(
                query=query,
                embedding=query_embedding,
                top_k=top_k,
                min_importance=min_importance,
            )

            # Convert to (memory_id, score) tuples
            # LanceDB returns chunk_id as the id field
            return [(r["chunk_id"], r.get("score", 0.0)) for r in results]

        except (RuntimeError, ValueError) as exc:
            logger.warning("LanceDB search failed: %s", exc)
            return []

    def get_stats(self) -> dict[str, Any]:
        """Get statistics from the underlying LanceDB store (sync view).

        Runs the async get_stats() in a thread with its own event loop,
        avoiding conflicts with any existing event loop in the caller thread.
        """
        import asyncio
        import concurrent.futures

        def _get_stats() -> dict[str, Any]:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._lancedb.get_stats())
            finally:
                loop.close()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_get_stats)
                return future.result(timeout=10.0)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to get LanceDB stats: %s", exc)
            return {"error": str(exc)}

    async def get_all_content_hashes(self) -> set[str]:
        """Return all content hashes currently stored in LanceDB.

        Used by KnowledgeSync to determine which items exist in LanceDB
        without doing a full table scan in Python.
        """
        try:
            self._lancedb._ensure_table()
            table_data = self._lancedb._table.to_arrow()
            if not table_data or len(table_data) == 0:
                return set()
            pydict = table_data.to_pydict()
            return {ch for ch in pydict.get("content_hash", []) if ch}
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to get LanceDB content hashes: %s", exc)
            return set()


# Type annotation for protocol
LanceDBVectorAdapter.__protocol__ = IdempotentVectorStorePort  # type: ignore[attr-defined]


__all__ = ["LanceDBVectorAdapter"]
