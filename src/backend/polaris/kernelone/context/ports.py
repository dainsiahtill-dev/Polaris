"""Context ports - interfaces for context module dependencies.

This module defines port interfaces that allow the context module to
interact with LLM capabilities without creating circular dependencies.

Architecture:
    - LLM modules depend on these interfaces (not implementations)
    - Concrete implementations are injected at runtime via DI
    - All text uses UTF-8 encoding.

Ports defined:
    - LLMTokenBudgetPort: Interface for LLM token budget allocation
    - ReasoningStripperPort: Interface for reasoning content stripping

Usage::

    from polaris.kernelone.context.ports import LLMTokenBudgetPort, ReasoningStripperPort

    # In a service constructor
    def __init__(self, budget_port: LLMTokenBudgetPort | None = None):
        self._budget_port = budget_port or DefaultTokenBudgetPort()

    # In a component that needs reasoning stripping
    def __init__(self, stripper_port: ReasoningStripperPort | None = None):
        self._stripper = stripper_port or DefaultReasoningStripperPort()
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMTokenBudgetPort(Protocol):
    """Interface for LLM token budget operations.

    This port allows context modules to interact with LLM budget
    capabilities without importing from the llm package directly.
    """

    def allocate(self, tokens: int) -> BudgetAllocation:
        """Allocate budget tokens.

        Args:
            tokens: Number of tokens to allocate.

        Returns:
            BudgetAllocation with allocation details.
        """
        ...

    def release(self, allocation_id: str) -> None:
        """Release a previously allocated budget.

        Args:
            allocation_id: The allocation ID to release.
        """
        ...

    def get_remaining(self) -> int:
        """Get remaining token budget.

        Returns:
            Number of tokens remaining in the budget.
        """
        ...

    def get_total(self) -> int:
        """Get total token budget.

        Returns:
            Total token budget size.
        """
        ...


@dataclass(frozen=True)
class BudgetAllocation:
    """Immutable snapshot of a budget allocation."""

    allocation_id: str
    tokens: int
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging/debugging."""
        return {
            "allocation_id": self.allocation_id,
            "tokens": self.tokens,
            "created_at": self.created_at.isoformat(),
        }


