from __future__ import annotations

import pytest
from polaris.kernelone.llm.engine.stream_executor import StreamExecutor


class _FakeTextToolProvider:
    async def invoke_stream(self, prompt: str, model: str, config: dict[str, object]):
        del prompt, model, config
        yield '<tool_call>{"tool":"read_file","arguments":{"path":"README.md"}}</tool_call>'


@pytest.mark.asyncio
async def test_text_stream_tool_call_is_promoted_to_structured_event() -> None:
    executor = StreamExecutor(workspace=".")
    provider = _FakeTextToolProvider()

    events = [
        event
        async for event in executor._invoke_text_stream(
            provider_instance=provider,
            prompt_input="inspect README",
            model="minimax",
            invoke_cfg={"timeout": 5},
            trace_id="test-trace",
        )
    ]

    tool_events = [event for event in events if event.type.value == "tool_call"]

    assert len(tool_events) == 1
    assert tool_events[0].tool_call == {
        "tool": "read_file",
        "arguments": {"path": "README.md"},
        "call_id": "xml_minimax_1",
    }


def test_finalize_skips_anthropic_placeholder_without_json_delta() -> None:
    executor = StreamExecutor(workspace=".")
    pending: dict[str, object] = {}

    emitted = executor._accumulate_stream_tool_call(
        pending,  # type: ignore[arg-type]
        {
            "tool": "read_file",
            "arguments": {},
            "arguments_text": "{}",
            "arguments_complete": True,
            "call_id": "toolu_1",
            "content_block_index": 0,
        },
        ordinal=0,
        provider_type="anthropic_compat",
    )

    assert emitted is None
    accumulator = next(iter(pending.values()))
    finalized = executor._finalize_stream_tool_call(accumulator)  # type: ignore[arg-type]
    assert finalized is None


def test_openai_empty_arguments_are_not_treated_as_anthropic_placeholders() -> None:
    executor = StreamExecutor(workspace=".")
    pending: dict[str, object] = {}

    emitted = executor._accumulate_stream_tool_call(
        pending,  # type: ignore[arg-type]
        {
            "tool": "list_files",
            "arguments": {},
            "arguments_text": "{}",
            "arguments_complete": True,
            "call_id": "call_openai_1",
            "index": 0,
        },
        ordinal=0,
        provider_type="openai_compat",
    )

    assert emitted is not None
    assert emitted["tool"] == "list_files"
    assert emitted["arguments"] == {}
