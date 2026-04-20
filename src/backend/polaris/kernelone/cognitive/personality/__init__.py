"""Personality Layer - Cognitive traits and interaction posture."""

from polaris.kernelone.cognitive.personality.expressions import (
    UncertaintyExpression,
    UncertaintyLevel,
    confidence_to_uncertainty_level,
    express_uncertainty,
)
from polaris.kernelone.cognitive.personality.posture import (
    InteractionPosture,
    PostureGuidance,
    select_posture_for_intent,
)
from polaris.kernelone.cognitive.personality.traits import (
    ROLE_TRAIT_PROFILES,
    CognitiveTrait,
    TraitProfile,
    get_trait_profile_for_role,
)

__all__ = [
    "ROLE_TRAIT_PROFILES",
    "CognitiveTrait",
    "InteractionPosture",
    "PostureGuidance",
    "TraitProfile",
    "UncertaintyExpression",
    "UncertaintyLevel",
    "confidence_to_uncertainty_level",
    "express_uncertainty",
    "get_trait_profile_for_role",
    "select_posture_for_intent",
]
