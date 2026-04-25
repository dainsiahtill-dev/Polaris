"""Unit tests for polaris.cells.llm.evaluation.internal.constants."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from polaris.cells.llm.evaluation.internal.constants import (
    INTERVIEW_EMBEDDING_MODEL,
    INTERVIEW_SEMANTIC_ENABLED,
    INTERVIEW_SEMANTIC_MAX_CHARS,
    INTERVIEW_SEMANTIC_MIN_CHARS,
    INTERVIEW_SEMANTIC_THRESHOLD,
    INTERVIEW_SEMANTIC_TIMEOUT,
    REQUIRED_SUITES_BY_ROLE,
    ROLE_REQUIREMENTS,
    SUITES,
    THINKING_INDICATORS,
    _env_flag,
    _env_float,
    _env_int,
)


class TestThinkingIndicators:
    """Tests for THINKING_INDICATORS constant."""

    def test_contains_expected_values(self) -> None:
        assert "<thinking>" in THINKING_INDICATORS
        assert "<reasoning>" in THINKING_INDICATORS
        assert "let me think" in THINKING_INDICATORS
        assert "step by step" in THINKING_INDICATORS
        assert "my reasoning" in THINKING_INDICATORS


class TestRoleRequirements:
    """Tests for ROLE_REQUIREMENTS constant."""

    def test_pm_requires_thinking(self) -> None:
        assert ROLE_REQUIREMENTS["pm"]["requires_thinking"] is True
        assert ROLE_REQUIREMENTS["pm"]["min_confidence"] == 0.7

    def test_director_requires_thinking(self) -> None:
        assert ROLE_REQUIREMENTS["director"]["requires_thinking"] is True
        assert ROLE_REQUIREMENTS["director"]["min_confidence"] == 0.7


class TestEnvFlag:
    """Tests for _env_flag helper."""

    def test_default_true(self) -> None:
        assert _env_flag("NONEXISTENT_VAR_XYZ", default=True) is True
        assert _env_flag("NONEXISTENT_VAR_XYZ", default=False) is False

    def test_true_values(self) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", "Yes", "ON"):
            with patch.dict(os.environ, {"TEST_FLAG": val}):
                assert _env_flag("TEST_FLAG", default=False) is True

    def test_false_values(self) -> None:
        for val in ("0", "false", "no", "off", "FALSE", "No", "OFF"):
            with patch.dict(os.environ, {"TEST_FLAG": val}):
                assert _env_flag("TEST_FLAG", default=True) is False


class TestEnvFloat:
    """Tests for _env_float helper."""

    def test_default(self) -> None:
        assert _env_float("NONEXISTENT_VAR_XYZ", 0.5) == 0.5

    def test_valid_value(self) -> None:
        with patch.dict(os.environ, {"TEST_FLOAT": "0.75"}):
            assert _env_float("TEST_FLOAT", 0.5) == 0.75

    def test_invalid_value(self) -> None:
        with patch.dict(os.environ, {"TEST_FLOAT": "not_a_number"}):
            assert _env_float("TEST_FLOAT", 0.5) == 0.5


class TestEnvInt:
    """Tests for _env_int helper."""

    def test_default(self) -> None:
        assert _env_int("NONEXISTENT_VAR_XYZ", 100) == 100

    def test_valid_value(self) -> None:
        with patch.dict(os.environ, {"TEST_INT": "42"}):
            assert _env_int("TEST_INT", 100) == 42

    def test_invalid_value(self) -> None:
        with patch.dict(os.environ, {"TEST_INT": "not_a_number"}):
            assert _env_int("TEST_INT", 100) == 100


class TestInterviewSemanticConstants:
    """Tests for interview semantic constants."""

    def test_semantic_enabled(self) -> None:
        assert isinstance(INTERVIEW_SEMANTIC_ENABLED, bool)

    def test_semantic_threshold(self) -> None:
        assert isinstance(INTERVIEW_SEMANTIC_THRESHOLD, float)
        assert 0.0 < INTERVIEW_SEMANTIC_THRESHOLD < 1.0

    def test_semantic_min_chars(self) -> None:
        assert isinstance(INTERVIEW_SEMANTIC_MIN_CHARS, int)
        assert INTERVIEW_SEMANTIC_MIN_CHARS > 0

    def test_semantic_max_chars(self) -> None:
        assert isinstance(INTERVIEW_SEMANTIC_MAX_CHARS, int)
        assert INTERVIEW_SEMANTIC_MAX_CHARS > INTERVIEW_SEMANTIC_MIN_CHARS

    def test_semantic_timeout(self) -> None:
        assert isinstance(INTERVIEW_SEMANTIC_TIMEOUT, float)
        assert INTERVIEW_SEMANTIC_TIMEOUT > 0

    def test_embedding_model(self) -> None:
        assert isinstance(INTERVIEW_EMBEDDING_MODEL, str)
        assert len(INTERVIEW_EMBEDDING_MODEL) > 0


class TestSuites:
    """Tests for SUITES constant."""

    def test_contains_expected_suites(self) -> None:
        assert "connectivity" in SUITES
        assert "response" in SUITES
        assert "thinking" in SUITES
        assert "qualification" in SUITES
        assert "interview" in SUITES
        assert "agentic_benchmark" in SUITES
        assert "tool_calling_matrix" in SUITES


class TestRequiredSuitesByRole:
    """Tests for REQUIRED_SUITES_BY_ROLE constant."""

    def test_pm_suites(self) -> None:
        assert "connectivity" in REQUIRED_SUITES_BY_ROLE["pm"]
        assert "interview" in REQUIRED_SUITES_BY_ROLE["pm"]

    def test_director_suites(self) -> None:
        assert "connectivity" in REQUIRED_SUITES_BY_ROLE["director"]
        assert "interview" in REQUIRED_SUITES_BY_ROLE["director"]

    def test_architect_suites(self) -> None:
        assert "connectivity" in REQUIRED_SUITES_BY_ROLE["architect"]
        assert "thinking" in REQUIRED_SUITES_BY_ROLE["architect"]
        assert "interview" not in REQUIRED_SUITES_BY_ROLE["architect"]

    def test_default_suites(self) -> None:
        assert "connectivity" in REQUIRED_SUITES_BY_ROLE["default"]
        assert "response" in REQUIRED_SUITES_BY_ROLE["default"]
        assert "qualification" in REQUIRED_SUITES_BY_ROLE["default"]
