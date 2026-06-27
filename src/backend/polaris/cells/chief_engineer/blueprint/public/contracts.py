from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        rows = [value]
    elif isinstance(value, (list, tuple, set)):
        rows = list(value)
    else:
        return ()
    return tuple(str(item).strip() for item in rows if str(item or "").strip())


def _json_safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        if isinstance(item, Mapping):
            data[str(key)] = _json_safe_mapping(item)
        elif isinstance(item, (list, tuple, set)):
            data[str(key)] = [_json_safe_mapping(v) if isinstance(v, Mapping) else v for v in item]
        else:
            data[str(key)] = item
    return data


# ---------------------------------------------------------------------------
# Enums (Tier-1 governance surface)
# ---------------------------------------------------------------------------


class RiskSeverity(str, Enum):
    """Severity ladder for a Risk Register entry."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class RiskStatus(str, Enum):
    """Lifecycle status of a Risk Register entry."""

    OPEN = "open"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    REVERTED = "reverted"


class TechDebtSeverity(str, Enum):
    """Severity ladder for a Tech-Debt Ledger entry."""

    TRIVIAL = "trivial"
    MINOR = "minor"
    MAJOR = "major"
    SEVERE = "severe"
    FATAL = "fatal"


class TechDebtStatus(str, Enum):
    """Lifecycle status of a Tech-Debt Ledger entry."""

    REGISTERED = "registered"
    ACKNOWLEDGED = "acknowledged"
    SCHEDULED = "scheduled"
    PAID = "paid"
    WONTFIX = "wontfix"


class RollbackStrategy(str, Enum):
    """Rollback strategy attached to a blueprint."""

    GIT_REVERT = "git_revert"
    MANIFEST_RESTORE = "manifest_restore"
    FILE_SNAPSHOT = "file_snapshot"


class ADRStatus(str, Enum):
    """Lifecycle status of an Architecture Decision Record (decision log).

    This is the human-facing decision-log status — distinct from the
    internal construction-plan ADR compiler in ``adr_store.py``.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class TechRadarRing(str, Enum):
    """ThoughtWorks-style Tech-Radar ring for a library / technology.

    ``adopt`` and ``trial`` are permitted; ``hold`` and ``deprecated`` are
    stack-policy violations when a blueprint depends on them.
    """

    ADOPT = "adopt"
    TRIAL = "trial"
    HOLD = "hold"
    DEPRECATED = "deprecated"


class IncidentSeverity(str, Enum):
    """Incident severity ladder for a post-mortem (sev1 = most severe)."""

    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class ReleaseDecision(str, Enum):
    """Executive release / change-advisory verdict.

    ``go`` = clear; ``conditional_go`` = warnings only (ship with awareness);
    ``no_go`` = at least one hard blocker (release must not proceed).
    """

    GO = "go"
    CONDITIONAL_GO = "conditional_go"
    NO_GO = "no_go"


class PostMortemStatus(str, Enum):
    """Lifecycle status of a post-mortem / incident review."""

    DRAFT = "draft"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    ACTIONS_OPEN = "actions_open"
    CLOSED = "closed"


@dataclass(frozen=True)
class GenerateTaskBlueprintCommandV1:
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    llm_blueprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "constraints", _to_dict_copy(self.constraints))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "llm_blueprint", _to_dict_copy(self.llm_blueprint))


