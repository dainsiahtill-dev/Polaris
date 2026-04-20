"""Built-in RoleOverlay definitions — first-wave role-specific refinements.

Each overlay inherits from a foundation profile and applies role-targeted
overrides to specific strategy dimensions.

Role taxonomy (per blueprint §3.2):
    governance roles: PM, Architect, ChiefEngineer, QA
    execution-family roles: Director (parent), Coder, Writer (overlays)

Overlays are NOT new top-level roles. They are refinements of existing roles.

Overlay naming convention: "{role}.{variant}"
    - Coder/Writer are Director-line overlays, not separate governance roles.

Zero behavior drift: all fields are optional; unspecified keys inherit
from the parent foundation profile.

No existing logic is modified.
"""

from __future__ import annotations

from polaris.kernelone.constants import RoleId
from polaris.kernelone.context.strategy_overlay_contracts import RoleOverlay

# ------------------------------------------------------------------
# director.execution — Default execution底座 (extends canonical_balanced)
# ------------------------------------------------------------------
# Target: default code execution, bug fixes, targeted edits.
# Parent: canonical_balanced
# Rationale:
#   - Slightly higher trigger_at_budget_pct (0.80) for more aggressive
#     receipt micro-compaction during active editing.
#   - Preserves code changes and test evidence in history.

director_execution = RoleOverlay(
    role=RoleId.DIRECTOR,
    parent_profile_id="canonical_balanced",
    overlay_id="director.execution",
    exploration_overrides={
        "map_first": True,
        "search_before_read": True,
        "max_expansion_depth": 3,
    },
    read_escalation_overrides={
        "range_first_default": True,
        "full_read_threshold_kb": 200,
    },
    compaction_overrides={
        "trigger_at_budget_pct": 0.80,
        "receipt_micro_compact": True,
    },
    history_overrides={
        "preserve_code_changes": True,
        "preserve_test_evidence": True,
    },
    cache_overrides={
        "hot_slice_ttl_seconds": 300,
    },
    metadata={
        "description": (
            "Default execution overlay for director. "
            "Active editing / bug fixes. "
            "Preserves code changes and test evidence in history."
        ),
        "variant": "execution",
        "target_domain": "code",
    },
)

# ------------------------------------------------------------------
# director.coder — Code专项 (extends canonical_balanced)
# ------------------------------------------------------------------
# Target: deep symbol-level code exploration, symbol graph usage.
# Parent: canonical_balanced
# Rationale:
#   - Deeper expansion (4 levels) for cross-file symbol resolution.
#   - symbol_centric exploration gives repo-map / symbol index priority.
#   - More aggressive range-first (30 KB) for large codebases.
#   - Longer symbol index TTL (600 s) for faster re-resolution.

director_coder = RoleOverlay(
    role=RoleId.DIRECTOR,
    parent_profile_id="canonical_balanced",
    overlay_id="director.coder",
    exploration_overrides={
        "map_first": True,
        "max_expansion_depth": 4,
        "symbol_centric": True,
        "search_before_read": True,
    },
    read_escalation_overrides={
        "range_first_threshold_kb": 30,
        "full_read_threshold_kb": 150,
    },
    compaction_overrides={
        "preserve_symbol_changes": True,
        "preserve_imports": True,
        "receipt_micro_compact": True,
    },
    cache_overrides={
        "symbol_index_ttl_seconds": 600,
        "hot_slice_ttl_seconds": 300,
    },
    metadata={
        "description": (
            "Code-specialist overlay for director. "
            "Deep symbol-level exploration, cross-file symbol resolution. "
            "symbol_centric mode gives repo-map and symbol index priority."
        ),
        "variant": "coder",
        "target_domain": "code",
    },
)

# ------------------------------------------------------------------
# architect.analysis — 架构分析专项 (extends deep_research)
# ------------------------------------------------------------------
# Target: root-cause analysis, architectural investigations, broad refactors.
# Parent: deep_research
# Rationale:
#   - Inherits deep_research's 5-level expansion and large context budget.
#   - Adds even more aggressive neighbor expansion for dependency tracing.
#   - 20 KB range-first for thorough file sampling before committing
#     to full reads.

