"""Helper functions for Context OS."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
from typing import Any

from polaris.kernelone.context._token_estimator import estimate_tokens as _estimate_tokens
from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso

from .patterns import _CODE_PATH_RE, _LOW_SIGNAL_PATTERNS


def _normalize_text(value: Any) -> str:
    """Normalize text by collapsing whitespace and stripping."""
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def _trim_text(text: str, *, max_chars: int) -> str:
    """Trim text to max_chars, preserving head and tail with snip marker.

    Ensures output length never exceeds max_chars by computing tail as
    max_chars - snip_marker_len - head, guaranteeing head + tail + snip <= max_chars.
    """
    token = _normalize_text(text)
    if len(token) <= max_chars:
        return token

    snip_marker = " ...[snip]... "
    snip_marker_len = len(snip_marker)

    # For small max_chars (<=64), use 60/40 split to avoid tiny tails
    # For larger max_chars, use 72/28 split
    head_ratio = 0.6 if max_chars <= 64 else 0.72
    head = max(1, int(max_chars * head_ratio))

    # Explicitly ensure: head + snip_marker_len + tail <= max_chars
    tail = max(1, max_chars - snip_marker_len - head)

    head_content = token[:head]
    tail_content = token[-tail:] if tail > 0 else ""

    return f"{head_content.rstrip()}{snip_marker}{tail_content.lstrip()}"


def _slug(value: str) -> str:
    """Create a slug from value (lowercase, hyphen-separated)."""
    token = re.sub(r"[^a-z0-9]+", "-", _normalize_text(value).lower())
    return token.strip("-")[:48] or "item"


def _event_id(sequence: int, role: str, content: str) -> str:
    """Generate event ID from sequence, role, and content hash."""
    digest = hashlib.sha256(f"{sequence}:{role}:{content}".encode()).hexdigest()[:12]
    return f"evt_{sequence}_{digest}"


def _artifact_id(content: str) -> str:
    """Generate artifact ID from content hash for deduplication.

    Using content hash ensures that identical content from different events
    maps to the same artifact_id, preventing duplicate artifacts in the
    artifact_store.
    """
    return f"art_{hashlib.sha256(content.encode('utf-8')).hexdigest()[:10]}"


def _clamp_confidence(value: Any, default: float = 0.5) -> float:
    """Clamp confidence value to [0.0, 1.0] range."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _is_low_signal(text: str) -> bool:
    """Check if text matches low-signal patterns."""
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _LOW_SIGNAL_PATTERNS)


def _looks_like_large_payload(text: str, *, policy: Any) -> bool:
    """Check if text looks like a large payload (artifact candidate).

    Args:
        text: The text to check
        policy: StateFirstContextOSPolicy instance with threshold settings

    Returns:
        True if text appears to be a large payload
    """
    token = str(text or "")
    if not token:
        return False
    if len(token) >= policy.artifact_char_threshold:
        return True
    if _estimate_tokens(token) >= policy.artifact_token_threshold:
        return True
    stripped = token.lstrip()
    if stripped.startswith(("{", "[", "<html", "<!doctype", "```")):
        return True
    return token.count("\n") >= 18


