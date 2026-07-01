"""Regression tests for assistant-visible content and transcript boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.output_parser import OutputParser
from polaris.cells.roles.kernel.internal.turn_engine import (
    AssistantTurnArtifacts,
    assistant_raw_content,
)
from polaris.cells.roles.kernel.internal.turn_engine.utils import (
    append_transcript_cycle,
    sanitize_assistant_transcript_message,
)
from polaris.cells.roles.kernel.services.contracts import IOutputParser


def _native_read_file_call(path: str = "README.md") -> dict[str, Any]:
    return {
        "id": "call_readme",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": {"path": path},
        },
    }


def test_output_parser_strips_tool_wrapper_from_visible_content() -> None:
    parser = OutputParser()

    parsed = parser.parse_thinking(
        '先读取关键文件。\n[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]'
    )

    assert "[TOOL_CALL]" not in parsed.clean_content
    assert "[/TOOL_CALL]" not in parsed.clean_content
    assert "先读取关键文件" in parsed.clean_content


def test_output_parser_strips_output_wrappers_from_visible_content() -> None:
    parser = OutputParser()

    parsed = parser.parse_thinking("<output>最终答复</output>")

    assert parsed.clean_content == "最终答复"
    assert "<output>" not in parsed.clean_content


def test_parser_uses_native_tool_calls_and_ignores_textual_fallback() -> None:
    parser = OutputParser()

    tool_calls = parser.parse_tool_calls(
        '文本里的工具调用不应执行：[TOOL_CALL]{"tool":"read_file","arguments":{"path":"stale.md"}}[/TOOL_CALL]',
        native_tool_calls=[_native_read_file_call()],
        native_provider="openai",
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "read_file"
    assert tool_calls[0].args == {"path": "README.md"}


def test_textual_wrapper_without_native_tool_call_is_not_executable() -> None:
    parser = OutputParser()

    tool_calls = parser.parse_tool_calls(
        '[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        native_tool_calls=None,
        native_provider="openai",
    )

    assert tool_calls == []


def test_execution_parser_uses_typed_raw_content_boundary() -> None:
    parser = OutputParser()

    parser_hints = OutputParser.parse_execution_tool_calls.__annotations__
    protocol_hints = IOutputParser.parse_execution_tool_calls.__annotations__
    artifact_hints = AssistantTurnArtifacts.__annotations__

    assert parser_hints["content"] == "AssistantRawContent"
    assert protocol_hints["content"] == "AssistantRawContent"
    assert not hasattr(IOutputParser, "parse_tool_calls")
    assert artifact_hints["raw_content"] == "AssistantRawContent"
    assert artifact_hints["clean_content"] == "AssistantCleanContent"

    tool_calls = parser.parse_execution_tool_calls(
        assistant_raw_content("raw parser input"),
        native_tool_calls=[_native_read_file_call()],
        native_provider="openai",
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "read_file"


def test_append_transcript_cycle_persists_sanitized_content_only() -> None:
    captured: dict[str, object] = {}

    def _append_tool_cycle(*, assistant_message: str, tool_results: list[dict[str, object]]) -> None:
        captured["assistant_message"] = assistant_message
        captured["tool_results"] = tool_results

    controller = SimpleNamespace(append_tool_cycle=_append_tool_cycle)
    turn = AssistantTurnArtifacts(
        raw_content='分析结果 [TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        clean_content="分析结果",
        thinking=None,
    )

    append_transcript_cycle(
        controller=controller,
        turn=turn,
        tool_results=[{"tool": "read_file", "success": True}],
    )

    assert captured["assistant_message"] == "分析结果"
    assert captured["tool_results"] == [{"tool": "read_file", "success": True}]


def test_clean_content_strips_multiple_interleaved_tool_wrappers() -> None:
    raw = (
        "第一步读取文件。\n"
        '[TOOL_CALL]{"tool":"read_file","arguments":{"path":"a.md"}}[/TOOL_CALL]\n'
        "第二步写入结果。\n"
        '[TOOL_CALL]{"tool":"write_file","arguments":{"path":"b.md"}}[/TOOL_CALL]\n'
        "完成。"
    )

    clean = sanitize_assistant_transcript_message(
        raw,
        allowed_tool_names=["read_file", "write_file"],
    )

    assert "[TOOL_CALL]" not in clean
    assert "[/TOOL_CALL]" not in clean
    assert "第一步读取文件" in clean
    assert "第二步写入结果" in clean
    assert "完成" in clean


def test_clean_content_empty_when_raw_is_only_tool_wrapper() -> None:
    clean = sanitize_assistant_transcript_message(
        '[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        allowed_tool_names=["read_file"],
    )

    assert clean == ""


def test_thinking_with_tool_wrapper_does_not_leak_into_clean_content() -> None:
    parser = OutputParser()

    parsed = parser.parse_thinking(
        "<thinking>我应该先[TOOL_CALL]{'tool':'read_file','arguments':{'path':'x.md'}}[/TOOL_CALL]读取文件。</thinking>\n"
        "我已经读取了文件，现在总结一下。"
    )

    assert parsed.thinking is not None and "[TOOL_CALL]" in parsed.thinking
    assert "[TOOL_CALL]" not in parsed.clean_content
    assert "我已经读取了文件" in parsed.clean_content


def test_sanitize_strips_variations_of_canonical_wrappers() -> None:
    raw = (
        "结论。\n"
        '[TOOL_CALLS][{"tool":"read_file","arguments":{"path":"a.md"}}][/TOOL_CALLS]\n'
        "附加说明。\n"
        '<tool_call>{"tool":"read_file","arguments":{"path":"b.md"}}</tool_call>\n'
        "结束。"
    )

    clean = sanitize_assistant_transcript_message(raw, allowed_tool_names=["read_file"])

    assert "[TOOL_CALL]" not in clean
    assert "[TOOL_CALLS]" not in clean
    assert "<tool_call>" not in clean
    assert "结论" in clean
    assert "附加说明" in clean
    assert "结束" in clean


def test_raw_content_never_used_in_append_transcript() -> None:
    captured: dict[str, object] = {}

    def _append_tool_cycle(*, assistant_message: str, tool_results: list[dict[str, object]]) -> None:
        captured["assistant_message"] = assistant_message
        captured["tool_results"] = tool_results

    controller = SimpleNamespace(append_tool_cycle=_append_tool_cycle)
    turn = AssistantTurnArtifacts(
        raw_content='[TOOL_CALL]{"tool":"read_file"}[/TOOL_CALL]',
        clean_content="读取文件",
        thinking=None,
    )

    append_transcript_cycle(
        controller=controller,
        turn=turn,
        tool_results=[],
    )

    assistant_msg = str(captured.get("assistant_message", ""))
    assert "[TOOL_CALL]" not in assistant_msg
    assert assistant_msg == "读取文件"


def test_quoted_tool_wrapper_not_stripped_from_clean_content() -> None:
    raw = (
        '> [TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]\n助手不应该执行上面的引用内容。'
    )

    clean = sanitize_assistant_transcript_message(raw, allowed_tool_names=["read_file"])

    assert "> [TOOL_CALL]" in clean
    assert "助手不应该执行上面的引用内容" in clean
