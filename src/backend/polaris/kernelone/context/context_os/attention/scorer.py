"""Attention Scorer: multi-signal scoring for context candidates.

This module implements the core attention scoring logic.
Each context candidate receives a multi-dimensional score based on:
- Semantic similarity to current intent
- Recency (time decay)
- Contract overlap (goal, acceptance criteria)
- Evidence weight (is this an evidence event?)
- Phase affinity (does this content type match current phase?)
- User pin boost (explicitly pinned by user)

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Scores influence ranking, not contract protection.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.context.context_os.decision_log import AttentionScore
from polaris.kernelone.context.context_os.phase_detection import TaskPhase

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScoringContext:
    """Context for scoring candidates."""

    # Current intent (from latest user message + goal)
    current_intent: str = ""
    current_goal: str = ""

    # Contract elements
    acceptance_criteria: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()
    current_task_id: str = ""

    # Phase context
    current_phase: TaskPhase = TaskPhase.INTAKE

    # Timestamps for recency scoring
    current_time: float = 0.0
    recent_window_turns: int = 5


class AttentionScorer:
    """Multi-signal attention scorer for context candidates.

    V1 Implementation: Multi-signal weighted scoring.
    V2 (Future): Add graph propagation (PageRank-style).

    Scoring Formula:
        attention_score =
          semantic_similarity   * 0.35
        + recency_score         * 0.15
        + contract_overlap      * 0.20
        + evidence_weight       * 0.15
        + phase_affinity        * 0.10
        + user_pin_boost        * 0.05
    """

    # Scoring weights
    WEIGHT_SEMANTIC = 0.35
    WEIGHT_RECENCY = 0.15
    WEIGHT_CONTRACT = 0.20
    WEIGHT_EVIDENCE = 0.15
    WEIGHT_PHASE = 0.10
    WEIGHT_PIN = 0.05

    # Phase affinity matrix: which content types are important in each phase
    PHASE_AFFINITY: dict[TaskPhase, dict[str, float]] = {
        TaskPhase.INTAKE: {
            "user_turn": 0.9,
            "assistant_turn": 0.5,
            "tool_result": 0.3,
            "system": 0.8,
            "error": 0.2,
        },
        TaskPhase.PLANNING: {
            "user_turn": 0.7,
            "assistant_turn": 0.8,
            "tool_result": 0.4,
            "system": 0.6,
            "error": 0.3,
        },
        TaskPhase.EXPLORATION: {
            "user_turn": 0.5,
            "assistant_turn": 0.6,
            "tool_result": 0.9,
            "system": 0.4,
            "error": 0.5,
        },
        TaskPhase.IMPLEMENTATION: {
            "user_turn": 0.6,
            "assistant_turn": 0.8,
            "tool_result": 0.7,
            "system": 0.5,
            "error": 0.6,
        },
        TaskPhase.VERIFICATION: {
            "user_turn": 0.5,
            "assistant_turn": 0.6,
            "tool_result": 0.9,
            "system": 0.4,
            "error": 0.9,
        },
        TaskPhase.DEBUGGING: {
            "user_turn": 0.4,
            "assistant_turn": 0.5,
            "tool_result": 0.8,
            "system": 0.3,
            "error": 1.0,
        },
        TaskPhase.REVIEW: {
            "user_turn": 0.7,
            "assistant_turn": 0.8,
            "tool_result": 0.6,
            "system": 0.5,
            "error": 0.4,
        },
    }

    def score_candidate(
        self,
        candidate: Any,
        context: ScoringContext,
    ) -> AttentionScore:
        """Score a single context candidate.

        Args:
            candidate: TranscriptEvent or similar with attributes:
                - content: str
                - role: str
                - kind: str
                - sequence: int
                - event_id: str
                - metadata: dict
                - created_at: str (ISO timestamp)
            context: Scoring context with current intent, goal, phase

        Returns:
            AttentionScore with all component scores
        """
        # Extract candidate attributes
        content = str(getattr(candidate, "content", "") or "")
        kind = str(getattr(candidate, "kind", "") or "").lower()
        metadata = dict(getattr(candidate, "metadata", {}) or {})

        # Calculate component scores
        semantic = self._score_semantic_similarity(content, context.current_intent)
        recency = self._score_recency(candidate, context)
        contract = self._score_contract_overlap(content, context)
        evidence = self._score_evidence_weight(kind, metadata)
        phase = self._score_phase_affinity(kind, context.current_phase)
        pin = self._score_user_pin_boost(metadata)

        # Calculate final score
        final = (
            semantic * self.WEIGHT_SEMANTIC
            + recency * self.WEIGHT_RECENCY
            + contract * self.WEIGHT_CONTRACT
            + evidence * self.WEIGHT_EVIDENCE
            + phase * self.WEIGHT_PHASE
            + pin * self.WEIGHT_PIN
        )

        return AttentionScore(
            semantic_similarity=semantic,
            recency_score=recency,
            contract_overlap=contract,
            evidence_weight=evidence,
            phase_affinity=phase,
            user_pin_boost=pin,
            final_score=final,
        )

    def _score_semantic_similarity(self, content: str, intent: str) -> float:
        """Score semantic similarity between content and current intent.

        V1: Simple keyword overlap (no embeddings).
        V2 (Future): Use embedding-based cosine similarity.
        """
        if not content or not intent:
            return 0.0

        # Normalize
        content_lower = content.lower()
        intent_lower = intent.lower()

        # Extract keywords from intent
        intent_words = set(intent_lower.split())
        content_words = set(content_lower.split())

        # Calculate Jaccard similarity
        if not intent_words:
            return 0.0

        intersection = intent_words & content_words
        union = intent_words | content_words

        if not union:
            return 0.0

        return len(intersection) / len(union)

    def _score_recency(self, candidate: Any, context: ScoringContext) -> float:
        """Score recency with exponential decay."""
        if context.current_time <= 0:
            return 0.5  # Default if no time info

        # Get candidate timestamp
        created_at = str(getattr(candidate, "created_at", "") or "")
        if not created_at:
            return 0.3  # Unknown timestamp gets low score

        try:
            # Parse ISO timestamp
            from datetime import datetime

            candidate_time = datetime.fromisoformat(created_at).timestamp()
            age_seconds = context.current_time - candidate_time

            # Exponential decay: score = e^(-lambda * age)
            # lambda chosen so that 1 hour old = 0.5 score
            lambda_decay = math.log(2) / 3600  # Half-life = 1 hour
            return max(0.0, min(1.0, math.exp(-lambda_decay * age_seconds)))
        except (ValueError, TypeError):
            return 0.3  # Parse error gets low score

    def _score_contract_overlap(self, content: str, context: ScoringContext) -> float:
        """Score overlap with contract elements (goal, acceptance criteria)."""
        if not content:
            return 0.0

        content_lower = content.lower()
        score = 0.0
        max_score = 0.0

        # Check goal overlap
        if context.current_goal:
            max_score += 1.0
            goal_lower = context.current_goal.lower()
            if goal_lower in content_lower or any(
                word in content_lower for word in goal_lower.split() if len(word) > 3
            ):
                score += 1.0

        # Check acceptance criteria overlap
        for criterion in context.acceptance_criteria:
            max_score += 0.5
            criterion_lower = criterion.lower()
            if criterion_lower in content_lower:
                score += 0.5

        # Check hard constraints overlap
        for constraint in context.hard_constraints:
            max_score += 0.3
            constraint_lower = constraint.lower()
            if constraint_lower in content_lower:
                score += 0.3

        # Check task ID overlap
        if context.current_task_id:
            max_score += 0.5
            if context.current_task_id in content:
                score += 0.5

        return score / max_score if max_score > 0 else 0.0

    def _score_evidence_weight(self, kind: str, metadata: dict[str, Any]) -> float:
        """Score evidence weight (is this an evidence event?)."""
        score = 0.0

        # Tool results are evidence
        if "tool" in kind:
            score += 0.5

        # Error events are evidence
        if "error" in kind or metadata.get("is_error"):
            score += 0.8

        # Decision events are evidence
        if "decision" in kind:
            score += 0.6

        # Contract events are evidence
        if "contract" in kind or metadata.get("is_contract"):
            score += 0.9

        return min(1.0, score)

    def _score_phase_affinity(self, kind: str, phase: TaskPhase) -> float:
        """Score phase affinity (does this content type match current phase?)."""
        # Normalize kind
        kind_lower = kind.lower()
        if "user" in kind_lower:
            kind_key = "user_turn"
        elif "assistant" in kind_lower:
            kind_key = "assistant_turn"
        elif "tool" in kind_lower:
            kind_key = "tool_result"
        elif "system" in kind_lower:
            kind_key = "system"
        elif "error" in kind_lower:
            kind_key = "error"
        else:
            kind_key = "assistant_turn"  # Default

        phase_matrix = self.PHASE_AFFINITY.get(phase, {})
        return phase_matrix.get(kind_key, 0.5)

    def _score_user_pin_boost(self, metadata: dict[str, Any]) -> float:
        """Score user pin boost (explicitly pinned by user)."""
        if metadata.get("pinned_by_user"):
            return 1.0
        if metadata.get("pinned_by_system"):
            return 0.5
        return 0.0
