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


@dataclass(frozen=True)
class GenerateTaskBlueprintCommandV1:
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "constraints", _to_dict_copy(self.constraints))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


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
class TaskBlueprintResultV1:
    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    summary: str = ""
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if self.blueprint_id is not None:
            object.__setattr__(self, "blueprint_id", str(self.blueprint_id).strip() or None)
        object.__setattr__(self, "recommendations", tuple(str(v) for v in self.recommendations))
        object.__setattr__(self, "risks", tuple(str(v) for v in self.risks))


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
        preconditions: Required pre-state (e.g. ``"no_blocker_risks_open"``).
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
    "ChiefEngineerBlueprintError",
    "ChiefEngineerBlueprintErrorV1",
    "GenerateTaskBlueprintCommandV1",
    "GetBlueprintStatusQueryV1",
    "GovernanceSummaryV1",
    "HandoffDecisionV1",
    "ListADRsQueryV1",
    "ListRisksQueryV1",
    "ListTechDebtQueryV1",
    "QualityGateResultV1",
    "RegisterADRCommandV1",
    "RegisterRiskCommandV1",
    "RegisterTechDebtCommandV1",
    "RiskEventV1",
    "RiskRecordV1",
    "RiskSeverity",
    "RiskStatus",
    "RollbackLinkV1",
    "RollbackStrategy",
    "TaskBlueprintGeneratedEventV1",
    "TaskBlueprintResultV1",
    "TechDebtEventV1",
    "TechDebtRecordV1",
    "TechDebtSeverity",
    "TechDebtStatus",
    "UpdateADRStatusCommandV1",
    "UpdateRiskStatusCommandV1",
    "UpdateTechDebtStatusCommandV1",
]
