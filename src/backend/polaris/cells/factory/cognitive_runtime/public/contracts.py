# ruff: noqa: N818
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from polaris.domain.cognitive_runtime import (
        ChangeSetValidationResult,
        ContextHandoffPack,
        ContextSnapshot,
        DiffCellMapping,
        EditScopeLease,
        HandoffRehydration,
        ProjectionCompileRequest,
        PromotionDecisionRecord,
        RollbackLedgerEntry,
        RuntimeReceipt,
    )


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _copy_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _copy_tuple(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(str(value) for value in (values or []) if str(value).strip())


@dataclass(frozen=True)
class ResolveContextCommandV1:
    workspace: str
    role: str
    query: str
    step: int
    run_id: str
    mode: str
    session_id: str | None = None
    events_path: str = ""
    sources_enabled: tuple[str, ...] = field(default_factory=tuple)
    policy: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "query", _require_non_empty("query", self.query))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        if self.step < 0:
            raise ValueError("step must be >= 0")
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "sources_enabled", _copy_tuple(self.sources_enabled))
        object.__setattr__(self, "policy", _copy_mapping(self.policy))


@dataclass(frozen=True)
class LeaseEditScopeCommandV1:
    workspace: str
    requested_by: str
    scope_paths: tuple[str, ...]
    ttl_seconds: int = 1800
    session_id: str | None = None
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "requested_by", _require_non_empty("requested_by", self.requested_by))
        object.__setattr__(self, "scope_paths", _copy_tuple(self.scope_paths))
        if not self.scope_paths:
            raise ValueError("scope_paths must contain at least one path")
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class ValidateChangeSetCommandV1:
    workspace: str
    changed_files: tuple[str, ...]
    allowed_scope_paths: tuple[str, ...]
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    require_change: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "changed_files", _copy_tuple(self.changed_files))
        object.__setattr__(self, "allowed_scope_paths", _copy_tuple(self.allowed_scope_paths))
        if not self.allowed_scope_paths:
            raise ValueError("allowed_scope_paths must contain at least one path")
        object.__setattr__(self, "evidence_refs", _copy_tuple(self.evidence_refs))


@dataclass(frozen=True)
class RecordRuntimeReceiptCommandV1:
    workspace: str
    receipt_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    run_id: str | None = None
    trace_refs: tuple[str, ...] = field(default_factory=tuple)
    turn_envelope: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "receipt_type", _require_non_empty("receipt_type", self.receipt_type))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "payload", _copy_mapping(self.payload))
        object.__setattr__(self, "trace_refs", _copy_tuple(self.trace_refs))
        object.__setattr__(self, "turn_envelope", _copy_mapping(self.turn_envelope))


@dataclass(frozen=True)
class ExportHandoffPackCommandV1:
    workspace: str
    session_id: str
    run_id: str | None = None
    reason: str = ""
    receipt_limit: int = 20
    turn_envelope: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.receipt_limit <= 0:
            raise ValueError("receipt_limit must be > 0")
        object.__setattr__(self, "turn_envelope", _copy_mapping(self.turn_envelope))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class MapDiffToCellsCommandV1:
    workspace: str
    changed_files: tuple[str, ...]
    graph_catalog_path: str = "docs/graph/catalog/cells.yaml"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "changed_files", _copy_tuple(self.changed_files))
        if not self.changed_files:
            raise ValueError("changed_files must contain at least one path")
        object.__setattr__(
            self, "graph_catalog_path", _require_non_empty("graph_catalog_path", self.graph_catalog_path)
        )


@dataclass(frozen=True)
class RequestProjectionCompileCommandV1:
    workspace: str
    requested_by: str
    subject_ref: str
    changed_files: tuple[str, ...]
    mapped_cells: tuple[str, ...] = field(default_factory=tuple)
    session_id: str | None = None
    run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "requested_by", _require_non_empty("requested_by", self.requested_by))
        object.__setattr__(self, "subject_ref", _require_non_empty("subject_ref", self.subject_ref))
        object.__setattr__(self, "changed_files", _copy_tuple(self.changed_files))
        if not self.changed_files:
            raise ValueError("changed_files must contain at least one path")
        object.__setattr__(self, "mapped_cells", _copy_tuple(self.mapped_cells))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class PromoteOrRejectCommandV1:
    workspace: str
    subject_ref: str
    changed_files: tuple[str, ...]
    mapped_cells: tuple[str, ...]
    write_gate_allowed: bool
    projection_status: str
    projection_request_id: str | None = None
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "subject_ref", _require_non_empty("subject_ref", self.subject_ref))
        object.__setattr__(self, "projection_status", _require_non_empty("projection_status", self.projection_status))
        object.__setattr__(self, "changed_files", _copy_tuple(self.changed_files))
        object.__setattr__(self, "mapped_cells", _copy_tuple(self.mapped_cells))
        object.__setattr__(self, "receipt_refs", _copy_tuple(self.receipt_refs))
        object.__setattr__(self, "reasons", _copy_tuple(self.reasons))
        if self.projection_request_id is not None:
            object.__setattr__(
                self,
                "projection_request_id",
                _require_non_empty("projection_request_id", self.projection_request_id),
            )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RecordRollbackLedgerCommandV1:
    workspace: str
    subject_ref: str
    reason: str
    decision_id: str | None = None
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "subject_ref", _require_non_empty("subject_ref", self.subject_ref))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        if self.decision_id is not None:
            object.__setattr__(self, "decision_id", _require_non_empty("decision_id", self.decision_id))
        object.__setattr__(self, "changed_files", _copy_tuple(self.changed_files))
        object.__setattr__(self, "receipt_refs", _copy_tuple(self.receipt_refs))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class GetRuntimeReceiptQueryV1:
    workspace: str
    receipt_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "receipt_id", _require_non_empty("receipt_id", self.receipt_id))


