"""Scoring Engine with Dynamic Weighting."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.role.routing.context import RoutingContext
    from polaris.kernelone.role.routing.result import RoleTriple, ScoringResult

logger = logging.getLogger(__name__)


class ScoringEngine:
    """Multi-dimensional scoring engine with context-aware dynamic weighting (v1.1).

    Different task types have different dimension tolerances:
    - security_review: Expertise must dominate (0.60)
    - code_explanation: Persona style matching can be higher (0.35)
    """

    # Critical tasks: Expertise must dominate
    CRITICAL_TASKS = {"security_review", "performance_critical", "architecture_design"}

    # Style priority tasks: Persona matching is more important
    STYLE_PRIORITY_TASKS = {"code_explanation", "casual_chat", "tutorial", "analysis"}

    def __init__(self) -> None:
        self._usage_counts: dict[str, int] = {}

    def score_candidate(
        self,
        candidate: RoleTriple,
        context: RoutingContext,
    ) -> ScoringResult:
        """Score candidate with multi-dimensional evaluation."""

        # Step 1: Get context-aware dynamic weights
        weights = self._get_dynamic_weights(context)

        # Step 2: Calculate dimension scores
        scores = {
            "expertise_match": self._calc_expertise(candidate, context),
            "persona_style_match": self._calc_persona_style(candidate, context),
            "workflow_match": self._calc_workflow_fit(candidate, context),
            "phase_match": self._calc_phase_match(candidate, context),
            "usage_score": self._calc_usage_score(candidate),
        }

        total = sum(scores[k] * weights[k] for k in weights)

        return ScoringResult(
            total_score=total,
            details=scores,
        )

    def _get_dynamic_weights(self, context: RoutingContext) -> dict[str, float]:
        """Context-aware dynamic weight calculation."""
        # Base weights
        weights = {
            "expertise_match": 0.35,
            "persona_style_match": 0.25,
            "workflow_match": 0.20,
            "phase_match": 0.10,
            "usage_score": 0.10,
        }

        # Critical task boost
        if context.task_type in self.CRITICAL_TASKS:
            weights["expertise_match"] = 0.60
            weights["persona_style_match"] = 0.10
            weights["workflow_match"] = 0.15
            weights["phase_match"] = 0.10
            weights["usage_score"] = 0.05

        # Style priority boost
        elif context.task_type in self.STYLE_PRIORITY_TASKS:
            weights["expertise_match"] = 0.20
            weights["persona_style_match"] = 0.35
            weights["workflow_match"] = 0.15
            weights["phase_match"] = 0.15
            weights["usage_score"] = 0.15

        # Phase binding: QA more important in verification phase
        if context.session_phase == "verification" and context.task_type in {
            "code_review",
            "security_review",
            "testing",
        }:
            weights["expertise_match"] += 0.10
            weights["phase_match"] = 0.15

        # User explicit style requirement adjustment
        if context.user_preference.formality == "strict":
            weights["workflow_match"] += 0.10
            weights["persona_style_match"] -= 0.05

        return self._normalize_weights(weights)

    def _normalize_weights(self, weights: dict[str, float]) -> dict[str, float]:
        """Normalize weights to sum to 1.0."""
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}

    def _calc_expertise(self, candidate: RoleTriple, context: RoutingContext) -> float:
        """Calculate expertise match score."""
        # TODO: Integrate profession_loader to get expertise
        # Current implementation: domain-based matching
        expertise_keywords = {
            "python": ["python", "django", "flask", "fastapi"],
            "typescript": ["typescript", "react", "vue", "frontend"],
            "rust": ["rust", "cargo", "systems"],
            "devops": ["docker", "k8s", "ci/cd", "deployment"],
            "security": ["security", "audit", "vulnerability"],
            "data": ["data", "sql", "database"],
            "ml": ["ml", "ai", "model", "tensor"],
        }

        keywords = expertise_keywords.get(context.domain, [])
        # Simple matching: domain vs profession_id overlap
        match_count = sum(1 for kw in keywords if kw in candidate.profession_id.lower())

        if match_count >= 2:
            return 1.0
        elif match_count == 1:
            return 0.7
        else:
            # Check if generic profession
            if candidate.profession_id in ("software_engineer", "generalist"):
                return 0.5
            return 0.3

    def _calc_persona_style(self, candidate: RoleTriple, context: RoutingContext) -> float:
        """Calculate Persona style match score."""
        # TODO: Integrate persona_loader to get style_tags
        # Current implementation: simple user_preference matching
        pref = context.user_preference

        if pref.communication_style == "direct":
            # Direct users prefer concise style
            return 0.8 if candidate.persona_id in ("gongbu_shilang", "default") else 0.6
        elif pref.formality == "casual":
            return 0.7 if candidate.persona_id in ("cyberpunk_hacker", "casual") else 0.5
        else:
            return 0.6

    def _calc_workflow_fit(self, candidate: RoleTriple, context: RoutingContext) -> float:
        """Calculate workflow fit score."""
        # TODO: Based on anchor workflow_mapping
        return 0.7

    def _calc_phase_match(self, candidate: RoleTriple, context: RoutingContext) -> float:
        """Calculate session phase match score (v1.1).

        Polaris system is essentially a state machine:
        - ideation -> blueprint -> execution -> verification
        """
        # QA has higher weight in verification phase
        if context.session_phase == "verification":
            if "qa" in candidate.anchor_id or "quality" in candidate.profession_id:
                return 1.0
            return 0.4
        elif context.session_phase == "execution":
            if "director" in candidate.anchor_id:
                return 0.9
            return 0.6
        elif context.session_phase == "blueprint":
            if "architect" in candidate.profession_id:
                return 0.9
            return 0.6
        else:  # ideation
            return 0.5

    def _calc_usage_score(self, candidate: RoleTriple) -> float:
        """Calculate historical usage score."""
        key = str(candidate)
        count = self._usage_counts.get(key, 0)

        # Cold start is 0, more usage leads to higher reuse tendency
        if count == 0:
            return 0.0
        elif count < 5:
            return 0.3
        elif count < 20:
            return 0.6
        else:
            return 0.9

    def record_usage(self, candidate: RoleTriple) -> None:
        """Record usage for subsequent scoring reference."""
        key = str(candidate)
        self._usage_counts[key] = self._usage_counts.get(key, 0) + 1
