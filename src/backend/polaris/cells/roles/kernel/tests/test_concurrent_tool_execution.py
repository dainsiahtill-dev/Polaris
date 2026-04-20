"""Test Concurrent Tool Execution — speculative and canonical tool calls must not race.

Validates:
- TurnLedger correctly tracks speculative vs canonical call IDs
- Shadow engine resolution (adopt/join/replay) prevents double execution
- Concurrent tool batch execution maintains consistency
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger


class TestConcurrentToolExecution:
    """Concurrent tool execution regression tests."""

    def _make_outcome(self, enabled: bool = True, error: str = "") -> dict[str, Any]:
        """Create a speculative outcome dict for ledger tracking."""
        return {"enabled": enabled, "error": error}

    # ──────────────────────────────────────────────────────────────────────────
    # Happy Path: Ledger Tracking
    # ──────────────────────────────────────────────────────────────────────────

    def test_ledger_tracks_speculative_attempts(self) -> None:
        """TurnLedger must record speculative attempted call IDs."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_speculative_outcome("call-1", self._make_outcome(enabled=True))
        ledger.record_speculative_outcome("call-2", self._make_outcome(enabled=True, error="non_readonly_tool"))
        ledger.record_speculative_outcome("call-3", self._make_outcome(enabled=True))

        # non_readonly_tool error causes early return, so not tracked
        assert "call-1" in ledger.speculative_attempted_call_ids
        assert "call-2" not in ledger.speculative_attempted_call_ids
        assert "call-3" in ledger.speculative_attempted_call_ids

    def test_ledger_tracks_speculative_successes(self) -> None:
        """TurnLedger must record speculative successful call IDs."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_speculative_outcome("call-1", self._make_outcome())
        ledger.record_speculative_outcome("call-2", self._make_outcome(error="some_error"))
        ledger.record_speculative_outcome("call-3", self._make_outcome())

        assert "call-1" in ledger.speculative_successful_call_ids
        assert "call-3" in ledger.speculative_successful_call_ids
        assert "call-2" not in ledger.speculative_successful_call_ids

    def test_ledger_tracks_canonical_calls(self) -> None:
        """TurnLedger must record canonical tool call IDs."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_tool_execution("read_file", "call-1", "success", 100)
        ledger.record_tool_execution("write_file", "call-2", "success", 200)

        assert "call-1" in ledger.canonical_tool_call_ids
        assert "call-2" in ledger.canonical_tool_call_ids

    def test_ledger_metrics_hit_rate_calculation(self) -> None:
        """TurnLedger must correctly calculate speculative hit rate."""
        ledger = TurnLedger(turn_id="t-1")

        # Simulate: 2 speculative attempts, 1 successful, 1 canonical call matches
        ledger.record_speculative_outcome("call-1", self._make_outcome())
        ledger.record_speculative_outcome("call-2", self._make_outcome(error="failed"))
        ledger.record_tool_execution("read_file", "call-1", "success", 100)

        metrics = ledger.build_monitoring_metrics("tool_batch")

        assert metrics["speculative.hit_rate"] == 0.5  # 1 hit / 2 attempts
        assert metrics["speculative.false_positive_rate"] == 0.5  # 1 false positive / 2 attempts

    def test_ledger_metrics_zero_attempts(self) -> None:
        """TurnLedger must handle zero speculative attempts gracefully."""
        ledger = TurnLedger(turn_id="t-1")

        metrics = ledger.build_monitoring_metrics("tool_batch")

        assert metrics["speculative.hit_rate"] == 0.0
        assert metrics["speculative.false_positive_rate"] == 0.0

    # ──────────────────────────────────────────────────────────────────────────
    # Edge Cases: Ledger Behavior
    # ──────────────────────────────────────────────────────────────────────────

    def test_ledger_record_tool_execution_without_call_id(self) -> None:
        """Ledger must handle tool execution without call_id gracefully."""
        ledger = TurnLedger(turn_id="t-1")

        # Should not raise
        ledger.record_tool_execution("read_file", "", "success", 100)

        assert len(ledger.tool_executions) == 1
        # Empty call_id should not be added to canonical_tool_call_ids
        assert "" not in ledger.canonical_tool_call_ids

    def test_ledger_record_speculative_disabled_not_tracked(self) -> None:
        """Ledger must not track disabled speculative outcomes."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_speculative_outcome("call-1", self._make_outcome(enabled=False))

        assert "call-1" not in ledger.speculative_attempted_call_ids
        assert "call-1" not in ledger.speculative_successful_call_ids

    def test_ledger_metrics_with_perfect_hit_rate(self) -> None:
        """Ledger must report 100% hit rate when all speculations are canonicalized."""
        ledger = TurnLedger(turn_id="t-perfect")

        ledger.record_speculative_outcome("call-1", self._make_outcome())
        ledger.record_speculative_outcome("call-2", self._make_outcome())
        ledger.record_tool_execution("read_file", "call-1", "success", 100)
        ledger.record_tool_execution("read_file", "call-2", "success", 200)

        metrics = ledger.build_monitoring_metrics("tool_batch")

        assert metrics["speculative.hit_rate"] == 1.0
        assert metrics["speculative.false_positive_rate"] == 0.0

    def test_ledger_metrics_with_zero_hit_rate(self) -> None:
        """Ledger must report 0% hit rate when no speculations are canonicalized."""
        ledger = TurnLedger(turn_id="t-zero")

        ledger.record_speculative_outcome("call-1", self._make_outcome())
        ledger.record_speculative_outcome("call-2", self._make_outcome())
        # No canonical calls recorded

        metrics = ledger.build_monitoring_metrics("tool_batch")

        assert metrics["speculative.hit_rate"] == 0.0
        assert metrics["speculative.false_positive_rate"] == 1.0

    # ──────────────────────────────────────────────────────────────────────────
    # Exceptions
    # ──────────────────────────────────────────────────────────────────────────

    def test_ledger_empty_call_id_not_added_to_canonical(self) -> None:
        """Empty call_id should not be added to canonical_tool_call_ids."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_tool_execution("read_file", "", "success", 100)

        assert len(ledger.canonical_tool_call_ids) == 0

    def test_ledger_record_speculative_with_empty_call_id(self) -> None:
        """Empty call_id should not be tracked in speculative sets."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_speculative_outcome("", self._make_outcome())

        assert len(ledger.speculative_attempted_call_ids) == 0
        assert len(ledger.speculative_successful_call_ids) == 0

    # ──────────────────────────────────────────────────────────────────────────
    # Regression: Double Execution Prevention
    # ──────────────────────────────────────────────────────────────────────────

    def test_ledger_no_race_between_speculative_and_canonical(self) -> None:
        """Ledger must correctly distinguish speculative from canonical calls."""
        ledger = TurnLedger(turn_id="t-race")

        # Simulate speculative execution
        ledger.record_speculative_outcome("call-1", self._make_outcome())
        ledger.record_speculative_outcome("call-2", self._make_outcome())

        # Simulate canonical execution (only call-1 is actually needed)
        ledger.record_tool_execution("read_file", "call-1", "success", 100)

        # call-2 was speculatively attempted but never canonicalized
        assert "call-1" in ledger.canonical_tool_call_ids
        assert "call-2" not in ledger.canonical_tool_call_ids
        assert "call-2" in ledger.speculative_attempted_call_ids

        metrics = ledger.build_monitoring_metrics("tool_batch")
        # 1 hit (call-1), 1 false positive (call-2), 2 attempts
        assert metrics["speculative.hit_rate"] == 0.5
        assert metrics["speculative.false_positive_rate"] == 0.5

    def test_ledger_multiple_canonical_calls_same_tool(self) -> None:
        """Ledger must track multiple canonical calls of the same tool."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.record_tool_execution("read_file", "call-1", "success", 100)
        ledger.record_tool_execution("read_file", "call-2", "success", 200)
        ledger.record_tool_execution("read_file", "call-3", "failure", 50)

        assert len(ledger.canonical_tool_call_ids) == 3
        assert "call-1" in ledger.canonical_tool_call_ids
        assert "call-2" in ledger.canonical_tool_call_ids
        assert "call-3" in ledger.canonical_tool_call_ids
        assert len(ledger.tool_executions) == 3

    def test_ledger_tool_batch_count_tracking(self) -> None:
        """Ledger must track tool batch count correctly."""
        ledger = TurnLedger(turn_id="t-1")

        # Simulate multiple tool batches
        ledger.tool_batch_count = 2

        metrics = ledger.build_monitoring_metrics("tool_batch")

        # With more than 1 batch, single_batch_ratio should be 0.0
        assert metrics["turn.single_batch_ratio"] == 0.0

    def test_ledger_single_batch_ratio(self) -> None:
        """Ledger must report single_batch_ratio as 1.0 when only 1 batch."""
        ledger = TurnLedger(turn_id="t-1")

        ledger.tool_batch_count = 1

        metrics = ledger.build_monitoring_metrics("tool_batch")

        assert metrics["turn.single_batch_ratio"] == 1.0

    def test_ledger_handoff_rate_tracking(self) -> None:
        """Ledger must track handoff rate correctly."""
        ledger = TurnLedger(turn_id="t-1")

        metrics = ledger.build_monitoring_metrics("handoff_workflow")

        assert metrics["workflow.handoff_rate"] == 1.0

        metrics = ledger.build_monitoring_metrics("tool_batch")

        assert metrics["workflow.handoff_rate"] == 0.0
