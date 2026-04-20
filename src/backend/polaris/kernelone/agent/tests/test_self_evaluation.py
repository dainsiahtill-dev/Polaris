from __future__ import annotations

import pytest
from polaris.kernelone.agent.self_evaluation import (
    CapabilityAssessment,
    SelfEvaluationEngine,
    SelfEvaluator,
)


class TestCapabilityAssessment:
    """Tests for CapabilityAssessment dataclass."""

    def test_capability_assessment_creation(self) -> None:
        """Test creating a CapabilityAssessment with all fields."""
        assessment = CapabilityAssessment(
            task_description="Write a test function",
            confidence=0.8,
            major_challenges=("Ambiguous requirements",),
            needed_help=("Clear requirements",),
            success_probability=0.75,
            potential_failure_modes=("Requirements change",),
            estimated_difficulty="medium",
        )

        assert assessment.task_description == "Write a test function"
        assert assessment.confidence == 0.8
        assert assessment.major_challenges == ("Ambiguous requirements",)
        assert assessment.needed_help == ("Clear requirements",)
        assert assessment.success_probability == 0.75
        assert assessment.potential_failure_modes == ("Requirements change",)
        assert assessment.estimated_difficulty == "medium"

    def test_capability_assessment_defaults(self) -> None:
        """Test creating a CapabilityAssessment with defaults."""
        assessment = CapabilityAssessment(
            task_description="Read a file",
            confidence=0.9,
        )

        assert assessment.major_challenges == ()
        assert assessment.needed_help == ()
        assert assessment.success_probability == 0.5
        assert assessment.potential_failure_modes == ()
        assert assessment.estimated_difficulty == "medium"

    def test_capability_assessment_is_frozen(self) -> None:
        """Test that CapabilityAssessment is immutable."""
        assessment = CapabilityAssessment(
            task_description="Test",
            confidence=0.5,
        )

        with pytest.raises(AttributeError):
            assessment.confidence = 0.9  # type: ignore[misc]


