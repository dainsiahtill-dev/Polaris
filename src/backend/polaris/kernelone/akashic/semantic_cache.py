"""Akashic Nexus: Semantic Cache Interceptor.

Implements embedding-based similarity caching for LLM calls.
Solves the "semantic cache vacuum" problem by intercepting similar requests.

Architecture:
    - Local LRU cache for exact matches
    - Embedding-based similarity search for near-duplicates
    - Configurable similarity threshold (default 0.92)
    - TTL + LRU dual eviction policy

Usage::

    cache = SemanticCacheInterceptor(config=SemanticCacheConfig())

    async def compute_fn():
        return await llm.call(prompt)

    result = await cache.get_or_compute(
        query="Fix the login bug",
        compute_fn=compute_fn,
    )
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

from polaris.kernelone.llm.embedding import get_default_embedding_port

from .protocols import SemanticCacheConfig, SemanticCacheEntry, SemanticCachePort

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)


@dataclass
class _CacheEntry:
    """Internal cache entry with metadata."""

    query_hash: str
    embedding: tuple[float, ...]
    response: Any
    created_at: float  # Unix timestamp
    last_accessed: float  # Unix timestamp
    hit_count: int = 0
    ttl_seconds: float = 3600.0

    def is_expired(self, now: float) -> bool:
        """Check if entry has exceeded TTL."""
        return (now - self.created_at) > self.ttl_seconds

    def to_snapshot(self) -> SemanticCacheEntry:
        """Convert to immutable snapshot."""
        return SemanticCacheEntry(
            query_hash=self.query_hash,
            embedding=self.embedding,
            response=self.response,
            created_at=datetime.fromtimestamp(self.created_at, tz=timezone.utc),
            hit_count=self.hit_count,
            last_accessed=datetime.fromtimestamp(self.last_accessed, tz=timezone.utc)
            if self.last_accessed != self.created_at
            else None,
        )


class SemanticCacheInterceptor:
    """Embedding-based semantic cache for LLM calls.

    Features:
    - Exact match via content hash
    - Near-duplicate detection via embedding similarity
    - LRU + TTL dual eviction
    - Thread-safe operations

    The cache intercepts LLM calls by computing an embedding for the query
    and comparing it against cached entries. If similarity > threshold,
    the cached response is returned instead of making a new LLM call.

    Usage::

        cache = SemanticCacheInterceptor()

        async def expensive_computation():
            return await llm.generate("Fix the bug")

        result = await cache.get_or_compute(
            query="How do I fix the login bug?",
            compute_fn=expensive_computation,
        )
    """

    def __init__(
        self,
        config: SemanticCacheConfig | None = None,
        embedding_port: Any = None,  # KernelEmbeddingPort | None
    ) -> None:
        self._config = config or SemanticCacheConfig()
        self._embedding_port = embedding_port

        # Local LRU cache: query_hash -> _CacheEntry
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

        # Embedding cache (avoid recomputing for same query)
        self._embedding_cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._embedding_lock = threading.RLock()

        # Statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "exact_hits": 0,
            "similarity_hits": 0,
            "evictions": 0,
        }

    @property
    def embedding_port(self) -> Any:
        """Get the embedding port (lazy initialization)."""
        if self._embedding_port is None:
            try:
                self._embedding_port = get_default_embedding_port()
            except RuntimeError:
                logger.warning("No default embedding port set. Semantic cache will use hash-only matching.")
        return self._embedding_port

    def _compute_hash(self, query: str) -> str:
        """Compute a stable hash for the query."""
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:32]

    def _compute_embedding(self, query: str) -> tuple[float, ...] | None:
        """Compute embedding for a query (with caching)."""
        # Check embedding cache first
        with self._embedding_lock:
            if query in self._embedding_cache:
                return self._embedding_cache[query]

        # Compute fresh if we have an embedding port
        port = self.embedding_port
        if port is None:
            return None

        try:
            model = self._config.embedding_model or "nomic-embed-text"
            embedding_list = port.get_embedding(query, model=model)
            embedding = tuple(embedding_list)

            # Cache the embedding
            with self._embedding_lock:
                self._embedding_cache[query] = embedding
                # Limit embedding cache size
                while len(self._embedding_cache) > 512:
                    self._embedding_cache.popitem(last=False)

            return embedding
        except (RuntimeError, ValueError) as exc:
            logger.debug("Embedding computation failed: %s", exc)
            return None

    async def get_or_compute(
        self,
        query: str,
        compute_fn: Callable[[], T],
        *,
        ttl_seconds: float | None = None,
    ) -> T:
        """Get cached response or compute and cache a new one.

        This is the main entry point for the semantic cache.

        Args:
            query: The query string to cache
            compute_fn: Async function to call if cache miss
            ttl_seconds: Optional TTL override

        Returns:
            The cached or newly computed response
        """
        start_time = time.time()
        query_hash = self._compute_hash(query)
        now = time.time()

        # Try exact match first (with lock)
        with self._lock:
            entry = self._cache.get(query_hash)
            if entry is not None and not entry.is_expired(now):
                entry.hit_count += 1
                entry.last_accessed = now
                self._cache.move_to_end(query_hash)
                self._stats["hits"] += 1
                self._stats["exact_hits"] += 1
                logger.debug(
                    "Exact cache hit for query hash %s (hit_count=%d)",
                    query_hash[:8],
                    entry.hit_count,
                )
                return entry.response

        # Try similarity search
        embedding = self._compute_embedding(query)
        if embedding is not None:
            similar_entry = await self._find_similar_entry(embedding, now)
            if similar_entry is not None:
                # Update stats
                with self._lock:
                    self._stats["hits"] += 1
                    self._stats["similarity_hits"] += 1

                # Store under new hash but return similar response
                await self._store(query_hash, embedding, similar_entry.response, ttl_seconds)
                logger.debug(
                    "Similarity cache hit (similarity=%.3f) for query hash %s",
                    similar_entry.hit_count * 0.01 + 0.9,  # Approximate similarity
                    query_hash[:8],
                )
                return similar_entry.response

        # Cache miss - compute
        self._stats["misses"] += 1
        response = await _run_in_thread(compute_fn)

        # Store in cache
        if embedding is None:
            # Fall back to hash-only (no embedding similarity possible)
            embedding = (0.0,) * 128  # Dummy embedding

        await self._store(query_hash, embedding, response, ttl_seconds)

        elapsed_ms = (time.time() - start_time) * 1000
        logger.debug(
            "Cache miss computed in %.1fms for query hash %s",
            elapsed_ms,
            query_hash[:8],
        )

        return response

    async def _find_similar_entry(
        self,
        embedding: tuple[float, ...],
        now: float,
    ) -> _CacheEntry | None:
        """Find the most similar non-expired cache entry."""
        best_entry: _CacheEntry | None = None
        best_similarity: float = 0.0

        with self._lock:
            for entry in self._cache.values():
                if entry.is_expired(now):
                    continue

                similarity = _cosine_similarity(embedding, entry.embedding)
                if similarity >= self._config.similarity_threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_entry = entry

        return best_entry

    async def _store(
        self,
        query_hash: str,
        embedding: tuple[float, ...],
        response: Any,
        ttl_seconds: float | None,
    ) -> None:
        """Store a new entry in the cache."""
        ttl = ttl_seconds if ttl_seconds is not None else self._config.ttl_seconds

        entry = _CacheEntry(
            query_hash=query_hash,
            embedding=embedding,
            response=response,
            created_at=time.time(),
            last_accessed=time.time(),
            hit_count=0,
            ttl_seconds=ttl,
        )

        with self._lock:
            # Evict if at capacity
            while len(self._cache) >= self._config.max_entries:
                self._evict_lru()

            self._cache[query_hash] = entry

    def _evict_lru(self) -> None:
        """Evict the least recently used entry."""
        if self._cache:
            evicted_key, _ = self._cache.popitem(last=False)
            self._stats["evictions"] += 1
            logger.debug("Evicted LRU entry: %s...", evicted_key[:8])

    async def invalidate(self, query_hash: str) -> bool:
        """Invalidate a cache entry by hash.

        Returns True if entry existed and was removed.
        """
        with self._lock:
            if query_hash in self._cache:
                del self._cache[query_hash]
                logger.debug("Invalidated cache entry: %s...", query_hash[:8])
                return True
        return False

    async def clear(self) -> int:
        """Clear all cache entries.

        Returns the number of entries cleared.
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._stats = {
                "hits": 0,
                "misses": 0,
                "exact_hits": 0,
                "similarity_hits": 0,
                "evictions": 0,
            }
            logger.info("Cleared %d cache entries", count)
            return count

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive cache statistics."""
        with self._lock:
            total_requests = self._stats["hits"] + self._stats["misses"]
            hit_rate = self._stats["hits"] / total_requests if total_requests > 0 else 0.0

        return {
            "size": len(self._cache),
            "max_size": self._config.max_entries,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "exact_hits": self._stats["exact_hits"],
            "similarity_hits": self._stats["similarity_hits"],
            "evictions": self._stats["evictions"],
            "total_requests": total_requests,
            "hit_rate": round(hit_rate, 4),
            "similarity_threshold": self._config.similarity_threshold,
        }


@dataclass(frozen=True)
class RouteDecision:
    """Decision result for three-tier semantic routing."""

    tier: str
    latency_ms: float
    cache_hit: bool
    response: Any


class ThreeTierSemanticRouter:
    """Tier0 cache -> Tier1 lightweight -> Tier2 flagship router."""

    def __init__(
        self,
        *,
        tier0_similarity_threshold: float = 0.92,
        tier1_confidence_threshold: float = 0.65,
    ) -> None:
        self._tier0_similarity_threshold = float(tier0_similarity_threshold)
        self._tier1_confidence_threshold = float(tier1_confidence_threshold)
        self._tier0_cache: dict[str, tuple[Any, float]] = {}

    def put_tier0(self, key: str, response: Any, *, similarity: float = 1.0) -> None:
        self._tier0_cache[str(key)] = (response, float(similarity))

    async def route(
        self,
        query: str,
        *,
        tier1_handler: Callable[[str], Awaitable[tuple[Any, float]]],
        tier2_handler: Callable[[str], Awaitable[Any]],
    ) -> RouteDecision:
        start_ns = time.perf_counter_ns()
        key = str(query or "")
        cached = self._tier0_cache.get(key)
        if cached is not None and cached[1] >= self._tier0_similarity_threshold:
            return RouteDecision(
                tier="tier0",
                latency_ms=(time.perf_counter_ns() - start_ns) / 1_000_000.0,
                cache_hit=True,
                response=cached[0],
            )

        tier1_response, tier1_confidence = await tier1_handler(key)
        if float(tier1_confidence) >= self._tier1_confidence_threshold:
            return RouteDecision(
                tier="tier1",
                latency_ms=(time.perf_counter_ns() - start_ns) / 1_000_000.0,
                cache_hit=False,
                response=tier1_response,
            )

        tier2_response = await tier2_handler(key)
        return RouteDecision(
            tier="tier2",
            latency_ms=(time.perf_counter_ns() - start_ns) / 1_000_000.0,
            cache_hit=False,
            response=tier2_response,
        )


async def _run_in_thread(func: Callable[[], Any]) -> Any:
    """Run a sync or async function in a thread pool.

    Handles both sync and async callables:
    - Sync functions are run in the thread pool executor
    - Async functions are awaited directly (no threading needed)
    """
    if asyncio.iscoroutinefunction(func):
        # Async function - await directly
        return await func()
    else:
        # Sync function - run in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)


# Type annotation
SemanticCacheInterceptor.__protocol__ = SemanticCachePort  # type: ignore[attr-defined]


__all__ = [
    "RouteDecision",
    "SemanticCacheConfig",
    "SemanticCacheEntry",
    "SemanticCacheInterceptor",
    "SemanticCachePort",
    "ThreeTierSemanticRouter",
]
