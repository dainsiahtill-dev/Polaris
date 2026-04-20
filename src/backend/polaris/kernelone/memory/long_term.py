"""Long-term memory system for cross-session knowledge accumulation.

This module provides persistent knowledge storage and retrieval across sessions,
supporting the intelligence enhancement phase (Phase 3).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class KnowledgeItem:
    """A piece of long-term knowledge.

    Represents a consolidated piece of information extracted from session events
    and stored for cross-session retrieval.

    Attributes:
        id: Unique identifier for this knowledge item.
        content: The actual knowledge content (text).
        source_session: Session ID this knowledge was extracted from.
        created_at: ISO timestamp when this item was created.
        last_accessed: ISO timestamp of last access (None if never accessed).
        access_count: Number of times this item has been accessed.
        tags: Tuple of tags for categorization.
        metadata: Additional metadata dictionary.
    """

    id: str
    content: str
    source_session: str
    created_at: str
    last_accessed: str | None = None
    access_count: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


class LongTermMemory:
    """Long-term memory for cross-session knowledge accumulation.

    This class provides mechanisms to consolidate session events into persistent
    knowledge, retrieve relevant knowledge items based on queries, and track
    access statistics for knowledge retention.

    Example:
        >>> memory = LongTermMemory(workspace="/path/to/workspace")
        >>> # Consolidate events from a session
        >>> items = await memory.consolidate(
        ...     session_id="sess_123",
        ...     session_events=[{"type": "tool_call", "content": "..."}]
        ... )
        >>> # Retrieve relevant knowledge
        >>> relevant = await memory.retrieve_relevant("Python async patterns")
    """

    _STORE_FILENAME = "long_term_memory.json"
    MAX_CACHE_ITEMS = 10000

    def __init__(self, workspace: str | None = None) -> None:
        """Initialize the long-term memory system.

        Args:
            workspace: Path to the workspace directory for persistent storage.
                If None, uses an in-memory store only.
        """
        self._workspace = workspace
        self._store_path: str | None = None
        if workspace:
            os.makedirs(workspace, exist_ok=True)
            self._store_path = os.path.join(workspace, self._STORE_FILENAME)
        self._cache: dict[str, KnowledgeItem] = {}
        self._index: dict[str, list[str]] = {}  # tag -> item_ids
        self._access_order: list[str] = []  # for LRU eviction
        self._loaded = False

    def _generate_id(self, content: str, session_id: str) -> str:
        """Generate a unique ID for a knowledge item.

        Args:
            content: The content to hash.
            session_id: The source session ID.

        Returns:
            A unique ID string.
        """
        raw = f"{session_id}:{content}:{uuid.uuid4()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _extract_tags(self, event: dict[str, Any]) -> tuple[str, ...]:
        """Extract tags from a session event.

        Args:
            event: The session event dictionary.

        Returns:
            Tuple of extracted tags.
        """
        tags: list[str] = []
        event_type = event.get("type", "")
        tags.append(event_type)

        if role := event.get("role"):
            tags.append(f"role:{role}")
        if tool := event.get("tool"):
            tags.append(f"tool:{tool}")

        if content := event.get("content", ""):
            words = content.split()[:5]
            tags.extend(words)

        return tuple(set(tags))

    def _extract_content(self, event: dict[str, Any]) -> str:
        """Extract searchable content from an event.

        Args:
            event: The session event dictionary.

        Returns:
            Extracted string content.
        """
        content_parts: list[str] = []

        if "content" in event:
            content_parts.append(str(event["content"]))
        if "message" in event:
            content_parts.append(str(event["message"]))
        if "result" in event:
            content_parts.append(str(event["result"]))
        if "error" in event:
            content_parts.append(f"Error: {event['error']}")

        return " | ".join(content_parts)

    def _now_iso(self) -> str:
        """Get current UTC time as ISO string.

        Returns:
            ISO format timestamp string.
        """
        return datetime.now(timezone.utc).isoformat()

    def _update_index(self, item: KnowledgeItem) -> None:
        """Update the tag index with a new item.

        Args:
            item: The knowledge item to index.
        """
        for tag in item.tags:
            if tag not in self._index:
                self._index[tag] = []
            if item.id not in self._index[tag]:
                self._index[tag].append(item.id)

    def _prune_cache(self) -> None:
        """Evict least recently used items when cache exceeds limit."""
        if len(self._cache) <= self.MAX_CACHE_ITEMS:
            return
        excess = len(self._cache) - self.MAX_CACHE_ITEMS
        to_evict = self._access_order[:excess]
        self._access_order = self._access_order[excess:]
        for item_id in to_evict:
            if item_id in self._cache:
                del self._cache[item_id]
        # Rebuild index for evicted items
        self._index = {}
        for item in self._cache.values():
            self._update_index(item)

    def _load_from_store(self) -> None:
        """Load knowledge items from persistent store into cache."""
        if self._loaded or not self._store_path:
            return

        if not os.path.exists(self._store_path):
            self._loaded = True
            return

        try:
            with open(self._store_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._loaded = True
            return

        items = data.get("items", [])
        for item_data in items:
            item = KnowledgeItem(
                id=item_data["id"],
                content=item_data["content"],
                source_session=item_data["source_session"],
                created_at=item_data["created_at"],
                last_accessed=item_data.get("last_accessed"),
                access_count=item_data.get("access_count", 0),
                tags=tuple(item_data.get("tags", [])),
                metadata=item_data.get("metadata", {}),
            )
            self._cache[item.id] = item
            self._update_index(item)

        index_data = data.get("index", {})
        if isinstance(index_data, dict):
            self._index = index_data

        access_order_data = data.get("access_order", [])
        if isinstance(access_order_data, list):
            self._access_order = access_order_data

        self._loaded = True

    def _save_to_store(self) -> None:
        """Persist cache and index to store."""
        if not self._store_path:
            return

        items_data = []
        for item in self._cache.values():
            items_data.append(
                {
                    "id": item.id,
                    "content": item.content,
                    "source_session": item.source_session,
                    "created_at": item.created_at,
                    "last_accessed": item.last_accessed,
                    "access_count": item.access_count,
                    "tags": list(item.tags),
                    "metadata": item.metadata,
                }
            )

        data = {
            "items": items_data,
            "index": self._index,
            "access_order": self._access_order,
        }

        temp_path = f"{self._store_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, self._store_path)

    async def consolidate(
        self,
        session_id: str,
        session_events: list[dict[str, Any]],
    ) -> list[KnowledgeItem]:
        """Consolidate session events into long-term knowledge.

        Extracts and stores knowledge items from session events, with deduplication
        based on content similarity.

        Args:
            session_id: The session identifier.
            session_events: List of event dictionaries from the session.

        Returns:
            List of newly created KnowledgeItem objects.
        """
        self._load_from_store()

        created_items: list[KnowledgeItem] = []
        seen_content: set[str] = {item.content for item in self._cache.values()}

        for event in session_events:
            content = self._extract_content(event)
            if not content or len(content) < 10:
                continue

            if content in seen_content:
                continue

            seen_content.add(content)
            tags = self._extract_tags(event)
            item_id = self._generate_id(content, session_id)

            item = KnowledgeItem(
                id=item_id,
                content=content,
                source_session=session_id,
                created_at=self._now_iso(),
                last_accessed=None,
                access_count=0,
                tags=tags,
                metadata={"original_event": event},
            )

            self._cache[item.id] = item
            self._update_index(item)
            self._access_order.append(item.id)
            created_items.append(item)

        if created_items:
            self._prune_cache()
            self._save_to_store()
            self._save_to_store()

        return created_items

    async def retrieve_relevant(
        self,
        query: str,
        limit: int = 10,
    ) -> list[KnowledgeItem]:
        """Retrieve relevant long-term memories for a query.

        Performs a simple keyword-based search across all knowledge items.

        Args:
            query: Search query string.
            limit: Maximum number of items to return (default 10).

        Returns:
            List of relevant KnowledgeItem objects, sorted by relevance.
        """
        self._load_from_store()

        query_lower = query.lower()
        query_terms = set(query_lower.split())

        scored: list[tuple[int, KnowledgeItem]] = []

        for item in self._cache.values():
            content_lower = item.content.lower()

            relevance = 0

            for term in query_terms:
                if term in content_lower:
                    relevance += 1
                if term in item.content:
                    relevance += 2

            for tag in item.tags:
                if query_lower in tag.lower():
                    relevance += 3

            if relevance > 0:
                scored.append((relevance, item))

        scored.sort(key=lambda x: (-x[0], x[1].created_at))
        results = [item for _, item in scored[:limit]]

        for item in results:
            await self.access_item(item.id)

        return results

    async def access_item(self, item_id: str) -> KnowledgeItem | None:
        """Record access to a knowledge item.

        Updates the last_accessed timestamp and increments access_count.

        Args:
            item_id: The ID of the knowledge item to access.

        Returns:
            Updated KnowledgeItem if found, None otherwise.
        """
        self._load_from_store()

        item = self._cache.get(item_id)
        if not item:
            return None

        updated_item = KnowledgeItem(
            id=item.id,
            content=item.content,
            source_session=item.source_session,
            created_at=item.created_at,
            last_accessed=self._now_iso(),
            access_count=item.access_count + 1,
            tags=item.tags,
            metadata=item.metadata,
        )

        self._cache[item_id] = updated_item
        # Move to end of access order (most recently used)
        if item_id in self._access_order:
            self._access_order.remove(item_id)
        self._access_order.append(item_id)
        self._save_to_store()

        return updated_item

    async def get_statistics(self) -> dict[str, Any]:
        """Get memory statistics.

        Returns:
            Dictionary containing:
                - total_items: Total number of knowledge items
                - total_sessions: Number of unique source sessions
                - total_accesses: Sum of all access counts
                - tag_counts: Dictionary mapping tags to their counts
                - items_by_session: Dictionary mapping sessions to item counts
        """
        self._load_from_store()

        total_items = len(self._cache)
        total_accesses = sum(item.access_count for item in self._cache.values())
        sessions: set[str] = {item.source_session for item in self._cache.values()}
        total_sessions = len(sessions)

        tag_counts: dict[str, int] = {}
        items_by_session: dict[str, int] = {}

        for item in self._cache.values():
            items_by_session[item.source_session] = items_by_session.get(item.source_session, 0) + 1
            for tag in item.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return {
            "total_items": total_items,
            "total_sessions": total_sessions,
            "total_accesses": total_accesses,
            "tag_counts": tag_counts,
            "items_by_session": items_by_session,
        }

    async def clear_session(self, session_id: str) -> int:
        """Remove all knowledge items from a specific session.

        Args:
            session_id: The session ID to clear.

        Returns:
            Number of items removed.
        """
        self._load_from_store()

        to_remove = [item_id for item_id, item in self._cache.items() if item.source_session == session_id]

        for item_id in to_remove:
            del self._cache[item_id]

        for tag in self._index:
            self._index[tag] = [iid for iid in self._index[tag] if iid not in to_remove]

        self._access_order = [iid for iid in self._access_order if iid not in to_remove]

        if to_remove:
            self._save_to_store()

        return len(to_remove)
