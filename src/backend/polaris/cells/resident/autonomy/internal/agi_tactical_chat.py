"""Resident AGI tactical-console response composition."""

from __future__ import annotations

from typing import Any

from polaris.cells.resident.autonomy.internal.agi_tactical_actions import (
    resident_agi_tactical_action_catalog,
    resident_agi_tactical_action_payload,
)

_DEFAULT_PARTICIPATION_SCOPES = (
    "final_request_audit",
    "decision_trace",
    "capability_surface",
    "decision_boundary",
)

_INTENT_SCOPE_IDS: dict[str, tuple[str, ...]] = {
    "status_summary": ("capability_surface",),
    "supervision_summary": ("capability_surface",),
    "explain_blockage": ("quality_gate_response", "final_request_audit"),
    "audit_only": ("final_request_audit",),
    "evidence_refresh": ("evidence_interface_selection",),
    "resident_agi_judgement": (
        "quality_gate_response",
        "architecture_option_selection",
        "evidence_interface_selection",
        "goal_promotion",
        "decision_trace",
    ),
    "director_repair_request": (
        "director_repair_advisory_policy",
        "director_repair_strategy_catalog",
        "quality_gate_response",
    ),
}


def _intent(message: str) -> str:
    normalized = str(message or "").strip().lower()
    if any(term in normalized for term in ("修复", "director", "fix", "repair", "处理一下", "自动修")):
        return "director_repair_request"
    if any(term in normalized for term in ("判断", "决策", "怎么办", "下一步", "建议", "agi", "llm", "智能研判")):
        return "resident_agi_judgement"
    if any(term in normalized for term in ("为什么", "卡住", "阻塞", "失败", "门禁", "gate", "blocked")):
        return "explain_blockage"
    if any(term in normalized for term in ("审计", "证据", "只读", "黑匣子", "contextos", "receipt", "上下文")):
        return "audit_only"
    if any(term in normalized for term in ("刷新", "refresh", "证据接口")):
        return "evidence_refresh"
    if any(term in normalized for term in ("进度", "情况", "状态", "status", "progress", "项目")):
        return "status_summary"
    return "supervision_summary"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _gate_status(audit_pack: dict[str, Any], key: str) -> str:
    gate = _dict(audit_pack.get(key))
    return str(gate.get("status") or "unknown").strip().lower() or "unknown"


def _refs(value: Any) -> list[str]:
    refs: list[str] = []
    for item in _list(value):
        token = str(item or "").strip()
        if token and token not in refs:
            refs.append(token)
    return refs


def _scope_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(".", "_").replace("-", "_").replace(" ", "_")


def _participation(status_payload: dict[str, Any], intent: str) -> dict[str, Any]:
    identity = _dict(status_payload.get("identity"))
    raw_participation = _dict(identity.get("resident_agi_participation"))
    enabled = bool(raw_participation.get("enabled"))
    configured_scope_ids: list[str] = []
    for scope in _list(raw_participation.get("scopes")):
        scope_id = _scope_key(scope)
        if scope_id and scope_id not in configured_scope_ids:
            configured_scope_ids.append(scope_id)
    flags = _dict(raw_participation.get("participation"))
    for key, value in flags.items():
        scope_id = _scope_key(key)
        if scope_id and bool(value) and scope_id not in configured_scope_ids:
            configured_scope_ids.append(scope_id)
    if enabled and not configured_scope_ids:
        configured_scope_ids.extend(_DEFAULT_PARTICIPATION_SCOPES)

    required_scope_ids = list(_INTENT_SCOPE_IDS.get(intent, ("capability_surface",)))
    allowed = enabled and any(_scope_key(item) in configured_scope_ids for item in required_scope_ids)
    if not enabled:
        reason = "resident_agi_participation.enabled is false"
    elif allowed:
        reason = "configured scope permits this tactical-console intent"
    else:
        reason = "configured participation scopes do not cover this tactical-console intent"
    return {
        "schema_version": "resident.agi_tactical_chat_participation.v1",
        "enabled": enabled,
        "intent": intent,
        "allowed_for_intent": allowed,
        "required_scope_ids": required_scope_ids,
        "configured_scope_ids": configured_scope_ids,
        "reason": reason,
        "custom_scopes_allowed": bool(raw_participation.get("custom_scopes_allowed", True)),
    }