@dataclass(frozen=True)
class GetBlueprintStatusQueryV1:
    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class TaskBlueprintGeneratedEventV1:
    event_id: str
    task_id: str
    workspace: str
    blueprint_path: str
    generated_at: str
    risk_level: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "blueprint_path", _require_non_empty("blueprint_path", self.blueprint_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class ArchitectureDecisionV1:
    """Structured Chief Engineer architecture or dependency decision."""

    concern: str
    decision: str
    selected_libraries: tuple[str, ...] = field(default_factory=tuple)
    options_considered: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    constraints: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "medium"
    evidence: Mapping[str, Any] = field(default_factory=dict)
    decision_status: str = "decision"
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "concern", _require_non_empty("concern", self.concern))
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "selected_libraries", _string_tuple(self.selected_libraries))
        object.__setattr__(self, "options_considered", _string_tuple(self.options_considered))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "constraints", _string_tuple(self.constraints))
        risk_level = str(self.risk_level or "medium").strip().lower() or "medium"
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "evidence", _json_safe_mapping(self.evidence))
        decision_status = str(self.decision_status or "decision").strip().lower() or "decision"
        object.__setattr__(self, "decision_status", decision_status)
        object.__setattr__(self, "source", str(self.source or "").strip())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ArchitectureDecisionV1:
        """Build a decision contract from a persisted JSON payload."""

        evidence = _json_safe_mapping(payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {})
        decision_status = str(
            payload.get("decision_status")
            or payload.get("status")
            or ("guidance" if evidence.get("guidance_only") is True else "decision")
        ).strip()
        return cls(
            concern=str(payload.get("concern") or payload.get("area") or "").strip(),
            decision=str(payload.get("decision") or payload.get("selected") or "").strip(),
            selected_libraries=_string_tuple(
                payload.get("selected_libraries") or payload.get("libraries") or payload.get("technologies")
            ),
            options_considered=_string_tuple(
                payload.get("options_considered") or payload.get("options") or payload.get("alternatives")
            ),
            rationale=str(payload.get("rationale") or payload.get("reason") or "").strip(),
            constraints=_string_tuple(payload.get("constraints")),
            risk_level=str(payload.get("risk_level") or "medium").strip(),
            evidence=evidence,
            decision_status=decision_status,
            source=str(payload.get("source") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe architecture decision payload."""

        return {
            "schema_version": "chief_engineer.architecture_decision.v1",
            "concern": self.concern,
            "decision": self.decision,
            "selected_libraries": list(self.selected_libraries),
            "options_considered": list(self.options_considered),
            "rationale": self.rationale,
            "constraints": list(self.constraints),
            "risk_level": self.risk_level,
            "evidence": _json_safe_mapping(self.evidence),
            "decision_status": self.decision_status,
            "source": self.source,
        }


@dataclass(frozen=True)
class TaskBlueprintResultV1:
    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    blueprint_hash: str = ""
    summary: str = ""
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    # D-05: Rich blueprint fields for Director context injection
    target_files: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    execution_checklist: tuple[str, ...] = field(default_factory=tuple)
    scope_paths: tuple[str, ...] = field(default_factory=tuple)
    objective: str = ""
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    architecture_decisions: tuple[ArchitectureDecisionV1, ...] = field(default_factory=tuple)
    selected_libraries: tuple[str, ...] = field(default_factory=tuple)
    # Existing target file export summaries so downstream Director tasks
    # (e.g. test generation) know the actual API of files created by earlier tasks.
    existing_target_files: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if self.blueprint_id is not None:
            object.__setattr__(self, "blueprint_id", str(self.blueprint_id).strip() or None)
        object.__setattr__(self, "blueprint_hash", str(self.blueprint_hash or "").strip())
        object.__setattr__(self, "recommendations", tuple(str(v) for v in self.recommendations))
        object.__setattr__(self, "risks", tuple(str(v) for v in self.risks))
        object.__setattr__(self, "target_files", tuple(str(v) for v in self.target_files))
        object.__setattr__(self, "acceptance_criteria", tuple(str(v) for v in self.acceptance_criteria))
        object.__setattr__(self, "execution_checklist", tuple(str(v) for v in self.execution_checklist))
        object.__setattr__(self, "scope_paths", tuple(str(v) for v in self.scope_paths))
        object.__setattr__(self, "objective", str(self.objective or "").strip())
        object.__setattr__(self, "dependencies", tuple(str(v) for v in self.dependencies))
        decisions: list[ArchitectureDecisionV1] = []
        for item in self.architecture_decisions:
            if isinstance(item, ArchitectureDecisionV1):
                decisions.append(item)
            elif isinstance(item, Mapping):
                decisions.append(ArchitectureDecisionV1.from_mapping(item))
        object.__setattr__(self, "architecture_decisions", tuple(decisions))
        object.__setattr__(self, "selected_libraries", _string_tuple(self.selected_libraries))


class ChiefEngineerBlueprintErrorV1(RuntimeError):  # noqa: N818
    """Raised when `chief_engineer.blueprint` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "chief_engineer_blueprint_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Backward-compatible alias — do not remove; external consumers may still import the old name.
ChiefEngineerBlueprintError = ChiefEngineerBlueprintErrorV1


# ---------------------------------------------------------------------------
# Risk Register contracts (Tier-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskRecordV1:
    """A single Risk Register entry.

    Attributes:
        risk_id: Unique risk identifier (e.g. ``risk-{task_id}-{nonce}``).
        task_id: Owning PM task id; ``"workspace"`` for cross-task risks.
        title: Human-readable risk title (caller-supplied, must not be
            project-specific code — Polaris §8).
        severity: ``RiskSeverity`` member.
        owner: Role or person accountable for mitigation.
        mitigation: Short description of the planned mitigation.
        status: ``RiskStatus`` member.
        detected_at: ISO-8601 timestamp (UTC).
        links: Free-form references (paths, ADR ids, ticket ids).
        supersedes: Optional prior risk id this one replaces.
        history: Append-only status change log; never shrinks.
    """

    risk_id: str
    task_id: str
    title: str
    severity: RiskSeverity
    owner: str
    mitigation: str
    status: RiskStatus
    detected_at: str
    links: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "risk_id", _require_non_empty("risk_id", self.risk_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "mitigation", str(self.mitigation or "").strip())
        object.__setattr__(self, "detected_at", _require_non_empty("detected_at", self.detected_at))
        if not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).strip().lower()))
        if not isinstance(self.status, RiskStatus):
            object.__setattr__(self, "status", RiskStatus(str(self.status).strip().lower()))
        object.__setattr__(self, "links", tuple(str(v) for v in self.links))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "task_id": self.task_id,
            "title": self.title,
            "severity": self.severity.value,
            "owner": self.owner,
            "mitigation": self.mitigation,
            "status": self.status.value,
            "detected_at": self.detected_at,
            "links": list(self.links),
            "supersedes": self.supersedes,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterRiskCommandV1:
    """Register a new risk in the workspace Risk Register."""

    task_id: str
    title: str
    severity: RiskSeverity
    owner: str
    mitigation: str
    workspace: str
    links: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "mitigation", str(self.mitigation or "").strip())
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).strip().lower()))
        object.__setattr__(self, "links", tuple(str(v) for v in self.links))


