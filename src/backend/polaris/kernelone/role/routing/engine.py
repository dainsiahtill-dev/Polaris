"""Role Routing Engine - Core orchestration engine."""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.role.loaders import (
    AnchorLoader,
    PersonaLoader,
    ProfessionLoader,
    get_anchor_loader,
    get_persona_loader,
    get_profession_loader,
)
from polaris.kernelone.role.routing.cache import RoutingCache
from polaris.kernelone.role.routing.compatibility import CompatibilityEngine, ConflictResolver
from polaris.kernelone.role.routing.context import RoutingContext
from polaris.kernelone.role.routing.preference import PreferenceLearner
from polaris.kernelone.role.routing.result import (
    RoleTriple,
    RoutingInference,
    RoutingManualSpec,
    RoutingResult,
)
from polaris.kernelone.role.routing.rules.loader import RoutingRuleLoader
from polaris.kernelone.role.routing.rules.matcher import RuleMatcher
from polaris.kernelone.role.routing.scoring import ScoringEngine
from polaris.kernelone.role.routing.semantic.inferrer import SemanticIntentInferer

logger = logging.getLogger(__name__)

_DEFAULT_ANCHOR_ID = "polaris_director"
_DEFAULT_PROFESSION_ID = "software_engineer"
_DEFAULT_PERSONA_ID = "gongbu_shilang"


