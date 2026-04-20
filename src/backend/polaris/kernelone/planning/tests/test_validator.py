"""Tests for the planning module - validator, models, and builder."""

from __future__ import annotations

import pytest
from polaris.kernelone.planning import (
    Plan,
    PlanBuilder,
    PlanStep,
    PlanStepBuilder,
    StructuralPlanValidator,
    ValidationResult,
    Violation,
    ViolationSeverity,
)


class TestStructuralPlanValidator:
    """Tests for StructuralPlanValidator."""

    def test_validate_empty_plan_returns_error(self) -> None:
        """Empty plan should return validation error."""
        plan = Plan(steps=(), max_duration=None)
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        assert len(result.violations) == 1
        assert result.violations[0].rule_id == "EMPTY_PLAN"

    def test_validate_valid_plan_returns_pass(self) -> None:
        """Plan with valid steps should pass."""
        plan = Plan(
            steps=(
                PlanStep(id="step1", description="First step"),
                PlanStep(id="step2", description="Second step", depends_on=("step1",)),
            ),
            max_duration=300,
        )
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is True
        assert len(result.violations) == 0

    def test_validate_invalid_dependency_returns_error(self) -> None:
        """Plan with invalid dependency should return error."""
        plan = Plan(
            steps=(
                PlanStep(id="step1", description="First step"),
                PlanStep(id="step2", description="Second step", depends_on=("nonexistent",)),
            ),
            max_duration=None,
        )
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        violation_ids = {v.rule_id for v in result.violations}
        assert "INVALID_DEPENDENCY" in violation_ids

    def test_validate_cycle_detection(self) -> None:
        """Plan with circular dependency should be detected."""
        plan = Plan(
            steps=(
                PlanStep(id="step1", description="First step", depends_on=("step3",)),
                PlanStep(id="step2", description="Second step", depends_on=("step1",)),
                PlanStep(id="step3", description="Third step", depends_on=("step2",)),
            ),
            max_duration=None,
        )
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is False
        violation_ids = {v.rule_id for v in result.violations}
        assert "CYCLE_DETECTED" in violation_ids

    def test_validate_duration_exceeds_warning(self) -> None:
        """Step duration exceeding plan max should return warning."""
        plan = Plan(
            steps=(PlanStep(id="step1", description="Long step", estimated_duration=500),),
            max_duration=300,
        )
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert result.is_valid is True  # Warnings don't make invalid
        warning_ids = {v.rule_id for v in result.violations if v.severity == ViolationSeverity.WARNING}
        assert "EXCEEDS_DURATION" in warning_ids

    def test_generate_suggestions_empty_plan(self) -> None:
        """Suggestions should be generated for empty plan."""
        plan = Plan(steps=(), max_duration=None)
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert len(result.suggestions) > 0
        assert any("Add at least one step" in s for s in result.suggestions)

    def test_generate_suggestions_cycle(self) -> None:
        """Suggestions should be generated for cycles."""
        plan = Plan(
            steps=(
                PlanStep(id="step1", description="First step", depends_on=("step2",)),
                PlanStep(id="step2", description="Second step", depends_on=("step1",)),
            ),
            max_duration=None,
        )
        validator = StructuralPlanValidator()
        result = validator.validate(plan)
        assert len(result.suggestions) > 0


class TestValidationResultFormatting:
    """Tests for ValidationResult formatting methods."""

    def test_format_errors_empty_plan(self) -> None:
        """Format errors for empty plan."""
        result = ValidationResult(
            is_valid=False,
            violations=(
                Violation(
                    severity=ViolationSeverity.ERROR,
                    rule_id="EMPTY_PLAN",
                    message="Plan has no steps",
                    location=None,
                ),
            ),
            suggestions=("Add at least one step to the plan",),
        )
        formatted = result.format_errors()
        assert "validation failed" in formatted.lower()
        assert "EMPTY_PLAN" in formatted
        assert "Add at least one step" in formatted

    def test_format_errors_with_location(self) -> None:
        """Format errors including location."""
        result = ValidationResult(
            is_valid=False,
            violations=(
                Violation(
                    severity=ViolationSeverity.ERROR,
                    rule_id="INVALID_DEPENDENCY",
                    message="Step step2 depends on non-existent step foo",
                    location="step2",
                ),
            ),
            suggestions=(),
        )
        formatted = result.format_errors()
        assert "at step2" in formatted
        assert "INVALID_DEPENDENCY" in formatted

    def test_format_warnings(self) -> None:
        """Format warnings correctly."""
        result = ValidationResult(
            is_valid=True,
            violations=(
                Violation(
                    severity=ViolationSeverity.WARNING,
                    rule_id="EXCEEDS_DURATION",
                    message="Step step1 exceeds max duration",
                    location="step1",
                ),
            ),
            suggestions=(),
        )
        formatted = result.format_warnings()
        assert "warning" in formatted.lower()
        assert "EXCEEDS_DURATION" in formatted

    def test_format_valid_plan(self) -> None:
        """Format valid plan returns pass message."""
        result = ValidationResult(
            is_valid=True,
            violations=(),
            suggestions=(),
        )
        formatted = result.format_errors()
        assert "passed" in formatted.lower()


