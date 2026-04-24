"""Tests for polaris.cells.roles.adapters.internal.schemas.ce_schema."""

from __future__ import annotations

import pytest
from polaris.cells.roles.adapters.internal.schemas.ce_schema import (
    BlueprintOutput,
    ComplexityAssessment,
    ConstructionPlan,
    DependencyInfo,
    RiskFlag,
)
from pydantic import ValidationError


class TestComplexityAssessment:
    def test_valid_assessment(self) -> None:
        assessment = ComplexityAssessment(
            level="medium",
            estimated_files=10,
            estimated_lines=500,
            technical_approach="Implement using standard MVC pattern with dependency injection",
        )
        assert assessment.level == "medium"
        assert assessment.estimated_files == 10

    def test_files_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ComplexityAssessment(
                level="high",
                estimated_files=200,  # > 100
                estimated_lines=100,
                technical_approach="x" * 60,
            )

    def test_lines_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ComplexityAssessment(
                level="high",
                estimated_files=10,
                estimated_lines=20000,  # > 10000
                technical_approach="x" * 60,
            )


class TestConstructionPlan:
    def test_valid_plan(self) -> None:
        plan = ConstructionPlan(
            preparation=["Install dependencies"],
            implementation=["Create module", "Write tests"],
            verification=["Run pytest"],
        )
        assert len(plan.implementation) == 2
        assert len(plan.verification) == 1

    def test_empty_implementation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConstructionPlan(
                preparation=[],
                implementation=[],  # min 1 required
                verification=["Verify"],
            )


class TestDependencyInfo:
    def test_defaults(self) -> None:
        deps = DependencyInfo()
        assert deps.required == []
        assert deps.concurrent_safe is True
        assert deps.external_libs == []

    def test_with_dependencies(self) -> None:
        deps = DependencyInfo(
            required=["pytest", "black"],
            external_libs=["pydantic"],
        )
        assert "pytest" in deps.required
        assert "pydantic" in deps.external_libs


class TestRiskFlag:
    def test_valid_warning(self) -> None:
        flag = RiskFlag(level="warning", description="Database connection may timeout")
        assert flag.level == "warning"

    def test_error_without_mitigation_allowed_at_flag_level(self) -> None:
        """RiskFlag itself allows error level without mitigation - validation is at BlueprintOutput level."""
        flag = RiskFlag(level="error", description="Critical failure that needs attention")
        assert flag.level == "error"
        assert flag.mitigation is None

    def test_error_with_mitigation(self) -> None:
        flag = RiskFlag(
            level="error",
            description="Critical failure",
            mitigation="Add retry logic with exponential backoff",
        )
        assert flag.mitigation is not None


class TestBlueprintOutput:
    def test_empty_blueprint(self) -> None:
        bp = BlueprintOutput()
        assert bp.blueprint_version == "1.0"
        assert bp.blueprint_id is None
        assert bp.analysis is None
        assert bp.construction_plan is None

    def test_with_analysis_and_plan(self) -> None:
        bp = BlueprintOutput(
            analysis=ComplexityAssessment(
                level="low",
                estimated_files=5,
                estimated_lines=200,
                technical_approach="Implement using standard patterns and proven frameworks for this module",
            ),
            construction_plan=ConstructionPlan(
                implementation=["Step 1", "Step 2"],
                verification=["Test"],
            ),
        )
        assert bp.analysis is not None
        assert bp.construction_plan is not None
        assert bp.analysis.level == "low"

    def test_scope_for_apply_validation(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            BlueprintOutput(scope_for_apply=["/absolute/path"])
        assert "relative" in str(exc_info.value)

    def test_scope_for_apply_with_parent_dir_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            BlueprintOutput(scope_for_apply=["../dangerous"])
        assert "relative" in str(exc_info.value)

    def test_risk_flags_validation(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            BlueprintOutput(risk_flags=[RiskFlag(level="error", description="Critical issue without mitigation")])
        assert "mitigation" in str(exc_info.value)

    def test_valid_blueprint_with_risk_flags(self) -> None:
        bp = BlueprintOutput(
            risk_flags=[
                RiskFlag(
                    level="warning",
                    description="Legacy code may cause issues",
                ),
                RiskFlag(
                    level="error",
                    description="API deprecation",
                    mitigation="Migrate to new API before deadline",
                ),
            ]
        )
        assert len(bp.risk_flags) == 2

    def test_inherits_base_tool_enabled_output(self) -> None:
        bp = BlueprintOutput(
            tool_calls=[],
            is_complete=True,
            next_action="respond",
        )
        assert bp.tool_calls == []
        assert bp.is_complete is True
