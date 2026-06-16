"""Parser hardening regression tests (2026-06-16 reliability audit).

Three audit-found bugs where the weak-Director's VALID tool call was silently
dropped or corrupted by the parser, feeding the dead-letter / no-materialized
wall:

- H1-F1: the Qwen3-Coder ``<parameter=>`` truncation guard counted RAW openers,
  so a complete call whose value contained a literal ``<parameter=...>`` (docs,
  a tutorial quoting the tool syntax, a duplicate key) false-positived and the
  whole call was dropped. Now only an opener OUTSIDE every closed span counts.
- H1-F2: the JSON fallback regex handled only single-level nesting, so the FIRST
  call in a batch whose arguments had depth>=2 braces was skipped. Now a
  brace-depth + string aware scanner extracts top-level objects at any depth.
- H1-F3: XML equals-style param values were routed through ``parse_value`` which
  JSON-decoded a string body like ``[1, 2, 3]`` into a Python list, corrupting
  the written artifact. String-typed params now keep their raw text.
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.parsers.json_based import (
    JSONToolParser,
    _iter_top_level_json_objects,
)
from polaris.kernelone.llm.toolkit.parsers.textual_tool_recovery import (
    recover_textual_tool_calls,
)
from polaris.kernelone.llm.toolkit.parsers.xml_based import XMLToolParser


class TestQwen3CoderTruncationGuardFalsePositive:
    """H1-F1: a literal ``<parameter=>`` inside a value must not drop the call."""

    def test_textual_recovery_keeps_call_with_literal_marker_in_value(self) -> None:
        text = (
            "<function=write_file>"
            "<parameter=file>README.md</parameter>"
            "<parameter=content>Docs: emit <parameter=foo> then the path.</parameter>"
            "</function>"
        )
        calls = recover_textual_tool_calls(text, allowed_tool_names=["write_file"])
        assert len(calls) == 1
        assert calls[0]["tool"] == "write_file"
        assert calls[0]["arguments"]["content"] == "Docs: emit <parameter=foo> then the path."

    def test_xml_parser_keeps_call_with_literal_marker_in_value(self) -> None:
        text = (
            "<function=write_file>"
            "<parameter=file>README.md</parameter>"
            "<parameter=content>see <parameter=x> here</parameter>"
            "</function>"
        )
        calls = XMLToolParser.parse(text, allowed_tool_names=["write_file"])
        assert len(calls) == 1
        assert calls[0].arguments["content"] == "see <parameter=x> here"

    def test_genuine_mid_value_truncation_still_skipped(self) -> None:
        # content param opened but never closed (cut at token budget) -> skip,
        # so we never materialise a file with missing content.
        text = (
            "<function=write_file>"
            "<parameter=file>a.py</parameter>"
            "<parameter=content>partial body never closed"
        )
        assert recover_textual_tool_calls(text, allowed_tool_names=["write_file"]) == []
        assert XMLToolParser.parse(text, allowed_tool_names=["write_file"]) == []


class TestJsonDeepNestingFirstCall:
    """H1-F2: the first call with depth>=2 nested args must be recovered."""

    def test_first_call_with_deeply_nested_args_recovered(self) -> None:
        first = '{"name": "write_file", "arguments": {"path": "a.js", "meta": {"x": {"y": 1}}}}'
        second = '{"name": "read_file", "arguments": {"path": "b.py"}}'
        calls = JSONToolParser.parse(first + "\n" + second)
        names = [c.name for c in calls]
        assert "write_file" in names, f"deep-nested first call dropped: {names}"
        assert "read_file" in names

    def test_scanner_is_string_and_escape_aware(self) -> None:
        # Braces inside string values must not affect depth.
        text = '{"a": "x = {b: {c: 1}}"} {"d": 2}'
        objs = _iter_top_level_json_objects(text)
        assert objs == ['{"a": "x = {b: {c: 1}}"}', '{"d": 2}']

    def test_scanner_drops_truncated_trailing_object_keeps_leading(self) -> None:
        text = '{"name": "write_file", "arguments": {"path": "a.js"}} {"name": "read_file"'
        objs = _iter_top_level_json_objects(text)
        assert objs == ['{"name": "write_file", "arguments": {"path": "a.js"}}']


class TestXmlParamValueCoercion:
    """H1-F3: string-typed param values keep raw text; scalars still coerce."""

    def test_json_array_content_kept_as_string(self) -> None:
        text = (
            "<function=write_file>"
            "<parameter=file>data.json</parameter>"
            "<parameter=content>[1, 2, 3]</parameter>"
            "</function>"
        )
        calls = XMLToolParser.parse(text, allowed_tool_names=["write_file"])
        assert len(calls) == 1
        content = calls[0].arguments["content"]
        assert isinstance(content, str)
        assert content == "[1, 2, 3]"

    def test_scalar_param_still_coerced(self) -> None:
        text = (
            "<function=read_file>"
            "<parameter=path>a.py</parameter>"
            "<parameter=limit>50</parameter>"
            "</function>"
        )
        calls = XMLToolParser.parse(text, allowed_tool_names=["read_file"])
        assert len(calls) == 1
        assert calls[0].arguments["path"] == "a.py"
        assert calls[0].arguments["limit"] == 50
