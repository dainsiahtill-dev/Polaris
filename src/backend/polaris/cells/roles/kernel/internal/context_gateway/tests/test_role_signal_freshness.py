"""跨 turn freshness 记忆：缓存单元 + 端到端熔断断流（解架构阻断的验证）。"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.context_gateway.role_signal_freshness import (
    RoleSignalFreshnessCache,
    get_previous_freshness,
    record_injected_freshness,
    reset_freshness_cache,
)
from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
    RoleSignalRegistry,
    SignalBuildContext,
    allocate_role_signals,
)
from polaris.kernelone.context.receipt_store import ReceiptStore

# ---------------- 缓存单元 ----------------


def test_cache_get_empty() -> None:
    c = RoleSignalFreshnessCache()
    assert c.get_previous("k") == {}
    assert c.get_previous("") == {}


def test_cache_record_and_merge() -> None:
    c = RoleSignalFreshnessCache()
    c.record("k", {"a": "1"})
    c.record("k", {"b": "2"})
    assert c.get_previous("k") == {"a": "1", "b": "2"}
    # 同 id 覆盖为新值
    c.record("k", {"a": "9"})
    assert c.get_previous("k")["a"] == "9"


def test_cache_bounded_lru_eviction() -> None:
    c = RoleSignalFreshnessCache(max_keys=2)
    c.record("k1", {"a": "1"})
    c.record("k2", {"a": "1"})
    c.record("k3", {"a": "1"})  # 触发淘汰最旧 k1
    assert c.get_previous("k1") == {}
    assert c.get_previous("k2") != {}
    assert c.get_previous("k3") != {}


def test_cache_empty_record_noop() -> None:
    c = RoleSignalFreshnessCache()
    c.record("k", {})
    c.record("", {"a": "1"})
    assert c.get_previous("k") == {}


# ---------------- 端到端跨 turn 熔断 ----------------


def _ctx(structure: str, history: str) -> SignalBuildContext:
    return SignalBuildContext(
        role="director",
        phase="exploring",
        task_id="task1",
        policy_flags={"include_project_structure": True, "include_task_history": True},
        get_project_structure=lambda: structure,
        get_task_history=lambda tid: history,
    )


def _alloc(ctx: SignalBuildContext, *, pressure: bool, key: str) -> object:
    return allocate_role_signals(
        registry=RoleSignalRegistry(),
        ctx=ctx,
        receipt_store=ReceiptStore(),
        budget_pressure=pressure,
        previous_freshness=get_previous_freshness(key),
        per_signal_char_cap=None,
        total_char_budget=None,
    )


def test_cross_turn_unchanged_skipped_under_pressure() -> None:
    """turn1 注入并记忆；turn2 压力 + 内容未变 → 两个 seed 信号被断流。"""
    reset_freshness_cache()
    ctx = _ctx("src/\n a.py", "task: open")
    # turn1: 无压力 → 全注入，记忆 freshness
    a1 = _alloc(ctx, pressure=False, key="task1")
    assert a1.sources == ["project_structure", "task_history"]
    record_injected_freshness("task1", a1.telemetry["injected_freshness"])
    # turn2: 压力 + 内容未变 → 断流（空注入，把窗口让给工具结果）
    a2 = _alloc(ctx, pressure=True, key="task1")
    assert set(a2.telemetry["skipped_unchanged"]) == {"project_structure", "task_history"}
    assert a2.sources == []


def test_cross_turn_changed_still_injected_under_pressure() -> None:
    """turn2 压力但内容已变 → 仍注入（freshness 跳变 = 资产刚被改过）。"""
    reset_freshness_cache()
    a1 = _alloc(_ctx("src/\n a.py", "task: open"), pressure=False, key="task1")
    record_injected_freshness("task1", a1.telemetry["injected_freshness"])
    # 项目结构变了、任务历史没变
    a2 = _alloc(_ctx("src/\n a.py\n b.py", "task: open"), pressure=True, key="task1")
    assert "project_structure" in a2.sources  # 变化 → 保留
    assert "task_history" in a2.telemetry["skipped_unchanged"]  # 未变 → 断流


def test_no_pressure_keeps_baseline_even_with_memory() -> None:
    """有记忆但无压力 → 不断流 → 与旧实现逐字节一致。"""
    reset_freshness_cache()
    ctx = _ctx("src/\n a.py", "task: open")
    a1 = _alloc(ctx, pressure=False, key="task1")
    record_injected_freshness("task1", a1.telemetry["injected_freshness"])
    a2 = _alloc(ctx, pressure=False, key="task1")
    assert a2.sources == ["project_structure", "task_history"]
