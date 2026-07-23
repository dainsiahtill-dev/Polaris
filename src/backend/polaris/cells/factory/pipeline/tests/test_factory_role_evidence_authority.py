"""A009B1 fenced Factory role-evidence cutoff authority tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from polaris.cells.events.fact_stream.public import (
    ProvisionFactStreamLockAuthorityCommandV1,
    QuerySegmentedFactEventsV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
    provision_fact_stream_lock_authority,
    query_segmented_fact_events,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptLiveControlPort,
    canonical_factory_physical_attempt_composite_hash,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_replay import (
    FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA,
    FactoryPhysicalAttemptReplayError,
    FactoryPhysicalAttemptReplayFenceV1,
    build_factory_physical_attempt_replay_candidate,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
    FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
    FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
    FactoryRoleEvidenceAuthorityError,
    FactoryRoleEvidenceAuthorityPort,
    FactoryRoleEvidenceCutoffBodyV1,
    FactoryRoleEvidenceReplaySnapshotV1,
    FactoryRoleEvidenceResolvedCutV1,
    FactoryRoleEvidenceSourceHeadV1,
    FactoryRoleEvidenceSourceItemV1,
    FactoryRoleEvidenceSourceSlotV1,
    FactoryRoleEvidenceStageAuthorityV1,
    UnavailableFactoryRoleEvidenceSourceAuthority,
    _canonical_cutoff_body_bytes,
    _CutoffCommitManifest,
    _CutoffFragmentPayload,
    _fragment_cutoff_body,
    query_factory_role_evidence_replay_snapshot,
)
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.public.contracts import (
    FactoryWorkspaceReleaseEvidenceV1,
    FactoryWorkspaceRunLeaseConflictError,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffRequestV1,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_CUTOFF_VIEW_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    FactoryPhysicalAttemptCutoffViewV1,
    FactoryPhysicalAttemptGrantViewV1,
    ProviderAttemptTerminalReceiptV1,
    SettleFactoryPhysicalAttemptV1,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA,
    FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA,
    FactoryProviderAttemptLifecycleReplayFactV1,
    FactoryProviderAttemptLifecycleReplaySnapshotV1,
    factory_provider_attempt_lifecycle_stream,
)
from polaris.kernelone.events.final_request_evidence import (
    canonical_role_final_request_hash,
    role_final_request_policy,
)
from polaris.kernelone.events.sourcing import SegmentedJsonlEventStore

_HASH_A = "a" * 64
_HASH_B = "b" * 64


class _NoopSegmentedHead(SegmentedFactLedgerHeadV1):
    def __post_init__(self) -> None:
        pass


class _NoopSegmentedReady(SegmentedFactLedgerReadyV1):
    def __post_init__(self) -> None:
        pass


class _NoopSegmentedQueryResult(SegmentedFactQueryResultV1):
    def __post_init__(self) -> None:
        pass


class _NoopSegmentedAppended(SegmentedFactEventAppendedV1):
    def __post_init__(self) -> None:
        pass


class _NoopCutoffRequest(FactoryRoleEvidenceCutoffRequestV1):
    def __post_init__(self) -> None:
        pass


class _NoopStageAuthority(FactoryRoleEvidenceStageAuthorityV1):
    def __post_init__(self) -> None:
        pass


class _NoopResolvedCut(FactoryRoleEvidenceResolvedCutV1):
    def __post_init__(self) -> None:
        pass


class _NoopSourceSlot(FactoryRoleEvidenceSourceSlotV1):
    def __post_init__(self) -> None:
        pass


class _NoopSourceHead(FactoryRoleEvidenceSourceHeadV1):
    def __post_init__(self) -> None:
        pass


class _NoopSourceItem(FactoryRoleEvidenceSourceItemV1):
    def __post_init__(self) -> None:
        pass


class _FactoryRunSubclass(FactoryRun):
    pass


class _MetadataSubclass(dict[str, Any]):
    pass


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _TupleSubclass(tuple):
    pass


def _request(
    *,
    request_type: type[FactoryRoleEvidenceCutoffRequestV1] = FactoryRoleEvidenceCutoffRequestV1,
    **overrides: object,
) -> FactoryRoleEvidenceCutoffRequestV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
        # B1 persists the real role-child run identity but does not infer or
        # validate membership from it; canonical membership authority is B2/B3.
        "run_id": "role-run-1",
        "role": "director",
        "turn_id": "turn-1",
        "call_id": "call-1",
        "request_freeze_id": "freeze-1",
        "semantic_candidate_hash": _HASH_A,
        "attempt_budget": 3,
        "execution_authority_hash": _HASH_B,
        "candidate_refs": ("pm-contract:1", "ce-blueprint:1"),
    }
    values.update(overrides)
    return request_type(**values)  # type: ignore[arg-type]


def _head(ref_kind: str, *, sequence: int = 2) -> FactoryRoleEvidenceSourceHeadV1:
    return FactoryRoleEvidenceSourceHeadV1(
        canonical_source_ref=f"factory-source:{ref_kind}",
        source_fact_schema="polaris.factory-role-source.v1",
        source_fact_version="v1",
        source_head_fact_id=f"{ref_kind}-head" if sequence else "",
        source_head_sequence=sequence,
        source_head_hash=canonical_role_final_request_hash([ref_kind, sequence]),
    )


def _present_slot(ref_kind: str) -> FactoryRoleEvidenceSourceSlotV1:
    head = _head(ref_kind)
    return FactoryRoleEvidenceSourceSlotV1(
        ref_kind=ref_kind,
        state="present",
        source_head=head,
        items=(
            FactoryRoleEvidenceSourceItemV1(
                ref_kind=ref_kind,
                canonical_ref=f"factory-fact:{ref_kind}:1",
                canonical_hash=canonical_role_final_request_hash({"ref_kind": ref_kind}),
                source_fact_id=head.source_head_fact_id,
                source_fact_sequence=2,
                source_fact_hash=head.source_head_hash,
            ),
        ),
    )


def _present_slot_with_items(ref_kind: str, count: int) -> FactoryRoleEvidenceSourceSlotV1:
    head = _head(ref_kind, sequence=count)
    items = tuple(
        FactoryRoleEvidenceSourceItemV1(
            ref_kind=ref_kind,
            canonical_ref=f"factory-fact:{ref_kind}:{sequence}",
            canonical_hash=canonical_role_final_request_hash([ref_kind, "canonical", sequence]),
            source_fact_id=(head.source_head_fact_id if sequence == count else f"{ref_kind}-fact-{sequence}"),
            source_fact_sequence=sequence,
            source_fact_hash=(
                head.source_head_hash
                if sequence == count
                else canonical_role_final_request_hash([ref_kind, "source", sequence])
            ),
        )
        for sequence in range(1, count + 1)
    )
    return FactoryRoleEvidenceSourceSlotV1(
        ref_kind=ref_kind,
        state="present",
        source_head=head,
        items=items,
    )


def _absent_slot(ref_kind: str) -> FactoryRoleEvidenceSourceSlotV1:
    return FactoryRoleEvidenceSourceSlotV1(
        ref_kind=ref_kind,
        state="absent_at_request_time",
        source_head=_head(ref_kind, sequence=0),
        items=(),
    )


def _resolved_cut(role: str = "director") -> FactoryRoleEvidenceResolvedCutV1:
    policy = role_final_request_policy(role)
    slots = tuple(
        _present_slot(ref_kind) if ref_kind in policy.required_present_slots else _absent_slot(ref_kind)
        for ref_kind in policy.slot_order
    )
    return FactoryRoleEvidenceResolvedCutV1(
        role=role,
        policy_hash=policy.policy_hash,
        slots=slots,
    )


class _Resolver:
    def __init__(
        self,
        result: object | None = None,
        *,
        after_resolve: Callable[[], None] | None = None,
    ) -> None:
        self.result = result if result is not None else _resolved_cut()
        self.calls = 0
        self.after_resolve = after_resolve

    def resolve_source_cut(self, *, request: object, authority: object, factory_run: object) -> object:
        del factory_run
        self.calls += 1
        if self.after_resolve is not None:
            self.after_resolve()
        return self.result


class _MemoryFactStream:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.ensure_calls = 0
        self.append_commands: list[Any] = []
        self.corrupt_reread: str | None = None
        self.fail_append = False
        self.page_size: int | None = None
        self.drift_captured_head_on_later_page = False
        self.cycle_continuation = False
        self.captured_head_count_offset = 0
        self.query_head_hash_override: str | None = None
        self.query_result_workspace_override: str | None = None
        self.query_result_stream_override: str | None = None
        self.append_workspace_override: str | None = None
        self.append_stream_override: str | None = None
        self.ensure_workspace_override: str | None = None
        self.ensure_stream_override: str | None = None
        self.ensure_retention_override: str | None = None
        self.ensure_head_hash_override: str | None = None
        self.ensure_storage_prefix_override: str | None = None
        self.ensure_storage_identity_token_override: str | None = None
        self.query_result_head_hash_override: str | None = None
        self.query_result_head_storage_prefix_override: str | None = None
        self.query_head_storage_prefix_override: str | None = None
        self.fail_before_event_type_once: str | None = None
        self.raise_after_persist_event_type_once: str | None = None
        self.fail_before_fragment_index_once: int | None = None
        self._failed_before = False
        self._raised_after = False
        self.after_append: Callable[[Any], None] | None = None
        self.after_query_events: Callable[[], None] | None = None
        self.unsafe_ensure_head_overrides: dict[str, object] = {}
        self.dto_subclass: str | None = None

    @staticmethod
    def _head(workspace: str, logical_stream: str, count: int) -> SegmentedFactLedgerHeadV1:
        return SegmentedFactLedgerHeadV1(
            workspace=workspace,
            logical_stream=logical_stream,
            storage_prefix="events/factory-authority",
            total_count=count,
            segment_count=1 if count else 0,
            global_seq=count,
            next_expected_global_seq=count + 1,
            tail_segment_index=0 if count else None,
            tail_local_seq=count,
            head_hash=canonical_role_final_request_hash([logical_stream, count]),
            storage_bytes=count,
        )

    def ensure(self, command: Any) -> SegmentedFactLedgerReadyV1:
        self.ensure_calls += 1
        head = self._head(command.workspace, command.logical_stream, len(self.events))
        ready = SegmentedFactLedgerReadyV1(
            workspace=command.workspace,
            logical_stream=command.logical_stream,
            storage_prefix=head.storage_prefix,
            storage_identity_token="authority-test-token",
            retention="pinned_audit_no_delete",
            head=head,
        )
        if self.ensure_head_hash_override is not None:
            object.__setattr__(head, "head_hash", self.ensure_head_hash_override)
        for field_name, value in self.unsafe_ensure_head_overrides.items():
            object.__setattr__(head, field_name, value)
        if self.ensure_workspace_override is not None:
            object.__setattr__(ready, "workspace", self.ensure_workspace_override)
        if self.ensure_stream_override is not None:
            object.__setattr__(ready, "logical_stream", self.ensure_stream_override)
        if self.ensure_storage_prefix_override is not None:
            object.__setattr__(ready, "storage_prefix", self.ensure_storage_prefix_override)
        if self.ensure_retention_override is not None:
            object.__setattr__(ready, "retention", self.ensure_retention_override)
        if self.ensure_storage_identity_token_override is not None:
            object.__setattr__(ready, "storage_identity_token", self.ensure_storage_identity_token_override)
        if self.dto_subclass == "ready":
            return _NoopSegmentedReady(**vars(ready))
        if self.dto_subclass == "head":
            object.__setattr__(ready, "head", _NoopSegmentedHead(**vars(head)))
        return ready

    def query_events(self, query: Any) -> SegmentedFactQueryResultV1:
        events = tuple(dict(event) for event in self.events)
        if self.corrupt_reread and events:
            corrupted = dict(events[-1])
            if self.corrupt_reread == "payload_body_hash":
                payload = dict(corrupted["payload"])
                payload["cutoff_body_hash"] = "f" * 64
                corrupted["payload"] = payload
            elif self.corrupt_reread == "global_seq":
                corrupted["global_seq"] = int(corrupted["global_seq"]) + 1
            else:
                corrupted[self.corrupt_reread] = {
                    "event_type": "wrong.event",
                    "idempotency_key": "wrong-idempotency",
                    "event_id": "wrong-event-id",
                    "event_hash": "f" * 64,
                }[self.corrupt_reread]
            events = (*events[:-1], corrupted)
        start = 0
        if query.continuation is not None:
            prefix = "authority-offset:"
            assert query.continuation.startswith(prefix)
            start = int(query.continuation.removeprefix(prefix))
        page_size = self.page_size or max(len(events), 1)
        page = events[start : start + page_size]
        next_offset = start + len(page)
        continuation = f"authority-offset:{next_offset}" if next_offset < len(events) else None
        corrupt_continuation: str | None = None
        if self.cycle_continuation and start > 0:
            corrupt_continuation = query.continuation
        head_count = len(events) + self.captured_head_count_offset
        if self.drift_captured_head_on_later_page and start > 0:
            head_count += 1
        captured_head = self._head(query.workspace, query.logical_stream, head_count)
        if self.query_result_head_hash_override is not None:
            captured_head = replace(captured_head, head_hash=self.query_result_head_hash_override)
        if self.query_result_head_storage_prefix_override is not None:
            captured_head = replace(
                captured_head,
                storage_prefix=self.query_result_head_storage_prefix_override,
            )
        result = SegmentedFactQueryResultV1(
            workspace=query.workspace,
            logical_stream=query.logical_stream,
            events=page,
            captured_head=captured_head,
            continuation=continuation,
        )
        if self.query_result_workspace_override is not None:
            object.__setattr__(result, "workspace", self.query_result_workspace_override)
        if self.query_result_stream_override is not None:
            object.__setattr__(result, "logical_stream", self.query_result_stream_override)
        if corrupt_continuation is not None:
            object.__setattr__(result, "continuation", corrupt_continuation)
        if self.dto_subclass == "query":
            result = _NoopSegmentedQueryResult(**vars(result))
        if self.after_query_events is not None:
            self.after_query_events()
        return result

    def query_head(self, query: Any) -> SegmentedFactLedgerHeadV1:
        head = self._head(query.workspace, query.logical_stream, len(self.events))
        if self.query_head_hash_override is not None:
            head = replace(head, head_hash=self.query_head_hash_override)
        if self.query_head_storage_prefix_override is not None:
            head = replace(head, storage_prefix=self.query_head_storage_prefix_override)
        return head

    def append(self, command: Any) -> SegmentedFactEventAppendedV1:
        if self.fail_append:
            raise RuntimeError("append-failed")
        if self.dto_subclass == "appended":
            return _NoopSegmentedAppended(
                workspace=command.workspace,
                logical_stream=command.logical_stream,
                event_id="forged-event",
                global_seq=command.expected_global_seq,
                segment_index=0,
                local_seq=command.expected_global_seq,
                event_hash="f" * 64,
                appended_at="2026-07-18T00:00:00+00:00",
            )
        fragment_index = None
        if command.event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE:
            fragment_index = command.payload.get("index")
        if not self._failed_before and (
            command.event_type == self.fail_before_event_type_once
            or (
                self.fail_before_fragment_index_once is not None
                and fragment_index == self.fail_before_fragment_index_once
            )
        ):
            self._failed_before = True
            raise RuntimeError("append-failed-before-persist")
        self.append_commands.append(command)
        sequence = len(self.events) + 1
        assert command.expected_global_seq == sequence
        event_id = f"cutoff-fact-{sequence}"
        event_hash = canonical_role_final_request_hash(
            {"event_id": event_id, "payload": command.payload, "sequence": sequence}
        )
        self.events.append(
            {
                "event_id": event_id,
                "logical_stream": command.logical_stream,
                "global_seq": sequence,
                "segment_index": 0,
                "local_seq": sequence,
                "event_type": command.event_type,
                "source": command.source,
                "payload": dict(command.payload),
                "idempotency_key": command.idempotency_key,
                "occurred_at": "2026-07-18T00:00:00+00:00",
                "previous_event_hash": self.events[-1]["event_hash"] if self.events else "0" * 64,
                "event_hash": event_hash,
            }
        )
        if self.after_append is not None:
            self.after_append(command)
        if not self._raised_after and command.event_type == self.raise_after_persist_event_type_once:
            self._raised_after = True
            raise RuntimeError("append-ack-lost-after-persist")
        return SegmentedFactEventAppendedV1(
            workspace=self.append_workspace_override or command.workspace,
            logical_stream=self.append_stream_override or command.logical_stream,
            event_id=event_id,
            global_seq=sequence,
            segment_index=0,
            local_seq=sequence,
            event_hash=event_hash,
            appended_at="2026-07-18T00:00:00+00:00",
        )


def _run(
    *,
    status: FactoryRunStatus = FactoryRunStatus.RUNNING,
    factory_run_id: str = "factory-run-1",
    stage: str = "director_dispatch",
) -> FactoryRun:
    return FactoryRun(
        id=factory_run_id,
        config=FactoryConfig(name="authority-test", stages=[stage]),
        status=status,
        created_at="2026-07-18T00:00:00+00:00",
        metadata={
            "current_stage": stage,
            "factory_stage_in_flight": True,
        },
    )


def _authority(
    *,
    tmp_path: Path,
    run: FactoryRun | None = None,
    resolver: object | None = None,
    facts: _MemoryFactStream | None = None,
    factory_run_id: str = "factory-run-1",
    stage: str = "director_dispatch",
    clock: Callable[[], datetime] | None = None,
    lease_ttl_seconds: float = 180.0,
    run_loader: Callable[[], Awaitable[FactoryRun | None]] | None = None,
    authority_type: type[FactoryRoleEvidenceStageAuthorityV1] = FactoryRoleEvidenceStageAuthorityV1,
    mutate_authority: Callable[[FactoryRoleEvidenceStageAuthorityV1], None] | None = None,
    physical_attempt_coordinator: FactoryPhysicalAttemptLiveControlPort | None = None,
) -> tuple[FactoryRoleEvidenceAuthorityPort, FactoryWorkspaceRunAdmission, _Resolver, _MemoryFactStream]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "admission",
        lease_ttl_seconds=lease_ttl_seconds,
        **({"clock": clock} if clock is not None else {}),
    )
    lease = admission.acquire(factory_run_id)
    claimed = admission.claim_stage(
        factory_run_id,
        fencing_token=lease.fencing_token,
        stage=stage,
        nonce="stage-nonce-1",
    )
    assert claimed.stage_execution_claim is not None
    captured = authority_type(
        factory_run_id=factory_run_id,
        stage=stage,
        workspace_fencing_token=claimed.fencing_token,
        stage_claim_attempt=claimed.stage_execution_claim.attempt,
        stage_claim_nonce=claimed.stage_execution_claim.nonce,
    )
    if mutate_authority is not None:
        mutate_authority(captured)
    active_run = run if run is not None else _run(factory_run_id=factory_run_id, stage=stage)

    async def load_run() -> FactoryRun | None:
        return active_run

    resolved = resolver if resolver is not None else _Resolver()
    fact_stream = facts or _MemoryFactStream()

    def revalidate_active_stage_claim(grant: FactoryPhysicalAttemptGrantViewV1) -> None:
        with admission.hold_active_stage_claim(
            grant.factory_run_id,
            fencing_token=grant.workspace_fencing_token,
            stage=grant.stage,
            attempt=grant.stage_claim_attempt,
            nonce=grant.stage_claim_nonce,
        ) as revalidate:
            revalidate()

    attempt_coordinator = physical_attempt_coordinator or FactoryPhysicalAttemptLiveControlPort(
        factory_run_id=factory_run_id,
        revalidate_active_stage_claim=revalidate_active_stage_claim,
    )
    port = FactoryRoleEvidenceAuthorityPort(
        workspace=workspace,
        authority=captured,
        run_lock=asyncio.Lock(),
        run_loader=run_loader or load_run,
        admission=admission,
        source_authority=resolved,  # type: ignore[arg-type]
        fact_stream=fact_stream,
        physical_attempt_coordinator=attempt_coordinator,
    )
    port._test_physical_attempt_coordinator = attempt_coordinator
    return port, admission, resolved, fact_stream  # type: ignore[return-value]


def _authorized_request(
    port: FactoryRoleEvidenceAuthorityPort,
    **overrides: object,
) -> FactoryRoleEvidenceCutoffRequestV1:
    binding = getattr(port, "_test_authority_binding", None)
    if type(binding) is not FactoryRoleEvidenceAuthorityBindingV1:
        binding = port.mint_authority_binding("director")
        port._test_authority_binding = binding
    values: dict[str, object] = {
        "role": binding.role,
        "attempt_budget": binding.attempt_budget,
        "execution_authority_hash": binding.execution_authority_hash,
    }
    values.update(overrides)
    return _request(**values)


def test_port_mints_exact_factory_owned_stage_role_budget_grant(tmp_path: Path) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)

    first = port.mint_authority_binding("director")
    second = port.mint_authority_binding("director")

    assert type(first) is FactoryRoleEvidenceAuthorityBindingV1
    assert first is not second
    assert first.execution_authority_hash != second.execution_authority_hash
    assert first.factory_run_id == "factory-run-1"
    assert first.role == "director"
    assert first.cutoff_port is port
    assert first.physical_attempt_control_port is port._test_physical_attempt_coordinator
    budget_state = port._test_physical_attempt_coordinator.budget_state(first.execution_authority_hash)
    assert budget_state.registered is True
    assert budget_state.factory_run_id == first.factory_run_id
    # 2 logical/structured branches x 5 minimum rate-limit transports x
    # 3 route/fallback heads = 30; Factory rounds upward to the immutable 32 cap.
    assert first.attempt_budget == FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET == 32
    assert len(first.execution_authority_hash) == 64
    assert first.execution_authority_hash == first.execution_authority_hash.lower()
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


def test_port_rejects_wrong_stage_role_before_minting_grant(tmp_path: Path) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        port.mint_authority_binding("qa")

    assert exc_info.value.code == "factory_role_evidence_stage_role_mismatch"
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


@pytest.mark.parametrize(
    ("stage", "role", "grant_cap"),
    [
        ("docs_generation", "architect", 1),
        ("pm_planning", "pm", 2),
        ("chief_engineer_review", "chief_engineer", 1),
        ("director_dispatch", "director", 512),
        ("quality_gate", "qa", 1),
    ],
)
def test_stage_grant_caps_fail_closed_before_source_or_fact_effect(
    tmp_path: Path,
    stage: str,
    role: str,
    grant_cap: int,
) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path, stage=stage)

    bindings = [port.mint_authority_binding(role) for _index in range(grant_cap)]

    assert len({binding.execution_authority_hash for binding in bindings}) == grant_cap
    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        port.mint_authority_binding(role)
    assert exc_info.value.code == "factory_role_evidence_stage_grant_cardinality_exceeded"
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


def test_pm_initial_and_recovery_grants_have_distinct_private_authority(tmp_path: Path) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path, stage="pm_planning")

    initial = port.mint_authority_binding("pm")
    recovery = port.mint_authority_binding("pm")

    assert initial.execution_authority_hash != recovery.execution_authority_hash
    assert initial.cutoff_port is recovery.cutoff_port is port
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


def test_director_capacity_preflight_rejects_513_without_minting_any_grant(tmp_path: Path) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        port.require_grant_capacity("director", 513)

    assert exc_info.value.code == "factory_role_evidence_stage_grant_cardinality_exceeded"
    assert port._grants == {}
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


@pytest.mark.asyncio
async def test_same_grant_same_child_accepts_32_freezes_then_33rd_has_zero_new_effect(
    tmp_path: Path,
) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)
    binding = port.mint_authority_binding("director")

    for index in range(32):
        await port.acquire_cutoff(
            _request(
                run_id="role-run-shared-child",
                request_freeze_id=f"freeze-{index + 1}",
                call_id=f"call-{index + 1}",
                semantic_candidate_hash=hashlib.sha256(f"candidate-{index + 1}".encode()).hexdigest(),
                role=binding.role,
                attempt_budget=binding.attempt_budget,
                execution_authority_hash=binding.execution_authority_hash,
            )
        )

    ensure_calls = facts.ensure_calls
    resolver_calls = resolver.calls
    event_count = len(facts.events)
    append_count = len(facts.append_commands)
    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        await port.acquire_cutoff(
            _request(
                run_id="role-run-shared-child",
                request_freeze_id="freeze-33",
                call_id="call-33",
                semantic_candidate_hash=hashlib.sha256(b"candidate-33").hexdigest(),
                role=binding.role,
                attempt_budget=binding.attempt_budget,
                execution_authority_hash=binding.execution_authority_hash,
            )
        )

    assert exc_info.value.code == "factory_role_evidence_request_freeze_cardinality_exceeded"
    assert facts.ensure_calls == ensure_calls
    assert resolver.calls == resolver_calls == 32
    assert len(facts.events) == event_count
    assert len(facts.append_commands) == append_count


@pytest.mark.asyncio
async def test_same_grant_rejects_different_child_before_new_ledger_source_or_fact_effect(
    tmp_path: Path,
) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)
    first = _authorized_request(port, run_id="role-run-child-1")
    await port.acquire_cutoff(first)
    ensure_calls = facts.ensure_calls
    resolver_calls = resolver.calls
    event_count = len(facts.events)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        await port.acquire_cutoff(
            _authorized_request(
                port,
                run_id="role-run-child-2",
                request_freeze_id="freeze-child-2",
                call_id="call-child-2",
            )
        )

    assert exc_info.value.code == "factory_role_evidence_controlled_child_run_mismatch"
    assert facts.ensure_calls == ensure_calls
    assert resolver.calls == resolver_calls
    assert len(facts.events) == event_count


@pytest.mark.asyncio
async def test_stale_claim_rejects_before_child_or_freeze_registry_mutation(tmp_path: Path) -> None:
    port, admission, resolver, facts = _authority(tmp_path=tmp_path)
    binding = port.mint_authority_binding("director")
    grant = port._grants[binding.execution_authority_hash]
    active = admission.current()
    assert active is not None and active.stage_execution_claim is not None
    old_claim = active.stage_execution_claim
    admission.release_stage(
        "factory-run-1",
        fencing_token=active.fencing_token,
        stage=old_claim.stage,
        nonce=old_claim.nonce,
    )
    admission.claim_stage(
        "factory-run-1",
        fencing_token=active.fencing_token,
        stage="director_dispatch",
        nonce="replacement-stage-nonce",
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError):
        await port.acquire_cutoff(
            _request(
                run_id="stale-child",
                request_freeze_id="stale-freeze",
                role=binding.role,
                attempt_budget=binding.attempt_budget,
                execution_authority_hash=binding.execution_authority_hash,
            )
        )

    assert grant.controlled_child_run_id == ""
    assert grant.request_freeze_ids == set()
    assert facts.ensure_calls == 0
    assert resolver.calls == 0
    assert facts.events == []


@pytest.mark.asyncio
async def test_close_revokes_existing_grants_and_rejects_new_mints_without_effect(tmp_path: Path) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)

    port.close_authority()

    closed_budget = port._test_physical_attempt_coordinator.budget_state(request.execution_authority_hash)
    assert closed_budget.closed is True
    assert closed_budget.revoked is True

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as acquire_error:
        await port.acquire_cutoff(request)
    assert acquire_error.value.code == "factory_role_evidence_authority_closed"
    with pytest.raises(FactoryRoleEvidenceAuthorityError) as mint_error:
        port.mint_authority_binding("director")
    assert mint_error.value.code == "factory_role_evidence_authority_closed"
    assert resolver.calls == 0
    assert facts.ensure_calls == 0
    assert facts.events == []


@pytest.mark.asyncio
async def test_close_returns_while_acquisition_awaits_run_loader_then_acquisition_fails_closed(
    tmp_path: Path,
) -> None:
    loader_entered = asyncio.Event()
    release_loader = asyncio.Event()

    async def blocked_run_loader() -> FactoryRun:
        loader_entered.set()
        await release_loader.wait()
        return _run()

    port, _admission, resolver, facts = _authority(
        tmp_path=tmp_path,
        run_loader=blocked_run_loader,
    )
    request = _authorized_request(port)
    close_published = threading.Event()

    class _ObservedGrantRegistry(dict[str, Any]):
        def values(self) -> Any:
            close_published.set()
            return super().values()

    port._grants = _ObservedGrantRegistry(port._grants)
    acquisition_task = asyncio.create_task(port.acquire_cutoff(request))
    await asyncio.wait_for(loader_entered.wait(), timeout=5)
    active_acquisitions_while_loader_blocked = port._active_acquisitions
    close_task = asyncio.create_task(asyncio.to_thread(port.close_authority))
    assert await asyncio.to_thread(close_published.wait, 5)
    try:
        await asyncio.wait_for(asyncio.shield(close_task), timeout=0.1)
        close_returned_while_loader_blocked = True
    except TimeoutError:
        close_returned_while_loader_blocked = False

    release_loader.set()
    with pytest.raises(FactoryRoleEvidenceAuthorityError) as acquisition_error:
        await asyncio.wait_for(acquisition_task, timeout=5)
    await asyncio.wait_for(close_task, timeout=5)

    assert close_returned_while_loader_blocked is True
    assert active_acquisitions_while_loader_blocked == 0
    assert acquisition_error.value.code == "factory_role_evidence_authority_closed"
    assert resolver.calls == 0
    assert facts.ensure_calls == 0
    assert facts.events == []


def test_close_waits_for_fragment_append_acquisition_to_fail_and_drain(tmp_path: Path) -> None:
    port, _admission, _resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    fragment_append_blocked = threading.Event()
    release_fragment_append = threading.Event()
    close_published = threading.Event()
    close_returned = threading.Event()
    acquisition_outcome: dict[str, object] = {}

    class _ObservedGrantRegistry(dict[str, Any]):
        def values(self) -> Any:
            close_published.set()
            return super().values()

    port._grants = _ObservedGrantRegistry(port._grants)

    def block_first_fragment(command: Any) -> None:
        if command.event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE and command.payload["index"] == 0:
            fragment_append_blocked.set()
            assert release_fragment_append.wait(timeout=5)

    facts.after_append = block_first_fragment

    def acquire() -> None:
        try:
            acquisition_outcome["ack"] = asyncio.run(port.acquire_cutoff(request))
        except FactoryRoleEvidenceAuthorityError as exc:
            acquisition_outcome["error"] = exc

    def close() -> None:
        try:
            port.close_authority()
        finally:
            close_returned.set()

    acquisition_thread = threading.Thread(target=acquire, name="factory-role-evidence-acquire")
    close_thread = threading.Thread(target=close, name="factory-role-evidence-close")
    acquisition_thread.start()
    assert fragment_append_blocked.wait(timeout=5)
    close_thread.start()
    assert close_published.wait(timeout=5)
    close_returned_while_append_blocked = close_returned.wait(timeout=0.1)
    release_fragment_append.set()
    acquisition_thread.join(timeout=5)
    close_thread.join(timeout=5)

    assert not acquisition_thread.is_alive()
    assert not close_thread.is_alive()
    assert close_returned_while_append_blocked is False
    error = acquisition_outcome.get("error")
    assert type(error) is FactoryRoleEvidenceAuthorityError
    assert error.code == "factory_role_evidence_authority_closed"
    assert "ack" not in acquisition_outcome
    assert [event["event_type"] for event in facts.events] == [FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE]


def test_close_wins_commit_linearization_without_commit_or_ack(tmp_path: Path) -> None:
    port, _admission, _resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    commit_precheck_passed = threading.Event()
    release_commit = threading.Event()
    close_published = threading.Event()
    acquisition_outcome: dict[str, object] = {}
    all_fragments_seen = False
    original_require_live = port._require_acquisition_live

    class _ObservedGrantRegistry(dict[str, Any]):
        def values(self) -> Any:
            close_published.set()
            return super().values()

    port._grants = _ObservedGrantRegistry(port._grants)

    def pause_after_commit_precheck(cutoff_request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        nonlocal all_fragments_seen
        original_require_live(cutoff_request)
        fragment_events = [
            event for event in facts.events if event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE
        ]
        if not fragment_events or len(fragment_events) != fragment_events[0]["payload"]["count"]:
            return
        if not all_fragments_seen:
            all_fragments_seen = True
            return
        commit_precheck_passed.set()
        assert release_commit.wait(timeout=5)

    port._require_acquisition_live = pause_after_commit_precheck

    def acquire() -> None:
        try:
            acquisition_outcome["ack"] = asyncio.run(port.acquire_cutoff(request))
        except FactoryRoleEvidenceAuthorityError as exc:
            acquisition_outcome["error"] = exc

    def close() -> None:
        port.close_authority()

    acquisition_thread = threading.Thread(target=acquire, name="factory-role-evidence-commit-close-acquire")
    close_thread = threading.Thread(target=close, name="factory-role-evidence-commit-close")
    acquisition_thread.start()
    assert commit_precheck_passed.wait(timeout=5)
    close_thread.start()
    assert close_published.wait(timeout=5)
    release_commit.set()
    acquisition_thread.join(timeout=5)
    close_thread.join(timeout=5)

    assert not acquisition_thread.is_alive()
    assert not close_thread.is_alive()
    error = acquisition_outcome.get("error")
    assert type(error) is FactoryRoleEvidenceAuthorityError
    assert error.code == "factory_role_evidence_authority_closed"
    assert "ack" not in acquisition_outcome
    assert facts.events
    assert all(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE for event in facts.events)


def test_close_wins_replay_ack_publication_without_ack(tmp_path: Path) -> None:
    port, _admission, _resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    seeded_ack = asyncio.run(port.acquire_cutoff(request))
    seeded_event_count = len(facts.events)
    ack_precheck_passed = threading.Event()
    release_ack = threading.Event()
    close_published = threading.Event()
    acquisition_outcome: dict[str, object] = {}
    require_live_calls = 0
    original_require_live = port._require_acquisition_live

    class _ObservedGrantRegistry(dict[str, Any]):
        def values(self) -> Any:
            close_published.set()
            return super().values()

    port._grants = _ObservedGrantRegistry(port._grants)

    def pause_after_ack_precheck(cutoff_request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        nonlocal require_live_calls
        original_require_live(cutoff_request)
        require_live_calls += 1
        if require_live_calls == 2:
            ack_precheck_passed.set()
            assert release_ack.wait(timeout=5)

    port._require_acquisition_live = pause_after_ack_precheck

    def replay() -> None:
        try:
            acquisition_outcome["ack"] = asyncio.run(port.acquire_cutoff(request))
        except FactoryRoleEvidenceAuthorityError as exc:
            acquisition_outcome["error"] = exc

    acquisition_thread = threading.Thread(target=replay, name="factory-role-evidence-replay-close-acquire")
    close_thread = threading.Thread(target=port.close_authority, name="factory-role-evidence-replay-close")
    acquisition_thread.start()
    assert ack_precheck_passed.wait(timeout=5)
    close_thread.start()
    assert close_published.wait(timeout=5)
    release_ack.set()
    acquisition_thread.join(timeout=5)
    close_thread.join(timeout=5)

    assert not acquisition_thread.is_alive()
    assert not close_thread.is_alive()
    assert type(seeded_ack) is FactoryRoleEvidenceCutoffAckV1
    error = acquisition_outcome.get("error")
    assert type(error) is FactoryRoleEvidenceAuthorityError
    assert error.code == "factory_role_evidence_authority_closed"
    assert "ack" not in acquisition_outcome
    assert len(facts.events) == seeded_event_count


def test_revoke_wins_commit_linearization_without_commit_or_ack(tmp_path: Path) -> None:
    port, _admission, _resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    binding = port._test_authority_binding
    assert type(binding) is FactoryRoleEvidenceAuthorityBindingV1
    commit_precheck_passed = threading.Event()
    release_commit = threading.Event()
    acquisition_outcome: dict[str, object] = {}
    all_fragments_seen = False
    original_require_live = port._require_acquisition_live

    def pause_after_commit_precheck(cutoff_request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        nonlocal all_fragments_seen
        original_require_live(cutoff_request)
        fragment_events = [
            event for event in facts.events if event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE
        ]
        if not fragment_events or len(fragment_events) != fragment_events[0]["payload"]["count"]:
            return
        if not all_fragments_seen:
            all_fragments_seen = True
            return
        commit_precheck_passed.set()
        assert release_commit.wait(timeout=5)

    port._require_acquisition_live = pause_after_commit_precheck

    def acquire() -> None:
        try:
            acquisition_outcome["ack"] = asyncio.run(port.acquire_cutoff(request))
        except FactoryRoleEvidenceAuthorityError as exc:
            acquisition_outcome["error"] = exc

    acquisition_thread = threading.Thread(target=acquire, name="factory-role-evidence-commit-revoke-acquire")
    acquisition_thread.start()
    assert commit_precheck_passed.wait(timeout=5)
    port.revoke_authority_binding(binding)

    revoked_budget = port._test_physical_attempt_coordinator.budget_state(binding.execution_authority_hash)
    assert revoked_budget.revoked is True
    release_commit.set()
    acquisition_thread.join(timeout=5)

    assert not acquisition_thread.is_alive()
    error = acquisition_outcome.get("error")
    assert type(error) is FactoryRoleEvidenceAuthorityError
    assert error.code == "factory_role_evidence_grant_revoked"
    assert "ack" not in acquisition_outcome
    assert facts.events
    assert all(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE for event in facts.events)


@pytest.mark.asyncio
async def test_unused_role_task_grant_revoke_blocks_cutoff_without_refunding_stage_cap(tmp_path: Path) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path, stage="quality_gate")
    binding = port.mint_authority_binding("qa")
    request = _request(
        role=binding.role,
        attempt_budget=binding.attempt_budget,
        execution_authority_hash=binding.execution_authority_hash,
    )

    port.revoke_authority_binding(binding)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as acquire_error:
        await port.acquire_cutoff(request)
    assert acquire_error.value.code == "factory_role_evidence_grant_revoked"
    with pytest.raises(FactoryRoleEvidenceAuthorityError) as mint_error:
        port.mint_authority_binding("qa")
    assert mint_error.value.code == "factory_role_evidence_stage_grant_cardinality_exceeded"
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "expected_code"),
    [
        ({"role": "qa"}, "factory_role_evidence_stage_role_mismatch"),
        ({"attempt_budget": 33}, "factory_role_evidence_attempt_budget_mismatch"),
        ({"execution_authority_hash": "c" * 64}, "factory_role_evidence_execution_authority_hash_mismatch"),
    ],
)
async def test_cutoff_rejects_forged_grant_before_run_source_or_fact_effect(
    tmp_path: Path,
    override: dict[str, object],
    expected_code: str,
) -> None:
    port, _admission, resolver, facts = _authority(tmp_path=tmp_path)
    binding = port.mint_authority_binding("director")
    request_values: dict[str, object] = {
        "role": binding.role,
        "attempt_budget": binding.attempt_budget,
        "execution_authority_hash": binding.execution_authority_hash,
    }
    request_values.update(override)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        await port.acquire_cutoff(_request(**request_values))

    assert exc_info.value.code == expected_code
    assert resolver.calls == 0
    assert facts.ensure_calls == 0


def test_cutoff_nested_contracts_require_exact_types_and_base_revalidation() -> None:
    request = _request()
    authority = FactoryRoleEvidenceStageAuthorityV1(
        factory_run_id="factory-run-1",
        stage="director_dispatch",
        workspace_fencing_token=1,
        stage_claim_attempt=1,
        stage_claim_nonce="stage-nonce-1",
    )
    resolved = _resolved_cut()
    first_slot = resolved.slots[0]

    evil_head = _NoopSourceHead(
        canonical_source_ref=first_slot.source_head.canonical_source_ref,
        source_fact_schema=first_slot.source_head.source_fact_schema,
        source_fact_version=first_slot.source_head.source_fact_version,
        source_head_fact_id=first_slot.source_head.source_head_fact_id,
        source_head_sequence=first_slot.source_head.source_head_sequence,
        source_head_hash=first_slot.source_head.source_head_hash,
    )
    with pytest.raises(TypeError, match="source_head_type_invalid"):
        replace(first_slot, source_head=evil_head)

    original_item = first_slot.items[0]
    evil_item = _NoopSourceItem(
        ref_kind=original_item.ref_kind,
        canonical_ref=original_item.canonical_ref,
        canonical_hash=original_item.canonical_hash,
        source_fact_id=original_item.source_fact_id,
        source_fact_sequence=original_item.source_fact_sequence,
        source_fact_hash=original_item.source_fact_hash,
    )
    with pytest.raises(TypeError, match="source_item_type_invalid"):
        replace(first_slot, items=(evil_item,))

    evil_slot = _NoopSourceSlot(
        ref_kind=first_slot.ref_kind,
        state=first_slot.state,
        source_head=first_slot.source_head,
        items=first_slot.items,
    )
    with pytest.raises(TypeError, match="source_cut_slot_type_invalid"):
        replace(resolved, slots=(evil_slot, *resolved.slots[1:]))

    evil_request = _request(request_type=_NoopCutoffRequest, schema_version="evil.request")
    evil_authority = _NoopStageAuthority(
        factory_run_id="factory-run-1",
        stage="director_dispatch",
        workspace_fencing_token=0,
        stage_claim_attempt=0,
        stage_claim_nonce="",
    )
    evil_resolved = _NoopResolvedCut(
        role="director",
        policy_hash="evil",
        slots=(),
        schema_version="evil.cut",
    )
    for field_name, value, error in (
        ("request", evil_request, "cutoff_request_type_invalid"),
        ("authority", evil_authority, "cutoff_stage_authority_type_invalid"),
        ("resolved_source_cut", evil_resolved, "resolved_source_cut_type_invalid"),
    ):
        values = {
            "factory_run_id": authority.factory_run_id,
            "request": request,
            "authority": authority,
            "resolved_source_cut": resolved,
        }
        values[field_name] = value
        with pytest.raises(TypeError, match=error):
            FactoryRoleEvidenceCutoffBodyV1(**values)  # type: ignore[arg-type]

    corrupted_request = _request()
    object.__setattr__(corrupted_request, "schema_version", "evil.request")
    with pytest.raises(ValueError, match="request_schema_mismatch"):
        FactoryRoleEvidenceCutoffBodyV1(
            factory_run_id=authority.factory_run_id,
            request=corrupted_request,
            authority=authority,
            resolved_source_cut=resolved,
        )

    corrupted_authority = replace(authority)
    object.__setattr__(corrupted_authority, "workspace_fencing_token", 0)
    with pytest.raises(ValueError, match="workspace_fencing_token_invalid"):
        FactoryRoleEvidenceCutoffBodyV1(
            factory_run_id=authority.factory_run_id,
            request=request,
            authority=corrupted_authority,
            resolved_source_cut=resolved,
        )

    corrupted_resolved = replace(resolved)
    object.__setattr__(corrupted_resolved, "schema_version", "evil.cut")
    with pytest.raises(ValueError, match="source_cut_schema_mismatch"):
        FactoryRoleEvidenceCutoffBodyV1(
            factory_run_id=authority.factory_run_id,
            request=request,
            authority=authority,
            resolved_source_cut=corrupted_resolved,
        )


def test_internal_authority_contracts_reject_scalar_and_container_subclasses() -> None:
    with pytest.raises(TypeError, match="factory_run_id_type_invalid"):
        FactoryRoleEvidenceStageAuthorityV1(
            factory_run_id=_StrSubclass("factory-run-1"),
            stage="director_dispatch",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="stage-nonce-1",
        )
    with pytest.raises(TypeError, match="workspace_fencing_token_type_invalid"):
        FactoryRoleEvidenceStageAuthorityV1(
            factory_run_id="factory-run-1",
            stage="director_dispatch",
            workspace_fencing_token=_IntSubclass(1),
            stage_claim_attempt=1,
            stage_claim_nonce="stage-nonce-1",
        )

    head = _head("pm_contract")
    with pytest.raises(TypeError, match="source_head_sequence_type_invalid"):
        replace(head, source_head_sequence=_IntSubclass(head.source_head_sequence))
    with pytest.raises(TypeError, match="source_head_hash_type_invalid"):
        replace(head, source_head_hash=_StrSubclass(head.source_head_hash))

    slot = _present_slot("pm_contract")
    with pytest.raises(TypeError, match="source_items_tuple_required"):
        replace(slot, items=_TupleSubclass(slot.items))
    item = slot.items[0]
    with pytest.raises(TypeError, match="source_fact_sequence_type_invalid"):
        replace(item, source_fact_sequence=_IntSubclass(item.source_fact_sequence))

    cut = _resolved_cut()
    with pytest.raises(TypeError, match="schema_version_type_invalid"):
        replace(cut, schema_version=_StrSubclass(cut.schema_version))
    with pytest.raises(TypeError, match="source_cut_slots_tuple_required"):
        replace(cut, slots=_TupleSubclass(cut.slots))

    authority = FactoryRoleEvidenceStageAuthorityV1(
        factory_run_id="factory-run-1",
        stage="director_dispatch",
        workspace_fencing_token=1,
        stage_claim_attempt=1,
        stage_claim_nonce="stage-nonce-1",
    )
    with pytest.raises(TypeError, match="cutoff_body_schema_type_invalid"):
        FactoryRoleEvidenceCutoffBodyV1(
            factory_run_id=authority.factory_run_id,
            request=_request(),
            authority=authority,
            resolved_source_cut=cut,
            schema_version=_StrSubclass("polaris.factory_role_evidence_cutoff_body.v1"),
        )

    manifest = _CutoffCommitManifest(
        factory_run_id="factory-run-1",
        request_freeze_id="freeze-1",
        request_authority_hash=_HASH_A,
        cutoff_body_hash=_HASH_B,
        fragment_count=1,
        cutoff_fragment_vector_hash="c" * 64,
    )
    with pytest.raises(ValueError, match="payload_fields_mismatch"):
        _CutoffCommitManifest.from_record(_MetadataSubclass(manifest.to_record()))
    forged_record = manifest.to_record()
    schema_value = forged_record.pop("schema_version")
    forged_record[_StrSubclass("schema_version")] = schema_value
    with pytest.raises(ValueError, match="payload_fields_mismatch"):
        _CutoffCommitManifest.from_record(forged_record)


@pytest.mark.asyncio
async def test_request_subclass_is_rejected_before_run_resolver_append_or_ack(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    request = _request(request_type=_NoopCutoffRequest, schema_version="evil.request")

    with pytest.raises(TypeError, match="cutoff_request_exact_type_required"):
        await port.acquire_cutoff(request)

    assert resolver.calls == 0
    assert facts.ensure_calls == 0
    assert facts.append_commands == []
    assert facts.events == []


def test_stage_authority_subclass_and_corrupted_base_are_rejected_at_port_boundary(tmp_path: Path) -> None:
    subclass_resolver = _Resolver()
    subclass_facts = _MemoryFactStream()
    with pytest.raises(TypeError, match="stage_authority_exact_type_required"):
        _authority(
            tmp_path=tmp_path / "subclass",
            resolver=subclass_resolver,
            facts=subclass_facts,
            authority_type=_NoopStageAuthority,
        )
    assert subclass_resolver.calls == 0
    assert subclass_facts.ensure_calls == 0
    assert subclass_facts.append_commands == []

    corrupted_resolver = _Resolver()
    corrupted_facts = _MemoryFactStream()

    def corrupt(authority: FactoryRoleEvidenceStageAuthorityV1) -> None:
        object.__setattr__(authority, "workspace_fencing_token", 0)

    with pytest.raises(ValueError, match="workspace_fencing_token_invalid"):
        _authority(
            tmp_path=tmp_path / "corrupted",
            resolver=corrupted_resolver,
            facts=corrupted_facts,
            mutate_authority=corrupt,
        )
    assert corrupted_resolver.calls == 0
    assert corrupted_facts.ensure_calls == 0
    assert corrupted_facts.append_commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forgery", "expected_error"),
    [
        ("resolved_subclass", "source_cut_type_invalid"),
        ("resolved_corrupted", "source_cut_invalid"),
        ("slot_subclass", "source_cut_invalid"),
        ("head_subclass", "source_cut_invalid"),
        ("item_subclass", "source_cut_invalid"),
    ],
)
async def test_resolver_nested_subclasses_fail_after_resolve_before_append_or_ack(
    tmp_path: Path,
    forgery: str,
    expected_error: str,
) -> None:
    cut = _resolved_cut()
    if forgery == "resolved_subclass":
        result: object = _NoopResolvedCut(
            role=cut.role,
            policy_hash=cut.policy_hash,
            slots=cut.slots,
            schema_version=cut.schema_version,
        )
    else:
        result = cut
        if forgery == "resolved_corrupted":
            object.__setattr__(cut, "schema_version", "evil.cut")
        elif forgery == "slot_subclass":
            first = cut.slots[0]
            evil_slot = _NoopSourceSlot(
                ref_kind=first.ref_kind,
                state=first.state,
                source_head=first.source_head,
                items=first.items,
            )
            object.__setattr__(cut, "slots", (evil_slot, *cut.slots[1:]))
        elif forgery == "head_subclass":
            first = cut.slots[0]
            head = first.source_head
            evil_head = _NoopSourceHead(
                canonical_source_ref=head.canonical_source_ref,
                source_fact_schema=head.source_fact_schema,
                source_fact_version=head.source_fact_version,
                source_head_fact_id=head.source_head_fact_id,
                source_head_sequence=head.source_head_sequence,
                source_head_hash=head.source_head_hash,
            )
            object.__setattr__(first, "source_head", evil_head)
        elif forgery == "item_subclass":
            first = cut.slots[0]
            item = first.items[0]
            evil_item = _NoopSourceItem(
                ref_kind=item.ref_kind,
                canonical_ref=item.canonical_ref,
                canonical_hash=item.canonical_hash,
                source_fact_id=item.source_fact_id,
                source_fact_sequence=item.source_fact_sequence,
                source_fact_hash=item.source_fact_hash,
            )
            object.__setattr__(first, "items", (evil_item,))

    resolver = _Resolver(result=result)
    facts = _MemoryFactStream()
    port, _, _, _ = _authority(tmp_path=tmp_path, resolver=resolver, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=expected_error):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 1
    assert facts.append_commands == []
    assert facts.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forgery",
    ["run_subclass", "metadata_subclass", "metadata_key_subclass", "status_string"],
)
async def test_run_loader_projection_requires_exact_run_metadata_and_status(
    tmp_path: Path,
    forgery: str,
) -> None:
    base = _run()
    if forgery == "run_subclass":
        run: FactoryRun = _FactoryRunSubclass(**vars(base))
        expected_error = "run_type_invalid"
    else:
        run = base
        if forgery == "metadata_subclass":
            run.metadata = _MetadataSubclass(run.metadata)
            expected_error = "run_metadata_invalid"
        elif forgery == "metadata_key_subclass":
            current_stage = run.metadata.pop("current_stage")
            run.metadata[_StrSubclass("current_stage")] = current_stage
            expected_error = "run_metadata_invalid"
        else:
            run.status = "running"  # type: ignore[assignment]
            expected_error = "run_status_invalid"
    port, _, resolver, facts = _authority(tmp_path=tmp_path, run=run)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=expected_error):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.ensure_calls == 0
    assert facts.append_commands == []
    assert facts.events == []


@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
def test_resolved_cut_requires_each_roles_exact_policy_order_and_required_presence(role: str) -> None:
    cut = _resolved_cut(role)
    policy = role_final_request_policy(role)

    assert tuple(slot.ref_kind for slot in cut.slots) == policy.slot_order
    if len(cut.slots) > 1:
        with pytest.raises(ValueError, match="slot_order"):
            replace(cut, slots=tuple(reversed(cut.slots)))
    required_index = policy.slot_order.index(policy.required_present_slots[0])
    malformed_slots = list(cut.slots)
    malformed_slots[required_index] = _absent_slot(policy.required_present_slots[0])
    with pytest.raises(ValueError, match="required_slot"):
        replace(cut, slots=tuple(malformed_slots))


def test_source_slot_rejects_head_overrun_cross_slot_items_and_duplicates() -> None:
    cut = _resolved_cut()
    with pytest.raises(ValueError, match="source_fact_sequence_exceeds_head"):
        replace(
            cut.slots[0],
            items=(replace(cut.slots[0].items[0], source_fact_sequence=3),),
        )
    with pytest.raises(ValueError, match="absent_slot_items_forbidden"):
        replace(
            cut.slots[3],
            source_head=_head(cut.slots[3].ref_kind),
            items=(replace(cut.slots[0].items[0], ref_kind=cut.slots[3].ref_kind),),
        )
    with pytest.raises(ValueError, match="item_ref_kind_mismatch"):
        replace(cut.slots[0], items=(replace(cut.slots[0].items[0], ref_kind="target_files"),))
    with pytest.raises(ValueError, match="duplicate_canonical_ref"):
        replace(cut.slots[0], items=(cut.slots[0].items[0], cut.slots[0].items[0]))
    with pytest.raises(ValueError, match="duplicate_source_fact_locator"):
        replace(
            cut.slots[0],
            items=(
                cut.slots[0].items[0],
                replace(
                    cut.slots[0].items[0],
                    canonical_ref="factory-fact:pm_contract:other",
                    canonical_hash="d" * 64,
                ),
            ),
        )
    item_two = FactoryRoleEvidenceSourceItemV1(
        ref_kind="pm_contract",
        canonical_ref="factory-fact:pm_contract:2",
        canonical_hash="2" * 64,
        source_fact_id="pm-contract-fact-2",
        source_fact_sequence=2,
        source_fact_hash="3" * 64,
    )
    item_one = replace(
        item_two,
        canonical_ref="factory-fact:pm_contract:1",
        canonical_hash="1" * 64,
        source_fact_id="pm-contract-fact-1",
        source_fact_sequence=1,
        source_fact_hash="4" * 64,
    )
    with pytest.raises(ValueError, match="source_item_sequence_not_strictly_increasing"):
        FactoryRoleEvidenceSourceSlotV1(
            ref_kind="pm_contract",
            state="present",
            source_head=_head("pm_contract", sequence=3),
            items=(item_two, item_one),
        )
    with pytest.raises(ValueError, match="source_head_item_locator_mismatch"):
        FactoryRoleEvidenceSourceSlotV1(
            ref_kind="pm_contract",
            state="present",
            source_head=_head("pm_contract"),
            items=(replace(item_two, source_fact_id="not-the-head"),),
        )


def test_cutoff_fragment_codec_roundtrips_canonical_utf8_body_in_bounded_chunks() -> None:
    authority = FactoryRoleEvidenceStageAuthorityV1(
        factory_run_id="factory-run-1",
        stage="director_dispatch",
        workspace_fencing_token=1,
        stage_claim_attempt=1,
        stage_claim_nonce="stage-nonce-1",
    )
    body = FactoryRoleEvidenceCutoffBodyV1(
        factory_run_id=authority.factory_run_id,
        request=_request(),
        authority=authority,
        resolved_source_cut=_resolved_cut(),
    )

    raw, body_hash, fragments = _fragment_cutoff_body(body)

    assert raw == _canonical_cutoff_body_bytes(body.to_record())
    assert body_hash == hashlib.sha256(raw).hexdigest()
    assert fragments
    assert all(0 < len(fragment.raw) <= 1024 for fragment in fragments)
    assert b"".join(fragment.raw for fragment in fragments) == raw
    assert tuple(_CutoffFragmentPayload.from_record(fragment.to_record()) for fragment in fragments) == fragments


def test_source_item_bounds_fail_closed_per_slot_and_across_cut() -> None:
    with pytest.raises(ValueError, match="items_per_slot_limit"):
        _present_slot_with_items("pm_contract", 33)

    policy = role_final_request_policy("director")
    over_total_slots = tuple(_present_slot_with_items(ref_kind, 32) for ref_kind in policy.slot_order)
    assert sum(len(slot.items) for slot in over_total_slots) > 128
    with pytest.raises(ValueError, match="total_items_limit"):
        FactoryRoleEvidenceResolvedCutV1(
            role="director",
            policy_hash=policy.policy_hash,
            slots=over_total_slots,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("encoding", "base64"),
        ("raw_byte_count", 1025),
        ("chunk_hash", "f" * 64),
        ("data", "not+padded="),
        ("index", 2),
        ("count", 65),
    ],
)
def test_cutoff_fragment_codec_rejects_noncanonical_or_unbounded_payload(
    field_name: str,
    value: object,
) -> None:
    payload = _CutoffFragmentPayload(
        factory_run_id="factory-run-1",
        request_freeze_id="freeze-1",
        request_authority_hash=_HASH_A,
        cutoff_body_hash=_HASH_B,
        index=0,
        count=1,
        raw=b"canonical-fragment",
        chunk_hash=hashlib.sha256(b"canonical-fragment").hexdigest(),
    ).to_record()
    payload[field_name] = value

    with pytest.raises((TypeError, ValueError)):
        _CutoffFragmentPayload.from_record(payload)


def test_cutoff_commit_manifest_is_exact_and_body_bound_fails_closed() -> None:
    manifest = _CutoffCommitManifest(
        factory_run_id="factory-run-1",
        request_freeze_id="freeze-1",
        request_authority_hash=_HASH_A,
        cutoff_body_hash=_HASH_B,
        fragment_count=4,
        cutoff_fragment_vector_hash="c" * 64,
    )
    assert _CutoffCommitManifest.from_record(manifest.to_record()) == manifest
    malformed = manifest.to_record()
    malformed["raw_body"] = {"forbidden": True}
    with pytest.raises(ValueError, match="payload_fields_mismatch"):
        _CutoffCommitManifest.from_record(malformed)
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="body_too_large"):
        _canonical_cutoff_body_bytes({"oversized": "x" * (64 * 1024)})


def test_hold_active_stage_claim_requires_exact_live_claim(tmp_path: Path) -> None:
    _, admission, _, _ = _authority(tmp_path=tmp_path)
    current = admission.current()
    assert current is not None and current.stage_execution_claim is not None
    claim = current.stage_execution_claim

    with admission.hold_active_stage_claim(
        "factory-run-1",
        fencing_token=current.fencing_token,
        stage=claim.stage,
        attempt=claim.attempt,
        nonce=claim.nonce,
    ) as revalidate:
        assert revalidate() == current

    bad_cases = (
        {"fencing_token": current.fencing_token + 1},
        {"stage": "quality_gate"},
        {"attempt": claim.attempt + 1},
        {"nonce": "wrong-nonce"},
    )
    for overrides in bad_cases:
        values = {
            "fencing_token": current.fencing_token,
            "stage": claim.stage,
            "attempt": claim.attempt,
            "nonce": claim.nonce,
        }
        values.update(overrides)
        with (
            pytest.raises(FactoryWorkspaceRunLeaseConflictError),
            admission.hold_active_stage_claim("factory-run-1", **values),
        ):
            raise AssertionError("unreachable")


def test_hold_active_stage_claim_rejects_draining_and_lifecycle_claim(tmp_path: Path) -> None:
    _, admission, _, _ = _authority(tmp_path=tmp_path)
    current = admission.current()
    assert current is not None and current.stage_execution_claim is not None
    claim = current.stage_execution_claim
    draining = admission.begin_draining(
        "factory-run-1",
        fencing_token=current.fencing_token,
        reason="test-drain",
    )
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as draining_error,
        admission.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=draining.fencing_token,
            stage=claim.stage,
            attempt=claim.attempt,
            nonce=claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert draining_error.value.code == "factory_workspace_run_not_active"

    other = tmp_path / "other"
    _, lifecycle_admission, _, _ = _authority(tmp_path=other)
    before = lifecycle_admission.current()
    assert before is not None and before.stage_execution_claim is not None
    lifecycle_admission.claim_lifecycle_operation(
        "factory-run-1",
        operation="cancel",
        nonce="lifecycle-nonce",
        acquire_if_available=False,
        expected_fencing_token=before.fencing_token,
    )
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as lifecycle_error,
        lifecycle_admission.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=before.fencing_token,
            stage=before.stage_execution_claim.stage,
            attempt=before.stage_execution_claim.attempt,
            nonce=before.stage_execution_claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert lifecycle_error.value.code == "factory_lifecycle_operation_inflight"


def test_hold_active_stage_claim_rejects_expired_and_released_lease(tmp_path: Path) -> None:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def clock() -> datetime:
        return now

    workspace = tmp_path / "expired-workspace"
    workspace.mkdir()
    expired_admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "expired-admission",
        lease_ttl_seconds=1,
        clock=clock,
    )
    lease = expired_admission.acquire("factory-run-1")
    claimed = expired_admission.claim_stage(
        "factory-run-1",
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="stage-nonce-1",
    )
    assert claimed.stage_execution_claim is not None
    claim = claimed.stage_execution_claim
    now += timedelta(seconds=2)
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired_error,
        expired_admission.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=claimed.fencing_token,
            stage=claim.stage,
            attempt=claim.attempt,
            nonce=claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert expired_error.value.code == "factory_workspace_run_lease_expired"

    _, released_admission, _, _ = _authority(tmp_path=tmp_path / "released")
    active = released_admission.current()
    assert active is not None and active.stage_execution_claim is not None
    old_claim = active.stage_execution_claim
    released_admission.release_stage(
        "factory-run-1",
        fencing_token=active.fencing_token,
        stage=old_claim.stage,
        nonce=old_claim.nonce,
    )
    draining = released_admission.begin_draining(
        "factory-run-1",
        fencing_token=active.fencing_token,
        reason="released-test",
    )
    released = released_admission.release(
        "factory-run-1",
        fencing_token=active.fencing_token,
        settlement_evidence=FactoryWorkspaceReleaseEvidenceV1(
            factory_run_id="factory-run-1",
            source="test",
            observed_at="2026-07-18T00:00:00+00:00",
        ),
    )
    assert draining.state.value == "draining"
    assert released.state.value == "released"
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as released_error,
        released_admission.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=active.fencing_token,
            stage=old_claim.stage,
            attempt=old_claim.attempt,
            nonce=old_claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert released_error.value.code == "factory_workspace_run_not_active"


@pytest.mark.asyncio
async def test_cutoff_uses_run_scoped_pinned_fsync_fact_and_strict_reread(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)

    ack = await port.acquire_cutoff(_authorized_request(port))

    expected_stream = f"factory.role_evidence_authority.{hashlib.sha256(b'factory-run-1').hexdigest()}"
    assert type(ack) is FactoryRoleEvidenceCutoffAckV1
    FactoryRoleEvidenceCutoffAckV1.__post_init__(ack)
    assert ack.schema_version == FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA
    assert ack.factory_run_id == "factory-run-1"
    assert ack.authority_stream == expected_stream
    assert resolver.calls == 1
    assert facts.ensure_calls == 1
    fragment_commands = facts.append_commands[:-1]
    commit = facts.append_commands[-1]
    assert len(fragment_commands) == ack.cutoff_fragment_count
    assert ack.cutoff_fact_sequence == len(facts.append_commands)
    for index, command in enumerate(fragment_commands):
        assert command.logical_stream == expected_stream
        assert command.event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE
        assert command.idempotency_key == f"role-evidence-cutoff:freeze-1:fragment:{index}"
        assert command.durability == "fsync"
        assert command.expected_global_seq == index + 1
        assert set(command.payload) == {
            "schema_version",
            "factory_run_id",
            "request_freeze_id",
            "request_authority_hash",
            "cutoff_body_hash",
            "index",
            "count",
            "encoding",
            "raw_byte_count",
            "chunk_hash",
            "data",
        }
        assert command.payload["raw_byte_count"] <= 1024
    assert commit.logical_stream == expected_stream
    assert commit.event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    assert commit.idempotency_key == "role-evidence-cutoff:freeze-1"
    assert commit.durability == "fsync"
    assert commit.expected_global_seq == len(fragment_commands) + 1
    assert set(commit.payload) == {
        "schema_version",
        "factory_run_id",
        "request_freeze_id",
        "request_authority_hash",
        "cutoff_body_hash",
        "fragment_count",
        "cutoff_fragment_vector_hash",
    }
    assert commit.payload["cutoff_body_hash"] == ack.cutoff_body_hash
    assert commit.payload["cutoff_fragment_vector_hash"] == ack.cutoff_fragment_vector_hash
    assert commit.payload["fragment_count"] == ack.cutoff_fragment_count
    serialized = repr(commit.payload)
    assert "cutoff_fact_id" not in serialized
    assert "cutoff_fact_sequence" not in serialized
    assert "raw_payload" not in serialized
    assert "resolved_source_cut" not in serialized


@pytest.mark.asyncio
async def test_replay_snapshot_reuses_strict_cutoff_codec_without_live_grant_capability(tmp_path: Path) -> None:
    port, _, _, facts = _authority(tmp_path=tmp_path)
    ack = await port.acquire_cutoff(_authorized_request(port))

    replay = query_factory_role_evidence_replay_snapshot(
        workspace=tmp_path / "workspace",
        factory_run_id="factory-run-1",
        fact_stream=facts,
    )

    assert type(replay) is FactoryRoleEvidenceReplaySnapshotV1
    assert replay.captured_head.total_count == len(facts.events)
    assert len(replay.cutoffs) == 1
    cutoff = replay.cutoffs[0]
    assert cutoff.cutoff_fact_id == ack.cutoff_fact_id
    assert cutoff.cutoff_sequence == ack.cutoff_fact_sequence
    assert cutoff.cutoff_event_hash == ack.cutoff_fact_hash
    assert cutoff.body.request.request_freeze_id == "freeze-1"
    assert not hasattr(cutoff, "reserve")
    assert not hasattr(replay, "physical_attempt_control_port")


@pytest.mark.asyncio
async def test_replay_snapshot_rejects_partial_cutoff_fragments(tmp_path: Path) -> None:
    port, _, _, facts = _authority(tmp_path=tmp_path)
    await port.acquire_cutoff(_authorized_request(port))
    facts.events.pop()

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="factory_role_evidence_replay_partial_cutoff"):
        query_factory_role_evidence_replay_snapshot(
            workspace=tmp_path / "workspace",
            factory_run_id="factory-run-1",
            fact_stream=facts,
        )


@pytest.mark.asyncio
async def test_physical_attempt_replay_candidate_matches_cutoff_and_never_exposes_admission(tmp_path: Path) -> None:
    port, _, _, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    await port.acquire_cutoff(request)
    role_replay = query_factory_role_evidence_replay_snapshot(
        workspace=tmp_path / "workspace",
        factory_run_id="factory-run-1",
        fact_stream=facts,
    )
    cutoff = role_replay.cutoffs[0]
    authority = cutoff.body.authority
    grant = FactoryPhysicalAttemptGrantViewV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-1",
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
        provider="test-provider",
        model="test-model",
        semantic_request_hash="b" * 64,
        physical_wire_hash="c" * 64,
    )
    composite_hash = canonical_factory_physical_attempt_composite_hash(cutoff_view, 1)
    start = FactoryProviderAttemptLifecycleReplayFactV1(
        schema_version=FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA,
        phase="start",
        lifecycle_event_id="lifecycle-start-1",
        logical_sequence=1,
        event_hash="d" * 64,
        factory_run_id="factory-run-1",
        scope_id="factory-run-1",
        run_id=request.run_id,
        role=request.role,
        turn_id=request.turn_id,
        call_id=request.call_id,
        request_freeze_id=request.request_freeze_id,
        execution_authority_hash=request.execution_authority_hash,
        attempt_budget=request.attempt_budget,
        provider="test-provider",
        model="test-model",
        semantic_candidate_hash=request.semantic_candidate_hash,
        semantic_request_hash="b" * 64,
        physical_wire_hash="c" * 64,
        composite_request_hash=composite_hash,
        reservation_id="reservation-1",
        provider_request_id="provider-request-1",
        authority_attempt_ordinal=1,
        start_permit_id="start-permit-1",
        context_snapshot_ref="e" * 24,
        pin_hash="f" * 64,
    )
    lifecycle_stream = factory_provider_attempt_lifecycle_stream("factory-run-1")
    lifecycle_head = SegmentedFactLedgerHeadV1(
        workspace=str((tmp_path / "workspace").resolve()),
        logical_stream=lifecycle_stream,
        storage_prefix="segmented/provider-attempt-replay",
        total_count=1,
        segment_count=1,
        global_seq=1,
        next_expected_global_seq=2,
        tail_segment_index=0,
        tail_local_seq=1,
        head_hash="1" * 64,
        storage_bytes=1,
    )
    lifecycle_replay = FactoryProviderAttemptLifecycleReplaySnapshotV1(
        schema_version=FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA,
        workspace=str((tmp_path / "workspace").resolve()),
        factory_run_id="factory-run-1",
        logical_stream=lifecycle_stream,
        captured_head=lifecycle_head,
        facts=(start,),
    )
    fence = FactoryPhysicalAttemptReplayFenceV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA,
        factory_run_id="factory-run-1",
        factory_stage_head_sequence=7,
        factory_stage_head_hash="2" * 64,
        workspace_fencing_token=authority.workspace_fencing_token,
        current_stage=authority.stage,
        fence_kind="stage_claim",
        fence_sequence=authority.stage_claim_attempt,
        fence_nonce=authority.stage_claim_nonce,
        replay_fenced=True,
        live_mutation_forbidden=True,
    )

    assert start.semantic_candidate_hash == request.semantic_candidate_hash
    assert start.semantic_request_hash != request.semantic_candidate_hash
    mismatched_lifecycle = replace(
        lifecycle_replay,
        facts=(replace(start, semantic_candidate_hash="0" * 64),),
    )
    with pytest.raises(
        FactoryPhysicalAttemptReplayError,
        match="factory_physical_attempt_replay_lifecycle_identity_mismatch",
    ):
        build_factory_physical_attempt_replay_candidate(
            fence=fence,
            role_evidence=role_replay,
            lifecycle=mismatched_lifecycle,
        )

    candidate = build_factory_physical_attempt_replay_candidate(
        fence=fence,
        role_evidence=role_replay,
        lifecycle=lifecycle_replay,
    )

    assert candidate.permanently_dead_for_admission is True
    assert candidate.outbound_count == 0
    assert len(candidate.records) == 1
    assert candidate.records[0].recovery_terminal_required is True
    assert not hasattr(candidate, "reserve")
    coordinator, recovery_work = FactoryPhysicalAttemptLiveControlPort.from_replay_candidate(candidate)
    state = coordinator.budget_state(request.execution_authority_hash)
    assert state.closed is True
    assert state.revoked is True
    assert state.committed_count == 1
    assert state.recovered_count == 1
    assert state.terminal_count == 0
    assert state.settled is False
    assert coordinator.drain_snapshot().settled is False
    assert len(recovery_work) == 1
    work = recovery_work[0]
    recovered_terminal = ProviderAttemptTerminalReceiptV1(
        schema_version=PROVIDER_ATTEMPT_TERMINAL_RECEIPT_SCHEMA,
        verification_scope=work.lease.verification_scope,
        factory_run_id=work.lease.factory_run_id,
        run_id=work.lease.run_id,
        role=work.lease.role,
        turn_id=work.lease.turn_id,
        call_id=work.lease.call_id,
        request_freeze_id=work.lease.request_freeze_id,
        execution_authority_hash=work.lease.execution_authority_hash,
        attempt_budget=work.lease.attempt_budget,
        provider=work.lease.provider,
        model=work.lease.model,
        semantic_request_hash=work.lease.semantic_request_hash,
        physical_wire_hash=work.lease.physical_wire_hash,
        composite_request_hash=work.lease.composite_request_hash,
        reservation_id=work.lease.reservation_id,
        provider_request_id=work.lease.provider_request_id,
        authority_attempt_ordinal=work.lease.authority_attempt_ordinal,
        start_permit_id=work.lease.start_permit_id,
        lease_id=work.lease.lease_id,
        lifecycle_event_id="recovered-terminal-1",
        logical_sequence=2,
        event_hash="4" * 64,
        phase="terminal",
        durability_acked=True,
        terminal_status="cancelled",
    )
    settled = coordinator.settle(
        SettleFactoryPhysicalAttemptV1(
            schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
            lease=work.lease,
            terminal_receipt=recovered_terminal,
        )
    )
    assert settled.recovered_count == 1
    assert settled.terminal_count == 1
    assert settled.settled is True
    recovered_terminal_fact = replace(
        start,
        phase="terminal",
        lifecycle_event_id="recovered-terminal-1",
        logical_sequence=2,
        event_hash="4" * 64,
        lease_id=work.lease.lease_id,
        terminal_status="cancelled",
        error="recovered unmatched durable start; physical redispatch forbidden",
    )
    paired_candidate = build_factory_physical_attempt_replay_candidate(
        fence=fence,
        role_evidence=role_replay,
        lifecycle=replace(
            lifecycle_replay,
            captured_head=replace(
                lifecycle_head,
                total_count=2,
                global_seq=2,
                next_expected_global_seq=3,
                tail_local_seq=2,
                head_hash="4" * 64,
                storage_bytes=2,
            ),
            facts=(start, recovered_terminal_fact),
        ),
    )
    restarted, restarted_work = FactoryPhysicalAttemptLiveControlPort.from_replay_candidate(paired_candidate)
    restarted_state = restarted.budget_state(request.execution_authority_hash)
    assert restarted_work == ()
    assert restarted_state.recovered_count == 1
    assert restarted_state.terminal_count == 1
    assert restarted_state.settled is True
    with pytest.raises(FactoryPhysicalAttemptReplayError, match="composite_hash_mismatch"):
        build_factory_physical_attempt_replay_candidate(
            fence=fence,
            role_evidence=role_replay,
            lifecycle=replace(lifecycle_replay, facts=(replace(start, composite_request_hash="3" * 64),)),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["director", "qa", "chief_engineer", "pm", "architect"])
async def test_real_segmented_fact_stream_fragments_cutoff_under_4k_and_replays_on_live_grant(
    tmp_path: Path,
    role: str,
) -> None:
    workspace = tmp_path / f"real-{role}"
    workspace.mkdir()
    factory_run_id = f"factory-run-{role}"
    logical_stream = f"factory.role_evidence_authority.{hashlib.sha256(factory_run_id.encode('utf-8')).hexdigest()}"
    provision_fact_stream_lock_authority(
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace=str(workspace),
            streams=(),
            maintenance_reason="a009b1_real_segmented_cutoff_test",
        )
    )
    stage = {
        "director": "director_dispatch",
        "qa": "quality_gate",
        "chief_engineer": "chief_engineer_review",
        "pm": "pm_planning",
        "architect": "docs_generation",
    }[role]
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / f"admission-{role}")
    lease = admission.acquire(factory_run_id)
    claimed = admission.claim_stage(
        factory_run_id,
        fencing_token=lease.fencing_token,
        stage=stage,
        nonce=f"stage-nonce-{role}",
    )
    assert claimed.stage_execution_claim is not None
    authority = FactoryRoleEvidenceStageAuthorityV1(
        factory_run_id=factory_run_id,
        stage=stage,
        workspace_fencing_token=claimed.fencing_token,
        stage_claim_attempt=claimed.stage_execution_claim.attempt,
        stage_claim_nonce=claimed.stage_execution_claim.nonce,
    )
    run = _run(factory_run_id=factory_run_id, stage=stage)

    async def load_run() -> FactoryRun:
        return run

    resolver = _Resolver(_resolved_cut(role))

    def revalidate_active_stage_claim(grant: FactoryPhysicalAttemptGrantViewV1) -> None:
        with admission.hold_active_stage_claim(
            grant.factory_run_id,
            fencing_token=grant.workspace_fencing_token,
            stage=grant.stage,
            attempt=grant.stage_claim_attempt,
            nonce=grant.stage_claim_nonce,
        ) as revalidate:
            revalidate()

    attempt_coordinator = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id=factory_run_id,
        revalidate_active_stage_claim=revalidate_active_stage_claim,
    )
    port = FactoryRoleEvidenceAuthorityPort(
        workspace=workspace,
        authority=authority,
        run_lock=asyncio.Lock(),
        run_loader=load_run,
        admission=admission,
        source_authority=resolver,
        physical_attempt_coordinator=attempt_coordinator,
    )
    binding = port.mint_authority_binding(role)
    request = _request(
        role=role,
        run_id=f"role-run-{role}",
        request_freeze_id=f"freeze-{role}",
        attempt_budget=binding.attempt_budget,
        execution_authority_hash=binding.execution_authority_hash,
    )

    first = await port.acquire_cutoff(request)
    result = query_segmented_fact_events(
        QuerySegmentedFactEventsV1(
            workspace=str(workspace),
            logical_stream=logical_stream,
            limit=511,
        )
    )
    assert result.events[-1]["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    assert all(
        event["event_type"] in {"factory.role_evidence_cutoff.fragment", FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE}
        for event in result.events
    )
    assert len(result.events) >= 2
    assert first.cutoff_fact_id == result.events[-1]["event_id"]
    assert first.cutoff_fact_sequence == result.events[-1]["global_seq"]
    assert first.cutoff_fact_hash == result.events[-1]["event_hash"]

    store = SegmentedJsonlEventStore(str(workspace), logical_stream=logical_stream)
    record_sizes: list[int] = []
    for segment_index in range(result.captured_head.segment_count):
        segment = Path(store.segment_absolute_path(segment_index))
        for raw_line in segment.read_bytes().splitlines():
            record_sizes.append(len(raw_line))
    assert record_sizes
    assert max(record_sizes) < 4096
    assert max(record_sizes) <= 3072

    replay = await port.acquire_cutoff(request)
    assert replay == first
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_exact_replay_returns_original_ack_without_resolver_or_new_event(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)

    first = await port.acquire_cutoff(request)
    appended_count = len(facts.append_commands)
    second = await port.acquire_cutoff(request)

    assert second == first
    assert resolver.calls == 1
    assert len(facts.append_commands) == appended_count


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["architect", "pm", "chief_engineer", "director", "qa"])
async def test_resolve_cutoff_proof_strictly_rereads_exact_ack_and_returns_public_proof(
    tmp_path: Path,
    role: str,
) -> None:
    stage = {
        "architect": "docs_generation",
        "pm": "pm_planning",
        "chief_engineer": "chief_engineer_review",
        "director": "director_dispatch",
        "qa": "quality_gate",
    }[role]
    port, _, _, facts = _authority(
        tmp_path=tmp_path,
        factory_run_id=f"factory-run-{role}",
        stage=stage,
        resolver=_Resolver(_resolved_cut(role)),
    )
    binding = port.mint_authority_binding(role)
    request = _request(
        role=role,
        request_freeze_id=f"freeze-{role}",
        attempt_budget=binding.attempt_budget,
        execution_authority_hash=binding.execution_authority_hash,
    )
    ack = await port.acquire_cutoff(request)
    event_count = len(facts.events)

    proof = await port.resolve_cutoff_proof(ack)

    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
        FactoryRoleEvidenceCutoffProofV1,
        FactoryRoleEvidenceCutoffSourceHeadV1,
    )

    assert type(proof) is FactoryRoleEvidenceCutoffProofV1
    assert proof.ack == ack
    assert proof.signed_factory_binding_ref == (
        f"{ack.authority_stream}@{ack.cutoff_fact_sequence}#{ack.cutoff_fact_id}"
    )
    assert all(type(head) is FactoryRoleEvidenceCutoffSourceHeadV1 for head in proof.source_head_vector)
    assert proof.policy_facts.role == role
    assert len(facts.events) == event_count


@pytest.mark.asyncio
async def test_cutoff_operations_from_foreign_event_loop_run_on_factory_lock_owner(
    tmp_path: Path,
) -> None:
    """Director worker loops must not acquire the Factory-owned asyncio lock."""

    owner_loop = asyncio.get_running_loop()
    loaded_on: list[asyncio.AbstractEventLoop] = []

    async def load_run() -> FactoryRun:
        loaded_on.append(asyncio.get_running_loop())
        return _run()

    port, _, _, _ = _authority(tmp_path=tmp_path, run_loader=load_run)
    request = _authorized_request(port)

    # Bind the lock to the Factory loop, then hold it while a foreign role loop
    # starts the cutoff.  The old implementation raised "bound to a different
    # event loop" here; the port must instead marshal the whole critical section
    # back to the owner loop and wait for the Factory lifecycle lock.
    async def wait_once() -> None:
        async with port._run_lock:
            return

    async with port._run_lock:
        bind_waiter = asyncio.create_task(wait_once())
        await asyncio.sleep(0)
    await bind_waiter
    await port._run_lock.acquire()
    foreign_started = threading.Event()

    def run_from_foreign_loop() -> tuple[FactoryRoleEvidenceCutoffAckV1, Any]:
        async def workflow() -> tuple[FactoryRoleEvidenceCutoffAckV1, Any]:
            foreign_started.set()
            ack = await port.acquire_cutoff(request)
            return ack, await port.resolve_cutoff_proof(ack)

        return asyncio.run(workflow())

    foreign_task = asyncio.create_task(asyncio.to_thread(run_from_foreign_loop))
    while not foreign_started.is_set():
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    port._run_lock.release()
    ack, proof = await foreign_task

    assert proof.ack == ack
    assert loaded_on == [owner_loop, owner_loop]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"cutoff_fact_id": "other-fact"},
        {"cutoff_fact_sequence": 999},
        {"cutoff_fact_hash": "f" * 64},
        {"cutoff_body_hash": "f" * 64},
        {"cutoff_fragment_vector_hash": "f" * 64},
        {"cutoff_fragment_count": 63},
        {"semantic_candidate_hash": "f" * 64},
    ],
)
async def test_resolve_cutoff_proof_rejects_every_ack_locator_or_identity_mismatch(
    tmp_path: Path,
    tamper: dict[str, object],
) -> None:
    port, _, _, facts = _authority(tmp_path=tmp_path)
    ack = await port.acquire_cutoff(_authorized_request(port))
    event_count = len(facts.events)

    with pytest.raises(FactoryRoleEvidenceAuthorityError):
        await port.resolve_cutoff_proof(replace(ack, **tamper))

    assert len(facts.events) == event_count


@pytest.mark.asyncio
async def test_expiry_during_resolve_leaves_no_authoritative_cutoff(tmp_path: Path) -> None:
    now = [datetime(2026, 7, 18, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return now[0]

    def expire() -> None:
        now[0] += timedelta(seconds=2)

    resolver = _Resolver(after_resolve=expire)
    port, _, _, facts = _authority(
        tmp_path=tmp_path,
        resolver=resolver,
        clock=clock,
        lease_ttl_seconds=1,
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired:
        await port.acquire_cutoff(_authorized_request(port))

    assert expired.value.code == "factory_workspace_run_lease_expired"
    assert resolver.calls == 1
    assert facts.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("head_overrides", "identity_token"),
    [
        ({"head_hash": "x"}, None),
        ({}, ""),
        ({"total_count": 1}, None),
        ({"tail_segment_index": 0}, None),
        ({"storage_bytes": 1}, None),
    ],
)
async def test_forged_segmented_ready_dto_fails_before_resolver_append_or_ack(
    tmp_path: Path,
    head_overrides: dict[str, object],
    identity_token: str | None,
) -> None:
    facts = _MemoryFactStream()
    facts.unsafe_ensure_head_overrides = head_overrides
    facts.ensure_storage_identity_token_override = identity_token
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="ledger_corrupt"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.append_commands == []
    assert facts.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dto_subclass", "error_code"),
    [
        ("ready", "ledger_corrupt"),
        ("head", "ledger_corrupt"),
        ("query", "strict_scan_corrupt"),
        ("appended", "append_corrupt"),
    ],
)
async def test_segmented_dto_subclass_cannot_bypass_authority_validation(
    tmp_path: Path,
    dto_subclass: str,
    error_code: str,
) -> None:
    facts = _MemoryFactStream()
    facts.dto_subclass = dto_subclass
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=error_code):
        await port.acquire_cutoff(_authorized_request(port))

    if dto_subclass == "appended":
        assert resolver.calls == 1
    else:
        assert resolver.calls == 0
    assert facts.append_commands == []
    assert facts.events == []


@pytest.mark.asyncio
async def test_expiry_during_fragments_leaves_only_nonauthoritative_fragments(tmp_path: Path) -> None:
    now = [datetime(2026, 7, 18, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return now[0]

    facts = _MemoryFactStream()

    def expire_after_first_fragment(command: Any) -> None:
        if command.event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE:
            now[0] += timedelta(seconds=2)
            facts.after_append = None

    facts.after_append = expire_after_first_fragment
    port, _, resolver, _ = _authority(
        tmp_path=tmp_path,
        facts=facts,
        clock=clock,
        lease_ttl_seconds=1,
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired:
        await port.acquire_cutoff(_authorized_request(port))

    assert expired.value.code == "factory_workspace_run_lease_expired"
    assert resolver.calls == 1
    assert facts.events
    assert all(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE for event in facts.events)


@pytest.mark.asyncio
async def test_complete_partial_resume_revalidates_expiry_before_commit(tmp_path: Path) -> None:
    now = [datetime(2026, 7, 18, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return now[0]

    port, _, resolver, facts = _authority(
        tmp_path=tmp_path,
        clock=clock,
        lease_ttl_seconds=1,
    )
    request = _authorized_request(port)
    await port.acquire_cutoff(request)
    commit = facts.events.pop()
    assert commit["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    fragment_count = len(facts.events)
    resolver_calls = resolver.calls

    def expire_after_partial_scan() -> None:
        now[0] += timedelta(seconds=2)
        facts.after_query_events = None

    facts.after_query_events = expire_after_partial_scan
    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired:
        await port.acquire_cutoff(request)

    assert expired.value.code == "factory_workspace_run_lease_expired"
    assert resolver.calls == resolver_calls
    assert len(facts.events) == fragment_count
    assert all(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE for event in facts.events)


@pytest.mark.asyncio
async def test_complete_partial_freeze_resumes_commit_without_resolving_sources(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    original = await port.acquire_cutoff(request)
    original_commit = facts.events.pop()
    assert original_commit["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    resolver_calls = resolver.calls

    resumed = await port.acquire_cutoff(request)

    assert resumed == original
    assert resolver.calls == resolver_calls
    assert facts.events[-1]["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE


@pytest.mark.asyncio
async def test_incomplete_partial_freeze_fails_closed_without_resolver_or_source_recapture(
    tmp_path: Path,
) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    await port.acquire_cutoff(request)
    facts.events = facts.events[:-2]
    resolver_calls = resolver.calls
    append_calls = len(facts.append_commands)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="partial_incomplete"):
        await port.acquire_cutoff(request)

    assert resolver.calls == resolver_calls
    assert len(facts.append_commands) == append_calls


@pytest.mark.asyncio
async def test_partial_freeze_uses_request_authority_hash_for_conflict_before_incomplete(
    tmp_path: Path,
) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    await port.acquire_cutoff(_authorized_request(port))
    facts.events = facts.events[:-2]
    resolver_calls = resolver.calls

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="replay_conflict"):
        await port.acquire_cutoff(_authorized_request(port, call_id="different-call"))

    assert resolver.calls == resolver_calls


@pytest.mark.asyncio
async def test_same_sequence_but_different_full_head_hash_fails_before_append(tmp_path: Path) -> None:
    facts = _MemoryFactStream()
    facts.query_head_hash_override = "f" * 64
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="head_drift"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 1
    assert facts.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("spoof", ["workspace", "stream"])
async def test_query_result_workspace_or_stream_spoof_fails_before_resolver(
    tmp_path: Path,
    spoof: str,
) -> None:
    facts = _MemoryFactStream()
    if spoof == "workspace":
        facts.query_result_workspace_override = str(tmp_path / "other-workspace")
    else:
        facts.query_result_stream_override = "factory.role_evidence_authority." + "f" * 64
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="strict_scan_corrupt"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("spoof", ["workspace", "stream"])
async def test_append_ack_workspace_or_stream_spoof_fails_closed(
    tmp_path: Path,
    spoof: str,
) -> None:
    facts = _MemoryFactStream()
    if spoof == "workspace":
        facts.append_workspace_override = str(tmp_path / "other-workspace")
    else:
        facts.append_stream_override = "factory.role_evidence_authority." + "e" * 64
    port, _, _, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="append_corrupt"):
        await port.acquire_cutoff(_authorized_request(port))

    assert len(facts.events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spoof",
    ["workspace", "stream", "retention", "ready_storage_prefix", "ready_head_hash"],
)
async def test_ensure_ready_identity_retention_and_head_must_match(
    tmp_path: Path,
    spoof: str,
) -> None:
    facts = _MemoryFactStream()
    if spoof == "workspace":
        facts.ensure_workspace_override = str(tmp_path / "other-workspace")
    elif spoof == "stream":
        facts.ensure_stream_override = "factory.role_evidence_authority." + "d" * 64
    elif spoof == "retention":
        facts.ensure_retention_override = "delete_allowed"
    elif spoof == "ready_storage_prefix":
        facts.ensure_storage_prefix_override = "wrong/storage-prefix"
    else:
        facts.ensure_head_hash_override = "f" * 64
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=r"ledger_(corrupt|head_mismatch)"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("spoof", ["captured_head_hash", "captured_storage_prefix"])
async def test_query_captured_full_head_must_equal_ensure_head(
    tmp_path: Path,
    spoof: str,
) -> None:
    facts = _MemoryFactStream()
    if spoof == "captured_head_hash":
        facts.query_result_head_hash_override = "f" * 64
    else:
        facts.query_result_head_storage_prefix_override = "wrong/captured-prefix"
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="ledger_head_mismatch"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.events == []


@pytest.mark.asyncio
async def test_same_sequence_but_different_storage_prefix_fails_before_append(tmp_path: Path) -> None:
    facts = _MemoryFactStream()
    facts.query_head_storage_prefix_override = "wrong/current-prefix"
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="head_drift"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 1
    assert facts.events == []


@pytest.mark.asyncio
async def test_commit_pre_persist_failure_resumes_from_complete_fragments_without_resolver(
    tmp_path: Path,
) -> None:
    facts = _MemoryFactStream()
    facts.fail_before_event_type_once = FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)
    request = _authorized_request(port)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="append_failed"):
        await port.acquire_cutoff(request)
    fragment_count = len(facts.events)
    assert fragment_count >= 1
    assert all(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE for event in facts.events)
    resolver_calls = resolver.calls

    ack = await port.acquire_cutoff(request)

    assert resolver.calls == resolver_calls
    assert ack.cutoff_fragment_count == fragment_count
    assert len(facts.events) == fragment_count + 1


@pytest.mark.asyncio
async def test_fragment_mid_append_failure_remains_incomplete_without_recapturing_source(
    tmp_path: Path,
) -> None:
    facts = _MemoryFactStream()
    facts.fail_before_fragment_index_once = 2
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)
    request = _authorized_request(port)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="append_failed"):
        await port.acquire_cutoff(request)
    assert len(facts.events) == 2
    resolver_calls = resolver.calls

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="partial_incomplete"):
        await port.acquire_cutoff(request)

    assert resolver.calls == resolver_calls
    assert len(facts.events) == 2


@pytest.mark.asyncio
async def test_commit_ack_loss_replays_persisted_commit_without_resolver_or_new_event(
    tmp_path: Path,
) -> None:
    facts = _MemoryFactStream()
    facts.raise_after_persist_event_type_once = FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    port, _, resolver, _ = _authority(tmp_path=tmp_path, facts=facts)
    request = _authorized_request(port)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="append_failed"):
        await port.acquire_cutoff(request)
    persisted_commit = dict(facts.events[-1])
    assert persisted_commit["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE
    event_count = len(facts.events)
    resolver_calls = resolver.calls

    ack = await port.acquire_cutoff(request)

    assert resolver.calls == resolver_calls
    assert len(facts.events) == event_count
    assert ack.cutoff_fact_id == persisted_commit["event_id"]
    assert ack.cutoff_fact_sequence == persisted_commit["global_seq"]
    assert ack.cutoff_fact_hash == persisted_commit["event_hash"]


@pytest.mark.asyncio
async def test_fragment_payload_corruption_is_rejected_before_replay_ack_or_resolver(
    tmp_path: Path,
) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    await port.acquire_cutoff(request)
    fragment_payload = dict(facts.events[0]["payload"])
    fragment_payload["chunk_hash"] = "f" * 64
    facts.events[0]["payload"] = fragment_payload
    resolver_calls = resolver.calls

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="event_malformed"):
        await port.acquire_cutoff(request)

    assert resolver.calls == resolver_calls


async def _seed_three_cutoffs(port: FactoryRoleEvidenceAuthorityPort) -> tuple[object, object, object]:
    return (
        await port.acquire_cutoff(_authorized_request(port)),
        await port.acquire_cutoff(
            _authorized_request(
                port,
                request_freeze_id="freeze-2",
                call_id="call-2",
                semantic_candidate_hash="c" * 64,
            )
        ),
        await port.acquire_cutoff(
            _authorized_request(
                port,
                request_freeze_id="freeze-3",
                call_id="call-3",
                semantic_candidate_hash="d" * 64,
            )
        ),
    )


@pytest.mark.asyncio
async def test_exact_replay_scans_every_authority_page_before_returning_ack(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    first, _, _ = await _seed_three_cutoffs(port)
    append_count = len(facts.append_commands)
    facts.page_size = 1

    replay = await port.acquire_cutoff(_authorized_request(port))

    assert replay == first
    assert resolver.calls == 3
    assert len(facts.append_commands) == append_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_mode", "error_code"),
    [
        ("head_drift", "scan_head_drift"),
        ("continuation_cycle", "scan_continuation_cycle"),
        ("count_mismatch", "scan_count_mismatch"),
    ],
)
async def test_strict_scan_pagination_corruption_conveys_no_ack_or_new_authority(
    tmp_path: Path,
    failure_mode: str,
    error_code: str,
) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    await _seed_three_cutoffs(port)
    facts.page_size = 1
    if failure_mode == "head_drift":
        facts.drift_captured_head_on_later_page = True
    elif failure_mode == "continuation_cycle":
        facts.cycle_continuation = True
    else:
        facts.captured_head_count_offset = 1
    resolver_calls_before = resolver.calls
    append_count_before = len(facts.append_commands)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=error_code):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == resolver_calls_before
    assert len(facts.append_commands) == append_count_before
    assert len(facts.events) == append_count_before


@pytest.mark.asyncio
async def test_old_freeze_replay_keeps_old_source_vector_without_resolving_new_heads(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    request = _authorized_request(port)
    first = await port.acquire_cutoff(request)
    original_events = json.loads(json.dumps(facts.events))

    changed = _resolved_cut()
    changed_head = replace(changed.slots[0].source_head, source_head_hash="e" * 64)
    changed_first = replace(
        changed.slots[0],
        source_head=changed_head,
        items=(replace(changed.slots[0].items[0], source_fact_hash=changed_head.source_head_hash),),
    )
    resolver.result = replace(changed, slots=(changed_first, *changed.slots[1:]))
    replay = await port.acquire_cutoff(request)

    assert replay == first
    assert resolver.calls == 1
    assert facts.events == original_events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"role": "qa"}, "factory_role_evidence_stage_role_mismatch"),
        ({"turn_id": "turn-2"}, "factory_role_evidence_cutoff_replay_conflict"),
        ({"call_id": "call-2"}, "factory_role_evidence_cutoff_replay_conflict"),
        ({"run_id": "role-run-2"}, "factory_role_evidence_controlled_child_run_mismatch"),
        ({"attempt_budget": 4}, "factory_role_evidence_attempt_budget_mismatch"),
        (
            {"execution_authority_hash": "d" * 64},
            "factory_role_evidence_execution_authority_hash_mismatch",
        ),
        ({"candidate_refs": ("pm-contract:2",)}, "factory_role_evidence_cutoff_replay_conflict"),
        ({"semantic_candidate_hash": "c" * 64}, "factory_role_evidence_cutoff_replay_conflict"),
    ],
)
async def test_same_freeze_conflict_fails_before_resolver(
    tmp_path: Path,
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)
    await port.acquire_cutoff(_authorized_request(port))
    event_count = len(facts.events)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=expected_code):
        await port.acquire_cutoff(_authorized_request(port, **overrides))

    assert resolver.calls == 1
    assert len(facts.events) == event_count


@pytest.mark.asyncio
async def test_different_freezes_are_monotonic_and_run_namespaced(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)

    first = await port.acquire_cutoff(_authorized_request(port))
    second = await port.acquire_cutoff(
        _authorized_request(
            port,
            request_freeze_id="freeze-2",
            call_id="call-2",
            semantic_candidate_hash="c" * 64,
        )
    )

    assert first.cutoff_fact_sequence >= 2
    assert second.cutoff_fact_sequence > first.cutoff_fact_sequence
    assert second.cutoff_fact_sequence == len(facts.events)
    assert resolver.calls == 2
    assert sum(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE for event in facts.events) == 2


@pytest.mark.asyncio
async def test_same_freeze_isolated_across_factory_run_namespaces(tmp_path: Path) -> None:
    first_port, _, _, first_facts = _authority(
        tmp_path=tmp_path / "first",
        factory_run_id="factory-run-1",
    )
    second_port, _, _, second_facts = _authority(
        tmp_path=tmp_path / "second",
        factory_run_id="factory-run-2",
    )

    first = await first_port.acquire_cutoff(_authorized_request(first_port))
    second = await second_port.acquire_cutoff(_authorized_request(second_port))

    assert first.request_freeze_id == second.request_freeze_id == "freeze-1"
    assert first.factory_run_id == "factory-run-1"
    assert second.factory_run_id == "factory-run-2"
    assert first.authority_stream != second.authority_stream
    assert first.cutoff_fact_sequence == second.cutoff_fact_sequence
    assert first.cutoff_fact_sequence >= 2
    assert len(first_facts.events) == first.cutoff_fact_sequence
    assert len(second_facts.events) == second.cutoff_fact_sequence


@pytest.mark.asyncio
async def test_same_freeze_concurrency_appends_once(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(tmp_path=tmp_path)

    first, second = await asyncio.gather(
        port.acquire_cutoff(_authorized_request(port)),
        port.acquire_cutoff(_authorized_request(port)),
    )

    assert first == second
    assert resolver.calls == 1
    assert len(facts.events) == first.cutoff_fact_sequence
    assert sum(event["event_type"] == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE for event in facts.events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "status", "error"),
    [
        ({"current_stage": "quality_gate", "factory_stage_in_flight": True}, FactoryRunStatus.RUNNING, "stage"),
        (
            {"current_stage": "director_dispatch", "factory_stage_in_flight": False},
            FactoryRunStatus.RUNNING,
            "in_flight",
        ),
        ({"current_stage": "director_dispatch", "factory_stage_in_flight": True}, FactoryRunStatus.PAUSED, "status"),
    ],
)
async def test_run_projection_must_be_current_and_inflight(
    tmp_path: Path,
    metadata: dict[str, object],
    status: FactoryRunStatus,
    error: str,
) -> None:
    run = _run(status=status)
    run.metadata = metadata
    port, _, resolver, facts = _authority(tmp_path=tmp_path, run=run)

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match=error):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.events == []


@pytest.mark.asyncio
async def test_factory_run_identity_mismatch_conveys_no_ack(tmp_path: Path) -> None:
    port, _, resolver, facts = _authority(
        tmp_path=tmp_path,
        run=_run(factory_run_id="other-factory-run"),
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="run_missing_or_mismatched"):
        await port.acquire_cutoff(_authorized_request(port))

    assert resolver.calls == 0
    assert facts.events == []


@pytest.mark.asyncio
async def test_unavailable_or_malformed_resolver_conveys_no_ack(tmp_path: Path) -> None:
    unavailable_port, _, _, unavailable_facts = _authority(
        tmp_path=tmp_path / "unavailable",
        resolver=UnavailableFactoryRoleEvidenceSourceAuthority(),
    )
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="source_authority_unavailable"):
        await unavailable_port.acquire_cutoff(_authorized_request(unavailable_port))
    assert unavailable_facts.events == []

    malformed = _Resolver(result={"raw": "not-typed"})
    malformed_port, _, _, malformed_facts = _authority(
        tmp_path=tmp_path / "malformed",
        resolver=malformed,
    )
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="source_cut_type_invalid"):
        await malformed_port.acquire_cutoff(_authorized_request(malformed_port))
    assert malformed_facts.events == []


@pytest.mark.asyncio
async def test_append_failure_conveys_no_ack(tmp_path: Path) -> None:
    append_facts = _MemoryFactStream()
    append_facts.fail_append = True
    append_port, _, _, _ = _authority(tmp_path=tmp_path / "append", facts=append_facts)
    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="append_failed"):
        await append_port.acquire_cutoff(_authorized_request(append_port))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt_field",
    [
        "event_type",
        "idempotency_key",
        "payload_body_hash",
        "event_id",
        "global_seq",
        "event_hash",
    ],
)
async def test_strict_reread_validates_every_event_locator_and_body_binding(
    tmp_path: Path,
    corrupt_field: str,
) -> None:
    reread_facts = _MemoryFactStream()
    reread_facts.corrupt_reread = corrupt_field
    reread_port, _, _, _ = _authority(tmp_path=tmp_path / corrupt_field, facts=reread_facts)
    with pytest.raises(FactoryRoleEvidenceAuthorityError):
        await reread_port.acquire_cutoff(_authorized_request(reread_port))
