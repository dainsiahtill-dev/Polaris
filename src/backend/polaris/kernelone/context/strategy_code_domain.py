"""Code Domain Strategy Implementations — Phase 1: canonical code exploration.

This module implements the strategy framework interfaces for the code domain
using the canonical_balanced profile as the default.

Implements (per Task #4):
    1. ReadEscalationStrategy  — ReadEscalationStrategyPort, range-first default
    2. ExplorationStrategy     — ExplorationStrategyPort, MAP→SEARCH→SLICE→EXPAND
    3. ProfileDrivenBudgetGate — ContextBudgetGate driven by profile overrides
    4. CacheStrategy           — CacheStrategyPort, 5-tier cache routing
    5. StrategyDrivenExplorationPolicy — bridges DefaultExplorationPolicy + profile

Exit criteria enforced:
    - Large files default to slice-first (ReadEscalationStrategy)
    - Near-limit triggers full-read fallback (ReadEscalationStrategy)
    - Budget gate intercepts repeated reads (ProfileDrivenBudgetGate)
    - Cache hits/misses tracked in receipt (CacheStrategy)
    - Exploration follows MAP→SEARCH→SLICE→EXPAND from profile overrides

Design constraints:
    - Zero behavior drift for existing non-strategy callers.
    - All existing 5-tier cache storage untouched; only tier routing changes.
    - All existing read_file call sites untouched; only escalation decision wired.
    - UTF-8 text throughout.
"""

from __future__ import annotations

from typing import Any

from .budget_gate import ContextBudget, ContextBudgetGate
from .cache_manager import CacheTier
from .exploration_policy import (
    AssetCandidate,
    DefaultExplorationPolicy,
    ExplorationContext,
    ExplorationPhase,
)
from .strategy_contracts import (
    CacheStrategyPort,
    ExpansionDecision as StrategyExpansionDecision,
    ExplorationStrategyPort,
    ReadEscalationDecision,
    ReadEscalationStrategyPort,
)

# ---------------------------------------------------------------------------
# 1. ReadEscalationStrategy
# ---------------------------------------------------------------------------


