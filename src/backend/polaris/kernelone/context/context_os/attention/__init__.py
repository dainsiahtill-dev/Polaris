"""Attention Scoring: multi-signal scoring for context candidates.

This module implements ContextOS 3.0 Phase 3: Attention Scoring V1.
Instead of static rules, every context candidate gets a multi-dimensional
attention score that determines its priority in the projection.

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Attention scores influence ranking, not contract protection.

Scoring Formula (V1):
    attention_score =
      semantic_similarity   * 0.35
    + recency_score         * 0.15
    + contract_overlap      * 0.20
    + evidence_weight       * 0.15
    + phase_affinity        * 0.10
    + user_pin_boost        * 0.05

Phase 2 (Future): Add graph propagation (PageRank-style).
"""

from .ranker import CandidateRanker
from .reason_codes import ReasonCodeGenerator
from .scorer import AttentionScorer

__all__ = [
    "AttentionScorer",
    "CandidateRanker",
    "ReasonCodeGenerator",
]
