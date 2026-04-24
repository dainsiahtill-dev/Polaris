"""Tests for polaris.cells.roles.kernel.internal.debug_strategy.models."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    DebugStep,
    DefenseCheckpoint,
    ErrorClassification,
    ErrorContext,
    Evidence,
    Hypothesis,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import (
    DebugPhase,
    DebugStrategy,
    DefenseLayer,
    ErrorCategory,
)


class TestErrorContext:
    def test_defaults(self) -> None:
        ctx = ErrorContext(error_type="test", error_message="msg", stack_trace="trace")
        assert ctx.error_type == "test"
        assert ctx.error_message == "msg"
        assert ctx.stack_trace == "trace"
        assert ctx.recent_changes == []
        assert ctx.environment == {}
        assert ctx.previous_attempts == []
        assert ctx.file_path is None
        assert ctx.line_number is None
        assert ctx.tool_name is None

    def test_from_dict(self) -> None:
        data = {
            "error_type": "runtime",
            "error_message": "boom",
            "stack_trace": "trace",
            "file_path": "/tmp/f.py",
            "line_number": 42,
        }
        ctx = ErrorContext.from_dict(data)
        assert ctx.error_type == "runtime"
        assert ctx.error_message == "boom"
        assert ctx.file_path == "/tmp/f.py"
        assert ctx.line_number == 42

    def test_to_dict(self) -> None:
        ctx = ErrorContext(error_type="test", error_message="msg", stack_trace="trace")
        d = ctx.to_dict()
        assert d["error_type"] == "test"
        assert d["error_message"] == "msg"
        assert d["stack_trace"] == "trace"


class TestDefenseCheckpoint:
    def test_fields(self) -> None:
        cp = DefenseCheckpoint(
            layer=DefenseLayer.INPUT_VALIDATION,
            description="check input",
            validation_command="cmd",
            expected_result="ok",
            failure_action="abort",
        )
        assert cp.layer == DefenseLayer.INPUT_VALIDATION
        assert cp.description == "check input"


class TestDebugStep:
    def test_defaults(self) -> None:
        step = DebugStep(
            phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
            description="investigate",
            commands=["cmd"],
            expected_outcome="found",
        )
        assert step.phase == DebugPhase.ROOT_CAUSE_INVESTIGATION
        assert step.rollback_commands == []
        assert step.defense_checkpoints == []
        assert step.timeout_seconds == 60


class TestDebugPlan:
    def test_fields(self) -> None:
        plan = DebugPlan(
            plan_id="p1",
            strategy=DebugStrategy.TRACE_BACKWARD,
            steps=[],
            estimated_time=10,
            rollback_plan="revert",
        )
        assert plan.plan_id == "p1"
        assert plan.strategy == DebugStrategy.TRACE_BACKWARD
        assert plan.success_criteria == []
        assert plan.failure_criteria == []


class TestHypothesis:
    def test_fields(self) -> None:
        h = Hypothesis(
            hypothesis_id="h1",
            description="test",
            confidence=0.8,
            test_approach="unit test",
            validation_criteria=["pass"],
        )
        assert h.confidence == 0.8
        assert h.related_patterns == []


class TestEvidence:
    def test_defaults(self) -> None:
        ev = Evidence(evidence_id="e1", source="log", content="data", timestamp=1.0)
        assert ev.confidence == 1.0
        assert ev.metadata == {}


class TestErrorClassification:
    def test_defaults(self) -> None:
        ec = ErrorClassification(
            category=ErrorCategory.RUNTIME_ERROR,
            severity="high",
            root_cause_likely="null pointer",
        )
        assert ec.debug_plan is None
        assert ec.related_patterns == []
        assert ec.suggested_strategies == []
