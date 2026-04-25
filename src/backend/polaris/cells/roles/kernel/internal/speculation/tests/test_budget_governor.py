"""Tests for BudgetGovernor — speculation budget admission control."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.speculation.budget import BudgetGovernor
from polaris.cells.roles.kernel.internal.speculation.models import (
    BudgetSnapshot,
    ToolSpecPolicy,
)


def _policy(
    tool_name: str = "read_file",
    *,
    side_effect: str = "readonly",
    cost: str = "cheap",
    cancellability: str = "cooperative",
    reusability: str = "adoptable",
    speculate_mode: str = "speculative_allowed",
    timeout_ms: int = 1200,
) -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect=side_effect,
        cost=cost,
        cancellability=cancellability,
        reusability=reusability,
        speculate_mode=speculate_mode,
        timeout_ms=timeout_ms,
    )


def _snapshot(
    mode: str = "balanced",
    active_shadow_tasks: int = 0,
    abandonment_ratio: float = 0.0,
    timeout_ratio: float = 0.0,
) -> BudgetSnapshot:
    return BudgetSnapshot(
        mode=mode,
        active_shadow_tasks=active_shadow_tasks,
        abandonment_ratio=abandonment_ratio,
        timeout_ratio=timeout_ratio,
        queue_pressure=0.0,
        cpu_pressure=0.0,
        memory_pressure=0.0,
        external_quota_pressure=0.0,
    )


class TestSpecTier:
    """Tests for _spec_tier() mapping logic."""

    def test_forbid_policy_returns_minus_one(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(speculate_mode="forbid")
        assert governor._spec_tier(policy) == -1

    def test_pure_cheap_returns_tier_0(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(side_effect="pure", cost="cheap")
        assert governor._spec_tier(policy) == 0

    def test_readonly_cheap_returns_tier_1(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(side_effect="readonly", cost="cheap")
        assert governor._spec_tier(policy) == 1

    def test_readonly_medium_returns_tier_1(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(side_effect="readonly", cost="medium")
        assert governor._spec_tier(policy) == 1

    def test_pure_medium_returns_tier_1(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(side_effect="pure", cost="medium")
        assert governor._spec_tier(policy) == 1

    def test_high_confidence_only_shadowed_by_readonly_cheap(self) -> None:
        """high_confidence_only is shadowed by readonly+cheap (line 61 checked before line 63)."""
        governor = BudgetGovernor()
        # readonly + cheap matches line 61 first → returns tier 1
        policy = _policy(side_effect="readonly", cost="cheap", speculate_mode="high_confidence_only")
        assert governor._spec_tier(policy) == 1

    def test_high_confidence_only_reaches_tier_2_when_not_readonly_cheap(self) -> None:
        """high_confidence_only reaches line 63 when not shadowed by readonly+cheap."""
        governor = BudgetGovernor()
        policy = _policy(side_effect="mutating", cost="expensive", speculate_mode="high_confidence_only")
        assert governor._spec_tier(policy) == 2

    def test_speculative_allowed_shadowed_by_readonly_cheap(self) -> None:
        """speculative_allowed is shadowed by readonly+cheap (line 61 checked before line 65)."""
        governor = BudgetGovernor()
        # readonly + cheap matches line 61 first → returns tier 1
        policy = _policy(side_effect="readonly", cost="cheap", speculate_mode="speculative_allowed")
        assert governor._spec_tier(policy) == 1

    def test_speculative_allowed_reaches_tier_3_when_not_readonly_cheap(self) -> None:
        """speculative_allowed reaches line 65 when not shadowed by readonly+cheap."""
        governor = BudgetGovernor()
        policy = _policy(side_effect="mutating", cost="expensive", speculate_mode="speculative_allowed")
        assert governor._spec_tier(policy) == 3

    def test_dry_run_only_falls_to_default_tier_2(self) -> None:
        """dry_run_only falls to the default return 2 (not matching any specific branch)."""
        governor = BudgetGovernor()
        # dry_run_only doesn't match forbid/pure_cheap/readonly_cheap/high_confidence/speculative_allowed
        # falls through to return 2
        policy = _policy(side_effect="mutating", cost="expensive", speculate_mode="dry_run_only")
        assert governor._spec_tier(policy) == 2


class TestMaxAllowedTier:
    """Tests for _max_allowed_tier() degradation logic."""

    def test_turbo_base_max_is_3(self) -> None:
        governor = BudgetGovernor(mode="turbo")
        snap = _snapshot()
        assert governor._max_allowed_tier(snap) == 3

    def test_balanced_base_max_is_2(self) -> None:
        governor = BudgetGovernor(mode="balanced")
        snap = _snapshot()
        assert governor._max_allowed_tier(snap) == 2

    def test_safe_base_max_is_1(self) -> None:
        governor = BudgetGovernor(mode="safe")
        snap = _snapshot()
        assert governor._max_allowed_tier(snap) == 1

    def test_wrong_adoption_count_pauses_all(self) -> None:
        governor = BudgetGovernor(mode="turbo", wrong_adoption_count=1)
        snap = _snapshot()
        assert governor._max_allowed_tier(snap) == -1

    def test_abandonment_ratio_above_60_degrades_to_tier_1(self) -> None:
        governor = BudgetGovernor(mode="turbo")
        snap = _snapshot(abandonment_ratio=0.61)
        assert governor._max_allowed_tier(snap) == 1

    def test_abandonment_ratio_at_60_threshold_unchanged(self) -> None:
        governor = BudgetGovernor(mode="turbo")
        snap = _snapshot(abandonment_ratio=0.60)
        assert governor._max_allowed_tier(snap) == 3

    def test_timeout_ratio_above_20_degrades_one_level(self) -> None:
        governor = BudgetGovernor(mode="balanced")  # base=2
        snap = _snapshot(timeout_ratio=0.21)
        assert governor._max_allowed_tier(snap) == 1

    def test_timeout_ratio_at_20_threshold_unchanged(self) -> None:
        governor = BudgetGovernor(mode="balanced")
        snap = _snapshot(timeout_ratio=0.20)
        assert governor._max_allowed_tier(snap) == 2

    def test_combined_degradation_abandonment_first(self) -> None:
        """abandonment degrades first, then timeout applies to reduced max."""
        governor = BudgetGovernor(mode="turbo")  # base=3
        snap = _snapshot(abandonment_ratio=0.61, timeout_ratio=0.30)
        # abandonment: effective_max = min(3, 1) = 1
        # timeout: max(1-1, 0) = 0
        assert governor._max_allowed_tier(snap) == 0


class TestAdmit:
    """Tests for admit() decision logic."""

    def test_forbidden_policy_denied(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(speculate_mode="forbid")
        snap = _snapshot()
        result = governor.admit(policy, snap)
        assert result["allowed"] is False
        assert result["reason"] == "speculation_forbidden_by_policy"

    def test_wrong_adoption_pauses_all(self) -> None:
        governor = BudgetGovernor(wrong_adoption_count=1)
        policy = _policy()
        snap = _snapshot()
        result = governor.admit(policy, snap)
        assert result["allowed"] is False
        assert result["reason"] == "speculation_paused_due_to_wrong_adoption"

    def test_tier_exceeds_max_denied(self) -> None:
        governor = BudgetGovernor(mode="safe")  # max tier = 1
        # tier 3 (mutating + speculative_allowed) should be denied
        policy = _policy(side_effect="mutating", cost="expensive", speculate_mode="speculative_allowed")
        snap = _snapshot()
        result = governor.admit(policy, snap)
        assert result["allowed"] is False
        assert "tier_3_exceeds_max_allowed_1" in result["reason"]

    def test_active_tasks_at_limit_denied(self) -> None:
        governor = BudgetGovernor(mode="balanced")  # max active = 4
        policy = _policy()
        snap = _snapshot(active_shadow_tasks=4)
        result = governor.admit(policy, snap)
        assert result["allowed"] is False
        assert "active_tasks_4_at_limit_4" in result["reason"]

    def test_active_tasks_below_limit_allowed(self) -> None:
        governor = BudgetGovernor(mode="balanced")
        policy = _policy()
        snap = _snapshot(active_shadow_tasks=3)
        result = governor.admit(policy, snap)
        assert result["allowed"] is True
        assert result["reason"] is None

    def test_turbo_mode_allows_up_to_8_active(self) -> None:
        governor = BudgetGovernor(mode="turbo")
        # tier 3 (mutating + speculative_allowed) with 7 active tasks
        policy = _policy(side_effect="mutating", cost="expensive", speculate_mode="speculative_allowed")
        snap = _snapshot(active_shadow_tasks=7)
        result = governor.admit(policy, snap)
        assert result["allowed"] is True

    def test_safe_mode_allows_only_tier_0_and_1(self) -> None:
        governor = BudgetGovernor(mode="safe")  # max tier = 1
        # Tier 0 (pure + cheap) should be allowed
        policy_tier0 = _policy(side_effect="pure", cost="cheap")
        snap = _snapshot()
        assert governor.admit(policy_tier0, snap)["allowed"] is True
        # Tier 1 (readonly + cheap) should be allowed
        policy_tier1 = _policy(side_effect="readonly", cost="cheap")
        assert governor.admit(policy_tier1, snap)["allowed"] is True
        # Tier 2 (mutating + high_confidence) should be denied
        policy_tier2 = _policy(side_effect="mutating", cost="expensive", speculate_mode="high_confidence_only")
        assert governor.admit(policy_tier2, snap)["allowed"] is False

    def test_balanced_mode_allows_up_to_tier_2(self) -> None:
        governor = BudgetGovernor(mode="balanced")
        snap = _snapshot()
        # Tier 3 should be denied
        policy_t3 = _policy(side_effect="mutating", cost="expensive", speculate_mode="speculative_allowed")
        assert governor.admit(policy_t3, snap)["allowed"] is False
        # Tier 2 should be allowed
        policy_t2 = _policy(side_effect="mutating", cost="expensive", speculate_mode="high_confidence_only")
        assert governor.admit(policy_t2, snap)["allowed"] is True

    def test_unknown_mode_defaults_to_balanced_max(self) -> None:
        governor = BudgetGovernor(mode="unknown_mode")
        snap = _snapshot()
        # Tier 3 should be denied (balanced max = 2)
        policy_t3 = _policy(side_effect="mutating", cost="expensive", speculate_mode="speculative_allowed")
        assert governor.admit(policy_t3, snap)["allowed"] is False


class TestModeSetter:
    """Tests for mode property setter."""

    def test_mode_can_be_changed(self) -> None:
        governor = BudgetGovernor(mode="safe")
        assert governor.mode == "safe"
        governor.mode = "turbo"
        assert governor.mode == "turbo"
        governor.mode = "balanced"
        assert governor.mode == "balanced"


class TestWrongAdoptionCountSetter:
    """Tests for wrong_adoption_count property setter."""

    def test_wrong_adoption_count_can_be_changed(self) -> None:
        governor = BudgetGovernor(wrong_adoption_count=0)
        assert governor.wrong_adoption_count == 0
        governor.wrong_adoption_count = 3
        assert governor.wrong_adoption_count == 3
