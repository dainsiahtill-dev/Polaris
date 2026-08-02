from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class RuntimeProjectionQueryV1:
    scope: str = "runtime"


@dataclass(frozen=True)
class RuntimeProjectionResultV1:
    payload: dict[str, Any]


@dataclass(frozen=True)
class RuntimeProjectedEventV1:
    scope: str
    channels: tuple[str, ...] = ()


class RuntimeObserverEventTypeV1(StrEnum):
    """Observer-facing projection event types carried by runtime.v2."""

    LLM_WAITING = "llm_waiting"
    LLM_COMPLETED = "llm_completed"
    LLM_FAILED = "llm_failed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING_CHUNK = "thinking_chunk"
    THINKING_PREVIEW = "thinking_preview"
    CONTENT_CHUNK = "content_chunk"
    CONTENT_PREVIEW = "content_preview"
    ERROR = "error"


@dataclass(frozen=True)
class RuntimeObserverEventV1:
    """Structured observer event projected from canonical runtime facts."""

    run_id: str
    role: str
    event_type: RuntimeObserverEventTypeV1
    content: str = ""
    task_id: str = ""
    attempt: int = 0
    tool_name: str = ""
    tool_args: dict[str, Any] | None = None
    tool_status: str = ""
    tool_success: bool | None = None
    tool_result_raw: Any = None


class RuntimeProjectionError(Exception):
    """Raised when projection assembly fails."""


class DeliveryAxisV1(StrEnum):
    """Independent delivery evidence axis for ProjectOutcomeV1."""

    UNKNOWN = "unknown"
    MISSING = "missing"
    PRESENT_UNVERIFIED = "present_unverified"
    VERIFIED = "verified"


class ChainAxisV1(StrEnum):
    """Independent PM→CE→Director→QA chain progress axis."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"
    CONTROL_PLANE_FAILED = "control_plane_failed"


class QaAxisV1(StrEnum):
    """Independent QA verdict axis."""

    NOT_RUN = "not_run"
    PENDING = "pending"
    FAILED = "failed"
    PASSED = "passed"


class TaskBoundaryAxisV1(StrEnum):
    """Independent TaskBoundary evidence axis."""

    UNKNOWN = "unknown"
    FAILED = "failed"
    PASSED = "passed"


class TaskRuntimeAxisV1(StrEnum):
    """Independent TaskRuntime convergence axis."""

    NOT_CONVERGED = "not_converged"
    CONVERGED = "converged"


class RunLedgerAxisV1(StrEnum):
    """Independent Run Ledger release/evidence closedness axis."""

    NOT_CLOSED = "not_closed"
    CLOSED = "closed"


class RecommendedDispositionV1(StrEnum):
    """Advisory observer disposition. Not scheduling or write authority."""

    OBSERVE = "observe"
    REVALIDATE = "revalidate"
    REPAIR = "repair"
    ESCALATE_CONTROL_PLANE = "escalate_control_plane"
    BLOCKED_INCOMPLETE = "blocked_incomplete"
    AWAIT_AUTHORITY_BINDING = "await_authority_binding"
    COMPLETE = "complete"


class ProjectOutcomeValidationV1Error(ValueError):
    """Typed fail-closed validation error for ProjectOutcome queries."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = str(error_code or "").strip() or "invalid_project_outcome_query"
        super().__init__(message)


def _normalize_token_tuple(values: object, field_name: str) -> tuple[str, ...]:
    """Validate, sort, and deduplicate string tokens deterministically."""
    if not isinstance(values, (tuple, list)):
        raise ProjectOutcomeValidationV1Error(
            f"invalid_{field_name}_type",
            f"{field_name} must be a tuple or list of strings",
        )
    tokens: list[str] = []
    for item in values:
        if type(item) is not str:
            raise ProjectOutcomeValidationV1Error(
                f"invalid_{field_name}_item_type",
                f"{field_name} items must be exact strings",
            )
        token = item.strip()
        if token:
            tokens.append(token)
    return tuple(sorted(set(tokens)))


def _require_exact_int(value: object, field_name: str) -> int:
    """Accept only exact ``int`` values (reject bool, str, float, other types)."""
    if type(value) is not int:
        raise ProjectOutcomeValidationV1Error(
            f"invalid_{field_name}_type",
            f"{field_name} must be an exact int (bool/str/float rejected)",
        )
    return value


