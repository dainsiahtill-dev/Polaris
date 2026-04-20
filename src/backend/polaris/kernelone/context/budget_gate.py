"""Context Budget Gate — token budget enforcement for KernelOne context assembly.

Architecture:
    Budget resolution priority (highest to lowest):
        1. Resolved model spec context_window (provider/model level)
        2. Role context policy fallback (profile context_policy.max_context_tokens)
        3. Default hard fallback (MIN_BUDGET_TOKENS = 30_000)

Safety margin:
    Effective limit = model_window * safety_margin
    Default safety_margin = 0.85 (85% of model window)

    Note: This safety_margin is an "effective_window_ratio" (0.85 means use 85% of the window).
    It is conceptually different from StateFirstContextOSPolicy.safety_margin_ratio which
    is a reserve percentage (5% = 0.05). Claude Code uses: safety_margin = max(2048, 0.05C).

Design constraints:
    - All text uses UTF-8 encoding.
    - Immutable budget state after construction (ContextBudget is a dataclass snapshot).
    - Thread-safe for async use (no shared mutable state in a single gate instance).
    - All token estimates use the kernelone token estimator when available.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

# Safety defaults
DEFAULT_SAFETY_MARGIN = 0.85
MIN_BUDGET_TOKENS = 30_000  # absolute floor when nothing else is known


@dataclass(frozen=True)
class ContextBudgetUsage:
    """Immutable snapshot of current budget usage state.

    This is distinct from contracts.ContextBudget which defines allocation limits.
    ContextBudgetUsage tracks runtime token consumption for a budget gate.

    Architecture decision (P1-CTX-001 convergence):
        - contracts.ContextBudget: allocation limits (max_tokens, max_chars, cost_class)
        - ContextBudgetUsage: runtime tracking (model_window, safety_margin, current_tokens)
    """

    model_window: int
    safety_margin: float  # 0.0–1.0
    current_tokens: int = 0

    @property
    def effective_limit(self) -> int:
        """Hard ceiling after safety margin."""
        return int(self.model_window * self.safety_margin)

    @property
    def headroom(self) -> int:
        """Tokens still available for new content."""
        return self.effective_limit - self.current_tokens

    @property
    def usage_ratio(self) -> float:
        """How full the budget is (0.0–1.0+)."""
        if self.effective_limit <= 0:
            return 0.0
        return self.current_tokens / self.effective_limit


# Backward compatibility alias - ContextBudget was previously defined here
# Now renamed to ContextBudgetUsage per P1-CTX-001 convergence
ContextBudget = ContextBudgetUsage


@dataclass
class SectionAllocation:
    """Budget section allocation result."""

    section: str
    allocated: int
    actual: int
    compressed: bool


@dataclass
class ContextBudgetGate:
    """Token budget gate for context assembly.

    Usage::

        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.85)
        ok, reason = gate.can_add(estimated_tokens=5000)
        if not ok:
            compaction = gate.suggest_compaction()
        gate.record_usage(5000)
        budget = gate.get_current_budget()

        # Section-based allocation
        allocation = gate.allocate_section("system", 4000)
        breakdown = gate.get_section_breakdown()
    """

    model_window: int
    safety_margin: float = field(default=DEFAULT_SAFETY_MARGIN)
    _current_tokens: int = field(default=0, repr=False)
    _estimator_locator: ServiceLocator | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _section_allocations: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # Ensure lock is always an RLock instance
        if not hasattr(self, "_lock") or not isinstance(self._lock, threading.RLock):
            object.__setattr__(self, "_lock", threading.RLock())

    def __init__(
        self,
        model_window: int,
        safety_margin: float = DEFAULT_SAFETY_MARGIN,
        *,
        initial_tokens: int = 0,
        estimator_locator: ServiceLocator | None = None,
    ) -> None:
        if not isinstance(model_window, int) or model_window <= 0:
            raise ValueError(f"model_window must be a positive int, got {model_window!r}")
        if not (0.0 < safety_margin <= 1.0):
            raise ValueError(f"safety_margin must be in (0.0, 1.0], got {safety_margin!r}")
        object.__setattr__(self, "model_window", model_window)
        object.__setattr__(self, "safety_margin", safety_margin)
        object.__setattr__(self, "_current_tokens", initial_tokens)
        object.__setattr__(self, "_estimator_locator", estimator_locator)
        object.__setattr__(self, "_lock", threading.RLock())
        object.__setattr__(self, "_section_allocations", {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_add(self, estimated_tokens: int) -> tuple[bool, str]:
        """Check if adding estimated_tokens would stay within budget.

        Returns:
            (True, "") if it fits.
            (False, reason) with a human-readable reason if it exceeds.
        """
        if estimated_tokens < 0:
            return False, "estimated_tokens may not be negative"
        budget = self.get_current_budget()
        if budget.headroom >= estimated_tokens:
            return True, ""
        ratio = budget.usage_ratio
        if ratio >= 1.0:
            reason = "Budget exhausted; compact before adding more content."
        else:
            pct = int(ratio * 100)
            headroom = budget.headroom
            reason = (
                f"Adding {estimated_tokens:,} tokens would exceed budget ({pct}% used, {headroom:,} tokens headroom)."
            )
        return False, reason

    def get_current_budget(self) -> ContextBudgetUsage:
        """Return an immutable snapshot of the current budget state.

        Thread-safe: holds lock during read to ensure consistent snapshot.
        """
        with self._lock:
            return ContextBudgetUsage(
                model_window=self.model_window,
                safety_margin=self.safety_margin,
                current_tokens=self._current_tokens,
            )

    def record_usage(self, tokens: int) -> None:
        """Record newly consumed tokens.

        Thread-safe: uses RLock to prevent race conditions in concurrent access.
        """
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        with self._lock:
            object.__setattr__(self, "_current_tokens", self._current_tokens + tokens)

    def reset(self) -> None:
        """Reset current token count to zero (start of a new assembly pass).

        Thread-safe: uses RLock to prevent race conditions in concurrent access.
        """
        with self._lock:
            object.__setattr__(self, "_current_tokens", 0)

    def suggest_compaction(self) -> str:
        """Return a human-readable suggestion for the best compaction strategy."""
        budget = self.get_current_budget()
        ratio = budget.usage_ratio
        if ratio < 0.50:
            return f"Budget healthy. No compaction needed (usage: {int(ratio * 100)}%)."
        if ratio < 0.75:
            return (
                "Budget approaching limit. Consider micro-compaction "
                "(collapse old tool results) or trimming low-importance slices."
            )
        if ratio < 0.90:
            return (
                "Budget critical. Apply sliding-window truncation to recent window "
                "or invoke LLM summarization for the working set."
            )
        return (
            "Budget overflow imminent. Must compact before next LLM call. "
            "Recommended: truncate to safety-margin window + inject continuity summary."
        )

    def allocate_section(
        self,
        section: str,
        allocated: int,
        actual: int,
    ) -> SectionAllocation:
        """Allocate budget for a specific section.

        Args:
            section: Name of the section (e.g., "system", "task", "conversation").
            allocated: Budget allocation (limit) for the section.
            actual: Actual token usage for the section.

        Returns:
            SectionAllocation with allocation details.
        """
        with self._lock:
            self._section_allocations[section] = actual
            return SectionAllocation(
                section=section,
                allocated=allocated,
                actual=actual,
                compressed=actual < allocated,
            )

    def get_section_breakdown(self) -> dict[str, int]:
        """Return token usage per section.

        Returns:
            Dictionary mapping section names to their allocated tokens.
        """
        with self._lock:
            return dict(self._section_allocations)

    def estimate_tokens_for_text(self, text: str) -> int:
        """Estimate token count for a string using the registered estimator or fallback.

        Uses CJK-aware estimation via TokenEstimator.heuristic_estimate:
        - ASCII characters: ~4 chars per token
        - CJK characters: ~2 chars per token
        - Mixed text: weighted average based on CJK ratio
        """
        if not text:
            return 0
        locator = self._estimator_locator
        if locator is not None:
            estimator = getattr(locator, "get_token_estimator", lambda: None)()
            if estimator is not None:
                result = estimator.estimate_messages_tokens([{"role": "user", "content": text}])
                if isinstance(result, int) and result >= 0:
                    return result
        # CJK-aware fallback using TokenEstimator's heuristic
        return TokenEstimator.estimate(text)

    # ------------------------------------------------------------------
    # Factory helpers (budget resolution chain)
    # ------------------------------------------------------------------

    @classmethod
    def from_model_window(cls, window: int, **kwargs: object) -> ContextBudgetGate:
        """Construct a gate directly from a known model context window."""
        return cls(model_window=window, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_provider_spec(
        cls,
        provider_name: str,
        model_name: str,
        **kwargs: object,
    ) -> ContextBudgetGate:
        """Resolve context window from a provider/model spec.

        Resolution order:
            1. provider.get_model_context_window(model_name)
            2. hard-coded table for known models
            3. DEFAULT_FALLBACK_WINDOW
        """
        window = _resolve_model_window_from_spec(provider_name, model_name)
        return cls(model_window=window, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_role_policy(
        cls,
        max_context_tokens: int,
        **kwargs: object,
    ) -> ContextBudgetGate:
        """Construct a gate from a role's context policy token limit.

        If max_context_tokens is 0 or negative, falls back to MIN_BUDGET_TOKENS.
        """
        window = max(max_context_tokens, MIN_BUDGET_TOKENS)
        return cls(model_window=window, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def default_gate(cls, **kwargs: object) -> ContextBudgetGate:
        """Construct a gate with the absolute safe fallback window."""
        return cls(model_window=MIN_BUDGET_TOKENS, **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

# DEPRECATED: This constant is kept for backward compatibility only.
# The fallback mechanism it supported has been removed per SSOT principles.
# Model context windows MUST now be configured in llm_config.json.
# If not configured, ModelCatalog.resolve() will raise ValueError.
DEFAULT_FALLBACK_WINDOW = 0  # Invalid value - should never be used


def _resolve_model_window_from_spec(provider_name: str, model_name: str, workspace: str = ".") -> int:
    """Resolve model window from ModelCatalog (SSOT for model specs).

    Args:
        provider_name: Provider ID (e.g., 'openai', 'anthropic')
        model_name: Model name (e.g., 'gpt-4o', 'claude-3-5-sonnet')
        workspace: Workspace path for ModelCatalog initialization

    Returns:
        Model's max_context_tokens from ModelCatalog

    Raises:
        ValueError: If model context window is not configured in llm_config.json
    """
    from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

    catalog = ModelCatalog(workspace=workspace)
    spec = catalog.resolve(provider_id=provider_name, model=model_name)
    return spec.max_context_tokens


__all__ = [
    "DEFAULT_SAFETY_MARGIN",
    "MIN_BUDGET_TOKENS",
    "ContextBudget",
    "ContextBudgetGate",
    "ContextBudgetUsage",
    "SectionAllocation",
]
