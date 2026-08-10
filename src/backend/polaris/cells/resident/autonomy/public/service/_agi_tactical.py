"""Resident AGI tactical chat and action execution."""

from __future__ import annotations

from typing import Any

from polaris.cells.resident.autonomy.internal.agi_tactical_actions import (
    resident_agi_tactical_action_catalog,
    resident_agi_tactical_action_payload,
    resident_agi_tactical_action_spec,
)
from polaris.cells.resident.autonomy.internal.agi_tactical_chat import (
    build_resident_agi_tactical_chat_response,
)
from polaris.cells.resident.autonomy.public.contracts import (
    CreateResidentGoalCommandV1,
    ExecuteResidentAgiTacticalActionCommandV1,
    QueryResidentAgiAuditPackV1,
    QueryResidentAgiEvidenceInterfacesV1,
    QueryResidentAgiRepairAdvisoryOverlayV1,
    QueryResidentAgiTacticalChatV1,
    QueryResidentStatusV1,
    RecordResidentDecisionCommandV1,
    RunResidentAgiDecisionTurnCommandV1,
)

from ._agi_decision import run_resident_agi_decision_turn
from ._agi_interfaces import query_resident_agi_evidence_interfaces
from ._agi_participation import (
    query_resident_agi_audit_pack,
    query_resident_agi_repair_advisory_overlay,
)
from ._helpers import _merge_non_empty_strings, logger
from ._lifecycle import create_resident_goal, query_resident_status, record_resident_decision_entry


def query_resident_agi_tactical_chat(query: QueryResidentAgiTacticalChatV1) -> dict[str, Any]:
    """Return a tactical-console response backed by Resident AGI public evidence."""

    status_payload = query_resident_status(QueryResidentStatusV1(workspace=query.workspace), include_details=True)
    audit_pack = query_resident_agi_audit_pack(
        QueryResidentAgiAuditPackV1(workspace=query.workspace, decision_limit=query.decision_limit)
    )
    evidence_interfaces = query_resident_agi_evidence_interfaces(
        QueryResidentAgiEvidenceInterfacesV1(
            workspace=query.workspace,
            decision_type=query.decision_type,
            run_id=query.run_id,
            task_id=query.task_id,
            context_refs=query.context_refs,
            evidence_refs=query.evidence_refs,
            decision_limit=query.decision_limit,
            max_runs=query.max_runs,
        )
    )
    repair_overlay_query = query_resident_agi_repair_advisory_overlay(
        QueryResidentAgiRepairAdvisoryOverlayV1(workspace=query.workspace, limit=query.decision_limit)
    )
    return build_resident_agi_tactical_chat_response(
        workspace=query.workspace,
        message=query.message,
        context_refs=query.context_refs,
        evidence_refs=query.evidence_refs,
        status_payload=status_payload,
        audit_pack=audit_pack,
        evidence_interfaces=evidence_interfaces,
        repair_overlay_query=repair_overlay_query,
    )


def query_resident_agi_tactical_action_catalog() -> dict[str, Any]:
    """Return the read-only Resident AGI tactical-console action catalog."""

    return resident_agi_tactical_action_catalog()


