from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    normalize_failure_class as normalize_run_ledger_failure_class,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _to_string_tuple(values: tuple[str, ...] | list[str] | set[str] | None) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        token = str(value or "").strip()
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return tuple(rows)


def _require_image_model_capability_ref(value: str) -> str:
    ref = _require_non_empty("model_capability_ref", value)
    parts = ref.split(":")
    if (
        len(parts) != 5
        or parts[0] != "llm.control_plane"
        or parts[1] != "model-capability"
        or not parts[2]
        or parts[3] != "image_input"
        or not parts[4]
    ):
        raise ValueError("model_capability_ref must point to llm.control_plane image_input capability")
    return ref


@dataclass(frozen=True)
class TracebackFrameV1:
    path: str
    line: int
    function: str
    code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_non_empty("path", self.path))
        object.__setattr__(self, "function", _require_non_empty("function", self.function))
        object.__setattr__(self, "code", str(self.code or "").strip())
        line = int(self.line)
        if line < 1:
            raise ValueError("line must be >= 1")
        object.__setattr__(self, "line", line)


@dataclass(frozen=True)
class FailureSignalV1:
    signal_id: str
    task_id: str
    workspace: str
    signal_type: str
    summary: str
    frames: tuple[TracebackFrameV1, ...] = field(default_factory=tuple)
    severity: str = "error"
    source: str = "traceback"
    raw_excerpt: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _require_non_empty("signal_id", self.signal_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "signal_type", _require_non_empty("signal_type", self.signal_type))
        object.__setattr__(self, "summary", _require_non_empty("summary", self.summary))
        object.__setattr__(self, "severity", _require_non_empty("severity", self.severity))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        frames = tuple(self.frames)
        if not all(isinstance(frame, TracebackFrameV1) for frame in frames):
            raise TypeError("frames must contain TracebackFrameV1 values")
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "raw_excerpt", str(self.raw_excerpt or ""))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class ParseTracebackFramesCommandV1:
    task_id: str
    workspace: str
    traceback_text: str
    run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "traceback_text", _require_non_empty("traceback_text", self.traceback_text))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class ParseTracebackFramesResultV1:
    ok: bool
    task_id: str
    workspace: str
    signal: FailureSignalV1
    frame_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.signal, FailureSignalV1):
            raise TypeError("signal must be a FailureSignalV1")
        object.__setattr__(self, "frame_count", len(self.signal.frames))


@dataclass(frozen=True)
class RunQaAuditCommandV1:
    task_id: str
    workspace: str
    run_id: str | None = None
    criteria: Mapping[str, Any] = field(default_factory=dict)
    evidence_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "criteria", _to_dict_copy(self.criteria))
        object.__setattr__(self, "evidence_paths", tuple(str(v) for v in self.evidence_paths if str(v).strip()))


@dataclass(frozen=True)
class VisualAuditFindingV1:
    """One typed QA finding derived from image evidence."""

    finding_id: str
    image_ref: str
    category: str
    summary: str
    severity: str = "info"
    confidence: float = 0.0
    evidence_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", _require_non_empty("finding_id", self.finding_id))
        object.__setattr__(self, "image_ref", _require_non_empty("image_ref", self.image_ref))
        object.__setattr__(self, "category", _require_non_empty("category", self.category))
        object.__setattr__(self, "summary", _require_non_empty("summary", self.summary))
        object.__setattr__(self, "severity", _require_non_empty("severity", self.severity))
        confidence = float(self.confidence)
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        if self.evidence_ref is not None:
            object.__setattr__(self, "evidence_ref", _require_non_empty("evidence_ref", self.evidence_ref))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RunVisualQaAuditCommandV1:
    """Run a QA visual audit over image evidence refs using a verified vision model."""

    task_id: str
    workspace: str
    image_refs: tuple[str, ...]
    model_capability_ref: str
    run_id: str | None = None
    criteria: Mapping[str, Any] = field(default_factory=dict)
    evidence_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        image_refs = _to_string_tuple(list(self.image_refs))
        if not image_refs:
            raise ValueError("image_refs must include at least one image ref")
        object.__setattr__(self, "image_refs", image_refs)
        object.__setattr__(
            self,
            "model_capability_ref",
            _require_image_model_capability_ref(self.model_capability_ref),
        )
        object.__setattr__(self, "criteria", _to_dict_copy(self.criteria))
        object.__setattr__(self, "evidence_paths", _to_string_tuple(list(self.evidence_paths)))


