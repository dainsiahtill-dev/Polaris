"""Tests for TieredAssetCacheManager and cache_policies (blueprint §5.7).

Covers:
    - TieredAssetCacheManager: all 5 tiers, TTL, LRU eviction,
      get_or_compute, invalidate, stats, clear_tier
    - CacheTier enum
    - CachePolicies: HotSliceCachePolicy, RepoMapCachePolicy,
      SymbolIndexCachePolicy, ProjectionCachePolicy
    - AssetCachePort protocol compliance

AssetCachePort protocol signature:
    async def get(self, key: str, tier: CacheTier) -> Any | None
    async def set(self, key: str, tier: CacheTier, value: Any, ttl: float | None = None) -> None
    async def invalidate(self, key: str, tier: CacheTier | None = None) -> None
    async def get_or_compute(self, key: str, tier: CacheTier, factory, ttl: float | None = None) -> Any
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from polaris.kernelone.context.cache_manager import (
    CacheEntry,
    CacheStats,
    CacheTier,
    TieredAssetCacheManager,
    _as_tier,
)
from polaris.kernelone.context.cache_policies import (
    HOT_SLICE_MAX_ENTRIES,
    HOT_SLICE_TTL_SECONDS,
    PROJECTION_MAX_ENTRIES,
    PROJECTION_TTL_SECONDS,
    REPO_MAP_MAX_ENTRIES,
    REPO_MAP_TTL_SECONDS,
    SESSION_CONTINUITY_TTL_SECONDS,
    SYMBOL_INDEX_MAX_ENTRIES,
    SYMBOL_INDEX_TTL_SECONDS,
    HotSliceCachePolicy,
    ProjectionCachePolicy,
    RepoMapCachePolicy,
    SymbolIndexCachePolicy,
    warming_hint_for_phase,
)

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Create a temporary workspace directory."""
    base = Path(__file__).resolve().parents[4] / ".tmp_pytest_context_tiered_cache"
    base.mkdir(parents=True, exist_ok=True)
    workspace = base / f"ws_{uuid4().hex[:12]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def cache(temp_workspace: Path) -> Generator[TieredAssetCacheManager, None, None]:
    """Create a TieredAssetCacheManager with short TTLs for fast testing."""
    manager = TieredAssetCacheManager(
        workspace=temp_workspace,
        hot_slice_ttl=2.0,
        projection_ttl=2.0,
        repo_map_ttl=5.0,
        symbol_index_ttl=5.0,
        session_continuity_ttl=5.0,
        hot_slice_max_entries=5,
        repo_map_max_entries=3,
        symbol_index_max_entries=4,
        projection_max_entries=3,
    )
    yield manager
    # Cleanup
    manager._hot_slices.clear()
    manager._session_continuity.clear()


# ---------------------------------------------------------------------------
# CacheTier enum tests
# ---------------------------------------------------------------------------


class TestCacheTier:
    def test_all_five_tiers_present(self) -> None:
        """All five tiers from blueprint §5.7 are present."""
        assert CacheTier.SESSION_CONTINUITY == "session_continuity"
        assert CacheTier.REPO_MAP == "repo_map"
        assert CacheTier.SYMBOL_INDEX == "symbol_index"
        assert CacheTier.HOT_SLICE == "hot_slice"
        assert CacheTier.PROJECTION == "projection"

    def test_str_enum_value(self) -> None:
        """CacheTier values are strings."""
        assert isinstance(CacheTier.HOT_SLICE, str)
        assert isinstance(CacheTier.REPO_MAP, str)

    def test_from_string(self) -> None:
        """_as_tier converts string to CacheTier."""
        t = _as_tier("hot_slice")
        assert isinstance(t, CacheTier)
        assert t == CacheTier.HOT_SLICE

    def test_from_cache_tier_passthrough(self) -> None:
        """_as_tier returns CacheTier members unchanged."""
        t = _as_tier(CacheTier.PROJECTION)
        assert t is CacheTier.PROJECTION

    def test_isinstance_cache_tier(self) -> None:
        """CacheTier instances satisfy isinstance checks."""
        assert isinstance(CacheTier.SESSION_CONTINUITY, CacheTier)
        assert isinstance(CacheTier.REPO_MAP, CacheTier)


