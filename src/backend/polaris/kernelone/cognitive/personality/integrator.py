"""Personality Integrator - Connects personality traits to cognitive pipeline."""

from __future__ import annotations

from polaris.kernelone.cognitive.personality.expressions import (
    confidence_to_uncertainty_level,
    express_uncertainty,
)
from polaris.kernelone.cognitive.personality.posture import InteractionPosture
from polaris.kernelone.cognitive.personality.traits import (
    CognitiveTrait,
    TraitProfile,
    get_trait_profile_for_role,
)


def _has_trait(profile: TraitProfile, trait: CognitiveTrait) -> bool:
    """Check if a trait is enabled in the profile."""
    return trait in profile.enabled_traits


class PersonalityIntegrator:
    """
    Integrates personality traits and postures into cognitive pipeline.

    This ensures that role-specific traits influence how the cognitive
    system processes and responds to information.
    """

    def __init__(self) -> None:
        self._default_role = "director"

    def apply_posture_to_response(
        self,
        response: str,
        posture: InteractionPosture,
        uncertainty_score: float = 0.0,
        confidence: float = 0.5,
    ) -> str:
        """
        Modify response based on selected posture.

        Different postures require different transparency and
        uncertainty expression levels.
        """
        uncertainty_level = confidence_to_uncertainty_level(confidence)

        if posture == InteractionPosture.TRANSPARENT_REASONING:
            # Add reasoning transparency markers
            return f"[思考过程]\n{response}"

        elif posture == InteractionPosture.ADMIT_IGNORANCE:
            # Express uncertainty about what we don't know
            context = {"confidence": confidence, "uncertainty_score": uncertainty_score}
            uncertainty_expr = express_uncertainty(uncertainty_level, context)
            return f"{uncertainty_expr}\n{response}"

        elif posture == InteractionPosture.PROACTIVE_INFERENCE:
            # Make inferences explicit
            if not response.startswith("["):
                return f"[基于上下文推断] {response}"
            return response

        elif posture == InteractionPosture.MANAGE_EXPECTATIONS:
            # Set appropriate expectations
            if "uncertainty" not in response.lower():
                return f"[注意: 仅供参考] {response}"
            return response

        elif posture == InteractionPosture.GUIDE_LEARNING:
            # Frame as learning opportunity
            return f"[探索中] {response}"

        return response

    def get_role_traits(self, role_id: str) -> TraitProfile:
        """Get trait profile for a role."""
        profile = get_trait_profile_for_role(role_id)
        if profile is None:
            # Return default director profile
            profile = get_trait_profile_for_role("director")
            if profile is None:
                raise RuntimeError("Director profile not found")
        return profile

    def should_proceed_based_on_traits(
        self,
        trait_profile: TraitProfile,
        confidence: float,
        risk_level: int,
    ) -> tuple[bool, str]:
        """
        Determine if should proceed based on trait profile.

        Certain traits (CAUTIOUS, CRITICAL) increase threshold for
        proceeding with uncertain or risky actions.
        """
        # Cautious traits raise the bar
        trait_multiplier = 1.0
        if _has_trait(trait_profile, CognitiveTrait.CAUTIOUS):
            trait_multiplier *= 0.9
        if _has_trait(trait_profile, CognitiveTrait.CRITICAL):
            trait_multiplier *= 0.85
        if _has_trait(trait_profile, CognitiveTrait.HUMBLE):
            trait_multiplier *= 0.95

        adjusted_confidence = confidence * trait_multiplier

        # High risk (L3+) needs higher confidence
        if risk_level >= 3:
            threshold = 0.8
        elif risk_level >= 2:
            threshold = 0.6
        else:
            threshold = 0.4

        should_proceed = adjusted_confidence >= threshold

        if not should_proceed:
            reason = f"Confidence {adjusted_confidence:.2f} below threshold {threshold:.2f} for risk level {risk_level}"
        else:
            reason = "Proceed"

        return should_proceed, reason

    def express_based_on_traits(
        self,
        response: str,
        trait_profile: TraitProfile,
        situation: str = "default",
    ) -> str:
        """
        Modify expression style based on trait profile.

        Traits like CURIOUS, CRITICAL, HONEST influence how
        information is presented.
        """
        if _has_trait(trait_profile, CognitiveTrait.CURIOUS) and situation == "learning":
            response = f"[好奇] {response}"

        if _has_trait(trait_profile, CognitiveTrait.CRITICAL):
            response = f"[批判性审视] {response}"

        if _has_trait(trait_profile, CognitiveTrait.HONEST) and ("[确定]" in response or "[肯定]" in response):
            # Ensure we're not overstating confidence
            response = response.replace("[确定]", "[较确定]")
            response = response.replace("[肯定]", "[倾向]")

        if _has_trait(trait_profile, CognitiveTrait.HUMBLE) and not response.startswith("[") and "可能" not in response:
            response = f"[供参考] {response}"

        return response
