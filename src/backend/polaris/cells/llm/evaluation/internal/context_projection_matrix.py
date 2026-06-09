"""ContextOS / ProjectionEngine 评测矩阵（确定性，无 LLM）.

ProjectionEngine 是真实热路径上的"最终 prompt 组装器"（`context_gateway.gateway`
每个 turn 都调 `project()`）。它的"真实效果"是对发给模型的 messages 做四件可验证的事：

1. **control-plane 隔离**：budget_status/metrics/policy_verdict/telemetry/thinking 等
   控制面字段绝不能进入模型可见的 messages（顶层键 + 每条 turn 的 metadata）。
2. **receipt 卸载**：超阈值的大工具输出被替换成占位符 + receipt_ref（省 token），
   且原文可经 ReceiptStore 取回（不丢信息）。
3. **user 指令置末**：当前 user turn 必须是最后一条，历史 run_card/tail_hint 不得盖过它。
4. **结构化发现注入 / system_hint 置首 / 稳定排序**：confirmed_facts 进 system；
   system_hint 在最前；同等 patch 信号按 sequence 稳定输出，自适应权重必须能改变
   支撑上下文的投影排序。

这些是每个 prompt 的正确性/卫生属性，确定性可判定——故本套件是确定性矩阵（像
deterministic judge，不需要 LLM）。运行产出每用例 PASS/FAIL + 聚合指标
（control-plane 泄漏数必须为 0、receipt 省下的字符数等）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping

from polaris.cells.roles.kernel.public.service import (
    DEFAULT_SIGNAL_PROVIDERS,
    RoleSignalRegistry,
    SignalBlock,
    SignalBuildContext,
    allocate_role_signals,
)
from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore


def _signal_ctx(role: str, *, task_id: str = "T1") -> SignalBuildContext:
    return SignalBuildContext(
        role=role,
        phase="exploring",
        task_id=task_id,
        policy_flags={"include_project_structure": True, "include_task_history": True},
        get_project_structure=lambda: "src/\n  a.py",
        get_task_history=lambda tid: f"task {tid}: 1 open",
    )


class _BlueprintSignal:
    """chief_engineer 专属信号（评测隔离用）。"""

    id = "blueprint_overview"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return ctx.role == "chief_engineer"

    def priority(self, ctx: SignalBuildContext) -> int:
        return 5

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        return SignalBlock(id=self.id, content="【蓝图】v2 architecture overview", priority=5, freshness_key="bp1")


class _HugePlanSignal:
    id = "plan_overview"

    def applies_to(self, ctx: SignalBuildContext) -> bool:
        return True

    def priority(self, ctx: SignalBuildContext) -> int:
        return 2

    def build(self, ctx: SignalBuildContext) -> SignalBlock | None:
        return SignalBlock(id=self.id, content="PLAN " * 20000, priority=2, freshness_key="huge")


def _case_role_signal_byte_identical() -> dict[str, Any]:
    """逐字节 + 绝对索引一致：seed-only 注入 == 旧硬编码实现。"""
    res = allocate_role_signals(
        registry=RoleSignalRegistry(),
        ctx=_signal_ctx("director"),
        receipt_store=ReceiptStore(),
        per_signal_char_cap=None,
        total_char_budget=None,
    )
    expected = [
        {"role": "system", "content": "【项目结构】\nsrc/\n  a.py", "name": "project_structure"},
        {"role": "system", "content": "【任务历史】\ntask T1: 1 open", "name": "task_history"},
    ]
    ok = res.turns == expected and res.sources == ["project_structure", "task_history"]
    return {
        "case": "role_signal_byte_identical",
        "passed": ok,
        "checks": [{"name": "seed_output_byte_identical_and_ordered", "ok": ok, "detail": f"sources={res.sources}"}],
        "metrics": {},
    }


def _case_role_signal_cross_role_isolation() -> dict[str, Any]:
    """跨角色隔离：QA turn 的 payload 绝不含 blueprint。"""
    reg = RoleSignalRegistry((*DEFAULT_SIGNAL_PROVIDERS, _BlueprintSignal()))
    ce = allocate_role_signals(
        registry=reg,
        ctx=_signal_ctx("chief_engineer"),
        receipt_store=ReceiptStore(),
        per_signal_char_cap=None,
        total_char_budget=None,
    )
    qa = allocate_role_signals(
        registry=reg,
        ctx=_signal_ctx("qa"),
        receipt_store=ReceiptStore(),
        per_signal_char_cap=None,
        total_char_budget=None,
    )
    ce_has = "blueprint_overview" in ce.sources
    qa_clean = "blueprint_overview" not in qa.sources and all("蓝图" not in t["content"] for t in qa.turns)
    checks = [
        {"name": "chief_engineer_gets_blueprint", "ok": ce_has, "detail": f"ce.sources={ce.sources}"},
        {"name": "qa_payload_has_no_blueprint", "ok": qa_clean, "detail": "isolation enforced"},
    ]
    return {
        "case": "role_signal_cross_role_isolation",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {"leaks": 0 if qa_clean else 1},
    }


def _signal_ctx_with_assets(role: str, *, blueprint: bool, verdict: bool) -> SignalBuildContext:
    return SignalBuildContext(
        role=role,
        phase="exploring",
        task_id="T1",
        policy_flags={
            "include_project_structure": True,
            "include_task_history": True,
            "include_blueprint_overview": blueprint,
            "include_verdict_history": verdict,
        },
        get_project_structure=lambda: "src/\n  a.py",
        get_task_history=lambda tid: f"task {tid}: 1 open",
        get_blueprint_overview=lambda: "v2 layered architecture; single commit point",
        get_verdict_history=lambda: "gate X: FAIL; gate Y: PASS",
    )


def _case_role_specific_assets_and_isolation() -> dict[str, Any]:
    """真·角色信号：CE 拿蓝图、QA 拿判定，且互不串台。"""
    reg = RoleSignalRegistry()
    ce = allocate_role_signals(
        registry=reg,
        ctx=_signal_ctx_with_assets("chief_engineer", blueprint=True, verdict=True),
        receipt_store=ReceiptStore(),
        per_signal_char_cap=None,
        total_char_budget=None,
    )
    qa = allocate_role_signals(
        registry=reg,
        ctx=_signal_ctx_with_assets("qa", blueprint=True, verdict=True),
        receipt_store=ReceiptStore(),
        per_signal_char_cap=None,
        total_char_budget=None,
    )
    ce_ok = "blueprint_overview" in ce.sources and "verdict_history" not in ce.sources
    qa_ok = "verdict_history" in qa.sources and "blueprint_overview" not in qa.sources
    leak = any("质量判定" in t["content"] for t in ce.turns) or any("蓝图" in t["content"] for t in qa.turns)
    checks = [
        {"name": "chief_engineer_gets_blueprint_only", "ok": ce_ok, "detail": f"ce={ce.sources}"},
        {"name": "qa_gets_verdict_only", "ok": qa_ok, "detail": f"qa={qa.sources}"},
        {"name": "no_cross_asset_leak", "ok": not leak, "detail": "blueprint↔verdict isolated"},
    ]
    return {
        "case": "role_specific_assets_and_isolation",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {"leaks": 0 if not leak else 1},
    }


def _case_role_signal_circuit_breaker() -> dict[str, Any]:
    """熔断截断：超大信号 → 截断为 receipt:// 占位符，原文可取回。"""
    rs = ReceiptStore()
    reg = RoleSignalRegistry((_HugePlanSignal(),))
    res = allocate_role_signals(registry=reg, ctx=_signal_ctx("pm"), receipt_store=rs, per_signal_char_cap=4000)
    plan_turn = next((t for t in res.turns if t.get("name") == "plan_overview"), None)
    has_ref = plan_turn is not None and "receipt://plan_overview" in plan_turn.get("content", "")
    retrievable = rs.get("plan_overview") == "PLAN " * 20000
    saved = len("PLAN " * 20000) - (len(plan_turn["content"]) if plan_turn else 0)
    checks = [
        {"name": "oversized_signal_truncated_to_receipt", "ok": has_ref, "detail": "receipt:// placeholder present"},
        {"name": "original_retrievable", "ok": retrievable, "detail": "full content recoverable"},
    ]
    return {
        "case": "role_signal_circuit_breaker",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {"chars_saved": saved if saved > 0 else 0},
    }


