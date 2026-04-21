"""Tests for Hypothesis Generator - 假设生成器测试。"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.debug_strategy.hypothesis_generator import (
    HypothesisGenerator,
)
from polaris.cells.roles.kernel.internal.debug_strategy.models import ErrorContext
from polaris.cells.roles.kernel.internal.debug_strategy.types import ErrorCategory


class TestHypothesisGenerator:
    """假设生成器测试。"""

    @pytest.fixture
    def generator(self) -> HypothesisGenerator:
        """创建假设生成器实例。"""
        return HypothesisGenerator()

    def test_generate_hypotheses_syntax_error(self, generator: HypothesisGenerator) -> None:
        """测试语法错误假设生成。"""
        context = ErrorContext(
            error_type="syntax_error",
            error_message="invalid syntax",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.SYNTAX_ERROR)

        assert len(hypotheses) > 0
        for hyp in hypotheses:
            assert hyp.hypothesis_id.startswith("hyp_")
            assert hyp.description
            assert 0 <= hyp.confidence <= 1
            assert hyp.test_approach
            assert len(hyp.validation_criteria) > 0

    def test_generate_hypotheses_timing_error(self, generator: HypothesisGenerator) -> None:
        """测试时序错误假设生成。"""
        context = ErrorContext(
            error_type="timeout",
            error_message="Connection timeout",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.TIMING_ERROR)

        assert len(hypotheses) > 0
        descriptions = [h.description for h in hypotheses]
        # 应该包含时序相关的假设
        assert any("async" in d.lower() or "wait" in d.lower() for d in descriptions)

    def test_generate_hypotheses_max_limit(self, generator: HypothesisGenerator) -> None:
        """测试最大假设数量限制。"""
        context = ErrorContext(
            error_type="error",
            error_message="test",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR, max_hypotheses=2)

        assert len(hypotheses) <= 2

    def test_confidence_calculation(self, generator: HypothesisGenerator) -> None:
        """测试置信度计算。"""
        context = ErrorContext(
            error_type="runtime_error",
            error_message="variable not defined",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.RUNTIME_ERROR)

        for hyp in hypotheses:
            assert 0.3 <= hyp.confidence <= 0.95

    def test_hypothesis_id_unique(self, generator: HypothesisGenerator) -> None:
        """测试假设ID唯一性。"""
        context = ErrorContext(
            error_type="error",
            error_message="test",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR)
        ids = [h.hypothesis_id for h in hypotheses]

        assert len(ids) == len(set(ids))  # 所有ID唯一

    def test_generate_from_error_message_none(self, generator: HypothesisGenerator) -> None:
        """测试从None错误消息生成假设。"""
        context = ErrorContext(
            error_type="error",
            error_message="Object is None",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR)

        descriptions = [h.description.lower() for h in hypotheses]
        assert any("none" in d or "null" in d for d in descriptions)

    def test_generate_from_error_message_index(self, generator: HypothesisGenerator) -> None:
        """测试从索引错误消息生成假设。"""
        context = ErrorContext(
            error_type="error",
            error_message="Index out of range",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR)

        descriptions = [h.description.lower() for h in hypotheses]
        assert any("index" in d or "range" in d for d in descriptions)

    def test_generate_from_error_message_key(self, generator: HypothesisGenerator) -> None:
        """测试从Key错误消息生成假设。"""
        context = ErrorContext(
            error_type="error",
            error_message="Key not found",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR)

        descriptions = [h.description.lower() for h in hypotheses]
        assert any("key" in d or "dictionary" in d for d in descriptions)

    def test_unknown_error_category(self, generator: HypothesisGenerator) -> None:
        """测试未知错误类别。"""
        context = ErrorContext(
            error_type="mysterious_error",
            error_message="Something weird happened",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.UNKNOWN_ERROR)

        assert len(hypotheses) > 0

    def test_hypothesis_sorted_by_confidence(self, generator: HypothesisGenerator) -> None:
        """测试假设按置信度排序。"""
        context = ErrorContext(
            error_type="error",
            error_message="test",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR)

        # 检查是否按置信度降序排列
        for i in range(len(hypotheses) - 1):
            assert hypotheses[i].confidence >= hypotheses[i + 1].confidence


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