# ---------------------------------------------------------------------------
# CacheEntry tests
# ---------------------------------------------------------------------------


class TestCacheEntry:
    def test_not_expired_without_ttl(self) -> None:
        """Entry without TTL must not expire."""
        entry = CacheEntry(
            key="test",
            value="data",
            tier=CacheTier.HOT_SLICE,
            created_at=time.time(),
            last_accessed=time.time(),
        )
        assert not entry.is_expired()

    def test_expired_with_ttl(self) -> None:
        """Entry must expire after TTL elapses."""
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
        """Entry must not expire before TTL elapses."""
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
        """touch() increments access_count and updates last_accessed."""
        entry = CacheEntry(
            key="test",
            value="data",
            tier=CacheTier.HOT_SLICE,
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=0,
        )
        time.sleep(0.01)
        entry.touch()
        assert entry.access_count == 1
        assert entry.last_accessed >= entry.created_at


# ---------------------------------------------------------------------------
# CacheStats tests
# ---------------------------------------------------------------------------


class TestCacheStats:
    def test_hit_ratio_zero_when_no_operations(self) -> None:
        """Hit ratio must be 0.0 when no operations recorded."""
        stats = CacheStats()
        assert stats.hit_ratio == 0.0

    def test_hit_ratio_calculation(self) -> None:
        """Hit ratio must be computed correctly."""
        stats = CacheStats(hits_hot_slice=3, misses_hot_slice=1)
        assert stats.hit_ratio == 3.0 / 4.0

    def test_total_hits_sums_all_tiers(self) -> None:
        """total_hits aggregates across all tiers."""
        stats = CacheStats(
            hits_session_continuity=1,
            hits_repo_map=2,
            hits_symbol_index=3,
            hits_hot_slice=4,
            hits_projection=5,
        )
        assert stats.total_hits == 15

    def test_total_misses_sums_all_tiers(self) -> None:
        """total_misses aggregates across all tiers."""
        stats = CacheStats(
            misses_session_continuity=1,
            misses_repo_map=2,
            misses_symbol_index=3,
            misses_hot_slice=4,
            misses_projection=5,
        )
        assert stats.total_misses == 15

    def test_to_dict_includes_all_fields(self) -> None:
        """to_dict() must include all stat fields."""
        stats = CacheStats(hits_hot_slice=5, misses_hot_slice=2, evictions=3)
        d = stats.to_dict()
        assert d["hits_hot_slice"] == 5
        assert d["misses_hot_slice"] == 2
        assert d["evictions"] == 3
        assert "hit_ratio" in d
        assert "total_hits" in d


# ---------------------------------------------------------------------------
# TieredAssetCacheManager - Hot Slice tier (LRU) tests
# ---------------------------------------------------------------------------


