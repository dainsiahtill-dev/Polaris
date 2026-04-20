from __future__ import annotations

from polaris.kernelone.llm.providers.stream_thinking_parser import StreamThinkingParser


def _collect(parser: StreamThinkingParser, chunks: list[object]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for chunk in chunks:
        output.extend(parser.feed(chunk))
    output.extend(parser.flush())
    return [(kind, text) for kind, text in output if text]


def test_plain_content_passthrough() -> None:
    parser = StreamThinkingParser()
    items = _collect(parser, ["hello", " world"])
    assert items == [("content", "hello"), ("content", " world")]


def test_think_tag_split_across_chunks() -> None:
    parser = StreamThinkingParser()
    items = _collect(parser, ["before ", "<thi", "nk>reason", "</thi", "nk>", " after"])
    assert items == [
        ("content", "before "),
        ("thinking", "reason"),
        ("content", " after"),
    ]


def test_thought_variant_and_uppercase_partial_tags() -> None:
    parser = StreamThinkingParser()
    items = _collect(parser, ["A<THO", "UGHT>R1</TH", "OUGHT>B"])
    assert items == [("content", "A"), ("thinking", "R1"), ("content", "B")]


def test_non_string_token_is_accepted() -> None:
    parser = StreamThinkingParser()
    items = _collect(parser, ["x", 123, None, "<think>", "y", "</think>"])
    assert items == [
        ("content", "x"),
        ("content", "123"),
        ("thinking", "y"),
    ]


def test_nested_function_calls_wrapper_does_not_leak_closing_tag() -> None:
    parser = StreamThinkingParser()
    items = _collect(
        parser,
        [
            "前缀",
            '<function_calls><invoke name="write_file"><arg name="path">a.py</arg></invoke></function_calls>',
            "后缀",
        ],
    )
    visible = "".join(text for kind, text in items if kind == "content")
    assert visible == "前缀后缀"
    assert "</function_calls>" not in visible


def test_unclosed_tool_wrapper_flushes_remaining_text_instead_of_dropping() -> None:
    parser = StreamThinkingParser()
    items = _collect(
        parser,
        [
            "前缀",
            '<function_calls><invoke name="write_file"><arg name="path">a.py</arg>',
            "最终说明",
        ],
    )
    visible = "".join(text for kind, text in items if kind == "content")
    assert "前缀" in visible
    assert "最终说明" in visible
