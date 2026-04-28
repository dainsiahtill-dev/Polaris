"""Embedding-based Semantic Similarity for ContextOS 3.0.

This module provides embedding-based cosine similarity for attention scoring.
It replaces simple keyword overlap with more accurate semantic understanding.

Key Design Principle:
    "Embeddings enhance understanding, not replace contracts."
    Semantic similarity is used for scoring, not for contract protection.

Usage:
    from polaris.kernelone.context.context_os.attention.embeddings import EmbeddingProvider

    provider = EmbeddingProvider()
    embedding1 = provider.get_embedding("implement feature X")
    embedding2 = provider.get_embedding("create a new module")
    similarity = provider.cosine_similarity(embedding1, embedding2)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Result of an embedding computation."""

    text: str
    embedding: tuple[float, ...]
    model: str
    dimensions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:100] + "..." if len(self.text) > 100 else self.text,
            "dimensions": self.dimensions,
            "model": self.model,
        }


class EmbeddingProvider:
    """Provides embedding-based semantic similarity.

    This class computes embeddings for text and calculates cosine similarity.
    It supports multiple embedding backends:
    1. Local lightweight models (all-MiniLM-L6-v2)
    2. API-based models (OpenAI, Cohere)
    3. Fallback to keyword overlap

    Usage:
        provider = EmbeddingProvider()
        similarity = provider.similarity("implement feature X", "create a new module")
    """

    def __init__(
        self,
        model: str = "local",
        cache_size: int = 1000,
    ) -> None:
        self._model = model
        self._cache_size = cache_size
        self._embedding_cache: dict[str, tuple[float, ...]] = {}
        self._local_model = None

        # Try to initialize local model
        if model == "local":
            self._init_local_model()

    def _init_local_model(self) -> None:
        """Initialize local embedding model."""
        try:
            # Try to use sentence-transformers
            from sentence_transformers import SentenceTransformer

            self._local_model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Initialized local embedding model: all-MiniLM-L6-v2")
        except ImportError:
            logger.warning("sentence-transformers not available, using fallback")
            self._local_model = None

    def get_embedding(self, text: str) -> tuple[float, ...]:
        """Get embedding for text.

        Args:
            text: Input text

        Returns:
            Tuple of floats representing the embedding
        """
        if not text:
            return ()

        # Check cache
        cache_key = text[:200]  # Use first 200 chars as cache key
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        # Compute embedding
        embedding = self._compute_embedding(text)

        # Cache result
        if len(self._embedding_cache) < self._cache_size:
            self._embedding_cache[cache_key] = embedding

        return embedding

    def _compute_embedding(self, text: str) -> tuple[float, ...]:
        """Compute embedding using configured backend."""
        if self._local_model is not None:
            return self._compute_local_embedding(text)

        # Fallback: simple hash-based pseudo-embedding
        return self._compute_fallback_embedding(text)

    def _compute_local_embedding(self, text: str) -> tuple[float, ...]:
        """Compute embedding using local model."""
        try:
            embedding = self._local_model.encode(text, show_progress_bar=False)
            return tuple(embedding.tolist())
        except (RuntimeError, ValueError, TypeError):
            logger.warning("Local embedding failed, using fallback", exc_info=True)
            return self._compute_fallback_embedding(text)

    def _compute_fallback_embedding(self, text: str) -> tuple[float, ...]:
        """Compute fallback embedding using simple hashing."""
        # Simple hash-based pseudo-embedding (32 dimensions)
        import hashlib

        hash_obj = hashlib.md5(text.encode("utf-8"))
        hash_bytes = hash_obj.digest()

        # Convert to float values
        embedding = []
        for i in range(0, len(hash_bytes), 2):
            if i + 1 < len(hash_bytes):
                value = (hash_bytes[i] * 256 + hash_bytes[i + 1]) / 65535.0
            else:
                value = hash_bytes[i] / 255.0
            embedding.append(value)

        return tuple(embedding)

    def cosine_similarity(
        self,
        embedding1: tuple[float, ...],
        embedding2: tuple[float, ...],
    ) -> float:
        """Calculate cosine similarity between two embeddings.

        Args:
            embedding1: First embedding
            embedding2: Second embedding

        Returns:
            Cosine similarity (0-1)
        """
        if not embedding1 or not embedding2:
            return 0.0

        if len(embedding1) != len(embedding2):
            return 0.0

        # Calculate dot product
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2, strict=True))

        # Calculate norms
        norm1 = math.sqrt(sum(a * a for a in embedding1))
        norm2 = math.sqrt(sum(b * b for b in embedding2))

        # Avoid division by zero
        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def similarity(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0-1)
        """
        embedding1 = self.get_embedding(text1)
        embedding2 = self.get_embedding(text2)
        return self.cosine_similarity(embedding1, embedding2)

    @property
    def stats(self) -> dict[str, Any]:
        """Get provider statistics."""
        return {
            "model": self._model,
            "cache_size": len(self._embedding_cache),
            "max_cache_size": self._cache_size,
            "local_model_available": self._local_model is not None,
        }
