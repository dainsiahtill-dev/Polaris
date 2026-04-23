"""Public contracts for ``runtime.task_market``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_VALID_QUEUE_STAGES = {
    "pending_design",
    "pending_exec",
    "pending_qa",
    "waiting_human",
}
_VALID_TERMINAL_STATUSES = {"resolved", "rejected", "dead_letter"}
_VALID_PRIORITY = {"low", "medium", "high", "critical"}
_VALID_CHANGE_ORDER_TYPES = {
    "doc_patch",
    "scope_add",
    "scope_remove",
    "acceptance_patch",
    "priority_patch",
    "task_cancel",
    "manual_task_edit",
}
_VALID_HUMAN_RESOLUTIONS = {
    "requeue_design",
    "requeue_exec",
    "force_resolve",
    "close_as_invalid",
    "shadow_continue",
}


class TaskWorkItemState(str, Enum):
    """Two-stage job claiming lifecycle states."""

    PENDING = "pending"
    STAGE1_CLAIMED = "stage1_claimed"
    STAGE1_COMPLETE = "stage1_complete"
    STAGE2_CLAIMED = "stage2_claimed"
    STAGE2_COMPLETE = "stage2_complete"


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _copy_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _normalize_stage(name: str, value: str) -> str:
    token = _require_non_empty(name, value).lower()
    if token not in _VALID_QUEUE_STAGES:
        raise ValueError(f"{name} must be one of: {sorted(_VALID_QUEUE_STAGES)}")
    return token


def _normalize_terminal_status(name: str, value: str) -> str:
    token = _require_non_empty(name, value).lower()
    if token not in _VALID_TERMINAL_STATUSES:
        raise ValueError(f"{name} must be one of: {sorted(_VALID_TERMINAL_STATUSES)}")
    return token


def _normalize_priority(value: str) -> str:
    token = str(value or "").strip().lower() or "medium"
    if token not in _VALID_PRIORITY:
        raise ValueError(f"priority must be one of: {sorted(_VALID_PRIORITY)}")
    return token


def _normalize_optional_string(value: object) -> str:
    return str(value or "").strip()


def _normalize_change_policy(value: object) -> str:
    return str(value or "").strip().lower() or "strict"


def _normalize_change_order_type(value: object) -> str:
    token = str(value or "").strip().lower()
    if token not in _VALID_CHANGE_ORDER_TYPES:
        raise ValueError(f"change_type must be one of: {sorted(_VALID_CHANGE_ORDER_TYPES)}")
    return token


def _normalize_human_resolution(name: str, value: str) -> str:
    token = _require_non_empty(name, value).lower()
    if token not in _VALID_HUMAN_RESOLUTIONS:
        raise ValueError(f"{name} must be one of: {sorted(_VALID_HUMAN_RESOLUTIONS)}")
    return token


def _normalize_dependency_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    else:  # pragma: no cover - defensive branch
        raise ValueError("depends_on must be a sequence of task ids")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return tuple(normalized)


@dataclass(frozen=True)
class PublishTaskWorkItemCommandV1:
    workspace: str
    trace_id: str
    run_id: str
    task_id: str
    stage: str
    source_role: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    priority: str = "medium"
    max_attempts: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)
    plan_id: str = ""
    plan_revision_id: str = ""
    root_task_id: str = ""
    parent_task_id: str = ""
    is_leaf: bool = True
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    requirement_digest: str = ""
    constraint_digest: str = ""
    summary_ref: str = ""
    superseded_by_revision: str = ""
    change_policy: str = "strict"
    compensation_group_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "trace_id", _require_non_empty("trace_id", self.trace_id))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "stage", _normalize_stage("stage", self.stage))
        object.__setattr__(self, "source_role", _require_non_empty("source_role", self.source_role))
        copied_payload = _copy_mapping(self.payload)
        if not copied_payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", copied_payload)
        object.__setattr__(self, "priority", _normalize_priority(self.priority))
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        object.__setattr__(self, "plan_id", _normalize_optional_string(self.plan_id))
        object.__setattr__(self, "plan_revision_id", _normalize_optional_string(self.plan_revision_id))
        object.__setattr__(self, "root_task_id", _normalize_optional_string(self.root_task_id))
        object.__setattr__(self, "parent_task_id", _normalize_optional_string(self.parent_task_id))
        object.__setattr__(self, "is_leaf", bool(self.is_leaf))
        object.__setattr__(self, "depends_on", _normalize_dependency_list(self.depends_on))
        object.__setattr__(self, "requirement_digest", _normalize_optional_string(self.requirement_digest))
        object.__setattr__(self, "constraint_digest", _normalize_optional_string(self.constraint_digest))
        object.__setattr__(self, "summary_ref", _normalize_optional_string(self.summary_ref))
        object.__setattr__(
            self,
            "superseded_by_revision",
            _normalize_optional_string(self.superseded_by_revision),
        )
        object.__setattr__(self, "change_policy", _normalize_change_policy(self.change_policy))
        object.__setattr__(
            self,
            "compensation_group_id",
            _normalize_optional_string(self.compensation_group_id),
        )


@dataclass(frozen=True)
class ClaimTaskWorkItemCommandV1:
    workspace: str
    stage: str
    worker_id: str
    worker_role: str
    visibility_timeout_seconds: int = 900
    task_id: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "stage", _normalize_stage("stage", self.stage))
        object.__setattr__(self, "worker_id", _require_non_empty("worker_id", self.worker_id))
        object.__setattr__(self, "worker_role", _require_non_empty("worker_role", self.worker_role))
        if self.visibility_timeout_seconds < 1:
            raise ValueError("visibility_timeout_seconds must be >= 1")
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        if self.trace_id is not None:
            object.__setattr__(self, "trace_id", _require_non_empty("trace_id", self.trace_id))


@dataclass(frozen=True)
class RenewTaskLeaseCommandV1:
    workspace: str
    task_id: str
    lease_token: str
    visibility_timeout_seconds: int = 900

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "lease_token", _require_non_empty("lease_token", self.lease_token))
        if self.visibility_timeout_seconds < 1:
            raise ValueError("visibility_timeout_seconds must be >= 1")


@dataclass(frozen=True)
class AcknowledgeTaskStageCommandV1:
    workspace: str
    task_id: str
    lease_token: str
    next_stage: str | None = None
    terminal_status: str | None = None
    summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "lease_token", _require_non_empty("lease_token", self.lease_token))
        if self.next_stage is not None:
            object.__setattr__(self, "next_stage", _normalize_stage("next_stage", self.next_stage))
        if self.terminal_status is not None:
            object.__setattr__(
                self,
                "terminal_status",
                _normalize_terminal_status("terminal_status", self.terminal_status),
            )
        if self.next_stage is None and self.terminal_status is None:
            object.__setattr__(self, "terminal_status", "resolved")
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class FailTaskStageCommandV1:
    workspace: str
    task_id: str
    lease_token: str
    error_code: str
    error_message: str
    requeue_stage: str | None = None
    to_dead_letter: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "lease_token", _require_non_empty("lease_token", self.lease_token))
        object.__setattr__(self, "error_code", _require_non_empty("error_code", self.error_code))
        object.__setattr__(self, "error_message", _require_non_empty("error_message", self.error_message))
        if self.requeue_stage is not None:
            object.__setattr__(self, "requeue_stage", _normalize_stage("requeue_stage", self.requeue_stage))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RequeueTaskCommandV1:
    workspace: str
    task_id: str
    target_stage: str
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "target_stage", _normalize_stage("target_stage", self.target_stage))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class MoveTaskToDeadLetterCommandV1:
    workspace: str
    task_id: str
    reason: str
    error_code: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        if self.error_code is not None:
            object.__setattr__(self, "error_code", _require_non_empty("error_code", self.error_code))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RequestHumanReviewCommandV1:
    workspace: str
    task_id: str
    reason: str
    trace_id: str = ""
    requested_by: str = "system"
    escalation_policy: str = "tri_council"
    callback_url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "trace_id", _normalize_optional_string(self.trace_id))
        object.__setattr__(self, "requested_by", _require_non_empty("requested_by", self.requested_by))
        object.__setattr__(
            self,
            "escalation_policy",
            _normalize_optional_string(self.escalation_policy).lower() or "tri_council",
        )
        object.__setattr__(self, "callback_url", _normalize_optional_string(self.callback_url))


@dataclass(frozen=True)
class ResolveHumanReviewCommandV1:
    workspace: str
    task_id: str
    resolution: str
    resolved_by: str = "human"
    note: str = ""
    callback_url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "resolution", _normalize_human_resolution("resolution", self.resolution))
        object.__setattr__(self, "resolved_by", _require_non_empty("resolved_by", self.resolved_by))
        object.__setattr__(self, "note", _normalize_optional_string(self.note))
        object.__setattr__(self, "callback_url", _normalize_optional_string(self.callback_url))


@dataclass(frozen=True)
class QueryPendingHumanReviewsV1:
    workspace: str
    limit: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


@dataclass(frozen=True)
class QueryTaskMarketStatusV1:
    workspace: str
    stage: str | None = None
    status: str | None = None
    limit: int = 200
    include_payload: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.stage is not None:
            object.__setattr__(self, "stage", _normalize_stage("stage", self.stage))
        if self.status is not None:
            object.__setattr__(self, "status", _require_non_empty("status", self.status).lower())
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


@dataclass(frozen=True)
class RegisterPlanRevisionCommandV1:
    workspace: str
    plan_id: str
    plan_revision_id: str
    source_role: str
    requirement_digest: str = ""
    constraint_digest: str = ""
    parent_revision_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "plan_revision_id", _require_non_empty("plan_revision_id", self.plan_revision_id))
        object.__setattr__(self, "source_role", _require_non_empty("source_role", self.source_role))
        object.__setattr__(self, "requirement_digest", _normalize_optional_string(self.requirement_digest))
        object.__setattr__(self, "constraint_digest", _normalize_optional_string(self.constraint_digest))
        object.__setattr__(self, "parent_revision_id", _normalize_optional_string(self.parent_revision_id))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class SubmitChangeOrderCommandV1:
    workspace: str
    plan_id: str
    from_revision_id: str
    to_revision_id: str
    source_role: str
    change_type: str
    summary: str = ""
    trace_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    affected_task_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(
            self,
            "from_revision_id",
            _require_non_empty("from_revision_id", self.from_revision_id),
        )
        object.__setattr__(self, "to_revision_id", _require_non_empty("to_revision_id", self.to_revision_id))
        if self.from_revision_id == self.to_revision_id:
            raise ValueError("to_revision_id must differ from from_revision_id")
        object.__setattr__(self, "source_role", _require_non_empty("source_role", self.source_role))
        object.__setattr__(self, "change_type", _normalize_change_order_type(self.change_type))
        object.__setattr__(self, "summary", _normalize_optional_string(self.summary))
        object.__setattr__(self, "trace_id", _normalize_optional_string(self.trace_id))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        object.__setattr__(self, "affected_task_ids", _normalize_dependency_list(self.affected_task_ids))


@dataclass(frozen=True)
class QueryPlanRevisionsV1:
    workspace: str
    plan_id: str = ""
    limit: int = 200

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "plan_id", _normalize_optional_string(self.plan_id))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


@dataclass(frozen=True)
class QueryChangeOrdersV1:
    workspace: str
    plan_id: str = ""
    limit: int = 200

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "plan_id", _normalize_optional_string(self.plan_id))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


@dataclass(frozen=True)
class TaskWorkItemPublishedEventV1:
    event_id: str
    task_id: str
    stage: str
    status: str
    emitted_at: str


@dataclass(frozen=True)
class TaskLeaseGrantedEventV1:
    event_id: str
    task_id: str
    stage: str
    worker_id: str
    lease_token: str
    lease_expires_at: str
    emitted_at: str


@dataclass(frozen=True)
class TaskStageAdvancedEventV1:
    event_id: str
    task_id: str
    from_status: str
    to_status: str
    emitted_at: str


@dataclass(frozen=True)
class TaskDeadLetteredEventV1:
    event_id: str
    task_id: str
    reason: str
    emitted_at: str


@dataclass(frozen=True)
class TaskWorkItemResultV1:
    ok: bool
    task_id: str
    stage: str
    status: str
    version: int
    trace_id: str = ""
    run_id: str = ""
    lease_token: str = ""
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    claimed_by: str = ""
    source_chain: list[str] = field(default_factory=list)
    consolidated_from: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimStage1Result:
    success: bool
    claimant_id: str = ""
    already_claimed_by: str = ""
    merged: bool = False


@dataclass(frozen=True)
class ClaimStage2Result:
    success: bool
    claimant_id: str = ""
    stage1_result_available: bool = False
    consolidated_result: Any = None


@dataclass(frozen=True)
class TaskLeaseRenewResultV1:
    ok: bool
    task_id: str
    lease_token: str
    lease_expires_at: str
    version: int
    reason: str = ""


@dataclass(frozen=True)
class TaskMarketStatusResultV1:
    workspace: str
    total: int
    counts: Mapping[str, int]
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HumanReviewResultV1:
    ok: bool
    task_id: str
    status: str
    stage: str
    resolution: str = ""
    reason: str = ""


@dataclass(frozen=True)
class PlanRevisionResultV1:
    ok: bool
    workspace: str
    plan_id: str
    plan_revision_id: str
    parent_revision_id: str
    reason: str = ""


@dataclass(frozen=True)
class ChangeOrderResultV1:
    ok: bool
    workspace: str
    plan_id: str
    from_revision_id: str
    to_revision_id: str
    change_type: str
    impacted_total: int
    impact_counts: Mapping[str, int]
    affected_task_ids: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


class TaskMarketError(RuntimeError):
    """Raised when ``runtime.task_market`` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "task_market_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _copy_mapping(details)


