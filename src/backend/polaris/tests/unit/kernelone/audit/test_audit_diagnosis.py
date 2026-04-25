"""Tests for polaris.kernelone.audit.diagnosis."""

from __future__ import annotations

from polaris.kernelone.audit.diagnosis import (
    DiagnosisResult,
    ErrorPattern,
    PatternRegistry,
    SuspiciousLocation,
    diagnose_error,
    diagnose_from_exception,
)


class TestErrorPattern:
    def test_fields(self) -> None:
        pattern = ErrorPattern(
            name="test",
            regex=r"test",
            description="desc",
            root_cause_template="root: {group_0}",
            fix_suggestion_template="fix: {group_0}",
            search_keywords=["kw"],
            priority=5,
        )
        assert pattern.name == "test"
        assert pattern.priority == 5


class TestSuspiciousLocation:
    def test_fields(self) -> None:
        loc = SuspiciousLocation(
            file_path="file.py:10",
            line_number=10,
            line_content="x = 1",
            match_reason="keyword",
            confidence=0.8,
        )
        assert loc.confidence == 0.8
        assert loc.file_path == "file.py:10"


class TestDiagnosisResult:
    def test_has_result_true(self) -> None:
        pattern = ErrorPattern(
            name="test",
            regex=r"test",
            description="d",
            root_cause_template="r",
            fix_suggestion_template="f",
            search_keywords=[],
        )
        result = DiagnosisResult(
            error_pattern=pattern,
            extracted_info={"group_0": "x"},
            suspicious_locations=[
                SuspiciousLocation("f", 1, "c", "m", 0.5),
            ],
            root_cause="rc",
            fix_suggestion="fs",
        )
        assert result.has_result() is True

    def test_has_result_false_no_pattern(self) -> None:
        result = DiagnosisResult(
            error_pattern=None,
            extracted_info={},
            suspicious_locations=[SuspiciousLocation("f", 1, "c", "m", 0.5)],
        )
        assert result.has_result() is False

    def test_has_result_false_no_locations(self) -> None:
        pattern = ErrorPattern(
            name="test",
            regex=r"test",
            description="d",
            root_cause_template="r",
            fix_suggestion_template="f",
            search_keywords=[],
        )
        result = DiagnosisResult(
            error_pattern=pattern,
            extracted_info={},
            suspicious_locations=[],
        )
        assert result.has_result() is False


class TestPatternRegistry:
    def test_builtin_patterns_loaded(self) -> None:
        registry = PatternRegistry()
        assert len(registry._patterns) > 0

    def test_register_and_sort(self) -> None:
        registry = PatternRegistry()
        initial_count = len(registry._patterns)
        low_priority = ErrorPattern(
            name="low",
            regex=r"low",
            description="d",
            root_cause_template="r",
            fix_suggestion_template="f",
            search_keywords=[],
            priority=-1,
        )
        registry.register(low_priority)
        assert len(registry._patterns) == initial_count + 1
        # Highest priority should be first
        assert registry._patterns[0].priority >= registry._patterns[-1].priority

    def test_match_len_on_invalid_type(self) -> None:
        registry = PatternRegistry()
        pattern, info = registry.match("object of type 'method' has no len()")
        assert pattern is not None
        assert pattern.name == "len_on_invalid_type"
        assert "group_0" in info
        assert info["group_0"] == "method"

    def test_match_attribute_error(self) -> None:
        registry = PatternRegistry()
        pattern, info = registry.match("'str' object has no attribute 'foo'")
        assert pattern is not None
        assert pattern.name == "attribute_error"
        assert info.get("group_0") == "str"
        assert info.get("group_1") == "foo"

    def test_match_type_error_format(self) -> None:
        registry = PatternRegistry()
        pattern, info = registry.match("can only concatenate str (not 'int') to str")
        assert pattern is not None
        assert pattern.name == "type_error_format"
        assert info.get("group_0") == "int"

    def test_no_match(self) -> None:
        registry = PatternRegistry()
        pattern, info = registry.match("totally unknown error message")
        assert pattern is None
        assert info == {}

    def test_match_named_groups(self) -> None:
        registry = PatternRegistry()
        custom = ErrorPattern(
            name="named",
            regex=r"(?P<code>\d+): (?P<msg>.+)",
            description="d",
            root_cause_template="code={code}",
            fix_suggestion_template="fix={code}",
            search_keywords=[],
        )
        registry.register(custom)
        pattern, info = registry.match("404: not found")
        assert pattern is not None
        assert info.get("code") == "404"
        assert info.get("msg") == "not found"


class TestDiagnoseError:
    def test_known_error(self) -> None:
        result = diagnose_error("object of type 'method' has no len()")
        assert result.error_pattern is not None
        assert "method" in result.root_cause
        assert result.diagnosis_time_ms >= 0.0

    def test_unknown_error(self) -> None:
        result = diagnose_error("something completely unknown")
        assert result.error_pattern is None
        assert result.root_cause == "未知错误模式"
        assert result.diagnosis_time_ms >= 0.0

    def test_with_traceback(self) -> None:
        result = diagnose_error(
            "object of type 'method' has no len()",
            traceback_stack=["file.py:100: in func", "event.reasoning"],
        )
        assert len(result.suspicious_locations) > 0
        assert result.suspicious_locations[0].confidence == 0.8

    def test_max_results(self) -> None:
        result = diagnose_error(
            "object of type 'method' has no len()",
            traceback_stack=["event.reasoning"] * 10,
            max_results=3,
        )
        assert len(result.suspicious_locations) <= 3

    def test_template_substitution(self) -> None:
        result = diagnose_error("'str' object has no attribute 'foo'")
        assert "str" in result.root_cause
        assert "foo" in result.root_cause


class TestDiagnoseFromException:
    def test_from_exception(self) -> None:
        try:
            raise ValueError("object of type 'method' has no len()")
        except ValueError as exc:
            result = diagnose_from_exception(exc)
            assert result.error_pattern is not None
            assert "method" in result.root_cause
