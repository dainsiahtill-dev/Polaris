"""Reason Code Generator: generates human-readable reason codes for attention decisions.

This module implements the explainability component of ContextOS 3.0 Phase 3.
Every attention decision must have a reason code that explains WHY.

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Reason codes make attention decisions transparent and auditable.
"""

from __future__ import annotations

import logging

from polaris.kernelone.context.context_os.decision_log import AttentionScore, ReasonCode
from polaris.kernelone.context.context_os.phase_detection import TaskPhase

logger = logging.getLogger(__name__)


class ReasonCodeGenerator:
    """Generates reason codes for attention decisions.

    This class makes attention decisions transparent by providing
    machine-readable reason codes for every decision.
    """

    # Reason code descriptions for human readability
    REASON_DESCRIPTIONS: dict[ReasonCode, str] = {
        ReasonCode.MATCHES_CURRENT_GOAL: "Content matches current task goal",
        ReasonCode.REFERENCED_BY_CONTRACT: "Content referenced by contract (goal, acceptance criteria)",
        ReasonCode.REFERENCED_BY_ACCEPTANCE_CRITERIA: "Content matches acceptance criteria",
        ReasonCode.RECENT_TOOL_OUTPUT: "Recent tool output (high evidence weight)",
        ReasonCode.PINNED_BY_USER: "Explicitly pinned by user",
        ReasonCode.PINNED_BY_SYSTEM: "Pinned by system (phase affinity)",
        ReasonCode.FORCED_RECENT: "Forced inclusion as recent message",
        ReasonCode.OPEN_LOOP_REFERENCE: "Referenced by open loop",
        ReasonCode.DELIVERABLE_REFERENCE: "Referenced by deliverable",
        ReasonCode.ACTIVE_ARTIFACT: "Active artifact",
        ReasonCode.LOW_ATTENTION_SCORE: "Low attention score (below threshold)",
        ReasonCode.TOKEN_BUDGET_EXCEEDED: "Token budget exceeded",
        ReasonCode.ROUTE_CLEARED: "Route cleared (excluded by routing)",
        ReasonCode.NOT_IN_ACTIVE_WINDOW: "Not in active window",
        ReasonCode.SUPERSEDED_BY_NEWER: "Superseded by newer content",
        ReasonCode.JIT_SEMANTIC_COMPRESSION: "Just-in-time semantic compression applied",
        ReasonCode.BRUTE_FORCE_TRUNCATION: "Brute-force truncation applied",
        ReasonCode.BUDGET_PRESSURE: "Budget pressure (pre-emptive compression)",
        ReasonCode.PHASE_AFFINITY_LOW: "Low phase affinity (content type doesn't match phase)",
        ReasonCode.PHASE_DETECTED: "Phase detected from signals",
        ReasonCode.PHASE_TRANSITION: "Phase transition occurred",
        ReasonCode.PHASE_HYSTERESIS: "Phase hysteresis applied (kept current phase)",
    }

    def generate_reason_codes(
        self,
        score: AttentionScore,
        phase: TaskPhase,
        is_root: bool = False,
        is_forced_recent: bool = False,
        is_active_artifact: bool = False,
        is_open_loop: bool = False,
        is_deliverable: bool = False,
        is_user_pinned: bool = False,
    ) -> tuple[ReasonCode, ...]:
        """Generate reason codes based on score and context.

        Args:
            score: Attention score
            phase: Current task phase
            is_root: Is this a root event?
            is_forced_recent: Is this a forced recent event?
            is_active_artifact: Is this an active artifact?
            is_open_loop: Is this referenced by an open loop?
            is_deliverable: Is this referenced by a deliverable?
            is_user_pinned: Is this pinned by user?

        Returns:
            Tuple of ReasonCode
        """
        reasons: list[ReasonCode] = []

        # Forced recent
        if is_forced_recent:
            reasons.append(ReasonCode.FORCED_RECENT)

        # User pin
        if is_user_pinned:
            reasons.append(ReasonCode.PINNED_BY_USER)

        # Active artifact
        if is_active_artifact:
            reasons.append(ReasonCode.ACTIVE_ARTIFACT)

        # Open loop reference
        if is_open_loop:
            reasons.append(ReasonCode.OPEN_LOOP_REFERENCE)

        # Deliverable reference
        if is_deliverable:
            reasons.append(ReasonCode.DELIVERABLE_REFERENCE)

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

        # Root event (not already covered)
        if is_root and not any(
            r in reasons
            for r in (
                ReasonCode.FORCED_RECENT,
                ReasonCode.ACTIVE_ARTIFACT,
                ReasonCode.OPEN_LOOP_REFERENCE,
                ReasonCode.DELIVERABLE_REFERENCE,
            )
        ):
            reasons.append(ReasonCode.PINNED_BY_SYSTEM)

        # Default if no specific reason
        if not reasons:
            reasons.append(ReasonCode.NOT_IN_ACTIVE_WINDOW)

        return tuple(reasons)

    def generate_exclusion_reason(
        self,
        score: AttentionScore,
        phase: TaskPhase,
        token_budget_exceeded: bool = False,
        route_cleared: bool = False,
        superseded: bool = False,
    ) -> tuple[ReasonCode, str]:
        """Generate reason code for exclusion.

        Args:
            score: Attention score
            phase: Current task phase
            token_budget_exceeded: Is token budget exceeded?
            route_cleared: Is route cleared?
            superseded: Is content superseded?

        Returns:
            Tuple of (ReasonCode, human-readable explanation)
        """
        if route_cleared:
            return (
                ReasonCode.ROUTE_CLEARED,
                self.REASON_DESCRIPTIONS[ReasonCode.ROUTE_CLEARED],
            )

        if superseded:
            return (
                ReasonCode.SUPERSEDED_BY_NEWER,
                self.REASON_DESCRIPTIONS[ReasonCode.SUPERSEDED_BY_NEWER],
            )

        if token_budget_exceeded:
            return (
                ReasonCode.TOKEN_BUDGET_EXCEEDED,
                self.REASON_DESCRIPTIONS[ReasonCode.TOKEN_BUDGET_EXCEEDED],
            )

        # Low attention score
        if score.final_score < 0.3:
            return (
                ReasonCode.LOW_ATTENTION_SCORE,
                f"Low attention score ({score.final_score:.2f}), "
                f"semantic={score.semantic_similarity:.2f}, "
                f"contract={score.contract_overlap:.2f}",
            )

        # Low phase affinity
        if score.phase_affinity < 0.3:
            return (
                ReasonCode.PHASE_AFFINITY_LOW,
                f"Low phase affinity ({score.phase_affinity:.2f}) for phase {phase.value}",
            )

        # Default
        return (
            ReasonCode.LOW_ATTENTION_SCORE,
            self.REASON_DESCRIPTIONS[ReasonCode.LOW_ATTENTION_SCORE],
        )

    def get_reason_description(self, reason_code: ReasonCode) -> str:
        """Get human-readable description for a reason code."""
        return self.REASON_DESCRIPTIONS.get(reason_code, reason_code.value)
