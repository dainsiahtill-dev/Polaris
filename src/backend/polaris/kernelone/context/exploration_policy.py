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
# Default implementation
# ------------------------------------------------------------------


class DefaultExplorationPolicy:
    """Sane default exploration policy.

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
        self.config = config or ExplorationPolicyConfig()

    async def should_expand(
        self,
        current_budget: ContextBudget,
        candidate: AssetCandidate,
        ctx: ExplorationContext,
    ) -> ExpansionDecision:
        # 1. Deduplication
        key = candidate.display_key
        if key in ctx.seen_assets:
            _logger.debug("should_expand: %s already seen, DENIED", key)
            return ExpansionDecision.DENIED

        if key in ctx.denied_assets and self.config.deny_revisit_after_denied:
            _logger.debug("should_expand: %s previously denied, DENIED", key)
            return ExpansionDecision.DENIED

        # 2. Depth check
        if ctx.depth >= ctx.max_depth:
            _logger.debug("should_expand: max depth %d reached, DENIED", ctx.max_depth)
            return ExpansionDecision.DENIED

        # 3. Budget feasibility — over-budget assets are DEFERRED so they can be
        #    reconsidered when budget frees up during the phase.  This applies to
        #    all priority levels; priority gates (step 4) only run when the
        #    candidate fits within available headroom.
        if current_budget.headroom < candidate.estimated_tokens:
            _logger.debug(
                "should_expand: %s exceeds budget (headroom=%d < needed=%d), DEFERRED",
                key,
                current_budget.headroom,
                candidate.estimated_tokens,
            )
            return ExpansionDecision.DEFERRED

        # 4. Within available budget — apply priority gates
        if candidate.priority >= self.config.min_priority_for_auto_approve:
            _logger.debug("should_expand: %s auto-approved (priority=%d)", key, candidate.priority)
            return ExpansionDecision.APPROVED

        if candidate.priority >= self.config.min_priority_for_defer:
            _logger.debug("should_expand: %s DEFERRED (priority=%d)", key, candidate.priority)
            return ExpansionDecision.DEFERRED

        _logger.debug("should_expand: %s low priority, DENIED", key)
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
    "ExpansionDecision",
    "ExplorationContext",
    "ExplorationPhase",
    "ExplorationPolicyConfig",
    "ExplorationPolicyPort",
]
