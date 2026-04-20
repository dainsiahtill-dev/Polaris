"""Intent Inference - Surface → Deep → Unstated Needs."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.cognitive.perception.models import IntentChain, IntentNode


class IntentInferenceEngine:
    """
    Implements the Intent Understanding Protocol:
    Surface Intent → Deep Intent → Unstated Needs → Uncertainty Quantification
    """

    # Context keywords that suggest deeper intents
    DEEP_INTENT_HINTS = {
        "create_file": ["new", "implement", "build", "add"],
        "modify_file": ["update", "improve", "fix", "change"],
        "read_file": ["understand", "analyze", "review", "check"],
        "delete_file": ["cleanup", "remove", "refactor", "deprecate"],
    }

    UNSTATED_NEEDS_KEYWORDS = {
        "create_file": ["test", "documentation", "backup", "review"],
        "modify_file": ["backup", "test", "review", "rollback"],
        "explain": ["example", "use case", "alternative", "trade-off"],
        "plan": ["timeline", "resources", "dependencies", "risks"],
    }

    def infer_deep_intent(
        self,
        surface_intent: IntentNode,
        context: Any = None,
    ) -> IntentNode | None:
        """Infer the deep intent behind surface intent."""
        if surface_intent.confidence < 0.7:
            return None

        hints = self.DEEP_INTENT_HINTS.get(surface_intent.intent_type, [])
        content_lower = surface_intent.content.lower()

        for hint in hints:
            if hint in content_lower:
                return IntentNode(
                    node_id=f"deep_{surface_intent.node_id}",
                    intent_type="deep",
                    content=f"Inferred goal: {hint}",
                    confidence=surface_intent.confidence * 0.85,
                    source_event_id="intent_inference",
                    uncertainty_factors=("inferred_from_surface",),
                )

        return None

    def detect_unstated_needs(
        self,
        surface_intent: IntentNode,
        context: Any = None,
    ) -> tuple[IntentNode, ...]:
        """Detect unstated needs behind the request."""
        needs = []
        intent_type = surface_intent.intent_type

        for keyword in self.UNSTATED_NEEDS_KEYWORDS.get(intent_type, []):
            content_lower = surface_intent.content.lower()
            if keyword not in content_lower:
                need = IntentNode(
                    node_id=f"need_{keyword}_{surface_intent.node_id}",
                    intent_type="unstated",
                    content=f"Implicit need: {keyword}",
                    confidence=0.5,
                    source_event_id="unstated_detection",
                )
                needs.append(need)

        return tuple(needs)

    def build_intent_chain(
        self,
        surface_intent: IntentNode,
        deep_intent: IntentNode | None,
        unstated_needs: tuple[IntentNode, ...],
    ) -> IntentChain:
        """Build complete intent chain."""
        all_nodes = [surface_intent]
        if deep_intent:
            all_nodes.append(deep_intent)
        all_nodes.extend(unstated_needs)

        avg_confidence = sum(n.confidence for n in all_nodes) / len(all_nodes)

        if avg_confidence >= 0.8:
            level = "high"
        elif avg_confidence >= 0.6:
            level = "medium"
        elif avg_confidence >= 0.4:
            level = "low"
        else:
            level = "unknown"

        return IntentChain(
            chain_id=f"chain_{surface_intent.node_id}",
            surface_intent=surface_intent,
            deep_intent=deep_intent,
            unstated_needs=unstated_needs,
            uncertainty=1.0 - avg_confidence,
            confidence_level=level,
        )