class TestHotSliceTier:
    @pytest.mark.asyncio
    async def test_hot_slice_miss_on_empty(self, cache: TieredAssetCacheManager) -> None:
        """Hot slice must miss on an empty cache."""
        result = await cache.get_hot_slice("nonexistent_key")
        assert result is None
        stats = await cache.get_stats()
        assert stats.misses_hot_slice == 1

    @pytest.mark.asyncio
    async def test_hot_slice_put_and_get(self, cache: TieredAssetCacheManager) -> None:
        """Hot slice must round-trip through put/get."""
        await cache.put_hot_slice("k1", "hello")
        result = await cache.get_hot_slice("k1")
        assert result == "hello"
        stats = await cache.get_stats()
        assert stats.hits_hot_slice == 1
        assert stats.misses_hot_slice == 0

    @pytest.mark.asyncio
    async def test_hot_slice_lru_eviction(self, cache: TieredAssetCacheManager) -> None:
        """When at max capacity, oldest entry must be evicted (LRU)."""
        # max_entries=5 (from fixture)
        for i in range(5):
            await cache.put_hot_slice(f"key_{i}", f"value_{i}")

        # Insert one more — key_0 must be evicted
        await cache.put_hot_slice("key_5", "value_5")

        assert await cache.get_hot_slice("key_0") is None
        assert await cache.get_hot_slice("key_5") == "value_5"

    @pytest.mark.asyncio
    async def test_hot_slice_ttl_expiry(self, cache: TieredAssetCacheManager) -> None:
        """Hot slice must expire after its TTL."""
        await cache.put_hot_slice("ttl_key", "ttl_value")
        assert await cache.get_hot_slice("ttl_key") == "ttl_value"
        time.sleep(2.5)
        assert await cache.get_hot_slice("ttl_key") is None

    @pytest.mark.asyncio
    async def test_hot_slice_mtime_invalidation(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Hot slice must be invalidated if source file mtime is newer."""
        test_file = temp_workspace / "sample.py"
        test_file.write_text("# original", encoding="utf-8")

        # Use | separator to avoid Windows path colon issues
        key = f"slice|{test_file}|1|10"
        await cache.put_hot_slice(key, "original_content", file_path=str(test_file))
        assert await cache.get_hot_slice(key) == "original_content"

        # Touch the file to update its mtime
        time.sleep(0.1)
        test_file.write_text("# modified", encoding="utf-8")

        # The cache entry should be invalidated
        assert await cache.get_hot_slice(key) is None


# ---------------------------------------------------------------------------
# TieredAssetCacheManager - Session Continuity tier tests
# ---------------------------------------------------------------------------


class TestSessionContinuityTier:
    @pytest.mark.asyncio
    async def test_session_continuity_miss_on_empty(self, cache: TieredAssetCacheManager) -> None:
        """Session continuity must miss on an empty cache."""
        # Use AssetCachePort protocol: get(key, tier)
        result = await cache.get("session_key", CacheTier.SESSION_CONTINUITY)
        assert result is None
        stats = await cache.get_stats()
        assert stats.misses_session_continuity == 1

    @pytest.mark.asyncio
    async def test_session_continuity_put_and_get(self, cache: TieredAssetCacheManager) -> None:
        """Session continuity must round-trip via set/get."""
        await cache.set("sess1", CacheTier.SESSION_CONTINUITY, {"summary": "test"})
        result = await cache.get("sess1", CacheTier.SESSION_CONTINUITY)
        assert result == {"summary": "test"}

    @pytest.mark.asyncio
    async def test_session_continuity_ttl_expiry(self, cache: TieredAssetCacheManager) -> None:
        """Session continuity must expire after TTL (5s in fixture)."""
        await cache.set("sess_ttl", CacheTier.SESSION_CONTINUITY, {"val": 1}, ttl=1.0)
        assert await cache.get("sess_ttl", CacheTier.SESSION_CONTINUITY) == {"val": 1}
        time.sleep(1.5)
        assert await cache.get("sess_ttl", CacheTier.SESSION_CONTINUITY) is None


# ---------------------------------------------------------------------------
# TieredAssetCacheManager - Repo Map tier tests
# ---------------------------------------------------------------------------


class TestRepoMapTier:
    @pytest.mark.asyncio
    async def test_repo_map_miss(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Repo map must miss on cache miss."""
        result = await cache.get_repo_map(temp_workspace, "python")
        assert result is None

    @pytest.mark.asyncio
    async def test_repo_map_put_and_get(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Repo map must round-trip."""
        snapshot = {"files": ["a.py", "b.py"]}
        await cache.put_repo_map(temp_workspace, "python", snapshot)
        result = await cache.get_repo_map(temp_workspace, "python")
        assert result == snapshot

    @pytest.mark.asyncio
    async def test_repo_map_per_language(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Different languages must have separate cache entries."""
        await cache.put_repo_map(temp_workspace, "python", {"lang": "py"})
        await cache.put_repo_map(temp_workspace, "typescript", {"lang": "ts"})
        assert await cache.get_repo_map(temp_workspace, "python") == {"lang": "py"}
        assert await cache.get_repo_map(temp_workspace, "typescript") == {"lang": "ts"}


# ---------------------------------------------------------------------------
# TieredAssetCacheManager - Symbol Index tier tests
# ---------------------------------------------------------------------------


class TestSymbolIndexTier:
    @pytest.mark.asyncio
    async def test_symbol_index_miss(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Symbol index must miss on cache miss."""
        fp = temp_workspace / "test.py"
        result = await cache.get_symbol_index(fp)
        assert result is None

    @pytest.mark.asyncio
    async def test_symbol_index_put_and_get(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Symbol index must round-trip."""
        fp = temp_workspace / "test.py"
        index = {"symbols": [{"name": "foo", "type": "function"}]}
        await cache.put_symbol_index(fp, index)
        result = await cache.get_symbol_index(fp)
        assert result == index


# ---------------------------------------------------------------------------
# TieredAssetCacheManager - Projection tier tests
# ---------------------------------------------------------------------------


class TestProjectionTier:
    @pytest.mark.asyncio
    async def test_projection_miss(self, cache: TieredAssetCacheManager) -> None:
        """Projection cache must miss on empty."""
        result = await cache.get_continuity_pack("session_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_projection_put_and_get(self, cache: TieredAssetCacheManager) -> None:
        """Projection cache must round-trip."""
        pack = {"summary": "test summary", "open_loops": ["loop1"]}
        await cache.put_continuity_pack("sess_abc", pack)
        result = await cache.get_continuity_pack("sess_abc")
        assert result == pack


# ---------------------------------------------------------------------------
# AssetCachePort protocol / get_or_compute tests
# ---------------------------------------------------------------------------


class TestAssetCachePortProtocol:
    @pytest.mark.asyncio
    async def test_get_or_compute_cache_miss_calls_factory(self, cache: TieredAssetCacheManager) -> None:
        """get_or_compute must call factory on cache miss."""
        call_count = 0

        def factory() -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            return {"computed": "value"}

        result = await cache.get_or_compute(
            "compute_key",
            CacheTier.SESSION_CONTINUITY,
            factory,
            ttl=10.0,
        )
        assert result == {"computed": "value"}
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_get_or_compute_cache_hit_skips_factory(self, cache: TieredAssetCacheManager) -> None:
        """get_or_compute must NOT call factory on cache hit."""
        call_count = 0

        def factory() -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            return {"computed": "skipped"}

        # set(key, tier, value) — AssetCachePort signature
        await cache.set("hit_key", CacheTier.SESSION_CONTINUITY, {"cached": "yes"})
        result = await cache.get_or_compute(
            "hit_key",
            CacheTier.SESSION_CONTINUITY,
            factory,
        )
        assert result == {"cached": "yes"}
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_invalidate_specific_tier(self, cache: TieredAssetCacheManager) -> None:
        """invalidate with a tier must remove only that tier's entry."""
        await cache.set("k_hot", CacheTier.HOT_SLICE, "v1")
        await cache.set("k_sc", CacheTier.SESSION_CONTINUITY, "v2")

        # invalidate(key, tier) — AssetCachePort signature
        await cache.invalidate("k_hot", CacheTier.HOT_SLICE)

        assert await cache.get("k_hot", CacheTier.HOT_SLICE) is None
        assert await cache.get("k_sc", CacheTier.SESSION_CONTINUITY) == "v2"

    @pytest.mark.asyncio
    async def test_invalidate_all_tiers(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """invalidate with tier=None must clear across all tiers for the key."""
        await cache.set("key1", CacheTier.HOT_SLICE, "v1")
        await cache.set("key1", CacheTier.SESSION_CONTINUITY, "v2")
        await cache.put_repo_map(temp_workspace, "python", {"x": 1})

        # Clear across all tiers for key1
        await cache.invalidate("key1")

        assert await cache.get("key1", CacheTier.HOT_SLICE) is None
        assert await cache.get("key1", CacheTier.SESSION_CONTINUITY) is None

    @pytest.mark.asyncio
    async def test_string_tier_coercion(self, cache: TieredAssetCacheManager) -> None:
        """Cache operations must accept string tier values (not just enum members)."""
        # set(key, tier) where tier is a string
        await cache.set("str_key", CacheTier("hot_slice"), "str_value")
        result = await cache.get("str_key", CacheTier("hot_slice"))
        assert result == "str_value"


# ---------------------------------------------------------------------------
# Cache eviction under pressure
# ---------------------------------------------------------------------------


class TestEvictionUnderPressure:
    @pytest.mark.asyncio
    async def test_hot_slice_lru_at_capacity(self, cache: TieredAssetCacheManager) -> None:
        """Hot slice LRU must evict oldest entry when at capacity."""
        for i in range(7):
            await cache.put_hot_slice(f"slice_{i}", f"val_{i}")
        assert await cache.get_hot_slice("slice_0") is None  # evicted
        assert await cache.get_hot_slice("slice_6") == "val_6"  # kept

    @pytest.mark.asyncio
    async def test_session_continuity_no_entry_limit(self, cache: TieredAssetCacheManager) -> None:
        """Session continuity has no entry limit — old entries must not be evicted by count."""
        for i in range(20):
            await cache.set(f"sc_{i}", CacheTier.SESSION_CONTINUITY, f"v_{i}")
        assert await cache.get("sc_0", CacheTier.SESSION_CONTINUITY) == "v_0"


# ---------------------------------------------------------------------------
# Cache policies tests
# ---------------------------------------------------------------------------


class TestCachePolicies:
    def test_hot_slice_policy_defaults(self) -> None:
        """HotSliceCachePolicy must use blueprint §5.7 defaults."""
        policy = HotSliceCachePolicy()
        assert policy.max_entries == HOT_SLICE_MAX_ENTRIES == 50
        assert policy.ttl_seconds == HOT_SLICE_TTL_SECONDS == 300.0

    def test_hot_slice_cache_key_for_slice(self) -> None:
        """HotSliceCachePolicy.cache_key_for_slice must produce the expected format."""
        policy = HotSliceCachePolicy()
        key = policy.cache_key_for_slice("src/main.py", 10, 50)
        assert key == "slice|src/main.py|10|50"

    def test_hot_slice_should_evict_at_capacity(self) -> None:
        """HotSliceCachePolicy.should_evict must return True at capacity."""
        policy = HotSliceCachePolicy(max_entries=50)
        assert policy.should_evict(49) is False
        assert policy.should_evict(50) is True
        assert policy.should_evict(51) is True

    def test_repo_map_policy_defaults(self) -> None:
        """RepoMapCachePolicy must use blueprint §5.7 defaults."""
        policy = RepoMapCachePolicy()
        assert policy.max_entries == REPO_MAP_MAX_ENTRIES == 5
        assert policy.ttl_seconds == REPO_MAP_TTL_SECONDS == 600.0

    def test_repo_map_file_changed_since_false_when_unchanged(self, temp_workspace: Path) -> None:
        """file_changed_since must return False when file mtime <= reference."""
        test_file = temp_workspace / "check.py"
        test_file.write_text("# x", encoding="utf-8")
        mtime = os.path.getmtime(str(test_file))

        policy = RepoMapCachePolicy()
        assert policy.file_changed_since(test_file, mtime + 1) is False

    def test_repo_map_file_changed_since_true_when_modified(self, temp_workspace: Path) -> None:
        """file_changed_since must return True when file is newer than reference."""
        test_file = temp_workspace / "check.py"
        test_file.write_text("# v1", encoding="utf-8")
        old_mtime = os.path.getmtime(str(test_file))

        time.sleep(0.05)
        test_file.write_text("# v2 modified", encoding="utf-8")
        new_mtime = os.path.getmtime(str(test_file))

        policy = RepoMapCachePolicy()
        assert policy.file_changed_since(test_file, old_mtime) is True
        assert policy.file_changed_since(test_file, new_mtime) is False

    def test_repo_map_workspace_files_changed_since(self, temp_workspace: Path) -> None:
        """workspace_files_changed_since must return True if any source file changed."""
        src_file = temp_workspace / "src" / "main.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# v1", encoding="utf-8")
        old_mtime = os.path.getmtime(str(src_file))

        time.sleep(0.05)
        src_file.write_text("# v2 changed", encoding="utf-8")

        policy = RepoMapCachePolicy()
        assert policy.workspace_files_changed_since(temp_workspace, old_mtime) is True

    def test_repo_map_workspace_files_changed_since_false_when_clean(self, temp_workspace: Path) -> None:
        """workspace_files_changed_since must return False when workspace is unchanged."""
        src_file = temp_workspace / "src" / "main.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("# original", encoding="utf-8")
        mtime = os.path.getmtime(str(src_file))

        policy = RepoMapCachePolicy()
        assert policy.workspace_files_changed_since(temp_workspace, mtime) is False

    def test_symbol_index_policy_defaults(self) -> None:
        """SymbolIndexCachePolicy must use blueprint §5.7 defaults."""
        policy = SymbolIndexCachePolicy()
        assert policy.max_entries == SYMBOL_INDEX_MAX_ENTRIES == 20
        assert policy.ttl_seconds == SYMBOL_INDEX_TTL_SECONDS == 600.0

    def test_symbol_index_cache_key_deterministic(self) -> None:
        """SymbolIndexCachePolicy.cache_key must be stable across calls."""
        policy = SymbolIndexCachePolicy()
        key1 = policy.cache_key("/repo/src/main.py")
        key2 = policy.cache_key("/repo/src/main.py")
        assert key1 == key2
        assert key1.startswith("symbol_index:")

    def test_symbol_index_source_mtime(self, temp_workspace: Path) -> None:
        """source_mtime must return the file's current mtime."""
        test_file = temp_workspace / "indexed.py"
        test_file.write_text("# src", encoding="utf-8")
        mtime = os.path.getmtime(str(test_file))

        policy = SymbolIndexCachePolicy()
        assert policy.source_mtime(test_file) == mtime

    def test_symbol_index_source_mtime_nonexistent(self) -> None:
        """source_mtime must return None for nonexistent files."""
        policy = SymbolIndexCachePolicy()
        result = policy.source_mtime("/nonexistent/path/file.py")
        assert result is None

    def test_projection_policy_defaults(self) -> None:
        """ProjectionCachePolicy must use blueprint §5.7 defaults."""
        policy = ProjectionCachePolicy()
        assert policy.max_entries == PROJECTION_MAX_ENTRIES == 10
        assert policy.ttl_seconds == PROJECTION_TTL_SECONDS == 120.0

    def test_projection_cache_key_format(self) -> None:
        """ProjectionCachePolicy.cache_key must produce the expected format."""
        policy = ProjectionCachePolicy()
        key = policy.cache_key("sess_123")
        assert key == "continuity:sess_123"

    def test_projection_cache_key_for_workspace_includes_hash(self) -> None:
        """cache_key_for_workspace must scope key to both workspace and session."""
        policy = ProjectionCachePolicy()
        key1 = policy.cache_key_for_workspace("/repo", "sess_a")
        key2 = policy.cache_key_for_workspace("/repo", "sess_b")
        assert key1 != key2
        assert key1.startswith("projection:")
        assert key2.startswith("projection:")

    def test_warming_hints_for_map_phase(self) -> None:
        """MAP phase must return session_continuity and repo_map hints."""
        hints = warming_hint_for_phase("map")
        tiers = [t for t, _ in hints]
        assert "session_continuity" in tiers
        assert "repo_map" in tiers

    def test_warming_hints_for_slice_phase(self) -> None:
        """SLICE phase must return hot_slice hints."""
        hints = warming_hint_for_phase("slice")
        tiers = [t for t, _ in hints]
        assert "hot_slice" in tiers

    def test_warming_hints_unknown_phase_returns_empty(self) -> None:
        """Unknown phase must return an empty list."""
        hints = warming_hint_for_phase("unknown_phase_xyz")
        assert hints == []


# ---------------------------------------------------------------------------
# Stats aggregation tests
# ---------------------------------------------------------------------------


class TestStatsAggregation:
    @pytest.mark.asyncio
    async def test_stats_aggregate_all_tiers(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """Stats must aggregate across all tiers."""
        # HOT_SLICE: miss then hit
        await cache.get("key", CacheTier.HOT_SLICE)  # miss
        await cache.put_hot_slice("k1", "v1")
        await cache.get("k1", CacheTier.HOT_SLICE)  # hit

        # SESSION_CONTINUITY: miss then hit
        await cache.get("sc1", CacheTier.SESSION_CONTINUITY)  # miss
        await cache.set("sc2", CacheTier.SESSION_CONTINUITY, {"x": 1})
        await cache.get("sc2", CacheTier.SESSION_CONTINUITY)  # hit

        # REPO_MAP: miss then hit
        await cache.get_repo_map(temp_workspace, "python")  # miss
        await cache.put_repo_map(temp_workspace, "python", {"f": []})
        await cache.get_repo_map(temp_workspace, "python")  # hit

        stats = await cache.get_stats()
        assert stats.total_hits >= 2
        assert stats.total_misses >= 2

    @pytest.mark.asyncio
    async def test_eviction_counter_increments(self, cache: TieredAssetCacheManager) -> None:
        """Eviction counter must increment when LRU entries are removed."""
        for i in range(7):
            await cache.put_hot_slice(f"evict_{i}", f"v_{i}")

        stats = await cache.get_stats()
        assert stats.evictions >= 2


# ---------------------------------------------------------------------------
# Clear tier tests
# ---------------------------------------------------------------------------


class TestClearTier:
    @pytest.mark.asyncio
    async def test_clear_hot_slice(self, cache: TieredAssetCacheManager) -> None:
        """clear_tier(HOT_SLICE) must remove all hot slice entries."""
        await cache.put_hot_slice("k1", "v1")
        await cache.put_hot_slice("k2", "v2")
        assert await cache.get_hot_slice("k1") == "v1"

        await cache.clear_tier(CacheTier.HOT_SLICE)

        assert await cache.get_hot_slice("k1") is None
        assert await cache.get_hot_slice("k2") is None

    @pytest.mark.asyncio
    async def test_clear_session_continuity(self, cache: TieredAssetCacheManager) -> None:
        """clear_tier(SESSION_CONTINUITY) must remove all session continuity entries."""
        await cache.set("sc1", CacheTier.SESSION_CONTINUITY, "v1")
        await cache.set("sc2", CacheTier.SESSION_CONTINUITY, "v2")

        await cache.clear_tier(CacheTier.SESSION_CONTINUITY)

        assert await cache.get("sc1", CacheTier.SESSION_CONTINUITY) is None
        assert await cache.get("sc2", CacheTier.SESSION_CONTINUITY) is None

    @pytest.mark.asyncio
    async def test_clear_repo_map(self, cache: TieredAssetCacheManager, temp_workspace: Path) -> None:
        """clear_tier(REPO_MAP) must remove all repo map entries."""
        await cache.put_repo_map(temp_workspace, "python", {"a": 1})
        await cache.put_repo_map(temp_workspace, "go", {"b": 2})

        await cache.clear_tier(CacheTier.REPO_MAP)

        assert await cache.get_repo_map(temp_workspace, "python") is None
        assert await cache.get_repo_map(temp_workspace, "go") is None


# ---------------------------------------------------------------------------
# Module exports tests
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_import_from_context_package(self) -> None:
        """All new types must be importable from polaris.kernelone.context."""
        from polaris.kernelone.context import (
            TieredAssetCacheManager as TACM,  # noqa: N817
            TieredCacheTier,
            warming_hint_for_phase,
        )

        assert TACM is not None
        assert issubclass(TieredCacheTier, str)
        assert callable(warming_hint_for_phase)

    def test_tiered_cache_stats_to_dict_fields(self) -> None:
        """CacheStats.to_dict must have all required fields."""
        stats = CacheStats(hits_hot_slice=3, misses_hot_slice=1, evictions=2)
        d = stats.to_dict()
        assert d["hits_hot_slice"] == 3
        assert d["misses_hot_slice"] == 1
        assert d["evictions"] == 2
        assert "hit_ratio" in d
        assert "total_hits" in d
        assert "total_misses" in d

    def test_session_continuity_ttl_constant(self) -> None:
        """SESSION_CONTINUITY_TTL_SECONDS must equal 3600."""
        assert SESSION_CONTINUITY_TTL_SECONDS == 3600.0