# ProjectionEngine 承诺剥离的控制面键（与 ProjectionEngine 内部常量对齐）。
_CONTROL_PLANE_KEYS = (
    "budget_status",
    "metrics",
    "policy_verdict",
    "system_warnings",
    "telemetry",
    "telemetry_events",
)
_TURN_BLOCKED_META_KEYS = (
    "budget_status",
    "metrics",
    "policy_verdict",
    "raw_output",
    "system_warnings",
    "telemetry",
    "telemetry_events",
    "thinking",
    "thinking_content",
)


def _evt(
    *,
    sequence: int,
    role: str,
    content: str,
    route: str = "patch",
    event_id: str = "",
    metadata: tuple[tuple[str, Any], ...] = (),
    artifact_id: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        sequence=sequence,
        role=role,
        content=content,
        route=route,
        event_id=event_id or f"e{sequence}",
        metadata=metadata,
        artifact_id=artifact_id,
    )


def _messages_have_control_plane_leak(messages: list[dict[str, Any]]) -> list[str]:
    """返回泄漏证据列表（应为空）."""
    leaks: list[str] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, Mapping):
            continue
        for key in _CONTROL_PLANE_KEYS:
            if key in msg:
                leaks.append(f"msg[{i}] top-level control key {key!r}")
        meta = msg.get("metadata")
        if isinstance(meta, Mapping):
            for key in _TURN_BLOCKED_META_KEYS:
                if key in meta:
                    leaks.append(f"msg[{i}] metadata blocked key {key!r}")
    return leaks


