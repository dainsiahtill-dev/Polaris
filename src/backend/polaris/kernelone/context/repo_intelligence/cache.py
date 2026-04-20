"""Persistent tags cache with mtime-based invalidation.

This module provides SQLite-based persistent caching for tags extraction,
integrating with TieredAssetCacheManager for workspace-persistent storage.

Cache structure:
- Tags are stored per-file with mtime tracking
- Cache hits when file mtime matches cached mtime
- Automatic invalidation when file is modified
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_VERSION = 1
TAGS_CACHE_DIR = f".polaris.kernelone.tags.cache.v{CACHE_VERSION}"

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class TagsCacheStats:
    """Statistics for tags cache operations."""

    hits: int = 0
    misses: int = 0
    sets: int = 0
    invalidations: int = 0
    errors: int = 0

    @property
    def total_requests(self) -> int:
        return self.hits + self.misses

    @property
    def hit_ratio(self) -> float:
        total = self.total_requests
        if total == 0:
            return 0.0
        return self.hits / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "invalidations": self.invalidations,
            "errors": self.errors,
            "total_requests": self.total_requests,
            "hit_ratio": round(self.hit_ratio, 4),
        }


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class TagsCacheEntry:
    """A cached tags entry with mtime tracking."""

    data: list[dict[str, Any]]
    mtime: float
    created_at: float
    last_accessed: float
    access_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "mtime": self.mtime,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TagsCacheEntry:
        return cls(
            data=d.get("data", []),
            mtime=d.get("mtime", 0.0),
            created_at=d.get("created_at", 0.0),
            last_accessed=d.get("last_accessed", 0.0),
            access_count=d.get("access_count", 0),
        )


# ---------------------------------------------------------------------------
# TagsCache
# ---------------------------------------------------------------------------


class TagsCache:
    """Persistent tags cache with mtime-based invalidation.

    This cache stores extracted tags per file and automatically invalidates
    when the file's mtime changes.

    Usage:
        cache = TagsCache(workspace="/repo")

        # Get tags (returns cached if mtime matches)
        tags = cache.get_tags("src/main.py")

        # Cache tags
        cache.set_tags("src/main.py", tags)
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.workspace = str(workspace)
        self._cache_dir = self._resolve_cache_dir(cache_dir)
        self._memory_cache: dict[str, TagsCacheEntry] = {}
        self._stats = TagsCacheStats()
        self._ensure_cache_dir()

    def _resolve_cache_dir(self, cache_dir: str | Path | None) -> Path:
        """Resolve cache directory path."""
        if cache_dir is not None:
            path = Path(cache_dir)
            if not path.is_absolute():
                path = Path(self.workspace) / path
            return path
        return Path(self.workspace) / TAGS_CACHE_DIR

    def _ensure_cache_dir(self) -> None:
        """Ensure cache directory exists."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "TagsCache: failed to create cache dir %s: %s",
                self._cache_dir,
                exc,
            )

    def _file_path(self, abs_path: str) -> Path:
        """Get cache file path for a source file."""
        # Use hash of absolute path to avoid filesystem path issues
        import hashlib

        path_hash = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{path_hash}.json"

    def _get_file_mtime(self, abs_path: str) -> float | None:
        """Get file modification time."""
        try:
            return os.path.getmtime(abs_path)
        except OSError:
            return None

    def get_tags(self, abs_path: str) -> list[dict[str, Any]] | None:
        """Get cached tags for a file.

        Returns cached tags if:
        - File exists in cache
        - File mtime matches cached mtime

        Args:
            abs_path: Absolute path to the source file.

        Returns:
            List of tag dicts, or None on cache miss.
        """
        cache_path = self._file_path(abs_path)
        file_mtime = self._get_file_mtime(abs_path)

        if file_mtime is None:
            self._stats.misses += 1
            return None

        # Check memory cache first
        entry = self._memory_cache.get(abs_path)
        if entry is not None and entry.mtime == file_mtime:
            self._stats.hits += 1
            entry.last_accessed = time.time()
            entry.access_count += 1
            return entry.data

        # Check disk cache
        if not cache_path.exists():
            self._stats.misses += 1
            return None

        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)

            entry = TagsCacheEntry.from_dict(data)

            # Check mtime
            if entry.mtime != file_mtime:
                self._stats.misses += 1
                # Invalidate stale cache
                self._invalidate_file(abs_path)
                return None

            # Update memory cache and stats
            self._stats.hits += 1
            entry.last_accessed = time.time()
            entry.access_count += 1
            self._memory_cache[abs_path] = entry
            return entry.data

        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "TagsCache: failed to read cache for %s: %s",
                abs_path,
                exc,
            )
            self._stats.errors += 1
            self._stats.misses += 1
            return None

    def set_tags(
        self,
        abs_path: str,
        tags: list[dict[str, Any]],
        *,
        file_mtime: float | None = None,
    ) -> None:
        """Cache tags for a file.

        Args:
            abs_path: Absolute path to the source file.
            tags: List of tag dicts to cache.
            file_mtime: Optional file mtime (fetched if not provided).
        """
        if file_mtime is None:
            file_mtime = self._get_file_mtime(abs_path)
            if file_mtime is None:
                return

        cache_path = self._file_path(abs_path)
        now = time.time()

        entry = TagsCacheEntry(
            data=tags,
            mtime=file_mtime,
            created_at=now,
            last_accessed=now,
            access_count=1,
        )

        # Update memory cache
        self._memory_cache[abs_path] = entry

        # Write to disk
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
            tmp = cache_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(cache_path)
            self._stats.sets += 1
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "TagsCache: failed to write cache for %s: %s",
                abs_path,
                exc,
            )
            self._stats.errors += 1

    def _invalidate_file(self, abs_path: str) -> None:
        """Invalidate cache for a specific file."""
        # Remove from memory cache
        self._memory_cache.pop(abs_path, None)

        # Remove disk cache
        cache_path = self._file_path(abs_path)
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            cache_path.unlink(missing_ok=True)

        self._stats.invalidations += 1

    def invalidate_file(self, abs_path: str) -> None:
        """Invalidate cache for a specific file (public API)."""
        self._invalidate_file(abs_path)

    def invalidate_all(self) -> None:
        """Invalidate all cached tags."""
        self._memory_cache.clear()

        try:
            for path in self._cache_dir.iterdir():
                if path.suffix == ".json":
                    with contextlib.suppress(OSError, ValueError, RuntimeError):
                        path.unlink(missing_ok=True)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "TagsCache: invalidate_all failed: %s",
                exc,
            )

        self._stats.invalidations += 1

    def get_stats(self) -> TagsCacheStats:
        """Get cache statistics."""
        return TagsCacheStats(
            hits=self._stats.hits,
            misses=self._stats.misses,
            sets=self._stats.sets,
            invalidations=self._stats.invalidations,
            errors=self._stats.errors,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tags_caches: dict[str, TagsCache] = {}


def get_tags_cache(workspace: str | Path) -> TagsCache:
    """Get or create the tags cache for a workspace.

    Args:
        workspace: Workspace root path.

    Returns:
        TagsCache instance (singleton per workspace).
    """
    workspace_str = str(workspace)
    if workspace_str not in _tags_caches:
        _tags_caches[workspace_str] = TagsCache(workspace_str)
    return _tags_caches[workspace_str]


def clear_tags_cache(workspace: str | Path) -> None:
    """Clear the tags cache for a workspace (useful for testing)."""
    workspace_str = str(workspace)
    if workspace_str in _tags_caches:
        _tags_caches[workspace_str].invalidate_all()
        del _tags_caches[workspace_str]


__all__ = [
    "TagsCache",
    "TagsCacheEntry",
    "TagsCacheStats",
    "clear_tags_cache",
    "get_tags_cache",
]
