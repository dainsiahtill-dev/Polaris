from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _copy_mapping(payload: dict[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _copy_mapping_tuple(
    values: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    for value in values or ():
        if not isinstance(value, dict):
            continue
        normalized.append(dict(value))
    return tuple(normalized)


def _copy_tuple(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(str(value) for value in (values or []) if str(value).strip())


def _copy_optional_str(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _copy_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_structured_findings(value: Any) -> StructuredFindings | None:
    if not isinstance(value, dict):
        return None
    return StructuredFindings(
        confirmed_facts=[str(v) for v in value.get("confirmed_facts", []) if isinstance(v, str)],
        rejected_hypotheses=[str(v) for v in value.get("rejected_hypotheses", []) if isinstance(v, str)],
        open_questions=[str(v) for v in value.get("open_questions", []) if isinstance(v, str)],
        relevant_refs=[str(v) for v in value.get("relevant_refs", []) if isinstance(v, str)],
        source_turn_id=str(value.get("source_turn_id") or ""),
        extracted_at=str(value.get("extracted_at") or ""),
    )


@dataclass(frozen=True)
class ContextSnapshot:
    workspace: str
    role: str
    query: str
    run_id: str
    step: int
    mode: str
    session_id: str | None = None
    rendered_prompt: str = ""
    token_usage_estimate: int = 0
    source_refs: tuple[str, ...] = field(default_factory=tuple)
    context_os_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurnEnvelope:
    turn_id: str
    projection_version: str | None = None
    lease_id: str | None = None
    validation_id: str | None = None
    receipt_ids: tuple[str, ...] = field(default_factory=tuple)
    session_id: str | None = None
    run_id: str | None = None
    role: str | None = None
    task_id: str | None = None
    state_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "projection_version": self.projection_version,
            "lease_id": self.lease_id,
            "validation_id": self.validation_id,
            "receipt_ids": list(self.receipt_ids),
            "session_id": self.session_id,
            "run_id": self.run_id,
            "role": self.role,
            "task_id": self.task_id,
            "state_version": self.state_version,
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> TurnEnvelope | None:
        if not isinstance(payload, dict):
            return None
        turn_id = str(payload.get("turn_id") or "").strip()
        if not turn_id:
            return None
        return cls(
            turn_id=turn_id,
            projection_version=_copy_optional_str(payload.get("projection_version")),
            lease_id=_copy_optional_str(payload.get("lease_id")),
            validation_id=_copy_optional_str(payload.get("validation_id")),
            receipt_ids=_copy_tuple(payload.get("receipt_ids")),
            session_id=_copy_optional_str(payload.get("session_id")),
            run_id=_copy_optional_str(payload.get("run_id")),
            role=_copy_optional_str(payload.get("role")),
            task_id=_copy_optional_str(payload.get("task_id")),
            state_version=_copy_optional_int(payload.get("state_version")),
        )

    def with_receipt_ids(
        self,
        receipt_ids: list[str] | tuple[str, ...] | None,
    ) -> TurnEnvelope:
        merged = list(self.receipt_ids)
        for value in receipt_ids or ():
            token = str(value or "").strip()
            if token and token not in merged:
                merged.append(token)
        return TurnEnvelope(
            turn_id=self.turn_id,
            projection_version=self.projection_version,
            lease_id=self.lease_id,
            validation_id=self.validation_id,
            receipt_ids=tuple(merged),
            session_id=self.session_id,
            run_id=self.run_id,
            role=self.role,
            task_id=self.task_id,
            state_version=self.state_version,
        )


@dataclass(frozen=True)
class EditScopeLease:
    lease_id: str
    workspace: str
    requested_by: str
    scope_paths: tuple[str, ...]
    issued_at: str
    expires_at: str
    reason: str = ""
    session_id: str | None = None
    current_goal: str = ""
    hard_constraints: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChangeSetValidationResult:
    validation_id: str | None
    workspace: str
    changed_files: tuple[str, ...]
    allowed_scope_paths: tuple[str, ...]
    write_gate_allowed: bool
    impact_score: int
    risk_level: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    extra_files: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.write_gate_allowed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeReceipt:
    receipt_id: str
    receipt_type: str
    workspace: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    run_id: str | None = None
    trace_refs: tuple[str, ...] = field(default_factory=tuple)
    turn_envelope: TurnEnvelope | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["turn_envelope"] = self.turn_envelope.to_dict() if self.turn_envelope is not None else None
        return payload

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> RuntimeReceipt | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            receipt_id=str(payload.get("receipt_id") or ""),
            receipt_type=str(payload.get("receipt_type") or ""),
            workspace=str(payload.get("workspace") or ""),
            created_at=str(payload.get("created_at") or ""),
            payload=_copy_mapping(payload.get("payload")),
            session_id=str(payload["session_id"]) if payload.get("session_id") else None,
            run_id=str(payload["run_id"]) if payload.get("run_id") else None,
            trace_refs=_copy_tuple(payload.get("trace_refs")),
            turn_envelope=TurnEnvelope.from_mapping(payload.get("turn_envelope")),
        )


@dataclass(frozen=True)
class StructuredFindings:
    """跨 turn 认知状态传递的最小四要素。"""

    confirmed_facts: list[str] = field(default_factory=list)
    rejected_hypotheses: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    relevant_refs: list[str] = field(default_factory=list)
    source_turn_id: str = ""
    extracted_at: str = ""


@dataclass(frozen=True)
class ContextHandoffPack:
    handoff_id: str
    workspace: str
    created_at: str
    session_id: str
    reason: str = ""
    run_id: str | None = None
    current_goal: str = ""
    hard_constraints: tuple[str, ...] = field(default_factory=tuple)
    open_loops: tuple[str, ...] = field(default_factory=tuple)
    run_card: dict[str, Any] = field(default_factory=dict)
    context_slice_plan: dict[str, Any] = field(default_factory=dict)
    decision_log: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    episode_refs: tuple[str, ...] = field(default_factory=tuple)
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    source_spans: tuple[str, ...] = field(default_factory=tuple)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    turn_envelope: TurnEnvelope | None = None
    checkpoint_state: dict[str, Any] = field(default_factory=dict)
    pending_receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    suggestion_rankings: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    lease_token: str | None = None
    # Phase 1: Structured findings for cognitive continuity
    structured_findings: StructuredFindings | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["turn_envelope"] = self.turn_envelope.to_dict() if self.turn_envelope is not None else None
        return payload

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextHandoffPack | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            handoff_id=str(payload.get("handoff_id") or ""),
            workspace=str(payload.get("workspace") or ""),
            created_at=str(payload.get("created_at") or ""),
            session_id=str(payload.get("session_id") or ""),
            reason=str(payload.get("reason") or ""),
            run_id=str(payload["run_id"]) if payload.get("run_id") else None,
            current_goal=str(payload.get("current_goal") or ""),
            hard_constraints=_copy_tuple(payload.get("hard_constraints")),
            open_loops=_copy_tuple(payload.get("open_loops")),
            run_card=_copy_mapping(payload.get("run_card")),
            context_slice_plan=_copy_mapping(payload.get("context_slice_plan")),
            decision_log=_copy_mapping_tuple(payload.get("decision_log")),
            artifact_refs=_copy_tuple(payload.get("artifact_refs")),
            episode_refs=_copy_tuple(payload.get("episode_refs")),
            receipt_refs=_copy_tuple(payload.get("receipt_refs")),
            source_spans=_copy_tuple(payload.get("source_spans")),
            state_snapshot=_copy_mapping(payload.get("state_snapshot")),
            turn_envelope=TurnEnvelope.from_mapping(payload.get("turn_envelope")),
            checkpoint_state=_copy_mapping(payload.get("checkpoint_state")),
            pending_receipt_refs=_copy_tuple(payload.get("pending_receipt_refs")),
            suggestion_rankings=_copy_mapping_tuple(payload.get("suggestion_rankings")),
            lease_token=_copy_optional_str(payload.get("lease_token")),
            structured_findings=_parse_structured_findings(payload.get("structured_findings")),
        )


@dataclass(frozen=True)
class HandoffRehydration:
    rehydration_id: str
    handoff_id: str
    workspace: str
    created_at: str
    target_role: str
    target_session_id: str | None = None
    current_goal: str = ""
    hard_constraints: tuple[str, ...] = field(default_factory=tuple)
    open_loops: tuple[str, ...] = field(default_factory=tuple)
    run_card: dict[str, Any] = field(default_factory=dict)
    context_slice_plan: dict[str, Any] = field(default_factory=dict)
    decision_log: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    episode_refs: tuple[str, ...] = field(default_factory=tuple)
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    source_spans: tuple[str, ...] = field(default_factory=tuple)
    context_override: dict[str, Any] = field(default_factory=dict)
    metadata_patch: dict[str, Any] = field(default_factory=dict)
    turn_envelope: TurnEnvelope | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["turn_envelope"] = self.turn_envelope.to_dict() if self.turn_envelope is not None else None
        return payload

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> HandoffRehydration | None:
        if not isinstance(payload, dict):
            return None
        rehydration_id = str(payload.get("rehydration_id") or "").strip()
        handoff_id = str(payload.get("handoff_id") or "").strip()
        workspace = str(payload.get("workspace") or "").strip()
        created_at = str(payload.get("created_at") or "").strip()
        target_role = str(payload.get("target_role") or "").strip()
        if not (rehydration_id and handoff_id and workspace and created_at and target_role):
            return None
        return cls(
            rehydration_id=rehydration_id,
            handoff_id=handoff_id,
            workspace=workspace,
            created_at=created_at,
            target_role=target_role,
            target_session_id=_copy_optional_str(payload.get("target_session_id")),
            current_goal=str(payload.get("current_goal") or "").strip(),
            hard_constraints=_copy_tuple(payload.get("hard_constraints")),
            open_loops=_copy_tuple(payload.get("open_loops")),
            run_card=_copy_mapping(payload.get("run_card")),
            context_slice_plan=_copy_mapping(payload.get("context_slice_plan")),
            decision_log=_copy_mapping_tuple(payload.get("decision_log")),
            artifact_refs=_copy_tuple(payload.get("artifact_refs")),
            episode_refs=_copy_tuple(payload.get("episode_refs")),
            receipt_refs=_copy_tuple(payload.get("receipt_refs")),
            source_spans=_copy_tuple(payload.get("source_spans")),
            context_override=_copy_mapping(payload.get("context_override")),
            metadata_patch=_copy_mapping(payload.get("metadata_patch")),
            turn_envelope=TurnEnvelope.from_mapping(payload.get("turn_envelope")),
        )


@dataclass(frozen=True)
class DiffCellMapping:
    mapping_id: str
    workspace: str
    created_at: str
    graph_catalog_path: str
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    matched_cells: tuple[str, ...] = field(default_factory=tuple)
    unmapped_files: tuple[str, ...] = field(default_factory=tuple)
    file_to_cells: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_id": self.mapping_id,
            "workspace": self.workspace,
            "created_at": self.created_at,
            "graph_catalog_path": self.graph_catalog_path,
            "changed_files": list(self.changed_files),
            "matched_cells": list(self.matched_cells),
            "unmapped_files": list(self.unmapped_files),
            "file_to_cells": {k: list(v) for k, v in self.file_to_cells.items()},
            "notes": list(self.notes),
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> DiffCellMapping | None:
        if not isinstance(payload, dict):
            return None
        file_to_cells_raw = payload.get("file_to_cells")
        normalized_file_to_cells: dict[str, tuple[str, ...]] = {}
        if isinstance(file_to_cells_raw, dict):
            for key, value in file_to_cells_raw.items():
                normalized_key = str(key or "").strip()
                if not normalized_key:
                    continue
                normalized_file_to_cells[normalized_key] = _copy_tuple(value)
        return cls(
            mapping_id=str(payload.get("mapping_id") or ""),
            workspace=str(payload.get("workspace") or ""),
            created_at=str(payload.get("created_at") or ""),
            graph_catalog_path=str(payload.get("graph_catalog_path") or ""),
            changed_files=_copy_tuple(payload.get("changed_files")),
            matched_cells=_copy_tuple(payload.get("matched_cells")),
            unmapped_files=_copy_tuple(payload.get("unmapped_files")),
            file_to_cells=normalized_file_to_cells,
            notes=_copy_tuple(payload.get("notes")),
        )


@dataclass(frozen=True)
class ProjectionCompileRequest:
    request_id: str
    workspace: str
    created_at: str
    requested_by: str
    subject_ref: str
    status: str
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    mapped_cells: tuple[str, ...] = field(default_factory=tuple)
    session_id: str | None = None
    run_id: str | None = None
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ProjectionCompileRequest | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            request_id=str(payload.get("request_id") or ""),
            workspace=str(payload.get("workspace") or ""),
            created_at=str(payload.get("created_at") or ""),
            requested_by=str(payload.get("requested_by") or ""),
            subject_ref=str(payload.get("subject_ref") or ""),
            status=str(payload.get("status") or ""),
            changed_files=_copy_tuple(payload.get("changed_files")),
            mapped_cells=_copy_tuple(payload.get("mapped_cells")),
            session_id=str(payload["session_id"]) if payload.get("session_id") else None,
            run_id=str(payload["run_id"]) if payload.get("run_id") else None,
            receipt_refs=_copy_tuple(payload.get("receipt_refs")),
            metadata=_copy_mapping(payload.get("metadata")),
        )


@dataclass(frozen=True)
class PromotionDecisionRecord:
    decision_id: str
    workspace: str
    created_at: str
    subject_ref: str
    decision: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    mapped_cells: tuple[str, ...] = field(default_factory=tuple)
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    projection_request_id: str | None = None
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> PromotionDecisionRecord | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            decision_id=str(payload.get("decision_id") or ""),
            workspace=str(payload.get("workspace") or ""),
            created_at=str(payload.get("created_at") or ""),
            subject_ref=str(payload.get("subject_ref") or ""),
            decision=str(payload.get("decision") or ""),
            reasons=_copy_tuple(payload.get("reasons")),
            mapped_cells=_copy_tuple(payload.get("mapped_cells")),
            changed_files=_copy_tuple(payload.get("changed_files")),
            projection_request_id=(
                str(payload["projection_request_id"]) if payload.get("projection_request_id") else None
            ),
            receipt_refs=_copy_tuple(payload.get("receipt_refs")),
            metadata=_copy_mapping(payload.get("metadata")),
        )


@dataclass(frozen=True)
class RollbackLedgerEntry:
    rollback_id: str
    workspace: str
    created_at: str
    subject_ref: str
    reason: str
    decision_id: str | None = None
    changed_files: tuple[str, ...] = field(default_factory=tuple)
    receipt_refs: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> RollbackLedgerEntry | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            rollback_id=str(payload.get("rollback_id") or ""),
            workspace=str(payload.get("workspace") or ""),
            created_at=str(payload.get("created_at") or ""),
            subject_ref=str(payload.get("subject_ref") or ""),
            reason=str(payload.get("reason") or ""),
            decision_id=str(payload["decision_id"]) if payload.get("decision_id") else None,
            changed_files=_copy_tuple(payload.get("changed_files")),
            receipt_refs=_copy_tuple(payload.get("receipt_refs")),
            metadata=_copy_mapping(payload.get("metadata")),
        )


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    proposal_type: str
    subject_ref: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconcileResult:
    ok: bool
    proposal_id: str
    decision: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FitnessSpec:
    spec_id: str
    criteria: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionDecision:
    decision_id: str
    subject_ref: str
    status: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