_EVIDENCE_AXIS_NAMES: tuple[str, ...] = (
    "delivery",
    "chain",
    "qa",
    "task_boundary",
    "task_runtime",
    "run_ledger",
)


@dataclass(frozen=True)
class ProjectOutcomeEvidenceRefsV1:
    """Per-axis evidence reference tuples for ProjectOutcome completion.

    Each axis holds normalized (sorted, deduplicated) caller-supplied refs.
    GR0 treats them only as untrusted structural claims: non-empty strings can
    make an outcome a completion *candidate*, but never authoritative
    ``completed_verified``. GR1 must resolve owner facts itself instead of
    trusting these values.
    """

    delivery: tuple[str, ...] = field(default_factory=tuple)
    chain: tuple[str, ...] = field(default_factory=tuple)
    qa: tuple[str, ...] = field(default_factory=tuple)
    task_boundary: tuple[str, ...] = field(default_factory=tuple)
    task_runtime: tuple[str, ...] = field(default_factory=tuple)
    run_ledger: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in _EVIDENCE_AXIS_NAMES:
            raw = getattr(self, name)
            if raw is None:
                normalized: tuple[str, ...] = ()
            elif isinstance(raw, (tuple, list)):
                normalized = _normalize_token_tuple(raw, f"evidence_refs_{name}")
            else:
                raise ProjectOutcomeValidationV1Error(
                    f"invalid_evidence_refs_{name}_type",
                    f"evidence_refs.{name} must be a tuple or list of strings",
                )
            object.__setattr__(self, name, normalized)

    def empty_axes(self) -> tuple[str, ...]:
        """Return stable blocking axis names for empty per-axis evidence refs."""
        empty: list[str] = []
        for name in _EVIDENCE_AXIS_NAMES:
            if not getattr(self, name):
                empty.append(f"evidence_refs.{name}")
        return tuple(empty)


