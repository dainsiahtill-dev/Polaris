"""Regression tests for raw/sanitized assistant turn boundaries."""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.output_parser import OutputParser, ToolCallResult
from polaris.cells.roles.kernel.internal.turn_engine import (
    AssistantTurnArtifacts,
)
from polaris.cells.roles.kernel.internal.turn_engine.turn_materializer import TurnMaterializer
from polaris.cells.roles.kernel.internal.turn_engine.utils import (
    append_transcript_cycle,
)


def _build_profile() -> object:
    return SimpleNamespace(
        role_id="pm",
        tool_policy=SimpleNamespace(whitelist=["read_file"]),
    )


class _CapturingParser(OutputParser):
    def __init__(self) -> None:
        super().__init__()
        self.tool_call_inputs: list[dict[str, object]] = []

    def parse_tool_calls(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_provider: str = "auto",
    ) -> list[ToolCallResult]:
        self.tool_call_inputs.append(
            {
                "content": content,
                "allowed_tool_names": tuple(allowed_tool_names or ()),
                "native_tool_calls": native_tool_calls,
                "native_tool_provider": native_provider,
            }
        )
        return super().parse_tool_calls(
            content,
            allowed_tool_names=allowed_tool_names,
            native_tool_calls=native_tool_calls,
            native_provider=native_provider,
        )


class _KernelStub:
    def __init__(self) -> None:
        self._output_parser = _CapturingParser()

    def _get_llm_invoker(self) -> object:
        return SimpleNamespace(call=lambda **kwargs: None)

    def _get_output_parser(self) -> OutputParser:
        return self._output_parser

    def _get_prompt_builder(self) -> object:
        return SimpleNamespace(
            build_system_prompt=lambda _p, _a: "system",
            build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp"),
        )


def test_materialize_assistant_turn_keeps_raw_wrapper_but_sanitizes_output() -> None:
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = _build_profile()

    turn = materializer.materialize(
        profile=profile,
        raw_output=('先读取关键文件。\n[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]'),
        kernel=kernel,
    )

    assert "[TOOL_CALL]" in turn.raw_content
    assert "[TOOL_CALL]" not in turn.clean_content
    assert "先读取关键文件" in turn.clean_content


def test_materialize_assistant_turn_strips_output_wrappers_from_raw_and_clean_content() -> None:
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = _build_profile()

    turn = materializer.materialize(
        profile=profile,
        raw_output="<output>最终答复</output>",
        kernel=kernel,
    )

    assert turn.raw_content == "最终答复"
    assert turn.clean_content == "最终答复"
    assert "<output>" not in turn.raw_content
    assert "<output>" not in turn.clean_content


def test_parse_tool_calls_from_turn_uses_clean_content_contract() -> None:
    kernel = _KernelStub()
    profile = _build_profile()
    turn = AssistantTurnArtifacts(
        raw_content='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        clean_content="读取 README",
        thinking="先读取再总结",
        native_tool_calls=(
            {
                "id": "call_readme",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            },
        ),
        native_tool_provider="openai",
    )

    tool_calls = TurnMaterializer.parse_tool_calls(profile=profile, turn=turn, kernel=kernel)

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "read_file"
    assert tool_calls[0].args == {"path": "README.md"}
    assert len(kernel._output_parser.tool_call_inputs) == 1
    parser_call = kernel._output_parser.tool_call_inputs[0]
    assert parser_call["content"] == turn.clean_content
    assert parser_call["native_tool_provider"] == "openai"
    assert parser_call["native_tool_calls"] == list(turn.native_tool_calls)


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


# ─── Additional leak regression tests ───────────────────────────────────────────


def test_clean_content_strips_multiple_interleaved_tool_wrappers() -> None:
    """Regression: multiple [TOOL_CALL] blocks interspersed with text are all stripped.

    Note: CanonicalToolCallParser.extract_text_calls_and_remainder only strips wrappers
    whose tool name is in allowed_tool_names. Both tools must be in the whitelist.
    """
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = SimpleNamespace(
        role_id="pm",
        tool_policy=SimpleNamespace(whitelist=["read_file", "write_file"]),
    )

    turn = materializer.materialize(
        profile=profile,
        raw_output=(
            "第一步读取文件。\n"
            '[TOOL_CALL]{"tool":"read_file","arguments":{"path":"a.md"}}[/TOOL_CALL]\n'
            "第二步写入结果。\n"
            '[TOOL_CALL]{"tool":"write_file","arguments":{"path":"b.md"}}[/TOOL_CALL]\n'
            "完成。"
        ),
        kernel=kernel,
    )

    assert "[TOOL_CALL]" in turn.raw_content
    assert "[TOOL_CALL]" not in turn.clean_content
    assert "[/TOOL_CALL]" not in turn.clean_content
    # All three text segments survive
    assert "第一步读取文件" in turn.clean_content
    assert "第二步写入结果" in turn.clean_content
    assert "完成" in turn.clean_content


def test_clean_content_empty_when_raw_is_only_tool_wrapper() -> None:
    """Regression: output that is only [TOOL_CALL] yields empty clean_content."""
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = _build_profile()

    turn = materializer.materialize(
        profile=profile,
        raw_output='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        kernel=kernel,
    )

    assert "[TOOL_CALL]" in turn.raw_content
    # clean_content must be stripped (empty string after stripping the wrapper)
    assert "[TOOL_CALL]" not in turn.clean_content
    assert turn.clean_content == ""