@runtime_checkable
class ReasoningStripperPort(Protocol):
    """Interface for reasoning content stripping.

    This port allows modules to strip reasoning/thinking content
    from text without importing from the llm package directly.
    """

    def strip(self, text: str) -> StripResult:
        """Strip reasoning content from text.

        Args:
            text: Text containing reasoning content.

        Returns:
            StripResult with cleaned text and metadata.
        """
        ...

    def strip_from_history(
        self,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Strip reasoning content from a conversation history.

        Args:
            history: List of history entry dicts.

        Returns:
            History with reasoning content stripped.
        """
        ...


@dataclass(frozen=True)
class StripResult:
    """Result of stripping reasoning content."""

    cleaned_text: str
    removed_blocks: int
    removed_content: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging/debugging."""
        return {
            "cleaned_text_length": len(self.cleaned_text),
            "removed_blocks": self.removed_blocks,
            "removed_content_length": len(self.removed_content),
        }


# ----------------------------------------------------------------------
# Default implementations (fallback when no DI container is available)
# ----------------------------------------------------------------------


class DefaultTokenBudgetPort:
    """Default implementation of LLMTokenBudgetPort.

    This is a no-op implementation that provides unlimited budget.
    Use when no real budget management is needed or available.
    """

    _next_id: int = 0
    _allocations: dict[str, BudgetAllocation] = {}
    _total: int = 200_000  # Conservative default: 200k tokens

    def allocate(self, tokens: int) -> BudgetAllocation:
        """Allocate budget tokens (no-op implementation)."""
        DefaultTokenBudgetPort._next_id += 1
        allocation = BudgetAllocation(
            allocation_id=f"default_{DefaultTokenBudgetPort._next_id}",
            tokens=tokens,
            created_at=datetime.now(),
        )
        DefaultTokenBudgetPort._allocations[allocation.allocation_id] = allocation
        return allocation

    def release(self, allocation_id: str) -> None:
        """Release a budget allocation (no-op implementation)."""
        DefaultTokenBudgetPort._allocations.pop(allocation_id, None)

    def get_remaining(self) -> int:
        """Get remaining budget (returns total as no-op)."""
        return self._total

    def get_total(self) -> int:
        """Get total budget."""
        return self._total


class DefaultReasoningStripperPort:
    """Default implementation of ReasoningStripperPort.

    This implementation uses the actual ReasoningStripper from llm.reasoning.
    It's provided as a reference implementation that can be replaced via DI.
    """

    def __init__(self) -> None:
        self._stripper = self._load_stripper()

    def _load_stripper(self) -> Any:
        """Lazy load the actual ReasoningStripper from llm.reasoning."""
        try:
            from polaris.kernelone.llm.reasoning import ReasoningStripper

            return ReasoningStripper()
        except ImportError:
            # Return a minimal fallback that does nothing
            return _MinimalStripper()

    def strip(self, text: str) -> StripResult:
        """Strip reasoning content using the loaded stripper."""
        result = self._stripper.strip(text)
        return StripResult(
            cleaned_text=result.cleaned_text,
            removed_blocks=result.removed_blocks,
            removed_content=result.removed_content,
        )

    def strip_from_history(
        self,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Strip reasoning content from history using the loaded stripper."""
        return self._stripper.strip_from_history(history)


class _MinimalStripper:
    """Minimal fallback stripper when llm.reasoning is not available."""

    def strip(self, text: str) -> Any:
        """Return text unchanged."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class MinimalResult:
            cleaned_text: str
            removed_blocks: int
            removed_content: str

        return MinimalResult(cleaned_text=text, removed_blocks=0, removed_content="")

    def strip_from_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return history unchanged."""
        return history


@runtime_checkable
class ModelContextProviderPort(Protocol):
    """Interface for resolving model context windows.

    This port allows context modules to resolve model context windows
    without directly importing from the llm package.
    """

    def resolve_context_window(self, provider_id: str, model: str) -> int | None:
        """Resolve context window for a provider/model pair.

        Args:
            provider_id: Provider ID (e.g., 'openai', 'anthropic')
            model: Model name (e.g., 'gpt-4o', 'claude-3-5-sonnet')

        Returns:
            Context window in tokens, or None if not resolvable.
        """
        ...


class DefaultModelContextProvider:
    """Default implementation of ModelContextProviderPort.

    Uses the ModelCatalog to resolve context windows with fallback
    to hard-coded model specifications.
    """

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = workspace
        self._catalog_cache: Any = None

    def _get_catalog(self) -> Any:
        """Lazy load ModelCatalog."""
        if self._catalog_cache is None:
            from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

            self._catalog_cache = ModelCatalog(workspace=self._workspace)
        return self._catalog_cache

    def resolve_context_window(self, provider_id: str, model: str) -> int | None:
        """Resolve context window using ModelCatalog with fallback."""
        try:
            catalog = self._get_catalog()
            spec = catalog.resolve(provider_id=provider_id, model=model)
            if spec.max_context_tokens > 0:
                return spec.max_context_tokens
        except (RuntimeError, ValueError):
            pass

        # Fallback to hard-coded table via budget_gate helper
        try:
            from polaris.kernelone.context.budget_gate import _resolve_model_window_from_spec

            window = _resolve_model_window_from_spec(provider_id, model, self._workspace)
            if window > 0:
                return window
        except (RuntimeError, ValueError):
            pass

        return None


__all__ = [
    "BudgetAllocation",
    "DefaultModelContextProvider",
    "DefaultReasoningStripperPort",
    "DefaultTokenBudgetPort",
    "LLMTokenBudgetPort",
    "ModelContextProviderPort",
    "ReasoningStripperPort",
    "StripResult",
]
