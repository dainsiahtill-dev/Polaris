"""Pure B3.4 replay candidate construction behind a permanent replay fence."""

from __future__ import annotations

from dataclasses import dataclass

from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    canonical_factory_physical_attempt_composite_hash,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceReplayCutoffV1,
    FactoryRoleEvidenceReplaySnapshotV1,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptCutoffViewV1,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    FactoryProviderAttemptLifecycleReplayFactV1,
    FactoryProviderAttemptLifecycleReplaySnapshotV1,
)

FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA = "factory.physical_attempt_replay_fence.v1"
FACTORY_PHYSICAL_ATTEMPT_REPLAY_POLICY_SCHEMA = "factory.physical_attempt_replay_policy.v1"
FACTORY_PHYSICAL_ATTEMPT_REPLAY_RECORD_SCHEMA = "factory.physical_attempt_replay_record.v1"
FACTORY_PHYSICAL_ATTEMPT_REPLAY_CANDIDATE_SCHEMA = "factory.physical_attempt_replay_candidate.v1"


class FactoryPhysicalAttemptReplayError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


def _hash64(field_name: str, value: object) -> str:
    normalized = _text(field_name, value)
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name}_invalid")
    return normalized


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptReplayPolicyV1:
    schema_version: str = FACTORY_PHYSICAL_ATTEMPT_REPLAY_POLICY_SCHEMA
    max_full_replays: int = 3
    total_deadline_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PHYSICAL_ATTEMPT_REPLAY_POLICY_SCHEMA:
            raise ValueError("factory_physical_attempt_replay_policy_schema_mismatch")
        if self.max_full_replays != 3 or self.total_deadline_seconds != 30.0:
            raise ValueError("factory_physical_attempt_replay_policy_is_immutable")


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptReplayFenceV1:
    schema_version: str
    factory_run_id: str
    factory_stage_head_sequence: int
    factory_stage_head_hash: str
    workspace_fencing_token: int
    current_stage: str
    fence_kind: str
    fence_sequence: int
    fence_nonce: str
    replay_fenced: bool
    live_mutation_forbidden: bool

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA:
            raise ValueError("factory_physical_attempt_replay_fence_schema_mismatch")
        object.__setattr__(self, "factory_run_id", _text("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "current_stage", _text("current_stage", self.current_stage))
        fence_kind = _text("fence_kind", self.fence_kind)
        if fence_kind not in {"stage_claim", "lifecycle_operation"}:
            raise ValueError("factory_physical_attempt_replay_fence_kind_invalid")
        object.__setattr__(self, "fence_kind", fence_kind)
        object.__setattr__(self, "fence_nonce", _text("fence_nonce", self.fence_nonce))
        for field_name in ("factory_stage_head_sequence", "workspace_fencing_token", "fence_sequence"):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field_name}_invalid")
        object.__setattr__(
            self,
            "factory_stage_head_hash",
            _hash64("factory_stage_head_hash", self.factory_stage_head_hash),
        )
        if self.replay_fenced is not True or self.live_mutation_forbidden is not True:
            raise ValueError("factory_physical_attempt_replay_fence_required")


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptReplayRecordV1:
    schema_version: str
    cutoff: FactoryRoleEvidenceReplayCutoffV1
    start: FactoryProviderAttemptLifecycleReplayFactV1
    terminal: FactoryProviderAttemptLifecycleReplayFactV1 | None

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PHYSICAL_ATTEMPT_REPLAY_RECORD_SCHEMA:
            raise ValueError("factory_physical_attempt_replay_record_schema_mismatch")
        if type(self.cutoff) is not FactoryRoleEvidenceReplayCutoffV1:
            raise TypeError("factory_role_evidence_replay_cutoff_exact_type_required")
        if type(self.start) is not FactoryProviderAttemptLifecycleReplayFactV1 or self.start.phase != "start":
            raise TypeError("factory_provider_attempt_replay_start_exact_type_required")
        if self.terminal is not None and (
            type(self.terminal) is not FactoryProviderAttemptLifecycleReplayFactV1
            or self.terminal.phase != "terminal"
            or self.terminal.provider_request_id != self.start.provider_request_id
        ):
            raise TypeError("factory_provider_attempt_replay_terminal_exact_type_required")

    @property
    def recovery_terminal_required(self) -> bool:
        return self.terminal is None


@dataclass(frozen=True, slots=True)
class FactoryPhysicalAttemptReplayCandidateV1:
    schema_version: str
    fence: FactoryPhysicalAttemptReplayFenceV1
    role_evidence: FactoryRoleEvidenceReplaySnapshotV1
    lifecycle: FactoryProviderAttemptLifecycleReplaySnapshotV1
    records: tuple[FactoryPhysicalAttemptReplayRecordV1, ...]
    permanently_dead_for_admission: bool
    outbound_count: int

    def __post_init__(self) -> None:
        if self.schema_version != FACTORY_PHYSICAL_ATTEMPT_REPLAY_CANDIDATE_SCHEMA:
            raise ValueError("factory_physical_attempt_replay_candidate_schema_mismatch")
        if type(self.fence) is not FactoryPhysicalAttemptReplayFenceV1:
            raise TypeError("factory_physical_attempt_replay_fence_exact_type_required")
        if type(self.role_evidence) is not FactoryRoleEvidenceReplaySnapshotV1:
            raise TypeError("factory_role_evidence_replay_snapshot_exact_type_required")
        if type(self.lifecycle) is not FactoryProviderAttemptLifecycleReplaySnapshotV1:
            raise TypeError("factory_provider_attempt_lifecycle_replay_snapshot_exact_type_required")
        run_ids = {self.fence.factory_run_id, self.role_evidence.factory_run_id, self.lifecycle.factory_run_id}
        workspaces = {self.role_evidence.workspace, self.lifecycle.workspace}
        if len(run_ids) != 1 or len(workspaces) != 1:
            raise ValueError("factory_physical_attempt_replay_candidate_scope_mismatch")
        if type(self.records) is not tuple or any(
            type(record) is not FactoryPhysicalAttemptReplayRecordV1 for record in self.records
        ):
            raise TypeError("factory_physical_attempt_replay_records_exact_tuple_required")
        if self.permanently_dead_for_admission is not True or self.outbound_count != 0:
            raise ValueError("factory_physical_attempt_replay_candidate_must_be_non_dispatching")


def build_factory_physical_attempt_replay_candidate(
    *,
    fence: FactoryPhysicalAttemptReplayFenceV1,
    role_evidence: FactoryRoleEvidenceReplaySnapshotV1,
    lifecycle: FactoryProviderAttemptLifecycleReplaySnapshotV1,
) -> FactoryPhysicalAttemptReplayCandidateV1:
    """Exact-match lifecycle facts to detached Factory cutoff authority."""

    FactoryPhysicalAttemptReplayFenceV1.__post_init__(fence)
    FactoryRoleEvidenceReplaySnapshotV1.__post_init__(role_evidence)
    FactoryProviderAttemptLifecycleReplaySnapshotV1.__post_init__(lifecycle)
    if (
        fence.factory_run_id != role_evidence.factory_run_id
        or fence.factory_run_id != lifecycle.factory_run_id
        or role_evidence.workspace != lifecycle.workspace
    ):
        raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_scope_mismatch")

    cutoff_by_freeze = {cutoff.body.request.request_freeze_id: cutoff for cutoff in role_evidence.cutoffs}
    terminal_by_request = {fact.provider_request_id: fact for fact in lifecycle.facts if fact.phase == "terminal"}
    last_ordinal_by_authority: dict[str, int] = {}
    committed_by_authority: dict[str, int] = {}
    authority_identity: dict[str, tuple[object, ...]] = {}
    records: list[FactoryPhysicalAttemptReplayRecordV1] = []
    for start in (fact for fact in lifecycle.facts if fact.phase == "start"):
        cutoff = cutoff_by_freeze.get(start.request_freeze_id)
        if cutoff is None:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_cutoff_missing")
        _validate_start_against_cutoff(start, cutoff)
        authority_hash = start.execution_authority_hash
        identity = (
            cutoff.body.request.role,
            cutoff.body.request.run_id,
            cutoff.body.request.attempt_budget,
            cutoff.body.authority.stage,
            cutoff.body.authority.workspace_fencing_token,
            cutoff.body.authority.stage_claim_attempt,
            cutoff.body.authority.stage_claim_nonce,
        )
        existing_identity = authority_identity.setdefault(authority_hash, identity)
        if existing_identity != identity:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_cross_view_identity")
        previous_ordinal = last_ordinal_by_authority.get(authority_hash, 0)
        if start.authority_attempt_ordinal <= previous_ordinal:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_ordinal_regression")
        last_ordinal_by_authority[authority_hash] = start.authority_attempt_ordinal
        committed = committed_by_authority.get(authority_hash, 0) + 1
        if committed > start.attempt_budget:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_budget_overcommitted")
        committed_by_authority[authority_hash] = committed
        records.append(
            FactoryPhysicalAttemptReplayRecordV1(
                schema_version=FACTORY_PHYSICAL_ATTEMPT_REPLAY_RECORD_SCHEMA,
                cutoff=cutoff,
                start=start,
                terminal=terminal_by_request.get(start.provider_request_id),
            )
        )
    return FactoryPhysicalAttemptReplayCandidateV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_REPLAY_CANDIDATE_SCHEMA,
        fence=fence,
        role_evidence=role_evidence,
        lifecycle=lifecycle,
        records=tuple(records),
        permanently_dead_for_admission=True,
        outbound_count=0,
    )


