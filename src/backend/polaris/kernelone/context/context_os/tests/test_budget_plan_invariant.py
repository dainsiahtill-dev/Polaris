"""Test BudgetPlan Invariant — expected_next_input_tokens must not exceed model_context_window.

Validates:
- BudgetPlan.validate_invariants() raises BudgetExceededError when budget is exceeded
- BudgetPlan.validate_invariants() passes when budget is within limits
- BudgetExceededError contains correct diagnostic information
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.context.context_os.models_v2 import BudgetPlanV2 as BudgetPlan
from polaris.kernelone.errors import BudgetExceededError


class TestBudgetPlanInvariant:
    """BudgetPlan invariant validation regression tests."""

    def _make_budget_plan(
        self,
        model_context_window: int = 128000,
        expected_next_input_tokens: int = 0,
        **overrides: Any,
    ) -> BudgetPlan:
        """Create a BudgetPlan with sensible defaults for testing."""
        return BudgetPlan(
            model_context_window=model_context_window,
            output_reserve=overrides.get("output_reserve", 4096),
            tool_reserve=overrides.get("tool_reserve", 8192),
            safety_margin=overrides.get("safety_margin", 2048),
            input_budget=overrides.get("input_budget", 100000),
            retrieval_budget=overrides.get("retrieval_budget", 50000),
            soft_limit=overrides.get("soft_limit", 110000),
            hard_limit=overrides.get("hard_limit", 120000),
            emergency_limit=overrides.get("emergency_limit", 125000),
            current_input_tokens=overrides.get("current_input_tokens", 0),
            expected_next_input_tokens=expected_next_input_tokens,
            p95_tool_result_tokens=overrides.get("p95_tool_result_tokens", 0),
            planned_retrieval_tokens=overrides.get("planned_retrieval_tokens", 0),
            validation_error=overrides.get("validation_error", ""),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Happy Path
    # ──────────────────────────────────────────────────────────────────────────

    def test_valid_budget_passes_validation(self) -> None:
        """BudgetPlan with expected_next_input_tokens < model_context_window should pass."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=100000,
        )

        # Should not raise
        plan.validate_invariants()

    def test_exact_boundary_passes_validation(self) -> None:
        """BudgetPlan with expected_next_input_tokens == model_context_window should pass."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=128000,
        )

        # Should not raise (condition is >, not >=)
        plan.validate_invariants()

    def test_zero_expected_tokens_passes(self) -> None:
        """BudgetPlan with zero expected tokens should pass."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=0,
        )

        plan.validate_invariants()

    # ──────────────────────────────────────────────────────────────────────────
    # Edge Cases
    # ──────────────────────────────────────────────────────────────────────────

    def test_small_context_window_with_few_tokens_passes(self) -> None:
        """Small context window with appropriately small token count should pass."""
        plan = self._make_budget_plan(
            model_context_window=4096,
            expected_next_input_tokens=3000,
        )

        plan.validate_invariants()

    def test_large_overrun_raises_error(self) -> None:
        """Large token overrun should raise BudgetExceededError."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=200000,
        )

        with pytest.raises(BudgetExceededError) as exc_info:
            plan.validate_invariants()

        error = exc_info.value
        assert error.limit == 128000
        assert error.requested == 200000

    # ──────────────────────────────────────────────────────────────────────────
    # Exceptions
    # ──────────────────────────────────────────────────────────────────────────

    def test_exceeded_budget_raises_budget_exceeded_error(self) -> None:
        """expected_next_input_tokens > model_context_window must raise BudgetExceededError."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=128001,
        )

        with pytest.raises(BudgetExceededError) as exc_info:
            plan.validate_invariants()

        error = exc_info.value
        assert "invariant violated" in str(error).lower()
        assert error.limit == 128000
        assert error.requested == 128001
        assert error.current == 0

    def test_error_message_includes_token_counts(self) -> None:
        """BudgetExceededError message must include diagnostic token counts."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=150000,
            current_input_tokens=50000,
        )

        with pytest.raises(BudgetExceededError) as exc_info:
            plan.validate_invariants()

        error_message = str(exc_info.value)
        assert "150000" in error_message
        assert "128000" in error_message
        assert "22000" in error_message  # overrun amount (150000 - 128000 = 22000)

    def test_error_includes_current_tokens(self) -> None:
        """BudgetExceededError must capture current_input_tokens correctly."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=150000,
            current_input_tokens=75000,
        )

        with pytest.raises(BudgetExceededError) as exc_info:
            plan.validate_invariants()

        assert exc_info.value.current == 75000

    # ──────────────────────────────────────────────────────────────────────────
    # Regression: Boundary Tests
    # ──────────────────────────────────────────────────────────────────────────

    def test_one_token_over_boundary_raises(self) -> None:
        """Exactly one token over the boundary must raise BudgetExceededError."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=128001,
        )

        with pytest.raises(BudgetExceededError):
            plan.validate_invariants()

    def test_negative_expected_tokens_passes(self) -> None:
        """Negative expected_next_input_tokens should pass (edge case)."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=-100,
        )

        # Negative tokens should not exceed context window
        plan.validate_invariants()

    def test_zero_context_window_with_zero_tokens_passes(self) -> None:
        """Zero context window with zero tokens should pass."""
        plan = self._make_budget_plan(
            model_context_window=0,
            expected_next_input_tokens=0,
        )

        plan.validate_invariants()

    def test_zero_context_window_with_positive_tokens_raises(self) -> None:
        """Zero context window with positive tokens should raise."""
        plan = self._make_budget_plan(
            model_context_window=0,
            expected_next_input_tokens=1,
        )

        with pytest.raises(BudgetExceededError):
            plan.validate_invariants()

    def test_to_dict_includes_all_fields(self) -> None:
        """BudgetPlan.to_dict() must include all budget fields."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            expected_next_input_tokens=50000,
            current_input_tokens=30000,
            p95_tool_result_tokens=1000,
            planned_retrieval_tokens=2000,
        )

        d = plan.to_dict()

        assert d["model_context_window"] == 128000
        assert d["expected_next_input_tokens"] == 50000
        assert d["current_input_tokens"] == 30000
        assert d["p95_tool_result_tokens"] == 1000
        assert d["planned_retrieval_tokens"] == 2000
        assert d["output_reserve"] == 4096
        assert d["tool_reserve"] == 8192
        assert d["safety_margin"] == 2048
        assert d["input_budget"] == 100000
        assert d["retrieval_budget"] == 50000
        assert d["soft_limit"] == 110000
        assert d["hard_limit"] == 120000
        assert d["emergency_limit"] == 125000

    def test_context_window_status_event_creation(self) -> None:
        """BudgetPlan must create ContextWindowStatus event correctly."""
        plan = self._make_budget_plan(
            model_context_window=128000,
            current_input_tokens=64000,
        )

        status = plan.to_context_window_status_event()

        assert status.payload.current_tokens == 64000
        assert status.payload.max_tokens == 128000
