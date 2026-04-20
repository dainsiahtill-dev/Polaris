"""Cognitive Pipeline - Orchestrates Thinking + Acting phases."""

from __future__ import annotations

from dataclasses import dataclass, field

from polaris.kernelone.cognitive.execution.acting_handler import ActingPhaseHandler
from polaris.kernelone.cognitive.execution.cautious_policy import (
    CautiousExecutionPolicy,
    ExecutionPath,
)
from polaris.kernelone.cognitive.execution.thinking_engine import ThinkingPhaseEngine
from polaris.kernelone.cognitive.perception.models import IntentGraph, UncertaintyAssessment
from polaris.kernelone.cognitive.reasoning.meta_cognition import MetaCognitionSnapshot
from polaris.kernelone.cognitive.reasoning.models import ReasoningChain
from polaris.kernelone.cognitive.types import ActingOutput, ExecutionRecommendation, ThinkingOutput


@dataclass(frozen=True)
class CognitivePipelineResult:
    """Result from complete cognitive pipeline."""

    thinking_output: ThinkingOutput | None
    acting_output: ActingOutput | None
    execution_recommendation: ExecutionRecommendation
    reasoning_chain: ReasoningChain | None
    meta_cognition: MetaCognitionSnapshot | None
    path_taken: ExecutionPath
    blocked: bool
    block_reason: str | None
    # Error context for Workflow decision making
    error_type: str | None = None
    retryable: bool = True
    blocked_tools: tuple[str, ...] = field(default_factory=tuple)


class CognitivePipeline:
    """
    Complete Cognitive Pipeline: Perception → Reasoning → Decision → Thinking → Acting

    This is the main entry point for cognitive execution.
    """

    def __init__(self, workspace: str | None = None) -> None:
        self._policy = CautiousExecutionPolicy()
        self._thinking = ThinkingPhaseEngine()
        self._acting = ActingPhaseHandler(workspace=workspace)

    async def execute(
        self,
        message: str,
        intent_graph: IntentGraph,
        uncertainty: UncertaintyAssessment,
        reasoning_chain: ReasoningChain | None = None,
        meta_cognition: MetaCognitionSnapshot | None = None,
    ) -> CognitivePipelineResult:
        """
        Execute the complete cognitive pipeline.

        Returns:
            CognitivePipelineResult with thinking and acting outputs
        """
        # Step 1: Evaluate execution path (CautiousExecutionPolicy)
        recommendation = await self._policy.evaluate(
            intent_graph=intent_graph,
            reasoning_chain=reasoning_chain,
            uncertainty=uncertainty,
        )

        # Step 2: Handle blocked actions
        if recommendation.blockers and recommendation.path == ExecutionPath.FULL_PIPE:
            return CognitivePipelineResult(
                thinking_output=None,
                acting_output=None,
                execution_recommendation=recommendation,
                reasoning_chain=reasoning_chain,
                meta_cognition=meta_cognition,
                path_taken=recommendation.path,
                blocked=True,
                block_reason=f"Blockers: {recommendation.blockers}",
            )

        # Step 3: BYPASS path - skip thinking, go directly to acting
        if recommendation.path == ExecutionPath.BYPASS:
            acting_output = await self._acting.execute_action(
                action=message,
                execution_recommendation=recommendation,
            )
            return CognitivePipelineResult(
                thinking_output=None,
                acting_output=acting_output,
                execution_recommendation=recommendation,
                reasoning_chain=reasoning_chain,
                meta_cognition=meta_cognition,
                path_taken=ExecutionPath.BYPASS,
                blocked=False,
                block_reason=None,
                error_type=acting_output.error_type,
                retryable=acting_output.retryable,
                blocked_tools=acting_output.blocked_tools,
            )

        # Step 4: THINKING phase
        thinking_output = await self._thinking.run_thinking_phase(
            intent_graph=intent_graph,
            execution_recommendation=recommendation,
            reasoning_chain=reasoning_chain,
            meta_cognition=meta_cognition,
        )

        # Step 5: ACTING phase
        acting_output = await self._acting.execute_action(
            action=message,
            execution_recommendation=recommendation,
        )

        return CognitivePipelineResult(
            thinking_output=thinking_output,
            acting_output=acting_output,
            execution_recommendation=recommendation,
            reasoning_chain=reasoning_chain,
            meta_cognition=meta_cognition,
            path_taken=recommendation.path,
            blocked=False,
            block_reason=None,
            error_type=acting_output.error_type,
            retryable=acting_output.retryable,
            blocked_tools=acting_output.blocked_tools,
        )
