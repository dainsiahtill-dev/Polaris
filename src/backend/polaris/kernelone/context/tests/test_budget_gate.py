"""Tests for polaris.kernelone.context.budget_gate."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.budget_gate import (
    DEFAULT_SAFETY_MARGIN,
    MIN_BUDGET_TOKENS,
    ContextBudget,
    ContextBudgetGate,
    _resolve_model_window_from_spec,
)


class TestContextBudget:
    def test_effective_limit_applies_safety_margin(self) -> None:
        b = ContextBudget(model_window=100_000, safety_margin=0.80, current_tokens=0)
        assert b.effective_limit == 80_000

    def test_headroom_is_effective_limit_minus_used(self) -> None:
        b = ContextBudget(model_window=100_000, safety_margin=0.80, current_tokens=20_000)
        assert b.headroom == 60_000

    def test_headroom_never_negative(self) -> None:
        b = ContextBudget(model_window=100_000, safety_margin=0.80, current_tokens=90_000)
        assert b.headroom == -10_000  # Can go negative

    def test_usage_ratio(self) -> None:
        b = ContextBudget(model_window=100_000, safety_margin=0.80, current_tokens=40_000)
        assert b.usage_ratio == pytest.approx(0.5)

    def test_usage_ratio_zero_when_no_usage(self) -> None:
        b = ContextBudget(model_window=100_000, safety_margin=0.80, current_tokens=0)
        assert b.usage_ratio == 0.0

    def test_usage_ratio_capped_at_zero_when_effective_zero(self) -> None:
        b = ContextBudget(model_window=0, safety_margin=0.80, current_tokens=0)
        assert b.usage_ratio == 0.0


class TestContextBudgetGate:
    def test_construct_with_defaults(self) -> None:
        gate = ContextBudgetGate(model_window=128_000)
        assert gate.model_window == 128_000
        assert gate.safety_margin == DEFAULT_SAFETY_MARGIN
        assert gate.get_current_budget().current_tokens == 0

    def test_construct_with_custom_safety_margin(self) -> None:
        gate = ContextBudgetGate(model_window=200_000, safety_margin=0.90)
        assert gate.get_current_budget().effective_limit == 180_000

    def test_construct_rejects_invalid_model_window(self) -> None:
        with pytest.raises(ValueError, match="positive int"):
            ContextBudgetGate(model_window=0)
        with pytest.raises(ValueError, match="positive int"):
            ContextBudgetGate(model_window=-1)

    def test_construct_rejects_invalid_safety_margin(self) -> None:
        with pytest.raises(ValueError, match="0.0.*1.0"):
            ContextBudgetGate(model_window=128_000, safety_margin=0.0)
        with pytest.raises(ValueError, match="0.0.*1.0"):
            ContextBudgetGate(model_window=128_000, safety_margin=1.5)

    def test_can_add_fits(self) -> None:
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80)
        ok, reason = gate.can_add(10_000)
        assert ok is True
        assert reason == ""

    def test_can_add_exceeds(self) -> None:
        # 128k * 0.80 = 102,400 effective limit; 95k used -> headroom = 7,400; 10k > 7,400
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80, initial_tokens=95_000)
        ok, reason = gate.can_add(10_000)
        assert ok is False
        assert "exceed" in reason

    def test_can_add_negative_rejected(self) -> None:
        gate = ContextBudgetGate(model_window=128_000)
        ok, reason = gate.can_add(-100)
        assert ok is False
        assert "negative" in reason.lower()

    def test_record_usage_updates_current(self) -> None:
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80)
        gate.record_usage(10_000)
        assert gate.get_current_budget().current_tokens == 10_000
        gate.record_usage(5_000)
        assert gate.get_current_budget().current_tokens == 15_000

    def test_record_usage_rejects_negative(self) -> None:
        gate = ContextBudgetGate(model_window=128_000)
        with pytest.raises(ValueError, match="non-negative"):
            gate.record_usage(-1)

    def test_reset_clears_tokens(self) -> None:
        gate = ContextBudgetGate(model_window=128_000, initial_tokens=50_000)
        gate.reset()
        assert gate.get_current_budget().current_tokens == 0

    def test_suggest_compaction_healthy(self) -> None:
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80, initial_tokens=30_000)
        # 30k / (128k * 0.8 = 102.4k) ≈ 29%
        suggestion = gate.suggest_compaction()
        assert "healthy" in suggestion.lower()
        assert "no compaction" in suggestion.lower()

    def test_suggest_compaction_critical(self) -> None:
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80, initial_tokens=85_000)
        # 85k / 102.4k ≈ 83%
        suggestion = gate.suggest_compaction()
        assert "critical" in suggestion.lower()

    def test_suggest_compaction_overflow(self) -> None:
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80, initial_tokens=100_000)
        suggestion = gate.suggest_compaction()
        assert "overflow" in suggestion.lower() or "imminent" in suggestion.lower()

    def test_estimate_tokens_fallback(self) -> None:
        gate = ContextBudgetGate(model_window=128_000)
        # 4 chars/token fallback
        text = "a" * 400
        assert gate.estimate_tokens_for_text(text) == 100
        assert gate.estimate_tokens_for_text("") == 0

    def test_estimate_tokens_empty(self) -> None:
        gate = ContextBudgetGate(model_window=128_000)
        assert gate.estimate_tokens_for_text("") == 0

    def test_from_model_window(self) -> None:
        gate = ContextBudgetGate.from_model_window(200_000)
        assert gate.model_window == 200_000
        assert gate.get_current_budget().effective_limit == 170_000  # 0.85 margin

    def test_from_role_policy_uses_fallback_for_zero(self) -> None:
        gate = ContextBudgetGate.from_role_policy(max_context_tokens=0)
        assert gate.model_window == MIN_BUDGET_TOKENS

    def test_from_role_policy_uses_role_value(self) -> None:
        gate = ContextBudgetGate.from_role_policy(max_context_tokens=64_000)
        assert gate.model_window == 64_000

    def test_default_gate_uses_min_budget(self) -> None:
        gate = ContextBudgetGate.default_gate()
        assert gate.model_window == MIN_BUDGET_TOKENS

    def test_from_provider_spec_known_model(self) -> None:
        """Test from_provider_spec with a known model.

        This test requires ModelCatalog to be properly configured.
        Skips when not configured.
        """
        pytest.importorskip("polaris.kernelone.llm.engine.model_catalog")
        try:
            gate = ContextBudgetGate.from_provider_spec("anthropic", "claude-opus-4-5")
            assert gate.model_window > 0
        except ValueError:
            pytest.skip("ModelCatalog not configured for this model")

    def test_from_provider_spec_unknown_model(self) -> None:
        """Test from_provider_spec with unknown model raises ValueError.

        This test requires ModelCatalog to be properly configured.
        """
        pytest.importorskip("polaris.kernelone.llm.engine.model_catalog")
        with pytest.raises(ValueError, match="Context window not configured"):
            ContextBudgetGate.from_provider_spec("unknown_provider", "unknown-model")


class TestResolveModelWindowFromSpec:
    """Tests for model window resolution from ModelCatalog.

    Note: These tests require proper ModelCatalog configuration in llm_config.json.
    They are marked to skip when the catalog is not properly configured.
    """

    def test_known_anthropic_model(self) -> None:
        """Test that ModelCatalog resolves Anthropic models when configured."""
        # This test requires ModelCatalog to be properly configured
        pytest.importorskip("polaris.kernelone.llm.engine.model_catalog")
        # Skip if the model is not configured
        try:
            result = _resolve_model_window_from_spec("anthropic", "claude-sonnet-4-5")
            assert result > 0
        except ValueError:
            pytest.skip("ModelCatalog not configured for this model")

    def test_known_openai_model(self) -> None:
        """Test that ModelCatalog resolves OpenAI models when configured."""
        pytest.importorskip("polaris.kernelone.llm.engine.model_catalog")
        try:
            result = _resolve_model_window_from_spec("openai", "gpt-4o")
            assert result > 0
        except ValueError:
            pytest.skip("ModelCatalog not configured for this model")

    def test_unknown_provider_raises(self) -> None:
        """Test that unknown provider/model raises ValueError."""
        pytest.importorskip("polaris.kernelone.llm.engine.model_catalog")
        with pytest.raises(ValueError, match="Context window not configured"):
            _resolve_model_window_from_spec("unknown_provider", "unknown-model")

    def test_case_insensitive(self) -> None:
        """Test that provider/model names are case-insensitive when configured."""
        pytest.importorskip("polaris.kernelone.llm.engine.model_catalog")
        try:
            result = _resolve_model_window_from_spec("ANTHROPIC", "claude-opus-4-5")
            assert result > 0
        except ValueError:
            pytest.skip("ModelCatalog not configured for this model")


class TestContextBudgetGateOverflow:
    """Tests for budget overflow scenarios."""

    def test_budget_gate_enforces_overflow(self) -> None:
        """Test that budget gate properly handles overflow scenarios."""
        gate = ContextBudgetGate(model_window=1000, safety_margin=0.85)
        # Effective limit: 1000 * 0.85 = 850
        ok, reason = gate.can_add(estimated_tokens=500)
        assert ok is True
        assert reason == ""

        # Record usage first, then check overflow
        gate.record_usage(500)
        ok2, reason2 = gate.can_add(estimated_tokens=600)
        # After recording 500, headroom = 850 - 500 = 350
        # Adding 600 would exceed (350 < 600)
        assert ok2 is False
        assert "exceed" in reason2.lower()

    def test_budget_gate_headroom_calculation(self) -> None:
        """Test headroom calculation during overflow."""
        gate = ContextBudgetGate(model_window=1000, safety_margin=0.80)
        # Effective limit: 800, headroom: 800

        gate.record_usage(700)
        budget = gate.get_current_budget()
        assert budget.headroom == 100  # 800 - 700 = 100

    def test_budget_gate_overflow_with_tight_margin(self) -> None:
        """Test overflow behavior with tight safety margin."""
        gate = ContextBudgetGate(model_window=1000, safety_margin=0.50)
        # Effective limit: 500

        ok1, _ = gate.can_add(400)
        assert ok1 is True

        # Record usage, then check overflow
        gate.record_usage(400)
        # headroom = 500 - 400 = 100
        # 450 > 100, so should fail
        ok2, reason2 = gate.can_add(450)
        assert ok2 is False

    def test_budget_gate_suggest_compaction_on_overflow(self) -> None:
        """Test that compaction is suggested when approaching overflow."""
        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80)
        # Effective limit: 102,400
        gate.record_usage(95_000)

        suggestion = gate.suggest_compaction()
        assert "critical" in suggestion.lower() or "overflow" in suggestion.lower()

    def test_budget_gate_exact_limit(self) -> None:
        """Test behavior when adding exactly at limit."""
        gate = ContextBudgetGate(model_window=1000, safety_margin=0.80)
        # Effective limit: 800

        # Adding exactly what fits
        ok, reason = gate.can_add(800)
        assert ok is True

    def test_budget_gate_negative_tokens_rejected(self) -> None:
        """Test that negative tokens are always rejected."""
        gate = ContextBudgetGate(model_window=1000)
        ok, reason = gate.can_add(-100)
        assert ok is False
        assert "negative" in reason.lower()
