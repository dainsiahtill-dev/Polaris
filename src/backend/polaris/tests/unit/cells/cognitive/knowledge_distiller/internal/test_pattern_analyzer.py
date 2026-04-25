"""Unit tests for PatternAnalyzer."""

from __future__ import annotations

import pytest
from polaris.cells.cognitive.knowledge_distiller.internal.pattern_analyzer import (
    ExtractedPattern,
    PatternAnalyzer,
)


class TestExtractedPattern:
    """Tests for ExtractedPattern dataclass."""

    def test_defaults(self) -> None:
        p = ExtractedPattern(
            pattern_type="error_pattern",
            summary="Null pointer",
            insight="Check nulls",
            confidence=0.8,
            related_files=["a.py"],
        )
        assert p.error_signature is None
        assert p.prevention_hint is None
        assert p.success_factors is None


class TestPatternAnalyzer:
    """Tests for PatternAnalyzer."""

    @pytest.fixture
    def analyzer(self) -> PatternAnalyzer:
        return PatternAnalyzer()

    def test_analyze_empty_findings(self, analyzer: PatternAnalyzer) -> None:
        patterns = analyzer.analyze({}, "sess_1", "completed")
        assert patterns == []

    def test_analyze_success_pattern(self, analyzer: PatternAnalyzer) -> None:
        findings = {
            "verified_results": ["test_passed"],
            "patched_files": ["fix.py"],
        }
        patterns = analyzer.analyze(findings, "sess_1", "completed")
        assert len(patterns) >= 1
        types = {p.pattern_type for p in patterns}
        assert "success_pattern" in types

    def test_analyze_error_pattern(self, analyzer: PatternAnalyzer) -> None:
        findings = {
            "error_summary": "Index out of range",
            "suspected_files": ["bug.py"],
        }
        patterns = analyzer.analyze(findings, "sess_1", "failed")
        assert len(patterns) >= 1
        types = {p.pattern_type for p in patterns}
        assert "error_pattern" in types

    def test_analyze_stagnation_pattern(self, analyzer: PatternAnalyzer) -> None:
        findings = {
            "_findings_trajectory": [
                {"task_progress": "step1"},
                {"task_progress": "step1"},
                {"task_progress": "step1"},
            ],
        }
        patterns = analyzer.analyze(findings, "sess_1", "stagnation")
        assert len(patterns) >= 1
        types = {p.pattern_type for p in patterns}
        assert "stagnation_pattern" in types

    def test_analyze_generic_pattern_fallback(self, analyzer: PatternAnalyzer) -> None:
        findings = {
            "task_progress": "in_progress",
            "_findings_trajectory": [{"task_progress": "step1"}],
        }
        patterns = analyzer.analyze(findings, "sess_1", "completed")
        # Should get at least success pattern or generic pattern
        assert len(patterns) >= 1

    def test_error_pattern_confidence_with_trajectory(self, analyzer: PatternAnalyzer) -> None:
        findings = {
            "error_summary": "Bug",
            "_findings_trajectory": [{}, {}, {}, {}, {}],
        }
        patterns = analyzer.analyze(findings, "sess_1", "failed")
        error_p = next((p for p in patterns if p.pattern_type == "error_pattern"), None)
        assert error_p is not None
        assert error_p.confidence > 0.5

    def test_stagnation_not_enough_trajectory(self, analyzer: PatternAnalyzer) -> None:
        findings = {
            "_findings_trajectory": [{"task_progress": "step1"}],
        }
        patterns = analyzer.analyze(findings, "sess_1", "stagnation")
        # With only 1 trajectory item, stagnation pattern should not be extracted
        stagnation_p = next((p for p in patterns if p.pattern_type == "stagnation_pattern"), None)
        assert stagnation_p is None

    def test_extract_error_signature(self, analyzer: PatternAnalyzer) -> None:
        signature = analyzer._extract_error_signature("Error at /path/file.py:42\nMore info")
        assert "<file>" in signature
        assert ":42" not in signature

    def test_generate_error_prevention_test(self, analyzer: PatternAnalyzer) -> None:
        hint = analyzer._generate_error_prevention("add test", ["a.py"])
        assert "test" in hint.lower()

    def test_generate_error_prevention_validate(self, analyzer: PatternAnalyzer) -> None:
        hint = analyzer._generate_error_prevention("validate input", ["a.py"])
        assert "validation" in hint.lower()

    def test_generate_error_prevention_retry(self, analyzer: PatternAnalyzer) -> None:
        hint = analyzer._generate_error_prevention("retry connection", ["a.py"])
        assert "retry" in hint.lower()

    def test_generate_error_prevention_ignore(self, analyzer: PatternAnalyzer) -> None:
        hint = analyzer._generate_error_prevention("ignore warning", ["a.py"])
        assert "skip" in hint.lower()

    def test_generate_error_prevention_default(self, analyzer: PatternAnalyzer) -> None:
        hint = analyzer._generate_error_prevention("do something else", ["a.py"])
        assert "Action taken" in hint

    def test_has_error_indicators_by_value(self, analyzer: PatternAnalyzer) -> None:
        findings = {"some_key": "there was an error here"}
        assert analyzer._has_error_indicators(findings) is True

    def test_has_success_indicators(self, analyzer: PatternAnalyzer) -> None:
        findings = {"verified_results": "completed"}
        assert analyzer._has_success_indicators(findings) is True

    def test_has_stagnation_indicators(self, analyzer: PatternAnalyzer) -> None:
        findings = {"stagnation_marker": "loop detected"}
        assert analyzer._has_stagnation_indicators(findings) is True
