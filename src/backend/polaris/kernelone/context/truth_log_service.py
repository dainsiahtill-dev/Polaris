"""TruthLogService - append-only truth log for ContextOS with semantic indexing."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@dataclass
class TruthLogIndexEntry:
    """Indexed truth log entry with semantic metadata."""

    entry_id: str
    entry: dict[str, Any]
    role: str | None = None
    event_type: str | None = None
    timestamp: datetime | None = None
    session_id: str | None = None
    searchable_text: str | None = None


@dataclass
class TruthLogSearchResult:
    """A search result from TruthLog semantic search."""

    entry: dict[str, Any]
    score: float
    matched_on: list[str]  # which fields matched


class TruthLogIndex:
    """Semantic index layer for TruthLog entries.

    Wraps AkashicSemanticMemory to provide embedding-based search
    over TruthLog entries, with support for filtering by time,
    event type, and role.

    The index is optional and lazily initialized - if embedding
    is unavailable, falls back to keyword-based search.
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        enable_vector_search: bool = True,
        index_session_id: str | None = None,
    ) -> None:
        self._workspace = str(workspace or ".")
        self._enable_vector = enable_vector_search
        self._session_id = index_session_id
        self._initialized = False
        self._lock = asyncio.Lock()

        # Lazy-loaded components
        self._semantic_memory: Any | None = None
        self._embedding_port: Any | None = None

        # In-memory keyword index as fallback
        self._keyword_index: dict[str, set[str]] = {}  # term -> entry_ids
        self._entries_by_id: dict[str, TruthLogIndexEntry] = {}

    def _get_embedding_port(self) -> Any | None:
        """Get embedding port (lazy initialization)."""
        if self._embedding_port is None:
            try:
                from polaris.kernelone.llm.embedding import get_default_embedding_port

                self._embedding_port = get_default_embedding_port()
            except (RuntimeError, ValueError, TypeError):
                logger.debug("Could not get default embedding port for TruthLogIndex")
                return None
        return self._embedding_port

    def _init_semantic_memory(self) -> None:
        """Initialize semantic memory backend (lazy)."""
        if self._initialized:
            return
        self._initialized = True

        if not self._enable_vector:
            return

        try:
            from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory

            self._semantic_memory = AkashicSemanticMemory(
                workspace=self._workspace,
                memory_file=f"runtime/semantic/truthlog_index_{self._session_id or 'default'}.jsonl",
                enable_vector_search=True,
            )
            logger.debug("TruthLogIndex semantic memory initialized")
        except (ImportError, TypeError, OSError) as exc:
            logger.debug("Could not initialize AkashicSemanticMemory for TruthLogIndex: %s", exc)
            self._semantic_memory = None

    def _extract_searchable_text(self, entry: dict[str, Any]) -> str:
        """Extract searchable text from a truth log entry."""
        parts: list[str] = []

        # Extract common fields that contain meaningful text
        for field_name in ("content", "text", "message", "response", "summary", "description"):
            if entry.get(field_name):
                parts.append(str(entry[field_name]))

        # Handle nested structures
        if "data" in entry and isinstance(entry["data"], dict):
            for field_name in ("content", "text", "message", "result"):
                if field_name in entry["data"] and entry["data"][field_name]:
                    parts.append(str(entry["data"][field_name]))

        return " ".join(parts)

    def _extract_metadata(self, entry: dict[str, Any]) -> tuple[str | None, str | None, datetime | None]:
        """Extract role, event_type, and timestamp from entry."""
        role = entry.get("role") or entry.get("speaker") or entry.get("agent")
        event_type = entry.get("type") or entry.get("event_type") or entry.get("kind")
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = None
        elif not isinstance(timestamp, datetime):
            timestamp = None
        return role, event_type, timestamp

    def _tokenize(self, text: str) -> set[str]:
        """Simple tokenizer for keyword indexing."""
        import re

        # Split on whitespace and punctuation, lowercase
        tokens = re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower())
        return {t for t in tokens if len(t) >= 2}

    def _index_entry(self, entry_id: str, entry: dict[str, Any]) -> None:
        """Index a single entry for keyword search."""
        searchable_text = self._extract_searchable_text(entry)
        role, event_type, timestamp = self._extract_metadata(entry)

        index_entry = TruthLogIndexEntry(
            entry_id=entry_id,
            entry=entry,
            role=role if isinstance(role, str) else None,
            event_type=event_type if isinstance(event_type, str) else None,
            timestamp=timestamp if isinstance(timestamp, datetime) else None,
            searchable_text=searchable_text,
        )

        self._entries_by_id[entry_id] = index_entry

        # Update keyword index
        tokens = self._tokenize(searchable_text)
        for token in tokens:
            if token not in self._keyword_index:
                self._keyword_index[token] = set()
            self._keyword_index[token].add(entry_id)

    async def add_entry(self, entry: dict[str, Any], entry_id: str | None = None) -> str:
        """Add an entry to the semantic index.

        Args:
            entry: The truth log entry to index
            entry_id: Optional ID, generated if not provided

        Returns:
            The entry_id used for indexing
        """
        async with self._lock:
            if entry_id is None:
                entry_id = f"tl_{len(self._entries_by_id)}_{datetime.now(timezone.utc).timestamp()}"

            self._init_semantic_memory()

            # Index for keyword search
            self._index_entry(entry_id, entry)

            # Index in semantic memory if available
            if self._semantic_memory is not None:
                searchable_text = self._extract_searchable_text(entry)
                if searchable_text:
                    try:
                        role, event_type, _ = self._extract_metadata(entry)
                        await self._semantic_memory.add(
                            searchable_text,
                            importance=5,
                            metadata={
                                "entry_id": entry_id,
                                "role": role,
                                "event_type": event_type,
                            },
                        )
                    except (RuntimeError, ValueError, TypeError) as exc:
                        logger.debug("Failed to add entry to semantic memory: %s", exc)

            return entry_id

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        role_filter: str | None = None,
        event_type_filter: str | None = None,
        time_range: tuple[datetime, datetime] | None = None,
        min_score: float = 0.0,
    ) -> list[TruthLogSearchResult]:
        """Search the truth log with semantic similarity.

        Args:
            query: Search query text
            top_k: Maximum number of results to return
            role_filter: Only return entries from this role
            event_type_filter: Only return entries of this event type
            time_range: Only return entries within this time range (start, end)
            min_score: Minimum similarity score threshold

        Returns:
            List of search results, sorted by score descending
        """
        results: list[TruthLogSearchResult] = []

        async with self._lock:
            self._init_semantic_memory()

            # Try semantic search first
            if self._semantic_memory is not None and query.strip():
                try:
                    semantic_results = await self._semantic_memory.search(query, top_k=top_k * 2)
                    for item in semantic_results:
                        entry_id = item.metadata.get("entry_id") if hasattr(item, "metadata") else None
                        if entry_id and entry_id in self._entries_by_id:
                            entry_data = self._entries_by_id[entry_id]
                            if self._passes_filters(entry_data, role_filter, event_type_filter, time_range):
                                results.append(
                                    TruthLogSearchResult(
                                        entry=entry_data.entry,
                                        score=getattr(item, "score", 1.0),
                                        matched_on=["semantic_embedding"],
                                    )
                                )
                except (RuntimeError, ValueError, TypeError) as exc:
                    logger.debug("Semantic search failed, falling back to keyword: %s", exc)

            # Keyword search fallback / supplement
            query_tokens = self._tokenize(query)
            for token in query_tokens:
                if token in self._keyword_index:
                    for entry_id in self._keyword_index[token]:
                        idx_entry: TruthLogIndexEntry | None = self._entries_by_id.get(entry_id)
                        if (
                            idx_entry
                            and self._passes_filters(idx_entry, role_filter, event_type_filter, time_range)
                            and not any(r.entry == idx_entry.entry for r in results)
                        ):
                            results.append(
                                TruthLogSearchResult(
                                    entry=idx_entry.entry,
                                    score=0.5,  # Keyword match score
                                    matched_on=[f"keyword:{token}"],
                                )
                            )

        # Sort by score and limit
        results.sort(key=lambda r: r.score, reverse=True)
        return [r for r in results if r.score >= min_score][:top_k]

    def _passes_filters(
        self,
        entry: TruthLogIndexEntry,
        role_filter: str | None,
        event_type_filter: str | None,
        time_range: tuple[datetime, datetime] | None,
    ) -> bool:
        """Check if entry passes all filters."""
        if role_filter and entry.role and role_filter.lower() not in entry.role.lower():
            return False
        if event_type_filter and entry.event_type and event_type_filter.lower() not in entry.event_type.lower():
            return False
        if time_range and entry.timestamp:
            start, end = time_range
            if not (start <= entry.timestamp <= end):
                return False
        return True

    def query_by_role(self, role: str) -> list[dict[str, Any]]:
        """Get all entries from a specific role."""
        return [e.entry for e in self._entries_by_id.values() if e.role and role.lower() in e.role.lower()]

    def query_by_event_type(self, event_type: str) -> list[dict[str, Any]]:
        """Get all entries of a specific event type."""
        return [
            e.entry for e in self._entries_by_id.values() if e.event_type and event_type.lower() in e.event_type.lower()
        ]

    def query_by_time_range(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Get all entries within a time range."""
        return [e.entry for e in self._entries_by_id.values() if e.timestamp and start <= e.timestamp <= end]

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Get the N most recent entries by insertion order."""
        all_entries = list(self._entries_by_id.values())
        all_entries.sort(key=lambda e: e.entry_id, reverse=True)
        return [e.entry for e in all_entries[:n]]

    def clear(self) -> None:
        """Clear the index."""
        self._entries_by_id.clear()
        self._keyword_index.clear()
        self._initialized = False


class TruthLogService:
    """Append-only log recording the canonical truth of a session.

    Enforces:
    - Entries are immutable once appended.
    - Replay returns a deep copy to prevent external mutation.

    Supports optional semantic indexing via TruthLogIndex for
    embedding-based search over historical entries.
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        enable_semantic_index: bool = True,
        session_id: str | None = None,
    ) -> None:
        self._entries: list[dict[str, Any]] = []
        self._workspace = str(workspace or ".")
        self._session_id = session_id
        self._index: TruthLogIndex | None = None
        self._enable_semantic_index = enable_semantic_index
        self._index_lock = asyncio.Lock()
        self._index_initialized = False

    def _ensure_index(self) -> TruthLogIndex:
        """Lazily create the semantic index."""
        if self._index is None:
            self._index = TruthLogIndex(
                workspace=self._workspace,
                enable_vector_search=self._enable_semantic_index,
                index_session_id=self._session_id,
            )
        return self._index

    @staticmethod
    def _normalize_entry(entry: Any) -> dict[str, Any]:
        if hasattr(entry, "to_dict") and callable(entry.to_dict):
            value = entry.to_dict()
        elif isinstance(entry, dict):
            value = entry
        else:
            msg = f"Unsupported truth log entry type: {type(entry).__name__}"
            raise TypeError(msg)
        return deepcopy(dict(value))

    async def _index_entry_async(self, entry: dict[str, Any], entry_id: str) -> None:
        """Index an entry in the semantic index (async)."""
        if not self._enable_semantic_index:
            return
        index = self._ensure_index()
        await index.add_entry(entry, entry_id)

    def append(self, entry: dict[str, Any] | Any) -> None:
        """Append an entry to the truth log (sync)."""
        normalized = self._normalize_entry(entry)
        self._entries.append(normalized)
        # Fire-and-forget index update (sync wrapper)
        try:
            import asyncio

            # Store reference to prevent GC, but don't wait on it
            task = asyncio.create_task(self._index_entry_async(normalized, f"tl_{len(self._entries) - 1}"))

            def _handle_index_error(t):
                try:
                    t.result()
                except (RuntimeError, asyncio.InvalidStateError, asyncio.CancelledError) as e:
                    import logging

                    logging.getLogger(__name__).debug(f"Truth log background indexing failed (safe to ignore): {e}")

            task.add_done_callback(_handle_index_error)
        except RuntimeError:
            # No event loop running, skip indexing
            pass

    async def append_async(self, entry: dict[str, Any] | Any) -> None:
        """Append an entry to the truth log (async version with proper indexing)."""
        normalized = self._normalize_entry(entry)
        entry_id = f"tl_{len(self._entries)}"
        self._entries.append(normalized)
        await self._index_entry_async(normalized, entry_id)

    def append_many(self, entries: Iterable[dict[str, Any] | Any]) -> None:
        """Append multiple entries while preserving append-only ordering."""
        for entry in entries:
            self.append(entry)

    def replace(self, entries: Iterable[dict[str, Any] | Any]) -> None:
        """Replace the in-memory view with a canonical transcript snapshot."""
        self._entries = [self._normalize_entry(entry) for entry in entries]

    def get_entries(self) -> tuple[dict[str, Any], ...]:
        """Return all entries as an immutable tuple."""
        return tuple(self._normalize_entry(e) for e in self._entries)

    def replay(self) -> list[dict[str, Any]]:
        """Return a deep-copied list suitable for replay."""
        return [self._normalize_entry(e) for e in self._entries]

    # ── Semantic Search API ──────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        role: str | None = None,
        event_type: str | None = None,
        time_range: tuple[datetime, datetime] | None = None,
        min_score: float = 0.0,
    ) -> list[TruthLogSearchResult]:
        """Search the truth log with semantic similarity.

        Requires enable_semantic_index=True on construction.

        Args:
            query: Search query text
            top_k: Maximum results to return
            role: Filter by role (e.g., "director", "pm")
            event_type: Filter by event type (e.g., "tool_call", "decision")
            time_range: Filter by time range (start, end)
            min_score: Minimum similarity score

        Returns:
            List of search results sorted by score descending
        """
        if not self._enable_semantic_index:
            logger.warning("search called but semantic index is disabled")
            return []
        index = self._ensure_index()
        return await index.search(
            query,
            top_k=top_k,
            role_filter=role,
            event_type_filter=event_type,
            time_range=time_range,
            min_score=min_score,
        )

    def query_by_role(self, role: str) -> list[dict[str, Any]]:
        """Get all entries from a specific role."""
        return self._ensure_index().query_by_role(role)

    def query_by_event_type(self, event_type: str) -> list[dict[str, Any]]:
        """Get all entries of a specific event type."""
        return self._ensure_index().query_by_event_type(event_type)

    def query_by_time_range(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Get all entries within a time range."""
        return self._ensure_index().query_by_time_range(start, end)

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Get the N most recent entries."""
        return self._ensure_index().get_recent(n)