# ---------------------------------------------------------------------------
# 用例：每个返回 (verdict_dict)。verdict: {case, passed, checks:[{name,ok,detail}], metrics}
# ---------------------------------------------------------------------------


def _case_control_plane_isolation() -> dict[str, Any]:
    engine = ProjectionEngine()
    rs = ReceiptStore()
    projection = {
        # 顶层控制面噪声（必须被剥离）
        "budget_status": {"tokens": 999},
        "metrics": {"latency_ms": 12},
        "policy_verdict": "allow",
        "telemetry": ["x"],
        "system_hint": "You are the director.",
        "turns": [
            {
                "role": "assistant",
                "content": "Reading config.",
                # turn metadata 含被封锁键（thinking/policy_verdict 必须被 sanitize 掉）
                "metadata": {"thinking": "secret chain of thought", "policy_verdict": "allow", "dialog_act": "commit"},
            },
            {"role": "user", "content": "now do X"},
        ],
    }
    messages = engine.project(projection, rs)
    leaks = _messages_have_control_plane_leak(messages)
    # dialog_act（非封锁键）应保留
    kept_dialog_act = any(isinstance(m.get("metadata"), Mapping) and "dialog_act" in m["metadata"] for m in messages)
    checks = [
        {"name": "no_control_plane_leak", "ok": not leaks, "detail": "; ".join(leaks) or "clean"},
        {"name": "non_blocked_metadata_kept", "ok": kept_dialog_act, "detail": "dialog_act preserved"},
    ]
    return {
        "case": "control_plane_isolation",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {"leaks": len(leaks)},
    }


def _case_user_message_last() -> dict[str, Any]:
    engine = ProjectionEngine()
    rs = ReceiptStore()
    projection = {
        "system_hint": "sys",
        "tail_hint": "tail reminder",
        "run_card": "【Run Card】Goal: finish",
        "turns": [
            {"role": "assistant", "content": "earlier"},
            {"role": "user", "content": "CURRENT INTENT: do the thing"},
        ],
    }
    messages = engine.project(projection, rs)
    last = messages[-1] if messages else {}
    ok_last = str(last.get("role")) == "user" and "CURRENT INTENT" in str(last.get("content"))
    # tail_hint / run_card 必须出现在 user 之前（不得盖过当前意图）
    user_idx = next((i for i, m in enumerate(messages) if m.get("role") == "user"), -1)
    tail_idx = next((i for i, m in enumerate(messages) if "tail reminder" in str(m.get("content"))), -1)
    rc_idx = next((i for i, m in enumerate(messages) if "Run Card" in str(m.get("content"))), -1)
    ok_order = user_idx == len(messages) - 1 and tail_idx < user_idx and rc_idx < user_idx
    checks = [
        {"name": "user_is_last", "ok": ok_last, "detail": f"last role={last.get('role')}"},
        {"name": "hints_before_user", "ok": ok_order, "detail": f"user@{user_idx} tail@{tail_idx} rc@{rc_idx}"},
    ]
    return {
        "case": "user_message_last",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {},
    }