@dataclass(frozen=True)
class ProjectOutcomeQueryV1:
    """Typed fact bundle reduced into ProjectOutcomeV1.

    Inputs must be derived from sole fact owners
    (``runtime.task_runtime``, ``control_plane.run_ledger``, QA, chain
    observers). This query never reads disk, executes commands, schedules
    goals, or establishes authoritative platform outcome by itself. A future
    owner-fact gathering adapter is required before this projection can be
    treated as an end-to-end platform outcome entry.
    """

    run_id: str
    delivery: DeliveryAxisV1
    chain: ChainAxisV1
    qa: QaAxisV1
    task_boundary: TaskBoundaryAxisV1
    task_runtime: TaskRuntimeAxisV1
    run_ledger: RunLedgerAxisV1
    evidence_refs: ProjectOutcomeEvidenceRefsV1 = field(default_factory=ProjectOutcomeEvidenceRefsV1)
    missing_required_modalities: tuple[str, ...] = field(default_factory=tuple)
    failed_required_modalities: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    task_count: int = 0
    completed_task_count: int = 0

    def __post_init__(self) -> None:
        if type(self.run_id) is not str:
            raise ProjectOutcomeValidationV1Error(
                "invalid_run_id_type",
                "run_id must be an exact string",
            )
        run_id = self.run_id.strip()
        if not run_id:
            raise ProjectOutcomeValidationV1Error(
                "empty_run_id",
                "run_id must be a non-empty string",
            )
        task_count = _require_exact_int(self.task_count, "task_count")
        completed_task_count = _require_exact_int(
            self.completed_task_count,
            "completed_task_count",
        )
        if task_count < 0:
            raise ProjectOutcomeValidationV1Error(
                "negative_task_count",
                "task_count must not be negative",
            )
        if completed_task_count < 0:
            raise ProjectOutcomeValidationV1Error(
                "negative_completed_task_count",
                "completed_task_count must not be negative",
            )
        if completed_task_count > task_count:
            raise ProjectOutcomeValidationV1Error(
                "completed_task_count_exceeds_task_count",
                "completed_task_count must not exceed task_count",
            )

        if not isinstance(self.evidence_refs, ProjectOutcomeEvidenceRefsV1):
            raise ProjectOutcomeValidationV1Error(
                "invalid_evidence_refs_type",
                "evidence_refs must be a ProjectOutcomeEvidenceRefsV1 instance",
            )

        missing = _normalize_token_tuple(
            self.missing_required_modalities,
            "missing_required_modalities",
        )
        failed = _normalize_token_tuple(
            self.failed_required_modalities,
            "failed_required_modalities",
        )
        overlap = tuple(sorted(set(missing) & set(failed)))
        if overlap:
            raise ProjectOutcomeValidationV1Error(
                "overlapping_required_modalities",
                f"missing_required_modalities and failed_required_modalities must be disjoint; overlap={overlap!r}",
            )

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "task_count", task_count)
        object.__setattr__(self, "completed_task_count", completed_task_count)
        object.__setattr__(self, "missing_required_modalities", missing)
        object.__setattr__(self, "failed_required_modalities", failed)
        object.__setattr__(self, "reasons", _normalize_token_tuple(self.reasons, "reasons"))

        for name, expected, value in (
            ("delivery", DeliveryAxisV1, self.delivery),
            ("chain", ChainAxisV1, self.chain),
            ("qa", QaAxisV1, self.qa),
            ("task_boundary", TaskBoundaryAxisV1, self.task_boundary),
            ("task_runtime", TaskRuntimeAxisV1, self.task_runtime),
            ("run_ledger", RunLedgerAxisV1, self.run_ledger),
        ):
            if not isinstance(value, expected):
                raise ProjectOutcomeValidationV1Error(
                    f"invalid_{name}",
                    f"{name} must be a {expected.__name__} value",
                )

    def candidate_blocking_axes(self) -> tuple[str, ...]:
        """Return deterministic blockers for the unbound completion candidate."""
        blocked: list[str] = []
        if self.delivery is not DeliveryAxisV1.VERIFIED:
            blocked.append("delivery")
        if self.chain is not ChainAxisV1.COMPLETED:
            blocked.append("chain")
        if self.qa is not QaAxisV1.PASSED:
            blocked.append("qa")
        if self.task_boundary is not TaskBoundaryAxisV1.PASSED:
            blocked.append("task_boundary")
        if self.task_runtime is not TaskRuntimeAxisV1.CONVERGED:
            blocked.append("task_runtime")
        if self.run_ledger is not RunLedgerAxisV1.CLOSED:
            blocked.append("run_ledger")
        if self.missing_required_modalities:
            blocked.append("missing_required_modalities")
        if self.failed_required_modalities:
            blocked.append("failed_required_modalities")
        if self.task_count <= 0:
            blocked.append("task_count")
        if self.completed_task_count != self.task_count:
            blocked.append("completed_task_count")
        blocked.extend(self.evidence_refs.empty_axes())
        return tuple(sorted(set(blocked)))

    def candidate_disposition(self) -> RecommendedDispositionV1:
        """Return advisory handling for an unbound, caller-supplied query."""
        blocking_axes = self.candidate_blocking_axes()
        if not blocking_axes:
            return RecommendedDispositionV1.AWAIT_AUTHORITY_BINDING
        if self.chain is ChainAxisV1.CONTROL_PLANE_FAILED:
            return RecommendedDispositionV1.ESCALATE_CONTROL_PLANE
        if (
            self.failed_required_modalities
            or self.qa is QaAxisV1.FAILED
            or self.task_boundary is TaskBoundaryAxisV1.FAILED
        ):
            return RecommendedDispositionV1.REPAIR
        if self.missing_required_modalities or self.delivery in {
            DeliveryAxisV1.UNKNOWN,
            DeliveryAxisV1.MISSING,
            DeliveryAxisV1.PRESENT_UNVERIFIED,
        }:
            return RecommendedDispositionV1.REVALIDATE
        if self.chain in {ChainAxisV1.NOT_STARTED, ChainAxisV1.ACTIVE} or self.qa in {
            QaAxisV1.NOT_RUN,
            QaAxisV1.PENDING,
        }:
            return RecommendedDispositionV1.OBSERVE
        return RecommendedDispositionV1.BLOCKED_INCOMPLETE