architect_analysis = RoleOverlay(
    role=RoleId.ARCHITECT,
    parent_profile_id="deep_research",
    overlay_id="architect.analysis",
    exploration_overrides={
        "map_first": True,
        "max_expansion_depth": 5,
        "neighbor_expansion_aggressive": True,
        "search_before_read": True,
    },
    read_escalation_overrides={
        "range_first_threshold_kb": 20,
        "full_read_threshold_kb": 500,
    },
    compaction_overrides={
        "trigger_at_budget_pct": 0.90,
        "receipt_micro_compact": True,
        "receipt_compact_threshold": 5,
    },
    cache_overrides={
        "symbol_index_ttl_seconds": 300,
        "hot_slice_ttl_seconds": 180,
        "repo_map_ttl_seconds": 600,
    },
    metadata={
        "description": (
            "Architect analysis overlay. "
            "Root-cause analysis, architectural investigations, "
            "broad refactors. Inherits deep_research foundation with "
            "more aggressive neighbor expansion."
        ),
        "variant": "analysis",
        "target_domain": "code",
    },
)

# ------------------------------------------------------------------
# qa.review — 测试评审专项 (extends canonical_balanced)
# ------------------------------------------------------------------
# Target: test-first review, coverage analysis, assertion validation.
# Parent: canonical_balanced
# Rationale:
#   - Higher test_evidence_weight gives test files priority in exploration.
#   - coverage_focused exploration biases toward test and spec files.
#   - Preserves test runs and assertions in history for regression evidence.
#   - Wider range-first threshold (100 KB) for test fixture files.

qa_review = RoleOverlay(
    role=RoleId.QA,
    parent_profile_id="canonical_balanced",
    overlay_id="qa.review",
    exploration_overrides={
        "test_evidence_weight": 0.8,
        "coverage_focused": True,
        "map_first": True,
        "search_before_read": True,
        "max_expansion_depth": 3,
    },
    read_escalation_overrides={
        "range_first_threshold_kb": 100,
        "full_read_threshold_kb": 200,
    },
    history_overrides={
        "preserve_test_runs": True,
        "preserve_assertions": True,
        "preserve_code_changes": False,
    },
    cache_overrides={
        "hot_slice_ttl_seconds": 300,
        "symbol_index_ttl_seconds": 600,
    },
    metadata={
        "description": (
            "QA review overlay. "
            "Test-first review, coverage analysis, assertion validation. "
            "test_evidence_weight=0.8 biases exploration toward test files. "
            "Preserves test runs and assertions in history."
        ),
        "variant": "review",
        "target_domain": "code",
    },
)

# ------------------------------------------------------------------
# director.writer — 文档专项 (extends speed_first)
# ------------------------------------------------------------------
# Target: documentation writing, markdown-first sessions.
# Parent: speed_first
# Rationale:
#   - Outline-first (no MAP) to avoid codebase overhead in doc sessions.
#   - Minimal expansion depth (2) for fast iteration.
#   - Tightest range-first threshold (10 KB) for fast doc reads.
#   - Preserves draft sections in history for continuity across sections.

director_writer = RoleOverlay(
    role=RoleId.DIRECTOR,
    parent_profile_id="speed_first",
    overlay_id="director.writer",
    exploration_overrides={
        "map_first": False,
        "outline_first": True,
        "max_expansion_depth": 2,
        "search_before_read": True,
    },
    read_escalation_overrides={
        "range_first_threshold_kb": 10,
        "full_read_threshold_kb": 100,
    },
    history_overrides={
        "preserve_draft_sections": True,
        "preserve_code_changes": False,
    },
    cache_overrides={
        "hot_slice_ttl_seconds": 600,
    },
    metadata={
        "description": (
            "Documentation writing overlay for director. "
            "outline_first mode skips repo-map overhead. "
            "Fast iteration with minimal expansion depth. "
            "Preserves draft sections in history for section continuity."
        ),
        "variant": "writer",
        "target_domain": "document",
    },
)

# ------------------------------------------------------------------
# Registry of all built-in overlays
# ------------------------------------------------------------------

BUILTIN_OVERLAYS: dict[str, RoleOverlay] = {
    "director.execution": director_execution,
    "director.coder": director_coder,
    "architect.analysis": architect_analysis,
    "qa.review": qa_review,
    "director.writer": director_writer,
}

__all__ = [
    "BUILTIN_OVERLAYS",
    "architect_analysis",
    "director_coder",
    "director_execution",
    "director_writer",
    "qa_review",
]
