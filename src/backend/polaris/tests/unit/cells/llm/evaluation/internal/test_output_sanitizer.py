"""Unit tests for polaris.cells.llm.evaluation.internal.output_sanitizer."""

from __future__ import annotations

from polaris.cells.llm.evaluation.internal.output_sanitizer import (
    DEFAULT_FILTER_MARKER,
    OutputSanitizer,
    SanitizationResult,
    SanitizationStrategy,
    create_sanitizer_from_case,
    sanitize_observation_output,
)


class TestSanitizationStrategy:
    """Tests for SanitizationStrategy enum."""

    def test_members(self) -> None:
        assert SanitizationStrategy.STRICT.value == "strict"
        assert SanitizationStrategy.REPLACE.value == "replace"
        assert SanitizationStrategy.SOFT.value == "soft"


class TestSanitizationResult:
    """Tests for SanitizationResult dataclass."""

    def test_defaults(self) -> None:
        result = SanitizationResult(sanitized_output="hello", was_modified=False)
        assert result.sanitized_output == "hello"
        assert result.was_modified is False
        assert result.matched_tokens == ()
        assert result.strategy_used == SanitizationStrategy.STRICT


class TestOutputSanitizerInit:
    """Tests for OutputSanitizer initialization."""

    def test_defaults(self) -> None:
        sanitizer = OutputSanitizer()
        assert sanitizer.forbidden_tokens == ()
        assert sanitizer.strategy == SanitizationStrategy.STRICT
        assert sanitizer.filter_marker == DEFAULT_FILTER_MARKER
        assert sanitizer.synonym_map == {}
        assert sanitizer.case_sensitive is False

    def test_normalization(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=["  bad  ", "", None, "evil"],
            strategy=SanitizationStrategy.REPLACE,
            filter_marker="",
            synonym_map={"  bad  ": "good", "": "x", None: "y"},
        )
        assert sanitizer.forbidden_tokens == ("bad", "evil")
        assert sanitizer.filter_marker == DEFAULT_FILTER_MARKER
        assert sanitizer.synonym_map == {"bad": "good"}

    def test_custom_values(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("foo", "bar"),
            strategy=SanitizationStrategy.SOFT,
            filter_marker="[REDACTED]",
            synonym_map={"foo": "baz"},
            case_sensitive=True,
        )
        assert sanitizer.forbidden_tokens == ("foo", "bar")
        assert sanitizer.strategy == SanitizationStrategy.SOFT
        assert sanitizer.filter_marker == "[REDACTED]"
        assert sanitizer.synonym_map == {"foo": "baz"}
        assert sanitizer.case_sensitive is True


class TestOutputSanitizerStrict:
    """Tests for STRICT sanitization strategy."""

    def test_removes_token(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.sanitized_output == "This is content"
        assert result.was_modified is True
        assert result.matched_tokens == ("bad",)

    def test_cleans_multiple_spaces(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This is  bad  content")
        assert result.sanitized_output == "This is content"

    def test_no_match(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This is good content")
        assert result.was_modified is False
        assert result.sanitized_output == "This is good content"

    def test_empty_output(self) -> None:
        sanitizer = OutputSanitizer(forbidden_tokens=("bad",))
        result = sanitizer.sanitize("")
        assert result.sanitized_output == ""
        assert result.was_modified is False

    def test_empty_tokens(self) -> None:
        sanitizer = OutputSanitizer(forbidden_tokens=())
        result = sanitizer.sanitize("hello")
        assert result.was_modified is False


class TestOutputSanitizerReplace:
    """Tests for REPLACE sanitization strategy."""

    def test_replaces_with_marker(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.sanitized_output == "This is [FILTERED] content"
        assert result.was_modified is True

    def test_replaces_with_synonym(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.REPLACE,
            synonym_map={"bad": "good"},
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.sanitized_output == "This is good content"

    def test_case_insensitive(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("BAD",),
            strategy=SanitizationStrategy.REPLACE,
            case_sensitive=False,
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.sanitized_output == "This is [FILTERED] content"

    def test_case_sensitive_no_match(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("BAD",),
            strategy=SanitizationStrategy.REPLACE,
            case_sensitive=True,
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.was_modified is False


class TestOutputSanitizerSoft:
    """Tests for SOFT sanitization strategy."""

    def test_filters_inside_parens(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("Call (bad) here")
        assert "[FILTERED]" in result.sanitized_output
        assert result.was_modified is True

    def test_no_match_outside_parens(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.was_modified is False


class TestSanitizeCaseOutput:
    """Tests for sanitize_case_output method."""

    def test_uses_case_tokens(self) -> None:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("global",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize_case_output("This is case bad", ("case", "bad"))
        assert result.was_modified is True
        assert "case" not in result.sanitized_output or "bad" not in result.sanitized_output

    def test_empty_case_tokens(self) -> None:
        sanitizer = OutputSanitizer(forbidden_tokens=("bad",))
        result = sanitizer.sanitize_case_output("hello", ())
        assert result.was_modified is False


class TestSanitizeObservationOutput:
    """Tests for sanitize_observation_output function."""

    def test_basic(self) -> None:
        class FakeJudge:
            forbidden_output_substrings = ("secret",)

        class FakeCase:
            judge = FakeJudge()

        out, think, out_res, think_res = sanitize_observation_output(
            "output with secret",
            "thinking with secret",
            FakeCase(),
        )
        assert "secret" not in out
        assert "secret" not in think
        assert out_res.was_modified is True
        assert think_res.was_modified is True

    def test_no_forbidden(self) -> None:
        class FakeJudge:
            forbidden_output_substrings = ()

        class FakeCase:
            judge = FakeJudge()

        out, think, out_res, _think_res = sanitize_observation_output(
            "output",
            "thinking",
            FakeCase(),
        )
        assert out == "output"
        assert think == "thinking"
        assert out_res.was_modified is False


class TestCreateSanitizerFromCase:
    """Tests for create_sanitizer_from_case function."""

    def test_creates_sanitizer(self) -> None:
        class FakeJudge:
            forbidden_output_substrings = ("bad",)

        class FakeCase:
            judge = FakeJudge()

        sanitizer = create_sanitizer_from_case(FakeCase())
        assert sanitizer.forbidden_tokens == ("bad",)
        assert sanitizer.strategy == SanitizationStrategy.REPLACE
