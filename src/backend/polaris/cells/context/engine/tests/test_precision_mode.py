"""Tests for `polaris.cells.context.engine.internal.precision_mode`."""

from __future__ import annotations

import pytest
from polaris.cells.context.engine.internal.precision_mode import (
    COST_BUDGETS,
    COST_CLASSES,
    COST_POLICIES,
    CostStrategy,
    merge_policy,
    normalize_cost_class,
    resolve_cost_class,
    resolve_sources,
    route_by_cost_model,
)

# ---------------------------------------------------------------------------
# normalize_cost_class
# ---------------------------------------------------------------------------


class TestNormalizeCostClass:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("LOCAL", "LOCAL"),
            ("local", "LOCAL"),
            ("Local", "LOCAL"),
            ("FIXED", "FIXED"),
            ("fixed", "FIXED"),
            ("METERED", "METERED"),
            ("metered", "METERED"),
        ],
    )
    def test_valid_classes(self, value: str, expected: str) -> None:
        assert normalize_cost_class(value) == expected

    @pytest.mark.parametrize("value", ["", None, "GARBAGE", "UNKNOWN", "  "])
    def test_invalid_defaults_to_local(self, value: str | None) -> None:
        assert normalize_cost_class(value) == "LOCAL"

    def test_strips_whitespace(self) -> None:
        assert normalize_cost_class("  metered  ") == "METERED"


# ---------------------------------------------------------------------------
# resolve_cost_class
# ---------------------------------------------------------------------------


class TestResolveCostClass:
    def test_explicit_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POLARIS_COST_MODEL", "METERED")
        assert resolve_cost_class("FIXED") == "FIXED"

    def test_env_polaris_cost_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POLARIS_COST_CLASS", raising=False)
        monkeypatch.setenv("POLARIS_COST_MODEL", "METERED")
        assert resolve_cost_class() == "METERED"

    def test_env_polaris_cost_class_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POLARIS_COST_CLASS", "FIXED")
        # COST_MODEL takes precedence
        assert resolve_cost_class() == "FIXED"

    def test_no_env_defaults_to_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POLARIS_COST_MODEL", raising=False)
        monkeypatch.delenv("POLARIS_COST_CLASS", raising=False)
        assert resolve_cost_class() == "LOCAL"


# ---------------------------------------------------------------------------
# merge_policy
# ---------------------------------------------------------------------------


class TestMergePolicy:
    def test_base_preserved_when_no_override(self) -> None:
        base = {"a": 1, "b": 2}
        result = merge_policy(base, None)
        assert result == base
        assert result is not base  # must be a copy

    def test_non_dict_override_returns_base(self) -> None:
        base = {"a": 1}
        for bad in [42, "str", [1, 2], 3.14]:
            result = merge_policy(base, bad)
            assert result == base

    def test_override_values_merged(self) -> None:
        base = {"a": 1, "b": 2, "c": 3}
        override = {"b": 99, "d": 4}
        result = merge_policy(base, override)
        assert result == {"a": 1, "b": 99, "c": 3, "d": 4}

    def test_none_values_in_override_skipped(self) -> None:
        """`None` values in override are dropped from the result entirely."""
        base = {"a": 1, "b": 2}
        override = {"b": None, "c": None}
        result = merge_policy(base, override)
        # b was 2 in base; None override drops it from merged result
        assert result == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# resolve_sources
# ---------------------------------------------------------------------------


class TestResolveSources:
    @pytest.mark.parametrize("role", ["pm", "director", "qa"])
    def test_known_roles_return_list(self, role: str) -> None:
        result = resolve_sources(role, "LOCAL")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_unknown_role_defaults_to_docs_contract(self) -> None:
        result = resolve_sources("unknown_role", "LOCAL")
        assert "docs" in result
        assert "contract" in result

    def test_metered_director_qa_slimmer_sources(self) -> None:
        result = resolve_sources("director", "METERED")
        assert "contract" in result
        assert "repo_evidence" in result
        assert "events" in result

    def test_metered_pm_has_docs(self) -> None:
        result = resolve_sources("pm", "METERED")
        assert "docs" in result

    def test_local_adds_repo_map(self) -> None:
        result = resolve_sources("pm", "LOCAL")
        assert "repo_map" in result

    def test_result_is_unique_preserve_order(self) -> None:
        # resolve_sources internally calls _unique_preserve from kernelone
        result = resolve_sources("director", "LOCAL")
        assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# route_by_cost_model
