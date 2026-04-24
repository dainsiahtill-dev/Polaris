"""Resident data models for domain layer."""

from __future__ import annotations

__all__ = [
    "CapabilityGraphSnapshot",
    "CapabilityNode",
    "DecisionOption",
    "DecisionRecord",
    "DecisionVerdict",
    "ExperimentRecord",
    "ExperimentStatus",
    "GoalProposal",
    "GoalStatus",
    "GoalType",
    "ImprovementProposal",
    "ImprovementStatus",
    "MetaInsight",
    "ResidentAgenda",
    "ResidentIdentity",
    "ResidentMode",
    "ResidentRuntimeState",
    "SkillArtifact",
    "SkillProposal",
    "SkillProposalStatus",
    "coerce_float",
    "coerce_mapping",
    "coerce_str_list",
    "new_id",
    "utc_now_iso",
]

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    token = str(prefix or "resident").strip().lower().replace(" ", "_") or "resident"
    return f"{token}-{uuid4().hex[:12]}"


def coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        items = []
    result: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if token:
            result.append(token)
    return result


def coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def coerce_float(
    value: Any,
    *,
    default: float = 0.0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if value is None:
        return float(default)
    try:
        parsed = float(value)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("coerce_float failed for value=%r: %s", value, e, exc_info=True)
        parsed = float(default)
    if minimum is not None:
        parsed = max(parsed, float(minimum))
    if maximum is not None:
        parsed = min(parsed, float(maximum))
    return parsed


def _jsonify(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        # Handle both instances and types
        if isinstance(value, type):
            instance = value()
            return {str(key): _jsonify(item) for key, item in asdict(instance).items()}
        return {str(key): _jsonify(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    return value


class ResidentMode(str, Enum):
    OBSERVE = "observe"
    PROPOSE = "propose"
    ASSIST = "assist"
    BOUNDED_AUTO = "bounded_auto"
    LAB_ONLY = "lab_only"
    DORMANT = "dormant"


class GoalType(str, Enum):
    MAINTENANCE = "maintenance"
    RELIABILITY = "reliability"
    KNOWLEDGE = "knowledge"
    CAPABILITY = "capability"


class GoalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MATERIALIZED = "materialized"
    ARCHIVED = "archived"


class DecisionVerdict(str, Enum):
    UNKNOWN = "unknown"
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class ExperimentStatus(str, Enum):
    SIMULATED = "simulated"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class ImprovementStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROMOTED = "promoted"


@dataclass
class DecisionOption:
    option_id: str = field(default_factory=lambda: new_id("option"))
    label: str = ""
    rationale: str = ""
    strategy_tags: list[str] = field(default_factory=list)
    estimated_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionOption:
        return cls(
            option_id=str(data.get("option_id") or new_id("option")).strip(),
            label=str(data.get("label") or "").strip(),
            rationale=str(data.get("rationale") or "").strip(),
            strategy_tags=coerce_str_list(data.get("strategy_tags") or []),
            estimated_score=coerce_float(data.get("estimated_score"), default=0.0),
        )


@dataclass
class ResidentIdentity:
    resident_id: str = field(default_factory=lambda: new_id("resident"))
    name: str = "Resident Engineer"
    mission: str = "Sustain Polaris as a governed, evidence-first, continuously improving engineering agent."
    owner: str = "human"
    active_workspace: str = ""
    operating_mode: ResidentMode = ResidentMode.OBSERVE
    values: list[str] = field(
        default_factory=lambda: [
            "evidence_over_claims",
            "contract_first",
            "safe_evolution",
            "governed_autonomy",
            "utf8_only",
        ]
    )
    memory_lineage: list[str] = field(default_factory=list)
    capability_profile: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonify(self)
        payload["operating_mode"] = self.operating_mode.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResidentIdentity:
        mode_token = str(data.get("operating_mode") or ResidentMode.OBSERVE.value).strip().lower()
        try:
            mode = ResidentMode(mode_token)
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "ResidentIdentity.from_dict: failed to parse operating_mode %r: %s", mode_token, e, exc_info=True
            )
            mode = ResidentMode.OBSERVE
        profile_raw = data.get("capability_profile")
        capability_profile = (
            {str(key): coerce_float(value, default=0.0, minimum=0.0, maximum=1.0) for key, value in profile_raw.items()}
            if isinstance(profile_raw, Mapping)
            else {}
        )
        default_mission = cls().mission
        return cls(
            resident_id=str(data.get("resident_id") or new_id("resident")).strip(),
            name=str(data.get("name") or "Resident Engineer").strip() or "Resident Engineer",
            mission=str(data.get("mission") or "").strip() or default_mission,
            owner=str(data.get("owner") or "human").strip() or "human",
            active_workspace=str(data.get("active_workspace") or "").strip(),
            operating_mode=mode,
            values=coerce_str_list(data.get("values") or []),
            memory_lineage=coerce_str_list(data.get("memory_lineage") or []),
            capability_profile=capability_profile,
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )


@dataclass
class ResidentAgenda:
    current_focus: list[str] = field(default_factory=list)
    pending_goal_ids: list[str] = field(default_factory=list)
    approved_goal_ids: list[str] = field(default_factory=list)
    materialized_goal_ids: list[str] = field(default_factory=list)
    risk_register: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    active_experiment_ids: list[str] = field(default_factory=list)
    active_improvement_ids: list[str] = field(default_factory=list)
    last_tick_at: str = ""
    tick_count: int = 0
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResidentAgenda:
        return cls(
            current_focus=coerce_str_list(data.get("current_focus") or []),
            pending_goal_ids=coerce_str_list(data.get("pending_goal_ids") or []),
            approved_goal_ids=coerce_str_list(data.get("approved_goal_ids") or []),
            materialized_goal_ids=coerce_str_list(data.get("materialized_goal_ids") or []),
            risk_register=coerce_str_list(data.get("risk_register") or []),
            next_actions=coerce_str_list(data.get("next_actions") or []),
            active_experiment_ids=coerce_str_list(data.get("active_experiment_ids") or []),
            active_improvement_ids=coerce_str_list(data.get("active_improvement_ids") or []),
            last_tick_at=str(data.get("last_tick_at") or "").strip(),
            tick_count=max(0, int(data.get("tick_count") or 0)),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )


@dataclass
class GoalProposal:
    goal_id: str = field(default_factory=lambda: new_id("goal"))
    goal_type: GoalType = GoalType.MAINTENANCE
    title: str = ""
    motivation: str = ""
    source: str = ""
    expected_value: float = 0.0
    risk_score: float = 0.0
    scope: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    status: GoalStatus = GoalStatus.PENDING
    approval_note: str = ""
    fingerprint: str = ""
    derived_from: list[str] = field(default_factory=list)
    pm_contract_outline: dict[str, Any] = field(default_factory=dict)
    materialization_artifacts: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonify(self)
        payload["goal_type"] = self.goal_type.value
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoalProposal:
        type_token = str(data.get("goal_type") or GoalType.MAINTENANCE.value).strip().lower()
        status_token = str(data.get("status") or GoalStatus.PENDING.value).strip().lower()
        try:
            goal_type = GoalType(type_token)
        except (RuntimeError, ValueError) as e:
            logger.warning("GoalProposal.from_dict: failed to parse goal_type %r: %s", type_token, e, exc_info=True)
            goal_type = GoalType.MAINTENANCE
        try:
            status = GoalStatus(status_token)
        except (RuntimeError, ValueError) as e:
            logger.warning("GoalProposal.from_dict: failed to parse status %r: %s", status_token, e, exc_info=True)
            status = GoalStatus.PENDING
        return cls(
            goal_id=str(data.get("goal_id") or new_id("goal")).strip(),
            goal_type=goal_type,
            title=str(data.get("title") or "").strip(),
            motivation=str(data.get("motivation") or "").strip(),
            source=str(data.get("source") or "").strip(),
            expected_value=coerce_float(data.get("expected_value"), default=0.0, minimum=0.0, maximum=1.0),
            risk_score=coerce_float(data.get("risk_score"), default=0.0, minimum=0.0, maximum=1.0),
            scope=coerce_str_list(data.get("scope") or []),
            budget=coerce_mapping(data.get("budget") or {}),
            evidence_refs=coerce_str_list(data.get("evidence_refs") or []),
            status=status,
            approval_note=str(data.get("approval_note") or "").strip(),
            fingerprint=str(data.get("fingerprint") or "").strip(),
            derived_from=coerce_str_list(data.get("derived_from") or []),
            pm_contract_outline=coerce_mapping(data.get("pm_contract_outline") or {}),
            materialization_artifacts=coerce_mapping(data.get("materialization_artifacts") or {}),
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )


@dataclass
class DecisionRecord:
    decision_id: str = field(default_factory=lambda: new_id("decision"))
    workspace: str = ""
    timestamp: str = field(default_factory=utc_now_iso)
    run_id: str = ""
    actor: str = ""
    stage: str = ""
    goal_id: str = ""
    task_id: str = ""
    summary: str = ""
    context_refs: list[str] = field(default_factory=list)
    options: list[DecisionOption] = field(default_factory=list)
    selected_option_id: str = ""
    strategy_tags: list[str] = field(default_factory=list)
    expected_outcome: dict[str, Any] = field(default_factory=dict)
    actual_outcome: dict[str, Any] = field(default_factory=dict)
    verdict: DecisionVerdict = DecisionVerdict.UNKNOWN
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0

    # Phase 1.1: 证据追溯增强字段
    # 关联到 EvidenceBundle（统一证据包）
    evidence_bundle_id: str | None = None
    # 父决策ID（形成决策链）
    parent_decision_id: str | None = None
    # 影响的文件列表（便于从文件查决策）
    affected_files: list[str] = field(default_factory=list)
    # 影响的符号（函数/类名）
    affected_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonify(self)
        payload["verdict"] = self.verdict.value
        payload["options"] = [item.to_dict() for item in self.options]
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionRecord:
        verdict_token = str(data.get("verdict") or DecisionVerdict.UNKNOWN.value).strip().lower()
        try:
            verdict = DecisionVerdict(verdict_token)
        except (RuntimeError, ValueError) as e:
            logger.warning("DecisionRecord.from_dict: failed to parse verdict %r: %s", verdict_token, e, exc_info=True)
            verdict = DecisionVerdict.UNKNOWN
        raw_options_val = data.get("options")
        raw_options = raw_options_val if isinstance(raw_options_val, list) else []
        options = [DecisionOption.from_dict(item) for item in raw_options if isinstance(item, Mapping)]
        # Phase 1.1: 新增字段的反序列化
        evidence_bundle_id = data.get("evidence_bundle_id")
        if evidence_bundle_id is not None:
            evidence_bundle_id = str(evidence_bundle_id).strip() or None

        parent_decision_id = data.get("parent_decision_id")
        if parent_decision_id is not None:
            parent_decision_id = str(parent_decision_id).strip() or None

        return cls(
            decision_id=str(data.get("decision_id") or new_id("decision")).strip(),
            workspace=str(data.get("workspace") or "").strip(),
            timestamp=str(data.get("timestamp") or utc_now_iso()).strip(),
            run_id=str(data.get("run_id") or "").strip(),
            actor=str(data.get("actor") or "").strip(),
            stage=str(data.get("stage") or "").strip(),
            goal_id=str(data.get("goal_id") or "").strip(),
            task_id=str(data.get("task_id") or "").strip(),
            summary=str(data.get("summary") or "").strip(),
            context_refs=coerce_str_list(data.get("context_refs") or []),
            options=options,
            selected_option_id=str(data.get("selected_option_id") or "").strip(),
            strategy_tags=coerce_str_list(data.get("strategy_tags") or []),
            expected_outcome=coerce_mapping(data.get("expected_outcome") or {}),
            actual_outcome=coerce_mapping(data.get("actual_outcome") or {}),
            verdict=verdict,
            evidence_refs=coerce_str_list(data.get("evidence_refs") or []),
            confidence=coerce_float(data.get("confidence"), default=0.0, minimum=0.0, maximum=1.0),
            # Phase 1.1: 新增字段
            evidence_bundle_id=evidence_bundle_id,
            parent_decision_id=parent_decision_id,
            affected_files=coerce_str_list(data.get("affected_files") or []),
            affected_symbols=coerce_str_list(data.get("affected_symbols") or []),
        )


@dataclass
class MetaInsight:
    insight_id: str = field(default_factory=lambda: new_id("insight"))
    insight_type: str = ""
    strategy_tag: str = ""
    summary: str = ""
    recommendation: str = ""
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MetaInsight:
        return cls(
            insight_id=str(data.get("insight_id") or new_id("insight")).strip(),
            insight_type=str(data.get("insight_type") or "").strip(),
            strategy_tag=str(data.get("strategy_tag") or "").strip(),
            summary=str(data.get("summary") or "").strip(),
            recommendation=str(data.get("recommendation") or "").strip(),
            confidence=coerce_float(data.get("confidence"), default=0.0, minimum=0.0, maximum=1.0),
            evidence_refs=coerce_str_list(data.get("evidence_refs") or []),
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
        )


@dataclass
class SkillArtifact:
    skill_id: str = field(default_factory=lambda: new_id("skill"))
    name: str = ""
    trigger: str = ""
    preconditions: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    version: int = 1
    source_decision_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SkillArtifact:
        return cls(
            skill_id=str(data.get("skill_id") or new_id("skill")).strip(),
            name=str(data.get("name") or "").strip(),
            trigger=str(data.get("trigger") or "").strip(),
            preconditions=coerce_str_list(data.get("preconditions") or []),
            steps=coerce_str_list(data.get("steps") or []),
            evidence_refs=coerce_str_list(data.get("evidence_refs") or []),
            failure_modes=coerce_str_list(data.get("failure_modes") or []),
            confidence=coerce_float(data.get("confidence"), default=0.0, minimum=0.0, maximum=1.0),
            version=max(1, int(data.get("version") or 1)),
            source_decision_ids=coerce_str_list(data.get("source_decision_ids") or []),
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )


class SkillProposalStatus(str, Enum):
    """技能提案状态"""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"


@dataclass
class SkillProposal:
    """技能提案 - 等待人工确认的技能提取"""

    proposal_id: str = field(default_factory=lambda: new_id("proposal"))
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    # 提案内容
    name: str = ""
    description: str = ""
    pattern: str = ""  # 模式代码/模板
    context_type: str = ""  # 适用上下文类型

    # 来源追踪
    extracted_from: list[str] = field(default_factory=list)  # 关联的 decision_ids
    evidence_bundle_ids: list[str] = field(default_factory=list)  # 关联的证据包

    # 统计置信度
    confidence: float = 0.0  # 0.0 - 1.0
    occurrence_count: int = 0  # 出现次数

    # 状态
    status: str = field(default=SkillProposalStatus.PENDING_REVIEW.value)

    # 审查记录
    reviewed_at: str | None = None
    reviewed_by: str | None = None  # reviewer identifier
    review_note: str | None = None

    # 关联的技能（批准后）
    skill_id: str | None = None  # 关联的 SkillArtifact ID

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SkillProposal:
        return cls(
            proposal_id=str(data.get("proposal_id") or new_id("proposal")).strip(),
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
            name=str(data.get("name") or "").strip(),
            description=str(data.get("description") or "").strip(),
            pattern=str(data.get("pattern") or "").strip(),
            context_type=str(data.get("context_type") or "").strip(),
            extracted_from=coerce_str_list(data.get("extracted_from") or []),
            evidence_bundle_ids=coerce_str_list(data.get("evidence_bundle_ids") or []),
            confidence=coerce_float(data.get("confidence"), default=0.0, minimum=0.0, maximum=1.0),
            occurrence_count=max(0, int(data.get("occurrence_count") or 0)),
            status=str(data.get("status") or SkillProposalStatus.PENDING_REVIEW.value).strip(),
            reviewed_at=str(data.get("reviewed_at") or "").strip() or None,
            reviewed_by=str(data.get("reviewed_by") or "").strip() or None,
            review_note=str(data.get("review_note") or "").strip() or None,
            skill_id=str(data.get("skill_id") or "").strip() or None,
        )


@dataclass
class CapabilityNode:
    capability_id: str = field(default_factory=lambda: new_id("capability"))
    name: str = ""
    kind: str = ""
    score: float = 0.0
    success_rate: float = 0.0
    attempts: int = 0
    evidence_count: int = 0
    supporting_skill_ids: list[str] = field(default_factory=list)
    supporting_strategy_tags: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityNode:
        return cls(
            capability_id=str(data.get("capability_id") or new_id("capability")).strip(),
            name=str(data.get("name") or "").strip(),
            kind=str(data.get("kind") or "").strip(),
            score=coerce_float(data.get("score"), default=0.0, minimum=0.0, maximum=1.0),
            success_rate=coerce_float(data.get("success_rate"), default=0.0, minimum=0.0, maximum=1.0),
            attempts=max(0, int(data.get("attempts") or 0)),
            evidence_count=max(0, int(data.get("evidence_count") or 0)),
            supporting_skill_ids=coerce_str_list(data.get("supporting_skill_ids") or []),
            supporting_strategy_tags=coerce_str_list(data.get("supporting_strategy_tags") or []),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )


@dataclass
class CapabilityGraphSnapshot:
    generated_at: str = field(default_factory=utc_now_iso)
    capabilities: list[CapabilityNode] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "gaps": list(self.gaps),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityGraphSnapshot:
        raw_capabilities_val = data.get("capabilities")
        raw_capabilities = raw_capabilities_val if isinstance(raw_capabilities_val, list) else []
        capabilities = [CapabilityNode.from_dict(item) for item in raw_capabilities if isinstance(item, Mapping)]
        return cls(
            generated_at=str(data.get("generated_at") or utc_now_iso()).strip(),
            capabilities=capabilities,
            gaps=coerce_str_list(data.get("gaps") or []),
        )


@dataclass
class ExperimentRecord:
    experiment_id: str = field(default_factory=lambda: new_id("experiment"))
    source_decision_id: str = ""
    baseline_strategy: str = ""
    counterfactual_strategy: str = ""
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    recommendation: str = ""
    rollback_plan: str = ""
    status: ExperimentStatus = ExperimentStatus.SIMULATED
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonify(self)
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperimentRecord:
        status_token = str(data.get("status") or ExperimentStatus.SIMULATED.value).strip().lower()
        try:
            status = ExperimentStatus(status_token)
        except (RuntimeError, ValueError) as e:
            logger.warning("ExperimentRecord.from_dict: failed to parse status %r: %s", status_token, e, exc_info=True)
            status = ExperimentStatus.SIMULATED
        return cls(
            experiment_id=str(data.get("experiment_id") or new_id("experiment")).strip(),
            source_decision_id=str(data.get("source_decision_id") or "").strip(),
            baseline_strategy=str(data.get("baseline_strategy") or "").strip(),
            counterfactual_strategy=str(data.get("counterfactual_strategy") or "").strip(),
            metrics_before=coerce_mapping(data.get("metrics_before") or {}),
            metrics_after=coerce_mapping(data.get("metrics_after") or {}),
            confidence=coerce_float(data.get("confidence"), default=0.0, minimum=0.0, maximum=1.0),
            recommendation=str(data.get("recommendation") or "").strip(),
            rollback_plan=str(data.get("rollback_plan") or "").strip(),
            status=status,
            evidence_refs=coerce_str_list(data.get("evidence_refs") or []),
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
        )


@dataclass
class ImprovementProposal:
    improvement_id: str = field(default_factory=lambda: new_id("improvement"))
    category: str = ""
    title: str = ""
    description: str = ""
    target_surface: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    experiment_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    rollback_plan: str = ""
    status: ImprovementStatus = ImprovementStatus.PROPOSED
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonify(self)
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ImprovementProposal:
        status_token = str(data.get("status") or ImprovementStatus.PROPOSED.value).strip().lower()
        try:
            status = ImprovementStatus(status_token)
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "ImprovementProposal.from_dict: failed to parse status %r: %s", status_token, e, exc_info=True
            )
            status = ImprovementStatus.PROPOSED
        return cls(
            improvement_id=str(data.get("improvement_id") or new_id("improvement")).strip(),
            category=str(data.get("category") or "").strip(),
            title=str(data.get("title") or "").strip(),
            description=str(data.get("description") or "").strip(),
            target_surface=str(data.get("target_surface") or "").strip(),
            evidence_refs=coerce_str_list(data.get("evidence_refs") or []),
            experiment_ids=coerce_str_list(data.get("experiment_ids") or []),
            confidence=coerce_float(data.get("confidence"), default=0.0, minimum=0.0, maximum=1.0),
            rollback_plan=str(data.get("rollback_plan") or "").strip(),
            status=status,
            created_at=str(data.get("created_at") or utc_now_iso()).strip(),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )


@dataclass
class ResidentRuntimeState:
    active: bool = True
    mode: ResidentMode = ResidentMode.OBSERVE
    last_tick_at: str = ""
    tick_count: int = 0
    last_error: str = ""
    last_summary: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = _jsonify(self)
        payload["mode"] = self.mode.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResidentRuntimeState:
        mode_token = str(data.get("mode") or ResidentMode.OBSERVE.value).strip().lower()
        try:
            mode = ResidentMode(mode_token)
        except (RuntimeError, ValueError) as e:
            logger.warning("ResidentRuntimeState.from_dict: failed to parse mode %r: %s", mode_token, e, exc_info=True)
            mode = ResidentMode.OBSERVE
        return cls(
            active=bool(data.get("active", True)),
            mode=mode,
            last_tick_at=str(data.get("last_tick_at") or "").strip(),
            tick_count=max(0, int(data.get("tick_count") or 0)),
            last_error=str(data.get("last_error") or "").strip(),
            last_summary=coerce_mapping(data.get("last_summary") or {}),
            updated_at=str(data.get("updated_at") or utc_now_iso()).strip(),
        )
