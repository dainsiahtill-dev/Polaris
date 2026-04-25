"""Tests for polaris.kernelone.llm.engine.stream.tool_accumulator."""

from __future__ import annotations

from polaris.kernelone.llm.engine.stream.tool_accumulator import (
    _debug_compact_payload,
    _debug_tool_arguments,
    _normalize_arguments,
    _provider_supports_structured_stream,
    _safe_text_length,
    _tool_accumulator_key,
    _ToolCallAccumulator,
)


class TestToolCallAccumulator:
    def test_defaults(self) -> None:
        acc = _ToolCallAccumulator()
        assert acc.tool_name == ""
        assert acc.call_id == ""
        assert acc.arguments_buffer == ""
        assert acc.explicit_arguments is None
        assert acc.explicit_arguments_provisional is False
        assert acc.emitted_signature == ""
        assert acc.provider_meta == {}

    def test_custom_values(self) -> None:
        acc = _ToolCallAccumulator(
            tool_name="test_tool",
            call_id="call-1",
            arguments_buffer='{"x": 1}',
            explicit_arguments={"x": 1},
            explicit_arguments_provisional=True,
            emitted_signature="sig",
            provider_meta={"provider": "test"},
        )
        assert acc.tool_name == "test_tool"
        assert acc.call_id == "call-1"
        assert acc.arguments_buffer == '{"x": 1}'
        assert acc.explicit_arguments == {"x": 1}
        assert acc.explicit_arguments_provisional is True
        assert acc.emitted_signature == "sig"
        assert acc.provider_meta == {"provider": "test"}


class TestProviderSupportsStructuredStream:
    def test_has_invoke_stream_events(self) -> None:
        class Provider:
            def invoke_stream_events(self) -> None:
                pass

        assert _provider_supports_structured_stream(Provider()) is True

    def test_missing_method(self) -> None:
        class Provider:
            pass

        assert _provider_supports_structured_stream(Provider()) is False

    def test_method_on_parent(self) -> None:
        class Base:
            def invoke_stream_events(self) -> None:
                pass

        class Child(Base):
            pass

        # __dict__ only contains methods defined directly on the class
        assert _provider_supports_structured_stream(Child()) is False


class TestNormalizeArguments:
    def test_dict_input(self) -> None:
        result, complete = _normalize_arguments({"x": 1})
        assert result == {"x": 1}
        assert complete is True

    def test_none_input(self) -> None:
        result, complete = _normalize_arguments(None)
        assert result == {}
        assert complete is False

    def test_empty_string(self) -> None:
        result, complete = _normalize_arguments("")
        assert result == {}
        assert complete is False

    def test_valid_json_string(self) -> None:
        result, complete = _normalize_arguments('{"x": 1}')
        assert result == {"x": 1}
        assert complete is True

    def test_invalid_json_string(self) -> None:
        result, complete = _normalize_arguments("not json")
        assert result == {}
        assert complete is False

    def test_json_list(self) -> None:
        result, complete = _normalize_arguments("[1, 2, 3]")
        assert result == {"value": [1, 2, 3]}
        assert complete is True

    def test_json_number(self) -> None:
        result, complete = _normalize_arguments("42")
        assert result == {"value": 42}
        assert complete is True


class TestToolAccumulatorKey:
    def test_content_block_index_priority(self) -> None:
        key = _tool_accumulator_key({"content_block_index": 5, "call_id": "abc", "tool": "t"}, 0)
        assert key == "content_block_index:5"

    def test_stream_index_priority(self) -> None:
        key = _tool_accumulator_key({"index": 3, "call_id": "abc", "tool": "t"}, 0)
        assert key == "index:3"

    def test_call_id_priority(self) -> None:
        key = _tool_accumulator_key({"call_id": "abc", "tool": "t"}, 0)
        assert key == "call_id:abc"

    def test_tool_name_priority(self) -> None:
        key = _tool_accumulator_key({"tool": "my_tool"}, 0)
        assert key == "tool:my_tool"

    def test_ordinal_fallback(self) -> None:
        key = _tool_accumulator_key({}, 7)
        assert key == "ordinal:7"

    def test_strips_whitespace(self) -> None:
        key = _tool_accumulator_key({"call_id": "  abc  ", "tool": "  t  "}, 0)
        assert key == "call_id:abc"


class TestSafeTextLength:
    def test_string(self) -> None:
        assert _safe_text_length("hello") == 5

    def test_bytes(self) -> None:
        assert _safe_text_length(b"hello") == 5

    def test_int(self) -> None:
        assert _safe_text_length(42) == 0

    def test_none(self) -> None:
        assert _safe_text_length(None) == 0

    def test_list(self) -> None:
        assert _safe_text_length([1, 2, 3]) == 0


class TestDebugCompactPayload:
    def test_small_dict(self) -> None:
        payload = {"key": "value"}
        result = _debug_compact_payload(payload)
        assert result == {"key": "value"}

    def test_small_string(self) -> None:
        result = _debug_compact_payload("hello")
        assert result == "hello"

    def test_large_string_truncated(self) -> None:
        big = "x" * 3000
        result = _debug_compact_payload(big, max_chars=2000)
        assert isinstance(result, dict)
        assert result["_truncated"] is True
        assert len(result["preview"]) == 2000
        assert result["total_length"] == 3000

    def test_unserializable(self) -> None:
        class Obj:
            def __str__(self) -> str:
                return "custom_obj"

        result = _debug_compact_payload(Obj())
        assert result == "custom_obj"


class TestDebugToolArguments:
    def test_complete_dict(self) -> None:
        result = _debug_tool_arguments({"x": 1})
        assert result == {"x": 1}

    def test_incomplete_with_text(self) -> None:
        result = _debug_tool_arguments(None, arguments_text='{"y": 2}')
        assert result == {"y": 2}

    def test_empty(self) -> None:
        result = _debug_tool_arguments(None)
        assert result == {}

    def test_raw_text_incomplete(self) -> None:
        result = _debug_tool_arguments(None, arguments_text="incomplete json")
        assert isinstance(result, dict)
        assert "_raw_arguments_text" in result
