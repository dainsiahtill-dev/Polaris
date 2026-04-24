"""Cautious Execution Policy - L0-L4 Risk Classification & Path Selection."""

from __future__ import annotations

from polaris.kernelone.cognitive.perception.models import IntentGraph, UncertaintyAssessment
from polaris.kernelone.cognitive.reasoning.models import ReasoningChain
from polaris.kernelone.cognitive.types import ExecutionPath, ExecutionRecommendation, RiskLevel


class CautiousExecutionPolicy:
    """
    Implements L0-L4 risk classification and execution path selection.

    Path Selection Logic:
    - Risk L0 (readonly): BYPASS
    - Risk L1 (create): FAST_THINK
    - Risk L2 (modify): THINKING
    - Risk L3/L4 (delete/irreversible): FULL_PIPE + rollback + user confirmation

    Uncertainty Override:
    - If UncertaintyQuantifier returns uncertainty > 0.6, force FULL_PIPE
    """

    def __init__(
        self,
        risk_bypass_threshold: float = 0.0,
        risk_fast_think_threshold: float = 1.0,
        uncertainty_full_pipe_threshold: float = 0.6,
    ) -> None:
        self._risk_bypass_threshold = risk_bypass_threshold
        self._risk_fast_think_threshold = risk_fast_think_threshold
        self._uncertainty_threshold = uncertainty_full_pipe_threshold

    async def evaluate(
        self,
        intent_graph: IntentGraph,
        reasoning_chain: ReasoningChain | None = None,
        uncertainty: UncertaintyAssessment | None = None,
    ) -> ExecutionRecommendation:
        """
        Evaluate risk and determine execution path.
        """
        # Step 1: Classify risk level
        risk_level = self._classify_risk(intent_graph)

        # Step 2: Check uncertainty override
        uncertainty_override = False
        if uncertainty and uncertainty.uncertainty_score >= self._uncertainty_threshold:
            uncertainty_override = True

        # Step 3: Determine path
        if risk_level == RiskLevel.L0_READONLY:
            path = ExecutionPath.BYPASS
            skip_cognitive = True
            confidence = 1.0
            rollback_required = False
            user_confirmation = False

        elif risk_level == RiskLevel.L1_CREATE and not uncertainty_override:
            path = ExecutionPath.FAST_THINK
            skip_cognitive = True  # Skip critical thinking
            confidence = 0.85
            rollback_required = False
            user_confirmation = False

        elif risk_level == RiskLevel.L2_MODIFY and not uncertainty_override:
            path = ExecutionPath.THINKING
            skip_cognitive = False
            confidence = 0.7
            rollback_required = True
            user_confirmation = False

        else:
            # L3, L4, or uncertainty override
            path = ExecutionPath.FULL_PIPE
            skip_cognitive = False
            confidence = 0.5
            rollback_required = True
            user_confirmation = risk_level in (RiskLevel.L3_DELETE, RiskLevel.L4_IRREVERSIBLE)

        # Check for blockers from reasoning
        blockers = []
        if reasoning_chain and not reasoning_chain.should_proceed:
            blockers = list(reasoning_chain.blockers)

        return ExecutionRecommendation(
            path=path,
            skip_cognitive_pipe=skip_cognitive,
            confidence=confidence,
            risk_level=risk_level,
            requires_rollback_plan=rollback_required,
            requires_user_confirmation=user_confirmation,
            blockers=tuple(blockers),
            uncertainty_threshold_exceeded=uncertainty_override,
        )

    def _classify_risk(self, intent_graph: IntentGraph) -> RiskLevel:
        """Classify risk level based on intent graph."""
        if not intent_graph.nodes:
            return RiskLevel.L2_MODIFY  # Default to moderate risk

        primary_intent = intent_graph.nodes[0]
        intent_type = primary_intent.intent_type

        risk_map = {
            "read_file": RiskLevel.L0_READONLY,
            "explain": RiskLevel.L0_READONLY,
            "search": RiskLevel.L0_READONLY,
            "create_file": RiskLevel.L1_CREATE,
            "modify_file": RiskLevel.L2_MODIFY,
            "delete_file": RiskLevel.L3_DELETE,
            "unknown": RiskLevel.L2_MODIFY,
        }

        return risk_map.get(intent_type, RiskLevel.L2_MODIFY)
