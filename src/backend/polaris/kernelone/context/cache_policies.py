"""Cache eviction and invalidation policies for the 5-tier hot-asset cache.

Each policy class encapsulates:
    - Tier-specific TTL
    - Size / entry count limits
    - Invalidation triggers (TTL expiry, mtime change, file change)

These policies are consumed by TieredAssetCacheManager and by
WorkingSetAssembler for cache warming hints.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Per-tier default policy constants (from blueprint §5.7)
# ---------------------------------------------------------------------------

# Hot Slice: max_entries=50, ttl=300s, eviction=LRU
HOT_SLICE_MAX_ENTRIES = 50
HOT_SLICE_TTL_SECONDS = 300.0

# Repo Map: max_entries=5, ttl=600s, invalidation on file-change
REPO_MAP_MAX_ENTRIES = 5
REPO_MAP_TTL_SECONDS = 600.0

# Symbol Index: max_entries=20, ttl=600s
SYMBOL_INDEX_MAX_ENTRIES = 20
SYMBOL_INDEX_TTL_SECONDS = 600.0

# Projection: max_entries=10, ttl=120s
PROJECTION_MAX_ENTRIES = 10
PROJECTION_TTL_SECONDS = 120.0

# Session Continuity: no entry limit, ttl=3600s (1 hour)
SESSION_CONTINUITY_TTL_SECONDS = 3600.0


# ---------------------------------------------------------------------------
# Policy dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HotSliceCachePolicy:
    """Policy for the Hot Slice cache tier (LRU, in-process).

    Defaults from blueprint §5.7:
        - max_entries: 50
        - ttl_seconds: 300 (5 minutes)
        - eviction: LRU (oldest entry evicted under pressure)
    """

    max_entries: int = HOT_SLICE_MAX_ENTRIES
    ttl_seconds: float = HOT_SLICE_TTL_SECONDS

    def cache_key_for_slice(
        self,
        file_path: str | Path,
        start_line: int,
        end_line: int,
    ) -> str:
        """Generate a stable cache key for a code slice.

        Format: "slice|{path}|{start}|{end}"
        Uses | as separator to avoid colon issues on Windows drive letters.
        """
        return f"slice|{file_path}|{start_line}|{end_line}"

    def cache_key_for_repo_map(self, lang: str) -> str:
        """Generate a stable cache key for a repo map hot copy."""
        return f"repo_map:hot:{lang}"

    def cache_key_for_symbol_index(self, file_path: str | Path) -> str:
        """Generate a stable cache key for a symbol index hot copy."""
        return f"symbol_index:hot:{file_path}"

    def should_evict(self, current_count: int) -> bool:
        """Return True when the cache is at capacity and should evict."""
        return current_count >= self.max_entries


@dataclass(frozen=True)
class RepoMapCachePolicy:
    """Policy for the Repo Map cache tier (workspace-persistent).

    Defaults from blueprint §5.7:
        - max_entries: 5  (one per language family)
        - ttl_seconds: 600 (10 minutes)
        - invalidation: when any owned workspace file changes (mtime check)
    """

    max_entries: int = REPO_MAP_MAX_ENTRIES
    ttl_seconds: float = REPO_MAP_TTL_SECONDS

    def cache_key(self, workspace: str | Path, lang: str) -> str:
        """Stable key for a workspace/language repo map entry."""
        return f"repo_map:{lang}"

    def file_changed_since(self, path: str | Path, mtime: float) -> bool:
        """Return True if the file has been modified since the given mtime.

        This is the core of mtime-based cache invalidation for repo maps.

        Usage::

            policy = RepoMapCachePolicy()
            if policy.file_changed_since("/repo/src/main.py", cached_mtime):
                await cache.invalidate("repo_map:python")
        """
        try:
            return os.path.getmtime(str(path)) > mtime
        except OSError:
            return False

    def workspace_files_changed_since(
        self,
        workspace: str | Path,
        mtime: float,
        extensions: tuple[str, ...] | None = None,
    ) -> bool:
        """Return True if any tracked source file in the workspace is newer.

        Args:
            workspace: Root workspace path.
            mtime: Reference timestamp (from the cached entry's created_at).
            extensions: File extensions to scan (default: common source extensions).

        This is an O(n) directory scan. For large workspaces prefer
        file_changed_since() on specific owned files.
        """
        workspace_path = Path(workspace)
        extensions = extensions or (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".java",
            ".go",
            ".rs",
            ".c",
            ".cpp",
            ".h",
            ".cs",
            ".yaml",
            ".yml",
            ".toml",
            ".json",
            ".md",
        )

        try:
            for ext in extensions:
                for file_path in workspace_path.rglob(f"*{ext}"):
                    if self.file_changed_since(file_path, mtime):
                        return True
        except OSError:
            pass
        return False

    def invalidation_key(self, lang: str) -> str:
        """Key used when bulk-invalidating all repo map entries for a language."""
        return f"repo_map:{lang}"


@dataclass(frozen=True)
class SymbolIndexCachePolicy:
    """Policy for the Symbol Index cache tier (workspace-persistent).

    Defaults from blueprint §5.7:
        - max_entries: 20
        - ttl_seconds: 600 (10 minutes)
        - No automatic invalidation (relies on file mtime check at read time)
    """

    max_entries: int = SYMBOL_INDEX_MAX_ENTRIES
    ttl_seconds: float = SYMBOL_INDEX_TTL_SECONDS

    def cache_key(self, file_path: str | Path) -> str:
        """Stable key for a per-file symbol index."""
        # Use a hash so filesystem-safe characters are guaranteed
        path_str = str(file_path).encode("utf-8")
        path_hash = hashlib.sha1(path_str).hexdigest()[:16]
        return f"symbol_index:{path_hash}"

    def source_mtime(self, file_path: str | Path) -> float | None:
        """Return the current mtime of the source file, or None if not accessible."""
        try:
            return os.path.getmtime(str(file_path))
        except OSError:
            return None


@dataclass(frozen=True)
class ProjectionCachePolicy:
    """Policy for the Continuity Projection cache tier (workspace-persistent).

    Defaults from blueprint §5.7:
        - max_entries: 10
        - ttl_seconds: 120 (2 minutes)
    """

    max_entries: int = PROJECTION_MAX_ENTRIES
    ttl_seconds: float = PROJECTION_TTL_SECONDS

    def cache_key(self, session_id: str) -> str:
        """Stable key for a session's continuity projection."""
        return f"continuity:{session_id}"

    def cache_key_for_workspace(self, workspace: str | Path, session_id: str) -> str:
        """Stable key scoped to workspace and session."""
        ws_hash = hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:8]
        return f"projection:{ws_hash}:{session_id}"


