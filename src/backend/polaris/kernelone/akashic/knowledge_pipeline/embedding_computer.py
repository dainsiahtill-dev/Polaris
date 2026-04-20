"""Batch Embedding Computer using KernelEmbeddingPort.

Provides:
- Batch processing for efficiency (max_batch_size=32)
- Async concurrency control (asyncio.Semaphore)
- Embedding cache reuse from semantic_cache pattern
- KernelEmbeddingPort integration for model flexibility
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from polaris.kernelone.akashic.knowledge_pipeline.protocols import EmbeddingComputerPort
from polaris.kernelone.akashic.protocols import AVAILABLE_EMBEDDING_MODELS

logger = logging.getLogger(__name__)


class EmbeddingComputer:
    """Batch embedding computation with cache and concurrency control.

    Wraps KernelEmbeddingPort to provide:
    1. Batch processing for throughput
    2. Async concurrency limiting via Semaphore
    3. Text-hash based exact-match cache
    4. Configurable embedding model

    Usage::

        from polaris.kernelone.llm.embedding import get_default_embedding_port

        port = get_default_embedding_port()
        computer = EmbeddingComputer(embedding_port=port, max_batch_size=32)

        texts = ["Hello world", "Semantic search is powerful"]
        embeddings = await computer.compute_batch(texts)
    """

    def __init__(
        self,
        embedding_port: Any,  # KernelEmbeddingPort but protocol not runtime_checkable
        *,
        model: str = "nomic-embed-text",
        max_batch_size: int = 32,
        max_concurrency: int = 8,
        cache_size: int = 1024,
    ) -> None:
        self._port = embedding_port
        self._model = model if model in AVAILABLE_EMBEDDING_MODELS else "nomic-embed-text"
        self._max_batch = max_batch_size
        self._semaphore = asyncio.Semaphore(max_concurrency)

        # Exact-match cache: text_hash -> embedding
        self._cache: dict[str, list[float]] = {}
        self._cache_order: list[str] = []  # For LRU eviction
        self._cache_size = cache_size

    def _text_hash(self, text: str) -> str:
        """Compute deterministic hash for cache key."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def _get_cached(self, text: str) -> list[float] | None:
        """Get cached embedding if available."""
        text_hash = self._text_hash(text)
        if text_hash in self._cache:
            # Move to end (LRU)
            self._cache_order.remove(text_hash)
            self._cache_order.append(text_hash)
            return self._cache[text_hash]
        return None

    def _set_cached(self, text: str, embedding: list[float]) -> None:
        """Cache embedding with LRU eviction."""
        text_hash = self._text_hash(text)

        # Evict if at capacity
        while len(self._cache) >= self._cache_size:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)

        self._cache[text_hash] = embedding
        self._cache_order.append(text_hash)

    async def compute_batch(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """Compute embedding vectors for multiple texts.

        Uses batch processing with concurrency control.
        Check cache first for exact text matches.

        Args:
            texts: List of text strings to embed
            model: Optional override for embedding model

        Returns:
            List of embedding vectors in same order as input texts
        """
        if not texts:
            return []

        effective_model = model or self._model

        # Phase 1: Check cache for each text
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []

        for i, text in enumerate(texts):
            cached = self._get_cached(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            logger.debug("Cache hit for all %d texts", len(texts))
            return results  # type: ignore[return-value]

        # Phase 2: Batch compute uncached embeddings
        uncached_texts = [texts[i] for i in uncached_indices]

        # Process in sub-batches to respect max_batch_size
        computed_embeddings: list[list[float]] = []

        for batch_start in range(0, len(uncached_texts), self._max_batch):
            batch_texts = uncached_texts[batch_start : batch_start + self._max_batch]

            # Compute embeddings in parallel with semaphore
            async with self._semaphore:
                batch_embeddings = await self._compute_embeddings(batch_texts, effective_model)

            computed_embeddings.extend(batch_embeddings)

        # Phase 3: Update cache and fill results
        for idx, original_idx in enumerate(uncached_indices):
            embedding = computed_embeddings[idx]
            text = texts[original_idx]

            # Cache the result
            self._set_cached(text, embedding)
            results[original_idx] = embedding

        logger.debug(
            "Computed %d embeddings (%d cache hits, %d new)",
            len(texts),
            len(texts) - len(uncached_texts),
            len(uncached_texts),
        )

        return results  # type: ignore[return-value]

    async def _compute_embeddings(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        """Compute embeddings for a batch of texts.

        Uses the underlying KernelEmbeddingPort. If the port's
        get_embedding is sync, runs in thread pool.
        """
        loop = asyncio.get_running_loop()

        embeddings: list[list[float]] = []
        for text in texts:
            try:
                # Check if get_embedding is a coroutine function
                import inspect

                if inspect.iscoroutinefunction(self._port.get_embedding):
                    embedding = await self._port.get_embedding(text, model=model)
                else:
                    # Run sync function in thread pool
                    # Use default args to bind loop variables early (B023 fix)
                    def _sync_wrapper(_t: str = text, _m: str = model) -> list[float]:
                        _result = self._port.get_embedding(_t, model=_m)
                        return _result if _result else self._zero_embedding()

                    embedding = await loop.run_in_executor(None, _sync_wrapper)
                embeddings.append(embedding if embedding else self._zero_embedding())
            except (RuntimeError, ValueError) as exc:
                logger.warning("Embedding computation failed for text[:50]=%s: %s", text[:50], exc)
                embeddings.append(self._zero_embedding())

        return embeddings

    def _zero_embedding(self, dims: int = 384) -> list[float]:
        """Return a zero embedding vector as fallback."""
        return [0.0] * dims

    async def compute_single(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float]:
        """Compute embedding for a single text.

        Convenience method wrapping compute_batch.
        """
        results = await self.compute_batch([text], model=model)
        return results[0]

    def get_stats(self) -> dict[str, Any]:
        """Get embedding computer statistics."""
        return {
            "model": self._model,
            "cache_size": len(self._cache),
            "cache_capacity": self._cache_size,
            "cache_hit_rate": "n/a",  # Would need tracking over time
        }


# Type annotation for protocol
EmbeddingComputer.__protocol__ = EmbeddingComputerPort  # type: ignore[attr-defined]


__all__ = ["EmbeddingComputer"]
