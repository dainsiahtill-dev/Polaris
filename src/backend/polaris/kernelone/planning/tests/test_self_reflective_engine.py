"""Tests for SelfReflectivePlanner."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.kernelone.planning.self_reflective_engine import (
    SelfReflectivePlanner,
)


class TestSelfReflectivePlanner:
    """Tests for SelfReflectivePlanner."""

    @pytest.fixture
    def mock_llm(self):
        """Mock LLM provider."""
        llm = AsyncMock()
        response = MagicMock()
        response.ok = True
        response.output = (
            '{"is_reasonable": true, "confidence": 0.9, "missing_info": [], "gaps": [], "needs_rethink": false}'
        )
        llm.invoke.return_value = response
        return llm

    @pytest.fixture
    def mock_base_planner(self):
        """Mock base planner."""
        planner = MagicMock()
        planner.plan.return_value = MagicMock(
            steps=(),
            max_duration=None,
            estimated_duration=None,
            metadata={},
        )
        return planner

    @pytest.fixture
    def planner(self, mock_llm, mock_base_planner):
        """Create planner with mocks."""
        return SelfReflectivePlanner(
            llm=mock_llm,
            base_planner=mock_base_planner,
            max_reflection_iterations=3,
            reflection_threshold=0.7,
        )

    def test_init(self, planner, mock_llm, mock_base_planner):
        """Test initialization."""
        assert planner._llm is mock_llm
        assert planner._base is mock_base_planner
        assert planner._max_reflection_iterations == 3
        assert planner._reflection_threshold == 0.7

    def test_format_plan_empty(self, planner):
        """Test formatting empty plan."""
        plan = MagicMock(steps=[])
        result = planner._format_plan(plan)
        assert result == "Empty plan (no steps)"

    def test_parse_reflection_response_valid(self, planner):
        """Test parsing valid reflection response."""
        output = '{"is_reasonable": true, "confidence": 0.8, "missing_info": ["info1"], "gaps": ["gap1"], "needs_rethink": true}'
        result = planner._parse_reflection_response(output)
        assert result.is_reasonable is True
        assert result.confidence == 0.8

    def test_parse_reflection_response_invalid(self, planner):
        """Test parsing invalid reflection response."""
        result = planner._parse_reflection_response("not json")
        assert result.is_reasonable is True
        assert result.confidence == 0.5

    def test_create_fallback_reflection(self, planner):
        """Test fallback reflection creation."""
        result = planner._create_fallback_reflection()
        assert result.is_reasonable is True
        assert result.confidence == 0.5

    def test_extract_json(self, planner):
        """Test JSON extraction from text."""
        text = 'Some text before {"key": "value"} some text after'
        result = planner._extract_json(text)
        assert result is not None