@dataclass(frozen=True)
class GetHandoffPackQueryV1:
    workspace: str
    handoff_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "handoff_id", _require_non_empty("handoff_id", self.handoff_id))


@dataclass(frozen=True)
class RehydrateHandoffPackCommandV1:
    workspace: str
    handoff_id: str
    target_role: str
    target_session_id: str | None = None
    turn_envelope: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "handoff_id", _require_non_empty("handoff_id", self.handoff_id))
        object.__setattr__(self, "target_role", _require_non_empty("target_role", self.target_role))
        if self.target_session_id is not None:
            object.__setattr__(
                self,
                "target_session_id",
                _require_non_empty("target_session_id", self.target_session_id),
            )
        object.__setattr__(self, "turn_envelope", _copy_mapping(self.turn_envelope))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class RuntimeReceiptIssuedEventV1:
    receipt_id: str
    receipt_type: str
    workspace: str
    created_at: str


@dataclass(frozen=True)
class HandoffPackExportedEventV1:
    handoff_id: str
    session_id: str
    workspace: str
    created_at: str


@dataclass(frozen=True)
class ResolveContextResultV1:
    ok: bool
    snapshot: ContextSnapshot | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class LeaseEditScopeResultV1:
    ok: bool
    lease: EditScopeLease | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ValidateChangeSetResultV1:
    ok: bool
    validation: ChangeSetValidationResult | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RuntimeReceiptResultV1:
    ok: bool
    receipt: RuntimeReceipt | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class HandoffPackResultV1:
    ok: bool
    handoff: ContextHandoffPack | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class HandoffRehydrationResultV1:
    ok: bool
    rehydration: HandoffRehydration | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class DiffCellMappingResultV1:
    ok: bool
    mapping: DiffCellMapping | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ProjectionCompileResultV1:
    ok: bool
    request: ProjectionCompileRequest | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PromotionDecisionResultV1:
    ok: bool
    decision: PromotionDecisionRecord | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RollbackLedgerResultV1:
    ok: bool
    entry: RollbackLedgerEntry | None = None
    error_code: str | None = None
    error_message: str | None = None


class CognitiveRuntimeErrorV1(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "cognitive_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _copy_mapping(details)


@runtime_checkable
class ICognitiveRuntimeService(Protocol):
    def resolve_context(self, command: ResolveContextCommandV1) -> ResolveContextResultV1: ...

    def lease_edit_scope(self, command: LeaseEditScopeCommandV1) -> LeaseEditScopeResultV1: ...

    def validate_change_set(self, command: ValidateChangeSetCommandV1) -> ValidateChangeSetResultV1: ...

    def record_runtime_receipt(self, command: RecordRuntimeReceiptCommandV1) -> RuntimeReceiptResultV1: ...

    def export_handoff_pack(self, command: ExportHandoffPackCommandV1) -> HandoffPackResultV1: ...

    def map_diff_to_cells(self, command: MapDiffToCellsCommandV1) -> DiffCellMappingResultV1: ...

    def request_projection_compile(
        self,
        command: RequestProjectionCompileCommandV1,
    ) -> ProjectionCompileResultV1: ...

    def promote_or_reject(self, command: PromoteOrRejectCommandV1) -> PromotionDecisionResultV1: ...

    def record_rollback_ledger(
        self,
        command: RecordRollbackLedgerCommandV1,
    ) -> RollbackLedgerResultV1: ...

    def get_runtime_receipt(self, query: GetRuntimeReceiptQueryV1) -> RuntimeReceiptResultV1: ...

    def get_handoff_pack(self, query: GetHandoffPackQueryV1) -> HandoffPackResultV1: ...

    def rehydrate_handoff_pack(
        self,
        command: RehydrateHandoffPackCommandV1,
    ) -> HandoffRehydrationResultV1: ...


__all__ = [
    "CognitiveRuntimeErrorV1",
    "DiffCellMappingResultV1",
    "ExportHandoffPackCommandV1",
    "GetHandoffPackQueryV1",
    "GetRuntimeReceiptQueryV1",
    "HandoffPackExportedEventV1",
    "HandoffPackResultV1",
    "HandoffRehydrationResultV1",
    "ICognitiveRuntimeService",
    "LeaseEditScopeCommandV1",
    "LeaseEditScopeResultV1",
    "MapDiffToCellsCommandV1",
    "ProjectionCompileResultV1",
    "PromoteOrRejectCommandV1",
    "PromotionDecisionResultV1",
    "RecordRollbackLedgerCommandV1",
    "RecordRuntimeReceiptCommandV1",
    "RehydrateHandoffPackCommandV1",
    "RequestProjectionCompileCommandV1",
    "ResolveContextCommandV1",
    "ResolveContextResultV1",
    "RollbackLedgerResultV1",
    "RuntimeReceiptIssuedEventV1",
    "RuntimeReceiptResultV1",
    "ValidateChangeSetCommandV1",
    "ValidateChangeSetResultV1",
]
