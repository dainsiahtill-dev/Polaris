"""回归测试：ProjectionEngine 自适应权重不再空转。

历史缺陷：`record_outcome` 把 route/confidence/recency 分硬编码为 0.5，使
`_compute_projection_quality` 形同虚设、权重学习恒为无操作。修复后 record_outcome
从最近一次投影的真实事件计算质量分，权重据此变化，并能区分"信号是否预测了结果"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.kernelone.context.projection_engine import (
    ProjectionEngine,
    reset_projection_adaptive_state,
)


def _evt(seq: int, route: str, conf: float) -> SimpleNamespace:
    return SimpleNamespace(
        sequence=seq,
        role="assistant",
        content="x",
        route=route,
        event_id=f"e{seq}",
        metadata=(("routing_confidence", conf),),
    )


def _high_route_window() -> list[SimpleNamespace]:
    return [_evt(1, "patch", 0.9), _evt(2, "patch", 0.95)]


def test_weights_change_after_outcomes() -> None:
    """权重在记录结果后确实变化（不再是冻结的常量）。"""
    reset_projection_adaptive_state()
    engine = ProjectionEngine(learning_key="t_change")
    engine.sort_events(_high_route_window())
    before = engine.get_adaptive_weights()["route_weight"]
    for _ in range(6):
        engine.record_outcome(success=True)
    after = engine.get_adaptive_weights()["route_weight"]
    assert abs(after - before) > 1e-6, "adaptive weights must move after outcomes (was inert)"


def test_quality_distinguishes_calibration() -> None:
    """同样的高-route 投影，成功 vs 失败应产生不同的权重轨迹（检测过度自信/误校准）。"""
    reset_projection_adaptive_state()
    win = _high_route_window()

    # 独立 learning_key → 两个引擎学习状态隔离。
    e_success = ProjectionEngine(learning_key="t_cal_success")
    e_success.sort_events(win)
    for _ in range(6):
        e_success.record_outcome(success=True)

    e_failure = ProjectionEngine(learning_key="t_cal_failure")
    e_failure.sort_events(win)
    for _ in range(6):
        e_failure.record_outcome(success=False)

    rw_success = e_success.get_adaptive_weights()["route_weight"]
    rw_failure = e_failure.get_adaptive_weights()["route_weight"]
    # 高 route + 成功（route 预测准）应与 高 route + 失败（route 过度自信）分道扬镳。
    assert abs(rw_success - rw_failure) > 1e-6, (
        "weights must diverge between well-calibrated and mis-calibrated outcomes"
    )


def test_record_outcome_uses_last_projection_events() -> None:
    """record_outcome 使用最近一次 sort_events 的事件来计算质量（而非常量）。"""
    reset_projection_adaptive_state()
    engine = ProjectionEngine(learning_key="t_high")
    # 不同质量的两次投影 → 不同的内部 outcome 质量分 → 权重不同。
    engine.sort_events([_evt(1, "patch", 0.95)])
    for _ in range(6):
        engine.record_outcome(success=True)
    high = engine.get_adaptive_weights()["route_weight"]

    engine2 = ProjectionEngine(learning_key="t_low")
    engine2.sort_events([_evt(1, "clear", 0.05)])
    for _ in range(6):
        engine2.record_outcome(success=True)
    low = engine2.get_adaptive_weights()["route_weight"]

    assert abs(high - low) > 1e-6, "different projection event quality must yield different learned weights"


def test_adaptive_state_persists_across_instances_same_key() -> None:
    """跨 turn 验证：同 learning_key 的新实例继承累积的权重 + outcomes 窗口。"""
    reset_projection_adaptive_state()
    # turn 1
    e1 = ProjectionEngine(learning_key="pm")
    e1.sort_events(_high_route_window())
    for _ in range(4):
        e1.record_outcome(success=True)
    w_after_turn1 = e1.get_adaptive_weights()["route_weight"]

    # turn 2：全新实例（模拟 gateway 每 turn 新建），同 key → 继承状态
    e2 = ProjectionEngine(learning_key="pm")
    assert e2.get_adaptive_weights()["route_weight"] == w_after_turn1  # 继承，未清零
    e2.sort_events(_high_route_window())
    e2.record_outcome(success=True)  # 第 5 个 outcome（跨 turn 累积）→ 触发 adjust
    assert len(e2._outcomes) >= 5  # outcomes 窗口跨 turn 累积，而非每 turn 清零

    # 不同 key 的实例不受影响（角色隔离）
    other = ProjectionEngine(learning_key="qa")
    assert other.get_adaptive_weights()["route_weight"] == 0.30  # 默认值，独立桶


def test_adaptive_ordering_env_switch_is_read_per_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    """A/B suite 的 env 开关必须真实控制 ProjectionEngine 排序，而非空开关。"""
    reset_projection_adaptive_state()
    events = [
        _evt(1, "clear", 0.99),
        _evt(2, "archive", 0.01),
        _evt(3, "summarize", 0.30),
    ]
    engine = ProjectionEngine(learning_key="env_switch")

    monkeypatch.setenv("ENABLE_PROJECTION_ADAPTIVE_ORDERING", "0")
    chronological = [event.sequence for event in engine.sort_events(events)]
    assert chronological == [1, 2, 3]

    monkeypatch.setenv("ENABLE_PROJECTION_ADAPTIVE_ORDERING", "1")
    adaptive = [event.sequence for event in engine.sort_events(events)]
    assert adaptive != chronological
