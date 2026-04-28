"""Candidate Ranker: ranks context candidates by attention score.

This module implements the ranking logic for ContextOS 3.0 Phase 3.
Candidates are ranked by their attention scores and selected until
token budget is exhausted.

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Ranking influences selection, not contract protection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.context.context_os.decision_log import AttentionScore, ReasonCode

from .scorer import AttentionScorer, ScoringContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """A candidate with its attention score and ranking metadata."""

    candidate: Any  # TranscriptEvent or similar
    score: AttentionScore
    rank: int
    selected: bool
    reason_codes: tuple[ReasonCode, ...] = ()
    token_cost: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": str(getattr(self.candidate, "event_id", "")),
            "score": self.score.to_dict(),
            "rank": self.rank,
            "selected": self.selected,
            "reason_codes": [rc.value for rc in self.reason_codes],
            "token_cost": self.token_cost,
        }


class CandidateRanker:
    """Ranks context candidates by attention score.

    V1 Implementation: Multi-signal weighted scoring.
    V2 (Future): Add graph propagation (PageRank-style).

    Usage:
        ranker = CandidateRanker()
        ranked = ranker.rank_candidates(
            candidates=transcript_events,
            context=scoring_context,
            token_budget=10000,
        )
    """

    def __init__(self, scorer: AttentionScorer | None = None) -> None:
        self._scorer = scorer or AttentionScorer()

    def rank_candidates(
        self,
        candidates: tuple[Any, ...],
        context: ScoringContext,
        token_budget: int,
        min_recent: int = 3,
    ) -> tuple[RankedCandidate, ...]:
        """Rank candidates by attention score and select until budget exhausted.

        Args:
            candidates: Tuple of TranscriptEvent or similar
            context: Scoring context
            token_budget: Maximum tokens for selected candidates
            min_recent: Minimum number of recent candidates to always include

        Returns:
            Tuple of RankedCandidate (all candidates, with selected flag)
        """
        if not candidates:
            return ()

        # Score all candidates
        scored: list[tuple[AttentionScore, Any, int]] = []
        for candidate in candidates:
            score = self._scorer.score_candidate(candidate, context)
            token_cost = self._estimate_tokens(str(getattr(candidate, "content", "") or ""))
            scored.append((score, candidate, token_cost))

        # Sort by score (descending)
        scored.sort(key=lambda x: x[0].final_score, reverse=True)

        # Select candidates until budget exhausted
        selected_ids: set[str] = set()
        token_count = 0
        ranked: list[RankedCandidate] = []

        # First pass: always include min_recent most recent candidates
        recent_candidates = sorted(
            scored,
            key=lambda x: int(getattr(x[1], "sequence", 0)),
            reverse=True,
        )[:min_recent]

        for score, candidate, token_cost in recent_candidates:
            candidate_id = str(getattr(candidate, "event_id", ""))
            if candidate_id not in selected_ids:
                selected_ids.add(candidate_id)
                token_count += token_cost
                ranked.append(
                    RankedCandidate(
                        candidate=candidate,
                        score=score,
                        rank=0,  # Will be set later
                        selected=True,
                        reason_codes=(ReasonCode.FORCED_RECENT,),
                        token_cost=token_cost,
                    )
                )

        # Second pass: select remaining by score until budget
        for score, candidate, token_cost in scored:
            candidate_id = str(getattr(candidate, "event_id", ""))
            if candidate_id in selected_ids:
                continue  # Already selected as recent

            if token_count + token_cost <= token_budget:
                selected_ids.add(candidate_id)
                token_count += token_cost
                ranked.append(
                    RankedCandidate(
                        candidate=candidate,
                        score=score,
                        rank=0,  # Will be set later
                        selected=True,
                        reason_codes=self._determine_reason_codes(score, context),
                        token_cost=token_cost,
                    )
                )
            else:
                # Over budget - add but not selected
                ranked.append(
                    RankedCandidate(
                        candidate=candidate,
                        score=score,
                        rank=0,  # Will be set later
                        selected=False,
                        reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
                        token_cost=token_cost,
                    )
                )

        # Sort by score and assign ranks
        ranked.sort(key=lambda x: x.score.final_score, reverse=True)
        result = tuple(
            RankedCandidate(
                candidate=r.candidate,
                score=r.score,
                rank=i + 1,
                selected=r.selected,
                reason_codes=r.reason_codes,
                token_cost=r.token_cost,
            )
            for i, r in enumerate(ranked)
        )

        logger.debug(
            "Ranked %d candidates: %d selected, %d excluded, token_budget=%d, token_used=%d",
            len(result),
            sum(1 for r in result if r.selected),
            sum(1 for r in result if not r.selected),
            token_budget,
            token_count,
        )

        return result

    def _determine_reason_codes(
        self,
        score: AttentionScore,
        context: ScoringContext,
    ) -> tuple[ReasonCode, ...]:
        """Determine reason codes based on score components."""
        reasons: list[ReasonCode] = []

        # High semantic similarity
        if score.semantic_similarity > 0.5:
            reasons.append(ReasonCode.MATCHES_CURRENT_GOAL)

        # High contract overlap
        if score.contract_overlap > 0.5:
            reasons.append(ReasonCode.REFERENCED_BY_CONTRACT)

        # High evidence weight
        if score.evidence_weight > 0.5:
            reasons.append(ReasonCode.RECENT_TOOL_OUTPUT)

        # High phase affinity
        if score.phase_affinity > 0.7:
            reasons.append(ReasonCode.PINNED_BY_SYSTEM)

        # User pin
        if score.user_pin_boost > 0.5:
            reasons.append(ReasonCode.PINNED_BY_USER)

        # Default if no specific reason
        if not reasons:
            reasons.append(ReasonCode.NOT_IN_ACTIVE_WINDOW)

        return tuple(reasons)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count for text."""
        if not text:
            return 0
        # Simple heuristic: ASCII chars / 4, CJK chars * 1.5
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        cjk_chars = len(text) - ascii_chars
        return max(1, int(ascii_chars / 4 + cjk_chars * 1.5))
