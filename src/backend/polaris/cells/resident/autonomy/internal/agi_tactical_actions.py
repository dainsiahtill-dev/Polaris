"""Resident AGI tactical-console action registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResidentAgiTacticalActionSpec:
    """Declarative contract for one Resident AGI tactical-console action."""

    action_id: str
    label: str
    mode: str
    default_status: str
    reason: str
    ui_handler: str
    capability_id: str
    contract_ref: str
    risk_level: str = "low"
    endpoint: str = ""
    execution_boundary: str = "read_first_controlled_actions"
    requires_participation: bool = False

    def to_payload(
        self,
        *,
        status: str | None = None,
        reason: str | None = None,
        goal_draft: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the serializable action payload consumed by the UI."""

        payload: dict[str, Any] = {
            "action_id": self.action_id,
            "label": self.label,
            "mode": self.mode,
            "status": status or self.default_status,
            "reason": reason or self.reason,
            "endpoint": self.endpoint,
            "ui_handler": self.ui_handler,
            "capability_id": self.capability_id,
            "contract_ref": self.contract_ref,
            "risk_level": self.risk_level,
            "execution_boundary": self.execution_boundary,
            "requires_participation": self.requires_participation,
            "authoritative": False,
            "agi_direct_execution_allowed": False,
        }
        if goal_draft:
            payload["goal_draft"] = goal_draft
        return payload

    def to_catalog_item(self) -> dict[str, Any]:
        """Return the read-only catalog representation."""

        return {
            "action_id": self.action_id,
            "label": self.label,
            "mode": self.mode,
            "default_status": self.default_status,
            "reason": self.reason,
            "endpoint": self.endpoint,
            "ui_handler": self.ui_handler,
            "capability_id": self.capability_id,
            "contract_ref": self.contract_ref,
            "risk_level": self.risk_level,
            "execution_boundary": self.execution_boundary,
            "requires_participation": self.requires_participation,
            "authoritative": False,
            "agi_direct_execution_allowed": False,
        }


_ACTION_SPECS: tuple[ResidentAgiTacticalActionSpec, ...] = (
    ResidentAgiTacticalActionSpec(
        action_id="open_evidence_black_box",
        label="查看证据黑匣子",
        mode="local_read_model",
        default_status="available",
        reason="展示已读取的 audit pack、evidence interfaces 和 repair advisory projection。",
        endpoint="",
        ui_handler="open_advanced_audit",
        capability_id="audit.evidence.bundle.read",
        contract_ref="resident.agi_tactical_console.local_evidence_view",
    ),
    ResidentAgiTacticalActionSpec(
        action_id="refresh_evidence_interfaces",
        label="刷新证据接口",
        mode="read_only",
        default_status="available",
        reason="只刷新 Resident AGI evidence interface read model，不改变项目状态。",
        endpoint="/v2/resident/agi/evidence-interfaces",
        ui_handler="refresh_evidence_interfaces",
        capability_id="audit.evidence_interface_selection",
        contract_ref="resident.autonomy.public.query_resident_agi_evidence_interfaces",
    ),
    ResidentAgiTacticalActionSpec(
        action_id="open_operator_settings",
        label="打开值守设定",
        mode="local_navigation",
        default_status="available",
        reason="打开常驻 AGI 参与范围设置；该动作只展开 UI，不自动开启 AGI 或修改权限。",
        endpoint="",
        ui_handler="open_operator_settings",
        capability_id="resident.agi_participation_policy.read",
        contract_ref="resident.workspace.local_operator_settings",
    ),
    ResidentAgiTacticalActionSpec(
        action_id="request_resident_agi_judgement",
        label="请求 AGI 判断",
        mode="execute_through_role_runtime",
        default_status="preview_only",
        reason=("通过 resident_agi 角色回合执行一次受控判断；只产出决策和证据，不直接写项目、不放行失败门禁。"),
        endpoint="/v2/resident/agi/actions/execute",
        ui_handler="execute_governed_action",
        capability_id="resident.agi_decision_turn.execute",
        contract_ref="resident.autonomy.public.run_resident_agi_decision_turn",
        risk_level="medium",
        execution_boundary="execute_through_role_runtime_only",
        requires_participation=True,
    ),
    ResidentAgiTacticalActionSpec(
        action_id="request_director_controlled_repair",
        label="请求 Director 受控修复",
        mode="controlled_execution",
        default_status="preview_only",
        reason=("AGI 聊天不能直接写文件或绕过 PM → Chief Engineer → Director → QA，只能建议进入受控链路。"),
        endpoint="/v2/resident/goals",
        ui_handler="execute_governed_action",
        capability_id="resident.goal_governance.commands",
        contract_ref="resident.goal_governance.commands",
        risk_level="high",
        execution_boundary="write_through_resident_goal_governance_only",
        requires_participation=True,
    ),
    ResidentAgiTacticalActionSpec(
        action_id="open_goals_tab",
        label="查看治理目标",
        mode="local_navigation",
        default_status="available",
        reason="打开 Resident 目标队列，查看刚创建的受控目标。",
        endpoint="",
        ui_handler="open_goals_tab",
        capability_id="resident.goal_governance.read",
        contract_ref="resident.workspace.local_navigation",
    ),
)


def resident_agi_tactical_action_specs() -> tuple[ResidentAgiTacticalActionSpec, ...]:
    """Return the canonical Resident AGI tactical-console action specs."""

    return _ACTION_SPECS


def resident_agi_tactical_action_spec(action_id: str) -> ResidentAgiTacticalActionSpec | None:
    """Return one action spec by id."""

    token = str(action_id or "").strip()
    for spec in _ACTION_SPECS:
        if spec.action_id == token:
            return spec
    return None


def resident_agi_tactical_action_payload(
    action_id: str,
    *,
    status: str | None = None,
    reason: str | None = None,
    goal_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a serializable action payload from the canonical registry."""

    spec = resident_agi_tactical_action_spec(action_id)
    if spec is None:
        return {}
    return spec.to_payload(status=status, reason=reason, goal_draft=goal_draft)


def resident_agi_tactical_action_catalog() -> dict[str, Any]:
    """Return the read-only action catalog exposed to chat and UI projections."""

    items = [spec.to_catalog_item() for spec in _ACTION_SPECS]
    return {
        "schema_version": "resident.agi_tactical_action_catalog.v1",
        "source": "resident.autonomy.internal.agi_tactical_actions",
        "items": items,
        "summary": {
            "total": len(items),
            "read_only": sum(1 for item in items if item["mode"] in {"read_only", "local_read_model"}),
            "controlled": sum(1 for item in items if item["ui_handler"] == "execute_governed_action"),
            "requires_participation": sum(1 for item in items if item["requires_participation"]),
            "agi_direct_execution_allowed": False,
            "authoritative_actions": 0,
            "required_chain": "PM → Chief Engineer → Director → QA",
        },
    }