def _extract_json_keys(text: str) -> tuple[str, ...]:
    """Extract top-level keys from JSON text.

    Args:
        text: JSON text to parse

    Returns:
        Tuple of up to 8 top-level keys from JSON object, or empty tuple.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ()
    except (RuntimeError, ValueError):
        logger = logging.getLogger(__name__)
        logger.warning("Unexpected error parsing JSON keys from text: %s", str(text)[:100])
        return ()
    if isinstance(payload, dict):
        return tuple(str(key).strip() for key in list(payload.keys())[:8] if str(key).strip())
    return ()


def _guess_artifact_type(text: str) -> str:
    """Guess artifact type from content structure.

    Args:
        text: Content to analyze

    Returns:
        Artifact type guess: "tool_result", "markup", "code", or "evidence"
    """
    stripped = str(text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "tool_result"
    if stripped.startswith("<"):
        return "markup"
    if "```" in stripped:
        return "code"
    return "evidence"


def _guess_mime(text: str) -> str:
    """Guess MIME type from content structure.

    Args:
        text: Content to analyze

    Returns:
        MIME type guess: "application/json", "text/html", "application/xml",
        "text/x-code", or "text/plain"
    """
    stripped = str(text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "application/json"
    if stripped.startswith("<html") or stripped.startswith("<!doctype"):
        return "text/html"
    if stripped.startswith("<"):
        return "application/xml"
    if "```" in stripped:
        return "text/x-code"
    return "text/plain"


def _extract_entities(text: str) -> tuple[str, ...]:
    """Extract code path entities from text.

    Args:
        text: Text to scan for code paths

    Returns:
        Tuple of unique normalized code paths found in text.
    """
    seen: set[str] = set()
    result: list[str] = []
    for match in _CODE_PATH_RE.findall(text):
        token = match[0] if isinstance(match, tuple) else match
        normalized = str(token).strip("`").replace("\\", "/").strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return tuple(result)


def _dedupe_state_entries(entries: list[Any], *, limit: int) -> tuple[Any, ...]:
    """Deduplicate state entries by path:value key.

    Args:
        entries: List of StateEntry objects to deduplicate
        limit: Maximum number of entries to return

    Returns:
        Tuple of deduplicated StateEntry objects, newest first within limit.
    """
    seen: set[str] = set()
    result: list[Any] = []
    for item in reversed(entries):
        key = f"{item.path}:{item.value}".lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    result.reverse()
    return tuple(result)


def get_metadata_value(
    metadata: dict[str, Any] | tuple[tuple[str, Any], ...],
    key: str,
    default: Any = None,
) -> Any:
    """Get a value from metadata, compatible with .get() interface.

    Supports both dict-format and tuple-format metadata.

    Args:
        metadata: Metadata as dict or tuple of (key, value) pairs
        key: The key to look up
        default: Default value if key not found

    Returns:
        The value associated with key, or default if not found
    """
    if isinstance(metadata, dict):
        return metadata.get(key, default)
    for k, v in metadata:
        if k == key:
            return v
    return default


class _StateAccumulator:
    """Accumulator for state entries with deduplication by path and value.

    Entries are capped at MAX_STATE_HISTORY to prevent unbounded growth
    across long-running sessions. When the cap is exceeded, the oldest
    superseded entries are pruned first.

    Cross-list deduplication: The same normalized value is stored only once,
    even when it would logically belong to multiple categories (e.g., a
    "blueprint" that matches both accepted_plan and deliverables patterns).
    This prevents 3x-4x content redundancy in working_state.
    """

    MAX_STATE_HISTORY: int = 50

    def __init__(self) -> None:
        self._entries: list[Any] = []
        self._last_by_path: dict[str, Any] = {}
        self._seen_value_hashes: dict[str, str] = {}  # value_hash -> entry_id

    def add(
        self,
        *,
        path: str,
        value: str,
        source_turns: tuple[str, ...],
        confidence: float,
        store: Any | None = None,
    ) -> Any | None:
        """Add a state entry, returning the entry or None if empty/duplicate.

        Uses models.StateEntry for the actual entry type (imported at runtime
        to avoid circular imports).

        Deduplication:
        1. Same path + same value → return existing entry (path-level dedup)
        2. Same value within same top-level group → return None (cross-list dedup)
           Top-level groups: task_state.*, user_profile.*, temporal_facts, active_entities
           This prevents 3x redundancy (same content in plan + loops + deliverables)
           while allowing orthogonal dimensions (preferences + goals) to coexist.
        """
        from .models_v2 import StateEntryV2 as StateEntry

        normalized = _normalize_text(value)
        if not normalized:
            return None

        # Cross-list dedup: skip if this exact value already exists under a path
        # in the SAME top-level group (e.g., task_state.accepted_plan vs task_state.open_loops)
        value_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        top_level = path.split(".")[0] if "." in path else path.split("::")[0]
        group_key = f"{top_level}:{value_hash}"
        if group_key in self._seen_value_hashes:
            return None

        previous = self._last_by_path.get(path)
        if previous is not None and previous.value == normalized:
            return previous

        # v2.1: intern value through ContentStore
        if store is not None:
            with contextlib.suppress(Exception):
                store.intern(normalized)

        entry = StateEntry(
            entry_id=f"fact_{len(self._entries) + 1}",
            path=path,
            value=normalized,
            source_turns=source_turns,
            confidence=max(0.0, min(1.0, confidence)),
            updated_at=_utc_now_iso(),
            supersedes=previous.entry_id if previous is not None else None,
        )
        self._entries.append(entry)
        self._last_by_path[path] = entry
        self._seen_value_hashes[group_key] = entry.entry_id

        # Prune oldest superseded entries when cap is exceeded
        if len(self._entries) > self.MAX_STATE_HISTORY:
            self._prune_oldest_superseded()

        return entry

    def _prune_oldest_superseded(self) -> None:
        """Remove oldest entries that have been superseded by newer values."""
        superseded_ids: set[str] = set()
        for entry in self._entries:
            sid = getattr(entry, "supersedes", None)
            if sid:
                superseded_ids.add(sid)

        if superseded_ids:
            self._entries = [e for e in self._entries if getattr(e, "entry_id", "") not in superseded_ids]
        else:
            # No superseded entries to prune; trim oldest entries
            excess = len(self._entries) - self.MAX_STATE_HISTORY
            if excess > 0:
                self._entries = self._entries[excess:]

    @property
    def entries(self) -> tuple[Any, ...]:
        """Return all accumulated entries as tuple."""
        return tuple(self._entries)
