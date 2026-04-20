"""Compatibility facade for anthropomorphic retrieval ranking.

Canonical implementation is hosted in
``polaris.kernelone.memory.retrieval_ranker``.
"""

from __future__ import annotations

from polaris.kernelone.memory.retrieval_ranker import (
    AdaptiveDiversityReranker,
    MMRReranker,
    RankedResult,
    create_reranker,
)

__all__ = [
    "AdaptiveDiversityReranker",
    "MMRReranker",
    "RankedResult",
    "create_reranker",
]