@dataclass(frozen=True)
class VisualQaAuditResultV1:
    """Structured result for a QA visual audit."""

    ok: bool
    task_id: str
    workspace: str
    verdict: str
    image_refs: tuple[str, ...]
    model_capability_ref: str
    findings: tuple[VisualAuditFindingV1, ...] = field(default_factory=tuple)
    score: float = 0.0
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))
        image_refs = _to_string_tuple(list(self.image_refs))
        if not image_refs:
            raise ValueError("visual audit result must include image_refs")
        object.__setattr__(self, "image_refs", image_refs)
        object.__setattr__(
            self,
            "model_capability_ref",
            _require_non_empty("model_capability_ref", self.model_capability_ref),
        )
        findings = tuple(self.findings)
        if not all(isinstance(finding, VisualAuditFindingV1) for finding in findings):
            raise TypeError("findings must contain VisualAuditFindingV1 values")
        object.__setattr__(self, "findings", findings)
        score = float(self.score)
        if score < 0.0:
            raise ValueError("score must be >= 0")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "evidence_refs", _to_string_tuple(list(self.evidence_refs)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class GetQaVerdictQueryV1:
    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class QaVerdictIssuedEventV1:
    event_id: str
    task_id: str
    workspace: str
    verdict: str
    issued_at: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))
        object.__setattr__(self, "issued_at", _require_non_empty("issued_at", self.issued_at))


@dataclass(frozen=True)
class QaAuditResultV1:
    ok: bool
    task_id: str
    workspace: str
    verdict: str
    score: float = 0.0
    findings: tuple[str, ...] = field(default_factory=tuple)
    suggestions: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))
        object.__setattr__(self, "findings", tuple(str(v) for v in self.findings))
        object.__setattr__(self, "suggestions", tuple(str(v) for v in self.suggestions))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.score < 0:
            raise ValueError("score must be >= 0")


