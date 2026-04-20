"""Perception Layer - Facade combining all perception components."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.cognitive.perception.context_modeler import ContextModeler
from polaris.kernelone.cognitive.perception.intent_inference import IntentInferenceEngine
from polaris.kernelone.cognitive.perception.models import (
    IntentGraph,
    UncertaintyAssessment,
)
from polaris.kernelone.cognitive.perception.semantic_parser import SemanticParser
from polaris.kernelone.cognitive.perception.uncertainty import UncertaintyQuantifier


class PerceptionLayer:
    """
    Complete Perception Layer implementing Intent Understanding Protocol.

    Usage:
        layer = PerceptionLayer()
        graph, uncertainty = await layer.process(
            "Create a new API endpoint",
            session_id="session_123"
        )
        # graph contains full intent chain
        # uncertainty determines execution path
    """

    def __init__(self) -> None:
        self._parser = SemanticParser()
        self._inference = IntentInferenceEngine()
        self._quantifier = UncertaintyQuantifier()
        self._context_modeler = ContextModeler()

    async def process(
        self,
        message: str,
        working_state: Any = None,
        session_id: str = "default",
    ) -> tuple[IntentGraph, UncertaintyAssessment]:
        """
        Process user message through full perception pipeline.

        Returns:
            (IntentGraph, UncertaintyAssessment)
        """
        # Step 0: Get context enrichment for this session
        context_enrichment = self._context_modeler.get_context_enrichment(session_id)

        # Step 1: Semantic parsing (Surface intent)
        surface_intent, surface_confidence = self._parser.parse(message, working_state)

        # Step 2: Deep intent inference (with context awareness)
        deep_intent = self._inference.infer_deep_intent(surface_intent, working_state)

        # Step 3: Unstated needs detection
        unstated_needs = self._inference.detect_unstated_needs(surface_intent, working_state)

        # Step 4: Build intent chain
        chain = self._inference.build_intent_chain(surface_intent, deep_intent, unstated_needs)

        # Step 5: Uncertainty quantification
        # Adjust uncertainty based on session context
        context_confidence = 0.8 if context_enrichment.get("has_history") else 0.5
        uncertainty = self._quantifier.quantify(
            intent_confidence=surface_confidence,
            context_confidence=context_confidence,
            uncertainty_factors=surface_intent.uncertainty_factors,
            working_state=working_state,
        )

        # Step 6: Build graph
        nodes = [surface_intent]
        if deep_intent:
            nodes.append(deep_intent)
        nodes.extend(unstated_needs)

        now = datetime.now(timezone.utc).isoformat()

        graph = IntentGraph(
            graph_id=f"graph_{abs(hash(message)) % (10**8):08d}",
            nodes=tuple(nodes),
            edges=(),  # Edges added by graph builder
            chains=(chain,),
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )

        # Step 7: Update session context with surface intent
        self._context_modeler.update_context(session_id, surface_intent)

        return graph, uncertainty
