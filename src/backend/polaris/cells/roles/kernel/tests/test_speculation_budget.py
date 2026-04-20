from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.budget import BudgetGovernor
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import (
    BudgetSnapshot,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    EphemeralSpecCache,
    ShadowTaskRegistry,
)


def _policy(
    tool_name: str = "read_file",
    side_effect: str = "readonly",
    cost: str = "cheap",
    speculate_mode: str = "speculative_allowed",
) -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect=side_effect,
        cost=cost,
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode=speculate_mode,
    )


def _snapshot(
    *,
    mode: str = "balanced",
    active_shadow_tasks: int = 0,
    abandonment_ratio: float = 0.0,
    timeout_ratio: float = 0.0,
    wrong_adoption_count: int = 0,
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
        wrong_adoption_count=wrong_adoption_count,
    )


class TestBudgetGovernorAdmit:
    def test_abandonment_over_60_denies_s2_s3(self) -> None:
        governor = BudgetGovernor(mode="balanced")
        snapshot = _snapshot(abandonment_ratio=0.61)

        # S3 policy should be denied
        s3_policy = _policy(speculate_mode="speculative_allowed", side_effect="externally_visible", cost="expensive")
        result = governor.admit(s3_policy, snapshot)
        assert result["allowed"] is False
        assert "exceeds_max_allowed" in str(result["reason"])

        # S2 policy should also be denied in balanced mode (max_allowed becomes 1)
        s2_policy = _policy(speculate_mode="high_confidence_only", side_effect="externally_visible", cost="medium")
        result = governor.admit(s2_policy, snapshot)
        assert result["allowed"] is False

        # S1 policy should still be allowed
        s1_policy = _policy(speculate_mode="speculative_allowed", side_effect="readonly", cost="cheap")
        result = governor.admit(s1_policy, snapshot)
        assert result["allowed"] is True

        # S0 policy should still be allowed
        s0_policy = _policy(speculate_mode="speculative_allowed", side_effect="pure", cost="cheap")
        result = governor.admit(s0_policy, snapshot)
        assert result["allowed"] is True

    def test_wrong_adoption_pauses_all_speculation(self) -> None:
        governor = BudgetGovernor(mode="turbo", wrong_adoption_count=1)
        snapshot = _snapshot()

        for tier in [0, 1, 2, 3]:
            if tier == 0:
                policy = _policy(side_effect="pure", cost="cheap")
            elif tier == 1:
                policy = _policy(side_effect="readonly", cost="cheap")
            elif tier == 2:
                policy = _policy(speculate_mode="high_confidence_only")
            else:
                policy = _policy(
                    speculate_mode="speculative_allowed", side_effect="externally_visible", cost="expensive"
                )

            result = governor.admit(policy, snapshot)
            assert result["allowed"] is False
            assert "wrong_adoption" in str(result["reason"])

    def test_turbo_mode_allows_more_than_safe_mode(self) -> None:
        snapshot = _snapshot()

        # turbo mode allows up to S3
        turbo = BudgetGovernor(mode="turbo")
        s3_policy = _policy(speculate_mode="speculative_allowed", side_effect="externally_visible", cost="expensive")
        assert turbo.admit(s3_policy, snapshot)["allowed"] is True

        # safe mode only allows up to S1
        safe = BudgetGovernor(mode="safe")
        assert safe.admit(s3_policy, snapshot)["allowed"] is False

        s1_policy = _policy(side_effect="readonly", cost="cheap")
        assert safe.admit(s1_policy, snapshot)["allowed"] is True

    def test_timeout_ratio_downgrades_one_tier(self) -> None:
        governor = BudgetGovernor(mode="balanced")
        snapshot = _snapshot(timeout_ratio=0.21)

        # balanced base max = 2, timeout > 20% downgrades by 1 => max 1
        s2_policy = _policy(speculate_mode="high_confidence_only", side_effect="externally_visible", cost="medium")
        result = governor.admit(s2_policy, snapshot)
        assert result["allowed"] is False
        assert "exceeds_max_allowed" in str(result["reason"])

        s1_policy = _policy(side_effect="readonly", cost="cheap")
        assert governor.admit(s1_policy, snapshot)["allowed"] is True

    def test_active_task_limit_by_mode(self) -> None:
        turbo = BudgetGovernor(mode="turbo")
        snapshot = _snapshot(active_shadow_tasks=8)
        policy = _policy(side_effect="pure", cost="cheap")
        result = turbo.admit(policy, snapshot)
        assert result["allowed"] is False
        assert "at_limit" in str(result["reason"])

        balanced = BudgetGovernor(mode="balanced")
        snapshot = _snapshot(active_shadow_tasks=4)
        result = balanced.admit(policy, snapshot)
        assert result["allowed"] is False
        assert "at_limit" in str(result["reason"])

        safe = BudgetGovernor(mode="safe")
        snapshot = _snapshot(active_shadow_tasks=2)
        result = safe.admit(policy, snapshot)
        assert result["allowed"] is False
        assert "at_limit" in str(result["reason"])

    def test_forbid_mode_always_denied(self) -> None:
        governor = BudgetGovernor(mode="turbo")
        snapshot = _snapshot()
        policy = _policy(speculate_mode="forbid")
        result = governor.admit(policy, snapshot)
        assert result["allowed"] is False
        assert "forbidden_by_policy" in str(result["reason"])