@dataclass(frozen=True)
class ListRisksQueryV1:
    """Filter Risk Register entries."""

    workspace: str
    task_id: str | None = None
    severity: RiskSeverity | None = None
    status: RiskStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", str(self.task_id).strip() or None)
        if self.severity is not None and not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).strip().lower()))
        if self.status is not None and not isinstance(self.status, RiskStatus):
            object.__setattr__(self, "status", RiskStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class UpdateRiskStatusCommandV1:
    """Transition a risk to a new status."""

    workspace: str
    risk_id: str
    status: RiskStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "risk_id", _require_non_empty("risk_id", self.risk_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, RiskStatus):
            object.__setattr__(self, "status", RiskStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class RiskEventV1:
    """Audit event emitted on risk state change."""

    event_id: str
    risk_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "risk_id", _require_non_empty("risk_id", self.risk_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Tech-Debt Ledger contracts (Tier-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechDebtRecordV1:
    """A single Technical-Debt Ledger entry."""

    debt_id: str
    title: str
    description: str
    severity: TechDebtSeverity
    surface: str
    owner: str
    evidence: tuple[str, ...]
    status: TechDebtStatus
    registered_at: str
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "debt_id", _require_non_empty("debt_id", self.debt_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "surface", _require_non_empty("surface", self.surface))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "evidence", tuple(str(v) for v in self.evidence))
        object.__setattr__(self, "registered_at", _require_non_empty("registered_at", self.registered_at))
        if not isinstance(self.severity, TechDebtSeverity):
            object.__setattr__(self, "severity", TechDebtSeverity(str(self.severity).strip().lower()))
        if not isinstance(self.status, TechDebtStatus):
            object.__setattr__(self, "status", TechDebtStatus(str(self.status).strip().lower()))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "debt_id": self.debt_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "surface": self.surface,
            "owner": self.owner,
            "evidence": list(self.evidence),
            "status": self.status.value,
            "registered_at": self.registered_at,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterTechDebtCommandV1:
    """Register a new tech-debt entry."""

    title: str
    description: str
    severity: TechDebtSeverity
    surface: str
    owner: str
    workspace: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "surface", _require_non_empty("surface", self.surface))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "evidence", tuple(str(v) for v in self.evidence))
        if not isinstance(self.severity, TechDebtSeverity):
            object.__setattr__(self, "severity", TechDebtSeverity(str(self.severity).strip().lower()))


@dataclass(frozen=True)
class ListTechDebtQueryV1:
    """Filter Tech-Debt Ledger entries."""

    workspace: str
    severity: TechDebtSeverity | None = None
    surface: str | None = None
    status: TechDebtStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.severity is not None and not isinstance(self.severity, TechDebtSeverity):
            object.__setattr__(self, "severity", TechDebtSeverity(str(self.severity).strip().lower()))
        if self.status is not None and not isinstance(self.status, TechDebtStatus):
            object.__setattr__(self, "status", TechDebtStatus(str(self.status).strip().lower()))
        if self.surface is not None:
            object.__setattr__(self, "surface", str(self.surface).strip() or None)


@dataclass(frozen=True)
class UpdateTechDebtStatusCommandV1:
    """Transition a tech-debt entry to a new status."""

    workspace: str
    debt_id: str
    status: TechDebtStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "debt_id", _require_non_empty("debt_id", self.debt_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, TechDebtStatus):
            object.__setattr__(self, "status", TechDebtStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class TechDebtEventV1:
    """Audit event emitted on tech-debt state change."""

    event_id: str
    debt_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "debt_id", _require_non_empty("debt_id", self.debt_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Quality Gate and Rollback Link (Tier-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityGateResultV1:
    """Structured quality-gate result for a blueprint.

    Attributes:
        passed: ``True`` iff ``blocker_count == 0``.
        blocker_count: Number of blocking issues.
        warning_count: Number of warnings (non-blocking).
        info_count: Number of informational notes.
        blockers: Blocking issues (must be resolved before handoff).
        warnings: Warnings (advisory).
        info: Informational notes.
        evaluated_at: ISO-8601 timestamp (UTC).
    """

    passed: bool
    blocker_count: int
    warning_count: int
    info_count: int
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    info: tuple[str, ...] = field(default_factory=tuple)
    evaluated_at: str = ""

    def __post_init__(self) -> None:
        for field_name in ("blocker_count", "warning_count", "info_count"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        object.__setattr__(self, "blockers", tuple(str(v) for v in self.blockers))
        object.__setattr__(self, "warnings", tuple(str(v) for v in self.warnings))
        object.__setattr__(self, "info", tuple(str(v) for v in self.info))
        object.__setattr__(self, "passed", bool(self.blocker_count == 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "info": list(self.info),
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True)
class RollbackLinkV1:
    """Rollback linkage attached to a blueprint.

    Attributes:
        enabled: Whether rollback is provisioned for this blueprint.
        strategy: ``RollbackStrategy`` member.
        marker_path: Path to the stash / snapshot / manifest.
        preconditions: Safe-state checks that CURRENTLY HOLD for this rollback.
            Each is listed only when satisfied (e.g. ``"no_blocker_risks_open"``
            appears only when no open blocker/critical risk exists); a check's
            ABSENCE means it is not yet satisfied — a gate still to clear.
    """

    enabled: bool
    strategy: RollbackStrategy
    marker_path: str
    preconditions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        if not isinstance(self.strategy, RollbackStrategy):
            object.__setattr__(
                self,
                "strategy",
                RollbackStrategy(str(self.strategy).strip().lower()),
            )
        object.__setattr__(self, "marker_path", _require_non_empty("marker_path", self.marker_path))
        object.__setattr__(self, "preconditions", tuple(str(v) for v in self.preconditions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy.value,
            "marker_path": self.marker_path,
            "preconditions": list(self.preconditions),
        }


# ---------------------------------------------------------------------------
# Architecture Decision Log contracts (Tier-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ADRRecordV1:
    """A single Architecture Decision Record (human-facing decision log).

    Distinct from the internal construction-plan ADR compiler
    (``adr_store.py``): this records *why* a technical decision was made,
    in the canonical ADR shape (context / decision / consequences /
    alternatives), for a real 技术总监's decision ownership.

    Attributes:
        adr_id: Unique decision id (e.g. ``adr_{slug}_{nonce}``).
        title: Short decision title (caller-supplied — Polaris §8).
        status: ``ADRStatus`` member.
        context: The forces / problem that motivated the decision.
        decision: The decision that was made.
        consequences: Resulting trade-offs (positive and negative).
        alternatives: Options that were considered and rejected.
        related_task_ids: Tasks / blueprints this decision governs.
        owner: Role or person accountable for the decision.
        decided_at: ISO-8601 timestamp (UTC).
        supersedes: Optional prior ADR id this one replaces.
        history: Append-only status change log; never shrinks.
    """

    adr_id: str
    title: str
    status: ADRStatus
    context: str
    decision: str
    consequences: str
    owner: str
    decided_at: str
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    related_task_ids: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adr_id", _require_non_empty("adr_id", self.adr_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "context", str(self.context or "").strip())
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "consequences", str(self.consequences or "").strip())
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "decided_at", _require_non_empty("decided_at", self.decided_at))
        if not isinstance(self.status, ADRStatus):
            object.__setattr__(self, "status", ADRStatus(str(self.status).strip().lower()))
        object.__setattr__(self, "alternatives", tuple(str(v) for v in self.alternatives))
        object.__setattr__(self, "related_task_ids", tuple(str(v) for v in self.related_task_ids))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "adr_id": self.adr_id,
            "title": self.title,
            "status": self.status.value,
            "context": self.context,
            "decision": self.decision,
            "consequences": self.consequences,
            "owner": self.owner,
            "decided_at": self.decided_at,
            "alternatives": list(self.alternatives),
            "related_task_ids": list(self.related_task_ids),
            "supersedes": self.supersedes,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterADRCommandV1:
    """Record a new Architecture Decision Record."""

    title: str
    decision: str
    owner: str
    workspace: str
    context: str = ""
    consequences: str = ""
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    related_task_ids: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "context", str(self.context or "").strip())
        object.__setattr__(self, "consequences", str(self.consequences or "").strip())
        object.__setattr__(self, "alternatives", tuple(str(v) for v in self.alternatives))
        object.__setattr__(self, "related_task_ids", tuple(str(v) for v in self.related_task_ids))


@dataclass(frozen=True)
class ListADRsQueryV1:
    """Filter Architecture Decision Records."""

    workspace: str
    status: ADRStatus | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.status is not None and not isinstance(self.status, ADRStatus):
            object.__setattr__(self, "status", ADRStatus(str(self.status).strip().lower()))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", str(self.task_id).strip() or None)


@dataclass(frozen=True)
class UpdateADRStatusCommandV1:
    """Transition an ADR to a new status."""

    workspace: str
    adr_id: str
    status: ADRStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "adr_id", _require_non_empty("adr_id", self.adr_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, ADRStatus):
            object.__setattr__(self, "status", ADRStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class ADREventV1:
    """Audit event emitted on ADR state change."""

    event_id: str
    adr_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "adr_id", _require_non_empty("adr_id", self.adr_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Tech Radar contracts (Tier-2 stack/library policy)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechRadarEntryV1:
    """A single Tech-Radar entry (a library/technology placed on a ring)."""

    entry_id: str
    library: str
    ring: TechRadarRing
    rationale: str
    owner: str
    decided_at: str
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _require_non_empty("entry_id", self.entry_id))
        object.__setattr__(self, "library", _require_non_empty("library", self.library))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "decided_at", _require_non_empty("decided_at", self.decided_at))
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "library": self.library,
            "ring": self.ring.value,
            "rationale": self.rationale,
            "owner": self.owner,
            "decided_at": self.decided_at,
            "supersedes": self.supersedes,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterTechRadarCommandV1:
    """Place a library on a Tech-Radar ring."""

    library: str
    ring: TechRadarRing
    owner: str
    workspace: str
    rationale: str = ""
    supersedes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "library", _require_non_empty("library", self.library))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))


@dataclass(frozen=True)
class ListTechRadarQueryV1:
    """Filter Tech-Radar entries."""

    workspace: str
    ring: TechRadarRing | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.ring is not None and not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))