def _case_receipt_offloading() -> dict[str, Any]:
    engine = ProjectionEngine()
    rs = ReceiptStore()
    big = "RESULT_LINE " * 600  # ~7800 chars，远超 tool 阈值 500
    events = [
        _evt(sequence=1, role="user", content="search the repo"),
        _evt(sequence=2, role="tool", content=big, event_id="grep1"),
    ]
    payload = engine.build_payload(active_window=events, receipt_store=rs)
    turns = payload["turns"]
    tool_turn = next((t for t in turns if t.get("role") == "tool"), None)
    offloaded = tool_turn is not None and bool(tool_turn.get("receipt_refs"))
    # 占位符短，原文长 → 字符节省
    projected_len = len(str(tool_turn.get("content"))) if tool_turn else 0
    saved = len(big) - projected_len
    # 原文可经 receipt 取回（信息不丢）
    retrievable = False
    if tool_turn and tool_turn.get("receipt_refs"):
        ref = tool_turn["receipt_refs"][0]
        retrievable = rs.get(ref) == big
    checks = [
        {
            "name": "tool_output_offloaded",
            "ok": offloaded,
            "detail": f"refs={tool_turn.get('receipt_refs') if tool_turn else None}",
        },
        {
            "name": "chars_saved_positive",
            "ok": saved > 5000,
            "detail": f"saved={saved} (orig={len(big)} proj={projected_len})",
        },
        {"name": "original_retrievable", "ok": retrievable, "detail": "full content recoverable from receipt"},
    ]
    return {
        "case": "receipt_offloading",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {"chars_saved": saved},
    }


def _case_structured_findings_injection() -> dict[str, Any]:
    engine = ProjectionEngine()
    rs = ReceiptStore()
    projection = {
        "system_hint": "sys",
        "structured_findings": {"confirmed_facts": ["config.py defines API_PORT=49977", "server.py is a stub"]},
        "turns": [{"role": "user", "content": "summarize"}],
    }
    messages = engine.project(projection, rs)
    facts_msg = next(
        (m for m in messages if m.get("role") == "system" and "Confirmed Facts" in str(m.get("content"))), None
    )
    ok_inject = facts_msg is not None and "API_PORT=49977" in str(facts_msg.get("content"))
    checks = [
        {
            "name": "confirmed_facts_injected_as_system",
            "ok": ok_inject,
            "detail": "facts present" if ok_inject else "missing",
        }
    ]
    return {
        "case": "structured_findings_injection",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {},
    }


def _case_content_level_control_plane_stripped() -> dict[str, Any]:
    """非信号角色(tool)内容里的框架性控制面标记必须被剥离,但真实内容保留。"""
    engine = ProjectionEngine()
    rs = ReceiptStore()
    projection = {
        "turns": [
            {"role": "tool", "content": "<tool_result>found 3 matches in config.py</tool_result>"},
            {"role": "tool", "content": "[system warning] internal budget exceeded\nreal grep line: API_PORT"},
            {"role": "user", "content": "ok"},
        ],
    }
    messages = engine.project(projection, rs)
    tool_contents = [str(m.get("content")) for m in messages if m.get("role") == "tool"]
    joined = "\n".join(tool_contents)
    no_tag = "<tool_result>" not in joined and "</tool_result>" not in joined
    no_warning_line = "[system warning]" not in joined
    real_kept = "found 3 matches in config.py" in joined and "real grep line: API_PORT" in joined
    checks = [
        {"name": "tool_result_tags_stripped", "ok": no_tag, "detail": "tags removed"},
        {"name": "system_warning_line_removed", "ok": no_warning_line, "detail": "noise line removed"},
        {"name": "real_tool_content_preserved", "ok": real_kept, "detail": "semantic content kept"},
    ]
    return {
        "case": "content_level_control_plane_stripped",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {"leaks": 0 if (no_tag and no_warning_line) else 1},
    }


