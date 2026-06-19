"""Tests for Orchestration layer Pydantic DTOs (AAA pattern).

Verifies:
    - Architect schemas validation and serialization
    - PM schemas validation and serialization
    - QA schemas validation and serialization
    - Frozen immutability
    - Round-trip serialization
"""

from __future__ import annotations

import pytest
from polaris.application.orchestration.architect_schemas import (
    ArchitectContextSchema,
    ArchitectDesignConfig,
    ArchitectDesignLifecycleResult,
    BlueprintResultSchema,
    DesignResultSchema,
    RequirementsInput,
)
from polaris.application.orchestration.pm_schemas import (
    PmBlockedPolicyResult,
    PmDispatchResult,
    PmIterationContext,
    PmIterationResult,
    PmPlanningResult,
)
from polaris.application.orchestration.qa_schemas import (
    QaAuditConfig,
    QaAuditLifecycleResult,
    QaAuditPlan,
    QaReviewResult,
    QaVerdictQuery,
    QaVerdictResult,
)
from pydantic import ValidationError

# =============================================================================
# Architect Schema Tests
# =============================================================================


class TestDesignResultSchema:
    """Tests for DesignResultSchema."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = DesignResultSchema(
            design_id="d-1",
            doc_type="requirements",
            title="Auth Design",
            status="completed",
        )

        # Assert
        assert result.design_id == "d-1"
        assert result.doc_type == "requirements"
        assert result.status == "completed"

    def test_invalid_doc_type_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            DesignResultSchema(
                design_id="d-1",
                doc_type="invalid_type",  # type: ignore[arg-type]
                title="Test",
                status="completed",
            )

    def test_frozen_immutable(self) -> None:
        # Arrange
        result = DesignResultSchema(design_id="d-1", doc_type="adr", title="Test", status="completed")

        # Act & Assert
        with pytest.raises(ValidationError):
            result.design_id = "changed"  # type: ignore[misc]


class TestBlueprintResultSchema:
    """Tests for BlueprintResultSchema."""

    def test_create_with_defaults(self) -> None:
        # Arrange & Act
        result = BlueprintResultSchema(blueprint_id="bp-1", summary="Test blueprint")

        # Assert
        assert result.status == "ready"
        assert result.design_ids == []
        assert result.recommendation_paths == []


class TestArchitectDesignLifecycleResult:
    """Tests for ArchitectDesignLifecycleResult."""

    def test_create_with_designs(self) -> None:
        # Arrange
        designs = [
            DesignResultSchema(
                design_id="d-1",
                doc_type="requirements",
                title="Test",
                status="completed",
            )
        ]

        # Act
        result = ArchitectDesignLifecycleResult(success=True, workspace="/test", designs=designs)

        # Assert
        assert result.success is True
        assert len(result.designs) == 1


class TestArchitectDesignConfig:
    """Tests for ArchitectDesignConfig."""

    def test_create_with_defaults(self) -> None:
        # Arrange & Act
        config = ArchitectDesignConfig(workspace="/test")

        # Assert
        assert config.docs_dir == "docs/product"
        assert config.objective == ""


class TestArchitectContextSchema:
    """Tests for ArchitectContextSchema."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        ctx = ArchitectContextSchema(workspace="/test", objective="Design auth")

        # Assert
        assert ctx.workspace == "/test"
        assert ctx.objective == "Design auth"


class TestRequirementsInput:
    """Tests for RequirementsInput."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        req = RequirementsInput(goal="Implement OAuth")

        # Assert
        assert req.goal == "Implement OAuth"
        assert req.in_scope == []
        assert req.out_of_scope == []


# =============================================================================
# PM Schema Tests
# =============================================================================


class TestPmPlanningResult:
    """Tests for PmPlanningResult."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = PmPlanningResult(exit_code=0)

        # Assert
        assert result.exit_code == 0
        assert result.task_count == 0


class TestPmDispatchResult:
    """Tests for PmDispatchResult."""

    def test_create_with_defaults(self) -> None:
        # Arrange & Act
        result = PmDispatchResult()

        # Assert
        assert result.used is False
        assert result.exit_code == 0
        assert result.error == ""