def _evidence_refs(
    *,
    audit_pack: dict[str, Any],
    evidence_interfaces: dict[str, Any],
    repair_overlay_query: dict[str, Any],
    explicit_refs: tuple[str, ...],
) -> list[str]:
    refs = _refs(explicit_refs)
    refs.extend(item for item in _refs(audit_pack.get("audit_refs")) if item not in refs)
    for item in _list(evidence_interfaces.get("interfaces")):
        interface = _dict(item)
        interface_id = str(interface.get("interface_id") or "").strip()
        source = str(interface.get("source") or "").strip()
        if interface_id and interface_id not in refs:
            refs.append(interface_id)
        if source and source not in refs:
            refs.append(source)
    decision_ref = _dict(repair_overlay_query.get("decision_ref"))
    decision_id = str(decision_ref.get("decision_id") or "").strip()
    if decision_id and decision_id not in refs:
        refs.append(decision_id)
    return refs[:12]


def _blockers(
    *,
    audit_pack: dict[str, Any],
    evidence_interfaces: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    hard_rule_status = _gate_status(audit_pack, "hard_rule_gate")
    evidence_status = _gate_status(audit_pack, "evidence_gate")
    if hard_rule_status not in {"pass", "ok", "unknown"}:
        blockers.append(f"硬规则门禁为 {hard_rule_status}，AGI 不能越过平台不变量。")
    if evidence_status in {"fail", "failed", "hold", "block", "blocked"}:
        blockers.append(f"证据门禁为 {evidence_status}，失败证据不能被当作已通过。")

    run_ledger_summary = _dict(audit_pack.get("run_ledger_summary"))
    failed_count = _int(run_ledger_summary.get("failed"))
    if failed_count > 0:
        blockers.append(f"Run Ledger 中仍有 {failed_count} 条失败门禁证据。")

    evidence_summary = _dict(evidence_interfaces.get("summary"))
    missing_required = _refs(evidence_summary.get("missing_required_interface_ids"))
    if missing_required:
        blockers.append(f"缺少必需证据接口：{', '.join(missing_required[:3])}。")
    return blockers


def _goal_status_counts(goals: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for goal in goals:
        goal_payload = _dict(goal)
        status = str(goal_payload.get("status") or "unknown").strip().lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _goal_progress_percent(goals: list[Any]) -> int:
    if not goals:
        return 0
    weights = {
        "pending": 15,
        "approved": 35,
        "staged": 55,
        "running": 75,
        "materialized": 90,
        "completed": 100,
        "done": 100,
        "rejected": 0,
        "failed": 0,
    }
    scores: list[int] = []
    for goal in goals:
        goal_payload = _dict(goal)
        status = str(goal_payload.get("status") or "unknown").strip().lower()
        scores.append(weights.get(status, 20))
    return max(0, min(100, round(sum(scores) / len(scores))))


def _mission_brief(
    *,
    runtime: dict[str, Any],
    goals: list[Any],
    decisions: list[Any],
    blockers: list[str],
    evidence_interfaces: dict[str, Any],
    participation: dict[str, Any],
    audit_pack: dict[str, Any],
) -> dict[str, Any]:
    evidence_summary = _dict(evidence_interfaces.get("summary"))
    goal_counts = _goal_status_counts(goals)
    latest_decision = _dict(decisions[0]) if decisions else {}
    primary_goal = _dict(goals[0]) if goals else {}
    hard_rule_status = _gate_status(audit_pack, "hard_rule_gate")
    evidence_gate_status = _gate_status(audit_pack, "evidence_gate")
    if blockers:
        severity = "danger"
        status_label = "存在阻塞"
    elif bool(runtime.get("active")):
        severity = "ok"
        status_label = "正在值守"
    else:
        severity = "idle"
        status_label = "等待启动"

    next_actions: list[str] = []
    if not bool(participation.get("enabled")):
        next_actions.append("如需 AGI 主动参与，先在身份设置中开启参与范围。")
    if _int(evidence_summary.get("missing_required")) > 0:
        next_actions.append("先刷新或补齐缺失证据接口，再请求 AGI 判断。")
    if blockers:
        next_actions.append("查看证据黑匣子，保留失败证据后进入受控修复链路。")
    if bool(participation.get("allowed_for_intent")):
        next_actions.append("可请求 AGI 角色回合给出继续、阻断、请求证据或升级的判断。")
    if not next_actions:
        next_actions.append("继续观察 runtime.v2 推送，必要时请求 AGI 解释当前状态。")

    return {
        "schema_version": "resident.agi_tactical_mission_brief.v1",
        "title": "项目态势",
        "severity": severity,
        "status_label": status_label,
        "progress_percent": _goal_progress_percent(goals),
        "current_focus": str(primary_goal.get("title") or "等待新的平台看护任务"),
        "current_stage": str(latest_decision.get("stage") or runtime.get("mode") or "observe"),
        "latest_verdict": str(latest_decision.get("verdict") or ""),
        "blockers": blockers[:3],
        "next_actions": next_actions[:4],
        "metrics": [
            {"label": "目标", "value": str(len(goals))},
            {"label": "决策", "value": str(len(decisions))},
            {
                "label": "证据",
                "value": f"{_int(evidence_summary.get('available'))}/{_int(evidence_summary.get('total'))}",
            },
            {"label": "门禁", "value": f"{hard_rule_status}/{evidence_gate_status}"},
        ],
        "goal_status_counts": goal_counts,
        "policy": {
            "read_from_public_contracts": True,
            "ui_must_not_recompute_verdict": True,
            "required_chain": "PM → Chief Engineer → Director → QA",
        },
    }


def _tool_trace(
    *,
    status_payload: dict[str, Any],
    audit_pack: dict[str, Any],
    evidence_interfaces: dict[str, Any],
    repair_overlay_query: dict[str, Any],
    participation: dict[str, Any],
) -> dict[str, Any]:
    evidence_summary = _dict(evidence_interfaces.get("summary"))
    evidence_available = _int(evidence_summary.get("available"))
    evidence_total = _int(evidence_summary.get("total"))
    audit_pack_schema = str(audit_pack.get("schema_version") or "").strip()
    repair_found = bool(repair_overlay_query.get("found"))
    items = [
        {
            "step_id": "auth.workspace_binding",
            "label": "工作区绑定",
            "mode": "authorization",
            "status": "passed",
            "contract": "Resident HTTP auth + workspace resolver",
            "summary": "请求已绑定到当前 workspace，没有使用默认工作区兜底。",
        },
        {
            "step_id": "resident.status.read",
            "label": "Resident 状态投影",
            "mode": "read_only",
            "status": "available" if status_payload else "empty",
            "contract": "resident.autonomy.public.query_resident_status",
            "summary": "读取 runtime、目标、决策、身份和 agenda 快照。",
        },
        {
            "step_id": "resident.agi_audit_pack.read",
            "label": "AGI 审计包",
            "mode": "read_only",
            "status": "available" if audit_pack_schema else "empty",
            "contract": "resident.autonomy.public.query_resident_agi_audit_pack",
            "summary": audit_pack_schema or "未发现可用审计包 schema。",
        },
        {
            "step_id": "resident.agi_evidence_interfaces.read",
            "label": "证据接口矩阵",
            "mode": "read_only",
            "status": "available" if evidence_available > 0 else "empty",
            "contract": "resident.autonomy.public.query_resident_agi_evidence_interfaces",
            "summary": f"可用 {evidence_available}/{evidence_total}。",
        },
        {
            "step_id": "director.repair_advisory_overlay.read",
            "label": "Director 修复建议覆盖层",
            "mode": "read_only",
            "status": "available" if repair_found else "not_found",
            "contract": "resident.autonomy.public.query_resident_agi_repair_advisory_overlay",
            "summary": "已找到可读 overlay。" if repair_found else "当前没有可注入的修复建议 overlay。",
        },
        {
            "step_id": "resident.agi_controlled_actions.boundary",
            "label": "受控动作边界",
            "mode": "controlled_action",
            "status": "available" if bool(participation.get("allowed_for_intent")) else "blocked",
            "contract": "resident.agi_tactical_chat_participation.v1",
            "summary": str(participation.get("reason") or ""),
        },
    ]
    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": "resident.agi_tactical_tool_trace.v1",
        "source": "resident.autonomy.public.query_resident_agi_tactical_chat",
        "items": items,
        "summary": {
            "total": len(items),
            "read_only": sum(1 for item in items if item.get("mode") == "read_only"),
            "controlled_action": sum(1 for item in items if item.get("mode") == "controlled_action"),
            "by_status": dict(sorted(status_counts.items())),
            "direct_execution_allowed": False,
        },
    }


def _suggested_actions(
    intent: str,
    blockers: list[str],
    participation: dict[str, Any],
    evidence_refs: list[str],
    context_refs: list[str],
) -> list[dict[str, Any]]:
    read_only_refresh_intents = {
        "status_summary",
        "supervision_summary",
        "explain_blockage",
        "audit_only",
        "evidence_refresh",
        "director_repair_request",
    }
    actions = [
        resident_agi_tactical_action_payload(
            "open_evidence_black_box",
            status="available",
        )
    ]
    if intent in read_only_refresh_intents or blockers:
        actions.append(
            resident_agi_tactical_action_payload(
                "refresh_evidence_interfaces",
                status="available",
                reason=(
                    "刷新证据接口是只读动作，不需要开启 AGI 参与；"
                    "它不会改变项目状态、不会写入目标，也不会放行失败门禁。"
                ),
            )
        )
    if not bool(participation.get("allowed_for_intent")) and intent in {
        "resident_agi_judgement",
        "director_repair_request",
    }:
        actions.append(
            resident_agi_tactical_action_payload(
                "open_operator_settings",
                status="available",
                reason=(
                    "当前 AGI 参与范围不允许这个意图；打开值守设定让用户手动选择参与范围，不会由 AGI 自动修改权限。"
                ),
            )
        )
    if bool(participation.get("allowed_for_intent")) and intent != "audit_only":
        actions.append(
            resident_agi_tactical_action_payload(
                "request_resident_agi_judgement",
                status="preview_only",
            )
        )
    if bool(participation.get("allowed_for_intent")) and (intent == "director_repair_request" or blockers):
        blocker_summary = "；".join(blockers[:3]) if blockers else "用户请求 AGI 整理 Director 受控修复预案。"
        actions.append(
            resident_agi_tactical_action_payload(
                "request_director_controlled_repair",
                status="preview_only",
                goal_draft={
                    "goal_type": "maintenance",
                    "title": "请求 Director 受控修复当前阻塞",
                    "motivation": (
                        "Resident AGI 战术控制台发现需要修复的阻塞："
                        f"{blocker_summary} 后续必须经 PM → Chief Engineer → Director → QA 链路处理。"
                    ),
                    "source": "resident_agi_tactical_console",
                    "scope": [
                        "resident.agi_tactical_chat",
                        "resident.goal_governance",
                        "director.controlled_repair",
                    ],
                    "budget": {
                        "handoff_chain": "PM → Chief Engineer → Director → QA",
                        "action_id": "request_director_controlled_repair",
                        "intent": intent,
                        "agi_direct_repair_allowed": False,
                    },
                    "evidence_refs": evidence_refs[:8],
                    "derived_from": context_refs[:8],
                    "expected_value": 0.72,
                    "risk_score": 0.42,
                },
            )
        )
    return [action for action in actions if action]


def _participation_gate(
    *,
    participation: dict[str, Any],
    suggested_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    required_scope_ids = _refs(participation.get("required_scope_ids"))
    configured_scope_ids = _refs(participation.get("configured_scope_ids"))
    configured_scope_keys = {_scope_key(item) for item in configured_scope_ids}
    enabled = bool(participation.get("enabled"))
    allowed = bool(participation.get("allowed_for_intent"))
    missing_scope_candidates = [
        scope_id for scope_id in required_scope_ids if _scope_key(scope_id) not in configured_scope_keys
    ]
    missing_scope_ids = [] if allowed else missing_scope_candidates
    suggested_action_ids = _refs([action.get("action_id") for action in suggested_actions])
    if allowed:
        status = "allowed"
        summary = "当前值守设定允许 AGI 处理这个意图。"
    elif not enabled:
        status = "disabled"
        summary = "AGI 参与总开关关闭；只允许只读解释和本地导航。"
    else:
        status = "scope_missing"
        summary = "AGI 参与范围不覆盖这个意图；只允许只读解释和本地导航。"
    return {
        "schema_version": "resident.agi_tactical_participation_gate.v1",
        "status": status,
        "enabled": enabled,
        "allowed_for_intent": allowed,
        "intent": str(participation.get("intent") or ""),
        "reason": str(participation.get("reason") or ""),
        "summary": summary,
        "required_scope_ids": required_scope_ids,
        "configured_scope_ids": configured_scope_ids,
        "missing_scope_ids": missing_scope_ids,
        "suggested_action_ids": suggested_action_ids,
        "settings_action_available": "open_operator_settings" in suggested_action_ids,
        "read_only_actions_available": any(
            str(action.get("mode") or "").strip() in {"read_only", "local_read_model"} for action in suggested_actions
        ),
        "governed_actions_available": any(
            str(action.get("ui_handler") or "").strip() == "execute_governed_action" for action in suggested_actions
        ),
        "agi_direct_permission_change_allowed": False,
    }


def _decision_route(
    *,
    intent: str,
    blockers: list[str],
    participation: dict[str, Any],
    audit_pack: dict[str, Any],
    evidence_interfaces: dict[str, Any],
    suggested_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    hard_rule_status = _gate_status(audit_pack, "hard_rule_gate")
    evidence_gate_status = _gate_status(audit_pack, "evidence_gate")
    recommended_action_ids = [
        str(action.get("action_id") or "").strip()
        for action in suggested_actions
        if str(action.get("action_id") or "").strip()
    ]
    read_only_actions = [
        action
        for action in suggested_actions
        if str(action.get("mode") or "").strip() in {"read_only", "local_read_model"}
    ]
    governed_actions = [
        action
        for action in suggested_actions
        if str(action.get("ui_handler") or "").strip() == "execute_governed_action"
        or str(action.get("mode") or "").strip() in {"controlled_execution", "execute_through_role_runtime"}
    ]
    evidence_summary = _dict(evidence_interfaces.get("summary"))
    missing_required = _refs(evidence_summary.get("missing_required_interface_ids"))
    blocked_reasons = list(blockers)
    if not bool(participation.get("enabled")):
        blocked_reasons.append("resident_agi_participation.enabled is false")
    elif not bool(participation.get("allowed_for_intent")):
        blocked_reasons.append(str(participation.get("reason") or "participation scope does not cover intent"))
    if missing_required:
        blocked_reasons.append(f"missing required evidence interfaces: {', '.join(missing_required[:3])}")

    if hard_rule_status not in {"pass", "ok", "unknown"}:
        route_status = "platform_blocked"
        route_reason = "platform hard-rule gate blocks AGI judgement"
    elif "request_director_controlled_repair" in recommended_action_ids:
        route_status = "ready_for_governed_handoff"
        route_reason = "controlled repair request is available through Resident goal governance"
    elif "request_resident_agi_judgement" in recommended_action_ids:
        route_status = "ready_for_role_turn"
        route_reason = "resident_agi role decision turn is available"
    elif bool(participation.get("allowed_for_intent")):
        route_status = "read_only_action_available"
        route_reason = "only read-only tactical actions are currently recommended"
    else:
        route_status = "read_only_explanation"
        route_reason = "AGI participation does not permit an execution-impacting recommendation"

    return {
        "schema_version": "resident.agi_tactical_decision_route.v1",
        "source": "resident.autonomy.internal.agi_tactical_chat",
        "intent": intent,
        "route_status": route_status,
        "route_reason": route_reason,
        "routing_basis": [
            "intent",
            "resident_agi_participation",
            "hard_rule_gate",
            "evidence_gate",
            "suggested_actions",
        ],
        "recommended_action_ids": recommended_action_ids,
        "read_only_action_ids": [
            str(action.get("action_id") or "").strip()
            for action in read_only_actions
            if str(action.get("action_id") or "").strip()
        ],
        "governed_action_ids": [
            str(action.get("action_id") or "").strip()
            for action in governed_actions
            if str(action.get("action_id") or "").strip()
        ],
        "blocked_reasons": _refs(blocked_reasons),
        "hard_rules": {
            "status": hard_rule_status,
            "platform_enforced": True,
            "llm_override_allowed": False,
        },
        "evidence_gate": {
            "status": evidence_gate_status,
            "missing_required_interface_ids": missing_required,
            "recommended_verdict": str(_dict(audit_pack.get("evidence_gate")).get("recommended_verdict") or ""),
        },
        "agi_judgement": {
            "allowed": "request_resident_agi_judgement" in recommended_action_ids,
            "action_id": "request_resident_agi_judgement",
            "execution_boundary": "execute_through_role_runtime_only",
            "requires_participation": True,
            "participation_allowed_for_intent": bool(participation.get("allowed_for_intent")),
        },
        "governed_execution": {
            "allowed": bool(governed_actions),
            "execution_boundary": "public_contract_handoff_only",
            "required_chain": "PM → Chief Engineer → Director → QA",
            "agi_direct_execution_allowed": False,
        },
    }


def _message(
    *,
    intent: str,
    runtime: dict[str, Any],
    goals: list[Any],
    decisions: list[Any],
    blockers: list[str],
    evidence_interfaces: dict[str, Any],
    repair_overlay_query: dict[str, Any],
    participation: dict[str, Any],
) -> str:
    active_label = "正在值守" if bool(runtime.get("active")) else "未启动"
    mode = str(runtime.get("mode") or "observe")
    evidence_summary = _dict(evidence_interfaces.get("summary"))
    evidence_available = _int(evidence_summary.get("available"))
    evidence_total = _int(evidence_summary.get("total"))
    blocker_text = "；".join(blockers[:3]) if blockers else "当前没有发现硬阻断。"
    if not bool(participation.get("enabled")):
        participation_note = "当前 AGI 参与开关关闭，我只能解释只读事实，不能提出受控执行建议。"
    elif not bool(participation.get("allowed_for_intent")):
        required = ", ".join(_refs(participation.get("required_scope_ids"))) or "未声明"
        participation_note = f"当前 AGI 参与范围不覆盖本意图（需要 {required}），我会降级为只读解释。"
    else:
        participation_note = "当前 AGI 参与范围允许我处理这个意图，但仍不能绕过平台硬规则。"

    if intent == "explain_blockage":
        return (
            f"{participation_note} 我按 Polaris 当前事实源排查了门禁和证据链。{blocker_text} "
            "如果要继续处理，应先保留失败证据，再通过受控角色链请求 Director 修复，不能由 AGI 直接放行。"
        )
    if intent == "audit_only":
        return (
            f"{participation_note} 我会按只读审计模式处理：当前状态为 {active_label}，模式 {mode}；"
            f"可用证据接口 {evidence_available}/{evidence_total}。我只引用公开 Cell 契约和审计投影，不执行写入。"
        )
    if intent == "director_repair_request":
        found_overlay = bool(repair_overlay_query.get("found"))
        overlay_text = (
            "已找到可读的 AGI repair advisory overlay"
            if found_overlay
            else "当前未发现可直接注入的 repair advisory overlay"
        )
        return (
            f"{participation_note} {overlay_text}。我可以把修复诉求整理成受控动作预案，但聊天入口不会直接写代码、不会跳过 Chief Engineer，"
            "也不会把 QA 失败标成通过。"
        )
    if intent == "evidence_refresh":
        return (
            f"{participation_note} 我已组合当前 evidence interface 投影：可用 {evidence_available}/{evidence_total}。"
            "刷新证据属于只读动作，可用于确认 ContextOS、Run Ledger、Audit 和 Director 策略目录是否可读。"
        )
    if intent == "resident_agi_judgement":
        return (
            f"{participation_note} 我可以把这个问题提交给 Resident AGI 角色回合做一次受控判断。"
            "判断会复用 ContextOS、Run Ledger、审计包和决策边界；结论只能是继续、阻断、请求证据或升级，"
            "不能替代 PM → Chief Engineer → Director → QA 链路。"
        )
    return (
        f"{participation_note} 我已读取 Resident runtime、目标、决策和 AGI 审计投影。当前 {active_label}，模式 {mode}；"
        f"目标 {len(goals)} 个，决策 {len(decisions)} 条，证据接口可用 {evidence_available}/{evidence_total}。{blocker_text}"
    )


def build_resident_agi_tactical_chat_response(
    *,
    workspace: str,
    message: str,
    context_refs: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    status_payload: dict[str, Any],
    audit_pack: dict[str, Any],
    evidence_interfaces: dict[str, Any],
    repair_overlay_query: dict[str, Any],
) -> dict[str, Any]:
    """Compose a readable AGI-console response from existing Resident facts."""

    intent = _intent(message)
    runtime = _dict(status_payload.get("runtime"))
    goals = _list(status_payload.get("goals"))
    decisions = _list(status_payload.get("decisions"))
    blockers = _blockers(audit_pack=audit_pack, evidence_interfaces=evidence_interfaces)
    participation = _participation(status_payload, intent)
    final_evidence_refs = _evidence_refs(
        audit_pack=audit_pack,
        evidence_interfaces=evidence_interfaces,
        repair_overlay_query=repair_overlay_query,
        explicit_refs=evidence_refs,
    )
    final_context_refs = _refs(context_refs)
    decision_profile = _dict(audit_pack.get("decision_profile"))
    evidence_summary = _dict(evidence_interfaces.get("summary"))
    mission_brief = _mission_brief(
        runtime=runtime,
        goals=goals,
        decisions=decisions,
        blockers=blockers,
        evidence_interfaces=evidence_interfaces,
        participation=participation,
        audit_pack=audit_pack,
    )
    tool_trace = _tool_trace(
        status_payload=status_payload,
        audit_pack=audit_pack,
        evidence_interfaces=evidence_interfaces,
        repair_overlay_query=repair_overlay_query,
        participation=participation,
    )
    suggested_actions = _suggested_actions(
        intent,
        blockers,
        participation,
        final_evidence_refs,
        final_context_refs,
    )
    participation_gate = _participation_gate(
        participation=participation,
        suggested_actions=suggested_actions,
    )
    decision_route = _decision_route(
        intent=intent,
        blockers=blockers,
        participation=participation,
        audit_pack=audit_pack,
        evidence_interfaces=evidence_interfaces,
        suggested_actions=suggested_actions,
    )
    return {
        "schema_version": "resident.agi_tactical_chat.v1",
        "source": "resident.autonomy.public.query_resident_agi_tactical_chat",
        "workspace": workspace,
        "intent": intent,
        "status": "blocked" if blockers else "ready",
        "message": _message(
            intent=intent,
            runtime=runtime,
            goals=goals,
            decisions=decisions,
            blockers=blockers,
            evidence_interfaces=evidence_interfaces,
            repair_overlay_query=repair_overlay_query,
            participation=participation,
        ),
        "flow": [
            "[授权] 使用 Resident HTTP 鉴权和 workspace 绑定",
            "[事实源] resident.status + resident.agi_audit_pack.v1",
            "[证据] resident.agi_evidence_interfaces.v1 + Director repair advisory projection",
            "[边界] AGI 聊天只读优先；受控动作必须进入 PM → Chief Engineer → Director → QA",
        ],
        "evidence_refs": final_evidence_refs,
        "context_refs": final_context_refs,
        "mission_brief": mission_brief,
        "tool_trace": tool_trace,
        "participation_gate": participation_gate,
        "decision_route": decision_route,
        "suggested_actions": suggested_actions,
        "action_catalog": resident_agi_tactical_action_catalog(),
        "receipt": {
            "schema_version": "resident.agi_tactical_chat_receipt.v1",
            "status": "READ",
            "title": "战术问答凭证",
            "summary": "已通过 Resident public contract 组合 AGI 控制台答复；未执行写入或门禁放行。",
            "rows": [
                {"label": "意图", "value": intent},
                {"label": "事实源", "value": "resident.autonomy.public"},
                {
                    "label": "证据接口",
                    "value": f"{evidence_summary.get('available', 0)}/{evidence_summary.get('total', 0)}",
                },
                {
                    "label": "AGI 参与",
                    "value": "enabled" if participation["enabled"] else "disabled",
                },
                {"label": "执行边界", "value": "read_first_controlled_actions"},
            ],
        },
        "facts": {
            "runtime": {
                "active": bool(runtime.get("active")),
                "mode": str(runtime.get("mode") or "observe"),
                "last_tick_at": str(runtime.get("last_tick_at") or ""),
            },
            "counts": {
                "goals": len(goals),
                "decisions": len(decisions),
                "blockers": len(blockers),
                "evidence_available": _int(evidence_summary.get("available")),
                "evidence_total": _int(evidence_summary.get("total")),
            },
            "gates": {
                "hard_rule_gate": _gate_status(audit_pack, "hard_rule_gate"),
                "evidence_gate": _gate_status(audit_pack, "evidence_gate"),
                "recommended_verdict": str(decision_profile.get("recommended_verdict") or ""),
            },
            "repair_advisory": {
                "found": bool(repair_overlay_query.get("found")),
                "status": str(repair_overlay_query.get("status") or "not_found"),
            },
            "participation": participation,
            "participation_gate_schema": participation_gate["schema_version"],
            "decision_route_schema": decision_route["schema_version"],
        },
        "policy": {
            "advisory_only": True,
            "participation_enabled": bool(participation["enabled"]),
            "participation_allowed_for_intent": bool(participation["allowed_for_intent"]),
            "participation_reason": str(participation["reason"]),
            "agi_direct_writes_allowed": False,
            "agi_direct_repair_allowed": False,
            "required_chain": "PM → Chief Engineer → Director → QA",
            "decision_endpoint": "/v2/resident/agi/decide",
        },
    }