@dataclass(frozen=True)
class ProjectOutcomeV1:
    """Read-only multi-axis project outcome projection.

    ``completed_verified`` is fail-closed and never inferred from caller input.
    Axes remain independent: chain/control-plane failure does not rewrite
    delivery, and missing modalities stay distinct from failed modalities.

    GR0 deliberately exposes only an unbound completion candidate. Therefore
    ``authority_bound`` and ``completed_verified`` must both remain false. GR1
    will add an owner-fact adapter and an authority-bound construction path; a
    caller cannot unlock completion by supplying arbitrary evidence strings.
    """

    run_id: str
    delivery: DeliveryAxisV1
    chain: ChainAxisV1
    qa: QaAxisV1
    task_boundary: TaskBoundaryAxisV1
    task_runtime: TaskRuntimeAxisV1
    run_ledger: RunLedgerAxisV1
    missing_required_modalities: tuple[str, ...]
    failed_required_modalities: tuple[str, ...]
    completion_candidate: bool
    authority_bound: bool
    completed_verified: bool
    recommended_disposition: RecommendedDispositionV1
    evidence_refs: ProjectOutcomeEvidenceRefsV1
    reasons: tuple[str, ...]
    blocking_axes: tuple[str, ...]
    task_count: int = 0
    completed_task_count: int = 0

    def __post_init__(self) -> None:
        """Reject directly constructed results that violate reducer invariants."""
        candidate_query = ProjectOutcomeQueryV1(
            run_id=self.run_id,
            delivery=self.delivery,
            chain=self.chain,
            qa=self.qa,
            task_boundary=self.task_boundary,
            task_runtime=self.task_runtime,
            run_ledger=self.run_ledger,
            evidence_refs=self.evidence_refs,
            missing_required_modalities=self.missing_required_modalities,
            failed_required_modalities=self.failed_required_modalities,
            reasons=self.reasons,
            task_count=self.task_count,
            completed_task_count=self.completed_task_count,
        )
        for field_name, value in (
            ("completion_candidate", self.completion_candidate),
            ("authority_bound", self.authority_bound),
            ("completed_verified", self.completed_verified),
        ):
            if type(value) is not bool:
                raise ProjectOutcomeValidationV1Error(
                    f"invalid_{field_name}_type",
                    f"{field_name} must be an exact bool",
                )
        if not isinstance(self.recommended_disposition, RecommendedDispositionV1):
            raise ProjectOutcomeValidationV1Error(
                "invalid_recommended_disposition",
                "recommended_disposition must be a RecommendedDispositionV1 value",
            )

        expected_blocking = candidate_query.candidate_blocking_axes()
        blocking = _normalize_token_tuple(self.blocking_axes, "blocking_axes")
        if blocking != expected_blocking:
            raise ProjectOutcomeValidationV1Error(
                "inconsistent_blocking_axes",
                f"blocking_axes must equal reducer-derived blockers {expected_blocking!r}",
            )
        expected_candidate = not expected_blocking
        if self.completion_candidate is not expected_candidate:
            raise ProjectOutcomeValidationV1Error(
                "inconsistent_completion_candidate",
                f"completion_candidate must be {expected_candidate!r}",
            )
        if self.authority_bound is not False:
            raise ProjectOutcomeValidationV1Error(
                "unsupported_authority_binding_v1",
                "GR0 ProjectOutcomeV1 cannot be authority-bound; use the future owner-fact adapter",
            )
        if self.completed_verified is not False:
            raise ProjectOutcomeValidationV1Error(
                "unbound_completed_verified",
                "completed_verified cannot be true before owner-fact authority binding",
            )
        expected_disposition = candidate_query.candidate_disposition()
        if self.recommended_disposition is not expected_disposition:
            raise ProjectOutcomeValidationV1Error(
                "inconsistent_recommended_disposition",
                f"recommended_disposition must be {expected_disposition.value!r}",
            )

        object.__setattr__(self, "run_id", candidate_query.run_id)
        object.__setattr__(self, "missing_required_modalities", candidate_query.missing_required_modalities)
        object.__setattr__(self, "failed_required_modalities", candidate_query.failed_required_modalities)
        object.__setattr__(self, "reasons", candidate_query.reasons)
        object.__setattr__(self, "blocking_axes", blocking)
        object.__setattr__(self, "task_count", candidate_query.task_count)
        object.__setattr__(self, "completed_task_count", candidate_query.completed_task_count)


__all__ = [
    "ChainAxisV1",
    "DeliveryAxisV1",
    "ProjectOutcomeEvidenceRefsV1",
    "ProjectOutcomeQueryV1",
    "ProjectOutcomeV1",
    "ProjectOutcomeValidationV1Error",
    "QaAxisV1",
    "RecommendedDispositionV1",
    "RunLedgerAxisV1",
    "RuntimeObserverEventTypeV1",
    "RuntimeObserverEventV1",
    "RuntimeProjectedEventV1",
    "RuntimeProjectionError",
    "RuntimeProjectionQueryV1",
    "RuntimeProjectionResultV1",
    "TaskBoundaryAxisV1",
    "TaskRuntimeAxisV1",
]
