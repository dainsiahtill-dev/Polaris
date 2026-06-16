"""Tests for Qwen3-Coder equals-style tool-call markup in XMLToolParser.

The qwen3.6-27b-code variant emits ``<function=NAME><parameter=KEY>VALUE</parameter>``
in content; the streaming decode path (stream/executor.py) routes through
``XMLToolParser``.  These tests pin the equals-style branch (section 8).
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.parsers.xml_based import XMLToolParser

QWEN3CODER_WRITE = (
    "<tool_call>\n"
    "<function=write_file>\n"
    "<parameter=file>\n"
    "index.html\n"
    "</parameter>\n"
    "<parameter=content>\n"
    "<!doctype html>\n"
    "<html></html>\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)


class TestQwen3CoderEqualsStyleXML:
    def test_parses_equals_style_write(self) -> None:
        calls = XMLToolParser.parse(QWEN3CODER_WRITE, allowed_tool_names=["write_file"])
        assert len(calls) == 1
        assert calls[0].name == "write_file"
        assert calls[0].arguments["file"] == "index.html"
        assert calls[0].arguments["content"] == "<!doctype html>\n<html></html>"

    def test_parses_multiple_equals_style(self) -> None:
        text = (
            "<function=read_file><parameter=path>a.js</parameter></function>"
            "<function=write_file><parameter=file>b.js</parameter><parameter=content>const x=1;</parameter></function>"
        )
        calls = XMLToolParser.parse(text, allowed_tool_names=["read_file", "write_file"])
        assert [c.name for c in calls] == ["read_file", "write_file"]

    def test_allowed_filter(self) -> None:
        text = "<function=frobnicate><parameter=x>1</parameter></function>"
        assert XMLToolParser.parse(text, allowed_tool_names=["write_file"]) == []

    def test_no_parameter_block_skipped(self) -> None:
        assert XMLToolParser.parse("<function=write_file></function>", allowed_tool_names=["write_file"]) == []

    def test_attribute_style_still_works(self) -> None:
        # Regression: the legacy name="..." style must not be broken by the new branch.
        text = '<function name="read_file"><param name="path">a.js</param></function>'
        calls = XMLToolParser.parse(text, allowed_tool_names=["read_file"])
        assert any(c.name == "read_file" for c in calls)
