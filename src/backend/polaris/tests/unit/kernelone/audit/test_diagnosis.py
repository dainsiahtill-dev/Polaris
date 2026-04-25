"""Unit tests for polaris.kernelone.audit.diagnosis."""

from __future__ import annotations

from polaris.kernelone.audit.diagnosis import (
    BUILTIN_PATTERNS,
    DiagnosisResult,
    ErrorPattern,
    PatternRegistry,
    SuspiciousLocation,
    diagnose_error,
    diagnose_from_exception,
)


class TestErrorPattern:
    def test_fields(self) -> None:
        p = ErrorPattern(
            name="test",
            regex=r"test (.+)",
            description="desc",
            root_cause_template="rc {group_0}",
            fix_suggestion_template="fix {group_0}",
            search_keywords=["kw"],
            priority=5,
        )
        assert p.name == "test"
        assert p.priority == 5


class TestPatternRegistry:
    def test_builtin_patterns_loaded(self) -> None:
        reg = PatternRegistry()
        assert len(reg._patterns) == len(BUILTIN_PATTERNS)

    def test_register_and_sort(self) -> None:
        reg = PatternRegistry()
        new_pattern = ErrorPattern(
            name="high_priority",
            regex=r"high",
            description="high",
            root_cause_template="rc",
            fix_suggestion_template="fix",
            search_keywords=[],
            priority=100,
        )
        reg.register(new_pattern)
        assert reg._patterns[0].name == "high_priority"

    def test_match_found(self) -> None:
        reg = PatternRegistry()
        pattern, info = reg.match("object of type 'method' has no len()")
        assert pattern is not None
        assert pattern.name == "len_on_invalid_type"
        assert info.get("group_0") == "method"

    def test_match_not_found(self) -> None:
        reg = PatternRegistry()
        pattern, info = reg.match("some completely unknown error")
        assert pattern is None
        assert info == {}

    def test_named_groups(self) -> None:
        reg = PatternRegistry()
        pattern, info = reg.match("can only concatenate str (not 'int') to str")
        assert pattern is not None
        assert info.get("group_0") == "int"


class TestDiagnoseError:
    def test_no_match(self) -> None:
        result = diagnose_error("unknown error xyz")
        assert result.error_pattern is None
        assert result.root_cause == "未知错误模式"
        assert result.diagnosis_time_ms >= 0.0

    def test_len_on_invalid_type(self) -> None:
        result = diagnose_error("object of type 'NoneType' has no len()")
        assert result.error_pattern is not None
        assert result.error_pattern.name == "len_on_invalid_type"
        assert "NoneType" in result.root_cause
        assert len(result.suspicious_locations) == 0

    def test_with_traceback(self) -> None:
        result = diagnose_error(
            "object of type 'method' has no len()",
            traceback_stack=["file.py:10: in func", "event.reasoning"],
        )
        assert result.error_pattern is not None
        assert len(result.suspicious_locations) > 0
        loc = result.suspicious_locations[0]
        assert isinstance(loc, SuspiciousLocation)
        assert loc.confidence == 0.8

    def test_fix_suggestion_substitution(self) -> None:
        result = diagnose_error("can only concatenate str (not 'int') to str")
        assert "int" in result.fix_suggestion

    def test_max_results(self) -> None:
        result = diagnose_error(
            "object of type 'method' has no len()",
            traceback_stack=["event.reasoning", "event.chunk", "len("],
            max_results=2,
        )
        assert len(result.suspicious_locations) <= 2


class TestDiagnoseFromException:
    def test_from_value_error(self) -> None:
        try:
            raise ValueError("object of type 'method' has no len()")
        except ValueError as exc:
            result = diagnose_from_exception(exc)
            assert result.error_pattern is not None
            assert result.error_pattern.name == "len_on_invalid_type"


class TestDiagnosisResult:
    def test_has_result_false(self) -> None:
        result = DiagnosisResult(
            error_pattern=None,
            extracted_info={},
            root_cause="",
            fix_suggestion="",
        )
        assert result.has_result() is False

    def test_has_result_true(self) -> None:
        pattern = BUILTIN_PATTERNS[0]
        loc = SuspiciousLocation(
            file_path="f.py",
            line_number=1,
            line_content="x",
            match_reason="test",
            confidence=0.5,
        )
        result = DiagnosisResult(
            error_pattern=pattern,
            extracted_info={"group_0": "x"},
            suspicious_locations=[loc],
            root_cause="rc",
            fix_suggestion="fix",
        )
        assert result.has_result() is True