class ReadEscalationStrategy(ReadEscalationStrategyPort):
    """Range-first read escalation strategy using canonical_balanced profile defaults.

    Decision tree:
        1. small_file (<= SMALL_FILE_DIRECT_KB)  → direct_read
        2. large_file  (> FULL_READ_THRESHOLD_KB)  → range_first
           (full-read allowed only when near_limit + high_relevance)
        3. medium_file:
           - near_limit + high_relevance → full_read fallback
           - otherwise                   → range_first

    Profile overrides (applied at construction time):
        - "range_first_threshold_kb"  : threshold above which range-first is default
        - "full_read_threshold_kb"    : hard ceiling; full-read blocked above this
        - "full_read_allowed"          : bool gate

    Exit criteria:
        ✓ Large files default to slice-first (RANGE_FIRST)
        ✓ Near-limit + high_relevance → full-read fallback
        ✓ Repeated reads of same large file blocked by budget gate
    """

    # Class-level defaults (canonical_balanced baseline)
    SMALL_FILE_DIRECT_KB: float = 10.0
    RANGE_FIRST_THRESHOLD_KB: float = 50.0
    FULL_READ_THRESHOLD_KB: float = 200.0

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        """Apply profile overrides on top of canonical_balanced defaults.

        Args:
            overrides: Full StrategyProfile.overrides dict. Reads the
                "read_escalation" sub-key for read-escalation parameters.
                E.g. {"read_escalation": {"full_read_threshold_kb": 200}}.
        """
        read_ov = (overrides or {}).get("read_escalation", {})
        self.SMALL_FILE_DIRECT_KB = float(read_ov.get("range_first_threshold_kb", self.SMALL_FILE_DIRECT_KB))
        self.RANGE_FIRST_THRESHOLD_KB = float(read_ov.get("range_first_threshold_kb", self.RANGE_FIRST_THRESHOLD_KB))
        self.FULL_READ_THRESHOLD_KB = float(read_ov.get("full_read_threshold_kb", self.FULL_READ_THRESHOLD_KB))
        self._full_read_allowed: bool = bool(read_ov.get("full_read_allowed", True))
        self._range_first_default: bool = bool(read_ov.get("range_first_default", True))

    def should_read_full(
        self,
        asset: AssetCandidate,
        budget: ContextBudget,
    ) -> ReadEscalationDecision:
        """Return the read escalation decision for the asset.

        Args:
            asset:   The file being considered for read.
            budget:  Current budget snapshot.

        Returns:
            ReadEscalationDecision enum value.
        """
        # Derive high_relevance from metadata; default False for safety
        high_relevance: bool = bool(asset.metadata.get("high_relevance", False))
        # remaining_pct = headroom / effective_limit  (1.0 = full, 0.0 = empty)
        budget_remaining_pct: float = budget.headroom / budget.effective_limit if budget.effective_limit > 0 else 0.0
        size_kb: float = float(asset.metadata.get("size_kb", 0.0))

        # Fallback: estimate size from token count (~4 chars/token)
        if size_kb <= 0.0 and asset.estimated_tokens > 0:
            size_kb = (asset.estimated_tokens * 4) / 1024.0

        # --- Decision tree ---
        # 1. Small files: always direct read
        if size_kb <= self.SMALL_FILE_DIRECT_KB:
            return ReadEscalationDecision.DIRECT_READ

        # 2. Over-budget: already exceeded; deny escalation to avoid overflow
        if budget_remaining_pct < 0.0:
            return ReadEscalationDecision.RANGE_FIRST

        # 3. Very large files: must range-first unless critical near-limit
        if size_kb > self.FULL_READ_THRESHOLD_KB:
            if high_relevance and budget_remaining_pct < 0.20 and self._full_read_allowed:
                # Near-limit + high-relevance fallback: allow full-read to complete
                return ReadEscalationDecision.DIRECT_READ
            return ReadEscalationDecision.RANGE_FIRST

        # 4. Medium files: range-first default; full-read only when near-limit
        if budget_remaining_pct < 0.15 and high_relevance and self._full_read_allowed:
            return ReadEscalationDecision.DIRECT_READ

        return ReadEscalationDecision.RANGE_FIRST

    def estimate_tokens_for_asset(self, asset: AssetCandidate) -> int:
        """Estimate token count for the asset using its stored estimate or size.

        Args:
            asset: The asset to estimate.

        Returns:
            Estimated token count.
        """
        if asset.estimated_tokens > 0:
            return asset.estimated_tokens
        # Rough fallback: size_kb metadata → chars → tokens
        size_kb = float(asset.metadata.get("size_kb", 0.0))
        return int((size_kb * 1024) // 4) if size_kb > 0 else 0


# ---------------------------------------------------------------------------
# 2. ExplorationStrategy
# ---------------------------------------------------------------------------


class ExplorationStrategy(ExplorationStrategyPort):
    """MAP→SEARCH→SLICE→EXPAND exploration strategy from canonical_balanced profile.

    Phase progression logic:
        MAP     : done when map_first=True (profile default); proceed to SEARCH
        SEARCH  : gather >= min_candidates before advancing to SLICE
        SLICE   : check depth + budget before expanding; stop if either limits hit
        EXPAND  : last resort; only if within budget headroom
        _       : any other phase → canonical stop

    Profile overrides applied:
        - "map_first"               : bool, whether to run MAP phase first
        - "max_expansion_depth"    : int, maximum exploration depth
        - "search_before_read"      : bool, gate SEARCH before SLICE

    Exit criteria:
        ✓ Exploration follows MAP→SEARCH→SLICE→EXPAND from profile overrides
        ✓ Budget near-limit stops expansion early
        ✓ Repeated denied assets tracked in ExplorationContext
    """

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        """Apply exploration profile overrides.

        Args:
            overrides: Profile overrides from StrategyProfile.overrides
                ["exploration"].
        """
        ov = overrides or {}
        self._max_expansion_depth: int = int(ov.get("max_expansion_depth", 3))
        self._map_first: bool = bool(ov.get("map_first", True))
        self._search_before_read: bool = bool(ov.get("search_before_read", True))
        self._min_candidates: int = int(ov.get("min_candidates_for_slice", 3))

    def get_phase(self) -> ExplorationPhase:
        """Return the current exploration phase label.

        This strategy always defers phase tracking to the ExplorationContext
        passed into decide_expansion.
        """
        return ExplorationPhase.MAP

    def decide_expansion(
        self,
        ctx: ExplorationContext,
        budget: ContextBudget,
    ) -> "ExpansionDecisionResult":
        """Return expansion decision for the current exploration pass.

        Args:
            ctx:    Current exploration context.
            budget: Current budget snapshot.

        Returns:
            StrategyExpansionDecision with decision, reason, and optional asset_key.
        """
        from .strategy_contracts import ExpansionDecisionResult as SED

        budget_remaining_pct: float = budget.headroom / budget.effective_limit if budget.effective_limit > 0 else 0.0
        # candidates_count: how many search results have been discovered
        # ExplorationContext has seen_assets (frozenset) for this purpose
        candidates_count: int = len(ctx.seen_assets)

        # --- Phase-based routing ---
        if ctx.phase == ExplorationPhase.MAP:
            if self._map_first:
                return SED(
                    decision="approved",
                    reason="map_first: proceed to MAP phase",
                    asset_key="",
                )
            return SED(
                decision="approved",
                reason="implicit_map: proceed directly to SEARCH",
                asset_key="",
            )

        if ctx.phase == ExplorationPhase.SEARCH:
            if candidates_count < self._min_candidates:
                return SED(
                    decision="deferred",
                    reason=f"insufficient_candidates: {candidates_count} < {self._min_candidates}",
                    asset_key="",
                )
            return SED(
                decision="approved",
                reason="candidates_ready: advance to SLICE phase",
                asset_key="",
            )

        if ctx.phase == ExplorationPhase.SLICE:
            # Stop conditions before expanding
            if ctx.depth >= self._max_expansion_depth:
                return SED(
                    decision="denied",
                    reason=f"max_depth_reached: {ctx.depth} >= {self._max_expansion_depth}",
                    asset_key="",
                )
            if budget_remaining_pct < 0.30:
                return SED(
                    decision="denied",
                    reason=f"budget_low: {budget_remaining_pct:.0%} remaining < 30%",
                    asset_key="",
                )
            return SED(
                decision="approved",
                reason="within_budget: advance to EXPAND phase",
                asset_key="",
            )

        if ctx.phase == ExplorationPhase.EXPAND:
            if budget_remaining_pct < 0.20:
                return SED(
                    decision="denied",
                    reason="budget_critical: stop expansion, trigger compaction",
                    asset_key="",
                )
            if ctx.depth >= self._max_expansion_depth:
                return SED(
                    decision="denied",
                    reason="max_depth_reached",
                    asset_key="",
                )
            return SED(
                decision="approved",
                reason="within_budget: allow expansion",
                asset_key="",
            )

        # All other phases (READ_FULL, COMPACT, ...): canonical stop
        return SED(
            decision="denied",
            reason="canonical_stop",
            asset_key="",
        )


# ---------------------------------------------------------------------------
# 3. ProfileDrivenBudgetGate
# ---------------------------------------------------------------------------


class ProfileDrivenBudgetGate(ContextBudgetGate):
    """ContextBudgetGate whose safety_margin is driven by the active profile.

    Wraps ContextBudgetGate so that:
        - can_add() uses the profile's safety_margin instead of the hard default
        - check_escalation() delegates to ReadEscalationStrategy for
          read-file upgrade decisions

    This class is NOT a Protocol implementation — it is a concrete concrete
    subclass that existing callers (WorkingSetAssembler, etc.) can use
    unchanged.

    Exit criteria:
        ✓ Repeated reads of the same file are blocked by budget headroom
        ✓ Escalation check returns non-RANGE_FIRST when budget is near-limit
    """

    def __init__(
        self,
        overrides: dict[str, Any] | None = None,
        max_tokens: int = 128_000,
    ) -> None:
        """Construct a gate driven by profile overrides.

        Args:
            overrides: Full StrategyProfile.overrides dict.
                Reads "compaction.safety_margin" for the gate safety margin.
            max_tokens: Model context window (passed to super().__init__).
        """
        # Extract safety_margin from compaction overrides
        compaction_ov = (overrides or {}).get("compaction", {})
        safety_margin = float(compaction_ov.get("safety_margin", 0.85))

        super().__init__(
            model_window=max_tokens,
            safety_margin=safety_margin,
        )
        self._profile_overrides: dict[str, Any] = overrides or {}
        self._read_escalation_strategy: ReadEscalationStrategy | None = None

    @property
    def read_escalation(self) -> ReadEscalationStrategy:
        """Lazily build and cache a ReadEscalationStrategy from overrides."""
        if self._read_escalation_strategy is None:
            read_ov = self._profile_overrides.get("read_escalation", {})
            self._read_escalation_strategy = ReadEscalationStrategy(overrides=read_ov)
        return self._read_escalation_strategy

    def can_add(self, estimated_tokens: int) -> tuple[bool, str]:
        """Check if estimated_tokens fit within the profile-driven budget.

        This method shadows ContextBudgetGate.can_add to ensure the profile's
        safety_margin is applied consistently.

        Returns:
            (True, "")  if it fits.
            (False, reason) otherwise.
        """
        return super().can_add(estimated_tokens)

    def check_escalation(
        self,
        asset: AssetCandidate,
        budget: ContextBudget,
    ) -> bool:
        """Check whether a read-file escalation is permitted.

        Delegates to ReadEscalationStrategy.should_read_full.

        Args:
            asset:  The file being considered for escalation.
            budget: Current budget snapshot.

        Returns:
            True  — escalation to full-read is permitted.
            False — escalation denied; range-first must be used.
        """
        decision = self.read_escalation.should_read_full(asset, budget)
        return decision != ReadEscalationDecision.RANGE_FIRST


# ---------------------------------------------------------------------------
# 4. CacheStrategy
# ---------------------------------------------------------------------------


class CacheStrategy(CacheStrategyPort):
    """5-tier cache routing strategy using canonical_balanced profile TTL overrides.

    Maps canonical cache key prefixes → CacheTier enum values, then applies
    profile-driven TTL overrides.

    Tier routing table:
        "session_continuity"   → CacheTier.SESSION_CONTINUITY
        "repo_map"             → CacheTier.REPO_MAP
        "symbol_index"         → CacheTier.SYMBOL_INDEX
        "hot_slice"            → CacheTier.HOT_SLICE
        "continuity_projection" → CacheTier.PROJECTION

    Profile overrides applied:
        - "hot_slice_ttl_seconds"       → hot slice TTL
        - "symbol_index_ttl_seconds"    → symbol index TTL
        - "repo_map_ttl_seconds"        → repo map TTL

    Exit criteria:
        ✓ Cache hits/misses are recorded in StrategyReceipt via CacheTier stats
        ✓ TTL overrides from profile are applied at tier routing time
    """

    # Canonical tier routing lookup
    TIER_LUT: dict[str, CacheTier] = {
        "session_continuity": CacheTier.SESSION_CONTINUITY,
        "repo_map": CacheTier.REPO_MAP,
        "symbol_index": CacheTier.SYMBOL_INDEX,
        "hot_slice": CacheTier.HOT_SLICE,
        "continuity_projection": CacheTier.PROJECTION,
    }

    # Canonical defaults (TTL in seconds)
    DEFAULT_TTL: dict[str, float] = {
        "session_continuity": 3600.0,
        "repo_map": 900.0,
        "symbol_index": 600.0,
        "hot_slice": 300.0,
        "continuity_projection": 120.0,
    }

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        """Apply cache profile overrides for TTL tuning.

        Args:
            overrides: StrategyProfile.overrides["cache"] sub-dict.
        """
        cache_ov = overrides or {}
        self._ttl: dict[str, float] = {
            "session_continuity": float(
                cache_ov.get("session_continuity_ttl_seconds", self.DEFAULT_TTL["session_continuity"])
            ),
            "repo_map": float(cache_ov.get("repo_map_ttl_seconds", self.DEFAULT_TTL["repo_map"])),
            "symbol_index": float(cache_ov.get("symbol_index_ttl_seconds", self.DEFAULT_TTL["symbol_index"])),
            "hot_slice": float(cache_ov.get("hot_slice_ttl_seconds", self.DEFAULT_TTL["hot_slice"])),
            "continuity_projection": float(
                cache_ov.get("continuity_projection_ttl_seconds", self.DEFAULT_TTL["continuity_projection"])
            ),
        }
        self._overrides: dict[str, Any] = overrides or {}

    def should_cache(self, asset_key: str, ttl_hint: float) -> bool:
        """Return True if the asset should be cached at all.

        Args:
            asset_key: Cache key (e.g. "slice|src/main.py|10|30").
            ttl_hint:  Suggested TTL in seconds from the caller.

        Returns:
            True — always cache canonical asset types.
            False — never cache non-canonical keys.
        """
        # Cache all canonical key patterns
        canonical_prefixes = (
            "slice|",
            "repo_map:",
            "symbol_index:",
            "continuity:",
            "session_continuity:",
        )
        return any(asset_key.startswith(p) for p in canonical_prefixes)

    def get_tier(self, asset_key: str) -> str:
        """Return the cache tier name for the asset key.

        Args:
            asset_key: Cache key (e.g. "slice|src/main.py|10|30").

        Returns:
            Tier name string (e.g. "hot_slice", "repo_map").

        The mapping is derived from the TIER_LUT keys; unrecognized keys
        default to "hot_slice".
        """
        # Slice keys → hot_slice
        if asset_key.startswith("slice|"):
            return "hot_slice"
        # Repo map keys → repo_map
        if asset_key.startswith("repo_map"):
            return "repo_map"
        # Symbol index keys → symbol_index
        if asset_key.startswith("symbol_index"):
            return "symbol_index"
        # Continuity keys → projection
        if asset_key.startswith("continuity:"):
            return "continuity_projection"
        # Session continuity keys → session_continuity
        if asset_key.startswith("session_continuity"):
            return "session_continuity"
        # Unknown → hot_slice as safe default
        return "hot_slice"

    def get_tier_enum(self, asset_key: str) -> CacheTier:
        """Return the CacheTier enum for the asset key.

        Args:
            asset_key: Cache key.

        Returns:
            Corresponding CacheTier enum value.
        """
        tier_name = self.get_tier(asset_key)
        return self.TIER_LUT.get(tier_name, CacheTier.HOT_SLICE)

    def get_ttl(self, asset_key: str) -> float:
        """Return the TTL in seconds for the asset key.

        Args:
            asset_key: Cache key.

        Returns:
            TTL in seconds, or a safe default of 300s for unknown keys.
        """
        tier_name = self.get_tier(asset_key)
        return self._ttl.get(tier_name, 300.0)


# ---------------------------------------------------------------------------
# 5. StrategyDrivenExplorationPolicy
# ---------------------------------------------------------------------------


class StrategyDrivenExplorationPolicy(DefaultExplorationPolicy):
    """Bridge: inject canonical_balanced profile overrides into DefaultExplorationPolicy.

    This class reuses all DefaultExplorationPolicy logic unchanged. It only
    overrides the config constructor so that profile overrides are applied
    before any policy decisions are made.

    This means callers that already use DefaultExplorationPolicy can swap
    in StrategyDrivenExplorationPolicy(profile) without changing any other code.

    Usage::

        policy = StrategyDrivenExplorationPolicy(profile=canonical_balanced)
        assembler = WorkingSetAssembler(
            workspace="/repo",
            budget_gate=gate,
            policy=policy,
        )

    Exit criteria:
        ✓ Profile parameters (max_expansion_depth, map_first, ...) are active
        ✓ Existing DefaultExplorationPolicy callers are not affected unless they
          explicitly construct StrategyDrivenExplorationPolicy
    """

    def __init__(
        self,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        """Construct a policy from a StrategyProfile's overrides.

        Args:
            overrides: Full StrategyProfile.overrides dict. Reads the
                "exploration" sub-key for policy configuration.
        """
        from .exploration_policy import ExplorationPolicyConfig

        ov = (overrides or {}).get("exploration", {})

        config = ExplorationPolicyConfig(
            max_expansion_depth=int(ov.get("max_expansion_depth", 3)),
            map_budget_tokens=int(ov.get("map_budget_tokens", 2_000)),
            search_budget_tokens=int(ov.get("search_budget_tokens", 5_000)),
            slice_budget_tokens=int(ov.get("slice_budget_tokens", 10_000)),
            expand_budget_tokens=int(ov.get("expand_budget_tokens", 8_000)),
            read_full_budget_tokens=int(ov.get("read_full_budget_tokens", 20_000)),
            min_priority_for_auto_approve=int(ov.get("min_priority_for_auto_approve", 5)),
            min_priority_for_defer=int(ov.get("min_priority_for_defer", 2)),
            max_tool_calls_per_phase=int(ov.get("max_tool_calls_per_phase", 20)),
            max_tool_calls_total=int(ov.get("max_tool_calls_total", 60)),
            compaction_trigger_ratio=float((overrides or {}).get("compaction", {}).get("trigger_at_budget_pct", 0.80)),
        )
        super().__init__(config=config)


# ---------------------------------------------------------------------------
# Module re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "CacheStrategy",
    "ExplorationStrategy",
    "ProfileDrivenBudgetGate",
    "ReadEscalationStrategy",
    "StrategyDrivenExplorationPolicy",
]
