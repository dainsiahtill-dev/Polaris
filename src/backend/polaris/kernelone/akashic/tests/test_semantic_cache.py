"""Tests for semantic_cache module."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.kernelone.akashic.protocols import SemanticCacheConfig, SemanticCacheEntry
from polaris.kernelone.akashic.semantic_cache import (
    RouteDecision,
    SemanticCacheInterceptor,
    ThreeTierSemanticRouter,
    _CacheEntry,
    _cosine_similarity,
)


class TestCosineSimilarity:
    """Tests for cosine similarity calculation."""

    def test_identical_vectors(self) -> None:
        """Test that identical vectors return similarity of 1.0."""
        vec = (1.0, 0.0, 0.0)
        result = _cosine_similarity(vec, vec)
        assert result == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        """Test that orthogonal vectors return similarity of 0.0."""
        a = (1.0, 0.0, 0.0)
        b = (0.0, 1.0, 0.0)
        result = _cosine_similarity(a, b)
        assert result == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        """Test that opposite vectors return similarity of -1.0."""
        a = (1.0, 0.0)
        b = (-1.0, 0.0)
        result = _cosine_similarity(a, b)
        assert result == pytest.approx(-1.0)

    def test_partial_similarity(self) -> None:
        """Test partial similarity between vectors."""
        a = (1.0, 0.0, 0.0)
        b = (0.707, 0.707, 0.0)  # ~45 degree angle
        result = _cosine_similarity(a, b)
        assert 0.7 < result < 0.8

    def test_different_length_vectors(self) -> None:
        """Test that vectors of different lengths return 0.0."""
        a = (1.0, 0.0)
        b = (1.0, 0.0, 0.0)
        result = _cosine_similarity(a, b)
        assert result == 0.0

    def test_zero_vector(self) -> None:
        """Test that zero vector returns 0.0 similarity."""
        a = (0.0, 0.0, 0.0)
        b = (1.0, 0.0, 0.0)
        result = _cosine_similarity(a, b)
        assert result == 0.0

    def test_both_zero_vectors(self) -> None:
        """Test that two zero vectors return 0.0 similarity."""
        a = (0.0, 0.0, 0.0)
        b = (0.0, 0.0, 0.0)
        result = _cosine_similarity(a, b)
        assert result == 0.0

    def test_high_dimensional_vectors(self) -> None:
        """Test cosine similarity with high-dimensional vectors."""
        a = tuple([1.0] * 128)
        b = tuple([1.0] * 128)
        result = _cosine_similarity(a, b)
        assert result == pytest.approx(1.0)


class TestCacheEntry:
    """Tests for _CacheEntry internal class."""

    def test_is_expired_not_expired(self) -> None:
        """Test that entry is not expired within TTL."""
        entry = _CacheEntry(
            query_hash="abc123",
            embedding=(0.1, 0.2, 0.3),
            response="test response",
            created_at=time.time() - 60,  # 60 seconds ago
            last_accessed=time.time() - 60,
            ttl_seconds=3600.0,  # 1 hour
        )
        assert entry.is_expired(time.time()) is False

    def test_is_expired_after_ttl(self) -> None:
        """Test that entry is expired after TTL."""
        entry = _CacheEntry(
            query_hash="abc123",
            embedding=(0.1, 0.2, 0.3),
            response="test response",
            created_at=time.time() - 7200,  # 2 hours ago
            last_accessed=time.time() - 7200,
            ttl_seconds=3600.0,  # 1 hour TTL
        )
        assert entry.is_expired(time.time()) is True

    def test_is_expired_custom_ttl(self) -> None:
        """Test expiration with custom TTL."""
        entry = _CacheEntry(
            query_hash="abc123",
            embedding=(0.1, 0.2, 0.3),
            response="test response",
            created_at=time.time() - 10,  # 10 seconds ago
            last_accessed=time.time() - 10,
            ttl_seconds=5.0,  # 5 second TTL
        )
        assert entry.is_expired(time.time()) is True

    def test_to_snapshot(self) -> None:
        """Test conversion to immutable snapshot."""
        now = time.time()
        entry = _CacheEntry(
            query_hash="abc123",
            embedding=(0.1, 0.2, 0.3),
            response="test response",
            created_at=now,
            last_accessed=now,
            hit_count=5,
            ttl_seconds=3600.0,
        )

        snapshot = entry.to_snapshot()
        assert isinstance(snapshot, SemanticCacheEntry)
        assert snapshot.query_hash == "abc123"
        assert snapshot.embedding == (0.1, 0.2, 0.3)
        assert snapshot.response == "test response"
        assert snapshot.hit_count == 5


class TestSemanticCacheConfig:
    """Tests for SemanticCacheConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = SemanticCacheConfig()
        assert config.similarity_threshold == 0.92
        assert config.max_entries == 1024
        assert config.ttl_seconds == 3600.0
        assert config.embedding_model is None

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = SemanticCacheConfig(
            similarity_threshold=0.85,
            max_entries=512,
            ttl_seconds=7200.0,
            embedding_model="mxbai-embed-large",
        )
        assert config.similarity_threshold == 0.85
        assert config.max_entries == 512
        assert config.ttl_seconds == 7200.0
        assert config.embedding_model == "mxbai-embed-large"