def _validate_start_against_cutoff(
    start: FactoryProviderAttemptLifecycleReplayFactV1,
    cutoff: FactoryRoleEvidenceReplayCutoffV1,
) -> None:
    request = cutoff.body.request
    authority = cutoff.body.authority
    if (
        start.factory_run_id != cutoff.body.factory_run_id
        or start.run_id != request.run_id
        or start.role != request.role
        or start.turn_id != request.turn_id
        or start.call_id != request.call_id
        or start.request_freeze_id != request.request_freeze_id
        or start.execution_authority_hash != request.execution_authority_hash
        or start.attempt_budget != request.attempt_budget
        or start.semantic_request_hash != request.semantic_candidate_hash
    ):
        raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_lifecycle_identity_mismatch")
    grant = FactoryPhysicalAttemptGrantViewV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
        verification_scope="factory",
        factory_run_id=cutoff.body.factory_run_id,
        role=request.role,
        stage=authority.stage,
        workspace_fencing_token=authority.workspace_fencing_token,
        stage_claim_attempt=authority.stage_claim_attempt,
        stage_claim_nonce=authority.stage_claim_nonce,
        execution_authority_hash=request.execution_authority_hash,
        attempt_budget=request.attempt_budget,
    )
    cutoff_view = FactoryPhysicalAttemptCutoffViewV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
        grant=grant,
        run_id=request.run_id,
        turn_id=request.turn_id,
        call_id=request.call_id,
        request_freeze_id=request.request_freeze_id,
        provider=start.provider,
        model=start.model,
        semantic_request_hash=start.semantic_request_hash,
        physical_wire_hash=start.physical_wire_hash,
    )
    expected_composite = canonical_factory_physical_attempt_composite_hash(
        cutoff_view,
        start.authority_attempt_ordinal,
    )
    if start.composite_request_hash != expected_composite:
        raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_composite_hash_mismatch")


__all__ = [
    "FACTORY_PHYSICAL_ATTEMPT_REPLAY_CANDIDATE_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_REPLAY_POLICY_SCHEMA",
    "FACTORY_PHYSICAL_ATTEMPT_REPLAY_RECORD_SCHEMA",
    "FactoryPhysicalAttemptReplayCandidateV1",
    "FactoryPhysicalAttemptReplayError",
    "FactoryPhysicalAttemptReplayFenceV1",
    "FactoryPhysicalAttemptReplayPolicyV1",
    "FactoryPhysicalAttemptReplayRecordV1",
    "build_factory_physical_attempt_replay_candidate",
]