class TestPlanBuilder:
    """Tests for PlanBuilder DSL."""

    def test_build_simple_plan(self) -> None:
        """Build a simple plan with single step."""
        plan = PlanBuilder().step("read", description="Read file").build()
        assert len(plan.steps) == 1
        assert plan.steps[0].id == "read"
        assert plan.steps[0].description == "Read file"

    def test_build_plan_with_dependencies(self) -> None:
        """Build plan with step dependencies."""
        plan = (
            PlanBuilder()
            .step("read", description="Read file")
            .step("edit", description="Edit file", depends_on=["read"])
            .build()
        )
        assert len(plan.steps) == 2
        assert plan.steps[1].depends_on == ("read",)

    def test_build_plan_with_duration(self) -> None:
        """Build plan with estimated duration."""
        plan = (
            PlanBuilder()
            .step("read", description="Read file", estimated_duration=10)
            .step("edit", description="Edit file", depends_on=["read"], estimated_duration=20)
            .build()
        )
        assert plan.estimated_duration == 30
        assert plan.max_duration is None

    def test_build_plan_with_max_duration(self) -> None:
        """Build plan with max duration constraint."""
        plan = PlanBuilder().step("read", description="Read").max_duration(300).build()
        assert plan.max_duration == 300

    def test_build_plan_with_metadata(self) -> None:
        """Build plan with metadata."""
        plan = PlanBuilder().step("read", description="Read file").metadata(author="test", version=1).build()
        assert plan.metadata["author"] == "test"
        assert plan.metadata["version"] == 1

    def test_build_empty_plan_raises_error(self) -> None:
        """Building plan with no steps should raise ValueError."""
        builder = PlanBuilder()
        with pytest.raises(ValueError, match="at least one step"):
            builder.build()

    def test_method_chaining(self) -> None:
        """Builder should support fluent method chaining."""
        plan = (
            PlanBuilder()
            .step("step1", description="First")
            .step("step2", description="Second", depends_on=["step1"])
            .step("step3", description="Third", depends_on=["step2"])
            .max_duration(300)
            .metadata(key="value")
            .build()
        )
        assert len(plan.steps) == 3
        assert plan.max_duration == 300
        assert plan.metadata["key"] == "value"


class TestPlanStepBuilder:
    """Tests for PlanStepBuilder."""

    def test_build_step_with_all_options(self) -> None:
        """Build step with all options."""
        step = (
            PlanStepBuilder("read")
            .description("Read configuration file")
            .depends_on("validate")
            .estimated_duration(15)
            .metadata(format="json")
            .build()
        )
        assert step.id == "read"
        assert step.description == "Read configuration file"
        assert step.depends_on == ("validate",)
        assert step.estimated_duration == 15
        assert step.metadata["format"] == "json"

    def test_build_step_with_multiple_dependencies(self) -> None:
        """Build step with multiple dependencies."""
        step = PlanStepBuilder("merge").depends_on(["validate", "build", "test"]).build()
        assert step.depends_on == ("validate", "build", "test")

    def test_build_step_empty_id_raises_error(self) -> None:
        """Building step with empty ID should raise ValueError."""
        builder = PlanStepBuilder("")
        with pytest.raises(ValueError, match="Step ID cannot be empty"):
            builder.build()


class TestPlanModel:
    """Tests for Plan and PlanStep models."""

    def test_plan_is_frozen(self) -> None:
        """Plan should be immutable (frozen)."""
        plan = Plan(steps=(), max_duration=None)
        with pytest.raises(AttributeError):
            plan.steps = (PlanStep(id="test", description="test"),)  # type: ignore[misc]

    def test_plan_step_is_frozen(self) -> None:
        """PlanStep should be immutable (frozen)."""
        step = PlanStep(id="test", description="test")
        with pytest.raises(AttributeError):
            step.description = "changed"  # type: ignore[misc]

    def test_plan_with_all_fields(self) -> None:
        """Plan with all fields specified."""
        steps = (PlanStep(id="s1", description="Step 1"),)
        plan = Plan(
            steps=steps,
            max_duration=600,
            estimated_duration=300,
            metadata={"author": "test"},
        )
        assert plan.steps == steps
        assert plan.max_duration == 600
        assert plan.estimated_duration == 300
        assert plan.metadata["author"] == "test"
