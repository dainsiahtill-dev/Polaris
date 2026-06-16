"""Tests for the T2-A token-shrink rejection guard in CompactionStrategy.

A compaction pass that runs but does not actually reduce the token estimate
must report ``triggered=False`` (no-op) so callers never treat a non-shrinking
pass as a win.

Adversarial-review note (false-green fix):
    The original ``test_non_shrinking_pass_reports_no_op`` used
    ``truncate_to_messages=1`` with a *single* uniform message. Truncation never
    engages on a one-element list (``len == 1`` is not ``> 1``), so
    ``compacted_items`` stayed ``0`` and ``triggered=False`` came from the
    ordinary nothing-happened path -- the guard branch
    (``compacted_items > 0 and tokens_recovered <= 0``) was NEVER executed.

    Why the guard is unreachable through pure truncation/real-compressor:
      * ``compacted_items = micro_compacted_count + truncated_count``.
      * ``micro_compact`` rewrites tool-result parts *in place* and never drops
        messages, so a list-length delta can never make ``micro_compacted_count``
        positive (the prior code measured exactly that delta -> always 0; now we
        count rewritten parts directly).
      * Truncation keeps the tail and drops older messages; the deterministic
        estimator charges every message >= 4 tokens, so dropping >= 1 message
        always recovers >= 4 tokens. Hence ``truncated_count > 0`` implies
        ``tokens_recovered > 0`` -- the two guard conditions are mutually
        exclusive on that path.

    The guard therefore guards against a compressor whose pass *changes items*
    yet does not shrink the estimate (re-serialization / placeholder substitution
    that is net-neutral or expanding on small inputs). We exercise exactly that
    via the public ``compressor=`` dependency seam with a conforming double that
    rewrites parts (marks ``_compacted=True``) without shrinking content. The
    guard's own logic still runs against the real ``_estimate_history_tokens`` on
    real message dicts -- the unit under test is not mocked away.

Run with:
    pytest polaris/kernelone/context/tests/test_compaction_strategy_noop_guard.py -v
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.context.compaction_strategy import (
    CompactionStrategy,
    MicroCompactorPort,
    _estimate_history_tokens,
)


def _make_messages(n: int, content: str) -> list[dict[str, Any]]:
    """Build a simple plain-string message history."""
    return [{"role": "user", "content": content} for _ in range(n)]


def _make_tool_result_messages(n: int, content: str) -> list[dict[str, Any]]:
    """Build a history of user messages each carrying one tool_result part."""
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"tu-{i}",
                    "status": "success",
                    "content": content,
                }
            ],
        }
        for i in range(n)
    ]


class _NonShrinkingCompressor(MicroCompactorPort):
    """Conforming RoleContextCompressor double that rewrites without shrinking.

    It marks every ``tool_result`` part as ``_compacted=True`` -- a genuine,
    in-place item rewrite that the strategy counts as compacted work -- but it
    does NOT reduce the content, so the deterministic token estimate cannot
    drop (the added marker key even expands the serialized form slightly). This
    is precisely the condition the T2-A guard exists to veto: items changed,
    tokens not recovered. We use a real conforming double (not a mock of the
    guard) so the strategy's own logic runs end-to-end against the real
    estimator on real message data.
    """

    def micro_compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    # Mark the part as rewritten WITHOUT shrinking its content.
                    part["_compacted"] = True
        return messages


class _ShrinkingCompressor(MicroCompactorPort):
    """Conforming double that genuinely shrinks: rewrites and reduces content."""

    def micro_compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    part["_compacted"] = True
                    part["content"] = "[compacted]"
        return messages


class TestNoOpGuard:
    def test_empty_history_no_op(self) -> None:
        strategy = CompactionStrategy()
        result = strategy.compact(history=[])
        assert result.triggered is False
        assert result.compacted_items == 0
        assert result.tokens_recovered == 0

    def test_non_shrinking_pass_drives_guard_branch(self) -> None:
        """A pass that rewrites items but does not shrink tokens must be vetoed.

        This genuinely executes the guard branch (``compacted_items > 0`` and
        ``tokens_recovered <= 0`` -> ``CompactionResult(triggered=False)`` with
        the no_op_summary return). We inject a conforming compressor double that
        marks ``tool_result`` parts ``_compacted=True`` (so the strategy counts
        them as compacted) without reducing content, so the real
        ``_estimate_history_tokens`` cannot drop. Truncation is disabled
        (``truncate_to_messages`` large) so the only compaction signal comes from
        the rewrite -- isolating the guard.
        """
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 1000}})
        history = _make_tool_result_messages(3, "X" * 300)

        original_tokens = _estimate_history_tokens(history)
        result = strategy.compact(history=history, compressor=_NonShrinkingCompressor())

        # Guard fired: items were rewritten internally (compacted_items>0 entering
        # the branch) but no tokens were recovered, so the result is vetoed.
        assert result.triggered is False
        assert result.compacted_items == 0
        assert result.tokens_recovered == 0
        assert "no-op" in result.summary.lower()
        # Sanity: the rewrite did NOT shrink the estimate (it slightly expanded),
        # which is exactly why the guard must veto it rather than claim a win.
        final_tokens = _estimate_history_tokens(history)
        assert final_tokens >= original_tokens

    def test_guard_does_not_fire_when_pass_genuinely_shrinks(self) -> None:
        """A rewrite that DOES shrink tokens must report a real win (not no-op).

        Complements the guard test: with a shrinking compressor the same
        item-rewrite path produces ``tokens_recovered > 0`` so the guard branch is
        NOT taken and ``triggered=True`` is reported. This proves the guard is
        selective, not a blanket veto.
        """
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 1000}})
        history = _make_tool_result_messages(3, "X" * 300)

        result = strategy.compact(history=history, compressor=_ShrinkingCompressor())

        assert result.triggered is True
        assert result.compacted_items > 0
        assert result.tokens_recovered > 0
        assert "no-op" not in result.summary.lower()

    def test_real_compressor_micro_only_reports_work(self) -> None:
        """Real RoleContextCompressor micro-only pass reports the items it changed.

        Regression guard for the under-reporting bug: micro_compact rewrites parts
        in place (same list length), so measuring compaction by a length delta
        always reported ``0``/``triggered=False`` even when hundreds of tokens were
        recovered. We now count rewritten parts, so a micro-only pass correctly
        reports ``triggered=True`` with positive recovery. Uses a real workspace
        tmp dir and the real compressor (no doubles).
        """
        import tempfile

        with tempfile.TemporaryDirectory() as workspace:
            strategy = CompactionStrategy(
                profile_overrides={
                    "compaction": {
                        # Disable truncation so the only signal is micro-compaction.
                        "truncate_to_messages": 1000,
                        "micro_compact_keep": 1,
                    }
                }
            )
            history: list[dict[str, Any]] = []
            for i in range(5):
                history.append(
                    {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "read_file"}]}
                )
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": f"t{i}", "status": "success", "content": "X" * 500}
                        ],
                    }
                )

            result = strategy.compact(history=history, workspace=workspace)

            assert result.triggered is True
            assert result.compacted_items > 0
            assert result.tokens_recovered > 0

    def test_real_shrink_via_truncation_still_triggers(self) -> None:
        """A genuine reduction via truncation must still report triggered=True."""
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 2}})
        # Many large messages so truncating to 2 genuinely reduces tokens.
        history = _make_messages(40, "a fairly long message body " * 20)
        result = strategy.compact(history=history)
        assert result.triggered is True
        assert result.tokens_recovered > 0
        assert result.compacted_items > 0

    def test_no_op_summary_is_explicit(self) -> None:
        strategy = CompactionStrategy(profile_overrides={"compaction": {"truncate_to_messages": 1000}})
        history = _make_tool_result_messages(3, "X" * 300)
        result = strategy.compact(history=history, compressor=_NonShrinkingCompressor())
        assert "no-op" in result.summary.lower()
        assert result.compacted_items == 0