class RoleRoutingEngine:
    """Intelligent role routing engine.

    Intelligently infers optimal Anchor + Profession + Persona combination based on context.
    Supports AUTO/MANUAL/MIXED three routing modes.

    Usage:
        engine = RoleRoutingEngine()
        result = engine.route(context)
    """

    def __init__(
        self,
        workspace: str = "",
        anchor_loader: AnchorLoader | None = None,
        persona_loader: PersonaLoader | None = None,
        profession_loader: ProfessionLoader | None = None,
    ) -> None:
        self._anchor_loader = anchor_loader or get_anchor_loader()
        self._persona_loader = persona_loader or get_persona_loader()
        self._profession_loader = profession_loader or get_profession_loader()

        # Initialize components
        self._rule_loader = RoutingRuleLoader()
        self._rule_matcher = RuleMatcher(self._rule_loader)
        self._compatibility = CompatibilityEngine(self._persona_loader, self._profession_loader)
        self._conflict_resolver = ConflictResolver(self._persona_loader, self._profession_loader)
        self._scoring = ScoringEngine()
        self._cache = RoutingCache()
        self._preference = PreferenceLearner(workspace)
        self._semantic = SemanticIntentInferer()

    def route(
        self,
        context: RoutingContext,
        manual_spec: RoutingManualSpec | None = None,
    ) -> RoutingResult:
        """Route to optimal combination based on context.

        Args:
            context: Routing context
            manual_spec: User-specified routing (MANUAL/MIXED mode)

        Returns:
            RoutingResult containing complete routing decision
        """
        # Step 1: Try cache
        cached = self._cache.get(context)
        if cached and not manual_spec:
            logger.debug("Cache hit for routing context")
            return cached

        # Step 2: Intent inference (if context not provided)
        if not context.task_type or context.task_type == "default":
            inference = self._semantic.infer(context.intent or context.domain)
            context.task_type = inference.task_type
            context.domain = inference.domain
            context.intent = inference.intent

        # Step 3: Rule matching
        matched_rules = self._rule_matcher.match(
            task_type=context.task_type,
            domain=context.domain,
            intent=context.intent,
            session_phase=context.session_phase,
            user_preference=context.user_preference.__dict__ if context.user_preference else None,
        )

        # Step 4: Candidate generation
        candidates = self._generate_candidates(matched_rules, context)

        # Step 5: Compatibility filtering
        candidates = [
            c
            for c in candidates
            if self._compatibility.is_compatible(c.anchor_id, c.profession_id, c.persona_id, context)
        ]

        if not candidates:
            # Fallback: return default combination
            return self._get_default_result(context)

        # Step 6: Score and rank
        scored = [(c, self._scoring.score_candidate(c, context)) for c in candidates]
        scored.sort(key=lambda x: x[1].total_score, reverse=True)

        best_candidate = scored[0][0]
        best_score = scored[0][1]

        # Step 7: MIXED mode conflict resolution
        if manual_spec:
            inferred = RoutingInference(
                anchor_id=best_candidate.anchor_id,
                profession_id=best_candidate.profession_id,
                persona_id=best_candidate.persona_id,
                confidence=best_score.total_score,
            )
            resolved = self._conflict_resolver.resolve(manual_spec, inferred, context)
            best_candidate = RoleTriple(
                anchor_id=resolved.anchor_id,
                profession_id=resolved.profession_id,
                persona_id=resolved.persona_id,
            )
            result = self._build_result(best_candidate, best_score, context, method="mixed")
            result.warnings = resolved.warnings
        else:
            result = self._build_result(best_candidate, best_score, context, method="auto")

        # Step 8: Cache result
        self._cache.set(context, result)

        # Step 9: Record usage
        self._scoring.record_usage(best_candidate)

        return result

    def route_with_fallback(
        self,
        context: RoutingContext,
        max_candidates: int = 3,
    ) -> list[RoutingResult]:
        """Return multiple candidates ranked by score.

        Args:
            context: Routing context
            max_candidates: Maximum number of candidates

        Returns:
            List of RoutingResult sorted by score
        """
        # Similar to route() but returns Top-N
        inference = self._semantic.infer(context.intent or context.domain)
        context.task_type = context.task_type or inference.task_type
        context.domain = context.domain or inference.domain

        matched_rules = self._rule_matcher.match(
            task_type=context.task_type,
            domain=context.domain,
            intent=context.intent,
            session_phase=context.session_phase,
        )

        candidates = self._generate_candidates(matched_rules, context)
        candidates = [
            c
            for c in candidates
            if self._compatibility.is_compatible(c.anchor_id, c.profession_id, c.persona_id, context)
        ]

        if not candidates:
            return [self._get_default_result(context)]

        scored = [(c, self._scoring.score_candidate(c, context)) for c in candidates]
        scored.sort(key=lambda x: x[1].total_score, reverse=True)

        results = []
        for candidate, score in scored[:max_candidates]:
            results.append(self._build_result(candidate, score, context))

        return results

    def learn_preference(
        self,
        session_id: str,
        persona_id: str,
        feedback: float,
    ) -> None:
        """Learn user preference from feedback.

        Args:
            session_id: Session ID
            persona_id: Persona ID used
            feedback: Feedback score (1.0 = fully satisfied, 0.0 = dissatisfied)
        """
        self._preference.record_feedback(session_id, persona_id, feedback)

    def _generate_candidates(
        self,
        matched_rules: list,
        context: RoutingContext,
    ) -> list[RoleTriple]:
        """Generate candidate triples based on matched rules."""
        candidates: list[RoleTriple] = []
        seen: set[str] = set()

        for matched in matched_rules:
            rule = matched.rule
            rec = rule.recommendation

            anchor_id = rec.get("anchor", _DEFAULT_ANCHOR_ID)
            profession_id = rec.get("profession", _DEFAULT_PROFESSION_ID)
            persona_id = rec.get("persona") or (
                self._preference.get_preferred_personas(context.session_id, context)[0]
                if context.session_id
                else _DEFAULT_PERSONA_ID
            )

            key = f"{anchor_id}:{profession_id}:{persona_id}"
            if key not in seen:
                seen.add(key)
                candidates.append(
                    RoleTriple(
                        anchor_id=anchor_id,
                        profession_id=profession_id,
                        persona_id=persona_id,
                    )
                )

        return candidates

    def _build_result(
        self,
        candidate: RoleTriple,
        score: Any,
        context: RoutingContext,
        method: str = "auto",
    ) -> RoutingResult:
        """Build routing result."""
        return RoutingResult(
            anchor_id=candidate.anchor_id,
            profession_id=candidate.profession_id,
            persona_id=candidate.persona_id,
            score=score.total_score,
            match_details=score.details,
            confidence=score.total_score,
            method=method,
        )

    def _get_default_result(self, context: RoutingContext) -> RoutingResult:
        """Return default fallback result."""
        logger.warning("No compatible candidates found, using default")
        return RoutingResult(
            anchor_id=_DEFAULT_ANCHOR_ID,
            profession_id=_DEFAULT_PROFESSION_ID,
            persona_id=_DEFAULT_PERSONA_ID,
            score=0.0,
            match_details={"fallback": True},
            method="fallback",
        )
