"""5-tier hot-asset cache system for KernelOne context assembly.

Five-layer caching architecture (blueprint §5.7):
    1. Session Continuity Cache  - in-process memory, stores SessionContinuityPack
    2. Repo Map Cache            - workspace-persistent, stores RepoMapSnapshot
    3. Symbol Index Cache        - workspace-persistent, stores per-file symbol index
    4. Hot Slice Cache           - in-process LRU, recently used code slices
    5. Continuity Projection Cache - workspace-persistent, session continuity projections

What NOT to cache (from blueprint §5.7):
    - Graph truth
    - Source-of-truth session rows
    - Public contract ownership

All text serialization uses UTF-8. All cache operations are async.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time as _time
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.context.cache_policies import (
    HOT_SLICE_TTL_SECONDS,
    PROJECTION_TTL_SECONDS,
    REPO_MAP_TTL_SECONDS,
    SESSION_CONTINUITY_TTL_SECONDS,
    SYMBOL_INDEX_TTL_SECONDS,
)
from polaris.kernelone.context.context_os.bounded_cache import (
    BoundedCache,
    LRUBoundedCache,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier and entry definitions
# ---------------------------------------------------------------------------


# Sentinel object for the AssetCachePort protocol type hint only
class _CacheTierMarker(str):
    """Marker class so AssetCachePort can reference CacheTier in type hints."""


class CacheTier(StrEnum):
    """5-tier cache tier enum (blueprint §5.7).

    Values are lowercase strings matching tier names.
    Both ``CacheTier.HOT_SLICE`` and ``CacheTier("hot_slice")`` are valid.
    """

    SESSION_CONTINUITY = "session_continuity"
    REPO_MAP = "repo_map"
    SYMBOL_INDEX = "symbol_index"
    HOT_SLICE = "hot_slice"
    PROJECTION = "projection"


def _as_tier(value: str | CacheTier) -> CacheTier:
    """Coerce a string or CacheTier to a CacheTier enum member."""
    if isinstance(value, CacheTier):
        return value
    return CacheTier(value)


@dataclass
class CacheEntry:
    """Core cache entry shared across all tiers.

    Attributes:
        key: Stable cache key (e.g. "slice|src/main.py|10|30").
        value: The cached value (any type; callers are responsible for
            serialising/deserialising for persistent tiers).
        tier: Which tier this entry lives in.
        created_at: Unix timestamp when the entry was created.
        last_accessed: Unix timestamp of the most recent access.
        access_count: Number of times this entry has been accessed.
        ttl_seconds: Time-to-live in seconds. None means no expiry.
        file_mtime: When set, the source file's modification time at cache-write
            time. Used for mtime-based cache invalidation in the Hot Slice tier.
    """

    key: str
    value: Any
    tier: CacheTier
    created_at: float
    last_accessed: float
    access_count: int = 0
    ttl_seconds: float | None = None
    file_mtime: float | None = None  # source file mtime at cache-write time

    def is_expired(self) -> bool:
        """Return True if the entry has passed its TTL."""
        if self.ttl_seconds is None:
            return False
        return _time.time() - self.created_at > self.ttl_seconds

    def touch(self) -> None:
        """Update access metadata (called on every cache hit)."""
        self.last_accessed = _time.time()
        self.access_count += 1


# ---------------------------------------------------------------------------
# AssetCachePort protocol (blueprint §5.7)
# ---------------------------------------------------------------------------


class AssetCachePort(Protocol):
    """Abstract cache interface required by WorkingSetAssembler.

    All five tiers must be addressable. Tier None means invalidate across
    all tiers for the given key.
    """

    async def get(self, key: str, tier: CacheTier) -> Any | None:
        """Retrieve a cached value, or None on miss / expiry."""
        ...

    async def set(
        self,
        key: str,
        tier: CacheTier,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """Store a value in the specified tier."""
        ...

    async def invalidate(self, key: str, tier: CacheTier | None = None) -> None:
        """Remove a key from a specific tier, or all tiers if tier is None."""
        ...

    async def get_or_compute(
        self,
        key: str,
        tier: CacheTier,
        factory: Callable[[], Any],
        ttl: float | None = None,
    ) -> Any:
        """Return cached value, or compute with factory and cache the result."""
        ...


# ---------------------------------------------------------------------------
# TieredAssetCacheManager
# ---------------------------------------------------------------------------

# Default TTLs per tier — imported from cache_policies (authority source)
_DEFAULT_HOT_SLICE_TTL = HOT_SLICE_TTL_SECONDS
_DEFAULT_PROJECTION_TTL = PROJECTION_TTL_SECONDS
_DEFAULT_REPO_MAP_TTL = REPO_MAP_TTL_SECONDS
_DEFAULT_SYMBOL_INDEX_TTL = SYMBOL_INDEX_TTL_SECONDS
_DEFAULT_SESSION_CONTINUITY_TTL = SESSION_CONTINUITY_TTL_SECONDS

# Size limits (these remain here as they are not in cache_policies)
_DEFAULT_HOT_SLICE_MAX_ENTRIES = 50  # blueprint says 50
_DEFAULT_REPO_MAP_MAX_ENTRIES = 5
_DEFAULT_SYMBOL_INDEX_MAX_ENTRIES = 20
_DEFAULT_PROJECTION_MAX_ENTRIES = 10


@dataclass
class CacheStats:
    """Hit/miss counters per tier, plus an aggregate."""

    hits_session_continuity: int = 0
    misses_session_continuity: int = 0
    hits_repo_map: int = 0
    misses_repo_map: int = 0
    hits_symbol_index: int = 0
    misses_symbol_index: int = 0
    hits_hot_slice: int = 0
    misses_hot_slice: int = 0
    hits_projection: int = 0
    misses_projection: int = 0
    evictions: int = 0  # total evictions across all tiers

    @property
    def total_hits(self) -> int:
        return (
            self.hits_session_continuity
            + self.hits_repo_map
            + self.hits_symbol_index
            + self.hits_hot_slice
            + self.hits_projection
        )

    @property
    def total_misses(self) -> int:
        return (
            self.misses_session_continuity
            + self.misses_repo_map
            + self.misses_symbol_index
            + self.misses_hot_slice
            + self.misses_projection
        )

    @property
    def hit_ratio(self) -> float:
        total = self.total_hits + self.total_misses
        if total == 0:
            return 0.0
        return self.total_hits / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits_session_continuity": self.hits_session_continuity,
            "misses_session_continuity": self.misses_session_continuity,
            "hits_repo_map": self.hits_repo_map,
            "misses_repo_map": self.misses_repo_map,
            "hits_symbol_index": self.hits_symbol_index,
            "misses_symbol_index": self.misses_symbol_index,
            "hits_hot_slice": self.hits_hot_slice,
            "misses_hot_slice": self.misses_hot_slice,
            "hits_projection": self.hits_projection,
            "misses_projection": self.misses_projection,
            "total_hits": self.total_hits,
            "total_misses": self.total_misses,
            "hit_ratio": round(self.hit_ratio, 4),
            "evictions": self.evictions,
        }


class TieredAssetCacheManager:
    """5-tier hot-asset cache manager.

    Implements the AssetCachePort protocol and satisfies the requirements of
    blueprint §5.7 Phase G.

    Tier mapping:
        SESSION_CONTINUITY  -> in-process OrderedDict (LRU, TTL)
        REPO_MAP            -> workspace-persistent JSON file per language
        SYMBOL_INDEX        -> workspace-persistent JSON file per file-path hash
        HOT_SLICE           -> in-process OrderedDict (LRU, max_entries=50, TTL=300s)
        PROJECTION          -> workspace-persistent JSON file per session-id

    Usage::

        cache = TieredAssetCacheManager(workspace="/repo")
        result = await cache.get_or_compute(
            key="slice:src/main.py:1:50",
            tier=CacheTier.HOT_SLICE,
            factory=lambda: expensive_read(),
            ttl=300.0,
        )
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        hot_slice_ttl: float = _DEFAULT_HOT_SLICE_TTL,
        projection_ttl: float = _DEFAULT_PROJECTION_TTL,
        repo_map_ttl: float = _DEFAULT_REPO_MAP_TTL,
        symbol_index_ttl: float = _DEFAULT_SYMBOL_INDEX_TTL,
        session_continuity_ttl: float = _DEFAULT_SESSION_CONTINUITY_TTL,
        hot_slice_max_entries: int = _DEFAULT_HOT_SLICE_MAX_ENTRIES,
        repo_map_max_entries: int = _DEFAULT_REPO_MAP_MAX_ENTRIES,
        symbol_index_max_entries: int = _DEFAULT_SYMBOL_INDEX_MAX_ENTRIES,
        projection_max_entries: int = _DEFAULT_PROJECTION_MAX_ENTRIES,
    ) -> None:
        self._workspace = str(workspace)
        self._hot_slice_ttl = hot_slice_ttl
        self._projection_ttl = projection_ttl
        self._repo_map_ttl = repo_map_ttl
        self._symbol_index_ttl = symbol_index_ttl
        self._session_continuity_ttl = session_continuity_ttl
        self._hot_slice_max = hot_slice_max_entries
        self._repo_map_max = repo_map_max_entries
        self._symbol_index_max = symbol_index_max_entries
        self._projection_max = projection_max_entries
        self._stats = CacheStats()

        # In-process tier stores (LRU OrderedDict for hot slices)
        self._hot_slices: OrderedDict[str, CacheEntry] = OrderedDict()
        # Session continuity tier with bounded cache
        self._session_continuity: BoundedCache[str, CacheEntry] = LRUBoundedCache(
            max_entries=256,
            max_bytes=100_000_000,  # 100MB
        )
        self._compute_lock = asyncio.Lock()

        # Workspace-persistent cache root
        self._cache_root = self._resolve_cache_root()

    # -------------------------------------------------------------------------
    # AssetCachePort implementation
    # -------------------------------------------------------------------------

    async def get(self, key: str, tier: CacheTier) -> Any | None:
        """Retrieve a cached value, or None on miss / expiry."""
        t = _as_tier(tier)

        if t == CacheTier.HOT_SLICE:
            return await self._get_hot_slice(key)
        if t == CacheTier.SESSION_CONTINUITY:
            return await self._get_session_continuity(key)
        if t == CacheTier.REPO_MAP:
            return await self._get_persistent(key, "repo_map")
        if t == CacheTier.SYMBOL_INDEX:
            return await self._get_persistent(key, "symbol_index")
        if t == CacheTier.PROJECTION:
            return await self._get_persistent(key, "projection")
        return None

    async def set(
        self,
        key: str,
        tier: CacheTier,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """Store a value in the specified tier."""
        t = _as_tier(tier)

        if t == CacheTier.HOT_SLICE:
            await self._put_hot_slice(key, value, ttl=ttl)
        elif t == CacheTier.SESSION_CONTINUITY:
            await self._put_session_continuity(key, value, ttl=ttl)
        elif t == CacheTier.REPO_MAP:
            await self._put_persistent(key, "repo_map", value, ttl=ttl)
        elif t == CacheTier.SYMBOL_INDEX:
            await self._put_persistent(key, "symbol_index", value, ttl=ttl)
        elif t == CacheTier.PROJECTION:
            await self._put_persistent(key, "projection", value, ttl=ttl)

    async def invalidate(self, key: str, tier: CacheTier | None = None) -> None:
        """Remove a key from a specific tier, or all tiers if tier is None."""
        if tier is None:
            await self.invalidate(key, CacheTier.HOT_SLICE)
            await self.invalidate(key, CacheTier.SESSION_CONTINUITY)
            await self._invalidate_persistent(key, "session_continuity")
            await self._invalidate_persistent(key, "symbol_index")
            # continuity pack uses "continuity:{key}" format in projection subdir
            await self._invalidate_persistent(f"continuity:{key}", "projection")
            return

        t = _as_tier(tier)

        if t == CacheTier.HOT_SLICE:
            self._hot_slices.pop(key, None)
        elif t == CacheTier.SESSION_CONTINUITY:
            self._session_continuity.pop(key, None)
        else:
            subdir = _tier_to_subdir(t)
            if subdir:
                await self._invalidate_persistent(key, subdir)

    async def get_or_compute(
        self,
        key: str,
        tier: CacheTier,
        factory: Callable[[], Any],
        ttl: float | None = None,
    ) -> Any:
        """Return cached value, or call factory and cache the result.

        Uses lock to prevent duplicate computation from concurrent calls.
        """
        async with self._compute_lock:
            result = await self.get(key, tier)
            if result is not None:
                return result
            computed = factory()
            await self.set(key, tier, computed, ttl=ttl)
            return computed

    # -------------------------------------------------------------------------
    # Hot Slice tier (LRU in-process)
    # -------------------------------------------------------------------------

    async def _get_hot_slice(self, key: str) -> Any | None:
        """LRU lookup with TTL + mtime invalidation."""
        entry = self._hot_slices.get(key)
        if entry is None:
            self._stats.misses_hot_slice += 1
            return None

        if entry.is_expired():
            self._hot_slices.pop(key, None)
            self._stats.misses_hot_slice += 1
            return None

        # mtime-based invalidation if recorded (file_mtime stores source file mtime as float)
        if entry.file_mtime is not None and entry.file_mtime > 0:
            mtime_recorded = entry.file_mtime
            path_part = _extract_path_from_key(key)
            if path_part:
                normalized = _normalize_path(path_part)
                if os.path.exists(normalized):
                    try:
                        current_mtime = os.path.getmtime(normalized)
                        if current_mtime > mtime_recorded:
                            self._hot_slices.pop(key, None)
                            self._stats.misses_hot_slice += 1
                            return None
                    except OSError:
                        pass

        self._hot_slices.move_to_end(key)
        entry.touch()
        self._stats.hits_hot_slice += 1
        return entry.value

    async def _put_hot_slice(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
        mtime_for_file: float | None = None,
    ) -> None:
        """Store with LRU eviction when at capacity."""
        effective_ttl = ttl if ttl is not None else self._hot_slice_ttl

        # Capture mtime if key looks like a file slice (format: "slice|{path}|{start}|{end}")
        # Prefer caller-supplied mtime; otherwise try to extract from key.
        mtime_recorded: float | None = mtime_for_file
        if mtime_recorded is None:
            path_part = _extract_path_from_key(key)
            if path_part:
                normalized = _normalize_path(path_part)
                if os.path.exists(normalized):
                    with contextlib.suppress(OSError):
                        mtime_recorded = os.path.getmtime(normalized)

        # Evict oldest entries until under capacity
        while len(self._hot_slices) >= self._hot_slice_max:
            oldest_key = next(iter(self._hot_slices))
            del self._hot_slices[oldest_key]
            self._stats.evictions += 1

        entry = CacheEntry(
            key=key,
            value=value,
            tier=CacheTier.HOT_SLICE,
            created_at=_time.time(),
            last_accessed=_time.time(),
            access_count=1,
            ttl_seconds=effective_ttl,
            file_mtime=mtime_recorded,
        )
        self._hot_slices.pop(key, None)  # remove if exists (re-add at end)
        self._hot_slices[key] = entry

    # -------------------------------------------------------------------------
    # Session Continuity tier (LRU in-process)
    # -------------------------------------------------------------------------

    async def _get_session_continuity(self, key: str) -> Any | None:
        """Session continuity lookup with TTL."""
        entry = self._session_continuity.get(key)
        if entry is None:
            self._stats.misses_session_continuity += 1
            return None
        if entry.is_expired():
            self._session_continuity.clear()
            self._stats.misses_session_continuity += 1
            return None
        entry.touch()
        self._stats.hits_session_continuity += 1
        return entry.value

    async def _put_session_continuity(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """Store in session continuity tier (bounded by LRUBoundedCache)."""
        entry = CacheEntry(
            key=key,
            value=value,
            tier=CacheTier.SESSION_CONTINUITY,
            created_at=_time.time(),
            last_accessed=_time.time(),
            access_count=1,
            ttl_seconds=ttl if ttl is not None else self._session_continuity_ttl,
        )
        self._session_continuity.put(key, entry)

    # -------------------------------------------------------------------------
    # Persistent tier helpers (REPO_MAP, SYMBOL_INDEX, PROJECTION)
    # -------------------------------------------------------------------------

    def _resolve_cache_root(self) -> Path:
        """Resolve workspace cache root: workspace/<metadata_dir>/cache/

        Note (P1-CTX-004 convergence): Unified path with KernelOneCacheManager facade.
        """
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        root = Path(self._workspace) / metadata_dir / "cache"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _persistent_path(self, key: str, subdir: str) -> Path:
        """Derive a stable filesystem path for a persistent cache entry.

        Keys may contain colons and slashes. We hash them to produce a
        safe filename while keeping the subdir structure meaningful.
        """
        key_hash = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
        safe_key = key_hash
        subdir_path = self._cache_root / subdir
        subdir_path.mkdir(parents=True, exist_ok=True)
        return subdir_path / f"{safe_key}.json"

    async def _get_persistent(self, key: str, subdir: str) -> Any | None:
        """Read a persistent cache entry; return None on miss or expiry."""
        path = self._persistent_path(key, subdir)
        if not path.exists():
            self._miss_for_tier(subdir)
            return None

        try:
            entry_data = await asyncio.to_thread(self._read_json_file, path)
        except (RuntimeError, ValueError) as exc:
            logger.warning("TieredAssetCacheManager: failed to read %s: %s", path, exc)
            self._miss_for_tier(subdir)
            return None

        created_at = entry_data.get("created_at", 0.0)
        ttl = entry_data.get("ttl_seconds")

        # Check TTL
        if ttl is not None and (_time.time() - created_at) > ttl:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                await asyncio.to_thread(path.unlink, missing_ok=True)
            self._miss_for_tier(subdir)
            return None

        # Update access metadata in-memory (best-effort)
        entry_data["last_accessed"] = _time.time()
        entry_data["access_count"] = entry_data.get("access_count", 0) + 1
        try:
            await asyncio.to_thread(self._write_json_file, path, entry_data)
        except (RuntimeError, ValueError) as exc:
            # best-effort: log cache metadata update failure
            logger.debug(
                "TieredAssetCacheManager: failed to update cache metadata for %s: %s",
                path,
                exc,
            )

        self._hit_for_tier(subdir)
        return entry_data.get("value")

    def _read_json_file(self, path: Path) -> dict[str, Any]:
        """Read JSON file synchronously (called via asyncio.to_thread)."""
        with open(path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data

    def _write_json_file(self, path: Path, entry_data: dict[str, Any]) -> None:
        """Write JSON file atomically (called via asyncio.to_thread)."""
        text = json.dumps(entry_data, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)

    async def _put_persistent(
        self,
        key: str,
        subdir: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        """Write a persistent cache entry atomically."""
        path = self._persistent_path(key, subdir)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry_data = {
            "key": key,
            "value": value,
            "created_at": _time.time(),
            "last_accessed": _time.time(),
            "access_count": 1,
            "ttl_seconds": ttl,
        }

        try:
            await asyncio.to_thread(self._write_json_file, path, entry_data)
        except (RuntimeError, ValueError) as exc:
            logger.warning("TieredAssetCacheManager: failed to write %s: %s", path, exc)

    async def _invalidate_persistent(self, key: str, subdir: str) -> None:
        """Remove a persistent cache entry."""
        path = self._persistent_path(key, subdir)
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            path.unlink(missing_ok=True)

    # -------------------------------------------------------------------------
    # Tier-specific named helpers (public, matching KernelOneCacheManager API)
    # -------------------------------------------------------------------------

    async def get_repo_map(self, workspace: str | Path, lang: str) -> dict[str, Any] | None:
        """Get cached repo map for a language, or None if not cached / expired."""
        key = f"repo_map:{lang}"
        return await self._get_persistent(key, "repo_map")

    async def put_repo_map(self, workspace: str | Path, lang: str, snapshot: dict[str, Any]) -> None:
        """Store repo map snapshot in persistent cache."""
        key = f"repo_map:{lang}"
        await self._put_persistent(key, "repo_map", snapshot, ttl=self._repo_map_ttl)

    async def get_symbol_index(self, file_path: Path) -> dict[str, Any] | None:
        """Get cached symbol index for a file, or None if not cached / expired."""
        key = f"symbol_index:{file_path}"
        return await self._get_persistent(key, "symbol_index")

    async def put_symbol_index(self, file_path: Path, index: dict[str, Any]) -> None:
        """Store symbol index in persistent cache."""
        key = f"symbol_index:{file_path}"
        await self._put_persistent(key, "symbol_index", index, ttl=self._symbol_index_ttl)

    async def get_hot_slice(self, key: str) -> Any | None:
        """Get hot slice by key."""
        return await self._get_hot_slice(key)

    async def put_hot_slice(self, key: str, content: Any, *, file_path: str | None = None) -> None:
        """Store content in hot slice cache."""
        # If file_path provided, embed mtime for sub-second precision invalidation
        mtime_recorded: float | None = None
        if file_path and os.path.exists(file_path):
            with contextlib.suppress(OSError):
                mtime_recorded = os.path.getmtime(file_path)
        await self._put_hot_slice(key, content, mtime_for_file=mtime_recorded)

    async def get_continuity_pack(self, session_id: str) -> dict[str, Any] | None:
        """Get continuity pack for a session."""
        key = f"continuity:{session_id}"
        return await self._get_persistent(key, "projection")

    async def put_continuity_pack(self, session_id: str, pack: dict[str, Any]) -> None:
        """Store continuity pack in projection cache."""
        key = f"continuity:{session_id}"
        await self._put_persistent(key, "projection", pack, ttl=self._projection_ttl)

    # -------------------------------------------------------------------------
    # Statistics and management
    # -------------------------------------------------------------------------

    async def get_stats(self) -> CacheStats:
        """Return current cache statistics snapshot."""
        return CacheStats(
            hits_session_continuity=self._stats.hits_session_continuity,
            misses_session_continuity=self._stats.misses_session_continuity,
            hits_repo_map=self._stats.hits_repo_map,
            misses_repo_map=self._stats.misses_repo_map,
            hits_symbol_index=self._stats.hits_symbol_index,
            misses_symbol_index=self._stats.misses_symbol_index,
            hits_hot_slice=self._stats.hits_hot_slice,
            misses_hot_slice=self._stats.misses_hot_slice,
            hits_projection=self._stats.hits_projection,
            misses_projection=self._stats.misses_projection,
            evictions=self._stats.evictions,
        )

    async def clear_tier(self, tier: CacheTier) -> None:
        """Clear all entries for a specific tier."""
        t = _as_tier(tier)

        if t == CacheTier.HOT_SLICE:
            self._hot_slices.clear()
        elif t == CacheTier.SESSION_CONTINUITY:
            self._session_continuity.clear()
        else:
            subdir = _tier_to_subdir(t)
            if subdir:
                subdir_path = self._cache_root / subdir
                if subdir_path.exists():
                    for file in subdir_path.iterdir():
                        if file.suffix == ".json":
                            with contextlib.suppress(OSError, ValueError, RuntimeError):
                                file.unlink(missing_ok=True)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _hit_for_tier(self, subdir: str) -> None:
        if subdir == "repo_map":
            self._stats.hits_repo_map += 1
        elif subdir == "symbol_index":
            self._stats.hits_symbol_index += 1
        elif subdir == "projection":
            self._stats.hits_projection += 1

    def _miss_for_tier(self, subdir: str) -> None:
        if subdir == "repo_map":
            self._stats.misses_repo_map += 1
        elif subdir == "symbol_index":
            self._stats.misses_symbol_index += 1
        elif subdir == "projection":
            self._stats.misses_projection += 1


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _tier_to_subdir(tier: CacheTier) -> str:
    """Map a cache tier to its persistent subdirectory name."""
    mapping = {
        CacheTier.REPO_MAP: "repo_map",
        CacheTier.SYMBOL_INDEX: "symbol_index",
        CacheTier.PROJECTION: "projection",
    }
    return mapping.get(tier, "")


def _normalize_path(path: str | Path) -> str:
    """Normalize a path for filesystem operations.

    Converts to absolute Path and resolves for cross-platform use.
    On Windows, this handles both forward-slash and backslash paths.
    """
    try:
        return str(Path(path).resolve())
    except (RuntimeError, ValueError):
        return str(path)


def _extract_path_from_key(key: str) -> str | None:
    """Extract a file path from a cache key for mtime-based invalidation.

    Supported key formats (using | as separator to avoid colon issues on Windows):
        "slice|{path}|{start}|{end}"
        "symbol_index|hot|{path}"
    """
    parts = key.split("|")
    if len(parts) >= 2 and parts[0] == "slice":
        # "slice|{path}|{start}|{end}" — path is everything from part 1 to part[-2]
        # (last part is end_line)
        path_part = "|".join(parts[1:-2]) if len(parts) > 3 else parts[1]
        return _normalize_path(path_part)
    if len(parts) >= 3 and parts[0] == "symbol_index" and parts[1] == "hot":
        return _normalize_path(parts[2])
    return None


# Re-export CacheTier as CacheTier so callers can reference it
CacheTierLiteral = CacheTier

__all__ = [
    "AssetCachePort",
    "CacheEntry",
    "CacheStats",
    "CacheTier",
    "TieredAssetCacheManager",
    "_as_tier",
]