# ---------------------------------------------------------------------------


class TestRouteByCostModel:
    def test_returns_cost_strategy(self) -> None:
        result = route_by_cost_model("LOCAL", "pm")
        assert isinstance(result, CostStrategy)

    def test_local_named_context_window_strategy(self) -> None:
        result = route_by_cost_model("LOCAL", "pm")
        assert result.name == "ContextWindowStrategy"
        assert result.cost_class == "LOCAL"

    def test_fixed_named_quota_optimization_strategy(self) -> None:
        result = route_by_cost_model("FIXED", "pm")
        assert result.name == "QuotaOptimizationStrategy"
        assert result.cost_class == "FIXED"

    def test_metered_named_token_saving_strategy(self) -> None:
        result = route_by_cost_model("METERED", "pm")
        assert result.name == "TokenSavingStrategy"

    def test_unknown_cost_class_defaults_to_local_strategy(self) -> None:
        result = route_by_cost_model("JUNK", "pm")
        assert result.cost_class == "LOCAL"
        assert result.name == "ContextWindowStrategy"

    def test_budget_from_cost_class(self) -> None:
        for cost_class in COST_CLASSES:
            result = route_by_cost_model(cost_class, "pm")
            assert result.budget == COST_BUDGETS[cost_class]

    def test_policy_includes_role_defaults(self) -> None:
        result = route_by_cost_model("LOCAL", "pm")
        assert result.policy.get("max_items") == 8
        assert "required_providers" in result.policy

    def test_policy_includes_cost_limits(self) -> None:
        result = route_by_cost_model("METERED", "pm")
        assert "docs_max_chars" in result.policy
        assert result.policy["docs_max_chars"] == COST_POLICIES["METERED"]["docs_max_chars"]

    def test_sources_from_resolve_sources(self) -> None:
        result = route_by_cost_model("LOCAL", "pm")
        assert isinstance(result.sources_enabled, list)
        assert len(result.sources_enabled) > 0

    def test_role_unknown_gets_defaults(self) -> None:
        result = route_by_cost_model("LOCAL", "unknown_role_xyz")
        assert "max_items" not in result.policy  # no role-specific policy
        assert result.policy.get("max_items") is None

    def test_cost_class_normalized(self) -> None:
        # lowercase should still work
        result = route_by_cost_model("local", "pm")
        assert result.cost_class == "LOCAL"

    def test_none_role_uses_local_cost_policy(self) -> None:
        result = route_by_cost_model("LOCAL", None)
        # role_key="" → no role_policy; cost_policy (LOCAL) is still merged in
        assert result.cost_class == "LOCAL"
        assert result.policy.get("docs_max_chars") == COST_POLICIES["LOCAL"]["docs_max_chars"]

    def test_none_cost_class_normalizes_to_local(self) -> None:
        result = route_by_cost_model(None, "pm")
        assert result.cost_class == "LOCAL"


# ---------------------------------------------------------------------------
# CostStrategy frozen dataclass invariants
# ---------------------------------------------------------------------------


class TestCostStrategyDataclass:
    def test_is_frozen(self) -> None:
        strategy = route_by_cost_model("LOCAL", "pm")
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            strategy.name = "Tampered"

    def test_fields_present(self) -> None:
        s = route_by_cost_model("FIXED", "director")
        assert hasattr(s, "name")
        assert hasattr(s, "cost_class")
        assert hasattr(s, "budget")
        assert hasattr(s, "policy")
        assert hasattr(s, "sources_enabled")
        assert isinstance(s.budget, dict)
        assert isinstance(s.policy, dict)
        assert isinstance(s.sources_enabled, list)


# ---------------------------------------------------------------------------
# Integration: precision_mode public contract re-exports
# ---------------------------------------------------------------------------


class TestPublicReExports:
    """Verify `public.precision_mode` re-exports the internal symbols."""

    def test_public_imports_work(self) -> None:
        from polaris.cells.context.engine.public.precision_mode import (
            CostStrategy,
            merge_policy,
            normalize_cost_class,
            resolve_cost_class,
            route_by_cost_model,
        )

        assert callable(normalize_cost_class)
        assert callable(merge_policy)
        assert callable(resolve_cost_class)
        assert callable(route_by_cost_model)
        assert issubclass(CostStrategy, object)  # frozen dataclass
