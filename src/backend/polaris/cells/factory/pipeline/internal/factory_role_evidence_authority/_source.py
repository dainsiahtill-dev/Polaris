"""Source-authority protocols, fact-stream adapters, and scan storage types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
    append_segmented_fact_event,
    ensure_segmented_fact_ledger,
    query_segmented_fact_events,
    query_segmented_fact_ledger_head,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryRun
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceCutoffRequestV1,
)
from polaris.kernelone.events.final_request_evidence import (
    canonical_role_final_request_hash,
)

from ._models import (
    FactoryRoleEvidenceCutoffBodyV1,
    FactoryRoleEvidenceResolvedCutV1,
    FactoryRoleEvidenceStageAuthorityV1,
    _CutoffFragmentPayload,
)
from ._primitives import FactoryRoleEvidenceAuthorityError


class FactoryRoleEvidenceSourceAuthority(Protocol):
    """Synchronous Factory-owned source resolver boundary for A009B2."""

    def resolve_source_cut(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        factory_run: FactoryRun,
    ) -> FactoryRoleEvidenceResolvedCutV1:
        """Capture canonical source facts and heads while the claim lock is held."""


class UnavailableFactoryRoleEvidenceSourceAuthority:
    """Production A009B1 default: no source authority until A009B2 exists."""

    def resolve_source_cut(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        factory_run: FactoryRun,
    ) -> FactoryRoleEvidenceResolvedCutV1:
        del request, authority, factory_run
        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_authority_unavailable")


class FactoryRoleEvidenceFactStream(Protocol):
    def ensure(self, command: EnsureSegmentedFactLedgerCommandV1) -> SegmentedFactLedgerReadyV1: ...

    def query_events(self, query: QuerySegmentedFactEventsV1) -> SegmentedFactQueryResultV1: ...

    def query_head(self, query: QuerySegmentedFactLedgerHeadV1) -> SegmentedFactLedgerHeadV1: ...

    def append(self, command: AppendSegmentedFactEventCommandV1) -> SegmentedFactEventAppendedV1: ...


class _PublicFactoryRoleEvidenceFactStream:
    def ensure(self, command: EnsureSegmentedFactLedgerCommandV1) -> SegmentedFactLedgerReadyV1:
        return ensure_segmented_fact_ledger(command)

    def query_events(self, query: QuerySegmentedFactEventsV1) -> SegmentedFactQueryResultV1:
        return query_segmented_fact_events(query)

    def query_head(self, query: QuerySegmentedFactLedgerHeadV1) -> SegmentedFactLedgerHeadV1:
        return query_segmented_fact_ledger_head(query)

    def append(self, command: AppendSegmentedFactEventCommandV1) -> SegmentedFactEventAppendedV1:
        return append_segmented_fact_event(command)


@dataclass(frozen=True, slots=True)
class _StoredFragment:
    event_id: str
    sequence: int
    event_hash: str
    payload: _CutoffFragmentPayload


@dataclass(frozen=True, slots=True)
class _PartialCutoff:
    request_authority_hash: str
    body_hash: str
    fragment_count: int
    fragments: tuple[_StoredFragment, ...]
    body: FactoryRoleEvidenceCutoffBodyV1 | None
    fragment_vector_hash: str | None


@dataclass(frozen=True, slots=True)
class _StoredCutoff:
    event_id: str
    sequence: int
    event_hash: str
    body_hash: str
    body: FactoryRoleEvidenceCutoffBodyV1
    fragment_count: int
    fragment_vector_hash: str


@dataclass(frozen=True, slots=True)
class _AuthorityScan:
    stored: dict[str, _StoredCutoff]
    partial: dict[str, _PartialCutoff]
    captured_head: SegmentedFactLedgerHeadV1


def _fragment_vector_hash(fragments: tuple[_StoredFragment, ...]) -> str:
    return canonical_role_final_request_hash(
        [
            {
                "index": fragment.payload.index,
                "event_id": fragment.event_id,
                "global_seq": fragment.sequence,
                "event_hash": fragment.event_hash,
                "chunk_hash": fragment.payload.chunk_hash,
            }
            for fragment in fragments
        ]
    )
