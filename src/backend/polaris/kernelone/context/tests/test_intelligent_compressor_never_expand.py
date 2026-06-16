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
        """If the appended summary makes output >= original, drop the summary.

        This test must *exercise* the never-EXPAND drop branch, not pass
        vacuously. To enter the summary path the greedy budget must be hit while
        items remain, and the appended summary must actually expand the
        assembled context beyond the original size. We assert both:
          1. ``_summarize_items`` was invoked (the summary path was taken).
          2. A summary-bearing selection that *expanded* past the original was
             assembled and then dropped (the guard's drop branch fired), so the
             final result is smaller than the original and free of the summary.
        """
        compressor = self._compressor()

        # Small items + a tight target force the greedy loop into the summary
        # branch with items still remaining; the per-summary assembly overhead
        # then makes the assembled context larger than the original, triggering
        # the drop. The marker is short enough to fit the greedy budget gate so
        # that it is actually appended (otherwise the path is never taken).
        marker = "MARK"
        compressor._summarize_items = AsyncMock(return_value=marker)  # type: ignore[method-assign]

        events = [_event("xxxxxxxx", seq=i) for i in range(3)]
        projection = _projection(events)

        # Instrument the assembly to confirm a summary-bearing selection that
        # *expands* past the original was built before the guard dropped it.
        original_build = compressor._build_compressed_context
        expanding_build_seen = {"v": False}

        def _traced_build(selected: list[object]) -> str:
            assembled = original_build(selected)
            has_summary = any(isinstance(item, dict) and "_summary" in item for item in selected)
            if has_summary:
                original_tokens = sum(ev.estimated_tokens for ev in compressor._score_items(list(events)))
                if compressor._estimate_tokens(assembled) >= original_tokens:
                    expanding_build_seen["v"] = True
            return assembled

        compressor._build_compressed_context = _traced_build  # type: ignore[method-assign]

        result = await compressor.compress(projection, target_tokens=3)

        # The summary path was actually taken (guard is not vacuously satisfied).
        assert compressor._summarize_items.called  # type: ignore[attr-defined]
        # A summary-bearing selection that expanded past the original was built,
        # i.e. the guard's drop branch had real work to do.
        assert expanding_build_seen["v"], "never-EXPAND drop branch was not exercised"
        # The guard must ensure the output never exceeds the original size.
        assert result.compressed_tokens <= result.original_tokens
        assert result.compression_ratio <= 1.0
        # The expanding summary must not appear in the final output.
        assert marker not in result.compressed_content

    async def test_normal_compression_unaffected(self) -> None:
        """When no expanding summary is involved, output is the selection."""
        compressor = self._compressor()
        events = [_event(f"important decision {i}", seq=i) for i in range(3)]
        projection = _projection(events)
        result = await compressor.compress(projection, target_tokens=10_000)
        assert result.compressed_tokens <= result.original_tokens
        assert result.compression_ratio <= 1.0
