"""ADR-0090 I1: empty-args tool calls are provisional mid-stream.

vLLM/qwen-style OpenAI-compatible servers open a streamed tool call with
``arguments: ""``/``{}`` before the argument deltas arrive. Emitting that
placeholder mid-stream made the partial call execute alongside the completed
one (observed live: ``edit_blocks{}`` burning the mutation-contract retry).

Contract under test:
- mid-stream: an empty-arguments payload is NEVER emitted;
- the completed call is emitted exactly once;
- a legitimately no-arg call is emitted exactly once, at the end-of-stream flush.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.llm.engine.stream import StreamExecutor
from polaris.kernelone.llm.engine.stream.tool_accumulator import _ToolCallAccumulator


def _accumulate(
    executor: StreamExecutor,
    pending: dict[str, _ToolCallAccumulator],
    tool_call: dict[str, Any],
    *,
    ordinal: int = 1,
) -> dict[str, Any] | None:
    return executor._accumulate_stream_tool_call(
        pending,
        tool_call,
        ordinal=ordinal,
        provider_type="openai_compat",
    )


class TestEmptyArgsProvisional:
    def test_empty_dict_arguments_not_emitted_mid_stream(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        emitted = _accumulate(
            executor,
            pending,
            {"tool": "repo_rg", "call_id": "c1", "index": 0, "arguments": {}},
        )

        assert emitted is None

    def test_empty_string_arguments_not_emitted_mid_stream(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        emitted = _accumulate(
            executor,
            pending,
            {"tool": "repo_rg", "call_id": "c1", "index": 0, "arguments": ""},
        )

        assert emitted is None

    def test_empty_json_text_placeholder_not_emitted_mid_stream(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        emitted = _accumulate(
            executor,
            pending,
            {
                "tool": "edit_blocks",
                "call_id": "c9",
                "index": 0,
                "arguments": "{}",
            },
        )

        assert emitted is None


class TestCompletedCallEmittedOnce:
    def test_nested_anthropic_arguments_preserve_shape_and_report_assembly_provenance(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        placeholder = executor._accumulate_stream_tool_call(
            pending,
            {
                "tool": "submit_structured_role_output",
                "call_id": "ce-result",
                "content_block_index": 0,
                "arguments": {},
                "arguments_text": "{}",
                "arguments_complete": True,
            },
            ordinal=1,
            provider_type="anthropic_compat",
        )
        emitted = executor._accumulate_stream_tool_call(
            pending,
            {
                "tool": "",
                "call_id": "",
                "content_block_index": 0,
                "arguments": {},
                "arguments_text": (
                    '{"construction_plan":{"task_plans":{"TASK-1":'
                    '{"provider_declarations":[]}}},'
                    '"project_completion_contract":{},"risk_flags":[]}'
                ),
                "arguments_complete": False,
            },
            ordinal=2,
            provider_type="anthropic_compat",
        )

        assert placeholder is None
        assert emitted is not None
        assert emitted["arguments"]["construction_plan"]["task_plans"]["TASK-1"] == {"provider_declarations": []}
        assert emitted["provider_meta"]["assembly"] == {
            "argument_source": "stream_fragments",
            "complete_snapshot_count": 0,
            "delta_count": 2,
            "explicit_complete_count": 0,
            "fragment_count": 1,
            "provisional_placeholder_count": 1,
        }

    def test_placeholder_then_completed_emits_exactly_once(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        first = _accumulate(
            executor,
            pending,
            {"tool": "repo_rg", "call_id": "c1", "index": 0, "arguments": {}},
        )
        second = _accumulate(
            executor,
            pending,
            {
                "tool": "repo_rg",
                "call_id": "c1",
                "index": 0,
                "arguments_text": '{"pattern": "class ExpressionWrapper"}',
                "arguments_complete": True,
            },
            ordinal=2,
        )

        assert first is None
        assert second is not None
        assert second["tool"] == "repo_rg"
        assert second["arguments"] == {"pattern": "class ExpressionWrapper"}

        # End-of-stream flush must not duplicate the already-emitted payload.
        accumulator = next(iter(pending.values()))
        assert executor._finalize_stream_tool_call(accumulator) is None

    def test_fragmented_arguments_emit_once_when_json_completes(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        first = _accumulate(
            executor,
            pending,
            {
                "tool": "repo_read_slice",
                "call_id": "c2",
                "index": 0,
                "arguments_text": '{"file": "django/db/models/expressi',
            },
        )
        second = _accumulate(
            executor,
            pending,
            {
                "tool": "repo_read_slice",
                "call_id": "c2",
                "index": 0,
                "arguments_text": 'ons.py", "start": 1, "end": 80}',
            },
            ordinal=2,
        )

        assert first is None
        assert second is not None
        assert second["arguments"] == {
            "file": "django/db/models/expressions.py",
            "start": 1,
            "end": 80,
        }


class TestNoArgCallFlushEmission:
    def test_end_of_stream_event_metadata_carries_provider_argument_audit(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        assert (
            _accumulate(
                executor,
                pending,
                {"tool": "repo_tree", "call_id": "c-meta", "index": 0, "arguments": {}},
            )
            is None
        )
        accumulator = next(iter(pending.values()))
        assert executor._finalize_stream_tool_call(accumulator) is not None

        metadata = executor._stream_tool_call_event_meta(
            accumulator,
            provider_type="openai_compat",
            trace_id="trace-meta",
            finalized=True,
        )

        assert metadata["finalized"] is True
        assert metadata["tool_call_argument_audit"] == accumulator.argument_audit

    def test_end_of_stream_flush_records_provider_argument_audit(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        mid_stream = _accumulate(
            executor,
            pending,
            {"tool": "repo_tree", "call_id": "c-audit", "index": 0, "arguments": {}},
        )
        assert mid_stream is None

        accumulator = next(iter(pending.values()))
        assert accumulator.argument_audit == {}

        flushed = executor._finalize_stream_tool_call(accumulator)

        assert flushed is not None
        assert accumulator.argument_audit["provider"] == "openai_compat"
        assert accumulator.argument_audit["tool_name"] == "repo_tree"
        assert accumulator.argument_audit["call_id"] == "c-audit"
        assert accumulator.argument_audit["raw_arguments_sha256"]
        assert accumulator.argument_audit["decoded_arguments_sha256"]

    def test_legit_no_arg_call_emitted_exactly_once_at_flush(self) -> None:
        executor = StreamExecutor()
        pending: dict[str, _ToolCallAccumulator] = {}

        mid_stream = _accumulate(
            executor,
            pending,
            {"tool": "repo_tree", "call_id": "c3", "index": 0, "arguments": {}},
        )
        assert mid_stream is None

        accumulator = next(iter(pending.values()))
        flushed = executor._finalize_stream_tool_call(accumulator)
        assert flushed is not None
        assert flushed["tool"] == "repo_tree"
        assert flushed["arguments"] == {}

        # Idempotent: a second flush emits nothing.
        assert executor._finalize_stream_tool_call(accumulator) is None
