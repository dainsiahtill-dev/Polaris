"""Knowledge Store Synchronization.

Provides bidirectional sync between IdempotentVectorStore (JSONL) and
KnowledgeLanceDB (LanceDB), enabling transparent migration and coexistence.

Architecture:
- IdempotentVectorStore: source-of-truth persistence (JSONL, durable)
- KnowledgeLanceDB: fast vector search layer (LanceDB, ephemeral-rebuildable)
- Sync reconciles by content_hash (the idempotent key shared by both stores)

Usage::

    sync = KnowledgeSync(
        jsonl_store=vector_store,
        lancedb_adapter=lancedb_adapter,
        embedding_computer=computer,
    )
    # Sync JSONL items into LanceDB (e.g., after importing historical data into JSONL)
    await sync.sync_to_lancedb()
    # Full bidirectional reconciliation
    await sync.sync_bidirectional()
    # Pull LanceDB-only items back into JSONL (ghost-data cleanup)
    await sync.sync_from_lancedb()
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from polaris.kernelone.akashic.knowledge_pipeline.protocols import (
        IdempotentVectorStorePort,
    )

logger = logging.getLogger(__name__)

# Type alias for the LanceDB adapter (has get_all_content_hashes + add)
LanceDBAdapterLike = Any


@dataclass
class SyncStats:
    """Statistics from a sync operation."""

    direction: str  # "jsonl→lancedb" | "lancedb→jsonl" | "bidirectional"
    jsonl_total: int = 0
    lancedb_total: int = 0
    items_added_to_lancedb: int = 0
    items_added_to_jsonl: int = 0
    items_removed_from_lancedb: int = 0  # ghost data in LanceDB not in JSONL
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


class KnowledgeSync:
    """Bidirectional sync between IdempotentVectorStore and KnowledgeLanceDB.

    Both stores use content_hash (SHA256[:32]) as the idempotent deduplication key:
    - IdempotentVectorStore._hash_index maps content_hash → memory_id
    - KnowledgeLanceDB stores content_hash directly as the record key

    This means any item can exist in one or both stores, and sync reconciles
    the two by content_hash — no ID mapping table needed.

    The typical workflow is:
    1. Historical JSONL data → sync_to_lancedb() → populate LanceDB
    2. Ongoing: pipeline uses one store or the other (not both simultaneously)
    3. sync_bidirectional() → reconcile after mixed usage

    Ghost-data cleanup (LanceDB items not in JSONL) is done by sync_from_lancedb()
    with delete_orphan_lancedb=True.
    """

    def __init__(
        self,
        jsonl_store: IdempotentVectorStorePort,
        lancedb_adapter: LanceDBAdapterLike,
        embedding_computer: Any,  # EmbeddingComputer
        *,
        source_file: str = "sync",
    ) -> None:
        self._jsonl = jsonl_store
        self._lancedb = lancedb_adapter
        self._embedding = embedding_computer
        self._source_file = source_file

    def _content_hash(self, text: str) -> str:
        """Compute stable content hash."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    # -------------------------------------------------------------------------
    # JSONL → LanceDB sync
    # -------------------------------------------------------------------------

    async def sync_to_lancedb(
        self,
        *,
        batch_size: int = 32,
        overwrite: bool = False,
    ) -> SyncStats:
        """Sync items from JSONL (IdempotentVectorStore) into LanceDB.

        For each content_hash in JSONL that does NOT exist in LanceDB,
        compute embedding and add to LanceDB.

        Args:
            batch_size: Number of embeddings to compute per batch.
            overwrite: If False (default), skip items already in LanceDB.
                      If True, re-embed and update LanceDB records.

        Returns:
            SyncStats with counts and timing.
        """
        import time

        t0 = time.monotonic()
        stats = SyncStats(direction="jsonl→lancedb")

        # Get JSONL content hashes from the store's hash index
        # IdempotentVectorStore stores content_hash → memory_id in _hash_index
        jsonl_hashes = self._get_jsonl_content_hashes()
        stats.jsonl_total = len(jsonl_hashes)

        # Get LanceDB content hashes
        lancedb_hashes = await self._lancedb.get_all_content_hashes()
        stats.lancedb_total = len(lancedb_hashes)

        # Items to add: in JSONL but not in LanceDB (or overwrite mode)
        to_add = jsonl_hashes if overwrite else jsonl_hashes - lancedb_hashes

        if not to_add:
            logger.info("sync_to_lancedb: no items to add (already in sync)")
            stats.duration_ms = (time.monotonic() - t0) * 1000
            return stats

        # Collect texts to embed in batches
        texts_by_hash: dict[str, str] = {}
        for content_hash in to_add:
            text = self._get_text_by_content_hash(content_hash)
            if text is not None:
                texts_by_hash[content_hash] = text

        # Process in batches
        content_hashes = list(texts_by_hash.keys())
        for i in range(0, len(content_hashes), batch_size):
            batch = content_hashes[i : i + batch_size]
            batch_texts = [texts_by_hash[ch] for ch in batch]

            try:
                embeddings = await self._embedding.compute_batch(batch_texts)
            except (RuntimeError, ValueError) as exc:
                logger.warning("Embedding batch failed, using zeros: %s", exc)
                stats.errors.append(f"Embedding batch failed: {exc}")
                embeddings = [[0.0] * 384] * len(batch_texts)

            for content_hash, text, embedding in zip(batch, batch_texts, embeddings, strict=True):
                try:
                    await self._lancedb.add(
                        chunk_id=content_hash,
                        text=text,
                        embedding=embedding,
                        source_file=self._source_file,
                        line_start=1,
                        line_end=1,
                        importance=5,
                        semantic_tags=[],
                        language="",
                    )
                    stats.items_added_to_lancedb += 1
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Failed to sync item %s to LanceDB: %s", content_hash[:12], exc)
                    stats.errors.append(f"add failed for {content_hash[:12]}: {exc}")

        stats.duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "sync_to_lancedb complete: %d/%d items added in %.1fms",
            stats.items_added_to_lancedb,
            stats.jsonl_total,
            stats.duration_ms,
        )
        return stats

    # -------------------------------------------------------------------------
    # LanceDB → JSONL sync (ghost-data cleanup)
    # -------------------------------------------------------------------------

    async def sync_from_lancedb(
        self,
        *,
        delete_orphan_lancedb: bool = False,
    ) -> SyncStats:
        """Sync items from LanceDB back into JSONL (IdempotentVectorStore).

        For each content_hash in LanceDB that does NOT exist in JSONL's hash index,
        add to JSONL. This recovers LanceDB-only items (e.g., from a pipeline run
        that used --vector-store lancedb without a corresponding JSONL write).

        If delete_orphan_lancedb=True, also deletes LanceDB items whose content_hash
        is NOT in JSONL — these are ghost data in LanceDB.

        Args:
            delete_orphan_lancedb: If True, delete LanceDB orphans instead of importing
                                   them into JSONL. Use this for "LanceDB as truth, clean JSONL"
                                   workflows.

        Returns:
            SyncStats with counts and timing.
        """
        import time

        t0 = time.monotonic()
        stats = SyncStats(direction="lancedb→jsonl")

        # Get all LanceDB content hashes
        lancedb_hashes = await self._lancedb.get_all_content_hashes()
        stats.lancedb_total = len(lancedb_hashes)

        # Get JSONL content hashes
        jsonl_hashes = self._get_jsonl_content_hashes()
        stats.jsonl_total = len(jsonl_hashes)

        # LanceDB-only items: in LanceDB but not in JSONL
        lancedb_only = lancedb_hashes - jsonl_hashes

        if not lancedb_only:
            logger.info("sync_from_lancedb: no LanceDB-only items")
            stats.duration_ms = (time.monotonic() - t0) * 1000
            return stats

        if delete_orphan_lancedb:
            # Delete orphans from LanceDB (ghost-data cleanup)
            for content_hash in lancedb_only:
                try:
                    await self._lancedb.delete(content_hash)
                    stats.items_removed_from_lancedb += 1
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Failed to delete orphan %s from LanceDB: %s", content_hash[:12], exc)
                    stats.errors.append(f"delete orphan failed for {content_hash[:12]}: {exc}")
        else:
            # Import orphans into JSONL
            texts = self._get_lancedb_texts(lancedb_only)
            for content_hash, text in texts.items():
                try:
                    await self._jsonl.add(text, importance=5)
                    stats.items_added_to_jsonl += 1
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Failed to sync item %s to JSONL: %s", content_hash[:12], exc)
                    stats.errors.append(f"add to JSONL failed for {content_hash[:12]}: {exc}")

        stats.duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "sync_from_lancedb complete: %d added to JSONL, %d removed from LanceDB, %.1fms",
            stats.items_added_to_jsonl,
            stats.items_removed_from_lancedb,
            stats.duration_ms,
        )
        return stats

    # -------------------------------------------------------------------------
    # Bidirectional full reconciliation
    # -------------------------------------------------------------------------

    async def sync_bidirectional(self) -> SyncStats:
        """Full bidirectional sync: reconcile JSONL and LanceDB.

        Steps:
        1. sync_to_lancedb() — push JSONL items into LanceDB
        2. sync_from_lancedb(delete_orphan_lancedb=True) — clean ghost data from LanceDB

        This is the canonical "repair" operation after mixed usage of both stores.

        Returns:
            Aggregate SyncStats from both directions.
        """
        import time

        t0 = time.monotonic()
        stats = SyncStats(direction="bidirectional")

        # Step 1: JSONL → LanceDB (add missing items)
        to_stats = await self.sync_to_lancedb()
        stats.jsonl_total = to_stats.jsonl_total
        stats.lancedb_total = to_stats.lancedb_total
        stats.items_added_to_lancedb = to_stats.items_added_to_lancedb
        stats.errors.extend(to_stats.errors)

        # Step 2: LanceDB → JSONL (clean LanceDB ghost data)
        from_stats = await self.sync_from_lancedb(delete_orphan_lancedb=True)
        stats.items_added_to_jsonl = from_stats.items_added_to_jsonl
        stats.items_removed_from_lancedb = from_stats.items_removed_from_lancedb
        stats.errors.extend(from_stats.errors)

        stats.duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "sync_bidirectional complete: +%d LanceDB, -%d LanceDB orphans, %.1fms",
            stats.items_added_to_lancedb,
            stats.items_removed_from_lancedb,
            stats.duration_ms,
        )
        return stats

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_jsonl_content_hashes(self) -> set[str]:
        """Get all content hashes from IdempotentVectorStore's hash index.

        The hash index maps content_hash → memory_id. Both are needed for
        the content_hash lookup in sync operations.
        """
        # IdempotentVectorStore stores items in self._semantic._items
        # which is dict[memory_id, SemanticMemoryItem(text=..., importance=..., ...)]
        # We compute content_hash from text.
        hashes: set[str] = set()
        try:
            # _semantic is the AkashicSemanticMemory wrapped by IdempotentVectorStore
            semantic = self._jsonl._semantic  # type: ignore[attr-defined]
            for item in semantic._items.values():
                hashes.add(self._content_hash(item.text))
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to enumerate JSONL items: %s", exc)
        return hashes

    def _get_text_by_content_hash(self, content_hash: str) -> str | None:
        """Look up the original text for a given content_hash from JSONL store."""
        try:
            semantic = self._jsonl._semantic  # type: ignore[attr-defined]
            for item in semantic._items.values():
                if self._content_hash(item.text) == content_hash:
                    return item.text
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to look up text for %s: %s", content_hash[:12], exc)
        return None

    def _get_lancedb_texts(self, content_hashes: set[str]) -> dict[str, str]:
        """Get text for given content hashes from LanceDB."""
        try:
            import concurrent.futures

            def _load() -> dict[str, str]:
                self._lancedb._ensure_table()
                table_data = self._lancedb._table.to_arrow()
                if not table_data or len(table_data) == 0:
                    return {}
                pydict = table_data.to_pydict()
                result = {}
                hashes = pydict.get("content_hash", [])
                texts_list = pydict.get("text", [])
                for ch, txt in zip(hashes, texts_list, strict=True):
                    if ch in content_hashes and txt:
                        result[str(ch)] = str(txt)
                return result

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_load)
                return future.result(timeout=DEFAULT_SHORT_TIMEOUT_SECONDS)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to load LanceDB texts: %s", exc)
            return {}


__all__ = ["KnowledgeSync", "SyncStats"]
