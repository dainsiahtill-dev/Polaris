from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


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


class ProjectOutcomeOwnerObservationV1Error(ValueError):
    """Typed fail-closed error for a direct owner observation query."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = str(error_code or "").strip() or "factory_chain_owner_observation_failed"
        super().__init__(message)


class DirectorStatusObservationV1Error(ValueError):
    """Typed fail-closed error for Director status owner observation."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = str(error_code or "").strip() or "director_status_observation_failed"
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


@dataclass(frozen=True, slots=True)
class ProjectOutcomeNonFactoryEvidenceRefsV1:
    """Caller claims for axes not owned by ``factory.pipeline``."""

    delivery: tuple[str, ...] = field(default_factory=tuple)
    qa: tuple[str, ...] = field(default_factory=tuple)
    task_boundary: tuple[str, ...] = field(default_factory=tuple)
    task_runtime: tuple[str, ...] = field(default_factory=tuple)
    run_ledger: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("delivery", "qa", "task_boundary", "task_runtime", "run_ledger"):
            object.__setattr__(
                self,
                name,
                _normalize_token_tuple(getattr(self, name), f"non_factory_evidence_refs_{name}"),
            )


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


@dataclass(frozen=True, slots=True)
class ProjectOutcomeNonFactoryClaimsV1:
    """Typed caller claims excluding the Factory-owned chain axis."""

    delivery: DeliveryAxisV1
    qa: QaAxisV1
    task_boundary: TaskBoundaryAxisV1
    task_runtime: TaskRuntimeAxisV1
    run_ledger: RunLedgerAxisV1
    evidence_refs: ProjectOutcomeNonFactoryEvidenceRefsV1 = field(
        default_factory=ProjectOutcomeNonFactoryEvidenceRefsV1
    )
    missing_required_modalities: tuple[str, ...] = field(default_factory=tuple)
    failed_required_modalities: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    task_count: int = 0
    completed_task_count: int = 0

    def __post_init__(self) -> None:
        if type(self.evidence_refs) is not ProjectOutcomeNonFactoryEvidenceRefsV1:
            raise ProjectOutcomeValidationV1Error(
                "invalid_non_factory_evidence_refs_type",
                "evidence_refs must be an exact ProjectOutcomeNonFactoryEvidenceRefsV1 instance",
            )
        probe = ProjectOutcomeQueryV1(
            run_id="non-factory-claims-validation",
            delivery=self.delivery,
            chain=ChainAxisV1.NOT_STARTED,
            qa=self.qa,
            task_boundary=self.task_boundary,
            task_runtime=self.task_runtime,
            run_ledger=self.run_ledger,
            evidence_refs=ProjectOutcomeEvidenceRefsV1(
                delivery=self.evidence_refs.delivery,
                qa=self.evidence_refs.qa,
                task_boundary=self.evidence_refs.task_boundary,
                task_runtime=self.evidence_refs.task_runtime,
                run_ledger=self.evidence_refs.run_ledger,
            ),
            missing_required_modalities=self.missing_required_modalities,
            failed_required_modalities=self.failed_required_modalities,
            reasons=self.reasons,
            task_count=self.task_count,
            completed_task_count=self.completed_task_count,
        )
        object.__setattr__(self, "missing_required_modalities", probe.missing_required_modalities)
        object.__setattr__(self, "failed_required_modalities", probe.failed_required_modalities)
        object.__setattr__(self, "reasons", probe.reasons)
        object.__setattr__(self, "task_count", probe.task_count)
        object.__setattr__(self, "completed_task_count", probe.completed_task_count)


