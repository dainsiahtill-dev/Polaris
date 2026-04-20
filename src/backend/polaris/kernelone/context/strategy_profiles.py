"""Built-in strategy profiles — zero behavior drift.

All profiles are frozen dataclasses.
All parameters are overridable via the registry.

Profile resolution does not change existing behavior;
it provides the framework for future profile-driven control.
"""

from __future__ import annotations

from polaris.kernelone.context.strategy_contracts import (
    ProfileMetadata,
    StrategyProfile,
)

# ------------------------------------------------------------------
# canonical_balanced — Default foundation profile
# ------------------------------------------------------------------
# Target: general asset-intensive work; Phase 1 primary: coding sessions.
# Characteristics:
#   - MAP first
#   - SEARCH before read
#   - range-first for medium and large files
#   - near-limit compaction only
#   - aggressive tool-receipt micro-compaction
#   - hot-slice cache enabled

canonical_balanced = StrategyProfile(
    profile_id="canonical_balanced",
    profile_version="1.0.0",
    bundle_id="kernelone.default.v1",
    overrides={
        "exploration": {
            "map_first": True,
            "search_before_read": True,
            "max_expansion_depth": 3,
            "range_first_threshold_kb": 50,
        },
        "read_escalation": {
            "full_read_allowed": True,
            "full_read_threshold_kb": 200,
            "range_first_default": True,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.80,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 3,
        },
        "cache": {
            "hot_slice_ttl_seconds": 300,
            "symbol_index_ttl_seconds": 600,
            "repo_map_ttl_seconds": 900,
        },
    },
    metadata=ProfileMetadata(
        description=(
            "Default foundation profile. MAP→SEARCH→SLICE→EXPAND→near-limit COMPACT. "
            "Best overall balance of quality, cost, and latency for code work."
        ),
        target_domain="code",
        risk_level="canonical",
    ),
)

# ------------------------------------------------------------------
# speed_first — Low-latency profile
# ------------------------------------------------------------------
# Target: quick debugging, short CLI turns, low-latency environments.
# Characteristics:
#   - lighter MAP / fewer expansion passes
#   - tighter prompt budget
#   - more cache reuse
#   - minimal continuity payload

speed_first = StrategyProfile(
    profile_id="speed_first",
    profile_version="1.0.0",
    bundle_id="kernelone.default.v1",
    overrides={
        "exploration": {
            "max_expansion_depth": 1,
            "map_first": False,
            "range_first_threshold_kb": 30,
        },
        "read_escalation": {
            "range_first_default": True,
            "full_read_allowed": False,
            "full_read_threshold_kb": 100,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.70,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 2,
        },
        "cache": {
            "hot_slice_ttl_seconds": 600,
            "symbol_index_ttl_seconds": 900,
            "repo_map_ttl_seconds": 1200,
        },
    },
    metadata=ProfileMetadata(
        description=(
            "Low-latency profile. Quick debugging, short CLI turns. "
            "Lighter MAP, fewer expansion passes, tighter budget."
        ),
        target_domain="code",
        risk_level="experimental",
    ),
)

# ------------------------------------------------------------------
# deep_research — Deep investigation profile
# ------------------------------------------------------------------
# Target: root-cause analysis, architectural investigations, broad refactors.
# Characteristics:
#   - stronger repo map and symbol graph usage
#   - more aggressive neighbor expansion
#   - larger working set before compaction
#   - slower but deeper evidence collection

