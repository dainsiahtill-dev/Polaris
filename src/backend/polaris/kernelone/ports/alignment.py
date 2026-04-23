"""IAlignmentService - Port interface for value alignment.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts, Cells provide implementations.

This port abstracts value alignment services, which contain Polaris-specific
governance logic that should not leak into KernelOne core.

Note: The interface defines an `evaluate` method that returns a result object
with `overall_score`, `conflicts`, and `final_verdict` attributes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.values.alignment_service import ValueAlignmentResult


class IAlignmentService(ABC):
    """Abstract interface for value alignment operations.

    Implementations are provided by Cells to maintain the KernelOne → Cells
    dependency direction while keeping Polaris-specific governance logic
    out of KernelOne core.

    Example:
        # KernelOne usage (abstract)
        from polaris.kernelone.ports import IAlignmentService

        async def check_action(service: IAlignmentService, action: str) -> float:
            result = await service.evaluate(action=action, user_intent="test")
            return result.overall_score

        # Cells provides concrete implementation
        # See: polaris.cells.adapters.kernelone.alignment_adapter
    """

    @abstractmethod
    async def evaluate(
        self,
        action: str,
        context: str = "",
        user_intent: str = "",
    ) -> ValueAlignmentResult:
        """Evaluate an action against the value alignment matrix.

        Args:
            action: The action/command to evaluate.
            context: Additional context for the evaluation.
            user_intent: The user's intent behind the action.

        Returns:
            ValueAlignmentResult with overall_score, conflicts, and final_verdict.
        """
        ...

    @abstractmethod
    async def is_action_aligned(self, action: str, **kwargs: Any) -> bool:
        """Check if an action is aligned with governance values.

        Args:
            action: The action to check.
            **kwargs: Additional context parameters.

        Returns:
            True if the action is aligned, False otherwise.
        """
        ...

    @abstractmethod
    async def get_alignment_score(self, action: str, **kwargs: Any) -> float:
        """Get an alignment score for an action.

        Args:
            action: The action to score.
            **kwargs: Additional context parameters.

        Returns:
            Alignment score between 0.0 (misaligned) and 1.0 (fully aligned).
        """
        ...

    @abstractmethod
    async def explain_misalignment(self, action: str, **kwargs: Any) -> str:
        """Explain why an action is misaligned.

        Args:
            action: The action to explain.
            **kwargs: Additional context parameters.

        Returns:
            Human-readable explanation of misalignment.
        """
        ...