class TestBudgetGovernorTierMapping:
    def test_pure_cheap_is_s0(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(side_effect="pure", cost="cheap")
        assert governor._spec_tier(policy) == 0

    def test_readonly_medium_is_s1(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(side_effect="readonly", cost="medium")
        assert governor._spec_tier(policy) == 1

    def test_high_confidence_is_s2(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(speculate_mode="high_confidence_only", side_effect="externally_visible", cost="expensive")
        assert governor._spec_tier(policy) == 2

    def test_speculative_allowed_is_s3(self) -> None:
        governor = BudgetGovernor()
        policy = _policy(speculate_mode="speculative_allowed", side_effect="externally_visible", cost="expensive")
        assert governor._spec_tier(policy) == 3


class TestRegistryBudgetIntegration:
    @pytest.mark.asyncio
    async def test_registry_denies_when_budget_governor_rejects(self) -> None:
        mock_executor = AsyncMock()
        metrics = SpeculationMetrics()
        governor = BudgetGovernor(mode="safe")
        registry = ShadowTaskRegistry(
            speculative_executor=mock_executor,
            metrics=metrics,
            cache=EphemeralSpecCache(),
            budget_governor=governor,
        )

        # safe mode only allows up to S1; S3 policy should be denied by tier limit
        s3_policy = _policy(
            speculate_mode="speculative_allowed",
            side_effect="externally_visible",
            cost="expensive",
        )

        with pytest.raises(RuntimeError, match="shadow task denied"):
            await registry.start_shadow_task(
                turn_id="t1",
                candidate_id="c2",
                tool_name="read_file",
                normalized_args={"path": "2.py"},
                spec_key="spec_2",
                env_fingerprint="fp",
                policy=s3_policy,
            )

    @pytest.mark.asyncio
    async def test_registry_allows_when_no_governor(self) -> None:
        mock_executor = AsyncMock()
        registry = ShadowTaskRegistry(
            speculative_executor=mock_executor,
            metrics=SpeculationMetrics(),
            cache=EphemeralSpecCache(),
        )

        record = await registry.start_shadow_task(
            turn_id="t1",
            candidate_id="c1",
            tool_name="read_file",
            normalized_args={"path": "a.py"},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy=_policy(),
        )
        assert record is not None

    @pytest.mark.asyncio
    async def test_registry_uses_provided_budget_snapshot(self) -> None:
        mock_executor = AsyncMock()
        governor = BudgetGovernor(mode="balanced")
        registry = ShadowTaskRegistry(
            speculative_executor=mock_executor,
            metrics=SpeculationMetrics(),
            cache=EphemeralSpecCache(),
            budget_governor=governor,
        )

        # Provide a snapshot with abandonment_ratio > 60% to deny S3
        snapshot = _snapshot(abandonment_ratio=0.61)
        s3_policy = _policy(speculate_mode="speculative_allowed", side_effect="externally_visible", cost="expensive")

        with pytest.raises(RuntimeError, match="shadow task denied"):
            await registry.start_shadow_task(
                turn_id="t1",
                candidate_id="c1",
                tool_name="read_file",
                normalized_args={"path": "a.py"},
                spec_key="spec_1",
                env_fingerprint="fp",
                policy=s3_policy,
                budget_snapshot=snapshot,
            )