@dataclass(frozen=True)
class QaFailureClassificationV1:
    """Typed QA routing decision with an optional canonical failure class.

    Successful QA verdicts carry ``failure_class=None``. Failure paths must use
    :class:`FailureClassV1` values through
    :func:`build_qa_failure_classification_v1`.
    """

    failure_class: str | None
    route: str
    reason: str
    repairable_by_director: bool
    severity: str = "medium"
    requires_ce_replan: bool = False
    requires_pm_revision: bool = False
    owner: str = ""
    responsible_layer: str = ""
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "polaris.qa_failure_classification.v1"

    def __post_init__(self) -> None:
        if self.failure_class is not None:
            failure_class = _require_non_empty("failure_class", self.failure_class)
            if failure_class.casefold() == "passed":
                raise ValueError("PASSED is a QA verdict state, not a failure class")
            object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "route", _require_non_empty("route", self.route))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "severity", _require_non_empty("severity", self.severity))
        object.__setattr__(self, "owner", str(self.owner or "").strip())
        object.__setattr__(self, "responsible_layer", str(self.responsible_layer or "").strip())
        object.__setattr__(self, "evidence_refs", _to_string_tuple(list(self.evidence_refs)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "failure_class": self.failure_class,
            "route": self.route,
            "reason": self.reason,
            "repairable_by_director": bool(self.repairable_by_director),
            "severity": self.severity,
            "requires_ce_replan": bool(self.requires_ce_replan),
            "requires_pm_revision": bool(self.requires_pm_revision),
            "owner": self.owner,
            "responsible_layer": self.responsible_layer,
            "evidence_refs": list(self.evidence_refs),
        }


_QA_FAILURE_CLASS_ALIASES = {
    "incomplete_materialization": FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
    "missing_entrypoint_target": FailureClassV1.MISSING_ENTRYPOINT_TARGET.value,
    "tool_dispatch_dropped": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
    "required_tool_text_fallback_not_dispatched": (
        FailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value
    ),
    "no_materialized_effect": FailureClassV1.NO_MATERIALIZED_EFFECT.value,
    "execution_evidence_missing": FailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
    "implementation_defect": FailureClassV1.IMPLEMENTATION_DEFECT.value,
    "compiler_or_test_failure": FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
    "implementation_defect_bounce_limit": FailureClassV1.IMPLEMENTATION_DEFECT_BOUNCE_LIMIT.value,
    "dependency_not_unlocked": FailureClassV1.DEPENDENCY_NOT_UNLOCKED.value,
    "deferred_followup_required": FailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value,
    "scope_mismatch": FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value,
    "blueprint_scope_mismatch": FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value,
    "blueprint_verify_invalid": FailureClassV1.BLUEPRINT_VERIFY_INVALID.value,
    "contract_ambiguous": FailureClassV1.CONTRACT_AMBIGUOUS.value,
    "test_environment_failure": FailureClassV1.TEST_ENVIRONMENT_FAILURE.value,
    "acceptance_invalid": FailureClassV1.ACCEPTANCE_INVALID.value,
    "security_policy_violation": FailureClassV1.SECURITY_POLICY_VIOLATION.value,
    "resource_budget_exhausted": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
    "progress_stalled": FailureClassV1.PROGRESS_STALLED.value,
    "model_provider_failure": FailureClassV1.MODEL_PROVIDER_FAILURE.value,
    "model_provider_timeout": FailureClassV1.MODEL_PROVIDER_TIMEOUT.value,
}

QA_ARTIFACT_FAILURE_CLASSES = frozenset(
    {
        FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
        FailureClassV1.MISSING_ENTRYPOINT_TARGET.value,
        FailureClassV1.IMPLEMENTATION_DEFECT.value,
        FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
    }
)

QA_DEFAULT_TASK_BOUNDARY_FAILURE_CLASS = FailureClassV1.TASK_BOUNDARY_FAILED.value
QA_DEFAULT_TOOL_LIFECYCLE_FAILURE_CLASS = FailureClassV1.TOOL_LIFECYCLE_FAILED.value

QA_PLATFORM_FAILURE_CLASSES = frozenset(
    {
        FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        FailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value,
        FailureClassV1.NO_MATERIALIZED_EFFECT.value,
        FailureClassV1.TASKBOARD_DEADLOCK.value,
        FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
        FailureClassV1.MODEL_PROVIDER_FAILURE.value,
        FailureClassV1.MODEL_PROVIDER_TIMEOUT.value,
        QA_DEFAULT_TOOL_LIFECYCLE_FAILURE_CLASS,
    }
)


def _qa_failure_class_key(value: str | FailureClassV1) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def _normalize_qa_failure_class(value: str | FailureClassV1) -> str:
    run_ledger_token = normalize_run_ledger_failure_class(value)
    token = _require_non_empty("failure_class", run_ledger_token)
    return _QA_FAILURE_CLASS_ALIASES.get(
        _qa_failure_class_key(run_ledger_token),
        _QA_FAILURE_CLASS_ALIASES.get(_qa_failure_class_key(token), run_ledger_token.upper()),
    )


def normalize_qa_failure_class(value: str | FailureClassV1) -> str:
    """Normalize QA failure-class tokens to the canonical contract form."""

    return _normalize_qa_failure_class(value)


def project_qa_failure_execution_state(
    failure_class: str | FailureClassV1 | None,
    *,
    default: str = QA_DEFAULT_TASK_BOUNDARY_FAILURE_CLASS,
) -> str:
    """Project a QA failure class into the runtime execution-state vocabulary."""

    normalized = normalize_qa_failure_class(str(failure_class or default))
    if normalized in QA_ARTIFACT_FAILURE_CLASSES:
        return "FAILED_ARTIFACT"
    if normalized in QA_PLATFORM_FAILURE_CLASSES:
        return "FAILED_PLATFORM"
    if normalized:
        return "BLOCKED_WITH_REASON"
    return "PENDING"


def build_qa_failure_classification_v1(
    *,
    failure_class: str | FailureClassV1,
    route: str,
    reason: str,
    repairable_by_director: bool,
    severity: str = "medium",
    requires_ce_replan: bool = False,
    requires_pm_revision: bool = False,
    owner: str = "",
    responsible_layer: str = "",
    evidence_refs: tuple[str, ...] | list[str] | set[str] | None = None,
) -> QaFailureClassificationV1:
    """Build the canonical QA failure classification contract."""

    normalized_failure_class = _normalize_qa_failure_class(failure_class)
    if normalized_failure_class.casefold() == "passed":
        raise ValueError("PASSED is a QA verdict state, not a failure class")
    return QaFailureClassificationV1(
        failure_class=normalized_failure_class,
        route=_require_non_empty("route", route),
        reason=_require_non_empty("reason", reason),
        repairable_by_director=bool(repairable_by_director),
        severity=_require_non_empty("severity", severity),
        requires_ce_replan=bool(requires_ce_replan),
        requires_pm_revision=bool(requires_pm_revision),
        owner=str(owner or "").strip(),
        responsible_layer=str(responsible_layer or "").strip(),
        evidence_refs=_to_string_tuple(evidence_refs),
    )


def build_qa_pass_classification_v1(
    *,
    reason: str,
    route: str = "resolved",
    owner: str = "qa",
    responsible_layer: str = "qa",
    evidence_refs: tuple[str, ...] | list[str] | set[str] | None = None,
) -> QaFailureClassificationV1:
    """Build QA routing metadata for a successful verdict without a failure."""

    return QaFailureClassificationV1(
        failure_class=None,
        route=_require_non_empty("route", route),
        reason=_require_non_empty("reason", reason),
        repairable_by_director=False,
        severity="info",
        owner=str(owner or "").strip(),
        responsible_layer=str(responsible_layer or "").strip(),
        evidence_refs=_to_string_tuple(evidence_refs),
    )


@dataclass(frozen=True)
class QaVerdictLineageV1:
    """Compact verdict history used to prevent QA/CE/Director macro loops."""

    previous_verdict_refs: tuple[str, ...] = field(default_factory=tuple)
    latest_blocking_verdict_ref: str = ""
    latest_blocking_verdict_hash: str = ""
    failure_class_history: tuple[str, ...] = field(default_factory=tuple)
    repeat_failure_count: int = 0
    lineage_hash: str = ""
    schema_version: str = "polaris.qa_verdict_lineage.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "previous_verdict_refs", _to_string_tuple(list(self.previous_verdict_refs)))
        object.__setattr__(self, "latest_blocking_verdict_ref", str(self.latest_blocking_verdict_ref or "").strip())
        object.__setattr__(self, "latest_blocking_verdict_hash", str(self.latest_blocking_verdict_hash or "").strip())
        object.__setattr__(self, "failure_class_history", _to_string_tuple(list(self.failure_class_history)))
        object.__setattr__(self, "repeat_failure_count", max(0, int(self.repeat_failure_count or 0)))
        object.__setattr__(self, "lineage_hash", str(self.lineage_hash or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "previous_verdict_refs": list(self.previous_verdict_refs),
            "latest_blocking_verdict_ref": self.latest_blocking_verdict_ref,
            "latest_blocking_verdict_hash": self.latest_blocking_verdict_hash,
            "failure_class_history": list(self.failure_class_history),
            "repeat_failure_count": self.repeat_failure_count,
            "lineage_hash": self.lineage_hash,
        }


@dataclass(frozen=True)
class QaVerdictEnvelopeV1:
    """Canonical QA verdict envelope for evidence-based routing."""

    workspace: str
    task_id: str
    run_id: str
    verdict: str
    ok: bool
    classification: QaFailureClassificationV1
    stage: str = "qa"
    next_stage: str = ""
    terminal_status: str = ""
    authority: Mapping[str, Any] = field(default_factory=dict)
    ledger: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    receipts: Mapping[str, Any] = field(default_factory=dict)
    artifact_quality: Mapping[str, Any] = field(default_factory=dict)
    lineage: QaVerdictLineageV1 = field(default_factory=QaVerdictLineageV1)
    findings: tuple[str, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    content_hash: str = ""
    schema_version: str = "qa.verdict_envelope.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())
        object.__setattr__(self, "stage", _require_non_empty("stage", self.stage))
        object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))
        if not isinstance(self.classification, QaFailureClassificationV1):
            raise TypeError("classification must be QaFailureClassificationV1")
        if not isinstance(self.lineage, QaVerdictLineageV1):
            raise TypeError("lineage must be QaVerdictLineageV1")
        object.__setattr__(self, "next_stage", str(self.next_stage or "").strip())
        object.__setattr__(self, "terminal_status", str(self.terminal_status or "").strip())
        object.__setattr__(self, "authority", _to_dict_copy(self.authority))
        object.__setattr__(self, "ledger", _to_dict_copy(self.ledger))
        object.__setattr__(self, "evidence", _to_dict_copy(self.evidence))
        object.__setattr__(self, "receipts", _to_dict_copy(self.receipts))
        object.__setattr__(self, "artifact_quality", _to_dict_copy(self.artifact_quality))
        object.__setattr__(self, "findings", _to_string_tuple(list(self.findings)))
        object.__setattr__(self, "metrics", _to_dict_copy(self.metrics))
        object.__setattr__(self, "evidence_refs", _to_string_tuple(list(self.evidence_refs)))
        object.__setattr__(self, "content_hash", str(self.content_hash or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "stage": self.stage,
            "verdict": self.verdict,
            "ok": bool(self.ok),
            "next_stage": self.next_stage,
            "terminal_status": self.terminal_status,
            "authority": dict(self.authority),
            "ledger": dict(self.ledger),
            "evidence": dict(self.evidence),
            "receipts": dict(self.receipts),
            "artifact_quality": dict(self.artifact_quality),
            "classification": self.classification.to_dict(),
            "lineage": self.lineage.to_dict(),
            "findings": list(self.findings),
            "metrics": dict(self.metrics),
            "evidence_refs": list(self.evidence_refs),
            "content_hash": self.content_hash,
        }


class QaAuditErrorV1(RuntimeError):  # noqa: N818
    """Raised when `qa.audit_verdict` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "qa_audit_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Backward-compatible alias — do not remove; external consumers may still import the old name.
QaAuditError = QaAuditErrorV1


# ── Task Market Consumer Contracts ──────────────────────────────────────


@dataclass(frozen=True)
class ClaimQaTaskCommandV1:
    """Internal contract for QA consumer claiming from task market."""

    task_id: str
    workspace: str
    worker_id: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "worker_id", _require_non_empty("worker_id", self.worker_id))


@dataclass(frozen=True)
class QaAuditCompletedEventV1:
    """Event emitted when QA audit completes and task advances."""

    event_id: str
    task_id: str
    workspace: str
    run_id: str | None = None
    verdict: str = "resolved"
    findings: tuple[str, ...] = field(default_factory=tuple)
    completed_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "verdict", _require_non_empty("verdict", self.verdict))


__all__ = [
    "QA_ARTIFACT_FAILURE_CLASSES",
    "QA_PLATFORM_FAILURE_CLASSES",
    # Task Market consumer contracts
    "ClaimQaTaskCommandV1",
    "FailureSignalV1",
    "GetQaVerdictQueryV1",
    "ParseTracebackFramesCommandV1",
    "ParseTracebackFramesResultV1",
    "QaAuditCompletedEventV1",
    "QaAuditError",
    "QaAuditErrorV1",
    "QaAuditResultV1",
    "QaFailureClassificationV1",
    "QaVerdictEnvelopeV1",
    "QaVerdictIssuedEventV1",
    "QaVerdictLineageV1",
    "RunQaAuditCommandV1",
    "RunVisualQaAuditCommandV1",
    "TracebackFrameV1",
    "VisualAuditFindingV1",
    "VisualQaAuditResultV1",
    "build_qa_failure_classification_v1",
    "build_qa_pass_classification_v1",
    "normalize_qa_failure_class",
    "project_qa_failure_execution_state",
]
