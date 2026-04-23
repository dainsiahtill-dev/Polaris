"""Exploration Policy — phase-aware expansion decision engine.

This module owns the logic for deciding:
    1. Whether to expand the working set with a candidate asset.
    2. Which tools to invoke in the current exploration phase.
    3. Whether the working set should trigger compaction.

Architecture role:
    ExplorationPolicy is consumed by the WorkingSetAssembler to gate
    incremental asset ingestion. It is a pure-policy object (no I/O).

Design constraints:
    - Fully async-compatible (all methods are async).
    - Deterministic: same inputs must yield same decisions for a given policy config.
    - UTF-8 text throughout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .budget_gate import ContextBudget

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class ExplorationPhase(Enum):
    """Stages of the exploration loop."""

    MAP = "map"  # Build / refresh repo-map
    SEARCH = "search"  # Search for relevant symbols / files
    SLICE = "slice"  # Read targeted code slices
    EXPAND = "expand"  # Expand to neighbors / related assets
    READ_FULL = "read_full"  # Read complete files when slices are insufficient
    COMPACT = "compact"  # Compaction pass (not a normal expansion phase)


class ExpansionDecision(Enum):
    """Decision for an asset expansion request."""

    APPROVED = "approved"  # Add the asset immediately
    DENIED = "denied"  # Skip the asset
    DEFERRED = "deferred"  # Not now; revisit after current phase completes


class AssetKind(Enum):
    """Classification of explorable asset types."""

    REPO_MAP = "repo_map"
    SYMBOL = "symbol"
    CODE_SLICE = "code_slice"
    NEIGHBOR = "neighbor"
    FULL_FILE = "full_file"


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass(frozen=True)
class AssetCandidate:
    """A candidate asset for potential inclusion in the working set."""

    asset_kind: AssetKind
    file_path: str
    line_range: tuple[int, int] | None = None  # (start, end) 1-indexed inclusive
    estimated_tokens: int = 0
    priority: int = 0  # Higher = more important; ties broken by arrival order
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def asset_type(self) -> str:
        """Stable string label for serialization."""
        return self.asset_kind.value

    @property
    def display_key(self) -> str:
        """Stable key for deduplication in expansion history."""
        if self.line_range:
            return f"{self.file_path}:{self.line_range[0]}-{self.line_range[1]}"
        return self.file_path


@dataclass(frozen=True)
class ExplorationContext:
    """Mutable-ish context passed through a single exploration pass.

    Not frozen because the assembler appends to history lists.
    """

    phase: ExplorationPhase
    workspace: str
    depth: int = 0  # Current expansion depth (0 = initial assets)
    max_depth: int = 3
    seen_assets: frozenset[str] = field(default_factory=frozenset)
    denied_assets: frozenset[str] = field(default_factory=frozenset)
    expansion_history: list[str] = field(default_factory=list)
    phase_tool_calls: int = 0  # Tool invocations in current phase
    total_tool_calls: int = 0  # Total tool invocations across all phases

    def with_approved_asset(self, candidate: AssetCandidate) -> ExplorationContext:
        """Return a new context with the asset recorded as approved."""
        new_history = list(self.expansion_history)
        new_history.append(candidate.display_key)
        new_seen = self.seen_assets | {candidate.display_key}
        return ExplorationContext(
            phase=self.phase,
            workspace=self.workspace,
            depth=self.depth,
            max_depth=self.max_depth,
            seen_assets=new_seen,
            denied_assets=self.denied_assets,
            expansion_history=new_history,
            phase_tool_calls=self.phase_tool_calls + 1,
            total_tool_calls=self.total_tool_calls + 1,
        )

    def with_denied_asset(self, candidate: AssetCandidate) -> ExplorationContext:
        """Return a new context with the asset recorded as denied."""
        new_denied = self.denied_assets | {candidate.display_key}
        return ExplorationContext(
            phase=self.phase,
            workspace=self.workspace,
            depth=self.depth,
            max_depth=self.max_depth,
            seen_assets=self.seen_assets,
            denied_assets=new_denied,
            expansion_history=self.expansion_history,
            phase_tool_calls=self.phase_tool_calls,
            total_tool_calls=self.total_tool_calls,
        )


@dataclass
class ExplorationPolicyConfig:
    """Tunable knobs for the exploration policy."""

    # Depth limits
    max_expansion_depth: int = 3

    # Token budgets per phase
    map_budget_tokens: int = 2_000
    search_budget_tokens: int = 5_000
    slice_budget_tokens: int = 10_000
    expand_budget_tokens: int = 8_000
    read_full_budget_tokens: int = 20_000

    # Priority thresholds
    min_priority_for_auto_approve: int = 5
    min_priority_for_defer: int = 2

    # Safety
    max_tool_calls_per_phase: int = 20
    max_tool_calls_total: int = 60
    deny_revisit_after_denied: bool = True

    # Compaction trigger
    compaction_trigger_ratio: float = 0.80  # Trigger at 80% of effective limit


# ------------------------------------------------------------------
# Selector Policy Protocol
# ------------------------------------------------------------------


class SelectorPolicy(Protocol):
    """Abstract interface for candidate selection strategies.

    Implementations define how candidates are selected from a pool
    based on context and budget constraints.
    """

    def select(
        self,
        candidates: list[AssetCandidate],
        context: ExplorationContext,
        budget: ContextBudget,
    ) -> list[AssetCandidate]:
        """Select candidates for expansion.

        Args:
            candidates: Pool of candidate assets to consider.
            context: Current exploration context.
            budget: Available budget for expansion.

        Returns:
            List of selected candidates in priority order.
        """
        ...

    def should_compact(self, context: ExplorationContext) -> bool:
        """Determine if compaction should be triggered.

        Args:
            context: Current exploration context.

        Returns:
            True if compaction should be triggered.
        """
        ...


# ------------------------------------------------------------------
# Selector Policy Implementations
# ------------------------------------------------------------------


class DefaultSelectorPolicy:
    """Default selection policy with heuristic-based filtering.

    Selection rules:
        - Filter out already seen assets
        - Filter out previously denied assets (if configured)
        - Filter out assets exceeding max depth
        - Filter out assets exceeding budget headroom
        - Sort by priority (highest first)
        - Auto-approve high-priority candidates
    """

    def __init__(self, config: ExplorationPolicyConfig | None = None) -> None:
        self.config = config or ExplorationPolicyConfig()

    def select(
        self,
        candidates: list[AssetCandidate],
        context: ExplorationContext,
        budget: ContextBudget,
    ) -> list[AssetCandidate]:
        """Select candidates using default heuristic rules."""
        selected: list[AssetCandidate] = []

        for candidate in candidates:
            key = candidate.display_key

            # 1. Deduplication
            if key in context.seen_assets:
                _logger.debug("select: %s already seen, skipping", key)
                continue

            # 2. Denied assets check
            if key in context.denied_assets and self.config.deny_revisit_after_denied:
                _logger.debug("select: %s previously denied, skipping", key)
                continue

            # 3. Depth check
            if context.depth >= context.max_depth:
                _logger.debug("select: max depth %d reached, skipping", context.max_depth)
                continue

            # 4. Budget check
            if budget.headroom < candidate.estimated_tokens:
                _logger.debug(
                    "select: %s exceeds budget (headroom=%d < needed=%d), skipping",
                    key,
                    budget.headroom,
                    candidate.estimated_tokens,
                )
                continue

            selected.append(candidate)

        # Sort by priority (highest first)
        selected.sort(key=lambda c: c.priority, reverse=True)

        return selected

    def should_compact(self, context: ExplorationContext) -> bool:
        """Check if compaction should be triggered based on expansion depth."""
        return context.depth >= context.max_depth


class GreedySelectorPolicy:
    """Greedy selection policy prioritizing budget efficiency.

    Selection rules:
        - Select assets with best tokens-to-value ratio
        - Prioritize smaller assets that fit within budget
        - Maximize number of assets within budget constraint
    """

    def __init__(self, config: ExplorationPolicyConfig | None = None) -> None:
        self.config = config or ExplorationPolicyConfig()

    def select(
        self,
        candidates: list[AssetCandidate],
        context: ExplorationContext,
        budget: ContextBudget,
    ) -> list[AssetCandidate]:
        """Select candidates using greedy budget-efficient strategy."""
        # Filter eligible candidates
        eligible: list[AssetCandidate] = []

        for candidate in candidates:
            key = candidate.display_key

            if key in context.seen_assets:
                continue
            if key in context.denied_assets and self.config.deny_revisit_after_denied:
                continue
            if context.depth >= context.max_depth:
                continue
            if budget.headroom < candidate.estimated_tokens:
                continue

            eligible.append(candidate)

        # Sort by estimated_tokens ascending (smallest first) for greedy selection
        # Break ties by priority (higher priority first)
        eligible.sort(key=lambda c: (c.estimated_tokens, -c.priority))

        selected: list[AssetCandidate] = []
        remaining_budget = budget.headroom

        for candidate in eligible:
            if candidate.estimated_tokens <= remaining_budget:
                selected.append(candidate)
                remaining_budget -= candidate.estimated_tokens

        return selected

    def should_compact(self, context: ExplorationContext) -> bool:
        """Trigger compaction when approaching depth limit."""
        return context.depth >= context.max_depth - 1


class SemanticRankSelectorPolicy:
    """Semantic ranking selection policy prioritizing relevance.

    Selection rules:
        - Use semantic relevance scores from metadata
        - Combine semantic score with priority for ranking
        - Filter by same constraints as default policy
    """

    def __init__(
        self,
        config: ExplorationPolicyConfig | None = None,
        semantic_weight: float = 0.7,
        priority_weight: float = 0.3,
    ) -> None:
        self.config = config or ExplorationPolicyConfig()
        self.semantic_weight = semantic_weight
        self.priority_weight = priority_weight

    def select(
        self,
        candidates: list[AssetCandidate],
        context: ExplorationContext,
        budget: ContextBudget,
    ) -> list[AssetCandidate]:
        """Select candidates using semantic relevance ranking."""
        # Filter eligible candidates
        eligible: list[AssetCandidate] = []

        for candidate in candidates:
            key = candidate.display_key

            if key in context.seen_assets:
                continue
            if key in context.denied_assets and self.config.deny_revisit_after_denied:
                continue
            if context.depth >= context.max_depth:
                continue
            if budget.headroom < candidate.estimated_tokens:
                continue

            eligible.append(candidate)

        # Calculate combined score: semantic_weight * semantic_score + priority_weight * normalized_priority
        def _score(candidate: AssetCandidate) -> float:
            semantic_score = float(candidate.metadata.get("semantic_score", 0.5))
            # Normalize priority to 0-1 range (assuming max priority ~10)
            normalized_priority = min(candidate.priority / 10.0, 1.0)
            return (
                self.semantic_weight * semantic_score
                + self.priority_weight * normalized_priority
            )

        eligible.sort(key=_score, reverse=True)

        return eligible

    def should_compact(self, context: ExplorationContext) -> bool:
        """Trigger compaction when expansion history is large."""
        return len(context.expansion_history) >= 10


# ------------------------------------------------------------------
# Policy Protocol
# ------------------------------------------------------------------


class ExplorationPolicyPort(Protocol):
    """Abstract interface for exploration policy implementations."""

    async def should_expand(
        self,
        current_budget: ContextBudget,
        candidate: AssetCandidate,
        ctx: ExplorationContext,
    ) -> ExpansionDecision:
        """Decide whether to expand the working set with candidate.

        Args:
            current_budget: Current budget snapshot.
            candidate: The asset under consideration.
            ctx: Current exploration context.

        Returns:
            APPROVED: Add the asset immediately.
            DENIED: Skip the asset permanently this pass.
            DEFERRED: Add to a revisit queue for after the current phase.
        """
        ...

    async def select_next_tools(
        self,
        phase: ExplorationPhase,
        ctx: ExplorationContext,
    ) -> list[dict[str, Any]]:
        """Return a ranked list of recommended tool invocations for the phase.

        Each dict describes one tool call:
            {"tool": "read_file", "priority": 10, "args": {...}}
        """
        ...

    async def should_compact(
        self,
        current_tokens: int,
        effective_limit: int,
        phase: ExplorationPhase,
    ) -> bool:
        """Return True when compaction should be triggered."""
        ...

    def infer_phase(self, tool_call_history: list[str]) -> ExplorationPhase:
        """Infer the current exploration phase from tool-call history.

        Defaults to MAP when no history is available.

        Phase inference logic:
            - No history -> MAP
            - Last call mentions repo_map -> SEARCH
            - Last call is ripgrep/repo_rg/search_code -> SLICE
            - Last call is repo_read/repo_read_slice -> EXPAND
            - Falls back to SEARCH
        """
        ...


# ------------------------------------------------------------------
# Exploration Policy (with pluggable SelectorPolicy)
# ------------------------------------------------------------------


class ExplorationPolicy:
    """Exploration policy with pluggable selector strategy.

    This class wraps a SelectorPolicy implementation and provides
    the full ExplorationPolicyPort interface.

    Backward compatibility:
        - If no selector_policy is provided, uses DefaultSelectorPolicy
        - Maintains same behavior as the original DefaultExplorationPolicy
    """

    def __init__(
        self,
        selector_policy: SelectorPolicy | None = None,
        config: ExplorationPolicyConfig | None = None,
    ) -> None:
        self.config = config or ExplorationPolicyConfig()
        self.selector_policy = selector_policy or DefaultSelectorPolicy(self.config)

    async def should_expand(
        self,
        current_budget: ContextBudget,
        candidate: AssetCandidate,
        ctx: ExplorationContext,
    ) -> ExpansionDecision:
        """Decide whether to expand the working set with candidate.

        Uses the underlying selector_policy to make decisions.
        """
        # Use selector_policy to check if candidate would be selected
        candidates = [candidate]
        selected = self.selector_policy.select(candidates, ctx, current_budget)

        if not selected:
            # Candidate was filtered out - determine if DENIED or DEFERRED
            key = candidate.display_key

            # Check if denied due to already seen or denied
            if key in ctx.seen_assets:
                return ExpansionDecision.DENIED
            if key in ctx.denied_assets and self.config.deny_revisit_after_denied:
                return ExpansionDecision.DENIED

            # Check if denied due to depth
            if ctx.depth >= ctx.max_depth:
                return ExpansionDecision.DENIED

            # Check if over budget - DEFERRED
            if current_budget.headroom < candidate.estimated_tokens:
                return ExpansionDecision.DEFERRED

            # Low priority - DENIED
            return ExpansionDecision.DENIED

        # Candidate was selected - check priority for APPROVED vs DEFERRED
        if candidate.priority >= self.config.min_priority_for_auto_approve:
            return ExpansionDecision.APPROVED

        if candidate.priority >= self.config.min_priority_for_defer:
            return ExpansionDecision.DEFERRED

        return ExpansionDecision.DENIED

    async def select_next_tools(
        self,
        phase: ExplorationPhase,
        ctx: ExplorationContext,
    ) -> list[dict[str, Any]]:
        """Return phase-appropriate tool recommendations."""
        # Tool call limits
        if ctx.phase_tool_calls >= self.config.max_tool_calls_per_phase:
            return []
        if ctx.total_tool_calls >= self.config.max_tool_calls_total:
            return []

        tools = _PHASE_TOOLS.get(phase, [])
        # Filter tools that would exceed per-phase limit
        available = self.config.max_tool_calls_per_phase - ctx.phase_tool_calls
        return tools[:available]

    async def should_compact(
        self,
        current_tokens: int,
        effective_limit: int,
        phase: ExplorationPhase,
    ) -> bool:
        """Return True when compaction should be triggered."""
        if effective_limit <= 0:
            return True
        ratio = current_tokens / effective_limit
        return ratio >= self.config.compaction_trigger_ratio

    def infer_phase(self, tool_call_history: list[str]) -> ExplorationPhase:
        """Infer the current exploration phase from tool-call history.

        Defaults to MAP when no history is available.
        """
        if not tool_call_history:
            return ExplorationPhase.MAP
        last = tool_call_history[-1]
        if "repo_map" in last:
            return ExplorationPhase.SEARCH
        if "repo_rg" in last or "ripgrep" in last or "search_code" in last:
            return ExplorationPhase.SLICE
        if "repo_read" in last:
            return ExplorationPhase.EXPAND
        return ExplorationPhase.SEARCH


# ------------------------------------------------------------------
# Default implementation (backward compatibility alias)
# ------------------------------------------------------------------


class DefaultExplorationPolicy(ExplorationPolicy):
    """Sane default exploration policy.

    This is a backward-compatible alias that uses DefaultSelectorPolicy.

    Phase flow:
        MAP -> SEARCH -> SLICE -> EXPAND -> READ_FULL
        (COMPACT is triggered by should_compact when budget is tight)

    Expansion rules:
        - Assets already in seen_assets are DENIED.
        - Assets in denied_assets are DENIED (no re-review within the same pass).
        - High-priority candidates (>= min_priority_for_auto_approve) are APPROVED.
        - Over-budget candidates are DEFERRED so they can be flushed when budget frees up.
        - Remaining candidates are DEFERRED to the end of the phase.
    """

    def __init__(self, config: ExplorationPolicyConfig | None = None) -> None:
        effective_config = config or ExplorationPolicyConfig()
        super().__init__(
            selector_policy=DefaultSelectorPolicy(effective_config),
            config=effective_config,
        )


# ------------------------------------------------------------------
# Tool catalogue per phase
# ------------------------------------------------------------------


def _build_phase_tools() -> dict[ExplorationPhase, list[dict[str, Any]]]:
    """Build the recommended-tool catalogue for each exploration phase."""

    def tool(
        name: str,
        priority: int,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        return {"tool": name, "priority": priority, "args": args}

    return {
        ExplorationPhase.MAP: [
            tool("build_repo_map", 10, {}),
        ],
        ExplorationPhase.SEARCH: [
            tool("search_files", 10, {}),
            tool("grep", 8, {}),
            tool("read_file", 5, {}),
        ],
        ExplorationPhase.SLICE: [
            tool("read_file", 10, {}),
            tool("grep", 7, {}),
            tool("search_files", 5, {}),
        ],
        ExplorationPhase.EXPAND: [
            tool("grep", 9, {}),
            tool("read_file", 8, {}),
            tool("search_files", 6, {}),
        ],
        ExplorationPhase.READ_FULL: [
            tool("read_file", 10, {}),
        ],
        ExplorationPhase.COMPACT: [
            # Compaction is handled by the RoleContextCompressor
            # No additional tools recommended during COMPACT phase
        ],
    }


_PHASE_TOOLS = _build_phase_tools()


__all__ = [
    "AssetCandidate",
    "AssetKind",
    "DefaultExplorationPolicy",
    "DefaultSelectorPolicy",
    "ExpansionDecision",
    "ExplorationContext",
    "ExplorationPhase",
    "ExplorationPolicy",
    "ExplorationPolicyConfig",
    "ExplorationPolicyPort",
    "GreedySelectorPolicy",
    "SelectorPolicy",
    "SemanticRankSelectorPolicy",
]