def test_thinking_with_tool_wrapper_does_not_leak_into_clean_content() -> None:
    """Regression: [TOOL_CALL] inside <thinking> stays in thinking, not clean_content."""
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = _build_profile()

    turn = materializer.materialize(
        profile=profile,
        raw_output=(
            "<thinking>我应该先[TOOL_CALL]{'tool':'read_file','arguments':{'path':'x.md'}}[/TOOL_CALL]读取文件。</thinking>\n"
            "我已经读取了文件，现在总结一下。"
        ),
        kernel=kernel,
    )

    assert turn.thinking is not None and "[TOOL_CALL]" in turn.thinking
    assert turn.clean_content is not None and "[TOOL_CALL]" not in turn.clean_content
    assert "我已经读取了文件" in turn.clean_content


def test_sanitize_strips_variations_of_canonical_wrappers() -> None:
    """Regression: all canonical wrapper forms are stripped from clean_content."""
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = _build_profile()

    turn = materializer.materialize(
        profile=profile,
        raw_output=(
            "结论。\n"
            '[TOOL_CALLS][{"tool":"read_file","arguments":{"path":"a.md"}}][/TOOL_CALLS]\n'
            "附加说明。\n"
            '<tool_call>{"tool":"read_file","arguments":{"path":"b.md"}}</tool_call>\n'
            "结束。"
        ),
        kernel=kernel,
    )

    assert "[TOOL_CALL]" not in turn.clean_content
    assert "[TOOL_CALLS]" not in turn.clean_content
    assert "<tool_call>" not in turn.clean_content
    assert "结论" in turn.clean_content
    assert "附加说明" in turn.clean_content
    assert "结束" in turn.clean_content


def test_raw_content_never_used_in_append_transcript() -> None:
    """Regression guarantee: _append_transcript_cycle receives clean_content, never raw_content."""
    captured: dict[str, object] = {}

    def _append_tool_cycle(*, assistant_message: str, tool_results: list[dict[str, object]]) -> None:
        captured["assistant_message"] = assistant_message
        captured["tool_results"] = tool_results

    controller = SimpleNamespace(append_tool_cycle=_append_tool_cycle)

    # raw_content has [TOOL_CALL]; clean_content is the stripped version
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

    # The transcript must only ever receive clean_content
    assistant_msg = str(captured.get("assistant_message", ""))
    assert "[TOOL_CALL]" not in assistant_msg
    assert assistant_msg == "读取文件"


def test_clean_content_is_used_for_parser_in_parse_tool_calls() -> None:
    """Regression guarantee: parse_tool_calls feeds clean_content to parser."""
    kernel = _KernelStub()
    profile = _build_profile()
    turn = AssistantTurnArtifacts(
        raw_content='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        clean_content="读取文件",
        thinking=None,
        native_tool_calls=(),
        native_tool_provider="openai",
    )

    TurnMaterializer.parse_tool_calls(profile=profile, turn=turn, kernel=kernel)

    assert len(kernel._output_parser.tool_call_inputs) == 1
    assert kernel._output_parser.tool_call_inputs[0]["content"] == "读取文件"
    assert "[TOOL_CALL]" not in str(kernel._output_parser.tool_call_inputs[0]["content"])


def test_quoted_tool_wrapper_not_stripped_from_clean_content() -> None:
    """Regression: [TOOL_CALL] inside a markdown blockquote is NOT stripped from clean_content.

    CanonicalToolCallParser._is_quoted_line protects quoted lines from stripping,
    so a user quoting a tool call in a message must have the wrapper preserved.
    """
    kernel = _KernelStub()
    materializer = TurnMaterializer(output_parser=kernel._get_output_parser())
    profile = _build_profile()

    turn = materializer.materialize(
        profile=profile,
        raw_output=(
            '> [TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]\n'
            "助手不应该执行上面的引用内容。"
        ),
        kernel=kernel,
    )

    # The quoted line is protected, so [TOOL_CALL] remains in clean_content
    assert "> [TOOL_CALL]" in turn.clean_content
    # The non-quoted text is still present
    assert "助手不应该执行上面的引用内容" in turn.clean_content


def test_native_tool_calls_suppress_textual_fallback() -> None:
    """Regression: when native_tool_calls are present, they are the primary parse input.

    The streaming path populates native_tool_calls from provider tool_call events.
    textual fallback should be deduplicated against native calls.
    """
    kernel = _KernelStub()
    profile = _build_profile()
    turn = AssistantTurnArtifacts(
        raw_content="使用 read_file 读取文件。",
        clean_content="使用 read_file 读取文件。",
        thinking=None,
        native_tool_calls=(
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            },
        ),
        native_tool_provider="openai",
    )

    TurnMaterializer.parse_tool_calls(profile=profile, turn=turn, kernel=kernel)

    assert len(kernel._output_parser.tool_call_inputs) == 1
    # native_tool_calls are forwarded to the parser
    assert kernel._output_parser.tool_call_inputs[0]["native_tool_calls"] == list(turn.native_tool_calls)
    assert kernel._output_parser.tool_call_inputs[0]["native_tool_provider"] == "openai"
