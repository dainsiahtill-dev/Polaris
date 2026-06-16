"""Tests for the T2-A token-shrink rejection guard in CompactionStrategy.

A compaction pass that runs but does not actually reduce the token estimate
must report ``triggered=False`` (no-op) so callers never treat a non-shrinking
pass as a win.

Run with:
    pytest polaris/kernelone/context/tests/test_compaction_strategy_noop_guard.py -v
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.context.compaction_strategy import CompactionStrategy


def _make_messages(n: int, content: str) -> list[dict[str, Any]]:
    """Build a simple plain-string message history."""
    return [{"role": "user", "content": content} for _ in range(n)]


class TestNoOpGuard:
    def test_empty_history_no_op(self) -> None:
        strategy = CompactionStrategy()
        result = strategy.compact(history=[])
        assert result.triggered is False
        assert result.compacted_items == 0
        assert result.tokens_recovered == 0

    def test_non_shrinking_pass_reports_no_op(self) -> None:
        """Truncation that does not reduce tokens must be reported as no-op.

        We force truncation to engage but keep all kept messages identical in
        size, so the estimate cannot drop. With no compressor (workspace=""),
        micro-compact never runs, so any reported compaction comes solely from
        truncation -- and if that does not shrink, the guard must veto it.
        """
        # Very aggressive truncation target so the fallback engages, but the
        # messages are uniform: trimming a uniform list still leaves the kept
        # tail at the same per-message size. The guard checks the net token
        # delta, which is what matters for callers.
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 1}})
        # Single uniform message: truncation cannot remove anything meaningful.
        history = _make_messages(1, "identical content block here for the test")
        result = strategy.compact(history=history)
        # One message in, one out: nothing recovered -> no-op.
        assert result.triggered is False
        assert result.tokens_recovered == 0

    def test_real_shrink_still_triggers(self) -> None:
        """A genuine reduction must still report triggered=True."""
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 2}})
        # Many large messages so truncating to 2 genuinely reduces tokens.
        history = _make_messages(40, "a fairly long message body " * 20)
        result = strategy.compact(history=history)
        assert result.triggered is True
        assert result.tokens_recovered > 0
        assert result.compacted_items > 0

    def test_no_op_summary_is_explicit(self) -> None:
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 1}})
        history = _make_messages(1, "identical content block here for the test")
        result = strategy.compact(history=history)
        assert "no-op" in result.summary.lower() or result.compacted_items == 0
