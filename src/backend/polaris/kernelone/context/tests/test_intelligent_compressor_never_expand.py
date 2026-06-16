"""Tests for the T2-A never-EXPAND guard in IntelligentCompressor (T2-B wire too).

The greedy compression pass may append an LLM summary whose tokens exceed what
was saved, so the assembled output can become larger than the original. The
never-EXPAND guard must degrade to the best-effort smallest by dropping summary
items, and the compression result must never be larger than the input.

Run with:
    pytest polaris/kernelone/context/tests/test_intelligent_compressor_never_expand.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.kernelone.context.context_os.models_v2 import (
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    TranscriptEventV2 as TranscriptEvent,
)
from polaris.kernelone.context.intelligent_compressor import IntelligentCompressor


def _event(content: str, seq: int) -> TranscriptEvent:
    """Build a TranscriptEvent for testing."""
    return TranscriptEvent(
        event_id=f"event_{seq}",
        sequence=seq,
        role="assistant",
        kind="message",
        route="test",
        content=content,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata=(),
    )


def _projection(events: list[TranscriptEvent]) -> ContextOSProjection:
    """Build a ContextOSProjection wrapping the given events."""
    snapshot = ContextOSSnapshot(
        transcript_log=tuple(events),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    return ContextOSProjection(
        snapshot=snapshot,
        head_anchor="start",
        tail_anchor="end",
        active_window=tuple(events),
    )


class TestNeverExpand:
    pytestmark = pytest.mark.asyncio

    def _compressor(self) -> IntelligentCompressor:
        mock_llm = MagicMock(spec=["invoke", "invoke_stream"])
        mock_llm.invoke = AsyncMock()
        return IntelligentCompressor(llm=mock_llm, max_tokens=1000)

    async def test_summary_that_expands_is_dropped(self) -> None:
        """If the appended summary makes output >= original, drop the summary."""
        compressor = self._compressor()

        # Force _summarize_items to return a large summary that, if appended,
        # would expand the assembled context beyond the original size.
        huge_summary = "EXPANDING SUMMARY TEXT " * 200
        compressor._summarize_items = AsyncMock(return_value=huge_summary)  # type: ignore[method-assign]

        events = [_event(f"item {i}: " + "content " * 10, seq=i) for i in range(8)]
        projection = _projection(events)

        result = await compressor.compress(projection, target_tokens=10_000)

        # The guard must ensure the output never exceeds the original size.
        assert result.compressed_tokens <= result.original_tokens
        assert result.compression_ratio <= 1.0
        # The expanding summary must not appear in the final output.
        assert "EXPANDING SUMMARY TEXT" not in result.compressed_content

    async def test_normal_compression_unaffected(self) -> None:
        """When no expanding summary is involved, output is the selection."""
        compressor = self._compressor()
        events = [_event(f"important decision {i}", seq=i) for i in range(3)]
        projection = _projection(events)
        result = await compressor.compress(projection, target_tokens=10_000)
        assert result.compressed_tokens <= result.original_tokens
        assert result.compression_ratio <= 1.0