def _resident_agi_tactical_action_tool_trace(
    *,
    action_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    for item in items:
        status_key = str(item.get("status") or "unknown").strip() or "unknown"
        mode_key = str(item.get("mode") or "unknown").strip() or "unknown"
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        mode_counts[mode_key] = mode_counts.get(mode_key, 0) + 1
    return {
        "schema_version": "resident.agi_tactical_action_tool_trace.v1",
        "source": "resident.autonomy.public.execute_resident_agi_tactical_action",
        "action_id": action_id,
        "items": items,
        "summary": {
            "total": len(items),
            "by_status": dict(sorted(status_counts.items())),
            "by_mode": dict(sorted(mode_counts.items())),
            "direct_execution_allowed": False,
            "agi_direct_repair_allowed": False,
            "required_chain": "PM → Chief Engineer → Director → QA",
        },
    }


def _resident_agi_tactical_follow_up_actions(
    *,
    action_id: str,
    chat: dict[str, Any],
    verdict: str = "",
    created_goal: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(action: dict[str, Any]) -> None:
        action_key = str(action.get("action_id") or "").strip()
        if not action_key or action_key in seen:
            return
        seen.add(action_key)
        actions.append(action)

    add(
        resident_agi_tactical_action_payload(
            "open_evidence_black_box",
            status="available",
            reason="查看本次 AGI 动作使用过的审计证据和契约轨迹。",
        )
    )
    normalized_verdict = str(verdict or "").strip().lower()
    if normalized_verdict == "request_evidence":
        add(
            resident_agi_tactical_action_payload(
                "refresh_evidence_interfaces",
                status="available",
                reason="AGI 判断需要更多证据；刷新 evidence interface read model。",
            )
        )

    if action_id == "request_resident_agi_judgement" and normalized_verdict in {
        "block",
        "escalate",
        "request_evidence",
    }:
        chat_actions_raw = chat.get("suggested_actions")
        chat_actions = chat_actions_raw if isinstance(chat_actions_raw, list) else []
        for item in chat_actions:
            if isinstance(item, dict) and str(item.get("action_id") or "") == "request_director_controlled_repair":
                add(dict(item))
                break

    if action_id == "request_director_controlled_repair" and created_goal:
        add(resident_agi_tactical_action_payload("open_goals_tab", status="available"))
        add(
            resident_agi_tactical_action_payload(
                "request_resident_agi_judgement",
                status="preview_only",
                reason="让 resident_agi 角色回合基于新目标和证据再判断下一步。",
            )
        )
    return actions


async def execute_resident_agi_tactical_action(command: ExecuteResidentAgiTacticalActionCommandV1) -> dict[str, Any]:
    """Execute a governed Resident AGI tactical-console action."""

    chat = query_resident_agi_tactical_chat(
        QueryResidentAgiTacticalChatV1(
            workspace=command.workspace,
            message=command.message,
            decision_type=command.decision_type,
            run_id=command.run_id,
            task_id=command.task_id,
            goal_id=command.goal_id,
            context=command.context,
            context_refs=command.context_refs,
            evidence_refs=command.evidence_refs,
            decision_limit=command.decision_limit,
            max_runs=command.max_runs,
        )
    )
    actions_raw = chat.get("suggested_actions")
    actions = actions_raw if isinstance(actions_raw, list) else []
    selected_action = next(
        (
            dict(item)
            for item in actions
            if isinstance(item, dict) and str(item.get("action_id") or "").strip() == command.action_id
        ),
        {},
    )
    action_spec = resident_agi_tactical_action_spec(command.action_id)
    action_spec_payload = action_spec.to_catalog_item() if action_spec else None
    action_catalog_raw = chat.get("action_catalog")
    action_catalog = (
        action_catalog_raw if isinstance(action_catalog_raw, dict) else resident_agi_tactical_action_catalog()
    )
    if not selected_action:
        return {
            "schema_version": "resident.agi_tactical_action_result.v1",
            "workspace": command.workspace,
            "action_id": command.action_id,
            "action_spec": action_spec_payload,
            "status": "blocked",
            "reason": "action is not available under current Resident AGI evidence and participation policy",
            "chat": chat,
            "goal": None,
            "decision": None,
            "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                action_id=command.action_id,
                chat=chat,
            ),
            "tool_trace": _resident_agi_tactical_action_tool_trace(
                action_id=command.action_id,
                items=[
                    {
                        "step_id": "resident.agi_tactical_chat.revalidate",
                        "label": "重新读取战术上下文",
                        "mode": "read_only",
                        "status": "available",
                        "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                        "summary": "执行前重新读取 Resident AGI public facts。",
                    },
                    {
                        "step_id": "resident.agi_action.policy_gate",
                        "label": "动作策略门禁",
                        "mode": "policy_gate",
                        "status": "blocked",
                        "contract": "resident.agi_tactical_chat_participation.v1",
                        "summary": "当前 action 未出现在后端建议动作列表中。",
                    },
                ],
            ),
            "receipt": {
                "schema_version": "resident.agi_tactical_action_receipt.v1",
                "status": "BLOCKED",
                "title": "受控动作阻断凭证",
                "summary": "后端重新读取事实源后，当前 action 不可执行。",
            },
            "policy": {
                "advisory_only": True,
                "agi_direct_repair_allowed": False,
                "required_chain": "PM → Chief Engineer → Director → QA",
            },
        }

    evidence_refs_raw = chat.get("evidence_refs")
    chat_evidence_refs = evidence_refs_raw if isinstance(evidence_refs_raw, list) else []
    context_refs_raw = chat.get("context_refs")
    chat_context_refs = context_refs_raw if isinstance(context_refs_raw, list) else []

    if command.action_id == "request_resident_agi_judgement":
        evidence_refs = _merge_non_empty_strings(list(command.evidence_refs), chat_evidence_refs)
        context_refs = _merge_non_empty_strings(list(command.context_refs), chat_context_refs)
        try:
            decision_turn_result = await run_resident_agi_decision_turn(
                RunResidentAgiDecisionTurnCommandV1(
                    workspace=command.workspace,
                    objective=f"Resident AGI tactical-console judgement request: {command.message}",
                    decision_type=command.decision_type,
                    run_id=command.run_id,
                    task_id=command.task_id,
                    goal_id=command.goal_id,
                    evidence={
                        "source": "resident_agi_tactical_console",
                        "action_id": command.action_id,
                        "selected_action_spec": action_spec_payload or {},
                        "selected_action": selected_action,
                        "available_tactical_actions": actions,
                        "tactical_action_catalog": action_catalog,
                        "chat_intent": chat.get("intent"),
                        "chat_status": chat.get("status"),
                        "chat_policy": chat.get("policy"),
                        "chat_facts": chat.get("facts"),
                        "user_context": dict(command.context),
                    },
                    constraints=(
                        "preserve_pm_chief_engineer_director_qa_chain",
                        "do_not_mark_failed_gates_as_passed",
                        "do_not_execute_direct_writes_or_direct_repairs",
                        "use_public_cell_contracts_only",
                    ),
                    candidate_actions=("continue", "block", "request_evidence", "escalate"),
                    context_refs=tuple(context_refs),
                    evidence_refs=tuple(evidence_refs),
                    confidence=0.55,
                    include_audit_pack=True,
                    audit_pack_decision_limit=command.decision_limit,
                )
            )
        except Exception as exc:  # noqa: BLE001 — preserve pre-split broad judgement catch
            logger.exception("execute_resident_agi_tactical_action judgement failed: %s", exc)
            return {
                "schema_version": "resident.agi_tactical_action_result.v1",
                "workspace": command.workspace,
                "action_id": command.action_id,
                "action_spec": action_spec_payload,
                "status": "blocked",
                "reason": f"Resident AGI judgement failed before producing a governed decision: {exc}",
                "chat": chat,
                "goal": None,
                "decision": None,
                "role_result": None,
                "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                    action_id=command.action_id,
                    chat=chat,
                ),
                "tool_trace": _resident_agi_tactical_action_tool_trace(
                    action_id=command.action_id,
                    items=[
                        {
                            "step_id": "resident.agi_tactical_chat.revalidate",
                            "label": "重新读取战术上下文",
                            "mode": "read_only",
                            "status": "available",
                            "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                            "summary": "执行前重新读取 Resident AGI public facts。",
                        },
                        {
                            "step_id": "resident.agi_decision_turn.execute",
                            "label": "AGI 判断回合",
                            "mode": "execute_through_role_runtime",
                            "status": "failed",
                            "contract": "resident.autonomy.public.run_resident_agi_decision_turn",
                            "summary": "角色回合失败，按 fail-closed 返回阻断凭证。",
                        },
                    ],
                ),
                "receipt": {
                    "schema_version": "resident.agi_tactical_action_receipt.v1",
                    "status": "BLOCKED",
                    "title": "AGI 判断阻断凭证",
                    "summary": "Resident AGI 角色回合未能完成；未创建目标、未执行修复、未放行门禁。",
                    "rows": [
                        {"label": "动作", "value": command.action_id},
                        {"label": "边界", "value": "fail_closed"},
                    ],
                },
                "policy": {
                    "advisory_only": True,
                    "agi_direct_repair_allowed": False,
                    "required_chain": "PM → Chief Engineer → Director → QA",
                    "role_runtime_required": True,
                },
            }

        recorded_decision_raw = decision_turn_result.get("recorded_decision")
        recorded_decision = recorded_decision_raw if isinstance(recorded_decision_raw, dict) else None
        decision_raw = decision_turn_result.get("decision")
        decision = decision_raw if isinstance(decision_raw, dict) else {}
        verdict = str(decision.get("verdict") or (recorded_decision or {}).get("verdict") or "unknown")
        return {
            "schema_version": "resident.agi_tactical_action_result.v1",
            "workspace": command.workspace,
            "action_id": command.action_id,
            "action_spec": action_spec_payload,
            "status": "executed",
            "reason": "ran Resident AGI judgement through the shared role runtime contract",
            "chat": chat,
            "goal": None,
            "decision": recorded_decision,
            "role_result": decision_turn_result,
            "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                action_id=command.action_id,
                chat=chat,
                verdict=verdict,
            ),
            "tool_trace": _resident_agi_tactical_action_tool_trace(
                action_id=command.action_id,
                items=[
                    {
                        "step_id": "resident.agi_tactical_chat.revalidate",
                        "label": "重新读取战术上下文",
                        "mode": "read_only",
                        "status": "available",
                        "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                        "summary": "执行前重新读取 Resident AGI public facts。",
                    },
                    {
                        "step_id": "resident.agi_decision_turn.execute",
                        "label": "AGI 判断回合",
                        "mode": "execute_through_role_runtime",
                        "status": "executed",
                        "contract": "resident.autonomy.public.run_resident_agi_decision_turn",
                        "summary": f"resident_agi 角色回合产出 {verdict} 判断。",
                    },
                    {
                        "step_id": "resident.decision_trace.write",
                        "label": "写入决策轨迹",
                        "mode": "write_through_resident_contract",
                        "status": "recorded" if recorded_decision else "empty",
                        "contract": "resident.decision_trace",
                        "summary": "判断结果已进入 Resident decision trace。",
                    },
                ],
            ),
            "receipt": {
                "schema_version": "resident.agi_tactical_action_receipt.v1",
                "status": "JUDGED",
                "title": "AGI 判断凭证",
                "summary": "已通过 resident_agi 角色回合完成受控判断；未创建目标、未直接修复、未跳过门禁。",
                "rows": [
                    {"label": "结论", "value": verdict},
                    {"label": "决策", "value": str((recorded_decision or {}).get("decision_id") or "not_recorded")},
                    {"label": "动作", "value": command.action_id},
                    {"label": "角色回合", "value": "resident_agi"},
                ],
            },
            "policy": {
                "advisory_only": True,
                "agi_direct_repair_allowed": False,
                "required_chain": "PM → Chief Engineer → Director → QA",
                "role_runtime_required": True,
                "resident_agi_decision_endpoint": "/v2/resident/agi/decide",
            },
        }

    goal_draft_raw = selected_action.get("goal_draft")
    goal_draft = goal_draft_raw if isinstance(goal_draft_raw, dict) else {}
    if command.action_id != "request_director_controlled_repair" or not goal_draft:
        return {
            "schema_version": "resident.agi_tactical_action_result.v1",
            "workspace": command.workspace,
            "action_id": command.action_id,
            "action_spec": action_spec_payload,
            "status": "blocked",
            "reason": "action has no governed Resident goal draft",
            "chat": chat,
            "goal": None,
            "decision": None,
            "follow_up_actions": _resident_agi_tactical_follow_up_actions(
                action_id=command.action_id,
                chat=chat,
            ),
            "tool_trace": _resident_agi_tactical_action_tool_trace(
                action_id=command.action_id,
                items=[
                    {
                        "step_id": "resident.agi_tactical_chat.revalidate",
                        "label": "重新读取战术上下文",
                        "mode": "read_only",
                        "status": "available",
                        "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                        "summary": "执行前重新读取 Resident AGI public facts。",
                    },
                    {
                        "step_id": "resident.goal_draft.policy_gate",
                        "label": "目标草案门禁",
                        "mode": "policy_gate",
                        "status": "blocked",
                        "contract": "resident.agi_tactical_action_result.v1",
                        "summary": "后端未生成受控 Resident goal draft，禁止前端补造。",
                    },
                ],
            ),
            "receipt": {
                "schema_version": "resident.agi_tactical_action_receipt.v1",
                "status": "BLOCKED",
                "title": "受控动作阻断凭证",
                "summary": "缺少后端生成的 Resident goal draft，未执行写入。",
            },
            "policy": {
                "advisory_only": True,
                "agi_direct_repair_allowed": False,
                "required_chain": "PM → Chief Engineer → Director → QA",
            },
        }

    created_goal = create_resident_goal(CreateResidentGoalCommandV1(workspace=command.workspace, payload=goal_draft))
    evidence_refs = chat_evidence_refs
    decision_payload = {
        "actor": "resident_agi",
        "stage": "tactical_console_action",
        "goal_id": str(created_goal.get("goal_id") or ""),
        "task_id": command.task_id,
        "run_id": command.run_id,
        "summary": f"AGI tactical console created a governed repair goal: {created_goal.get('title') or goal_draft.get('title')}",
        "context_refs": list(command.context_refs),
        "evidence_refs": list(evidence_refs),
        "options": [
            {
                "option_id": command.action_id,
                "label": str(selected_action.get("label") or command.action_id),
                "rationale": str(selected_action.get("reason") or ""),
                "strategy_tags": ["resident_agi_tactical_console", "controlled_repair_goal"],
                "estimated_score": float(goal_draft.get("expected_value") or 0.72),
            }
        ],
        "selected_option_id": command.action_id,
        "strategy_tags": [
            "resident_agi_tactical_console",
            "controlled_repair_goal",
            "pm_ce_director_qa_chain",
        ],
        "expected_outcome": {
            "next_state": "resident_goal_pending_governance",
            "required_chain": "PM → Chief Engineer → Director → QA",
            "agi_direct_repair_allowed": False,
        },
        "actual_outcome": {
            "created_goal_id": str(created_goal.get("goal_id") or ""),
            "created_goal_title": str(created_goal.get("title") or goal_draft.get("title") or ""),
            "action_id": command.action_id,
            "goal_draft": dict(goal_draft),
        },
        "verdict": "partial",
        "confidence": float(goal_draft.get("expected_value") or 0.72),
    }
    recorded_decision = record_resident_decision_entry(
        RecordResidentDecisionCommandV1(
            workspace=command.workspace,
            payload=decision_payload,
            action="resident_agi_tactical_action_executed",
            detail={
                "action_id": command.action_id,
                "goal_id": str(created_goal.get("goal_id") or ""),
            },
        )
    )
    return {
        "schema_version": "resident.agi_tactical_action_result.v1",
        "workspace": command.workspace,
        "action_id": command.action_id,
        "action_spec": action_spec_payload,
        "status": "executed",
        "reason": "created governed Resident goal and recorded decision trace",
        "chat": chat,
        "goal": created_goal,
        "decision": recorded_decision,
        "follow_up_actions": _resident_agi_tactical_follow_up_actions(
            action_id=command.action_id,
            chat=chat,
            created_goal=created_goal,
        ),
        "tool_trace": _resident_agi_tactical_action_tool_trace(
            action_id=command.action_id,
            items=[
                {
                    "step_id": "resident.agi_tactical_chat.revalidate",
                    "label": "重新读取战术上下文",
                    "mode": "read_only",
                    "status": "available",
                    "contract": "resident.autonomy.public.query_resident_agi_tactical_chat",
                    "summary": "执行前重新读取 Resident AGI public facts。",
                },
                {
                    "step_id": "resident.goal_governance.commands",
                    "label": "Resident 目标治理",
                    "mode": "write_through_resident_contract",
                    "status": "executed",
                    "contract": "resident.goal_governance.commands",
                    "summary": "已创建待治理目标；没有直接调用 Director 修复。",
                },
                {
                    "step_id": "resident.decision_trace.write",
                    "label": "写入决策轨迹",
                    "mode": "write_through_resident_contract",
                    "status": "recorded",
                    "contract": "resident.decision_trace",
                    "summary": "已记录 AGI 战术动作和治理链路。",
                },
            ],
        ),
        "receipt": {
            "schema_version": "resident.agi_tactical_action_receipt.v1",
            "status": "EXECUTED",
            "title": "受控动作执行凭证",
            "summary": "已通过 Resident public contract 创建目标并写入 decision trace；未直接执行 Director 修复。",
            "rows": [
                {"label": "目标", "value": str(created_goal.get("goal_id") or "")},
                {"label": "决策", "value": str(recorded_decision.get("decision_id") or "")},
                {"label": "动作", "value": command.action_id},
                {"label": "角色链", "value": "PM→CE→Director→QA preserved"},
            ],
        },
        "policy": {
            "advisory_only": True,
            "agi_direct_repair_allowed": False,
            "required_chain": "PM → Chief Engineer → Director → QA",
        },
    }
