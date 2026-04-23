"""Content-addressable store for ContextOS Memory Architecture v2.1.

This module provides deduplication and lifecycle management for string content
used across the ContextOS session substrate. Content is addressed by SHA-256
hash (truncated to 24 hex chars), enabling:

- String interning: identical content always maps to the same ContentRef
- Reference counting: automatic eviction when no active refs remain
- Byte-budget enforcement: configurable memory ceiling with mixed eviction
- Serialization round-trip: export/import for snapshot persistence

Architecture:
    ContentRef  -> lightweight frozen handle (hash, size, mime, encoding)
    ContentStore -> the actual intern table with ref-counted storage
    RefTracker  -> per-owner ref acquisition/release wrapper

Usage:
    store = ContentStore()
    ref = store.intern("some large text block")
    tracker = RefTracker(store)
    tracker.acquire(ref)
    # ... later ...
    tracker.release_all()
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)

__all__ = ["ContentRef", "ContentStore", "RefTracker"]


@dataclass(frozen=True, slots=True)
class ContentRef:
    """Immutable handle to content stored in a ContentStore.

    Attributes:
        hash: SHA-256 truncated to first 24 hex characters.
        size: Original content length in bytes (UTF-8 encoded).
        mime: Guessed MIME type (e.g. ``"text/plain"``, ``"application/json"``).
        encoding: Text encoding, defaults to ``"utf-8"``.
    """

    hash: str
    size: int
    mime: str
    encoding: str = "utf-8"


class ContentStore:
    """Content-addressable string store with reference counting and byte-budget eviction.

    The store is the single write path for interning content. Identical strings
    always resolve to the same :class:`ContentRef`, enabling deduplication. When
    the byte budget is exceeded, entries with zero active refs are evicted first,
    followed by lowest-ref-count + oldest-access-time entries.

    Args:
        max_entries: Maximum number of distinct entries (default 500).
        max_bytes: Maximum total bytes across all stored strings (default 50 MB).
    """

    def __init__(
        self,
        max_entries: int = 500,
        max_bytes: int = 50_000_000,
        workspace: str = ".",
    ) -> None:
        self._store: dict[str, str] = {}
        self._refs: dict[str, int] = {}
        self._access: dict[str, float] = {}
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._workspace = workspace
        self._current_bytes: int = 0
        self._dedup_saved_bytes: int = 0
        self._hits: int = 0
        self._misses: int = 0
        self._evict_count: int = 0
        # threading.Lock retained for sync contexts; async.Lock added for async contexts
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Async-safe helpers
    # ------------------------------------------------------------------

    @property
    def _async_lock_ctx(self):
        """Return the async lock context manager for write operations."""
        return self._async_lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def intern(self, content: str) -> ContentRef:
        """Intern a string and return its canonical ContentRef.

        If the same string was already interned the existing ref is returned
        and the reference count is incremented. Hash collisions (different
        content mapping to the same truncated hash) raise :class:`RuntimeError`.

        Args:
            content: The string content to intern.

        Returns:
            A frozen :class:`ContentRef` handle for the content.

        Raises:
            RuntimeError: If a hash collision is detected.
        """
        raw = content.encode("utf-8")
        size = len(raw)
        h = hashlib.sha256(raw).hexdigest()[:24]
        mime = self._guess_mime(content)

        with self._lock:
            if h in self._store:
                # Collision detection
                if self._store[h] != content:
                    raise RuntimeError(
                        f"Hash collision detected for {h}: stored content differs "
                        f"from incoming content (stored {len(self._store[h])}B, "
                        f"incoming {size}B)"
                    )
                self._refs[h] += 1
                self._access[h] = time.monotonic()
                self._hits += 1
                self._dedup_saved_bytes += size
                return ContentRef(hash=h, size=size, mime=mime)

            # New entry -- ensure capacity first
            self._evict_if_needed(size)

            self._store[h] = content
            self._refs[h] = 1
            self._access[h] = time.monotonic()
            self._current_bytes += size

            if size > 1_000_000:
                logger.warning(
                    "ContentStore large intern: hash=%s size=%d bytes=%d entries=%d",
                    h[:8],
                    size,
                    self._current_bytes,
                    len(self._store),
                )

            return ContentRef(hash=h, size=size, mime=mime)

    def get(self, ref: ContentRef) -> str:
        """Retrieve content by ref, returning a placeholder if evicted.

        Eviction placeholders count as misses for statistics.

        Args:
            ref: The ContentRef whose content to retrieve.

        Returns:
            The original string, or ``"<evicted:{hash}>"`` if no longer stored.
        """
        with self._lock:
            content = self._store.get(ref.hash)
            if content is not None:
                self._access[ref.hash] = time.monotonic()
                return content
            self._misses += 1
            return f"<evicted:{ref.hash}>"

    def get_if_present(self, ref: ContentRef) -> str | None:
        """Retrieve content without affecting miss statistics.

        Unlike :meth:`get`, this returns ``None`` silently when the content has
        been evicted and does **not** count as a miss.

        Args:
            ref: The ContentRef whose content to retrieve.

        Returns:
            The original string, or ``None`` if not found.
        """
        with self._lock:
            content = self._store.get(ref.hash)
            if content is not None:
                self._access[ref.hash] = time.monotonic()
                return content
            return None

    def release(self, ref: ContentRef) -> None:
        """Decrement the reference count for a stored entry.

        When the ref count drops to zero the entry becomes eligible for
        eviction but is **not** immediately removed, allowing potential reuse.

        Args:
            ref: The ContentRef to release.
        """
        with self._lock:
            count = self._refs.get(ref.hash, 0)
            if count <= 1:
                self._refs[ref.hash] = 0
            else:
                self._refs[ref.hash] = count - 1

    def release_all(self, refs: Iterable[ContentRef]) -> None:
        """Batch-release multiple refs.

        Args:
            refs: An iterable of ContentRef instances to release.
        """
        for ref in refs:
            self.release(ref)

    # ------------------------------------------------------------------
    # Async-safe write/read API
    # ------------------------------------------------------------------

    async def write(self, key: str, content: str) -> ContentRef:
        """Async-safe write content to store with key-based lookup.

        Stores content with a mapping from key to content hash for easy retrieval.
        Uses async lock to ensure thread safety in async contexts.

        Args:
            key: Logical key for the content (used for tracking and lookup).
            content: The string content to store.

        Returns:
            A frozen :class:`ContentRef` handle for the content.
        """
        async with self._async_lock:
            ref = self.intern(content)
            # Store key -> hash mapping for key-based lookup
            self._store[key] = content
            return ref

    async def read(self, key: str) -> str:
        """Async-safe read content from store by key.

        First attempts to look up by key directly, then by hash of key.
        This supports both direct key lookup and hash-based lookup.

        Args:
            key: The key or hash of the content to retrieve.

        Returns:
            The original string, or empty string if not found.
        """
        async with self._async_lock:
            # First try direct key lookup
            content = self._store.get(key)
            if content is not None:
                self._access[key] = time.monotonic()
                return content
            # Then try lookup by hash of key
            key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            content = self._store.get(key_hash)
            if content is not None:
                self._access[key_hash] = time.monotonic()
                return content
            self._misses += 1
            return ""

    async def delete(self, key: str) -> bool:
        """Async-safe delete content from store.

        Args:
            key: The key or hash of the content to delete.

        Returns:
            True if content was deleted, False if not found.
        """
        async with self._async_lock:
            if key in self._store:
                self._remove_entry(key)
                return True
            # Also try by hash
            key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            if key_hash in self._store:
                self._remove_entry(key_hash)
                return True
            return False

    async def update(self, key: str, content: str) -> ContentRef:
        """Async-safe update content in store.

        Deletes existing content and interns the new content.

        Args:
            key: The key or hash of the content to update.
            content: The new string content to store.

        Returns:
            A frozen :class:`ContentRef` handle for the new content.
        """
        async with self._async_lock:
            # Remove by key if exists
            if key in self._store:
                self._remove_entry(key)
            # Also try by hash
            key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            if key_hash in self._store:
                self._remove_entry(key_hash)
            ref = self.intern(content)
            # Store key -> content mapping for key-based lookup
            self._store[key] = content
            return ref

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def export_content_map(self, refs: set[str]) -> dict[str, str]:
        """Export a subset of the store for serialization.

        Only entries whose hash is in *refs* are included in the returned
        mapping.

        Args:
            refs: Set of hash strings to export.

        Returns:
            A ``{hash: content}`` dictionary suitable for persistence.
        """
        return {h: self._store[h] for h in refs if h in self._store}

    @classmethod
    def from_content_map(cls, content_map: dict[str, str]) -> ContentStore:
        """Reconstruct a ContentStore from a persisted content map.

        Each entry is interned with a reference count of 1.

        Args:
            content_map: A ``{hash: content}`` dictionary previously produced
                by :meth:`export_content_map`.

        Returns:
            A new ContentStore populated with the provided content.
        """
        store = cls()
        for h, content in content_map.items():
            raw = content.encode("utf-8")
            size = len(raw)
            actual_hash = hashlib.sha256(raw).hexdigest()[:24]
            if actual_hash != h:
                logger.warning(
                    "Hash mismatch in from_content_map: key=%s actual=%s, skipping",
                    h,
                    actual_hash,
                )
                continue
            store._store[h] = content
            store._refs[h] = 1
            store._access[h] = time.monotonic()
            store._current_bytes += size
        return store

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, int | float]:
        """Return a snapshot of store statistics.

        Returns:
            Dictionary with keys: ``entries``, ``bytes``, ``max_bytes``,
            ``utilization``, ``dedup_saved_bytes``, ``hit_rate``,
            ``evict_count``.
        """
        total_lookups = self._hits + self._misses
        return {
            "entries": len(self._store),
            "bytes": self._current_bytes,
            "max_bytes": self._max_bytes,
            "utilization": self._current_bytes / self._max_bytes if self._max_bytes else 0.0,
            "dedup_saved_bytes": self._dedup_saved_bytes,
            "hit_rate": self._hits / total_lookups if total_lookups else 0.0,
            "evict_count": self._evict_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_if_needed(self, incoming_bytes: int) -> None:
        """Evict entries to make room for *incoming_bytes*.

        Strategy:
            1. Evict all entries with ``ref_count == 0`` (oldest first).
            2. If still over budget, evict lowest-ref-count + oldest-access
               entries until there is enough headroom.

        Args:
            incoming_bytes: Size of the entry about to be inserted.
        """
        needed = self._current_bytes + incoming_bytes
        entry_limit = self._max_entries - 1  # reserve one slot for the new entry

        # Phase 1: evict zero-ref entries, oldest first
        if len(self._store) >= self._max_entries or needed > self._max_bytes:
            zero_ref = [h for h in self._store if self._refs.get(h, 0) == 0]
            # Sort by access time ascending (oldest first)
            zero_ref.sort(key=lambda h: self._access.get(h, 0.0))
            for h in zero_ref:
                if len(self._store) <= entry_limit and self._current_bytes + incoming_bytes <= self._max_bytes:
                    break
                self._remove_entry(h)
                logger.warning("Evicted zero-ref entry %s", h)

        # Phase 2: if still over budget, evict lowest ref-count + oldest
        if len(self._store) >= self._max_entries or self._current_bytes + incoming_bytes > self._max_bytes:
            all_entries = list(self._store.keys())
            # Primary key: ref count (ascending); secondary key: access time (ascending)
            all_entries.sort(key=lambda h: (self._refs.get(h, 0), self._access.get(h, 0.0)))
            for h in all_entries:
                if len(self._store) <= entry_limit and self._current_bytes + incoming_bytes <= self._max_bytes:
                    break
                self._remove_entry(h)
                logger.warning(
                    "Evicted entry %s (ref_count=%d) to make room",
                    h,
                    self._refs.get(h, 0),
                )

    def _remove_entry(self, h: str) -> None:
        """Remove a single entry from the store by hash.

        Args:
            h: The truncated SHA-256 hash key.
        """
        content = self._store.pop(h, None)
        if content is not None:
            self._current_bytes -= len(content.encode("utf-8"))
            self._evict_count += 1
        self._refs.pop(h, None)
        self._access.pop(h, None)

    @staticmethod
    def _guess_mime(content: str) -> str:
        """Heuristic MIME type detection based on content prefix.

        Detection order:
            1. ``application/json`` -- starts with ``{`` or ``[``
            2. ``application/xml`` -- starts with ``<?xml`` or ``<``
            3. ``text/x-code`` -- contains ``def ``, ``class ``, ``import ``,
               or ``function ``
            4. ``text/plain`` -- default fallback

        Args:
            content: The string to classify.

        Returns:
            A MIME type string.
        """
        stripped = content.lstrip()
        if stripped.startswith(("{", "[")):
            return "application/json"
        if stripped.startswith(("<?xml", "<")) and not stripped.startswith("<evicted:"):
            return "application/xml"
        # Code detection: look for common keywords near the start
        first_line = content.split("\n", 1)[0] if content else ""
        if any(kw in first_line for kw in ("def ", "class ", "import ", "function ")):
            return "text/x-code"
        return "text/plain"


class RefTracker:
    """Per-owner reference tracker wrapping a :class:`ContentStore`.

    Maintains a set of actively-acquired hash strings and provides batch
    release semantics. Each call to :meth:`acquire` registers the ref in this
    tracker and increments the store ref count; :meth:`release` decrements
    both.

    Args:
        store: The backing ContentStore instance.
    """

    def __init__(self, store: ContentStore) -> None:
        self._store = store
        self._active: set[str] = set()
        # RefTracker is used in both sync and async contexts; keep threading.Lock
        # for sync safety and add async.Lock for async contexts
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()

    def acquire(self, ref: ContentRef) -> ContentRef:
        """Register *ref* as actively held by this tracker.

        The ref must already exist in the store (i.e., have been interned).
        Attempting to acquire a ref whose hash is not present in the store
        raises :class:`ValueError` to prevent silent data corruption.

        If the ref's hash is already tracked, the store ref count is still
        incremented (the caller may need the extra count for its own purposes).

        Args:
            ref: The ContentRef to acquire. Must have been previously interned.

        Returns:
            The same ContentRef, for ergonomic chaining.

        Raises:
            ValueError: If the ref's hash is not present in the store.
        """
        with self._lock, self._store._lock:
            if ref.hash not in self._store._store:
                raise ValueError(
                    f"Cannot acquire ref {ref.hash}: hash not found in store. Refs must be interned before acquisition."
                )
            self._active.add(ref.hash)
            self._store._refs[ref.hash] = self._store._refs.get(ref.hash, 0) + 1
            self._store._access[ref.hash] = time.monotonic()
        return ref

    def release(self, ref: ContentRef) -> None:
        """Release a single ref from this tracker.

        Removes the hash from the active set and decrements the store ref
        count.

        Args:
            ref: The ContentRef to release.
        """
        with self._lock:
            self._active.discard(ref.hash)
            self._store.release(ref)

    def release_all(self) -> None:
        """Release all actively tracked refs in one batch."""
        with self._lock:
            active_copy = list(self._active)
            for h in active_copy:
                ref = ContentRef(hash=h, size=0, mime="text/plain")
                self._store.release(ref)
            self._active.clear()

    def collect_refs_for_persist(self) -> set[str]:
        """Return the set of hash strings for all currently active refs.

        This is used during serialization to determine which content entries
        must be exported.

        Returns:
            A set of 24-character hex hash strings.
        """
        with self._lock:
            return set(self._active)
