"""Tests for Debug Strategy Models - 数据模型测试。"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    DebugStep,
    ErrorClassification,
    ErrorContext,
    Evidence,
    Hypothesis,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import (
    DebugPhase,
    DebugStrategy,
    ErrorCategory,
)


class TestErrorContext:
    """ErrorContext测试。"""

    def test_create_error_context(self) -> None:
        """测试创建错误上下文。"""
        context = ErrorContext(
            error_type="test_error",
            error_message="Test message",
            stack_trace="Traceback...",
        )

        assert context.error_type == "test_error"
        assert context.error_message == "Test message"
        assert context.stack_trace == "Traceback..."

    def test_error_context_defaults(self) -> None:
        """测试错误上下文默认值。"""
        context = ErrorContext(
            error_type="test",
            error_message="test",
            stack_trace="test",
        )

        assert context.recent_changes == []
        assert context.environment == {}
        assert context.previous_attempts == []
        assert context.file_path is None
        assert context.line_number is None

    def test_error_context_from_dict(self) -> None:
        """测试从字典创建。"""
        data = {
            "error_type": "runtime_error",
            "error_message": "Something failed",
            "stack_trace": "Traceback...",
            "file_path": "test.py",
            "line_number": 42,
        }

        context = ErrorContext.from_dict(data)

        assert context.error_type == "runtime_error"
        assert context.file_path == "test.py"
        assert context.line_number == 42

    def test_error_context_to_dict(self) -> None:
        """测试转换为字典。"""
        context = ErrorContext(
            error_type="test",
            error_message="test",
            stack_trace="test",
            file_path="test.py",
            line_number=1,
        )

        data = context.to_dict()

        assert data["error_type"] == "test"
        assert data["file_path"] == "test.py"
        assert data["line_number"] == 1

    def test_error_context_roundtrip(self) -> None:
        """测试字典往返。"""
        original = ErrorContext(
            error_type="test",
            error_message="test message",
            stack_trace="trace",
            recent_changes=["change1"],
            environment={"KEY": "VALUE"},
            file_path="test.py",
            line_number=10,
        )

        data = original.to_dict()
        restored = ErrorContext.from_dict(data)

        assert restored.error_type == original.error_type
        assert restored.recent_changes == original.recent_changes
        assert restored.environment == original.environment


class TestDebugStep:
    """DebugStep测试。"""

    def test_create_debug_step(self) -> None:
        """测试创建调试步骤。"""
        step = DebugStep(
            phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
            description="Investigate the error",
            commands=["read_file test.py"],
            expected_outcome="Understand the error",
        )

        assert step.phase == DebugPhase.ROOT_CAUSE_INVESTIGATION
        assert step.description == "Investigate the error"
        assert step.commands == ["read_file test.py"]
        assert step.expected_outcome == "Understand the error"

    def test_debug_step_defaults(self) -> None:
        """测试调试步骤默认值。"""
        step = DebugStep(
            phase=DebugPhase.IMPLEMENTATION,
            description="Fix it",
            commands=["fix"],
            expected_outcome="Fixed",
        )

        assert step.rollback_commands == []
        assert step.defense_checkpoints == []
        assert step.timeout_seconds == 60


class TestDebugPlan:
    """DebugPlan测试。"""

    def test_create_debug_plan(self) -> None:
        """测试创建调试计划。"""
        steps = [
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="Step 1",
                commands=["cmd1"],
                expected_outcome="Result 1",
            ),
        ]

        plan = DebugPlan(
            plan_id="test_plan_001",
            strategy=DebugStrategy.TRACE_BACKWARD,
            steps=steps,
            estimated_time=30,
            rollback_plan="Rollback to previous version",
        )

        assert plan.plan_id == "test_plan_001"
        assert plan.strategy == DebugStrategy.TRACE_BACKWARD
        assert len(plan.steps) == 1
        assert plan.estimated_time == 30
        assert plan.rollback_plan == "Rollback to previous version"

    def test_debug_plan_defaults(self) -> None:
        """测试调试计划默认值。"""
        plan = DebugPlan(
            plan_id="test",
            strategy=DebugStrategy.PATTERN_MATCH,
            steps=[],
            estimated_time=10,
            rollback_plan="rollback",
        )

        assert plan.success_criteria == []
        assert plan.failure_criteria == []


class TestHypothesis:
    """Hypothesis测试。"""

    def test_create_hypothesis(self) -> None:
        """测试创建假设。"""
        hypothesis = Hypothesis(
            hypothesis_id="hyp_001",
            description="The error is caused by missing validation",
            confidence=0.8,
            test_approach="Add validation and test",
            validation_criteria=["No error", "Valid input passes"],
        )

        assert hypothesis.hypothesis_id == "hyp_001"
        assert hypothesis.confidence == 0.8
        assert len(hypothesis.validation_criteria) == 2

    def test_hypothesis_defaults(self) -> None:
        """测试假设默认值。"""
        hypothesis = Hypothesis(
            hypothesis_id="hyp",
            description="test",
            confidence=0.5,
            test_approach="test",
            validation_criteria=[],
        )

        assert hypothesis.related_patterns == []


class TestEvidence:
    """Evidence测试。"""

    def test_create_evidence(self) -> None:
        """测试创建证据。"""
        evidence = Evidence(
            evidence_id="evid_001",
            source="stack_trace",
            content="Traceback...",
            timestamp=1234567890.0,
        )

        assert evidence.evidence_id == "evid_001"
        assert evidence.source == "stack_trace"
        assert evidence.content == "Traceback..."
        assert evidence.timestamp == 1234567890.0

    def test_evidence_defaults(self) -> None:
        """测试证据默认值。"""
        evidence = Evidence(
            evidence_id="evid",
            source="code",
            content="code",
            timestamp=0.0,
        )

        assert evidence.confidence == 1.0
        assert evidence.metadata == {}


class TestErrorClassification:
    """ErrorClassification测试。"""

    def test_create_classification(self) -> None:
        """测试创建错误分类。"""
        classification = ErrorClassification(
            category=ErrorCategory.LOGIC_ERROR,
            severity="high",
            root_cause_likely="Missing validation",
        )

        assert classification.category == ErrorCategory.LOGIC_ERROR
        assert classification.severity == "high"
        assert classification.root_cause_likely == "Missing validation"
        assert classification.debug_plan is None

    def test_classification_with_plan(self) -> None:
        """测试带计划的分类。"""
        plan = DebugPlan(
            plan_id="plan_001",
            strategy=DebugStrategy.TRACE_BACKWARD,
            steps=[],
            estimated_time=30,
            rollback_plan="rollback",
        )

        classification = ErrorClassification(
            category=ErrorCategory.SYNTAX_ERROR,
            severity="medium",
            root_cause_likely="Typo",
            debug_plan=plan,
            suggested_strategies=[DebugStrategy.PATTERN_MATCH],
        )

        assert classification.debug_plan is not None
        assert classification.debug_plan.plan_id == "plan_001"
        assert DebugStrategy.PATTERN_MATCH in classification.suggested_strategies


class TestEnums:
    """枚举类型测试。"""

    def test_debug_phase_values(self) -> None:
        """测试DebugPhase值。"""
        assert DebugPhase.ROOT_CAUSE_INVESTIGATION.name == "ROOT_CAUSE_INVESTIGATION"
        assert DebugPhase.PATTERN_ANALYSIS.name == "PATTERN_ANALYSIS"
        assert DebugPhase.HYPOTHESIS_TESTING.name == "HYPOTHESIS_TESTING"
        assert DebugPhase.IMPLEMENTATION.name == "IMPLEMENTATION"

    def test_debug_strategy_values(self) -> None:
        """测试DebugStrategy值。"""
        assert DebugStrategy.TRACE_BACKWARD.value == "trace_backward"
        assert DebugStrategy.PATTERN_MATCH.value == "pattern_match"
        assert DebugStrategy.BINARY_SEARCH.value == "binary_search"
        assert DebugStrategy.CONDITIONAL_WAIT.value == "conditional_wait"
        assert DebugStrategy.DEFENSE_IN_DEPTH.value == "defense_in_depth"

    def test_error_category_values(self) -> None:
        """测试ErrorCategory值。"""
        assert ErrorCategory.SYNTAX_ERROR.value == "syntax_error"
        assert ErrorCategory.RUNTIME_ERROR.value == "runtime_error"
        assert ErrorCategory.LOGIC_ERROR.value == "logic_error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
