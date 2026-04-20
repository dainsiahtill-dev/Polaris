"""Idempotent Vector Store with Ghost-Data-Free Deletion.

Wraps AkashicSemanticMemory to provide:
1. Content-hash based deduplication (idempotent add)
2. Leverages AkashicSemanticMemory's built-in soft delete with _deleted_ids
3. Additional tombstone tracking for cross-instance consistency

Fixes the ghost data bug where deleted items would resurrect after restart
by using AkashicSemanticMemory's _deleted_ids + _compact_jsonl mechanism.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

# Reuse SemanticMemoryItem from akashic
from polaris.kernelone.fs.jsonl.locking import file_lock

from .protocols import IdempotentVectorStorePort

if TYPE_CHECKING:
    from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TombstonedItem:
    """A soft-deleted item marker written to JSONL."""

    memory_id: str
    content_hash: str
    deleted_at: datetime
    deleted: bool = True


class IdempotentVectorStore:
    """Idempotent vector storage with ghost-data-free soft delete.

    This wrapper around AkashicSemanticMemory provides:
    1. Content hash deduplication: same content always returns same memory_id
    2. Leverages AkashicSemanticMemory._deleted_ids for soft delete tracking
    3. Tombstone persistence for cross-instance consistency

    Usage::

        semantic = AkashicSemanticMemory(workspace=".")
        store = IdempotentVectorStore(semantic)

        # Idempotent add - same content returns same ID
        id1 = await store.add("Hello world", importance=5)
        id2 = await store.add("Hello world", importance=5)
        assert id1 == id2  # Same ID!

        # Soft delete (ghost-data-free)
        await store.delete(id1)

        # After restart, item stays deleted
        new_store = IdempotentVectorStore(semantic)
        result = await new_store.search("Hello world")
        assert result == []  # Deleted item not resurrected
    """

    def __init__(
        self,
        semantic_memory: AkashicSemanticMemory,
        *,
        tombstone_file: str | None = None,
    ) -> None:
        self._semantic = semantic_memory

        # Derive tombstone file path from semantic memory file
        memory_file = semantic_memory._memory_file
        self._tombstone_file = tombstone_file or os.path.join(
            os.path.dirname(memory_file) or ".",
            ".tombstones",
            os.path.basename(memory_file).replace(".jsonl", "_tombstones.jsonl"),
        )

        # In-memory hash index for O(1) deduplication
        # Maps content_hash -> memory_id for idempotent adds
        self._hash_index: dict[str, str] = {}
        # Maps memory_id -> content_hash for tracking
        self._memory_to_hash: dict[str, str] = {}

        # Async lock for thread-safe operations
        self._lock: asyncio.Lock = asyncio.Lock()

        # Load tombstones and seed hash index
        self._load_tombstones()
        self._seed_hash_index()

    def _content_hash(self, text: str) -> str:
        """Compute stable content hash for deduplication."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def _load_tombstones(self) -> None:
        """Load tombstones and update AkashicSemanticMemory._deleted_ids.

        This ensures deleted items don't resurrect after restart by:
        1. Reading tombstone records from JSONL
        2. Adding memory_ids to _deleted_ids (so _load skips them)
        3. Compacting the semantic memory JSONL if needed
        """
        if not os.path.exists(self._tombstone_file):
            return

        try:
            os.makedirs(os.path.dirname(self._tombstone_file) or ".", exist_ok=True)

            # Load tombstone memory_ids
            tombstoned_ids: set[str] = set()
            with open(self._tombstone_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("deleted"):
                        memory_id = data.get("memory_id", "")
                        if memory_id:
                            tombstoned_ids.add(memory_id)

            # Update AkashicSemanticMemory._deleted_ids so _load() skips these
            for memory_id in tombstoned_ids:
                if memory_id not in self._semantic._deleted_ids:
                    self._semantic._deleted_ids.add(memory_id)
                # Also remove from _items if already loaded (happens when _load()
                # runs before _load_tombstones due to AkashicSemanticMemory.__init__)
                if memory_id in self._semantic._items:
                    del self._semantic._items[memory_id]

            logger.debug("Loaded %d tombstones", len(tombstoned_ids))

        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load tombstones from %s: %s", self._tombstone_file, exc)

    def _seed_hash_index(self) -> None:
        """Seed hash index from existing semantic memory items."""
        for memory_id, item in self._semantic._items.items():
            # Compute hash from stored text
            content_hash = self._content_hash(item.text)
            self._hash_index[content_hash] = memory_id
            self._memory_to_hash[memory_id] = content_hash

    def _persist_tombstone(self, memory_id: str, content_hash: str) -> None:
        """Write tombstone entry to JSONL."""
        os.makedirs(os.path.dirname(self._tombstone_file) or ".", exist_ok=True)

        tombstone = TombstonedItem(
            memory_id=memory_id,
            content_hash=content_hash,
            deleted_at=datetime.now(timezone.utc),
        )

        lock_path = f"{self._tombstone_file}.lock"
        data = asdict(tombstone)
        data["deleted_at"] = data["deleted_at"].isoformat()

        with (
            file_lock(lock_path, timeout_sec=5.0),
            open(self._tombstone_file, "a", encoding="utf-8", newline="\n") as f,
        ):
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: int = 5,
    ) -> str:
        """Add a memory with content-hash deduplication.

        If the same content (same hash) was previously added, returns
        the existing memory_id instead of creating a duplicate (idempotent).
        """
        content_hash = self._content_hash(text)

        async with self._lock:
            # Check for existing entry (idempotent)
            if content_hash in self._hash_index:
                existing_id = self._hash_index[content_hash]
                logger.debug(
                    "Idempotent add: content_hash=%s maps to existing memory_id=%s",
                    content_hash[:8],
                    existing_id[:12],
                )
                return existing_id

            # Add to underlying semantic memory
            memory_id = await self._semantic.add(
                text,
                metadata=metadata,
                importance=importance,
            )

            # Update hash indexes
            self._hash_index[content_hash] = memory_id
            self._memory_to_hash[memory_id] = content_hash

            logger.debug(
                "Added new memory: memory_id=%s, content_hash=%s",
                memory_id[:12],
                content_hash[:8],
            )
            return memory_id

    async def delete(self, memory_id: str) -> bool:
        """Soft-delete a memory.

        Uses AkashicSemanticMemory.delete() which:
        1. Removes from in-memory _items
        2. Adds to _deleted_ids
        3. Triggers _compact_jsonl() to rewrite JSONL

        Also writes a tombstone for cross-instance consistency.

        Returns True if item existed, False otherwise.
        """
        async with self._lock:
            # Check if item exists
            if memory_id not in self._semantic._items:
                # Check if it was already deleted
                if memory_id in self._memory_to_hash:
                    logger.debug("Memory %s already deleted", memory_id[:12])
                    return True
                return False

            # Get content hash before removal
            content_hash = self._memory_to_hash.get(
                memory_id,
                self._content_hash(self._semantic._items[memory_id].text),
            )

            # Use AkashicSemanticMemory.delete() which handles:
            # - Removal from _items
            # - Addition to _deleted_ids
            # - Async JSONL compaction
            result = await self._semantic.delete(memory_id)
            if not result:
                return False

            # Write tombstone for persistence
            self._persist_tombstone(memory_id, content_hash)

            # Remove from hash indexes
            if content_hash in self._hash_index:
                del self._hash_index[content_hash]
            if memory_id in self._memory_to_hash:
                del self._memory_to_hash[memory_id]

            logger.debug(
                "Soft-deleted memory: memory_id=%s, content_hash=%s",
                memory_id[:12],
                content_hash[:8],
            )
            return True

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        min_importance: int = 1,
    ) -> list[tuple[str, float]]:
        """Search semantic memory by query text.

        Returns list of (memory_id, similarity_score) tuples sorted by relevance.
        Delegates to AkashicSemanticMemory.search() which already filters
        out items in _deleted_ids.
        """
        # Delegate to underlying semantic memory search
        results = await self._semantic.search(
            query,
            top_k=top_k * 2,  # Request more to account for deleted items
            min_importance=min_importance,
        )

        # Filter to only include items we track (not required but defensive)
        filtered: list[tuple[str, float]] = []
        for memory_id, score in results:
            # Only include if tracked in our hash index (active items)
            if memory_id in self._memory_to_hash:
                filtered.append((memory_id, score))
                if len(filtered) >= top_k:
                    break

        return filtered

    async def vacuum(self, max_age_days: int = 30) -> int:
        """Compact tombstone file by removing entries older than max_age_days.

        This rewrites the tombstone file to remove old tombstones.
        Does NOT affect the semantic memory JSONL file.

        Returns the number of tombstone entries removed.
        """
        if not os.path.exists(self._tombstone_file):
            return 0

        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
        removed = 0
        kept: list[str] = []

        try:
            lock_path = f"{self._tombstone_file}.lock"
            with file_lock(lock_path, timeout_sec=5.0), open(self._tombstone_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    deleted_at = data.get("deleted_at", "")
                    if deleted_at:
                        try:
                            ts = datetime.fromisoformat(deleted_at).timestamp()
                            if ts < cutoff:
                                removed += 1
                                continue
                        except (ValueError, TypeError):
                            # Malformed timestamp — keep the entry and let it be re-parsed next time
                            logger.debug("Vacuum: could not parse timestamp %r, keeping entry", deleted_at)
                    kept.append(line)

            # Rewrite tombstone file with kept entries
            with open(self._tombstone_file, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(kept)

            logger.info("Vacuumed %d old tombstone entries", removed)
            return removed

        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Vacuum failed: %s", exc)
            return 0

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about the idempotent store."""
        return {
            "semantic_stats": self._semantic.get_stats(),
            "hash_index_size": len(self._hash_index),
            "deleted_count": len(self._memory_to_hash),
            "tombstone_file": self._tombstone_file,
        }

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Retrieve a memory by ID.

        Returns the memory dict or None if not found / deleted.
        Delegates to AkashicSemanticMemory.get().
        """
        return await self._semantic.get(memory_id)


# Type annotation for protocol
IdempotentVectorStore.__protocol__ = IdempotentVectorStorePort  # type: ignore[attr-defined]


__all__ = ["IdempotentVectorStore"]
