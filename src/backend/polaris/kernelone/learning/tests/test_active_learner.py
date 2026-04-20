"""Tests for the ActiveLearner class."""

from __future__ import annotations

import pytest
from polaris.kernelone.learning.active_learner import (
    ActiveLearner,
    ErrorPattern,
    LearningResult,
)


class TestErrorPattern:
    """Test the ErrorPattern dataclass."""

    def test_error_pattern_creation(self) -> None:
        """Test creating an ErrorPattern."""
        pattern = ErrorPattern(
            pattern_id="test_pattern_1",
            root_cause="Null pointer exception",
            avoidance_strategy="Add null checks",
            learned_knowledge="Always validate inputs",
            occurrence_count=1,
            last_seen="2026-04-06T00:00:00Z",
        )

        assert pattern.pattern_id == "test_pattern_1"
        assert pattern.root_cause == "Null pointer exception"
        assert pattern.avoidance_strategy == "Add null checks"
        assert pattern.learned_knowledge == "Always validate inputs"
        assert pattern.occurrence_count == 1
        assert pattern.last_seen == "2026-04-06T00:00:00Z"

    def test_error_pattern_immutable(self) -> None:
        """Test that ErrorPattern is immutable (frozen)."""
        pattern = ErrorPattern(
            pattern_id="test_pattern_1",
            root_cause="Test",
            avoidance_strategy="Test",
            learned_knowledge="Test",
        )

        with pytest.raises(AttributeError):
            pattern.pattern_id = "new_id"


class TestLearningResult:
    """Test the LearningResult dataclass."""

    def test_learning_result_defaults(self) -> None:
        """Test LearningResult with default values."""
        result = LearningResult()

        assert result.patterns == ()
        assert result.judgment_updates == ()
        assert result.new_knowledge_acquired == ()

    def test_learning_result_with_values(self) -> None:
        """Test LearningResult with provided values."""
        pattern = ErrorPattern(
            pattern_id="test_1",
            root_cause="Timeout",
            avoidance_strategy="Add retry",
            learned_knowledge="Timeouts need handling",
        )
        result = LearningResult(
            patterns=(pattern,),
            judgment_updates=("Add timeout detection",),
            new_knowledge_acquired=("Timeouts require retry logic",),
        )

        assert len(result.patterns) == 1
        assert result.patterns[0].pattern_id == "test_1"
        assert result.judgment_updates == ("Add timeout detection",)
        assert result.new_knowledge_acquired == ("Timeouts require retry logic",)


