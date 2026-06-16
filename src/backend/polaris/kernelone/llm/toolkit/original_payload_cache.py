"""Reversible original-payload cache (CCR — Compress-Cache-Retrieve).

This module closes the one-way compression loop. When ContextOS pointerizes a
large tool output / context block, the *original* bytes are not stored anywhere
retrievable (``SessionReceiptStore.get_receipt`` only stores receipt metadata,
not the original payload). This cache is the reverse channel: callers store the
original content here, get back a content-addressed marker of the form
``<<ref:HASH>>``, and any later ``context_retrieve`` tool call can resolve that
marker back to the original bytes.

Design (mirrors headroom CCR, kept as a GENERIC platform container — no
business/target-project logic, see CLAUDE.md §8):

- Key = ``blake3(content.encode("utf-8")).hexdigest()[:24]`` (content-addressed,
  so identical content collapses to one entry).
- Marker form = ``<<ref:HASH>>``.
- Default TTL ~= 300s; expiry is measured with ``time.monotonic()`` and purged
  lazily on every read/write (no background thread).
- In-memory by default; optional sqlite backing for cross-process durability.
- Thread-safe via a re-entrant lock.

All text I/O is explicit UTF-8.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import blake3

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "OriginalPayloadCache",
    "build_ref_marker",
    "capture_under",
    "get_default_cache",
    "hash_content",
    "strip_ref_markers",
]

# Default time-to-live for a cached original payload, in seconds.
DEFAULT_TTL_SECONDS: float = 300.0

# Length of the hex prefix used as the content-addressed key.
_HASH_PREFIX_LEN: int = 24

# Marker shapes this module is willing to unwrap to recover a bare ref.
# These align with the pointer/receipt markers injected elsewhere in ContextOS:
#   - ``<<ref:HASH>>``     (this cache, primary CCR marker)
#   - ``<<ccr:HASH>>``     (headroom-compatible alias)
#   - ``[receipt_ref:ID]`` (projection_engine.py injection)
#   - ``<receipt_ref:ID>`` (context_os models injection)
#   - ``[See path]``       (engine pointerization)
_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^<<\s*ref\s*:\s*(?P<ref>.+?)\s*>>$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^<<\s*ccr\s*:\s*(?P<ref>.+?)\s*>>$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\[\s*receipt_ref\s*:\s*(?P<ref>.+?)\s*\]$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^<\s*receipt_ref\s*:\s*(?P<ref>.+?)\s*>$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\[\s*See\s+(?P<ref>.+?)\s*\]$", re.IGNORECASE | re.DOTALL),
)


def hash_content(content: str) -> str:
    """Return the content-addressed key for ``content``.

    Uses blake3 (same family headroom uses) truncated to a stable hex prefix.
    """
    digest = blake3.blake3(content.encode("utf-8")).hexdigest()
    return digest[:_HASH_PREFIX_LEN]


def build_ref_marker(ref_hash: str) -> str:
    """Wrap a bare hash into the canonical ``<<ref:HASH>>`` marker."""
    return f"<<ref:{ref_hash}>>"


def strip_ref_markers(raw: str) -> str:
    """Unwrap any known pointer/receipt marker to recover the bare ref.

    Weak models frequently paste the whole marker (e.g. ``[receipt_ref:abc]``)
    into the ``ref`` argument instead of the inner id. This tolerates every
    marker shape ContextOS injects and returns the inner reference. If no known
    marker matches, the stripped input is returned unchanged.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    for pattern in _MARKER_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group("ref").strip()
    return text


@dataclass(slots=True)
class _Entry:
    """A single cached original payload with its expiry deadline."""

    content: str
    expires_at: float


