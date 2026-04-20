"""Test suite for `roles.kernel` TokenBudget.

Covers:
- TokenBudget.allocate() — over-budget detection and suggestions
- TokenBudget.get_compression_strategy() — ratio-based strategy selection
- TokenBudget.total / available_conversation properties
- AllocationResult.is_over_budget invariant
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.token_budget import (
    AllocationResult,
    CompressionStrategy,
    TokenBudget,
    get_global_token_budget,
)


class TestTokenBudgetDefaults:
    """TokenBudget default配额符合ACGA finops规范。"""

    def test_total_is_sum_of_parts(self) -> None:
        budget = TokenBudget()
        expected = (
            budget.system_context + budget.task_context + budget.conversation + budget.override + budget.safety_margin
        )
        assert budget.total == expected

    def test_available_conversation_excludes_reserved(self) -> None:
        budget = TokenBudget()
        reserved = budget.system_context + budget.task_context + budget.override + budget.safety_margin
        assert budget.available_conversation == budget.total - reserved

    def test_default_budget_respects_safety_margin(self) -> None:
        budget = TokenBudget()
        usable = budget.system_context + budget.task_context + budget.conversation + budget.override
        assert budget.total == usable + budget.safety_margin


class TestTokenBudgetAllocate:
    """TokenBudget.allocate() 检测各层次超预算并提供裁剪建议。"""

    def test_within_budget_returns_empty_over_budget(self) -> None:
        budget = TokenBudget()
        actual = {
            "system": 1000,
            "task": 500,
            "conversation": 2000,
            "override": 200,
        }
        result = budget.allocate(actual)
        assert result.is_over_budget is False
        assert result.over_budget == []
        assert result.suggestions == []
        assert result.total_used == 3700  # 1000+500+2000+200

    def test_system_over_budget_reported(self) -> None:
        budget = TokenBudget()  # system_context=4000
        actual = {"system": 5000}
        result = budget.allocate(actual)
        assert "system" in result.over_budget
        assert any("系统上下文超出预算" in s for s in result.suggestions)

    def test_task_over_budget_reported(self) -> None:
        budget = TokenBudget()  # task_context=2000
        actual = {"task": 3000}
        result = budget.allocate(actual)
        assert "task" in result.over_budget
        assert any("任务上下文超出预算" in s for s in result.suggestions)

    def test_conversation_over_budget_reported(self) -> None:
        budget = TokenBudget()  # conversation=4000
        actual = {"conversation": 5000}
        result = budget.allocate(actual)
        assert "conversation" in result.over_budget
        assert any("对话历史超出预算" in s for s in result.suggestions)

    def test_override_over_budget_reported(self) -> None:
        budget = TokenBudget()  # override=1000
        actual = {"override": 1500}
        result = budget.allocate(actual)
        assert "override" in result.over_budget
        assert any("上下文覆盖超出预算" in s for s in result.suggestions)

    def test_total_over_budget_includes_total_entry(self) -> None:
        budget = TokenBudget()  # total=4000+2000+4000+1000+500=11500
        actual = {  # sum = 12000 > 11500
            "system": 5000,
            "task": 3000,
            "conversation": 4000,
        }
        result = budget.allocate(actual)
        assert "total" in result.over_budget
        assert result.total_budget == budget.total

    def test_partial_over_budget_only_reports_exceeded_parts(self) -> None:
        budget = TokenBudget()
        actual = {"system": 10000}  # only system exceeds
        result = budget.allocate(actual)
        assert result.over_budget == ["system"]  # total=10000 < budget.total=11500, so no "total" entry

    def test_allocate_with_unknown_keys_counted_in_total(self) -> None:
        budget = TokenBudget()
        actual = {"system": 500, "unknown_field": 99999}
        result = budget.allocate(actual)
        # unknown_field is included in total_used sum (all values summed)
        assert result.total_used == 500 + 99999


class TestTokenBudgetCompressionStrategy:
    """TokenBudget.get_compression_strategy() 根据压缩比例返回正确策略。"""

    def test_no_compression_when_within_target(self) -> None:
        budget = TokenBudget()
        strategy = budget.get_compression_strategy(current_tokens=5000, target_tokens=6000)
        assert strategy == CompressionStrategy.NONE

    def test_sliding_window_when_ratio_above_80_percent(self) -> None:
        budget = TokenBudget()
        # ratio 0.85 (> 0.8): need to compress < 20% → sliding window
        strategy = budget.get_compression_strategy(current_tokens=10000, target_tokens=8500)
        assert strategy == CompressionStrategy.SLIDING_WINDOW

    def test_summarize_when_ratio_50_to_80_percent(self) -> None:
        budget = TokenBudget()
        # ratio 0.6 (> 0.5, <= 0.8): need to compress 20-50% → summarize
        strategy = budget.get_compression_strategy(current_tokens=10000, target_tokens=6000)
        assert strategy == CompressionStrategy.SUMMARIZE

    def test_truncate_when_ratio_below_50_percent(self) -> None:
        budget = TokenBudget()
        # ratio 0.4 (< 0.5): need to compress > 50% → truncate
        strategy = budget.get_compression_strategy(current_tokens=10000, target_tokens=4000)
        assert strategy == CompressionStrategy.TRUNCATE

    def test_boundary_80_percent_is_summarize(self) -> None:
        # ratio = target/current = 8000/10000 = 0.8 exactly; condition is ratio > 0.8 so 0.8 falls to SUMMARIZE
        budget = TokenBudget()
        strategy = budget.get_compression_strategy(current_tokens=10000, target_tokens=8000)
        assert strategy == CompressionStrategy.SUMMARIZE

    def test_boundary_50_percent_is_truncate(self) -> None:
        # ratio = 5000/10000 = 0.5 exactly; condition is ratio > 0.5 so 0.5 falls to TRUNCATE
        budget = TokenBudget()
        strategy = budget.get_compression_strategy(current_tokens=10000, target_tokens=5000)
        assert strategy == CompressionStrategy.TRUNCATE


class TestAllocationResult:
    """AllocationResult.is_over_budget invariant。"""

    def test_empty_over_budget_false(self) -> None:
        result = AllocationResult()
        assert result.is_over_budget is False

    def test_with_entries_true(self) -> None:
        result = AllocationResult(over_budget=["system"])
        assert result.is_over_budget is True

    def test_to_dict_includes_all_fields(self) -> None:
        result = AllocationResult(over_budget=["system"], total_used=5000, total_budget=11500)
        d = result.to_dict()
        assert d["over_budget"] == ["system"]
        assert d["total_used"] == 5000
        assert d["total_budget"] == 11500
        assert d["is_over_budget"] is True


class TestGlobalTokenBudget:
    """get_global_token_budget() 返回单例实例。"""

    def test_returns_token_budget_instance(self) -> None:
        budget = get_global_token_budget()
        assert isinstance(budget, TokenBudget)

    def test_singleton_returns_same_instance(self) -> None:
        b1 = get_global_token_budget()
        b2 = get_global_token_budget()
        assert b1 is b2
