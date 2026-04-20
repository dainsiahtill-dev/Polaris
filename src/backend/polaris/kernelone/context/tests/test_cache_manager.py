"""Tests for KernelOneCacheManager (TieredAssetCacheManager facade).

These tests validate the backward-compatible facade that wraps TieredAssetCacheManager
with legacy TTL semantics and cache path.
"""

from __future__ import annotations

import os
import shutil
import time

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from polaris.kernelone.context.cache import (
    KernelOneCacheManager,
    clear_cache_manager,
    get_cache_manager,
)
from polaris.kernelone.context.cache_manager import (
    CacheEntry,
    CacheStats,
    CacheTier,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Create a temporary workspace directory."""
    base = Path(__file__).resolve().parents[4] / ".tmp_pytest_context_cache_manager"
    base.mkdir(parents=True, exist_ok=True)
    workspace = base / f"ws_{uuid4().hex[:12]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def cache_manager(temp_workspace: Path) -> Generator[KernelOneCacheManager, None, None]:
    """Create a KernelOneCacheManager (TieredAssetCacheManager facade) for testing.

    Uses short TTLs for fast testing:
    - hot_slice_ttl: 5 seconds
    - projection_ttl: 60 seconds (maps to legacy continuity_ttl)
    """
    manager = KernelOneCacheManager(
        workspace=temp_workspace,
        hot_slice_ttl=5.0,  # 5 seconds for fast testing
        continuity_ttl=60.0,  # 60 seconds (legacy naming for projection_ttl)
    )
    yield manager
    # Cleanup in-memory tiers
    manager._hot_slices.clear()
    manager._session_continuity.clear()


# ---------------------------------------------------------------------------
# CacheEntry tests
# ---------------------------------------------------------------------------


class TestCacheEntry:
    def test_not_expired_without_ttl(self) -> None:
        """Entry without TTL should not expire."""
        entry = CacheEntry(
            key="test",
            value="data",
            tier=CacheTier.HOT_SLICE,
            created_at=time.time(),
            last_accessed=time.time(),
        )
        assert not entry.is_expired()

    def test_expired_with_ttl(self) -> None:
        """Entry should expire after TTL."""
        entry = CacheEntry(
            key="test",
            value="data",
            tier=CacheTier.HOT_SLICE,
            created_at=time.time() - 100,
            last_accessed=time.time() - 100,
            ttl_seconds=60.0,
        )
        assert entry.is_expired()

    def test_not_expired_before_ttl(self) -> None:
        """Entry should not expire before TTL."""
        entry = CacheEntry(
            key="test",
            value="data",
            tier=CacheTier.HOT_SLICE,
            created_at=time.time() - 30,
            last_accessed=time.time() - 30,
            ttl_seconds=60.0,
        )
        assert not entry.is_expired()

    def test_touch_updates_access(self) -> None:
        """Touch should update last_accessed and increment count."""
        entry = CacheEntry(
            key="test",
            value="data",
            tier=CacheTier.HOT_SLICE,
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=0,
        )
        time.sleep(0.01)  # Ensure time difference
        entry.touch()
        assert entry.access_count == 1
        assert entry.last_accessed >= entry.created_at


# ---------------------------------------------------------------------------
# KernelOneCacheManager - Hot Slice Cache tests
# ---------------------------------------------------------------------------


class TestHotSliceCache:
    async def _put_and_get(
        self, manager: KernelOneCacheManager, key: str, value: str
    ) -> tuple[CacheStats, CacheStats, str | None]:
        """Helper to put and get, returning (before_stats, after_stats, result)."""
        stats_before = await manager.get_stats()
        await manager.put_hot_slice(key, value)
        result = await manager.get_hot_slice(key)
        stats_after = await manager.get_stats()
        return stats_before, stats_after, result

    def test_hot_slice_miss_on_empty(self, cache_manager: KernelOneCacheManager) -> None:
        """Hot slice should miss on empty cache."""
        result = cache_manager._hot_slices.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_slice_hit_increments_count(self, cache_manager: KernelOneCacheManager) -> None:
        """Second access to same key should be a hit (access count increases)."""
        key = "test_key"
        value = "test_content"

        # First put
        await cache_manager.put_hot_slice(key, value)
        first_result = await cache_manager.get_hot_slice(key)
        first_stats = await cache_manager.get_stats()

        # Second access
        second_result = await cache_manager.get_hot_slice(key)
        second_stats = await cache_manager.get_stats()

        assert first_result == value
        assert second_result == value
        assert second_stats.hits_hot_slice == first_stats.hits_hot_slice + 1

    @pytest.mark.asyncio
    async def test_hot_slice_eviction_on_max_size(self, cache_manager: KernelOneCacheManager) -> None:
        """When exceeding max entries, oldest entry should be evicted."""
        # Fill up to max (20 entries)
        for i in range(25):
            await cache_manager.put_hot_slice(f"key_{i}", f"value_{i}")

        # First entries should be evicted
        assert await cache_manager.get_hot_slice("key_0") is None
        assert await cache_manager.get_hot_slice("key_1") is None
        # Recent entries should remain
        assert await cache_manager.get_hot_slice("key_24") == "value_24"

    @pytest.mark.asyncio
    async def test_hot_slice_ttl_expiry(self, cache_manager: KernelOneCacheManager) -> None:
        """Hot slice should expire after TTL."""
        key = "ttl_test"
        value = "ttl_value"
        await cache_manager.put_hot_slice(key, value)

        # Should be available immediately
        assert await cache_manager.get_hot_slice(key) == value

        # Wait for TTL to expire (5 seconds)
        time.sleep(5.5)

        # Should now be expired
        assert await cache_manager.get_hot_slice(key) is None


# ---------------------------------------------------------------------------
# Repo Map Cache tests
# ---------------------------------------------------------------------------


class TestRepoMapCache:
    @pytest.mark.asyncio
    async def test_repo_map_cache_miss(self, cache_manager: KernelOneCacheManager, temp_workspace: Path) -> None:
        """Repo map should miss when not cached."""
        result = await cache_manager.get_repo_map(temp_workspace, "python")
        assert result is None
        stats = await cache_manager.get_stats()
        assert stats.misses_repo_map == 1

    @pytest.mark.asyncio
    async def test_repo_map_cache_hit(self, cache_manager: KernelOneCacheManager, temp_workspace: Path) -> None:
        """Repo map should hit when cached."""
        snapshot = {"files": ["a.py", "b.py"], "text": "repo map content"}

        await cache_manager.put_repo_map(temp_workspace, "python", snapshot)
        result = await cache_manager.get_repo_map(temp_workspace, "python")

        assert result == snapshot
        stats = await cache_manager.get_stats()
        assert stats.hits_repo_map == 1
        assert stats.misses_repo_map == 0

    @pytest.mark.asyncio
    async def test_repo_map_different_languages(
        self, cache_manager: KernelOneCacheManager, temp_workspace: Path
    ) -> None:
        """Different languages should have separate cache entries."""
        await cache_manager.put_repo_map(temp_workspace, "python", {"lang": "python"})
        await cache_manager.put_repo_map(temp_workspace, "typescript", {"lang": "typescript"})

        python_result = await cache_manager.get_repo_map(temp_workspace, "python")
        ts_result = await cache_manager.get_repo_map(temp_workspace, "typescript")

        assert python_result == {"lang": "python"}
        assert ts_result == {"lang": "typescript"}


# ---------------------------------------------------------------------------
# Symbol Index Cache tests
# ---------------------------------------------------------------------------


class TestSymbolIndexCache:
    @pytest.mark.asyncio
    async def test_symbol_index_cache_miss(self, cache_manager: KernelOneCacheManager, temp_workspace: Path) -> None:
        """Symbol index should miss when not cached."""
        file_path = temp_workspace / "test.py"
        result = await cache_manager.get_symbol_index(file_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_symbol_index_cache_hit(self, cache_manager: KernelOneCacheManager, temp_workspace: Path) -> None:
        """Symbol index should hit when cached."""
        file_path = temp_workspace / "test.py"
        index = {"symbols": [{"name": "foo", "type": "function"}]}

        await cache_manager.put_symbol_index(file_path, index)
        result = await cache_manager.get_symbol_index(file_path)

        assert result == index
        stats = await cache_manager.get_stats()
        assert stats.hits_symbol_index == 1


# ---------------------------------------------------------------------------
# Continuity Cache tests
# ---------------------------------------------------------------------------


class TestContinuityCache:
    @pytest.mark.asyncio
    async def test_continuity_pack_cache_miss(self, cache_manager: KernelOneCacheManager) -> None:
        """Continuity pack should miss when not cached."""
        result = await cache_manager.get_continuity_pack("session_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_continuity_pack_cache_hit(self, cache_manager: KernelOneCacheManager) -> None:
        """Continuity pack should hit when cached."""
        pack = {"summary": "test summary", "open_loops": ["loop1"]}

        await cache_manager.put_continuity_pack("session_123", pack)
        result = await cache_manager.get_continuity_pack("session_123")

        assert result == pack
        stats = await cache_manager.get_stats()
        assert stats.hits_projection == 1


# ---------------------------------------------------------------------------
# Session Continuity Cache tests (replaces old put_session/get_session API)
# ---------------------------------------------------------------------------


class TestSessionContinuityCache:
    """Tests for SESSION_CONTINUITY tier via the public get/set API."""

    @pytest.mark.asyncio
    async def test_session_continuity_put_and_get(self, cache_manager: KernelOneCacheManager) -> None:
        """Session continuity should store and retrieve values via SESSION_CONTINUITY tier."""
        key = "session_key1"
        value = {"data": "value"}
        await cache_manager.set(f"session:{key}", CacheTier.SESSION_CONTINUITY, value, ttl=60.0)
        result = await cache_manager.get(f"session:{key}", CacheTier.SESSION_CONTINUITY)
        assert result == value

    @pytest.mark.asyncio
    async def test_session_continuity_expires(self, cache_manager: KernelOneCacheManager) -> None:
        """Session continuity should expire after TTL (uses SESSION_CONTINUITY tier TTL)."""
        key = "session_key2"
        value = "value"
        await cache_manager.set(f"session:{key}", CacheTier.SESSION_CONTINUITY, value, ttl=1.0)
        result = await cache_manager.get(f"session:{key}", CacheTier.SESSION_CONTINUITY)
        assert result == value
        time.sleep(1.5)
        result = await cache_manager.get(f"session:{key}", CacheTier.SESSION_CONTINUITY)
        assert result is None


# ---------------------------------------------------------------------------
# Cache Stats tests
# ---------------------------------------------------------------------------


class TestCacheStats:
    def test_cache_stats_to_dict(self) -> None:
        """CacheStats should serialize to dict with all tier hit/miss fields."""
        stats = CacheStats(
            hits_session_continuity=3,
            misses_session_continuity=1,
            hits_repo_map=5,
            misses_repo_map=2,
            hits_symbol_index=10,
            misses_symbol_index=1,
            hits_hot_slice=20,
            misses_hot_slice=3,
            hits_projection=7,
            misses_projection=1,
            evictions=4,
        )
        d = stats.to_dict()
        assert d["hits_repo_map"] == 5
        assert d["hits_hot_slice"] == 20
        assert d["evictions"] == 4
        assert d["total_hits"] == 45
        assert d["total_misses"] == 8


# ---------------------------------------------------------------------------
# Cache Management tests
# ---------------------------------------------------------------------------


class TestCacheManagement:
    @pytest.mark.asyncio
    async def test_clear_tier_hot_slice(self, cache_manager: KernelOneCacheManager) -> None:
        """Clearing HOT_SLICE tier should clear in-memory hot slice cache."""
        await cache_manager.put_hot_slice("key1", "value1")
        await cache_manager.put_hot_slice("key2", "value2")

        assert await cache_manager.get_hot_slice("key1") == "value1"
        assert await cache_manager.get_hot_slice("key2") == "value2"

        await cache_manager.clear_tier(CacheTier.HOT_SLICE)

        assert await cache_manager.get_hot_slice("key1") is None
        assert await cache_manager.get_hot_slice("key2") is None

    @pytest.mark.asyncio
    async def test_clear_tier_session_continuity(self, cache_manager: KernelOneCacheManager) -> None:
        """Clearing SESSION_CONTINUITY tier should clear in-memory session cache."""
        key = "session_clear_test"
        await cache_manager.set(f"session:{key}", CacheTier.SESSION_CONTINUITY, {"data": "test"}, ttl=60.0)
        result = await cache_manager.get(f"session:{key}", CacheTier.SESSION_CONTINUITY)
        assert result == {"data": "test"}

        await cache_manager.clear_tier(CacheTier.SESSION_CONTINUITY)

        result = await cache_manager.get(f"session:{key}", CacheTier.SESSION_CONTINUITY)
        assert result is None

    @pytest.mark.asyncio
    async def test_clear_tier_workspace(self, cache_manager: KernelOneCacheManager, temp_workspace: Path) -> None:
        """Clearing REPO_MAP tier should clear persistent repo map cache."""
        await cache_manager.put_repo_map(temp_workspace, "python", {"data": "test"})

        # Verify it is cached
        assert await cache_manager.get_repo_map(temp_workspace, "python") == {"data": "test"}

        # Clear REPO_MAP tier
        await cache_manager.clear_tier(CacheTier.REPO_MAP)

        # Should be cleared
        assert await cache_manager.get_repo_map(temp_workspace, "python") is None

    @pytest.mark.asyncio
    async def test_stats_reflects_all_operations(
        self, cache_manager: KernelOneCacheManager, temp_workspace: Path
    ) -> None:
        """Stats should correctly track all cache operations."""
        # Generate some hits and misses
        await cache_manager.get_repo_map(temp_workspace, "python")  # miss
        await cache_manager.put_repo_map(temp_workspace, "python", {"data": "test"})
        await cache_manager.get_repo_map(temp_workspace, "python")  # hit
        await cache_manager.get_repo_map(temp_workspace, "python")  # hit
        await cache_manager.get_hot_slice("key1")  # miss
        await cache_manager.put_hot_slice("key1", "value1")
        await cache_manager.get_hot_slice("key1")  # hit
        await cache_manager.get_hot_slice("key1")  # hit

        stats = await cache_manager.get_stats()
        assert stats.misses_repo_map == 1
        assert stats.hits_repo_map == 2
        assert stats.misses_hot_slice == 1
        assert stats.hits_hot_slice == 2


# ---------------------------------------------------------------------------
# Module-level singleton tests
# ---------------------------------------------------------------------------


class TestSingletonManager:
    def test_get_cache_manager_returns_same_instance(self, temp_workspace: Path) -> None:
        """get_cache_manager should return same instance for same workspace."""
        mgr1 = get_cache_manager(temp_workspace)
        mgr2 = get_cache_manager(temp_workspace)
        assert mgr1 is mgr2

    def test_different_workspaces_different_managers(self, temp_workspace: Path) -> None:
        """Different workspaces should have different managers."""
        base = Path(__file__).resolve().parents[4] / ".tmp_pytest_context_cache_manager"
        base.mkdir(parents=True, exist_ok=True)
        workspace2 = base / f"ws_{uuid4().hex[:12]}"
        workspace2.mkdir(parents=True, exist_ok=False)
        try:
            mgr1 = get_cache_manager(temp_workspace)
            mgr2 = get_cache_manager(workspace2)
            assert mgr1 is not mgr2
        finally:
            shutil.rmtree(workspace2, ignore_errors=True)

    def test_clear_cache_manager(self, temp_workspace: Path) -> None:
        """clear_cache_manager should remove the manager."""
        mgr1 = get_cache_manager(temp_workspace)
        clear_cache_manager(temp_workspace)
        mgr2 = get_cache_manager(temp_workspace)
        assert mgr1 is not mgr2

    def test_relative_and_absolute_workspace_share_same_manager(self, tmp_path: Path) -> None:
        """Instance-scoped manager normalizes workspace identity by absolute path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        relative = workspace.relative_to(tmp_path)
        cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            mgr_relative = get_cache_manager(relative)
            mgr_absolute = get_cache_manager(workspace)
            assert mgr_relative is mgr_absolute
        finally:
            os.chdir(cwd)
            clear_cache_manager(workspace)
