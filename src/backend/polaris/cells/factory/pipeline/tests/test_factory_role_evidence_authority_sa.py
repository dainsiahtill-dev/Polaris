"""A009B1 fenced Factory role-evidence cutoff authority tests."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from polaris.cells.events.fact_stream.public import (
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptLiveControlPort,
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
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.kernelone.events.final_request_evidence import (
    canonical_role_final_request_hash,
    role_final_request_policy,
)

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
