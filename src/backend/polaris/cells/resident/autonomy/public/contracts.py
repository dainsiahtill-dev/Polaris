"""Public contracts for `resident.autonomy`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class RunResidentCycleCommandV1:
    workspace: str
    cycle_id: str
    goal: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "goal", _require_non_empty("goal", self.goal))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class RecordResidentEvidenceCommandV1:
    workspace: str
    cycle_id: str
    evidence_kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "evidence_kind", _require_non_empty("evidence_kind", self.evidence_kind))
        payload = _to_dict_copy(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class StartResidentCommandV1:
    workspace: str
    mode: str = "observe"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "mode", str(self.mode or "observe").strip() or "observe")


@dataclass(frozen=True)
class StopResidentCommandV1:
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class RunResidentTickCommandV1:
    workspace: str
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "force", bool(self.force))


@dataclass(frozen=True)
class UpdateResidentIdentityCommandV1:
    workspace: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))


@dataclass(frozen=True)
class CreateResidentGoalCommandV1:
    workspace: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        payload = _to_dict_copy(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class ApproveResidentGoalCommandV1:
    workspace: str
    goal_id: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "goal_id", _require_non_empty("goal_id", self.goal_id))
        object.__setattr__(self, "note", str(self.note or "").strip())


@dataclass(frozen=True)
class RejectResidentGoalCommandV1:
    workspace: str
    goal_id: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "goal_id", _require_non_empty("goal_id", self.goal_id))
        object.__setattr__(self, "note", str(self.note or "").strip())


@dataclass(frozen=True)
class RecordResidentDecisionCommandV1:
    workspace: str
    payload: Mapping[str, Any]
    action: str = "decision_recorded"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        payload = _to_dict_copy(self.payload)
        if not payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "action", str(self.action or "decision_recorded").strip() or "decision_recorded")
        object.__setattr__(self, "detail", _to_dict_copy(self.detail))


@dataclass(frozen=True)
class ExtractResidentSkillsCommandV1:
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class RunResidentExperimentsCommandV1:
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class RunResidentImprovementsCommandV1:
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class QueryResidentStatusV1:
    workspace: str
    cycle_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.cycle_id is not None:
            object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))


@dataclass(frozen=True)
class QueryResidentCapabilitiesV1:
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class QueryResidentAgiAuditPackV1:
    workspace: str
    decision_limit: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "decision_limit", max(1, min(int(self.decision_limit), 100)))


@dataclass(frozen=True)
class QueryResidentAgiEvidenceInterfacesV1:
    workspace: str
    decision_type: str = "platform_supervision"
    interface_ids: tuple[str, ...] = field(default_factory=tuple)
    run_id: str = ""
    task_id: str = ""
    decision_limit: int = 20
    max_runs: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "decision_type",
            str(self.decision_type or "platform_supervision").strip() or "platform_supervision",
        )
        object.__setattr__(
            self,
            "interface_ids",
            tuple(str(item or "").strip() for item in self.interface_ids if str(item or "").strip()),
        )
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "decision_limit", max(1, min(int(self.decision_limit), 100)))
        object.__setattr__(self, "max_runs", max(1, min(int(self.max_runs), 100)))


@dataclass(frozen=True)
class RunResidentAgiDecisionTurnCommandV1:
    workspace: str
    objective: str
    decision_type: str = "platform_supervision"
    run_id: str = ""
    task_id: str = ""
    goal_id: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    candidate_actions: tuple[str, ...] = field(default_factory=tuple)
    context_refs: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    include_audit_pack: bool = True
    audit_pack_decision_limit: int = 12

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(
            self,
            "decision_type",
            str(self.decision_type or "platform_supervision").strip() or "platform_supervision",
        )
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "goal_id", str(self.goal_id or "").strip())
        object.__setattr__(self, "evidence", _to_dict_copy(self.evidence))
        object.__setattr__(
            self,
            "constraints",
            tuple(str(item or "").strip() for item in self.constraints if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "candidate_actions",
            tuple(str(item or "").strip() for item in self.candidate_actions if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "context_refs",
            tuple(str(item or "").strip() for item in self.context_refs if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(str(item or "").strip() for item in self.evidence_refs if str(item or "").strip()),
        )
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(self.confidence or 0.0))))
        object.__setattr__(self, "include_audit_pack", bool(self.include_audit_pack))
        object.__setattr__(self, "audit_pack_decision_limit", max(1, min(int(self.audit_pack_decision_limit), 100)))


@dataclass(frozen=True)
class MaterializeResidentGoalCommandV1:
    workspace: str
    goal_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "goal_id", _require_non_empty("goal_id", self.goal_id))


@dataclass(frozen=True)
class StageResidentGoalCommandV1:
    workspace: str
    goal_id: str
    promote_to_pm_runtime: bool = False
    ramdisk_root: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "goal_id", _require_non_empty("goal_id", self.goal_id))
        object.__setattr__(self, "promote_to_pm_runtime", bool(self.promote_to_pm_runtime))
        object.__setattr__(self, "ramdisk_root", str(self.ramdisk_root or "").strip())


@dataclass(frozen=True)
class RunResidentGoalCommandV1:
    workspace: str
    goal_id: str
    settings: Any | None = None
    run_type: str = "pm"
    run_director: bool = False
    director_iterations: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "goal_id", _require_non_empty("goal_id", self.goal_id))
        object.__setattr__(self, "run_type", str(self.run_type or "pm").strip() or "pm")
        object.__setattr__(self, "run_director", bool(self.run_director))
        object.__setattr__(self, "director_iterations", max(1, min(int(self.director_iterations), 10)))


@dataclass(frozen=True)
class ResidentAgiCapabilityV1:
    capability_id: str
    name: str
    category: str
    access: str
    purpose: str
    contract_ref: str
    endpoint: str = ""
    risk_level: str = "low"
    guardrails: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _require_non_empty("capability_id", self.capability_id))
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        object.__setattr__(self, "category", _require_non_empty("category", self.category))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "purpose", _require_non_empty("purpose", self.purpose))
        object.__setattr__(self, "contract_ref", _require_non_empty("contract_ref", self.contract_ref))
        object.__setattr__(self, "endpoint", str(self.endpoint or "").strip())
        object.__setattr__(self, "risk_level", str(self.risk_level or "low").strip() or "low")
        object.__setattr__(
            self,
            "guardrails",
            tuple(str(v).strip() for v in self.guardrails if str(v).strip()),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(str(v).strip() for v in self.evidence_refs if str(v).strip()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "category": self.category,
            "access": self.access,
            "purpose": self.purpose,
            "contract_ref": self.contract_ref,
            "endpoint": self.endpoint,
            "risk_level": self.risk_level,
            "guardrails": list(self.guardrails),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class ResidentAgiDecisionCapabilityV1:
    decision_id: str
    name: str
    owner: str
    decision_scope: str
    risk_level: str
    required_evidence_interfaces: tuple[str, ...] = field(default_factory=tuple)
    optional_evidence_interfaces: tuple[str, ...] = field(default_factory=tuple)
    candidate_actions: tuple[str, ...] = field(default_factory=tuple)
    hard_constraints: tuple[str, ...] = field(default_factory=tuple)
    escalation: str = ""
    output_contract: str = "resident.agi_decision_turn"
    contract_refs: tuple[str, ...] = field(default_factory=tuple)
    llm_decision_required: bool = True
    platform_enforced: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_non_empty("decision_id", self.decision_id))
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "decision_scope", _require_non_empty("decision_scope", self.decision_scope))
        object.__setattr__(self, "risk_level", str(self.risk_level or "medium").strip() or "medium")
        object.__setattr__(
            self,
            "required_evidence_interfaces",
            tuple(str(v).strip() for v in self.required_evidence_interfaces if str(v).strip()),
        )
        object.__setattr__(
            self,
            "optional_evidence_interfaces",
            tuple(str(v).strip() for v in self.optional_evidence_interfaces if str(v).strip()),
        )
        object.__setattr__(
            self,
            "candidate_actions",
            tuple(str(v).strip() for v in self.candidate_actions if str(v).strip()),
        )
        object.__setattr__(
            self,
            "hard_constraints",
            tuple(str(v).strip() for v in self.hard_constraints if str(v).strip()),
        )
        object.__setattr__(self, "escalation", str(self.escalation or "").strip())
        object.__setattr__(
            self,
            "output_contract",
            str(self.output_contract or "resident.agi_decision_turn").strip() or "resident.agi_decision_turn",
        )
        object.__setattr__(
            self,
            "contract_refs",
            tuple(str(v).strip() for v in self.contract_refs if str(v).strip()),
        )
        object.__setattr__(self, "llm_decision_required", bool(self.llm_decision_required))
        object.__setattr__(self, "platform_enforced", bool(self.platform_enforced))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "name": self.name,
            "owner": self.owner,
            "decision_scope": self.decision_scope,
            "risk_level": self.risk_level,
            "required_evidence_interfaces": list(self.required_evidence_interfaces),
            "optional_evidence_interfaces": list(self.optional_evidence_interfaces),
            "candidate_actions": list(self.candidate_actions),
            "hard_constraints": list(self.hard_constraints),
            "escalation": self.escalation,
            "output_contract": self.output_contract,
            "contract_refs": list(self.contract_refs),
            "llm_decision_required": self.llm_decision_required,
            "platform_enforced": self.platform_enforced,
        }


@dataclass(frozen=True)
class ResidentAgiDecisionBoundaryV1:
    boundary_id: str
    name: str
    authority: str
    platform_hard_rule: str
    agi_decision_scope: str
    evidence_required: tuple[str, ...] = field(default_factory=tuple)
    escalation: str = ""
    contract_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "boundary_id", _require_non_empty("boundary_id", self.boundary_id))
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        object.__setattr__(self, "authority", _require_non_empty("authority", self.authority))
        object.__setattr__(
            self,
            "platform_hard_rule",
            _require_non_empty("platform_hard_rule", self.platform_hard_rule),
        )
        object.__setattr__(
            self,
            "agi_decision_scope",
            _require_non_empty("agi_decision_scope", self.agi_decision_scope),
        )
        object.__setattr__(
            self,
            "evidence_required",
            tuple(str(v).strip() for v in self.evidence_required if str(v).strip()),
        )
        object.__setattr__(self, "escalation", str(self.escalation or "").strip())
        object.__setattr__(
            self,
            "contract_refs",
            tuple(str(v).strip() for v in self.contract_refs if str(v).strip()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "name": self.name,
            "authority": self.authority,
            "platform_hard_rule": self.platform_hard_rule,
            "agi_decision_scope": self.agi_decision_scope,
            "evidence_required": list(self.evidence_required),
            "escalation": self.escalation,
            "contract_refs": list(self.contract_refs),
        }


@dataclass(frozen=True)
class ResidentCycleCompletedEventV1:
    event_id: str
    workspace: str
    cycle_id: str
    status: str
    completed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class ResidentAutonomyResultV1:
    ok: bool
    workspace: str
    cycle_id: str
    status: str
    actions: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "cycle_id", _require_non_empty("cycle_id", self.cycle_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "actions", tuple(str(v) for v in self.actions if str(v).strip()))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(str(v) for v in self.evidence_refs if str(v).strip()),
        )
        object.__setattr__(self, "metrics", _to_dict_copy(self.metrics))


class ResidentAutonomyError(RuntimeError):
    """Raised when `resident.autonomy` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "resident_autonomy_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "ApproveResidentGoalCommandV1",
    "CreateResidentGoalCommandV1",
    "ExtractResidentSkillsCommandV1",
    "MaterializeResidentGoalCommandV1",
    "QueryResidentAgiAuditPackV1",
    "QueryResidentCapabilitiesV1",
    "QueryResidentStatusV1",
    "RecordResidentDecisionCommandV1",
    "RecordResidentEvidenceCommandV1",
    "RejectResidentGoalCommandV1",
    "ResidentAgiCapabilityV1",
    "ResidentAgiDecisionBoundaryV1",
    "ResidentAgiDecisionCapabilityV1",
    "ResidentAutonomyError",
    "ResidentAutonomyResultV1",
    "ResidentCycleCompletedEventV1",
    "RunResidentAgiDecisionTurnCommandV1",
    "RunResidentCycleCommandV1",
    "RunResidentExperimentsCommandV1",
    "RunResidentGoalCommandV1",
    "RunResidentImprovementsCommandV1",
    "RunResidentTickCommandV1",
    "StageResidentGoalCommandV1",
    "StartResidentCommandV1",
    "StopResidentCommandV1",
    "UpdateResidentIdentityCommandV1",
]