# ---------------------------------------------------------------------------
# Cache warming hints for WorkingSetAssembler
# ---------------------------------------------------------------------------


def warming_hint_for_phase(
    phase: str,
) -> list[tuple[str, str]]:
    """Return cache warming hints for a given exploration phase.

    Returns a list of (tier, key_pattern) hints for pre-loading relevant
    cache entries before a phase begins.

    Args:
        phase: One of "map", "search", "slice", "expand", "read_full".

    Example usage in WorkingSetAssembler::

        for tier, pattern in warming_hint_for_phase("slice"):
            await cache.prefetch(tier, pattern)
    """
    hints: dict[str, list[tuple[str, str]]] = {
        "map": [
            ("session_continuity", "recent_workspace_state:*"),
            ("repo_map", "repo_map:*"),
        ],
        "search": [
            ("hot_slice", "slice:*"),
            ("symbol_index", "symbol_index:*"),
        ],
        "slice": [
            ("hot_slice", "slice:*"),
        ],
        "expand": [
            ("hot_slice", "slice:*"),
            ("symbol_index", "symbol_index:*"),
        ],
        "read_full": [
            ("hot_slice", "slice:*"),
        ],
    }
    return hints.get(phase, [])


# ---------------------------------------------------------------------------
# Module-level re-exports for convenience
# ------------------------------------------------------------------------__

__all__ = [
    # TTL constants (authoritative source for cache_manager.py)
    "HOT_SLICE_TTL_SECONDS",
    "PROJECTION_TTL_SECONDS",
    "REPO_MAP_TTL_SECONDS",
    "SESSION_CONTINUITY_TTL_SECONDS",
    "SYMBOL_INDEX_TTL_SECONDS",
    # Policy classes
    "HotSliceCachePolicy",
    "ProjectionCachePolicy",
    "RepoMapCachePolicy",
    "SymbolIndexCachePolicy",
    # Utilities
    "warming_hint_for_phase",
]
