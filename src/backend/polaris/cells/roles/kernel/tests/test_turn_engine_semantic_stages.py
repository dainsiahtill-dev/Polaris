"""Regression tests for raw/sanitized assistant turn boundaries."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.output_parser import OutputParser
from polaris.cells.roles.kernel.internal.turn_engine import (
    AssistantTurnArtifacts,
    TurnEngine,
)
from polaris.cells.roles.kernel.internal.turn_engine.utils import (
    append_transcript_cycle,
)


def _build_profile() -> object:
    return SimpleNamespace(
        role_id="pm",
        tool_policy=SimpleNamespace(whitelist=["read_file"]),
    )


class _KernelStub:
    def __init__(self) -> None:
        self._output_parser = OutputParser()
        self.parser_calls: list[dict[str, object]] = []

    def _get_llm_caller(self) -> object:
        return SimpleNamespace(call=lambda **kwargs: None)

    def _get_output_parser(self) -> OutputParser:
        return self._output_parser

    def _get_prompt_builder(self) -> object:
        return SimpleNamespace(
            build_system_prompt=lambda _p, _a: "system",
            build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp"),
        )

    def _parse_content_and_thinking_tool_calls(
        self,
        content: str,
        thinking: str | None,
        profile: object,
        native_tool_calls: list[dict[str, object]] | None,
        native_tool_provider: str,
    ) -> list[dict[str, object]]:
        self.parser_calls.append(
            {
                "content": content,
                "thinking": thinking,
                "profile": profile,
                "native_tool_calls": native_tool_calls,
                "native_tool_provider": native_tool_provider,
            }
        )
        return []


def test_materialize_assistant_turn_keeps_raw_wrapper_but_sanitizes_output() -> None:
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output=('先读取关键文件。\n[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]'),
    )

    assert "[TOOL_CALL]" in turn.raw_content
    assert "[TOOL_CALL]" not in turn.clean_content
    assert "先读取关键文件" in turn.clean_content


def test_materialize_assistant_turn_strips_output_wrappers_from_raw_and_clean_content() -> None:
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output="<output>最终答复</output>",
    )

    assert turn.raw_content == "最终答复"
    assert turn.clean_content == "最终答复"
    assert "<output>" not in turn.raw_content
    assert "<output>" not in turn.clean_content


def test_parse_tool_calls_from_turn_uses_clean_content_contract() -> None:
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
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

    engine._parse_tool_calls_from_turn(profile=profile, turn=turn)

    assert len(kernel.parser_calls) == 1
    parser_call = kernel.parser_calls[0]
    assert parser_call["content"] == turn.clean_content
    assert parser_call["thinking"] == turn.thinking
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
    engine = TurnEngine(kernel=kernel)
    profile = SimpleNamespace(
        role_id="pm",
        tool_policy=SimpleNamespace(whitelist=["read_file", "write_file"]),
    )

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output=(
            "第一步读取文件。\n"
            '[TOOL_CALL]{"tool":"read_file","arguments":{"path":"a.md"}}[/TOOL_CALL]\n'
            "第二步写入结果。\n"
            '[TOOL_CALL]{"tool":"write_file","arguments":{"path":"b.md"}}[/TOOL_CALL]\n'
            "完成。"
        ),
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
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
    )

    assert "[TOOL_CALL]" in turn.raw_content
    # clean_content must be stripped (empty string after stripping the wrapper)
    assert "[TOOL_CALL]" not in turn.clean_content
    assert turn.clean_content == ""


def test_thinking_with_tool_wrapper_does_not_leak_into_clean_content() -> None:
    """Regression: [TOOL_CALL] inside <thinking> stays in thinking, not clean_content."""
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output=(
            "<thinking>我应该先[TOOL_CALL]{'tool':'read_file','arguments':{'path':'x.md'}}[/TOOL_CALL]读取文件。</thinking>\n"
            "我已经读取了文件，现在总结一下。"
        ),
    )

    assert turn.thinking is not None and "[TOOL_CALL]" in turn.thinking
    assert turn.clean_content is not None and "[TOOL_CALL]" not in turn.clean_content
    assert "我已经读取了文件" in turn.clean_content


def test_sanitize_strips_variations_of_canonical_wrappers() -> None:
    """Regression: all canonical wrapper forms are stripped from clean_content."""
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output=(
            "结论。\n"
            '[TOOL_CALLS][{"tool":"read_file","arguments":{"path":"a.md"}}][/TOOL_CALLS]\n'
            "附加说明。\n"
            '<tool_call>{"tool":"read_file","arguments":{"path":"b.md"}}</tool_call>\n'
            "结束。"
        ),
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
    """Regression guarantee: _parse_tool_calls_from_turn feeds clean_content to parser."""
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()
    turn = AssistantTurnArtifacts(
        raw_content='[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]',
        clean_content="读取文件",
        thinking=None,
        native_tool_calls=(),
        native_tool_provider="openai",
    )

    engine._parse_tool_calls_from_turn(profile=profile, turn=turn)

    assert len(kernel.parser_calls) == 1
    assert kernel.parser_calls[0]["content"] == "读取文件"
    assert "[TOOL_CALL]" not in str(kernel.parser_calls[0]["content"])


def test_quoted_tool_wrapper_not_stripped_from_clean_content() -> None:
    """Regression: [TOOL_CALL] inside a markdown blockquote is NOT stripped from clean_content.

    CanonicalToolCallParser._is_quoted_line protects quoted lines from stripping,
    so a user quoting a tool call in a message must have the wrapper preserved.
    """
    kernel = _KernelStub()
    engine = TurnEngine(kernel=kernel)
    profile = _build_profile()

    turn = engine._materialize_assistant_turn(
        profile=profile,
        raw_output=(
            '> [TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]\n'
            "助手不应该执行上面的引用内容。"
        ),
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
    engine = TurnEngine(kernel=kernel)
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

    engine._parse_tool_calls_from_turn(profile=profile, turn=turn)

    assert len(kernel.parser_calls) == 1
    # native_tool_calls are forwarded to the parser
    assert kernel.parser_calls[0]["native_tool_calls"] == list(turn.native_tool_calls)
    assert kernel.parser_calls[0]["native_tool_provider"] == "openai"