@dataclass(frozen=True, slots=True)
class FactoryChainOwnerObservationV1:
    """Normalized immutable Factory chain facts consumed by projection."""

    workspace: str
    run_id: str
    available: bool
    status: str
    chain_completed: bool
    event_refs: tuple[str, ...]
    completion_event_ref: str | None
    projection_hash: str

    def __post_init__(self) -> None:
        for field_name in ("workspace", "run_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ProjectOutcomeOwnerObservationV1Error(
                    f"invalid_factory_chain_owner_{field_name}",
                    f"Factory chain owner {field_name} must be a non-empty exact string",
                )
            object.__setattr__(self, field_name, value.strip())
        if type(self.available) is not bool or type(self.chain_completed) is not bool:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_boolean",
                "Factory chain owner availability and completion must be exact booleans",
            )
        if type(self.status) is not str:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_status",
                "Factory chain owner status must be an exact string",
            )
        status = self.status.strip()
        if self.available is not bool(status):
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_availability",
                "Available Factory observations require status; unavailable observations forbid it",
            )
        if type(self.event_refs) is not tuple:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_event_refs",
                "Factory chain event_refs must be an exact tuple",
            )
        event_refs: list[str] = []
        for item in self.event_refs:
            if type(item) is not str or not item.strip():
                raise ProjectOutcomeOwnerObservationV1Error(
                    "invalid_factory_chain_owner_event_refs",
                    "Factory chain event_refs must contain non-empty exact strings",
                )
            event_ref = item.strip()
            if event_ref in event_refs:
                raise ProjectOutcomeOwnerObservationV1Error(
                    "invalid_factory_chain_owner_event_refs",
                    "Factory chain event_refs must be unique",
                )
            event_refs.append(event_ref)
        completion_event_ref = self.completion_event_ref
        if completion_event_ref is not None:
            if type(completion_event_ref) is not str or not completion_event_ref.strip():
                raise ProjectOutcomeOwnerObservationV1Error(
                    "invalid_factory_chain_owner_completion_event_ref",
                    "Factory completion_event_ref must be null or a non-empty exact string",
                )
            completion_event_ref = completion_event_ref.strip()
            if completion_event_ref not in event_refs:
                raise ProjectOutcomeOwnerObservationV1Error(
                    "invalid_factory_chain_owner_completion_event_ref",
                    "Factory completion_event_ref must identify an event_ref",
                )
        if self.chain_completed is not (completion_event_ref is not None and status == "completed"):
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_completion",
                "Factory chain completion must bind completed status and completion evidence",
            )
        projection_hash = self.projection_hash
        if (
            type(projection_hash) is not str
            or len(projection_hash) != 64
            or any(character not in "0123456789abcdef" for character in projection_hash)
        ):
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_projection_hash",
                "Factory projection_hash must be a lowercase SHA-256 digest",
            )
        if not self.available and (event_refs or completion_event_ref is not None):
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_owner_unavailable_evidence",
                "Unavailable Factory observations cannot carry event evidence",
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "event_refs", tuple(event_refs))
        object.__setattr__(self, "completion_event_ref", completion_event_ref)


@runtime_checkable
class FactoryChainOwnerObservationPortV1(Protocol):
    """Bootstrap-bound read port for authoritative Factory chain observation."""

    async def observe_factory_chain(
        self,
        *,
        workspace: str,
        run_id: str,
    ) -> FactoryChainOwnerObservationV1:
        """Return normalized immutable owner facts for one workspace run."""
        ...


@dataclass(frozen=True, slots=True)
class DirectorStatusObservationV1:
    """Exact projection-owned representation of Director status availability."""

    workspace: str
    available: bool
    status: dict[str, Any] | None

    def __post_init__(self) -> None:
        if type(self.workspace) is not str or not self.workspace.strip():
            raise DirectorStatusObservationV1Error(
                "invalid_director_status_owner_workspace",
                "Director status owner workspace must be a non-empty exact string",
            )
        if type(self.available) is not bool:
            raise DirectorStatusObservationV1Error(
                "invalid_director_status_owner_availability",
                "Director status owner availability must be an exact boolean",
            )
        if self.available:
            if type(self.status) is not dict:
                raise DirectorStatusObservationV1Error(
                    "invalid_director_status_owner_payload",
                    "Available Director status observations require an exact dict payload",
                )
            state = self.status.get("state")
            if type(state) is not str or not state.strip():
                raise DirectorStatusObservationV1Error(
                    "invalid_director_status_owner_state",
                    "Available Director status observations require a non-empty exact state",
                )
            object.__setattr__(self, "status", dict(self.status))
        elif self.status is not None:
            raise DirectorStatusObservationV1Error(
                "invalid_director_status_owner_availability",
                "Unavailable Director status observations cannot carry a status payload",
            )
        object.__setattr__(self, "workspace", self.workspace.strip())