class TestActiveLearner:
    """Test the ActiveLearner class."""

    @pytest.fixture
    def learner(self) -> ActiveLearner:
        """Create a fresh ActiveLearner instance."""
        return ActiveLearner()

    @pytest.mark.asyncio
    async def test_learn_from_error_basic(self, learner: ActiveLearner) -> None:
        """Test basic learning from an error."""
        result = await learner.learn_from_error(
            error_description="Connection timeout occurred",
            context_summary="Network request to external API",
            error_type="timeout",
        )

        assert isinstance(result, LearningResult)
        assert len(result.patterns) == 1
        assert result.patterns[0].root_cause is not None
        assert result.patterns[0].avoidance_strategy is not None
        assert result.patterns[0].learned_knowledge is not None

    @pytest.mark.asyncio
    async def test_learn_from_error_increments_count(self, learner: ActiveLearner) -> None:
        """Test that learning from same error increments occurrence count."""
        # Use identical error description to trigger pattern merge
        await learner.learn_from_error(
            error_description="Memory allocation failed",
            context_summary="Processing large dataset",
            error_type="memory",
        )

        result = await learner.learn_from_error(
            error_description="Memory allocation failed",
            context_summary="Processing large dataset",
            error_type="memory",
        )

        assert len(result.patterns) == 1
        assert result.patterns[0].occurrence_count == 2

    @pytest.mark.asyncio
    async def test_learn_from_error_without_type(self, learner: ActiveLearner) -> None:
        """Test learning when error type is not provided (auto-detection)."""
        result = await learner.learn_from_error(
            error_description="File not found error occurred",
            context_summary="Reading configuration file",
            error_type=None,
        )

        assert isinstance(result, LearningResult)
        assert len(result.patterns) == 1
        # Should detect IO error type
        pattern = result.patterns[0]
        assert "io" in pattern.root_cause.lower() or "error type: io" in pattern.root_cause.lower()

    @pytest.mark.asyncio
    async def test_get_learned_patterns_empty(self, learner: ActiveLearner) -> None:
        """Test getting patterns when none have been learned."""
        patterns = await learner.get_learned_patterns()

        assert patterns == []

    @pytest.mark.asyncio
    async def test_get_learned_patterns_all(self, learner: ActiveLearner) -> None:
        """Test getting all learned patterns."""
        await learner.learn_from_error(
            error_description="Timeout error",
            context_summary="API call",
            error_type="timeout",
        )
        await learner.learn_from_error(
            error_description="Memory error",
            context_summary="Data processing",
            error_type="memory",
        )

        patterns = await learner.get_learned_patterns()

        assert len(patterns) == 2

    @pytest.mark.asyncio
    async def test_get_learned_patterns_filtered(self, learner: ActiveLearner) -> None:
        """Test filtering patterns by category."""
        await learner.learn_from_error(
            error_description="Timeout error",
            context_summary="API call",
            error_type="timeout",
        )
        await learner.learn_from_error(
            error_description="Memory error",
            context_summary="Data processing",
            error_type="memory",
        )

        patterns = await learner.get_learned_patterns(category="timeout")

        assert len(patterns) == 1
        assert "timeout" in patterns[0].root_cause.lower()

    @pytest.mark.asyncio
    async def test_merge_patterns(self, learner: ActiveLearner) -> None:
        """Test merging two similar patterns."""
        pattern1 = ErrorPattern(
            pattern_id="pattern_1",
            root_cause="Null pointer error",
            avoidance_strategy="Add null checks",
            learned_knowledge="Validate inputs",
            occurrence_count=3,
            last_seen="2026-04-06T00:00:00Z",
        )
        pattern2 = ErrorPattern(
            pattern_id="pattern_2",
            root_cause="None reference error",
            avoidance_strategy="Check for None",
            learned_knowledge="Guard against None",
            occurrence_count=2,
            last_seen="2026-04-06T01:00:00Z",
        )

        merged = await learner.merge_patterns(pattern1, pattern2)

        assert merged.pattern_id.startswith("pattern_")
        assert "Null pointer error" in merged.root_cause
        assert "None reference error" in merged.root_cause
        assert merged.occurrence_count == 3
        assert merged.last_seen == "2026-04-06T01:00:00Z"

    @pytest.mark.asyncio
    async def test_pattern_extraction_timeout(self, learner: ActiveLearner) -> None:
        """Test that timeout errors get appropriate strategies."""
        result = await learner.learn_from_error(
            error_description="The request timed out after 30 seconds",
            context_summary="Calling slow external service",
            error_type="timeout",
        )

        pattern = result.patterns[0]
        assert "timeout" in pattern.avoidance_strategy.lower() or "retry" in pattern.avoidance_strategy.lower()
        assert len(result.judgment_updates) > 0

    @pytest.mark.asyncio
    async def test_pattern_extraction_validation(self, learner: ActiveLearner) -> None:
        """Test that validation errors get appropriate strategies."""
        result = await learner.learn_from_error(
            error_description="Invalid input: field 'email' has malformed data",
            context_summary="User registration form submission",
            error_type="validation",
        )

        pattern = result.patterns[0]
        assert "validation" in pattern.avoidance_strategy.lower() or "validation" in pattern.learned_knowledge.lower()

    @pytest.mark.asyncio
    async def test_pattern_extraction_authentication(self, learner: ActiveLearner) -> None:
        """Test that authentication errors get appropriate strategies."""
        result = await learner.learn_from_error(
            error_description="Unauthorized: Invalid or expired token",
            context_summary="API request with stale credentials",
            error_type="authentication",
        )

        pattern = result.patterns[0]
        assert "auth" in pattern.avoidance_strategy.lower() or "credential" in pattern.avoidance_strategy.lower()

    @pytest.mark.asyncio
    async def test_multiple_learn_from_error_stores_patterns(self, learner: ActiveLearner) -> None:
        """Test that multiple learn_from_error calls properly store patterns."""
        await learner.learn_from_error(
            error_description="Error 1",
            context_summary="Context 1",
            error_type="timeout",
        )
        await learner.learn_from_error(
            error_description="Error 2",
            context_summary="Context 2",
            error_type="memory",
        )
        await learner.learn_from_error(
            error_description="Error 3",
            context_summary="Context 3",
            error_type="io",
        )

        all_patterns = await learner.get_learned_patterns()
        assert len(all_patterns) == 3

    @pytest.mark.asyncio
    async def test_judgment_updates_generated(self, learner: ActiveLearner) -> None:
        """Test that judgment updates are properly generated."""
        result = await learner.learn_from_error(
            error_description="Connection refused",
            context_summary="Network connection attempt",
            error_type="network",
        )

        assert len(result.judgment_updates) > 0
        assert all(isinstance(update, str) for update in result.judgment_updates)

    @pytest.mark.asyncio
    async def test_new_knowledge_acquired(self, learner: ActiveLearner) -> None:
        """Test that new knowledge is acquired from errors."""
        result = await learner.learn_from_error(
            error_description="Rate limit exceeded",
            context_summary="Bulk API requests",
            error_type="rate_limit",
        )

        assert len(result.new_knowledge_acquired) > 0
        assert isinstance(result.new_knowledge_acquired[0], str)