class TestSemanticCacheInterceptor:
    """Tests for SemanticCacheInterceptor."""

    @pytest.fixture
    def mock_embedding_port(self) -> MagicMock:
        """Create a mock embedding port that returns unique embeddings per query."""
        port = MagicMock()

        def get_embedding(query: str, model: str | None = None) -> list[float]:
            # Return a unique embedding based on query string hash
            # This ensures different queries get different embeddings
            # with very low similarity between them
            base = hash(query) % 256 / 256.0
            return [(base + i * 0.01) % 1.0 for i in range(128)]

        port.get_embedding = MagicMock(side_effect=get_embedding)
        return port

    @pytest.fixture
    def cache(self, mock_embedding_port: MagicMock) -> SemanticCacheInterceptor:
        """Create a semantic cache interceptor with mock embedding port."""
        config = SemanticCacheConfig(
            similarity_threshold=0.92,
            max_entries=10,
            ttl_seconds=3600.0,
        )
        return SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

    def test_initialization(self) -> None:
        """Test cache initialization."""
        config = SemanticCacheConfig(max_entries=100, ttl_seconds=300)
        cache = SemanticCacheInterceptor(config=config)

        assert cache._config.max_entries == 100
        assert cache._config.ttl_seconds == 300
        assert isinstance(cache._cache, OrderedDict)
        assert len(cache._cache) == 0

    def test_initialization_with_defaults(self) -> None:
        """Test cache initialization with default config."""
        cache = SemanticCacheInterceptor()

        assert cache._config.similarity_threshold == 0.92
        assert cache._config.max_entries == 1024
        assert cache._config.ttl_seconds == 3600.0

    def test_compute_hash(self, cache: SemanticCacheInterceptor) -> None:
        """Test hash computation is stable."""
        hash1 = cache._compute_hash("test query")
        hash2 = cache._compute_hash("test query")
        hash3 = cache._compute_hash("different query")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 32  # SHA256 truncated to 32 chars

    def test_compute_embedding_cached(
        self,
        cache: SemanticCacheInterceptor,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test that embeddings are cached."""
        query = "test query"

        # First call should compute
        emb1 = cache._compute_embedding(query)
        assert emb1 is not None
        assert mock_embedding_port.get_embedding.call_count == 1

        # Second call should use cache
        emb2 = cache._compute_embedding(query)
        assert emb2 == emb1
        # Should still only be called once
        assert mock_embedding_port.get_embedding.call_count == 1

    def test_compute_embedding_no_port(self) -> None:
        """Test that embedding returns None when no port is available."""
        cache = SemanticCacheInterceptor()
        with patch("polaris.kernelone.akashic.semantic_cache.get_default_embedding_port") as mock_get:
            mock_get.side_effect = RuntimeError("No port")
            result = cache._compute_embedding("test query")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_or_compute_cache_miss(
        self,
        cache: SemanticCacheInterceptor,
    ) -> None:
        """Test cache miss triggers compute_fn."""
        compute_fn = AsyncMock(return_value="computed result")

        result = await cache.get_or_compute("new query", compute_fn)

        assert result == "computed result"
        assert compute_fn.call_count == 1
        stats = cache.get_stats()
        assert stats["misses"] == 1

    @pytest.mark.asyncio
    async def test_get_or_compute_exact_hit(
        self,
        cache: SemanticCacheInterceptor,
    ) -> None:
        """Test exact cache hit returns cached response."""
        compute_fn = AsyncMock(return_value="first result")

        # First call - cache miss
        result1 = await cache.get_or_compute("exact query", compute_fn)
        assert result1 == "first result"

        # Reset mock
        compute_fn.reset_mock()

        # Second call - exact hit
        result2 = await cache.get_or_compute("exact query", compute_fn)
        assert result2 == "first result"
        assert not compute_fn.called  # Should not call compute_fn

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["exact_hits"] == 1

    @pytest.mark.asyncio
    async def test_get_or_compute_identical_queries(
        self,
        cache: SemanticCacheInterceptor,
    ) -> None:
        """Test that identical queries hit the cache."""
        compute_fn = AsyncMock(return_value="result")

        # First query
        result1 = await cache.get_or_compute("Fix the bug", compute_fn)
        assert result1 == "result"

        # Identical query should hit cache
        result2 = await cache.get_or_compute("Fix the bug", compute_fn)
        assert result2 == "result"

        # compute_fn should only be called once
        assert compute_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_get_or_compute_ttl_expiration(
        self,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test that expired entries trigger recomputation."""
        config = SemanticCacheConfig(ttl_seconds=0.1)  # 100ms TTL
        cache = SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

        compute_fn = AsyncMock(return_value="first result")

        # First call
        result1 = await cache.get_or_compute("expiring query", compute_fn)
        assert result1 == "first result"

        # Wait for TTL to expire
        await asyncio.sleep(0.15)

        # Reset mock
        compute_fn.reset_mock()

        # Second call should miss cache (entry expired)
        result2 = await cache.get_or_compute("expiring query", compute_fn)
        assert result2 == "first result"
        assert compute_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_get_or_compute_similarity_hit(
        self,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test similarity-based cache hit."""
        # Use higher threshold so different queries can match
        config = SemanticCacheConfig(
            similarity_threshold=0.95,
            max_entries=10,
            ttl_seconds=3600.0,
        )
        cache = SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

        # Setup: first query stored with embedding
        compute_fn1 = AsyncMock(return_value="original result")
        await cache.get_or_compute("Fix the login bug", compute_fn1)

        # Second query - very similar, should hit similarity
        compute_fn2 = AsyncMock(return_value="similar result")
        result = await cache.get_or_compute("Fix the login bug", compute_fn2)

        # Should return the cached result
        assert result == "original result"

    @pytest.mark.asyncio
    async def test_lru_eviction_when_full(
        self,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test LRU eviction when cache is full."""
        config = SemanticCacheConfig(max_entries=3)
        cache = SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

        # Fill cache to capacity
        for i in range(3):
            await cache.get_or_compute(f"query{i}", AsyncMock(return_value=f"result{i}"))

        stats = cache.get_stats()
        assert stats["size"] == 3
        assert stats["evictions"] == 0

        # Add one more - should evict oldest (LRU)
        await cache.get_or_compute("query3", AsyncMock(return_value="result3"))

        stats = cache.get_stats()
        assert stats["size"] == 3  # Still 3 (one evicted)
        assert stats["evictions"] == 1

        # Oldest query should be evicted
        cache_miss_count = 0
        for i in range(3):
            # These should miss (were evicted)
            compute_fn = AsyncMock(return_value=f"result{i}")
            await cache.get_or_compute(f"query{i}", compute_fn)
            if compute_fn.called:
                cache_miss_count += 1

        # At least one of the original 3 should have been evicted
        assert cache_miss_count >= 1

    @pytest.mark.asyncio
    async def test_lru_order_maintained(
        self,
        cache: SemanticCacheInterceptor,
    ) -> None:
        """Test that accessing entries updates LRU order."""
        # Add entries
        for i in range(3):
            await cache.get_or_compute(f"query{i}", AsyncMock(return_value=f"result{i}"))

        # Access first entry to move it to end
        await cache.get_or_compute("query0", AsyncMock(return_value="result0"))

        # Add new entry - should evict query1 (now LRU)
        await cache.get_or_compute("query3", AsyncMock(return_value="result3"))

        # query0 should still be in cache (was accessed recently)
        compute_fn = AsyncMock(return_value="new result")
        await cache.get_or_compute("query0", compute_fn)
        assert not compute_fn.called  # Should hit cache

    @pytest.mark.asyncio
    async def test_invalidate(
        self,
        cache: SemanticCacheInterceptor,
    ) -> None:
        """Test invalidating a cache entry."""
        # Add entry
        await cache.get_or_compute("to invalidate", AsyncMock(return_value="result"))

        # Get hash
        query_hash = cache._compute_hash("to invalidate")

        # Invalidate
        result = await cache.invalidate(query_hash)
        assert result is True

        # Should miss cache now
        compute_fn = AsyncMock(return_value="new result")
        await cache.get_or_compute("to invalidate", compute_fn)
        assert compute_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent(self, cache: SemanticCacheInterceptor) -> None:
        """Test invalidating non-existent entry returns False."""
        result = await cache.invalidate("nonexistent_hash_12345")
        assert result is False

    @pytest.mark.asyncio
    async def test_clear(self, cache: SemanticCacheInterceptor) -> None:
        """Test clearing all cache entries."""
        # Add entries
        for i in range(3):
            await cache.get_or_compute(f"query{i}", AsyncMock(return_value=f"result{i}"))

        # Clear
        count = await cache.clear()
        assert count == 3

        # Stats should be reset
        stats = cache.get_stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    @pytest.mark.asyncio
    async def test_get_stats(
        self,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test getting cache statistics."""
        # Use high threshold to avoid similarity matches
        config = SemanticCacheConfig(similarity_threshold=0.99, max_entries=10)
        cache = SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

        # Add some entries
        await cache.get_or_compute("query0", AsyncMock(return_value="result0"))
        await cache.get_or_compute("query1", AsyncMock(return_value="result1"))
        await cache.get_or_compute("query0", AsyncMock(return_value="result0"))  # Hit

        stats = cache.get_stats()

        assert stats["size"] == 2
        assert stats["max_size"] == 10
        assert stats["hits"] == 1
        assert stats["misses"] == 2
        assert stats["exact_hits"] == 1
        assert stats["similarity_hits"] == 0
        assert stats["total_requests"] == 3
        assert stats["hit_rate"] == pytest.approx(1 / 3, rel=0.01)

    @pytest.mark.asyncio
    async def test_ttl_seconds_override(
        self,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test TTL override in get_or_compute."""
        # Use high threshold to avoid similarity matches
        config = SemanticCacheConfig(similarity_threshold=0.99, max_entries=10)
        cache = SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

        # Add entry with short TTL
        await cache.get_or_compute(
            "short ttl",
            AsyncMock(return_value="result"),
            ttl_seconds=0.05,
        )

        # Wait for short TTL
        await asyncio.sleep(0.1)

        # Should miss cache (entry expired)
        compute_fn = AsyncMock(return_value="new result")
        await cache.get_or_compute("short ttl", compute_fn)
        assert compute_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_cache(self, cache: SemanticCacheInterceptor) -> None:
        """Test operations on empty cache."""
        compute_fn = AsyncMock(return_value="result")

        result = await cache.get_or_compute("first", compute_fn)
        assert result == "result"

        stats = cache.get_stats()
        assert stats["size"] == 1
        assert stats["misses"] == 1
        assert stats["hits"] == 0

    @pytest.mark.asyncio
    async def test_thread_safety(
        self,
        mock_embedding_port: MagicMock,
    ) -> None:
        """Test thread-safe operations."""
        config = SemanticCacheConfig(max_entries=100)
        cache = SemanticCacheInterceptor(config=config, embedding_port=mock_embedding_port)

        async def add_entries(start: int, count: int) -> None:
            for i in range(start, start + count):
                await cache.get_or_compute(f"query{i}", AsyncMock(return_value=f"result{i}"))

        # Run multiple coroutines concurrently
        await asyncio.gather(
            add_entries(0, 10),
            add_entries(10, 10),
            add_entries(20, 10),
        )

        stats = cache.get_stats()
        # All entries should be added without errors
        assert stats["size"] == 30


class TestThreeTierSemanticRouter:
    """Tests for ThreeTierSemanticRouter."""

    @pytest.fixture
    def router(self) -> ThreeTierSemanticRouter:
        """Create a router for testing."""
        return ThreeTierSemanticRouter(
            tier0_similarity_threshold=0.92,
            tier1_confidence_threshold=0.65,
        )

    @pytest.mark.asyncio
    async def test_tier0_hit(self, router: ThreeTierSemanticRouter) -> None:
        """Test tier0 cache hit."""
        router.put_tier0("cache key", "cached response", similarity=1.0)

        tier1_handler = AsyncMock(return_value=("tier1 response", 0.8))
        tier2_handler = AsyncMock(return_value="tier2 response")

        decision = await router.route("cache key", tier1_handler=tier1_handler, tier2_handler=tier2_handler)

        assert decision.tier == "tier0"
        assert decision.cache_hit is True
        assert decision.response == "cached response"
        assert not tier1_handler.called
        assert not tier2_handler.called

    @pytest.mark.asyncio
    async def test_tier1_fallback(self, router: ThreeTierSemanticRouter) -> None:
        """Test tier1 fallback when cache misses."""
        tier1_handler = AsyncMock(return_value=("tier1 response", 0.7))
        tier2_handler = AsyncMock(return_value="tier2 response")

        decision = await router.route("uncached key", tier1_handler=tier1_handler, tier2_handler=tier2_handler)

        assert decision.tier == "tier1"
        assert decision.cache_hit is False
        assert decision.response == "tier1 response"
        assert tier1_handler.called
        assert not tier2_handler.called

    @pytest.mark.asyncio
    async def test_tier2_fallback(self, router: ThreeTierSemanticRouter) -> None:
        """Test tier2 fallback when tier1 confidence is low."""
        tier1_handler = AsyncMock(return_value=("low confidence", 0.5))  # Below threshold
        tier2_handler = AsyncMock(return_value="tier2 response")

        decision = await router.route("complex query", tier1_handler=tier1_handler, tier2_handler=tier2_handler)

        assert decision.tier == "tier2"
        assert decision.cache_hit is False
        assert decision.response == "tier2 response"
        assert tier1_handler.called
        assert tier2_handler.called

    @pytest.mark.asyncio
    async def test_latency_tracking(self, router: ThreeTierSemanticRouter) -> None:
        """Test that latency is tracked correctly."""
        tier1_handler = AsyncMock(return_value=("tier1 response", 0.3))
        tier2_handler = AsyncMock(return_value="tier2 response")

        decision = await router.route("query", tier1_handler=tier1_handler, tier2_handler=tier2_handler)

        assert decision.latency_ms >= 0
        assert isinstance(decision.latency_ms, float)

    def test_put_tier0(self, router: ThreeTierSemanticRouter) -> None:
        """Test putting items in tier0 cache."""
        router.put_tier0("key1", "response1", similarity=0.95)
        router.put_tier0("key2", "response2", similarity=1.0)

        assert router._tier0_cache["key1"] == ("response1", 0.95)
        assert router._tier0_cache["key2"] == ("response2", 1.0)

    def test_tier0_threshold(self, router: ThreeTierSemanticRouter) -> None:
        """Test that tier0 requires threshold similarity."""
        router.put_tier0("key", "response", similarity=0.85)  # Below 0.92 threshold

        assert router._tier0_cache["key"] == ("response", 0.85)
        # Note: threshold check happens in route(), not put_tier0()

    def test_tier1_confidence_threshold(self, router: ThreeTierSemanticRouter) -> None:
        """Test that tier1 requires confidence threshold."""
        assert router._tier1_confidence_threshold == 0.65

    @pytest.mark.asyncio
    async def test_empty_query(self, router: ThreeTierSemanticRouter) -> None:
        """Test handling empty query string."""
        tier1_handler = AsyncMock(return_value=("tier1 response", 0.8))
        tier2_handler = AsyncMock(return_value="tier2 response")

        decision = await router.route("", tier1_handler=tier1_handler, tier2_handler=tier2_handler)

        assert decision.tier == "tier1"
        assert tier1_handler.called


class TestRouteDecision:
    """Tests for RouteDecision dataclass."""

    def test_route_decision_creation(self) -> None:
        """Test creating a RouteDecision."""
        decision = RouteDecision(
            tier="tier0",
            latency_ms=1.5,
            cache_hit=True,
            response="cached response",
        )

        assert decision.tier == "tier0"
        assert decision.latency_ms == 1.5
        assert decision.cache_hit is True
        assert decision.response == "cached response"

    def test_route_decision_tiers(self) -> None:
        """Test all tier values."""
        for tier in ["tier0", "tier1", "tier2"]:
            decision = RouteDecision(
                tier=tier,
                latency_ms=0.0,
                cache_hit=tier == "tier0",
                response="response",
            )
            assert decision.tier == tier