class OriginalPayloadCache:
    """TTL-bounded, content-addressed cache of original (pre-pointerization) payloads.

    Generic platform container: it stores opaque strings keyed by their own
    content hash. It carries no notion of any specific project, file template,
    or domain model.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        sqlite_path: Path | None = None,
        max_entries: int = 4096,
    ) -> None:
        """Initialize the cache.

        Args:
            ttl_seconds: Lifetime of a cached payload. Values <= 0 fall back to
                the default TTL (a cache with no expiry would be a leak).
            sqlite_path: Optional path for durable, cross-process backing. When
                provided, payloads are mirrored to a content-addressed sqlite
                table. When None the cache is purely in-memory.
            max_entries: Soft cap on in-memory entries; the oldest-expiring
                entries are evicted first when the cap is exceeded.
        """
        self._ttl = float(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else DEFAULT_TTL_SECONDS
        self._max_entries = max(1, int(max_entries))
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self._sqlite_path = Path(sqlite_path) if sqlite_path is not None else None
        if self._sqlite_path is not None:
            self._init_sqlite()

    # ------------------------------------------------------------------
    # sqlite backing (optional)
    # ------------------------------------------------------------------

    def _init_sqlite(self) -> None:
        if self._sqlite_path is None:
            return
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ccr_originals (
                  ref_hash TEXT PRIMARY KEY,
                  content TEXT NOT NULL,
                  expires_at REAL NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        assert self._sqlite_path is not None
        return sqlite3.connect(str(self._sqlite_path), timeout=10.0)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def put(self, content: str) -> str:
        """Store an original payload and return its ``<<ref:HASH>>`` marker.

        Identical content is deduplicated to the same hash. Storing refreshes
        the TTL so that re-pointerizing the same content keeps it retrievable.
        """
        text = str(content or "")
        ref_hash = hash_content(text)
        deadline = self._now() + self._ttl
        with self._lock:
            self._purge_locked()
            self._entries[ref_hash] = _Entry(content=text, expires_at=deadline)
            self._evict_if_needed_locked()
            if self._sqlite_path is not None:
                self._sqlite_put(ref_hash, text, deadline)
        return build_ref_marker(ref_hash)

    def put_under(self, key: str, content: str) -> None:
        """Store ``content`` under an explicit, caller-chosen ``key``.

        Unlike :meth:`put` (which keys by content hash and returns the
        ``<<ref:HASH>>`` marker), this stores under a caller-supplied reference
        such as a ReceiptStore ``receipt_id``. It lets a later ``context_retrieve``
        resolve the *exact* pointer the model already sees in the transcript
        (e.g. ``[receipt_ref:ID]``) without changing that pointer's text —
        closing the pointerization loop in a floor-safe way (the prompt is
        unchanged). No-op for an empty key.
        """
        ref_key = str(key or "").strip()
        if not ref_key:
            return
        text = str(content or "")
        deadline = self._now() + self._ttl
        with self._lock:
            self._purge_locked()
            self._entries[ref_key] = _Entry(content=text, expires_at=deadline)
            self._evict_if_needed_locked()
            if self._sqlite_path is not None:
                self._sqlite_put(ref_key, text, deadline)

    def get(self, ref_or_marker: str) -> str | None:
        """Resolve a ref (bare hash or any known marker) to its original payload.

        Returns None when the ref is unknown or its entry has expired. Expired
        entries are purged lazily as a side effect of this read.
        """
        ref_hash = strip_ref_markers(ref_or_marker)
        if not ref_hash:
            return None
        with self._lock:
            self._purge_locked()
            entry = self._entries.get(ref_hash)
            if entry is not None:
                if entry.expires_at > self._now():
                    return entry.content
                # Expired: drop it.
                self._entries.pop(ref_hash, None)
            if self._sqlite_path is not None:
                return self._sqlite_get(ref_hash)
            return None

    def contains(self, ref_or_marker: str) -> bool:
        """Return True if a live (non-expired) entry exists for the ref."""
        return self.get(ref_or_marker) is not None

    def clear(self) -> None:
        """Drop all in-memory entries (and sqlite rows if backed). For tests."""
        with self._lock:
            self._entries.clear()
            if self._sqlite_path is not None:
                conn = self._connect()
                try:
                    conn.execute("DELETE FROM ccr_originals")
                    conn.commit()
                finally:
                    conn.close()

    def __len__(self) -> int:
        with self._lock:
            self._purge_locked()
            return len(self._entries)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _purge_locked(self) -> None:
        """Drop expired in-memory entries. Caller must hold the lock."""
        now = self._now()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    def _evict_if_needed_locked(self) -> None:
        """Bound memory: evict soonest-to-expire entries past the cap."""
        overflow = len(self._entries) - self._max_entries
        if overflow <= 0:
            return
        victims = sorted(self._entries.items(), key=lambda kv: kv[1].expires_at)[:overflow]
        for key, _entry in victims:
            self._entries.pop(key, None)

    def _sqlite_put(self, ref_hash: str, content: str, expires_at: float) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ccr_originals(ref_hash, content, expires_at) VALUES(?, ?, ?)",
                (ref_hash, content, expires_at),
            )
            conn.commit()
        finally:
            conn.close()

    def _sqlite_get(self, ref_hash: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT content, expires_at FROM ccr_originals WHERE ref_hash = ?",
                (ref_hash,),
            ).fetchone()
            if row is None:
                return None
            content, expires_at = str(row[0]), float(row[1])
            if expires_at <= self._now():
                conn.execute("DELETE FROM ccr_originals WHERE ref_hash = ?", (ref_hash,))
                conn.commit()
                return None
            # Promote back into the in-memory tier for subsequent reads.
            self._entries[ref_hash] = _Entry(content=content, expires_at=expires_at)
            return content
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Process-wide default cache (in-memory). Callers that want durability pass an
# explicit OriginalPayloadCache(sqlite_path=...). The default keeps the common
# in-process pointerize -> retrieve loop working with zero configuration.
# ---------------------------------------------------------------------------

_default_cache: OriginalPayloadCache | None = None
_default_lock = threading.Lock()


def get_default_cache() -> OriginalPayloadCache:
    """Return the process-wide in-memory CCR cache (lazily created)."""
    global _default_cache
    if _default_cache is None:
        with _default_lock:
            if _default_cache is None:
                _default_cache = OriginalPayloadCache()
    return _default_cache


def capture_under(key: str, content: str) -> None:
    """Mirror an offloaded original into the process CCR cache under ``key``.

    Intended as a ``ReceiptStore`` ``on_offload`` hook (T1-A producer loop
    closure): it stores ``content`` under the receipt ref ``key`` so a later
    ``context_retrieve`` resolves the same ``[receipt_ref:ID]`` pointer the model
    already sees. Best-effort — all errors are swallowed because CCR mirroring
    must never break the hot context-build / offload path.
    """
    try:
        get_default_cache().put_under(key, content)
    except Exception:  # noqa: BLE001 - best-effort cache mirror, never fatal.
        logger.debug("CCR capture_under failed for key=%s", key, exc_info=True)
