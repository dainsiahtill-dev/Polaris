"""RoleSignalPlane 单测：逐字节一致、角色隔离、预算熔断、溯源。"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
    DEFAULT_SIGNAL_PROVIDERS,
    RoleSignalRegistry,
    SignalBlock,
    SignalBuildContext,
    allocate_role_signals,
)
from polaris.kernelone.context.receipt_store import ReceiptStore


def _ctx(role: str, *, task_id: str = "T1", flags: dict[str, bool] | None = None) -> SignalBuildContext:
    return SignalBuildContext(
        role=role,
        phase="exploring",
        task_id=task_id,
        policy_flags=flags if flags is not None else {"include_project_structure": True, "include_task_history": True},
        get_project_structure=lambda: "src/\n  a.py\n  b.py",
        get_task_history=lambda tid: f"task {tid}: 3 done / 2 open",
    )


def test_baseline_byte_identical_and_order() -> None:
    """只启用两个 seed 信号时，turns 内容/顺序/绝对索引 + sources 与旧实现逐字节一致。"""
    reg = RoleSignalRegistry()
    res = allocate_role_signals(registry=reg, ctx=_ctx("director"), receipt_store=ReceiptStore())
    assert res.turns == [
        {"role": "system", "content": "【项目结构】\nsrc/\n  a.py\n  b.py", "name": "project_structure"},
        {"role": "system", "content": "【任务历史】\ntask T1: 3 done / 2 open", "name": "task_history"},
    ]
    assert res.sources == ["project_structure", "task_history"]  # 溯源泛化 + 顺序锁定


def test_policy_flag_gates_signal() -> None:
    reg = RoleSignalRegistry()
    res = allocate_role_signals(
        registry=reg,
        ctx=_ctx("director", flags={"include_project_structure": False, "include_task_history": True}),
        receipt_store=ReceiptStore(),
    )
    assert "project_structure" not in res.sources
    assert "task_history" in res.sources


def test_no_task_id_drops_task_history() -> None:
    reg = RoleSignalRegistry()
    res = allocate_role_signals(registry=reg, ctx=_ctx("director", task_id=""), receipt_store=ReceiptStore())
    assert res.sources == ["project_structure"]


class _BlueprintSignal:
    """chief_engineer 专属信号——用于验证跨角色隔离。"""

    id = "blueprint_overview"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return ctx.role == "chief_engineer"

    def priority(self, ctx: SignalBuildContext) -> int:
        return 5

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        return SignalBlock(id=self.id, content="【蓝图】v2 architecture", priority=5, freshness_key="bp1")


def test_cross_role_isolation() -> None:
    reg = RoleSignalRegistry((*DEFAULT_SIGNAL_PROVIDERS, _BlueprintSignal()))
    ce = allocate_role_signals(registry=reg, ctx=_ctx("chief_engineer"), receipt_store=ReceiptStore())
    qa = allocate_role_signals(registry=reg, ctx=_ctx("qa"), receipt_store=ReceiptStore())
    assert "blueprint_overview" in ce.sources
    # 隔离铁律：QA turn 的 payload 里绝不含 blueprint 任何字符串。
    assert "blueprint_overview" not in qa.sources
    assert all("蓝图" not in t["content"] for t in qa.turns)


class _HugePlanSignal:
    id = "plan_overview"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return True

    def priority(self, ctx: SignalBuildContext) -> int:
        return 2

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        return SignalBlock(id=self.id, content="PLAN " * 20000, priority=2, freshness_key="huge")


def test_circuit_breaker_oversized_offloaded_to_receipt() -> None:
    """超大信号 → 触发体积上限 → 截断为占位符 + receipt 引用，原文可取回。"""
    rs = ReceiptStore()
    reg = RoleSignalRegistry((_HugePlanSignal(),))
    res = allocate_role_signals(registry=reg, ctx=_ctx("pm"), receipt_store=rs, per_signal_char_cap=4000)
    plan_turn = next(t for t in res.turns if t["name"] == "plan_overview")
    assert "receipt://plan_overview" in plan_turn["content"]
    assert plan_turn.get("receipt_refs") == ["plan_overview"]
    assert "plan_overview" in res.telemetry["offloaded"]
    # 原文完整可取回（不丢信息）
    assert rs.get("plan_overview") == "PLAN " * 20000


def test_budget_pressure_skips_unchanged_nice_to_have() -> None:
    """压力下：freshness 未变的 nice-to-have 断流；变化的保留。"""
    reg = RoleSignalRegistry()
    ctx = _ctx("director")
    # 先算一次拿到 freshness 基线
    base = allocate_role_signals(registry=reg, ctx=ctx, receipt_store=ReceiptStore())
    # 构造 previous_freshness：project_structure 未变、task_history 视为已变（不在 prev）
    from polaris.cells.roles.kernel.internal.context_gateway.role_signals import _freshness_of

    prev = {"project_structure": _freshness_of("src/\n  a.py\n  b.py")}
    res = allocate_role_signals(
        registry=reg, ctx=ctx, receipt_store=ReceiptStore(), budget_pressure=True, previous_freshness=prev
    )
    assert "project_structure" in res.telemetry["skipped_unchanged"]  # 未变 → 断流
    assert "project_structure" not in res.sources
    assert "task_history" in res.sources  # 变化 → 保留
    assert base.sources == ["project_structure", "task_history"]  # 无压力时两者都在


class _MustHaveChanged:
    id = "verdict_history"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return True

    def priority(self, ctx: SignalBuildContext) -> int:
        return 3

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        return SignalBlock(
            id=self.id, content="【判定】FAIL on gate X", priority=3, level="must_have", freshness_key="changed"
        )


def _role_ctx_with_assets(role: str, *, flags: dict[str, bool]) -> SignalBuildContext:
    base = {"include_project_structure": True, "include_task_history": True}
    base.update(flags)
    return SignalBuildContext(
        role=role,
        phase="exploring",
        task_id="T1",
        policy_flags=base,
        get_project_structure=lambda: "src/",
        get_task_history=lambda tid: "task open",
        get_blueprint_overview=lambda: "v2 layered architecture; ADR-0071 commit point",
        get_verdict_history=lambda: "gate X: FAIL (2 prior); gate Y: PASS",
    )


def test_blueprint_signal_only_for_chief_engineer_when_enabled() -> None:
    reg = RoleSignalRegistry()
    flags = {"include_blueprint_overview": True}
    ce = allocate_role_signals(
        registry=reg, ctx=_role_ctx_with_assets("chief_engineer", flags=flags), receipt_store=ReceiptStore()
    )
    director = allocate_role_signals(
        registry=reg, ctx=_role_ctx_with_assets("director", flags=flags), receipt_store=ReceiptStore()
    )
    assert "blueprint_overview" in ce.sources
    assert any("【蓝图/技术架构】" in t["content"] for t in ce.turns)
    # 其它角色即便 flag 开也拿不到（role-bound）
    assert "blueprint_overview" not in director.sources


def test_verdict_signal_only_for_qa_and_is_must_have() -> None:
    reg = RoleSignalRegistry()
    flags = {"include_verdict_history": True}
    qa = allocate_role_signals(registry=reg, ctx=_role_ctx_with_assets("qa", flags=flags), receipt_store=ReceiptStore())
    assert "verdict_history" in qa.sources
    # must-have：预算压力 + freshness 未变也不被断流
    qa_pressure = allocate_role_signals(
        registry=reg,
        ctx=_role_ctx_with_assets("qa", flags=flags),
        receipt_store=ReceiptStore(),
        budget_pressure=True,
        previous_freshness={"verdict_history": "x", "project_structure": "y", "task_history": "z"},
    )
    assert "verdict_history" in qa_pressure.sources


def test_role_signals_off_by_default_keeps_baseline() -> None:
    """不开新 flag 时，CE/QA 的注入与旧实现一致（只有 seed 两条）。"""
    reg = RoleSignalRegistry()
    ce = allocate_role_signals(
        registry=reg,
        ctx=_role_ctx_with_assets("chief_engineer", flags={}),  # flags 默认不含 blueprint
        receipt_store=ReceiptStore(),
        per_signal_char_cap=None,
        total_char_budget=None,
    )
    assert ce.sources == ["project_structure", "task_history"]


def test_cross_role_isolation_blueprint_vs_verdict() -> None:
    """CE 拿蓝图不拿判定；QA 拿判定不拿蓝图。"""
    reg = RoleSignalRegistry()
    flags = {"include_blueprint_overview": True, "include_verdict_history": True}
    ce = allocate_role_signals(
        registry=reg, ctx=_role_ctx_with_assets("chief_engineer", flags=flags), receipt_store=ReceiptStore()
    )
    qa = allocate_role_signals(registry=reg, ctx=_role_ctx_with_assets("qa", flags=flags), receipt_store=ReceiptStore())
    assert "blueprint_overview" in ce.sources and "verdict_history" not in ce.sources
    assert "verdict_history" in qa.sources and "blueprint_overview" not in qa.sources
    assert all("质量判定" not in t["content"] for t in ce.turns)
    assert all("蓝图" not in t["content"] for t in qa.turns)


def test_must_have_survives_budget_pressure() -> None:
    """压力下 must-have 不被断流（即便要卸载也要挤入）。"""
    reg = RoleSignalRegistry((_MustHaveChanged(),))
    res = allocate_role_signals(
        registry=reg,
        ctx=_ctx("qa"),
        receipt_store=ReceiptStore(),
        budget_pressure=True,
        previous_freshness={"verdict_history": "changed"},  # 即便 prev 相同，must-have 也不跳过
    )
    assert "verdict_history" in res.sources
