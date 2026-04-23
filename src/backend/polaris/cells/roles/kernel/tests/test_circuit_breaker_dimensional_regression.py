"""Dimensional circuit breaker regression tests.

Covers:
1. Dimension trigger fires before global threshold
2. Dimension key isolation (tool A failures do not affect tool B)
3. Snapshot includes trigger_reason after trigger
4. Snapshot includes triggered_dimension after dimension trigger
5. Backward compat: evaluate_batch without invocations parameter
6. effect_threshold_overrides for a scope not present in receipts (no false trigger)
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.transaction.tool_failure_circuit_breaker import (
    ToolFailureCircuitBreaker,
    ToolFailureCircuitBreakerSnapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failure_receipt(
    *,
    call_id: str = "call_1",
    tool_name: str = "write_file",
    status: str = "error",
    error: str = "forced failure",
) -> dict[str, Any]:
    """Build a single-result receipt dict representing a tool failure."""
    return {
        "results": [
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "status": status,
                "error": error,
            }
        ]
    }


def _make_success_receipt(
    *,
    call_id: str = "call_ok",
    tool_name: str = "read_file",
) -> dict[str, Any]:
    """Build a single-result receipt dict representing a tool success."""
    return {
        "results": [
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "status": "success",
            }
        ]
    }


def _make_invocation(
    *,
    call_id: str = "call_1",
    tool_name: str = "write_file",
    effect_type: str = "write",
) -> dict[str, Any]:
    """Build a single invocation dict."""
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "effect_type": effect_type,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dimension_trigger_before_global_threshold() -> None:
    """With effect_threshold_overrides={"write": (1, 1)} and global=(3, 5),
    first write failure should trigger the dimension breaker, not the global one.
    """
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=3,
        total_failure_threshold=5,
        effect_threshold_overrides={"write": (1, 1)},
    )

    snapshot = breaker.evaluate_batch(
        turn_id="turn_dim_first",
        invocations=[
            _make_invocation(call_id="call_w1", tool_name="write_file", effect_type="write"),
        ],
        receipts=[
            _make_failure_receipt(call_id="call_w1", tool_name="write_file"),
        ],
    )

    assert snapshot.triggered is True
    assert snapshot.trigger_reason == "dimension_consecutive_threshold"
    # Global counters should still be below global thresholds
    assert snapshot.consecutive_failures < 3
    assert snapshot.total_failures < 5


def test_dimension_key_isolation() -> None:
    """Failures on tool_name='A' should not affect tool_name='B' dimension counts."""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=99,
        total_failure_threshold=99,
        effect_threshold_overrides={"write": (2, 99)},
    )

    # Record a failure for tool A (write scope)
    snap_a = breaker.evaluate_batch(
        turn_id="turn_isolation",
        invocations=[_make_invocation(call_id="call_a1", tool_name="tool_A", effect_type="write")],
        receipts=[_make_failure_receipt(call_id="call_a1", tool_name="tool_A")],
    )
    assert snap_a.triggered is False

    # Record a failure for tool B (write scope) -- different dimension key
    snap_b = breaker.evaluate_batch(
        turn_id="turn_isolation",
        invocations=[_make_invocation(call_id="call_b1", tool_name="tool_B", effect_type="write")],
        receipts=[_make_failure_receipt(call_id="call_b1", tool_name="tool_B")],
    )
    assert snap_b.triggered is False

    # Record another failure for tool A -- consecutive for A should be 1 (reset after B)
    # Because the dimension for tool_A was not in the *current* batch when tool_B failed,
    # its consecutive count was reset to 0 by _update_dimension_counters.
    snap_a2 = breaker.evaluate_batch(
        turn_id="turn_isolation",
        invocations=[_make_invocation(call_id="call_a2", tool_name="tool_A", effect_type="write")],
        receipts=[_make_failure_receipt(call_id="call_a2", tool_name="tool_A")],
    )
    # tool_A consecutive was reset when tool_B batch ran, so now it's 1 again, not 2
    assert snap_a2.triggered is False


def test_snapshot_includes_trigger_reason() -> None:
    """After trigger, snapshot.trigger_reason should be non-None."""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=1,
        total_failure_threshold=1,
    )

    snapshot = breaker.evaluate_batch(
        turn_id="turn_trigger_reason",
        receipts=[_make_failure_receipt(call_id="call_tr", tool_name="rm_file")],
        invocations=[_make_invocation(call_id="call_tr", tool_name="rm_file", effect_type="write")],
    )

    assert snapshot.triggered is True
    assert snapshot.trigger_reason is not None
    assert len(snapshot.trigger_reason) > 0


def test_snapshot_includes_triggered_dimension() -> None:
    """After dimension trigger, snapshot.triggered_dimension should identify the dimension."""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=99,
        total_failure_threshold=99,
        effect_threshold_overrides={"write": (1, 1)},
    )

    snapshot = breaker.evaluate_batch(
        turn_id="turn_dim_id",
        invocations=[_make_invocation(call_id="call_d1", tool_name="write_file", effect_type="write")],
        receipts=[_make_failure_receipt(call_id="call_d1", tool_name="write_file")],
    )

    assert snapshot.triggered is True
    assert snapshot.triggered_dimension is not None
    # Dimension string is formatted as "tool_name|effect_scope|failure_class"
    assert "write_file" in snapshot.triggered_dimension
    assert "write" in snapshot.triggered_dimension
    assert "error" in snapshot.triggered_dimension


def test_backward_compat_no_invocations() -> None:
    """Call evaluate_batch without invocations parameter; verify it still works (backward compat)."""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=3,
        total_failure_threshold=10,
    )

    # evaluate_batch with receipts only (invocations defaults to None)
    snapshot = breaker.evaluate_batch(
        turn_id="turn_compat",
        receipts=[
            {
                "tool_name": "read_file",
                "status": "error",
                "error": "not found",
            }
        ],
    )

    # Should not crash -- backward compatible
    assert isinstance(snapshot, ToolFailureCircuitBreakerSnapshot)
    assert snapshot.turn_id == "turn_compat"
    assert snapshot.batch_failures >= 1
    assert snapshot.triggered is False  # threshold=3, only 1 failure


def test_effect_threshold_overrides_missing_scope() -> None:
    """Use effect_threshold_overrides for 'write' scope but receipts only have 'read' failures.
    Verify no false trigger from the overrides.
    """
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=99,
        total_failure_threshold=99,
        effect_threshold_overrides={"write": (1, 1)},
    )

    # Send read-scope failures only -- the write override should not trigger
    snapshot = breaker.evaluate_batch(
        turn_id="turn_missing_scope",
        invocations=[
            _make_invocation(call_id="call_r1", tool_name="read_file", effect_type="read"),
        ],
        receipts=[
            _make_failure_receipt(call_id="call_r1", tool_name="read_file"),
        ],
    )

    assert snapshot.triggered is False
    # The write override (1,1) should not have been consulted for read-scope failures
    assert snapshot.trigger_reason is None
    assert snapshot.triggered_dimension is None


def test_global_trigger_after_consecutive_failures() -> None:
    """Global threshold triggers after N consecutive failing batches."""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=2,
        total_failure_threshold=99,
    )

    snap1 = breaker.evaluate_batch(
        turn_id="turn_global",
        invocations=[_make_invocation(call_id="c1", tool_name="t1", effect_type="read")],
        receipts=[_make_failure_receipt(call_id="c1", tool_name="t1")],
    )
    assert snap1.triggered is False

    snap2 = breaker.evaluate_batch(
        turn_id="turn_global",
        invocations=[_make_invocation(call_id="c2", tool_name="t1", effect_type="read")],
        receipts=[_make_failure_receipt(call_id="c2", tool_name="t1")],
    )
    # After 2 consecutive failures the breaker should fire
    assert snap2.triggered is True
    assert snap2.consecutive_failures == 2


def test_success_batch_resets_global_consecutive() -> None:
    """A success batch in between should reset global consecutive counter."""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=2,
        total_failure_threshold=99,
    )

    breaker.evaluate_batch(
        turn_id="turn_reset",
        invocations=[_make_invocation(call_id="c1", tool_name="t1", effect_type="read")],
        receipts=[_make_failure_receipt(call_id="c1", tool_name="t1")],
    )

    # Success batch
    snap_ok = breaker.evaluate_batch(
        turn_id="turn_reset",
        invocations=[_make_invocation(call_id="c_ok", tool_name="t1", effect_type="read")],
        receipts=[_make_success_receipt(call_id="c_ok", tool_name="t1")],
    )
    assert snap_ok.triggered is False
    assert snap_ok.consecutive_failures == 0

    # Another failure -- should be consecutive=1 again
    snap_after = breaker.evaluate_batch(
        turn_id="turn_reset",
        invocations=[_make_invocation(call_id="c2", tool_name="t1", effect_type="read")],
        receipts=[_make_failure_receipt(call_id="c2", tool_name="t1")],
    )
    assert snap_after.triggered is False
    assert snap_after.consecutive_failures == 1