class TestPmBlockedPolicyResult:
    """Tests for PmBlockedPolicyResult."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = PmBlockedPolicyResult(decision="continue", exit_code=0)

        # Assert
        assert result.decision == "continue"
        assert result.exit_code == 0

    def test_invalid_decision_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            PmBlockedPolicyResult(
                decision="invalid_decision",  # type: ignore[arg-type]
                exit_code=0,
            )


class TestPmIterationContext:
    """Tests for PmIterationContext."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        ctx = PmIterationContext(workspace="/test")

        # Assert
        assert ctx.iteration == 1
        assert ctx.dispatch_enabled is True


class TestPmIterationResult:
    """Tests for PmIterationResult."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = PmIterationResult(exit_code=0, run_id="run-1", iteration=1, status="completed")

        # Assert
        assert result.exit_code == 0
        assert result.run_id == "run-1"
        assert result.status == "completed"

    def test_invalid_status_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            PmIterationResult(
                exit_code=0,
                run_id="run-1",
                iteration=1,
                status="invalid_status",  # type: ignore[arg-type]
            )


# =============================================================================
# QA Schema Tests
# =============================================================================


class TestQaReviewResult:
    """Tests for QaReviewResult."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = QaReviewResult(review_id="r-1", target="TASK-123", status="completed")

        # Assert
        assert result.review_id == "r-1"
        assert result.issue_count == 0


class TestQaVerdictResult:
    """Tests for QaVerdictResult."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = QaVerdictResult(verdict="PASS", verdict_id="v-1", summary="All good")

        # Assert
        assert result.verdict == "PASS"
        assert result.score == 0.0

    def test_invalid_verdict_raises(self) -> None:
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            QaVerdictResult(
                verdict="INVALID",  # type: ignore[arg-type]
                verdict_id="v-1",
                summary="Test",
            )


class TestQaAuditLifecycleResult:
    """Tests for QaAuditLifecycleResult."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        result = QaAuditLifecycleResult(success=True, task_id="TASK-123", workspace="/test")

        # Assert
        assert result.success is True
        assert result.review is None
        assert result.verdict is None


class TestQaAuditConfig:
    """Tests for QaAuditConfig."""

    def test_create_with_defaults(self) -> None:
        # Arrange & Act
        config = QaAuditConfig(workspace="/test")

        # Assert
        assert config.auto_audit is True
        assert config.min_coverage == 0.7


class TestQaAuditPlan:
    """Tests for QaAuditPlan."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        plan = QaAuditPlan(task_id="TASK-123", workspace="/test")

        # Assert
        assert plan.task_id == "TASK-123"
        assert plan.evidence_paths == []


class TestQaVerdictQuery:
    """Tests for QaVerdictQuery."""

    def test_create_with_required_fields(self) -> None:
        # Arrange & Act
        query = QaVerdictQuery(ok=True, status="completed")

        # Assert
        assert query.ok is True
        assert query.verdict is None


# =============================================================================
# Round-trip serialization tests
# =============================================================================


class TestRoundTripSerialization:
    """Tests for round-trip serialization of all schemas."""

    def test_design_result_roundtrip(self) -> None:
        # Arrange
        original = DesignResultSchema(
            design_id="d-1",
            doc_type="adr",
            title="Test",
            status="completed",
            content_length=100,
        )

        # Act
        data = original.model_dump()
        restored = DesignResultSchema.model_validate(data)

        # Assert
        assert restored.design_id == original.design_id
        assert restored.doc_type == original.doc_type
        assert restored.status == original.status

    def test_pm_iteration_result_roundtrip(self) -> None:
        # Arrange
        original = PmIterationResult(exit_code=0, run_id="run-1", iteration=1, status="completed")

        # Act
        data = original.model_dump()
        restored = PmIterationResult.model_validate(data)

        # Assert
        assert restored.exit_code == original.exit_code
        assert restored.run_id == original.run_id
        assert restored.status == original.status

    def test_qa_verdict_result_roundtrip(self) -> None:
        # Arrange
        original = QaVerdictResult(
            verdict="PASS",
            verdict_id="v-1",
            summary="All tests pass",
            score=0.95,
        )

        # Act
        data = original.model_dump()
        restored = QaVerdictResult.model_validate(data)

        # Assert
        assert restored.verdict == original.verdict
        assert restored.score == original.score
