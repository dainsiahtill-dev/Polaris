"""测试 per-turn 推测执行指标暴露：snapshot / saved_ms / 订阅 seam / turn summary."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    subscribe,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import (
    SpeculationMetrics,
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