@runtime_checkable
class DirectorStatusObservationPortV1(Protocol):
    """Bootstrap-bound read port for authoritative Director status observation."""

    async def observe_director_status(
        self,
        *,
        workspace: str,
    ) -> DirectorStatusObservationV1:
        """Return normalized Director status facts for one workspace."""
        ...


@dataclass(frozen=True, slots=True)
class ProjectOutcomeFactoryOwnerQueryV1:
    """Exact direct-owner query; intentionally has no Factory DTO field."""

    workspace: str
    run_id: str
    claims: ProjectOutcomeNonFactoryClaimsV1

    def __post_init__(self) -> None:
        for field_name in ("workspace", "run_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ProjectOutcomeOwnerObservationV1Error(
                    f"invalid_{field_name}",
                    f"{field_name} must be a non-empty exact string",
                )
            object.__setattr__(self, field_name, value.strip())
        if type(self.claims) is not ProjectOutcomeNonFactoryClaimsV1:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_non_factory_claims_type",
                "claims must be an exact ProjectOutcomeNonFactoryClaimsV1 instance",
            )


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


_GR1B_REMAINING_UNBOUND_OWNER_AXES: tuple[str, ...] = (
    "delivery",
    "qa",
    "task_boundary",
    "task_runtime",
    "run_ledger",
)


@dataclass(frozen=True, slots=True)
class ProjectOutcomeFactoryOwnerBindingV1:
    """Outcome plus evidence that Factory chain ownership was directly observed."""

    outcome: ProjectOutcomeV1
    factory_chain_owner_observed: bool
    factory_chain_projection_hash: str
    factory_chain_evidence_refs: tuple[str, ...]
    remaining_unbound_owner_axes: tuple[str, ...] = _GR1B_REMAINING_UNBOUND_OWNER_AXES

    def __post_init__(self) -> None:
        if type(self.outcome) is not ProjectOutcomeV1:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_bound_outcome_type",
                "outcome must be an exact ProjectOutcomeV1 instance",
            )
        if self.factory_chain_owner_observed is not True:
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_owner_not_observed",
                "factory_chain_owner_observed must be true",
            )
        if type(self.factory_chain_projection_hash) is not str or not self.factory_chain_projection_hash.strip():
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_factory_chain_projection_hash",
                "factory_chain_projection_hash must be a non-empty exact string",
            )
        evidence_refs = _normalize_token_tuple(
            self.factory_chain_evidence_refs,
            "factory_chain_evidence_refs",
        )
        if not evidence_refs:
            raise ProjectOutcomeOwnerObservationV1Error(
                "missing_factory_chain_evidence_refs",
                "factory_chain_evidence_refs must include direct owner evidence",
            )
        if evidence_refs != self.outcome.evidence_refs.chain:
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_binding_evidence_mismatch",
                "factory_chain_evidence_refs must exactly equal outcome.evidence_refs.chain",
            )
        if self.factory_chain_projection_hash.strip() not in evidence_refs:
            raise ProjectOutcomeOwnerObservationV1Error(
                "factory_chain_projection_hash_not_bound",
                "factory_chain_projection_hash must be present in factory_chain_evidence_refs",
            )
        if self.remaining_unbound_owner_axes != _GR1B_REMAINING_UNBOUND_OWNER_AXES:
            raise ProjectOutcomeOwnerObservationV1Error(
                "invalid_remaining_unbound_owner_axes",
                "remaining_unbound_owner_axes must list every non-Factory owner axis",
            )
        object.__setattr__(self, "factory_chain_projection_hash", self.factory_chain_projection_hash.strip())
        object.__setattr__(self, "factory_chain_evidence_refs", evidence_refs)


__all__ = [
    "ChainAxisV1",
    "DeliveryAxisV1",
    "DirectorStatusObservationPortV1",
    "DirectorStatusObservationV1",
    "DirectorStatusObservationV1Error",
    "FactoryChainOwnerObservationPortV1",
    "FactoryChainOwnerObservationV1",
    "ProjectOutcomeEvidenceRefsV1",
    "ProjectOutcomeFactoryOwnerBindingV1",
    "ProjectOutcomeFactoryOwnerQueryV1",
    "ProjectOutcomeNonFactoryClaimsV1",
    "ProjectOutcomeNonFactoryEvidenceRefsV1",
    "ProjectOutcomeOwnerObservationV1Error",
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
