"""Tests for TranscriptMerger sequence uniqueness.

验证：
1. 工具调用序列号唯一性
2. 10 个工具调用的序列号严格递增
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2 as TranscriptEvent
from polaris.kernelone.context.context_os.pipeline.contracts import PipelineInput
from polaris.kernelone.context.context_os.pipeline.stages import TranscriptMerger


class TestTranscriptMergerSequence:
    """Sequence number correctness for tool calls."""

    def _make_input(self, messages: list[dict[str, Any]]) -> PipelineInput:
        """Helper to build PipelineInput with empty existing transcript."""
        return PipelineInput(
            existing_snapshot_transcript=(),
            messages=messages,
        )

    def test_tool_call_sequences_are_unique(self) -> None:
        """10 tool calls must receive unique sequence numbers."""
        merger = TranscriptMerger()
        tool_calls = [{"name": f"tool_{i}", "arguments": {}} for i in range(10)]
        inp = self._make_input(
            [
                {
                    "role": "assistant",
                    "content": "calling tools",
                    "metadata": {"tool_calls": tool_calls},
                }
            ]
        )

        out = merger.process(inp)
        sequences = [evt.sequence for evt in out.transcript]

        assert len(sequences) == len(set(sequences)), f"Duplicate sequences found: {sequences}"

    def test_tool_call_sequences_strictly_increasing(self) -> None:
        """Sequence numbers must strictly increase across tool calls."""
        merger = TranscriptMerger()
        tool_calls = [{"name": f"tool_{i}", "arguments": {}} for i in range(10)]
        inp = self._make_input(
            [
                {
                    "role": "assistant",
                    "content": "calling tools",
                    "metadata": {"tool_calls": tool_calls},
                }
            ]
        )

        out = merger.process(inp)
        sequences = [evt.sequence for evt in out.transcript]

        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1], f"Sequence not strictly increasing at index {i}: {sequences}"

    def test_sequences_start_from_zero_when_empty(self) -> None:
        """When existing transcript is empty, sequences start from 0."""
        merger = TranscriptMerger()
        inp = self._make_input(
            [
                {
                    "role": "user",
                    "content": "hello",
                }
            ]
        )

        out = merger.process(inp)
        sequences = [evt.sequence for evt in out.transcript]

        assert sequences[0] == 0

    def test_sequences_continue_from_existing(self) -> None:
        """New events continue sequence after existing transcript."""
        merger = TranscriptMerger()
        existing = [
            TranscriptEvent(
                event_id="e0",
                sequence=5,
                role="user",
                kind="user_turn",
                route="turn",
                content="previous",
            )
        ]
        inp = PipelineInput(
            existing_snapshot_transcript=tuple(existing),
            messages=[{"role": "assistant", "content": "reply"}],
        )

        out = merger.process(inp)
        sequences = [evt.sequence for evt in out.transcript]

        # The existing event (sequence=5) is preserved, new event gets sequence=6
        assert sequences == [5, 6]
