"""Routing context and intent inference for role-based routing."""

from polaris.kernelone.role.routing.context import (
    IntentInferenceResult,
    RoutingContext,
    UserPreference,
)
from polaris.kernelone.role.routing.semantic.inferrer import SemanticIntentInferer

__all__ = [
    "IntentInferenceResult",
    "RoutingContext",
    "SemanticIntentInferer",
    "UserPreference",
]