# Backward-compatible versioned alias used by governance assets.
TaskMarketErrorV1 = TaskMarketError


__all__ = [
    "AcknowledgeTaskStageCommandV1",
    "ChangeOrderResultV1",
    "ClaimStage1Result",
    "ClaimStage2Result",
    "ClaimTaskWorkItemCommandV1",
    "FailTaskStageCommandV1",
    "HumanReviewResultV1",
    "MoveTaskToDeadLetterCommandV1",
    "PlanRevisionResultV1",
    "PublishTaskWorkItemCommandV1",
    "QueryChangeOrdersV1",
    "QueryPendingHumanReviewsV1",
    "QueryPlanRevisionsV1",
    "QueryTaskMarketStatusV1",
    "RegisterPlanRevisionCommandV1",
    "RenewTaskLeaseCommandV1",
    "RequestHumanReviewCommandV1",
    "RequeueTaskCommandV1",
    "ResolveHumanReviewCommandV1",
    "SubmitChangeOrderCommandV1",
    "TaskDeadLetteredEventV1",
    "TaskLeaseGrantedEventV1",
    "TaskLeaseRenewResultV1",
    "TaskMarketError",
    "TaskMarketErrorV1",
    "TaskMarketStatusResultV1",
    "TaskStageAdvancedEventV1",
    "TaskWorkItemPublishedEventV1",
    "TaskWorkItemResultV1",
    "TaskWorkItemState",
]
