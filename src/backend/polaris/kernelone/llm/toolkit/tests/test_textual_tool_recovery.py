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
LFM_READ_HEAD = '<|tool_call_start|>[repo_read_head(file="src/utils/helpers.py", n=50)]<|tool_call_end|>'
LFM_MULTI_CALL = (
    '<|tool_call_start|>[repo_tree(path="."), repo_rg(pattern="TODO", paths=["backend", "frontend"])]<|tool_call_end|>'
)
XML_PYTHONIC_READ_HEAD = '<tool_call>repo_read_head(file="src/utils/helpers.py", n=50)</tool_call>'


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

    def test_recovers_lfm_pythonic_tool_call_block(self) -> None:
        calls = recover_textual_tool_calls(LFM_READ_HEAD)
        assert calls == [
            {
                "tool": "repo_read_head",
                "arguments": {"file": "src/utils/helpers.py", "n": 50},
                "call_id": "",
            }
        ]

    def test_recovers_multiple_lfm_pythonic_calls_in_order(self) -> None:
        calls = recover_textual_tool_calls(LFM_MULTI_CALL)
        assert [call["tool"] for call in calls] == ["repo_tree", "repo_rg"]
        assert calls[0]["arguments"] == {"path": "."}
        assert calls[1]["arguments"] == {
            "pattern": "TODO",
            "paths": ["backend", "frontend"],
        }

    def test_lfm_allowed_tool_names_filters_unknown(self) -> None:
        calls = recover_textual_tool_calls(LFM_MULTI_CALL, allowed_tool_names=["repo_rg"])
        assert len(calls) == 1
        assert calls[0]["tool"] == "repo_rg"

    def test_recovers_xml_pythonic_tool_call_block(self) -> None:
        calls = recover_textual_tool_calls(XML_PYTHONIC_READ_HEAD)
        assert calls == [
            {
                "tool": "repo_read_head",
                "arguments": {"file": "src/utils/helpers.py", "n": 50},
                "call_id": "",
            }
        ]


class TestHasTextualToolCalls:
    def test_detects_marker(self) -> None:
        assert has_textual_tool_calls(GEMMA_TREE) is True

    def test_detects_bare_call(self) -> None:
        assert has_textual_tool_calls("call:repo_tree{path:.}") is True

    def test_detects_lfm_marker(self) -> None:
        assert has_textual_tool_calls(LFM_READ_HEAD) is True

    def test_detects_bare_lfm_list(self) -> None:
        assert has_textual_tool_calls('[repo_tree(path=".")]') is True

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

    def test_strips_lfm_call_span_and_markers(self) -> None:
        cleaned = strip_textual_tool_call_markers("I will inspect it.\n" + LFM_READ_HEAD + "\nDone.")
        assert "I will inspect it." in cleaned
        assert "Done." in cleaned
        assert "repo_read_head" not in cleaned
        assert "<|tool_call_start|>" not in cleaned

    def test_strips_xml_pythonic_call_span_and_markers(self) -> None:
        cleaned = strip_textual_tool_call_markers("I will inspect it.\n" + XML_PYTHONIC_READ_HEAD + "\nDone.")
        assert "I will inspect it." in cleaned
        assert "Done." in cleaned
        assert "repo_read_head" not in cleaned
        assert "<tool_call>" not in cleaned


# Real captured qwen3.6-27b-code outputs (equals-style markup leaked into content
# when the vLLM tool-call-parser did not convert it to native tool_calls).
QWEN3CODER_WRITE = (
    "<tool_call>\n"
    "<function=write_file>\n"
    "<parameter=file>\n"
    "js/bricks.js\n"
    "</parameter>\n"
    "<parameter=content>\n"
    "export function makeBricks() {\n"
    "  return [1, 2, 3];\n"
    "}\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)
QWEN3CODER_MULTI = (
    "<function=read_file><parameter=path>a.js</parameter></function>"
    "<function=write_file><parameter=file>b.js</parameter><parameter=content>const x = 1;</parameter></function>"
)


class TestQwen3CoderEqualsStyle:
    def test_recovers_write_file_with_code_content(self) -> None:
        calls = recover_textual_tool_calls(QWEN3CODER_WRITE, allowed_tool_names=["write_file", "read_file"])
        assert len(calls) == 1
        assert calls[0]["tool"] == "write_file"
        assert calls[0]["arguments"]["file"] == "js/bricks.js"
        # Internal indentation of the code body is preserved.
        assert calls[0]["arguments"]["content"] == "export function makeBricks() {\n  return [1, 2, 3];\n}"

    def test_recovers_write_file_alias_when_only_canonical_allowed(self) -> None:
        text = (
            "<function=create_file>"
            "<parameter=path>src/app.py</parameter>"
            "<parameter=text>print('ok')\n</parameter>"
            "</function>"
        )

        calls = recover_textual_tool_calls(text, allowed_tool_names=["write_file"])

        assert len(calls) == 1
        assert calls[0]["tool"] == "write_file"
        assert calls[0]["arguments"] == {"file": "src/app.py", "content": "print('ok')"}

    def test_recovers_multiple_calls_in_order(self) -> None:
        calls = recover_textual_tool_calls(QWEN3CODER_MULTI, allowed_tool_names=["read_file", "write_file"])
        assert [c["tool"] for c in calls] == ["read_file", "write_file"]
        assert calls[0]["arguments"]["path"] == "a.js"
        assert calls[1]["arguments"]["content"] == "const x = 1;"

    def test_has_textual_detects_equals_style(self) -> None:
        assert has_textual_tool_calls(QWEN3CODER_WRITE) is True
        assert has_textual_tool_calls("<function=write_file><parameter=file>a</parameter></function>") is True

    def test_allowed_filter_rejects_unlisted_tool(self) -> None:
        prose = "Use the <function=frobnicate><parameter=x>1</parameter></function> helper."
        assert recover_textual_tool_calls(prose, allowed_tool_names=["write_file"]) == []

    def test_truncated_mid_value_is_not_recovered(self) -> None:
        # No closed <parameter> block -> never materialise half a file from a guess.
        assert recover_textual_tool_calls("<function=write_file><parameter=content>half a fil", ["write_file"]) == []

    def test_truncated_after_first_param_is_not_recovered(self) -> None:
        # file param closed but content param cut off mid-value (real zuoce truncation):
        # must NOT recover write_file(file=...) with missing content.
        truncated = "<function=write_file><parameter=file>game.js</parameter><parameter=content>(function () { var x ="
        assert recover_textual_tool_calls(truncated, ["write_file"]) == []

    def test_no_parameters_is_not_recovered(self) -> None:
        assert recover_textual_tool_calls("<function=write_file></function>", ["write_file"]) == []

    def test_strips_equals_style_span(self) -> None:
        cleaned = strip_textual_tool_call_markers("Plan.\n" + QWEN3CODER_WRITE + "\nDone.", ["write_file"])
        assert "Plan." in cleaned
        assert "Done." in cleaned
        assert "<function=" not in cleaned
        assert "makeBricks" not in cleaned