class TestSelfEvaluator:
    """Tests for SelfEvaluator class."""

    @pytest.fixture
    def evaluator(self) -> SelfEvaluator:
        """Create a SelfEvaluator instance."""
        return SelfEvaluator()

    @pytest.mark.asyncio
    async def test_evaluate_capability_read_task(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating a read-only task."""
        assessment = await evaluator.evaluate_capability("Read the file content")

        assert assessment.confidence >= 0.7
        assert assessment.estimated_difficulty == "easy"
        assert assessment.success_probability >= 0.7

    @pytest.mark.asyncio
    async def test_evaluate_capability_write_task(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating a write task."""
        assessment = await evaluator.evaluate_capability("Write a new function to process data")

        assert assessment.confidence >= 0.6
        assert "easy" in assessment.estimated_difficulty or "medium" in assessment.estimated_difficulty

    @pytest.mark.asyncio
    async def test_evaluate_capability_analysis_task(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating an analysis task."""
        assessment = await evaluator.evaluate_capability("Analyze the code structure")

        assert assessment.confidence >= 0.6
        assert assessment.success_probability >= 0.6

    @pytest.mark.asyncio
    async def test_evaluate_capability_complex_task(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating a complex task with challenges."""
        assessment = await evaluator.evaluate_capability("Design a secure architecture for authentication")

        assert len(assessment.major_challenges) > 0
        assert len(assessment.needed_help) > 0
        assert assessment.estimated_difficulty == "hard"
        assert assessment.confidence < 0.7

    @pytest.mark.asyncio
    async def test_evaluate_capability_ambiguous_task(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating an ambiguous task."""
        assessment = await evaluator.evaluate_capability("Maybe try to fix something if possible")

        assert "ambiguous" in str(assessment.major_challenges).lower()
        assert assessment.estimated_difficulty in ("hard", "unknown")
        assert assessment.confidence < 0.6

    @pytest.mark.asyncio
    async def test_evaluate_capability_external_dependency(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating a task with external dependencies."""
        assessment = await evaluator.evaluate_capability("Call the external API to fetch data")

        assert len(assessment.potential_failure_modes) > 0
        assert assessment.estimated_difficulty in ("medium", "hard")
        assert assessment.confidence < 0.8

    @pytest.mark.asyncio
    async def test_evaluate_capability_with_requirements(self, evaluator: SelfEvaluator) -> None:
        """Test evaluating with task requirements dict."""
        assessment = await evaluator.evaluate_capability(
            "Modify the config file",
            task_requirements={"file_path": "/tmp/test.yaml"},
        )

        assert assessment.task_description == "Modify the config file"
        assert assessment.confidence > 0.0

    @pytest.mark.asyncio
    async def test_evaluate_capability_confidence_bounds(self, evaluator: SelfEvaluator) -> None:
        """Test that confidence stays within bounds."""
        # Very easy task
        assessment1 = await evaluator.evaluate_capability("List all files in the directory")
        assert 0.0 <= assessment1.confidence <= 1.0

        # Very hard task
        assessment2 = await evaluator.evaluate_capability(
            "Design a distributed system with security and performance requirements"
        )
        assert 0.0 <= assessment2.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_evaluate_batch(self, evaluator: SelfEvaluator) -> None:
        """Test batch evaluation of multiple tasks."""
        tasks = [
            "Read the configuration file",
            "Write a new test case",
            "Analyze the code quality",
            "Design a secure authentication system",
        ]

        results = await evaluator.evaluate_batch(tasks)

        assert len(results) == 4
        assert all(isinstance(r, CapabilityAssessment) for r in results)
        assert results[0].task_description == "Read the configuration file"
        assert results[3].estimated_difficulty in ("hard", "unknown")

    @pytest.mark.asyncio
    async def test_evaluate_batch_empty(self, evaluator: SelfEvaluator) -> None:
        """Test batch evaluation with empty list."""
        results = await evaluator.evaluate_batch([])

        assert len(results) == 0


class TestSelfEvaluationEngine:
    """Tests for SelfEvaluationEngine class."""

    @pytest.fixture
    def engine(self) -> SelfEvaluationEngine:
        """Create a SelfEvaluationEngine instance."""
        return SelfEvaluationEngine()

    @pytest.mark.asyncio
    async def test_assess_task(self, engine: SelfEvaluationEngine) -> None:
        """Test single task assessment."""
        assessment = await engine.assess_task("List the files in the directory")

        assert isinstance(assessment, CapabilityAssessment)
        assert assessment.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_assess_batch(self, engine: SelfEvaluationEngine) -> None:
        """Test batch assessment."""
        tasks = [
            "Read the file",
            "Write code",
            "Analyze structure",
        ]

        results = await engine.assess_batch(tasks)

        assert len(results) == 3
        assert all(isinstance(r, CapabilityAssessment) for r in results)

    @pytest.mark.asyncio
    async def test_can_complete_true(self, engine: SelfEvaluationEngine) -> None:
        """Test can_complete returns True for easy tasks."""
        can_do, assessment = await engine.can_complete("Read the file content", threshold=0.6)

        assert can_do is True
        assert assessment.confidence >= 0.6

    @pytest.mark.asyncio
    async def test_can_complete_false(self, engine: SelfEvaluationEngine) -> None:
        """Test can_complete returns False for hard tasks."""
        can_do, assessment = await engine.can_complete(
            "Design a secure multi-tenant distributed system with zero-downtime deployment"
        )

        assert can_do is False
        assert assessment.confidence < 0.6

    @pytest.mark.asyncio
    async def test_can_complete_custom_threshold(self, engine: SelfEvaluationEngine) -> None:
        """Test can_complete with custom threshold."""
        # With high threshold
        can_do_high, _ = await engine.can_complete("List the files", threshold=0.9)
        # With low threshold
        can_do_low, _ = await engine.can_complete("List the files", threshold=0.3)

        assert can_do_high is False
        assert can_do_low is True


class TestBoundaryDetection:
    """Tests for boundary detection scenarios."""

    @pytest.fixture
    def evaluator(self) -> SelfEvaluator:
        """Create a SelfEvaluator instance."""
        return SelfEvaluator()

    @pytest.mark.asyncio
    async def test_file_operation_boundary(self, evaluator: SelfEvaluator) -> None:
        """Test boundary detection for file operations."""
        assessment = await evaluator.evaluate_capability("Create a new file at a specific path")

        assert assessment.confidence > 0.5
        assert assessment.estimated_difficulty in ("easy", "medium")

    @pytest.mark.asyncio
    async def test_testing_boundary(self, evaluator: SelfEvaluator) -> None:
        """Test boundary detection for testing tasks."""
        assessment = await evaluator.evaluate_capability("Run the test suite and generate coverage report")

        assert assessment.confidence > 0.3
        assert "test" in assessment.task_description.lower()

    @pytest.mark.asyncio
    async def test_review_boundary(self, evaluator: SelfEvaluator) -> None:
        """Test boundary detection for code review tasks."""
        assessment = await evaluator.evaluate_capability("Review the code for potential issues")

        assert assessment.confidence >= 0.6
        assert assessment.estimated_difficulty in ("easy", "medium", "hard")

    @pytest.mark.asyncio
    async def test_deployment_boundary(self, evaluator: SelfEvaluator) -> None:
        """Test boundary detection for deployment tasks."""
        assessment = await evaluator.evaluate_capability("Deploy the application to production")

        assert "external" in str(assessment.major_challenges).lower() or "hard" in assessment.estimated_difficulty
        assert assessment.confidence < 0.7
