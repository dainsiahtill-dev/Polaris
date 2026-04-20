"""KernelOne multi-tier cache manager (facade).

This module re-exports the canonical 5-tier cache implementation from
cache_manager.py (TieredAssetCacheManager) and provides a backward-compatible
facade (KernelOneCacheManager) for legacy callers.

## 架构收敛 (2026-04-04)

- ``TieredAssetCacheManager`` 是 canonical 实现 (cache_manager.py)
- ``KernelOneCacheManager`` 是 facade，继承自 TieredAssetCacheManager
- 保留旧 TTL 语义 (continuity=24h, hot_slice_max=20) 以确保向后兼容
- 缓存路径仍为 ``<metadata_dir>/cache/`` (legacy path)

迁移建议: 新代码直接使用 ``TieredAssetCacheManager``。
"""

from __future__ import annotations

import logging
from pathlib import Path

from polaris.kernelone.context.cache_manager import (
    CacheEntry as TieredCacheEntry,
    CacheStats as TieredCacheStats,
    CacheTier as TieredCacheTier,
    TieredAssetCacheManager,
)
from polaris.kernelone.context.cache_policies import SESSION_CONTINUITY_TTL_SECONDS
from polaris.kernelone.runtime.instance_state import (
    InstanceScopedStateStore,
    normalize_workspace_instance_id,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backward-compatible re-exports (deprecated names from this module's old API)
# ---------------------------------------------------------------------------

CacheEntry = TieredCacheEntry
CacheStats = TieredCacheStats
CacheTier = TieredCacheTier

# Default TTLs (P1-CTX-002 convergence: use canonical values from cache_policies)
_DEFAULT_HOT_SLICE_TTL = 300.0  # 5 minutes
_DEFAULT_CONTINUITY_TTL = SESSION_CONTINUITY_TTL_SECONDS  # 1 hour (canonical)
_MAX_HOT_SLICE_ENTRIES = 20


# ---------------------------------------------------------------------------
# KernelOneCacheManager — facade with legacy TTL / path semantics
# ---------------------------------------------------------------------------


class KernelOneCacheManager(TieredAssetCacheManager):
    """[已废弃] 向后兼容 facade。请直接使用 TieredAssetCacheManager。

    本类继承 TieredAssetCacheManager，但覆盖以下参数以保留旧语义：
    - 缓存路径: ``<metadata_dir>/cache/`` (而非 ``<metadata_dir>/kernelone_cache/``)
    - 热切片上限: 20 条 (而非 50 条)
    - Projection TTL: SESSION_CONTINUITY_TTL_SECONDS / 1h (P1-CTX-002 convergence)

    所有其他行为 (5 层架构、AssetCachePort 协议、统计) 均继承自 TieredAssetCacheManager。
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        hot_slice_max_entries: int = _MAX_HOT_SLICE_ENTRIES,
        continuity_ttl: float = _DEFAULT_CONTINUITY_TTL,
        hot_slice_ttl: float = _DEFAULT_HOT_SLICE_TTL,
    ) -> None:
        # 强制使用旧 TTL 语义: continuity pack = 24h (legacy continuity语义)
        # TieredAssetCacheManager 默认 projection_ttl=120s，新旧语义不同
        super().__init__(
            workspace,
            hot_slice_max_entries=hot_slice_max_entries,
            hot_slice_ttl=hot_slice_ttl,
            # projection_ttl 在 TieredAssetCacheManager 中对应 continuity pack
            # 旧系统 continuity_ttl=86400s (24h)，保持一致
            projection_ttl=continuity_ttl,
        )

    def _resolve_cache_root(self) -> Path:
        """覆盖缓存路径为 legacy 路径: workspace/<metadata_dir>/cache/"""
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        root = Path(self._workspace) / metadata_dir / "cache"
        root.mkdir(parents=True, exist_ok=True)
        return root


# ---------------------------------------------------------------------------
# Module-level singleton cache manager (returns KernelOneCacheManager facade)
# ---------------------------------------------------------------------------


def _dispose_cache_manager(manager: KernelOneCacheManager) -> None:
    """Dispose of a cache manager, clearing all in-memory tiers."""
    manager._hot_slices.clear()


_cache_managers = InstanceScopedStateStore[KernelOneCacheManager](
    normalizer=normalize_workspace_instance_id,
    on_dispose=_dispose_cache_manager,
)


def get_cache_manager(workspace: str | Path) -> KernelOneCacheManager:
    """Get or create the cache manager for a workspace.

    Returns a ``KernelOneCacheManager`` (subclass of TieredAssetCacheManager)
    that preserves legacy TTL semantics and cache path.

    For new code, consider using ``TieredAssetCacheManager`` directly.
    """
    workspace_key = normalize_workspace_instance_id(workspace)
    return _cache_managers.get_or_create(
        workspace_key,
        lambda: KernelOneCacheManager(workspace_key),
    )


def clear_cache_manager(workspace: str | Path) -> None:
    """Clear the cache manager for a workspace (useful for testing)."""
    workspace_key = normalize_workspace_instance_id(workspace)
    _cache_managers.dispose(workspace_key)


__all__ = [
    # Re-exports (backward compatibility)
    "CacheEntry",
    "CacheStats",
    "CacheTier",
    # Legacy facade (subclass of TieredAssetCacheManager)
    "KernelOneCacheManager",
    # Canonical name
    "TieredAssetCacheManager",
    "clear_cache_manager",
    # Module-level singleton accessors
    "get_cache_manager",
]
