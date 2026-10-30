"""AlignmentServiceAdapter - Implements IAlignmentService using Cells' ValueAlignmentService.

ACGA 2.0 Section 6.3: Cells provide implementations of KernelOne port interfaces.

This adapter bridges between KernelOne's abstract IAlignmentService interface
and Cells' concrete ValueAlignmentService implementation.

Example:
    >>> from polaris.kernelone.ports import IAlignmentService
    >>> from polaris.cells.adapters.kernelone import AlignmentServiceAdapter
    >>>
    >>> adapter: IAlignmentService = AlignmentServiceAdapter()
    >>> result = await adapter.evaluate(action="delete file", user_intent="cleanup")
    >>> result.overall_score
    0.85
    >>> result.final_verdict
    'APPROVED'
"""

from __future__ import annotations

from typing import Any

from polaris.cells.values.alignment_service import (
    ValueAlignmentResult,
    ValueAlignmentService as CellsValueAlignmentService,
)
from polaris.kernelone.ports.alignment import IAlignmentService


class AlignmentServiceAdapter(IAlignmentService):
    """Adapter implementing IAlignmentService using Cells' ValueAlignmentService.

    This adapter maintains the KernelOne → Cells dependency direction by
    implementing the abstract port interface defined in KernelOne while
    delegating to the Cells' concrete implementation.

    The adapter provides three views of alignment:
    1. Full evaluation: `evaluate()` - returns complete ValueAlignmentResult
    2. Boolean check: `is_action_aligned()` - quick alignment check
    3. Score-based: `get_alignment_score()` - returns numeric score
    4. Explanation: `explain_misalignment()` - human-readable explanation
    """

    def __init__(self) -> None:
        """Initialize the adapter with Cells' ValueAlignmentService."""
        self._impl = CellsValueAlignmentService()

    async def evaluate(
        self,
        action: str,
        context: str = "",
        user_intent: str = "",
    ) -> ValueAlignmentResult:
        """Evaluate an action against the value alignment matrix.

        Delegates to Cells' ValueAlignmentService.evaluate() to maintain
        KernelOne purity while using Cells' governance logic.

        Args:
            action: The action/command to evaluate.
            context: Additional context for the evaluation.
            user_intent: The user's intent behind the action.

        Returns:
            ValueAlignmentResult with overall_score, conflicts, and final_verdict.
        """
        return await self._impl.evaluate(
            action=action,
            context=context,
            user_intent=user_intent,
        )

    async def is_action_aligned(self, action: str, **kwargs: Any) -> bool:
        """Check if an action is aligned with governance values.

        Args:
            action: The action to check.
            **kwargs: Additional context parameters (user_intent, context).

        Returns:
            True if the action is aligned, False otherwise.
        """
        result = await self._impl.evaluate(
            action=action,
            context=kwargs.get("context", ""),
            user_intent=kwargs.get("user_intent", ""),
        )
        return result.final_verdict != "REJECTED" and result.overall_score >= 0.6

    async def get_alignment_score(self, action: str, **kwargs: Any) -> float:
        """Get an alignment score for an action.

        Args:
            action: The action to score.
            **kwargs: Additional context parameters (user_intent, context).

        Returns:
            Alignment score between 0.0 (misaligned) and 1.0 (fully aligned).
        """
        result = await self._impl.evaluate(
            action=action,
            context=kwargs.get("context", ""),
            user_intent=kwargs.get("user_intent", ""),
        )
        return result.overall_score

    async def explain_misalignment(self, action: str, **kwargs: Any) -> str:
        """Explain why an action is misaligned.

        Args:
            action: The action to explain.
            **kwargs: Additional context parameters (user_intent, context).

        Returns:
            Human-readable explanation of misalignment.
        """
        result = await self._impl.evaluate(
            action=action,
            context=kwargs.get("context", ""),
            user_intent=kwargs.get("user_intent", ""),
        )

        if result.final_verdict == "APPROVED":
            return "Action is fully aligned with governance values."

        explanations: list[str] = []
        for evaluation in result.evaluations:
            if evaluation.concerns:
                explanations.append(f"{evaluation.dimension.value}: {', '.join(evaluation.concerns)}")

        if result.conflicts:
            explanations.extend(result.conflicts)

        return "; ".join(explanations) if explanations else "Action requires further review."
