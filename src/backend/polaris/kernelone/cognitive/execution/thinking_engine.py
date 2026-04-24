"""Thinking Phase Engine - Slow, exploratory cognition."""

from __future__ import annotations

from dataclasses import dataclass

from polaris.kernelone.cognitive.perception.models import IntentGraph
from polaris.kernelone.cognitive.reasoning.meta_cognition import MetaCognitionSnapshot
from polaris.kernelone.cognitive.reasoning.models import ReasoningChain
from polaris.kernelone.cognitive.types import ClarityLevel, ExecutionRecommendation, ThinkingOutput


@dataclass(frozen=True)
class ThinkingPhaseConfig:
    """Configuration for thinking phase."""

    max_thinking_time_seconds: float = 30.0
    enable_devils_advocate: bool = True
    enable_uncertainty_quantification: bool = True
    min_confidence_for_action: float = 0.6


class ThinkingPhaseEngine:
    """
    Implements the THINKING phase of Cognitive Life Form.

    THINKING PHASE characteristics:
    - Can be slow, hesitant, exploratory
    - No constraints on time
    - Explores alternatives and uncertainties
    - Outputs reasoning trace for transparency
    """

    def __init__(self, config: ThinkingPhaseConfig | None = None) -> None:
        self._config = config or ThinkingPhaseConfig()
        self._thinking_history: list[ThinkingOutput] = []

    async def run_thinking_phase(
        self,
        intent_graph: IntentGraph,
        execution_recommendation: ExecutionRecommendation,
        reasoning_chain: ReasoningChain | None = None,
        meta_cognition: MetaCognitionSnapshot | None = None,
    ) -> ThinkingOutput:
        """
        Run the thinking phase and produce thinking output.

        The thinking phase:
        1. Explores the problem space
        2. Identifies assumptions and uncertainties
        3. Considers alternatives
        4. Assesses confidence
        5. Determines clarity level
        """
        # Start with intent content
        content_parts = []
        content_parts.append(f"Intent: {intent_graph.nodes[0].content if intent_graph.nodes else 'unknown'}")

        assumptions = []
        uncertainty_factors = []
        reasoning_chain_steps = []

        # Incorporate reasoning chain analysis
        if reasoning_chain:
            content_parts.append(f"Reasoning confidence: {reasoning_chain.confidence_level}")
            content_parts.append(f"Should proceed: {reasoning_chain.should_proceed}")

            if reasoning_chain.blockers:
                content_parts.append(f"Blockers: {', '.join(reasoning_chain.blockers)}")

            # Extract assumptions
            for assumption in reasoning_chain.six_questions.assumptions:
                assumptions.append(assumption.text if hasattr(assumption, "text") else str(assumption))

            # Extract reasoning steps
            reasoning_chain_steps.append(f"Probability: {reasoning_chain.six_questions.conclusion_probability}")
            reasoning_chain_steps.append(f"Knowledge status: {reasoning_chain.six_questions.knowledge_status}")

            if reasoning_chain.six_questions.verification_steps:
                reasoning_chain_steps.append(
                    f"Verification: {', '.join(reasoning_chain.six_questions.verification_steps)}"
                )

        # Incorporate meta-cognition
        if meta_cognition:
            content_parts.append(f"Knowledge boundary confidence: {meta_cognition.knowledge_boundary_confidence}")

            for gap in meta_cognition.knowledge_gaps:
                uncertainty_factors.append(f"Knowledge gap: {gap}")

            for unc in meta_cognition.uncertainty_sources:
                uncertainty_factors.append(f"Uncertainty source: {unc}")

        # Determine clarity level based on confidence
        confidence = execution_recommendation.confidence

        if confidence >= 0.9:
            clarity = ClarityLevel.FULL_TRANSPARENT
        elif confidence >= 0.75:
            clarity = ClarityLevel.ACTION_ORIENTED
        elif confidence >= 0.6:
            clarity = ClarityLevel.CERTAIN
        elif confidence >= 0.4:
            clarity = ClarityLevel.TENDENCY
        else:
            clarity = ClarityLevel.FUZZY

        # Build final content
        content_parts.insert(0, "=== THINKING PHASE ===")
        content_parts.append("=== END THINKING PHASE ===")

        thinking_output = ThinkingOutput(
            content="\n".join(content_parts),
            confidence=confidence,
            clarity_level=clarity,
            assumptions=tuple(assumptions),
            uncertainty_factors=tuple(uncertainty_factors),
            reasoning_chain=tuple(reasoning_chain_steps),
        )

        self._thinking_history.append(thinking_output)
        return thinking_output

    async def assess_clarity(self, thinking_output: ThinkingOutput) -> ClarityLevel:
        """Assess the clarity level of thinking output."""
        return thinking_output.clarity_level

    def get_thinking_history(self) -> tuple[ThinkingOutput, ...]:
        """Get history of thinking outputs."""
        return tuple(self._thinking_history)
