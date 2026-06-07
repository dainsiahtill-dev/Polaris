"""Tests for textual tool-call recovery (non-function-calling model compat)."""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.parsers.textual_tool_recovery import (
    has_textual_tool_calls,
    recover_textual_tool_calls,
    strip_textual_tool_call_markers,
)

# Real captured gemma-4-12B outputs (raw HTTP layer).
GEMMA_READ_HEAD = '<|tool_call>call:repo_read_head{file:<|"|>src/utils/helpers.py<|"|>,n:50}<tool_call|>'
GEMMA_TREE = '<|tool_call>call:repo_tree{path:<|"|>.<|"|>}'
GEMMA_RG = '<|tool_call>call:repo_rg{pattern:<|"|>def -<|"|>,path:<|"|>src/<|"|>}'
GEMMA_READ_TAIL = '<|tool_call>call:repo_read_tail{count:10,file:<|"|>server.py<|"|>}'


class TestRecoverTextualToolCalls:
    def test_recovers_string_and_int_args(self) -> None:
        calls = recover_textual_tool_calls(GEMMA_READ_HEAD)
        assert calls == [
            {
                "tool": "repo_read_head",
                "arguments": {"file": "src/utils/helpers.py", "n": 50},
                "call_id": "",
            }
        ]

    def test_recovers_single_string_arg(self) -> None:
        calls = recover_textual_tool_calls(GEMMA_TREE)
        assert len(calls) == 1
        assert calls[0]["tool"] == "repo_tree"
        assert calls[0]["arguments"] == {"path": "."}

    def test_recovers_value_with_space(self) -> None:
        calls = recover_textual_tool_calls(GEMMA_RG)
        assert calls[0]["tool"] == "repo_rg"
        assert calls[0]["arguments"] == {"pattern": "def -", "path": "src/"}

    def test_multiple_calls_in_order(self) -> None:
        text = GEMMA_TREE + "\n" + GEMMA_READ_TAIL
        calls = recover_textual_tool_calls(text)
        assert [c["tool"] for c in calls] == ["repo_tree", "repo_read_tail"]

    def test_allowed_tool_names_filters_unknown(self) -> None:
        # repo_tree not allowed -> not recovered.
        calls = recover_textual_tool_calls(GEMMA_TREE, allowed_tool_names=["read_file", "repo_rg"])
        assert calls == []

    def test_allowed_tool_names_permits_known(self) -> None:
        calls = recover_textual_tool_calls(GEMMA_READ_HEAD, allowed_tool_names=["repo_read_head"])
        assert len(calls) == 1
        assert calls[0]["tool"] == "repo_read_head"

    def test_empty_and_none(self) -> None:
        assert recover_textual_tool_calls("") == []
        assert recover_textual_tool_calls(None) == []

    def test_plain_prose_not_recovered(self) -> None:
        assert recover_textual_tool_calls("Here is the answer, no tools needed.") == []

    def test_unterminated_brace_is_ignored(self) -> None:
        assert recover_textual_tool_calls('<|tool_call>call:repo_tree{path:<|"|>.') == []

    def test_array_arg_with_quoted_elements(self) -> None:
        # Gemma emits array args as `[<|"|>a<|"|>,<|"|>b<|"|>]`; the comma between
        # elements must NOT split the key:value pair.
        text = 'call:repo_rg{pattern:<|"|>TODO<|"|>,paths:[<|"|>backend<|"|>,<|"|>frontend<|"|>]}'
        calls = recover_textual_tool_calls(text)
        assert calls[0]["tool"] == "repo_rg"
        assert calls[0]["arguments"] == {"pattern": "TODO", "paths": ["backend", "frontend"]}

    def test_empty_array_arg(self) -> None:
        calls = recover_textual_tool_calls("call:some_tool{items:[]}")
        assert calls[0]["arguments"] == {"items": []}

    def test_array_of_ints(self) -> None:
        calls = recover_textual_tool_calls("call:some_tool{nums:[1,2,3]}")
        assert calls[0]["arguments"] == {"nums": [1, 2, 3]}

    def test_bool_and_null_coercion(self) -> None:
        calls = recover_textual_tool_calls("call:some_tool{flag:true,missing:null,depth:3}")
        args = calls[0]["arguments"]
        assert args["flag"] is True
        assert args["missing"] is None
        assert args["depth"] == 3


class TestHasTextualToolCalls:
    def test_detects_marker(self) -> None:
        assert has_textual_tool_calls(GEMMA_TREE) is True

    def test_detects_bare_call(self) -> None:
        assert has_textual_tool_calls("call:repo_tree{path:.}") is True

    def test_negative(self) -> None:
        assert has_textual_tool_calls("just text") is False
        assert has_textual_tool_calls("") is False


class TestStripTextualToolCallMarkers:
    def test_strips_call_span_and_markers(self) -> None:
        cleaned = strip_textual_tool_call_markers(GEMMA_READ_HEAD)
        assert "call:" not in cleaned
        assert "<|tool_call" not in cleaned
        assert '<|"|>' not in cleaned
        assert cleaned == ""

    def test_preserves_surrounding_prose(self) -> None:
        text = "I will read it.\n" + GEMMA_TREE + "\nDone."
        cleaned = strip_textual_tool_call_markers(text)
        assert "I will read it." in cleaned
        assert "Done." in cleaned
        assert "repo_tree" not in cleaned

    def test_allowed_filter_keeps_unmatched_span(self) -> None:
        cleaned = strip_textual_tool_call_markers(GEMMA_TREE, allowed_tool_names=["read_file"])
        # repo_tree span not stripped (not allowed), but stray markers removed.
        assert "repo_tree" in cleaned