@dataclass(frozen=True)
class UpdateTechRadarRingCommandV1:
    """Move a Tech-Radar entry to a new ring."""

    workspace: str
    entry_id: str
    ring: TechRadarRing
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "entry_id", _require_non_empty("entry_id", self.entry_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))


@dataclass(frozen=True)
class StackPolicyViolationV1:
    """A blueprint dependency that violates the Tech Radar (hold/deprecated)."""

    library: str
    ring: TechRadarRing
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "library", _require_non_empty("library", self.library))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))

    def to_dict(self) -> dict[str, Any]:
        return {"library": self.library, "ring": self.ring.value, "rationale": self.rationale}


@dataclass(frozen=True)
class TechRadarEventV1:
    """Audit event emitted on Tech-Radar state change."""

    event_id: str
    entry_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "entry_id", _require_non_empty("entry_id", self.entry_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Post-Mortem / Incident Review contracts (Tier-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostMortemRecordV1:
    """A single post-mortem / incident review (blameless, learning-oriented)."""

    incident_id: str
    title: str
    severity: IncidentSeverity
    summary: str
    root_cause: str
    impact: str
    status: PostMortemStatus
    occurred_at: str
    owner: str
    recorded_at: str
    timeline: tuple[str, ...] = field(default_factory=tuple)
    action_items: tuple[str, ...] = field(default_factory=tuple)
    related_risk_ids: tuple[str, ...] = field(default_factory=tuple)
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "incident_id", _require_non_empty("incident_id", self.incident_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(self, "root_cause", str(self.root_cause or "").strip())
        object.__setattr__(self, "impact", str(self.impact or "").strip())
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "recorded_at", _require_non_empty("recorded_at", self.recorded_at))
        if not isinstance(self.severity, IncidentSeverity):
            object.__setattr__(self, "severity", IncidentSeverity(str(self.severity).strip().lower()))
        if not isinstance(self.status, PostMortemStatus):
            object.__setattr__(self, "status", PostMortemStatus(str(self.status).strip().lower()))
        object.__setattr__(self, "timeline", tuple(str(v) for v in self.timeline))
        object.__setattr__(self, "action_items", tuple(str(v) for v in self.action_items))
        object.__setattr__(self, "related_risk_ids", tuple(str(v) for v in self.related_risk_ids))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity.value,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "impact": self.impact,
            "status": self.status.value,
            "occurred_at": self.occurred_at,
            "owner": self.owner,
            "recorded_at": self.recorded_at,
            "timeline": list(self.timeline),
            "action_items": list(self.action_items),
            "related_risk_ids": list(self.related_risk_ids),
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterPostMortemCommandV1:
    """Record a new post-mortem / incident review."""

    title: str
    severity: IncidentSeverity
    occurred_at: str
    owner: str
    workspace: str
    summary: str = ""
    root_cause: str = ""
    impact: str = ""
    timeline: tuple[str, ...] = field(default_factory=tuple)
    action_items: tuple[str, ...] = field(default_factory=tuple)
    related_risk_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(self, "root_cause", str(self.root_cause or "").strip())
        object.__setattr__(self, "impact", str(self.impact or "").strip())
        if not isinstance(self.severity, IncidentSeverity):
            object.__setattr__(self, "severity", IncidentSeverity(str(self.severity).strip().lower()))
        object.__setattr__(self, "timeline", tuple(str(v) for v in self.timeline))
        object.__setattr__(self, "action_items", tuple(str(v) for v in self.action_items))
        object.__setattr__(self, "related_risk_ids", tuple(str(v) for v in self.related_risk_ids))


@dataclass(frozen=True)
class ListPostMortemsQueryV1:
    """Filter post-mortems."""

    workspace: str
    severity: IncidentSeverity | None = None
    status: PostMortemStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.severity is not None and not isinstance(self.severity, IncidentSeverity):
            object.__setattr__(self, "severity", IncidentSeverity(str(self.severity).strip().lower()))
        if self.status is not None and not isinstance(self.status, PostMortemStatus):
            object.__setattr__(self, "status", PostMortemStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class UpdatePostMortemStatusCommandV1:
    """Transition a post-mortem to a new status."""

    workspace: str
    incident_id: str
    status: PostMortemStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "incident_id", _require_non_empty("incident_id", self.incident_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, PostMortemStatus):
            object.__setattr__(self, "status", PostMortemStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class PostMortemEventV1:
    """Audit event emitted on post-mortem state change."""

    event_id: str
    incident_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "incident_id", _require_non_empty("incident_id", self.incident_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Release Readiness / Change-Advisory contract (Tier-2 capstone)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseReadinessV1:
    """Executive GO / NO-GO that aggregates the whole governance surface.

    A read-time synthesis (NOT a stored ledger) of the existing capabilities:
    open blocker/critical risks, per-blueprint quality-gate blockers, open
    sev1/sev2 incidents, stack-policy violations, and unpaid fatal/severe
    tech debt. ``decision`` is ``no_go`` if any hard blocker is present,
    ``conditional_go`` if only warnings, else ``go``.

    Attributes:
        decision: ``ReleaseDecision`` verdict.
        workspace: Assessed workspace.
        blocker_count: Number of release-blocking signals.
        warning_count: Number of advisory signals.
        blockers: Blocking signal messages (``"<source>: <detail>"``).
        warnings: Advisory signal messages.
        signals: Per-source structured counts (risk / quality_gate /
            post_mortem / stack_policy / tech_debt).
        assessed_at: ISO-8601 timestamp (UTC).
    """

    decision: ReleaseDecision
    workspace: str
    blocker_count: int
    warning_count: int
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    signals: dict[str, Any] = field(default_factory=dict)
    assessed_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        for field_name in ("blocker_count", "warning_count"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        if not isinstance(self.decision, ReleaseDecision):
            object.__setattr__(self, "decision", ReleaseDecision(str(self.decision).strip().lower()))
        object.__setattr__(self, "blockers", tuple(str(v) for v in self.blockers))
        object.__setattr__(self, "warnings", tuple(str(v) for v in self.warnings))
        if not isinstance(self.signals, dict):
            object.__setattr__(self, "signals", dict(self.signals or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "workspace": self.workspace,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "signals": dict(self.signals),
            "assessed_at": self.assessed_at,
        }


@dataclass(frozen=True)
class GovernanceSummaryV1:
    """Aggregate governance view attached to a blueprint.

    Attributes:
        blueprint_id: Owning blueprint.
        risk_summary: Counts and severities for related risks.
        tech_debt_summary: Counts and severities for related tech debt.
        quality_gate: Quality gate result.
        rollback: Rollback linkage.
    """

    blueprint_id: str
    risk_summary: dict[str, Any]
    tech_debt_summary: dict[str, Any]
    quality_gate: QualityGateResultV1
    rollback: RollbackLinkV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "blueprint_id", _require_non_empty("blueprint_id", self.blueprint_id))
        if not isinstance(self.risk_summary, dict):
            object.__setattr__(self, "risk_summary", dict(self.risk_summary or {}))
        if not isinstance(self.tech_debt_summary, dict):
            object.__setattr__(self, "tech_debt_summary", dict(self.tech_debt_summary or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "risk_summary": dict(self.risk_summary),
            "tech_debt_summary": dict(self.tech_debt_summary),
            "quality_gate": self.quality_gate.to_dict(),
            "rollback": self.rollback.to_dict(),
        }


@dataclass(frozen=True)
class HandoffDecisionV1:
    """Director-handoff gate decision for a blueprint.

    The enforcement primitive that closes the quality-gate loop: a real
    技术总监 blocks handoff to the Director when the blueprint carries
    blocking quality issues or open blocker/critical risks.

    Attributes:
        allowed: ``True`` iff the blueprint may be handed to the Director.
        blueprint_id: Owning blueprint id.
        task_id: Owning PM task id (best-effort).
        blocker_count: Number of blocking quality-gate issues.
        warning_count: Number of (non-blocking) quality-gate warnings.
        open_blocker_risk_count: Open risks of severity critical/blocker.
        blockers: The blocking issue messages (gate + risk-derived).
        reason: One-line human-readable decision rationale.
        evaluated_at: ISO-8601 timestamp (UTC).
    """

    allowed: bool
    blueprint_id: str
    blocker_count: int
    warning_count: int
    open_blocker_risk_count: int
    task_id: str = ""
    blockers: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    evaluated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "blueprint_id", _require_non_empty("blueprint_id", self.blueprint_id))
        for field_name in ("blocker_count", "warning_count", "open_blocker_risk_count"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        object.__setattr__(self, "blockers", tuple(str(v) for v in self.blockers))
        object.__setattr__(self, "allowed", bool(self.allowed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blueprint_id": self.blueprint_id,
            "task_id": self.task_id,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "open_blocker_risk_count": self.open_blocker_risk_count,
            "blockers": list(self.blockers),
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
        }


__all__ = [
    "ADREventV1",
    "ADRRecordV1",
    "ADRStatus",
    "ArchitectureDecisionV1",
    "ChiefEngineerBlueprintError",
    "ChiefEngineerBlueprintErrorV1",
    "GenerateTaskBlueprintCommandV1",
    "GetBlueprintStatusQueryV1",
    "GovernanceSummaryV1",
    "HandoffDecisionV1",
    "IncidentSeverity",
    "ListADRsQueryV1",
    "ListPostMortemsQueryV1",
    "ListRisksQueryV1",
    "ListTechDebtQueryV1",
    "ListTechRadarQueryV1",
    "PostMortemEventV1",
    "PostMortemRecordV1",
    "PostMortemStatus",
    "QualityGateResultV1",
    "RegisterADRCommandV1",
    "RegisterPostMortemCommandV1",
    "RegisterRiskCommandV1",
    "RegisterTechDebtCommandV1",
    "RegisterTechRadarCommandV1",
    "ReleaseDecision",
    "ReleaseReadinessV1",
    "RiskEventV1",
    "RiskRecordV1",
    "RiskSeverity",
    "RiskStatus",
    "RollbackLinkV1",
    "RollbackStrategy",
    "StackPolicyViolationV1",
    "TaskBlueprintGeneratedEventV1",
    "TaskBlueprintResultV1",
    "TechDebtEventV1",
    "TechDebtRecordV1",
    "TechDebtSeverity",
    "TechDebtStatus",
    "TechRadarEntryV1",
    "TechRadarEventV1",
    "TechRadarRing",
    "UpdateADRStatusCommandV1",
    "UpdatePostMortemStatusCommandV1",
    "UpdateRiskStatusCommandV1",
    "UpdateTechDebtStatusCommandV1",
    "UpdateTechRadarRingCommandV1",
]
