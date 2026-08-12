"""Replay snapshot models and query for factory role-evidence cutoffs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polaris.cells.events.fact_stream.public import (
    SegmentedFactLedgerHeadV1,
)

from ._models import FactoryRoleEvidenceCutoffBodyV1, FactoryRoleEvidenceStageAuthorityV1
from ._port import FactoryRoleEvidenceAuthorityPort
from ._primitives import (
    FactoryRoleEvidenceAuthorityError,
    _hash64,
    _locator,
    _positive_int,
    factory_role_evidence_authority_stream,
)
from ._source import (
    FactoryRoleEvidenceFactStream,
    _PublicFactoryRoleEvidenceFactStream,
)


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceReplayCutoffV1:
    """One detached committed cutoff fact; carries no live grant capability."""

    cutoff_fact_id: str
    cutoff_sequence: int
    cutoff_event_hash: str
    cutoff_body_hash: str
    cutoff_fragment_vector_hash: str
    cutoff_fragment_count: int
    body: FactoryRoleEvidenceCutoffBodyV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "cutoff_fact_id", _locator("cutoff_fact_id", self.cutoff_fact_id))
        object.__setattr__(self, "cutoff_sequence", _positive_int("cutoff_sequence", self.cutoff_sequence))
        object.__setattr__(self, "cutoff_event_hash", _hash64("cutoff_event_hash", self.cutoff_event_hash))
        object.__setattr__(self, "cutoff_body_hash", _hash64("cutoff_body_hash", self.cutoff_body_hash))
        object.__setattr__(
            self,
            "cutoff_fragment_vector_hash",
            _hash64("cutoff_fragment_vector_hash", self.cutoff_fragment_vector_hash),
        )
        object.__setattr__(
            self,
            "cutoff_fragment_count",
            _positive_int("cutoff_fragment_count", self.cutoff_fragment_count),
        )
        if type(self.body) is not FactoryRoleEvidenceCutoffBodyV1:
            raise TypeError("factory_role_evidence_cutoff_body_exact_type_required")
        FactoryRoleEvidenceCutoffBodyV1.__post_init__(self.body)


@dataclass(frozen=True, slots=True)
class FactoryRoleEvidenceReplaySnapshotV1:
    """Strict authority-ledger snapshot at one immutable captured head."""

    workspace: str
    factory_run_id: str
    logical_stream: str
    captured_head: SegmentedFactLedgerHeadV1
    cutoffs: tuple[FactoryRoleEvidenceReplayCutoffV1, ...]

    def __post_init__(self) -> None:
        workspace = str(Path(self.workspace).resolve())
        object.__setattr__(self, "workspace", workspace)
        factory_run_id = _locator("factory_run_id", self.factory_run_id)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        expected_stream = factory_role_evidence_authority_stream(factory_run_id)
        if self.logical_stream != expected_stream:
            raise ValueError("factory_role_evidence_replay_stream_mismatch")
        if type(self.captured_head) is not SegmentedFactLedgerHeadV1:
            raise TypeError("segmented_fact_ledger_head_exact_type_required")
        SegmentedFactLedgerHeadV1.__post_init__(self.captured_head)
        if self.captured_head.workspace != workspace or self.captured_head.logical_stream != self.logical_stream:
            raise ValueError("factory_role_evidence_replay_head_mismatch")
        if type(self.cutoffs) is not tuple or any(
            type(cutoff) is not FactoryRoleEvidenceReplayCutoffV1 for cutoff in self.cutoffs
        ):
            raise TypeError("factory_role_evidence_replay_cutoffs_exact_tuple_required")
        seen_freezes: set[str] = set()
        previous_sequence = 0
        for cutoff in self.cutoffs:
            FactoryRoleEvidenceReplayCutoffV1.__post_init__(cutoff)
            if cutoff.body.factory_run_id != factory_run_id:
                raise ValueError("factory_role_evidence_replay_factory_run_mismatch")
            freeze_id = cutoff.body.request.request_freeze_id
            if freeze_id in seen_freezes or cutoff.cutoff_sequence <= previous_sequence:
                raise ValueError("factory_role_evidence_replay_duplicate_or_regressing_cutoff")
            seen_freezes.add(freeze_id)
            previous_sequence = cutoff.cutoff_sequence


class _FactoryRoleEvidenceReplayScanReader(FactoryRoleEvidenceAuthorityPort):
    """Read-only reuse of the exact live cutoff ledger codec and validators."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        factory_run_id: str,
        fact_stream: FactoryRoleEvidenceFactStream,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._authority = FactoryRoleEvidenceStageAuthorityV1(
            factory_run_id=factory_run_id,
            stage="physical_attempt_replay_fence",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="replay-reader-no-live-claim",
        )
        self._facts = fact_stream
        self._logical_stream = factory_role_evidence_authority_stream(factory_run_id)


def query_factory_role_evidence_replay_snapshot(
    *,
    workspace: str | Path,
    factory_run_id: str,
    fact_stream: FactoryRoleEvidenceFactStream | None = None,
) -> FactoryRoleEvidenceReplaySnapshotV1:
    """Strictly read all committed cutoff facts without creating live authority."""

    normalized_run_id = _locator("factory_run_id", factory_run_id)
    reader = _FactoryRoleEvidenceReplayScanReader(
        workspace=workspace,
        factory_run_id=normalized_run_id,
        fact_stream=fact_stream or _PublicFactoryRoleEvidenceFactStream(),
    )
    scan = reader._scan_authority_events()
    if scan.partial:
        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_replay_partial_cutoff")
    cutoffs = tuple(
        FactoryRoleEvidenceReplayCutoffV1(
            cutoff_fact_id=stored.event_id,
            cutoff_sequence=stored.sequence,
            cutoff_event_hash=stored.event_hash,
            cutoff_body_hash=stored.body_hash,
            cutoff_fragment_vector_hash=stored.fragment_vector_hash,
            cutoff_fragment_count=stored.fragment_count,
            body=stored.body,
        )
        for stored in sorted(scan.stored.values(), key=lambda item: item.sequence)
    )
    return FactoryRoleEvidenceReplaySnapshotV1(
        workspace=str(Path(workspace).resolve()),
        factory_run_id=normalized_run_id,
        logical_stream=reader._logical_stream,
        captured_head=scan.captured_head,
        cutoffs=cutoffs,
    )
