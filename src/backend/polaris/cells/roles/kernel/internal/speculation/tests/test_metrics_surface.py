"""测试 per-turn 推测执行指标暴露：snapshot / saved_ms / 订阅 seam / turn summary."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    subscribe,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import (
    SpeculationMetrics,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    CandidateToolCall,
)


def test_snapshot_counts_resolutions_and_saved_ms() -> None:
    m = SpeculationMetrics()
    m.record_adopt("t1", "c1", "read_file", "k1", saved_ms=120)
    m.record_adopt("t1", "c2", "repo_rg", "k2", saved_ms=80)
    m.record_join("t1", "c3", "read_file", "k3", saved_ms=30)
    m.record_replay("t1", "c4", "write_file", reason="miss")

    snap = m.snapshot()
    assert snap["adopted"] == 2
    assert snap["joined"] == 1
    assert snap["replayed"] == 1
    assert snap["saved_ms_total"] == 230
    # hit_rate = (adopted + joined) / (adopted + joined + replayed) = 3/4
    assert snap["hit_rate"] == 0.75
    assert snap["wrong_adoption"] == 0


def test_saved_ms_ignores_missing_or_nonpositive() -> None:
    m = SpeculationMetrics()
    m.record_adopt("t1", "c1", "read_file", "k1", saved_ms=None)
    m.record_adopt("t1", "c2", "read_file", "k2", saved_ms=0)
    m.record_adopt("t1", "c3", "read_file", "k3", saved_ms=-5)
    assert m.snapshot()["saved_ms_total"] == 0
    assert m.snapshot()["adopted"] == 3


def test_wrong_adoption_is_tracked() -> None:
    m = SpeculationMetrics()
    assert m.wrong_adoption_count == 0
    m.record_wrong_adoption(reason="on_off_mismatch")
    assert m.wrong_adoption_count == 1
    assert m.snapshot()["wrong_adoption"] == 1


def test_subscribe_receives_events_and_unsubscribe_stops() -> None:
    captured: list[SpeculationEvent] = []
    unsubscribe = subscribe(captured.append)
    try:
        m = SpeculationMetrics()
        m.record_adopt("turn-A", "c1", "read_file", "k1", saved_ms=10)
    finally:
        unsubscribe()

    adopt_events = [e for e in captured if e.event_type == "speculation.resolve.adopt"]
    assert len(adopt_events) == 1
    assert adopt_events[0].turn_id == "turn-A"

    # 注销后不再收到事件
    m.record_replay("turn-A", "c2", "read_file", reason="miss")
    assert all(e.event_type != "speculation.resolve.replay" for e in captured)


def test_emit_turn_summary_carries_full_snapshot() -> None:
    captured: list[SpeculationEvent] = []
    unsubscribe = subscribe(captured.append)
    try:
        m = SpeculationMetrics()
        m.record_adopt("turn-B", "c1", "read_file", "k1", saved_ms=42)
        returned = m.emit_turn_summary("turn-B")
    finally:
        unsubscribe()

    summaries = [e for e in captured if e.event_type == "speculation.turn.summary"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.turn_id == "turn-B"
    assert summary.saved_ms == 42
    assert summary.metadata["adopted"] == 1
    assert summary.metadata["saved_ms_total"] == 42
    assert returned["saved_ms_total"] == 42


def test_record_timeout_increments_timeout_ratio() -> None:
    # Regression (Finding 1): a real deadline miss must increment the timeout
    # counter so BudgetGovernor's ``timeout_ratio > 0.2`` backpressure can fire.
    # Before the fix _timed_out_count only moved on a cancel reason containing
    # "timeout", which never happens for real deadline misses -> ratio stayed 0.
    m = SpeculationMetrics()
    m.record_started(_candidate(), "k1")
    m.record_timeout("task-1", "speculative tool timed out: timeout")

    snap = m.snapshot()
    assert snap["timed_out"] == 1
    # A timeout also counts as a failure for total-volume accounting.
    assert snap["failed"] == 1
    assert m.timeout_ratio > 0.0
    assert snap["timeout_ratio"] == 1.0


def test_abandonment_ratio_includes_cancelled_and_failed() -> None:
    # Regression (Finding 2): the denominator must be
    # completed + abandoned + cancelled + failed (per the docstring contract),
    # not just completed + abandoned. With completed=1, abandoned=2, cancelled=10
    # the ratio is 2/13 ~= 0.15, not 2/3 ~= 0.67 (which over-fired down-tiering).
    m = SpeculationMetrics()
    m.record_completed("t-c", 5)
    m.record_abandon("t-a1", "turn_drain")
    m.record_abandon("t-a2", "turn_drain")
    for i in range(10):
        m.record_cancel(f"t-x{i}", "cancelled")

    assert m.abandonment_ratio == 2 / 13
    assert m.abandonment_ratio < 0.6  # below BudgetGovernor's down-tier threshold


def _candidate() -> CandidateToolCall:
    return CandidateToolCall(
        candidate_id="c1",
        stream_id="s1",
        turn_id="turn-1",
        tool_name="read_file",
        stability_score=1.0,
        parse_state="schema_valid",
    )


def test_sink_exception_does_not_break_emit() -> None:
    def bad_sink(_: SpeculationEvent) -> None:
        raise RuntimeError("boom")

    unsubscribe = subscribe(bad_sink)
    try:
        m = SpeculationMetrics()
        # 不应抛出
        m.record_adopt("turn-C", "c1", "read_file", "k1", saved_ms=1)
    finally:
        unsubscribe()
