"""
Retrieval Ranker with MMR (Maximal Marginal Relevance) Diversity Re-ranking.

This module provides advanced re-ranking capabilities for memory retrieval results,
complementing the basic scoring in MemoryStore with diversity-aware selection.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import MemoryItem
else:
    try:
        from .schema import MemoryItem
    except ImportError:
        MemoryItem = object  # type: ignore[misc]

from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.llm.embedding import get_default_embedding_port

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = resolve_env_str("embedding_model") or "nomic-embed-text"
_MAX_EMBEDDING_CACHE_SIZE = 512


@dataclass
class RankedResult:
    """A memory item with its ranking information."""

    item: MemoryItem
    relevance_score: float
    diversity_score: float
    final_score: float
    rank: int
    selection_reason: str


class MMRReranker:
    """
    Maximal Marginal Relevance (MMR) reranker for retrieval results.

    MMR balances relevance with diversity by selecting items that are:
    1. Relevant to the query
    2. Diverse from already-selected items

    Formula: MMR = argmax(λ * rel(doc) - (1-λ) * max(sim(doc, selected)))

    Where:
    - λ (lambda_) controls relevance vs diversity trade-off (0 = max diversity, 1 = max relevance)
    - rel(doc) is the relevance score
    - sim(doc, selected) is similarity to already selected documents
    """

    def __init__(
        self,
        lambda_: float = 0.5,
        enable_vector_diversity: bool = True,
        enable_semantic_clusters: bool = True,
    ) -> None:
        """
        Initialize MMR reranker.

        Args:
            lambda_: Trade-off parameter (0-1). Higher = more relevance, lower = more diversity.
            enable_vector_diversity: Use embeddings for diversity calculation.
            enable_semantic_clusters: Use kind/role clustering for diversity.
        """
        self.lambda_ = self._normalize_lambda(lambda_)
        self.enable_vector_diversity = enable_vector_diversity
        self.enable_semantic_clusters = enable_semantic_clusters
        self._embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.RLock()

    def rerank(
        self,
        items: list[MemoryItem],
        relevance_scores: dict[str, float],
        top_k: int = 10,
        *,
        lambda_override: float | None = None,
    ) -> list[RankedResult]:
        """
        Apply MMR reranking to retrieve diverse yet relevant results.

        Args:
            items: List of memory items to rerank
            relevance_scores: Dict mapping item.id to relevance score
            top_k: Number of results to return

        Returns:
            List of RankedResult with final scores and rankings
        """
        if not items:
            return []

        lambda_value = self._normalize_lambda(self.lambda_ if lambda_override is None else lambda_override)
        selected: list[MemoryItem] = []
        results: list[RankedResult] = []

        remaining = list(items)

        while len(selected) < top_k and remaining:
            best_item = None
            best_score = -float("inf")
            best_reason = ""

            for item in remaining:
                # Relevance component
                rel_score = relevance_scores.get(item.id, 0.0)

                # Diversity component
                if selected and self.enable_vector_diversity:
                    div_score = self._calculate_diversity(item, selected)
                else:
                    div_score = 0.0

                # MMR formula
                mmr_score = lambda_value * rel_score - (1 - lambda_value) * div_score

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_item = item
                    if len(selected) == 0:
                        best_reason = "Highest relevance"
                    else:
                        best_reason = f"Best relevance-diversity balance (rel={rel_score:.3f}, div={div_score:.3f})"

            if best_item is None:
                break

            selected.append(best_item)
            remaining.remove(best_item)

            rel = relevance_scores.get(best_item.id, 0.0)
            div = self._calculate_diversity(best_item, selected[:-1]) if selected else 0.0

            results.append(
                RankedResult(
                    item=best_item,
                    relevance_score=rel,
                    diversity_score=div,
                    final_score=best_score,
                    rank=len(selected),
                    selection_reason=best_reason,
                )
            )

        return results

    def _calculate_diversity(
        self,
        item: MemoryItem,
        selected: list[MemoryItem],
    ) -> float:
        """Calculate maximum similarity to any selected item."""
        if not selected:
            return 0.0

        max_sim = 0.0

        for sel_item in selected:
            # Content-based similarity (simple)
            content_sim = self._content_similarity(item, sel_item)

            # Kind/role cluster similarity
            cluster_sim = self._cluster_similarity(item, sel_item)

            # Vector similarity (if available)
            vec_sim = 0.0
            if self.enable_vector_diversity:
                vec_sim = self._vector_similarity(item, sel_item)

            # Combined similarity (weighted average)
            combined = 0.4 * content_sim + 0.3 * cluster_sim + 0.3 * vec_sim
            max_sim = max(max_sim, combined)

        return max_sim

    def _content_similarity(self, item1: MemoryItem, item2: MemoryItem) -> float:
        """Calculate content-based similarity using Jaccard."""
        text1 = set((item1.text or "").lower().split())
        text2 = set((item2.text or "").lower().split())

        kw1 = set(item1.keywords or [])
        kw2 = set(item2.keywords or [])

        set1 = text1 | kw1
        set2 = text2 | kw2

        if not set1 or not set2:
            return 0.0

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union > 0 else 0.0

    def _cluster_similarity(self, item1: MemoryItem, item2: MemoryItem) -> float:
        """Calculate similarity based on kind and role clusters."""
        kind_sim = 1.0 if item1.kind == item2.kind else 0.0
        role_sim = 1.0 if item1.role == item2.role else 0.0

        return 0.5 * kind_sim + 0.5 * role_sim

    def _vector_similarity(self, item1: MemoryItem, item2: MemoryItem) -> float:
        """Calculate cosine similarity using cached embeddings."""
        emb1 = self._get_embedding(item1.text or "")
        emb2 = self._get_embedding(item2.text or "")

        if not emb1 or not emb2:
            return 0.0

        return self._cosine_similarity(emb1, emb2)

    def _get_embedding(self, text: str) -> list[float] | None:
        """Get embedding with caching."""
        if not text:
            return None

        cache_key = hashlib.sha1(text.encode("utf-8")).hexdigest()

        with self._lock:
            cached = self._embedding_cache.get(cache_key)
            if cached is not None:
                self._embedding_cache.move_to_end(cache_key)
                return list(cached)

        try:
            emb = get_default_embedding_port().get_embedding(text, model=EMBEDDING_MODEL)
            if emb:
                with self._lock:
                    self._embedding_cache[cache_key] = list(emb)
                    self._embedding_cache.move_to_end(cache_key)
                    while len(self._embedding_cache) > _MAX_EMBEDDING_CACHE_SIZE:
                        self._embedding_cache.popitem(last=False)
                return list(emb)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Embedding lookup failed during reranking: %s", exc)

        return None

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0

        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot / (norm1 * norm2)

    @staticmethod
    def _normalize_lambda(value: float) -> float:
        """Clamp lambda into the valid MMR range."""
        return max(0.0, min(1.0, float(value)))


class AdaptiveDiversityReranker:
    """
    Adaptive diversity reranker that adjusts strategy based on query type.

    Different query types require different diversity strategies:
    - Error queries: More error diversity (different error types)
    - Architecture queries: More role diversity (different perspectives)
    - Time queries: More recency diversity
    """

    # Query type to diversity strategy mapping
    QUERY_STRATEGIES: dict[str, dict[str, float]] = {
        "pm": {"kind_weight": 0.3, "role_weight": 0.4, "recency_weight": 0.3},
        "error": {"kind_weight": 0.5, "role_weight": 0.2, "recency_weight": 0.3},
        "architecture": {"kind_weight": 0.3, "role_weight": 0.5, "recency_weight": 0.2},
        "execution": {"kind_weight": 0.3, "role_weight": 0.3, "recency_weight": 0.4},
        "quality": {"kind_weight": 0.4, "role_weight": 0.3, "recency_weight": 0.3},
        "history": {"kind_weight": 0.2, "role_weight": 0.3, "recency_weight": 0.5},
        "time": {"kind_weight": 0.2, "role_weight": 0.2, "recency_weight": 0.6},
        "comp": {"kind_weight": 0.4, "role_weight": 0.3, "recency_weight": 0.3},
        "default": {"kind_weight": 0.3, "role_weight": 0.3, "recency_weight": 0.4},
    }

    def __init__(self) -> None:
        self._mmr = MMRReranker(lambda_=0.6)

    def rerank(
        self,
        items: list[MemoryItem],
        relevance_scores: dict[str, float],
        query_type: str,
        current_step: int,
        top_k: int = 10,
    ) -> list[RankedResult]:
        """
        Apply adaptive diversity reranking based on query type.

        Args:
            items: List of memory items to rerank
            relevance_scores: Dict mapping item.id to relevance score
            query_type: Type of query (pm, error, architecture, etc.)
            current_step: Current step in the execution
            top_k: Number of results to return

        Returns:
            List of RankedResult with final scores and rankings
        """
        # Get strategy for query type
        strategy = self.QUERY_STRATEGIES.get(query_type, self.QUERY_STRATEGIES["default"])

        # Adjust lambda based on recency weight
        # Higher recency weight = lower lambda (more diversity)
        lambda_value = 1.0 - strategy["recency_weight"] * 0.6
        # Add diversity-adjusted scores
        adjusted_scores: dict[str, float] = {}

        # Calculate max values for normalization
        max_rel = max(relevance_scores.values()) if relevance_scores else 1.0

        for item in items:
            base_rel = relevance_scores.get(item.id, 0.0)

            # Kind diversity bonus
            kind_bonus = self._kind_diversity_bonus(item, items, strategy["kind_weight"])

            # Role diversity bonus
            role_bonus = self._role_diversity_bonus(item, items, strategy["role_weight"])

            # Recency bonus
            recency_bonus = self._recency_bonus(item, current_step, strategy["recency_weight"])

            # Combined score
            normalized_rel = base_rel / max_rel if max_rel > 0 else 0.0
            adjusted = normalized_rel + kind_bonus + role_bonus + recency_bonus

            # Scale back
            adjusted_scores[item.id] = adjusted * max_rel

        return self._mmr.rerank(
            items,
            adjusted_scores,
            top_k,
            lambda_override=lambda_value,
        )

    def _kind_diversity_bonus(
        self,
        item: MemoryItem,
        all_items: list[MemoryItem],
        weight: float,
    ) -> float:
        """Bonus for items with underrepresented kinds."""
        kind_counts: dict[str, int] = {}
        for i in all_items:
            kind_counts[i.kind] = kind_counts.get(i.kind, 0) + 1

        total = len(all_items)
        if total == 0:
            return 0.0

        # Inverse frequency bonus
        item_freq = kind_counts.get(item.kind, 1) / total
        bonus = (1.0 - item_freq) * weight * 0.3

        return bonus

    def _role_diversity_bonus(
        self,
        item: MemoryItem,
        all_items: list[MemoryItem],
        weight: float,
    ) -> float:
        """Bonus for items with underrepresented roles."""
        role_counts: dict[str, int] = {}
        for i in all_items:
            role_counts[i.role] = role_counts.get(i.role, 0) + 1

        total = len(all_items)
        if total == 0:
            return 0.0

        item_freq = role_counts.get(item.role, 1) / total
        bonus = (1.0 - item_freq) * weight * 0.3

        return bonus

    def _recency_bonus(
        self,
        item: MemoryItem,
        current_step: int,
        weight: float,
    ) -> float:
        """Bonus for recency diversity (not just the most recent)."""
        # This creates a U-shaped curve: prefer neither too old nor too new
        delta = abs(current_step - item.step)
        max_delta = max(current_step, 1)

        normalized = delta / max_delta

        # U-shape: prefer middle-aged items for diversity
        # 0 = very recent, 1 = very old
        bonus = (1.0 - abs(0.5 - normalized) * 2) * weight * 0.2

        return max(0.0, bonus)


def create_reranker(
    strategy: str = "adaptive",
    lambda_: float = 0.5,
) -> MMRReranker | AdaptiveDiversityReranker:
    """
    Factory function to create a reranker.

    Args:
        strategy: Reranking strategy ("mmr" or "adaptive")
        lambda_: Trade-off parameter for MMR

    Returns:
        Reranker instance
    """
    if strategy == "mmr":
        return MMRReranker(lambda_=lambda_)
    elif strategy == "adaptive":
        return AdaptiveDiversityReranker()
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