def _case_signal_role_content_untouched() -> dict[str, Any]:
    """信号角色(user/assistant)的内容不得被改动——即便里面出现了类似标记的字符串。"""
    engine = ProjectionEngine()
    rs = ReceiptStore()
    literal = "please grep for the literal string <tool_result> in the codebase"
    projection = {"turns": [{"role": "user", "content": literal}]}
    messages = engine.project(projection, rs)
    user_msg = next((m for m in messages if m.get("role") == "user"), None)
    untouched = user_msg is not None and str(user_msg.get("content")) == literal
    checks = [{"name": "user_content_verbatim", "ok": untouched, "detail": "signal-role content preserved verbatim"}]
    return {
        "case": "signal_role_content_untouched",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {},
    }


def _case_chronological_order_and_system_first() -> dict[str, Any]:
    engine = ProjectionEngine()
    rs = ReceiptStore()
    # 故意乱序输入；同等 patch 信号应按 sequence 稳定输出，且 system_hint 在最前。
    events = [
        _evt(sequence=3, role="assistant", content="third"),
        _evt(sequence=1, role="user", content="first"),
        _evt(sequence=2, role="assistant", content="second"),
    ]
    payload = engine.build_payload(active_window=events, receipt_store=rs, head_anchor="SYSTEM HINT")
    messages = engine.project(payload, rs)
    contents = [str(m.get("content")) for m in messages]
    first_is_system = bool(messages) and messages[0].get("role") == "system" and "SYSTEM HINT" in contents[0]
    # 提取三条事件内容的相对顺序
    order_ok = True
    try:
        i1 = next(i for i, c in enumerate(contents) if "first" in c)
        i2 = next(i for i, c in enumerate(contents) if "second" in c)
        i3 = next(i for i, c in enumerate(contents) if "third" in c)
        order_ok = i1 < i2 < i3
    except StopIteration:
        order_ok = False
    checks = [
        {"name": "system_hint_first", "ok": first_is_system, "detail": contents[0][:40] if contents else "empty"},
        {"name": "chronological_order", "ok": order_ok, "detail": "first<second<third by sequence"},
    ]
    return {
        "case": "chronological_order_and_system_first",
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "metrics": {},
    }


def _case_empty_window_robust() -> dict[str, Any]:
    engine = ProjectionEngine()
    rs = ReceiptStore()
    ok = True
    detail = "ok"
    try:
        payload = engine.build_payload(active_window=[], receipt_store=rs)
        messages = engine.project(payload, rs)
        ok = isinstance(messages, list)
        detail = f"messages={len(messages)}"
    except Exception as exc:  # noqa: BLE001 - robustness probe
        ok = False
        detail = f"raised {exc!r}"
    return {
        "case": "empty_window_robust",
        "passed": ok,
        "checks": [{"name": "no_crash_on_empty", "ok": ok, "detail": detail}],
        "metrics": {},
    }


