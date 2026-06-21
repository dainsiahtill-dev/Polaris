"""LLM Event Deduplicator — prevents repeated audit entries for the same logical call.

Root cause: the LLMEventEmitter dual-write path (delegated emitter + fallback
emit_llm_event) can emit the same event twice when the
``_emits_canonical_llm_events`` flag is stale or mis-set.  The
OmniscientAuditBus dispatch loop and ``_emit_llm_event_to_disk`` can each
independently persist the same payload again.

Strategy: dedup key = ``(session_id, role, content_hash)`` inside a sliding
time window.  Only if the caller supplies *evidence* of a genuine re-invocation
(different ``call_id`` or timestamp gap > ``window_seconds``) do we allow a
second emission.

Public API:
    ``LLMEventDeduplicator`` — thread-safe, configurable window/threshold.
    ``get_global_llm_dedup`` / ``set_global_llm_dedup`` — module-level singleton.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SECONDS = 30.0
_DEFAULT_MAX_ENTRIES = 4096


def _content_hash(data: dict[str, Any] | None) -> str:
    """Stable short hash of the event payload (ignoring volatile fields)."""
    if not data:
        return "empty"
    # Strip fields that vary per-emission but don't change the logical identity
    stable = {k: v for k, v in data.items() if k not in {"call_id", "event_id", "timestamp", "ts", "seq", "elapsed_ms"}}
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class _DedupEntry:
    """Internal bookkeeping for one dedup key."""

    first_seen: float
    last_seen: float
    call_ids: set[str] = field(default_factory=set)
    count: int = 1


class LLMEventDeduplicator:
    """Sliding-window deduplicator for LLM audit events.

    Parameters
    ----------
    window_seconds:
        Minimum gap (in seconds) between two events with the same content hash
        before the second is considered a *distinct* call rather than a
        duplicate emission.  Default 30 s.
    max_entries:
        Hard cap on the in-memory dedup table.  Oldest entries are evicted
        when the cap is reached.  Default 4096.
    """

    def __init__(
        self,
        *,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._window = max(1.0, window_seconds)
        self._max_entries = max(64, max_entries)
        self._lock = threading.Lock()
        # key = (session_id, role, content_hash) -> _DedupEntry
        self._table: dict[tuple[str, str, str], _DedupEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_emit(
        self,
        *,
        session_id: str,
        role: str,
        event_data: dict[str, Any] | None,
        call_id: str = "",
        now: float | None = None,
    ) -> bool:
        """Return ``True`` if this event should be emitted (not a duplicate).

        A duplicate is an event whose ``(session_id, role, content_hash)``
        was already seen within the current sliding window **unless** the
        caller provides a different ``call_id`` (evidence of a genuinely
        separate LLM invocation).
        """
        ts = now if now is not None else time.time()
        c_hash = _content_hash(event_data)
        key = (session_id, role, c_hash)

        with self._lock:
            entry = self._table.get(key)
            if entry is None:
                self._table[key] = _DedupEntry(first_seen=ts, last_seen=ts, call_ids={call_id} if call_id else set())
                self._evict_if_needed()
                return True

            # Same call_id within window → duplicate
            if call_id and call_id in entry.call_ids and (ts - entry.last_seen) < self._window:
                logger.debug(
                    "[llm_dedup] Suppressed duplicate: session=%s role=%s hash=%s call_id=%s count=%d",
                    session_id,
                    role,
                    c_hash,
                    call_id,
                    entry.count,
                )
                entry.count += 1
                entry.last_seen = ts
                return False

            # Different call_id → evidence of a real re-invocation
            if call_id and call_id not in entry.call_ids:
                entry.call_ids.add(call_id)
                entry.count += 1
                entry.last_seen = ts
                return True

            # No call_id: rely purely on time window
            if (ts - entry.last_seen) < self._window:
                logger.debug(
                    "[llm_dedup] Suppressed duplicate (no call_id): session=%s role=%s hash=%s count=%d",
                    session_id,
                    role,
                    c_hash,
                    entry.count,
                )
                entry.count += 1
                entry.last_seen = ts
                return False

            # Window expired → treat as new call
            entry.first_seen = ts
            entry.last_seen = ts
            entry.call_ids = {call_id} if call_id else set()
            entry.count = 1
            return True

    def get_stats(self) -> dict[str, Any]:
        """Return dedup statistics for monitoring."""
        with self._lock:
            total_suppressed = sum(max(0, e.count - 1) for e in self._table.values())
            return {
                "active_keys": len(self._table),
                "total_suppressed": total_suppressed,
                "window_seconds": self._window,
                "max_entries": self._max_entries,
            }

    def reset(self) -> None:
        """Clear all state (for testing)."""
        with self._lock:
            self._table.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict oldest entries when the table exceeds max_entries."""
        if len(self._table) <= self._max_entries:
            return
        # Sort by last_seen ascending, remove oldest 25%
        sorted_keys = sorted(self._table, key=lambda k: self._table[k].last_seen)
        to_remove = len(sorted_keys) // 4 or 1
        for k in sorted_keys[:to_remove]:
            del self._table[k]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_dedup: LLMEventDeduplicator | None = None
_global_dedup_lock = threading.Lock()


def get_global_llm_dedup() -> LLMEventDeduplicator:
    """Get or create the global LLMEventDeduplicator singleton."""
    global _global_dedup
    if _global_dedup is not None:
        return _global_dedup
    with _global_dedup_lock:
        if _global_dedup is None:
            _global_dedup = LLMEventDeduplicator()
        return _global_dedup


def set_global_llm_dedup(dedup: LLMEventDeduplicator) -> None:
    """Replace the global singleton (for testing or custom config)."""
    global _global_dedup
    with _global_dedup_lock:
        _global_dedup = dedup


__all__ = [
    "LLMEventDeduplicator",
    "get_global_llm_dedup",
    "set_global_llm_dedup",
]
