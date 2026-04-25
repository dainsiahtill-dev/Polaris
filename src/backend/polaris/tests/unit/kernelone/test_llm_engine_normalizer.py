"""Tests for polaris.kernelone.llm.engine.normalizer."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.llm.engine.normalizer import (
    ResponseNormalizer,
    normalize_list,
    split_lines,
)


class TestExtractText:
    def test_string_payload(self) -> None:
        assert ResponseNormalizer.extract_text("  hello world  ") == "hello world"

    def test_non_dict_non_string(self) -> None:
        assert ResponseNormalizer.extract_text(123) == ""

    def test_output_text_key(self) -> None:
        payload = {"output_text": "  result  "}
        assert ResponseNormalizer.extract_text(payload) == "result"

    def test_choices_message_content(self) -> None:
        payload = {"choices": [{"message": {"content": "hello"}}]}
        assert ResponseNormalizer.extract_text(payload) == "hello"

    def test_choices_text_key(self) -> None:
        payload = {"choices": [{"text": "hello"}]}
        assert ResponseNormalizer.extract_text(payload) == "hello"

    def test_top_level_message(self) -> None:
        payload = {"message": {"content": "hello"}}
        assert ResponseNormalizer.extract_text(payload) == "hello"

    def test_top_level_content(self) -> None:
        payload = {"content": "hello"}
        assert ResponseNormalizer.extract_text(payload) == "hello"

    def test_fallback_keys(self) -> None:
        for key in ("text", "response", "output"):
            payload = {key: "hello"}
            assert ResponseNormalizer.extract_text(payload) == "hello"

    def test_empty_dict(self) -> None:
        assert ResponseNormalizer.extract_text({}) == ""

    def test_list_content(self) -> None:
        payload = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        assert ResponseNormalizer.extract_text(payload) == "hello\nworld"


class TestExtractReasoning:
    def test_non_dict(self) -> None:
        assert ResponseNormalizer.extract_reasoning("string") == ""

    def test_reasoning_content_key(self) -> None:
        payload = {"reasoning_content": "thinking..."}
        assert ResponseNormalizer.extract_reasoning(payload) == "thinking..."

    def test_reasoning_key(self) -> None:
        payload = {"reasoning": "analysis"}
        assert ResponseNormalizer.extract_reasoning(payload) == "analysis"

    def test_thinking_key(self) -> None:
        payload = {"thinking": "thoughts"}
        assert ResponseNormalizer.extract_reasoning(payload) == "thoughts"

    def test_analysis_key(self) -> None:
        payload = {"analysis": "deep analysis"}
        assert ResponseNormalizer.extract_reasoning(payload) == "deep analysis"

    def test_in_choices_message(self) -> None:
        payload = {"choices": [{"message": {"reasoning_content": "msg reasoning"}}]}
        assert ResponseNormalizer.extract_reasoning(payload) == "msg reasoning"

    def test_in_first_choice(self) -> None:
        payload = {"choices": [{"reasoning": "choice reasoning"}]}
        assert ResponseNormalizer.extract_reasoning(payload) == "choice reasoning"

    def test_empty_dict(self) -> None:
        assert ResponseNormalizer.extract_reasoning({}) == ""


class TestExtractFinishReason:
    def test_non_dict(self) -> None:
        assert ResponseNormalizer.extract_finish_reason("string") == ""

    def test_finish_reason_in_choice(self) -> None:
        payload = {"choices": [{"finish_reason": "stop"}]}
        assert ResponseNormalizer.extract_finish_reason(payload) == "stop"

    def test_stop_reason_in_choice(self) -> None:
        payload = {"choices": [{"stop_reason": "length"}]}
        assert ResponseNormalizer.extract_finish_reason(payload) == "length"

    def test_top_level_finish_reason(self) -> None:
        payload = {"finish_reason": "content_filter"}
        assert ResponseNormalizer.extract_finish_reason(payload) == "content_filter"

    def test_empty_dict(self) -> None:
        assert ResponseNormalizer.extract_finish_reason({}) == ""


class TestIsLengthFinishReason:
    def test_length(self) -> None:
        assert ResponseNormalizer.is_length_finish_reason("length") is True

    def test_max_tokens(self) -> None:
        assert ResponseNormalizer.is_length_finish_reason("max_tokens") is True

    def test_token_limit(self) -> None:
        assert ResponseNormalizer.is_length_finish_reason("token_limit") is True

    def test_stop(self) -> None:
        assert ResponseNormalizer.is_length_finish_reason("stop") is False

    def test_none(self) -> None:
        assert ResponseNormalizer.is_length_finish_reason(None) is False  # type: ignore[arg-type]

    def test_empty(self) -> None:
        assert ResponseNormalizer.is_length_finish_reason("") is False


class TestLooksTruncatedJson:
    def test_empty(self) -> None:
        assert ResponseNormalizer.looks_truncated_json("") is False

    def test_no_brace(self) -> None:
        assert ResponseNormalizer.looks_truncated_json("hello") is False

    def test_unmatched_braces(self) -> None:
        assert ResponseNormalizer.looks_truncated_json("{a: 1") is True

    def test_ends_with_comma(self) -> None:
        assert ResponseNormalizer.looks_truncated_json('{"a": 1,') is True

    def test_ends_with_colon(self) -> None:
        assert ResponseNormalizer.looks_truncated_json('{"a":') is True

    def test_ends_with_bracket(self) -> None:
        assert ResponseNormalizer.looks_truncated_json('{"a": [') is True

    def test_ends_with_quote(self) -> None:
        assert ResponseNormalizer.looks_truncated_json('{"a": "') is True

    def test_valid_json(self) -> None:
        assert ResponseNormalizer.looks_truncated_json('{"a": 1}') is False


class TestExtractJsonObject:
    def test_valid_json(self) -> None:
        result = ResponseNormalizer.extract_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self) -> None:
        result = ResponseNormalizer.extract_json_object("not json")
        assert result is None

    def test_empty(self) -> None:
        result = ResponseNormalizer.extract_json_object("")
        assert result is None


class TestNormalizeResponse:
    def test_success(self) -> None:
        from polaris.kernelone.llm.shared_contracts import AIResponse

        payload = {"choices": [{"message": {"content": "hello"}}]}
        result = ResponseNormalizer.normalize_response(payload, latency_ms=100, trace_id="t1")
        assert isinstance(result, AIResponse)
        assert result.ok is True
        assert result.output == "hello"
        assert result.latency_ms == 100
        assert result.trace_id == "t1"

    def test_failure_on_exception(self) -> None:
        from polaris.kernelone.llm.shared_contracts import AIResponse

        # Pass something that causes extract_text to fail weirdly
        with patch.object(ResponseNormalizer, "extract_text", side_effect=ValueError("boom")):
            result = ResponseNormalizer.normalize_response({}, latency_ms=50)
            assert isinstance(result, AIResponse)
            assert result.ok is False
            assert "boom" in result.error


class TestNormalizeList:
    def test_none(self) -> None:
        assert normalize_list(None) == []

    def test_empty_string(self) -> None:
        assert normalize_list("") == []

    def test_single_string(self) -> None:
        assert normalize_list("hello") == ["hello"]

    def test_list(self) -> None:
        assert normalize_list(["a", "b", ""]) == ["a", "b"]

    def test_tuple(self) -> None:
        assert normalize_list(("a", "b")) == ["a", "b"]

    def test_other_type(self) -> None:
        assert normalize_list(42) == ["42"]


class TestSplitLines:
    def test_empty(self) -> None:
        assert split_lines("") == []

    def test_single_line(self) -> None:
        assert split_lines("hello") == ["hello"]

    def test_multiple_lines(self) -> None:
        assert split_lines("a\nb\nc") == ["a", "b", "c"]

    def test_crlf(self) -> None:
        assert split_lines("a\r\nb") == ["a", "b"]

    def test_strips_whitespace(self) -> None:
        assert split_lines("  a  \n  b  ") == ["a", "b"]

    def test_skips_empty_lines(self) -> None:
        assert split_lines("a\n\nb") == ["a", "b"]