def _case_adaptive_learning_effect_probe() -> dict[str, Any]:
    """硬门禁：ProjectionEngine 自适应学习必须真实改变 prompt 投影。

    确定性地走完整因果链（无需 LLM——若机制不改 prompt，真实 LLM 必然测不到信号）：
      (1) 训练（record_outcome）改变角色级权重；
      (2) 权重改变会改变 active_window 的最终排序；
      (3) 两者共同证明 adaptive learning 不再只是内部计数或文档声明。
    """
    from polaris.kernelone.context.projection_engine import (
        ProjectionEngine,
        reset_projection_adaptive_state,
    )

    reset_projection_adaptive_state()

    # (1) 训练是否改变权重
    e = ProjectionEngine(learning_key="probe_train")
    win = [
        _evt(sequence=1, role="assistant", content="a", route="patch", metadata=(("routing_confidence", 0.95),)),
        _evt(sequence=2, role="assistant", content="b", route="patch", metadata=(("routing_confidence", 0.9),)),
    ]
    e.sort_events(win)
    before = e.get_adaptive_weights()["route_weight"]
    for _ in range(6):
        e.record_outcome(success=True)
    training_changes_weights = abs(e.get_adaptive_weights()["route_weight"] - before) > 1e-6

    # (2) 极端权重是否改变投影顺序（唯一 sequence = 真实常态）
    events = [
        _evt(
            sequence=1,
            role="assistant",
            content="s1_clear_high_conf",
            route="clear",
            metadata=(("routing_confidence", 0.99),),
        ),
        _evt(
            sequence=2,
            role="assistant",
            content="s2_archive_low_conf",
            route="archive",
            metadata=(("routing_confidence", 0.01),),
        ),
        _evt(
            sequence=3,
            role="assistant",
            content="s3_summarize_mid_conf",
            route="summarize",
            metadata=(("routing_confidence", 0.3),),
        ),
    ]
    e2 = ProjectionEngine(learning_key="probe_order")
    order_default = [x.content for x in e2.sort_events(events)]
    e2._weights.route_weight = 0.99
    e2._weights.confidence_weight = 0.0
    e2._weights.recency_weight = 0.0
    order_extreme = [x.content for x in e2.sort_events(events)]
    weights_change_ordering = order_default != order_extreme

    affects_prompt = training_changes_weights and weights_change_ordering

    checks = [
        # 可验证不变量（本会话修复的"训练半边"）——作为 gating check。
        {
            "name": "training_mechanism_changes_weights",
            "ok": training_changes_weights,
            "detail": f"before={before:.4f}",
        },
        {
            "name": "adaptive_weights_change_ordering",
            "ok": weights_change_ordering,
            "detail": f"default={order_default} extreme={order_extreme}",
        },
        {
            "name": "adaptive_affects_prompt",
            "ok": affects_prompt,
            "detail": f"affects_prompt={int(affects_prompt)}",
        },
    ]
    return {
        "case": "adaptive_learning_effect_probe",
        "passed": affects_prompt,
        "checks": checks,
        "metrics": {
            "adaptive_affects_prompt": int(affects_prompt),
            "adaptive_weights_change_ordering": int(weights_change_ordering),
            "adaptive_training_works": int(training_changes_weights),
        },
    }


_CASES = (
    _case_control_plane_isolation,
    _case_content_level_control_plane_stripped,
    _case_signal_role_content_untouched,
    _case_user_message_last,
    _case_receipt_offloading,
    _case_structured_findings_injection,
    _case_chronological_order_and_system_first,
    _case_empty_window_robust,
    _case_role_signal_byte_identical,
    _case_role_signal_cross_role_isolation,
    _case_role_specific_assets_and_isolation,
    _case_role_signal_circuit_breaker,
    _case_adaptive_learning_effect_probe,
)


async def run_context_projection_matrix_suite(
    provider_cfg: dict[str, Any],
    model: str,
    role: str,
    *,
    workspace: str,
    settings: Any | None = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """运行 ProjectionEngine 确定性评测矩阵.

    硬门禁：control-plane 泄漏总数必须为 0，且所有用例 PASS。
    """
    del provider_cfg, model, role, workspace, settings, context, options
    results = [case() for case in _CASES]
    passed = sum(1 for r in results if r["passed"])
    total_leaks = sum(int(r["metrics"].get("leaks", 0)) for r in results)
    total_saved = sum(int(r["metrics"].get("chars_saved", 0)) for r in results)
    adaptive_affects_prompt = max((int(r["metrics"].get("adaptive_affects_prompt", 0)) for r in results), default=0)
    ok = passed == len(results) and total_leaks == 0
    return {
        "ok": ok,
        "details": {
            "suite": "context_projection_matrix",
            "cases": results,
            "summary": {
                "total_cases": len(results),
                "passed_cases": passed,
                "failed_cases": len(results) - passed,
                "control_plane_leaks_total": total_leaks,
                "receipt_chars_saved_total": total_saved,
                # 硬门禁指标：自适应学习是否真正影响 prompt（0=否）。
                "adaptive_affects_prompt": adaptive_affects_prompt,
            },
        },
    }