deep_research = StrategyProfile(
    profile_id="deep_research",
    profile_version="1.0.0",
    bundle_id="kernelone.default.v1",
    overrides={
        "exploration": {
            "max_expansion_depth": 5,
            "map_first": True,
            "neighbor_expansion_aggressive": True,
            "range_first_threshold_kb": 20,
        },
        "read_escalation": {
            "full_read_allowed": True,
            "full_read_threshold_kb": 500,
            "range_first_threshold_kb": 20,
            "range_first_default": True,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.90,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 5,
        },
        "cache": {
            "hot_slice_ttl_seconds": 180,
            "symbol_index_ttl_seconds": 300,
            "repo_map_ttl_seconds": 600,
        },
    },
    metadata=ProfileMetadata(
        description=(
            "Deep investigation profile. Root-cause analysis, architectural "
            "investigations, broad refactors. Stronger repo map, more "
            "aggressive neighbor expansion, larger working set."
        ),
        target_domain="code",
        risk_level="experimental",
    ),
)

# ------------------------------------------------------------------
# cost_guarded — Cost-sensitive profile
# ------------------------------------------------------------------
# Target: cost-sensitive deployments, smaller-context local models.
# Characteristics:
#   - strict budget gate
#   - early micro-compaction of tool receipts
#   - conservative full-read escalation
#   - cache-first behavior

cost_guarded = StrategyProfile(
    profile_id="cost_guarded",
    profile_version="1.0.0",
    bundle_id="kernelone.default.v1",
    overrides={
        "exploration": {
            "max_expansion_depth": 2,
            "map_first": True,
            "search_before_read": True,
            "range_first_threshold_kb": 30,
        },
        "read_escalation": {
            "full_read_allowed": False,
            "full_read_threshold_kb": 100,
            "range_first_default": True,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.65,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 2,
        },
        "cache": {
            "hot_slice_ttl_seconds": 900,
            "symbol_index_ttl_seconds": 1800,
            "repo_map_ttl_seconds": 3600,
        },
    },
    metadata=ProfileMetadata(
        description=(
            "Cost-sensitive profile. Strict budget gate, aggressive compaction. "
            "Conservative full-read escalation, cache-first behavior."
        ),
        target_domain="universal",
        risk_level="experimental",
    ),
)

# ------------------------------------------------------------------
# claude_like_dynamic — Reference benchmark profile
# ------------------------------------------------------------------
# Target: benchmark comparison against common industry reading pattern.
# NOT a product emulation — a reference baseline for comparison.
# Characteristics:
#   - dynamic search-first exploration
#   - implicit map by search evidence
#   - small-file direct read allowed
#   - large-file slice-first
#   - near-limit trimming and continuity fallback

claude_like_dynamic = StrategyProfile(
    profile_id="claude_like_dynamic",
    profile_version="1.0.0",
    bundle_id="kernelone.default.v1",
    overrides={
        "exploration": {
            "search_first": True,
            "implicit_map": True,
            "max_expansion_depth": 3,
            "map_first": False,
        },
        "read_escalation": {
            "small_file_direct_read": True,
            "large_file_slice_first": True,
            "full_read_allowed": True,
            "full_read_threshold_kb": 300,
        },
        "compaction": {
            "trigger_at_budget_pct": 0.75,
            "receipt_micro_compact": True,
            "receipt_compact_threshold": 3,
        },
        "cache": {
            "hot_slice_ttl_seconds": 300,
            "symbol_index_ttl_seconds": 600,
            "repo_map_ttl_seconds": 900,
        },
    },
    metadata=ProfileMetadata(
        description=(
            "Reference profile: dynamic search-first (benchmark baseline). "
            "NOT product emulation. Use to validate whether canonical is "
            "actually better than common industry reading patterns."
        ),
        target_domain="code",
        risk_level="reference",
    ),
)

# ------------------------------------------------------------------
# Registry of all built-in profiles
# ------------------------------------------------------------------

BUILTIN_PROFILES: dict[str, StrategyProfile] = {
    "canonical_balanced": canonical_balanced,
    "speed_first": speed_first,
    "deep_research": deep_research,
    "cost_guarded": cost_guarded,
    "claude_like_dynamic": claude_like_dynamic,
}

__all__ = [
    "BUILTIN_PROFILES",
    "canonical_balanced",
    "claude_like_dynamic",
    "cost_guarded",
    "deep_research",
    "speed_first",
]
