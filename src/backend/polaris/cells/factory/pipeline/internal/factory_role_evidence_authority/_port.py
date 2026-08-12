"""FactoryRoleEvidenceAuthorityPort — live cutoff authority implementation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import threading
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptLiveControlPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryRun, FactoryRunStatus
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffRequestV1,
    FactoryRoleEvidenceCutoffSourceHeadV1 as PublicFactoryRoleEvidenceCutoffSourceHeadV1,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    role_final_request_policy,
)

from ._constants import (
    _AUTHORITY_SOURCE,
    _MAX_CUTOFF_BODY_BYTES,
    _MAX_REQUEST_FREEZES_PER_GRANT,
    _STAGE_ROLE_AND_GRANT_CAP,
    FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
    FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
    FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
    FACTORY_ROLE_EVIDENCE_EXECUTION_AUTHORITY_SCHEMA,
)
from ._models import (
    FactoryRoleEvidenceCutoffBodyV1,
    FactoryRoleEvidenceResolvedCutV1,
    FactoryRoleEvidenceStageAuthorityV1,
    _canonical_cutoff_body_bytes,
    _CutoffCommitManifest,
    _CutoffFragmentPayload,
    _fragment_cutoff_body,
    _request_authority_hash,
)
from ._primitives import (
    _T,
    FactoryRoleEvidenceAuthorityError,
    _FactoryRoleEvidenceGrantState,
    _hash64,
    _locator,
    _positive_int,
    _text,
    factory_role_evidence_authority_stream,
)
from ._source import (
    FactoryRoleEvidenceFactStream,
    FactoryRoleEvidenceSourceAuthority,
    _AuthorityScan,
    _fragment_vector_hash,
    _PartialCutoff,
    _PublicFactoryRoleEvidenceFactStream,
    _StoredCutoff,
    _StoredFragment,
)


class FactoryRoleEvidenceAuthorityPort:
    """Factory-owned A009B1 implementation of the async cutoff port."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        run_lock: asyncio.Lock,
        run_loader: Callable[[], Awaitable[FactoryRun | None]],
        admission: FactoryWorkspaceRunAdmission,
        source_authority: FactoryRoleEvidenceSourceAuthority,
        fact_stream: FactoryRoleEvidenceFactStream | None = None,
        physical_attempt_coordinator: FactoryPhysicalAttemptLiveControlPort,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if type(authority) is not FactoryRoleEvidenceStageAuthorityV1:
            raise TypeError("factory_role_evidence_stage_authority_exact_type_required")
        FactoryRoleEvidenceStageAuthorityV1.__post_init__(authority)
        self._authority = authority
        self._run_lock = run_lock
        self._owner_loop_guard = threading.Lock()
        try:
            self._owner_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._owner_loop = None
        self._run_loader = run_loader
        self._admission = admission
        self._source_authority = source_authority
        self._facts = fact_stream or _PublicFactoryRoleEvidenceFactStream()
        if type(physical_attempt_coordinator) is not FactoryPhysicalAttemptLiveControlPort:
            raise TypeError("factory_physical_attempt_control_port_exact_type_required")
        if physical_attempt_coordinator.factory_run_id != authority.factory_run_id:
            raise ValueError("factory_physical_attempt_factory_run_mismatch")
        self._physical_attempt_coordinator = physical_attempt_coordinator
        self._logical_stream = factory_role_evidence_authority_stream(authority.factory_run_id)
        self._grant_lock = threading.RLock()
        self._acquisition_condition = threading.Condition(self._grant_lock)
        self._grants: dict[str, _FactoryRoleEvidenceGrantState] = {}
        self._active_acquisitions = 0
        self._closed = False

    def _authority_owner_loop(self) -> asyncio.AbstractEventLoop:
        """Return the Factory loop that owns ``_run_lock``.

        Role attempts can execute in worker event loops, but the cutoff authority
        must remain serialized with the Factory run lifecycle.  Production
        captures that loop at construction.  Synchronous test setup has no owner
        loop to preserve, so its individual operations retain their calling loop.
        """

        current_loop = asyncio.get_running_loop()
        with self._owner_loop_guard:
            owner_loop = self._owner_loop
            if owner_loop is None:
                return current_loop
        if owner_loop.is_closed():
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_owner_loop_unavailable")
        return owner_loop

    async def _run_on_authority_owner_loop(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Execute a cutoff operation on the loop that owns the Factory lock."""

        current_loop = asyncio.get_running_loop()
        owner_loop = self._authority_owner_loop()
        if current_loop is owner_loop:
            return await operation()
        if not owner_loop.is_running():
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_owner_loop_unavailable")

        async def invoke() -> _T:
            return await operation()

        scheduled = asyncio.run_coroutine_threadsafe(invoke(), owner_loop)
        try:
            return await asyncio.wrap_future(scheduled)
        except asyncio.CancelledError:
            scheduled.cancel()
            raise

    def _stage_role_and_cap(self) -> tuple[str, int]:
        policy = _STAGE_ROLE_AND_GRANT_CAP.get(self._authority.stage)
        if policy is None:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_unsupported")
        return policy

    def _grant_hash(self, *, role: str, grant_nonce: str) -> str:
        authority = self._authority
        return canonical_role_final_request_hash(
            {
                "schema_version": FACTORY_ROLE_EVIDENCE_EXECUTION_AUTHORITY_SCHEMA,
                "factory_run_id": authority.factory_run_id,
                "stage": authority.stage,
                "workspace_fencing_token": authority.workspace_fencing_token,
                "stage_claim_attempt": authority.stage_claim_attempt,
                "stage_claim_nonce": authority.stage_claim_nonce,
                "role": role,
                "attempt_budget": FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
                "grant_nonce": grant_nonce,
            }
        )

    def mint_authority_binding(self, role: str) -> FactoryRoleEvidenceAuthorityBindingV1:
        """Mint one unique role-task grant under the immutable live-stage claim."""

        try:
            normalized_role = role_final_request_policy(role).role
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_invalid") from exc
        expected_role, grant_cap = self._stage_role_and_cap()
        if normalized_role != expected_role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_mismatch")
        with self._grant_lock:
            if self._closed:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
            if len(self._grants) >= grant_cap:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_grant_cardinality_exceeded")
            for _attempt in range(8):
                grant_nonce = secrets.token_hex(16)
                execution_authority_hash = self._grant_hash(role=normalized_role, grant_nonce=grant_nonce)
                if execution_authority_hash not in self._grants:
                    break
            else:  # pragma: no cover - cryptographic collision fail-closed guard
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_identity_exhausted")
            self._grants[execution_authority_hash] = _FactoryRoleEvidenceGrantState(
                grant_nonce=grant_nonce,
                role=normalized_role,
                attempt_budget=FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
                execution_authority_hash=execution_authority_hash,
            )
            self._physical_attempt_coordinator.register_grant(
                FactoryPhysicalAttemptGrantViewV1(
                    schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
                    verification_scope="factory",
                    factory_run_id=self._authority.factory_run_id,
                    role=normalized_role,
                    stage=self._authority.stage,
                    workspace_fencing_token=self._authority.workspace_fencing_token,
                    stage_claim_attempt=self._authority.stage_claim_attempt,
                    stage_claim_nonce=self._authority.stage_claim_nonce,
                    execution_authority_hash=execution_authority_hash,
                    attempt_budget=FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
                )
            )
        return FactoryRoleEvidenceAuthorityBindingV1(
            schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
            verification_scope="factory",
            factory_run_id=self._authority.factory_run_id,
            role=normalized_role,
            cutoff_port=self,
            physical_attempt_control_port=self._physical_attempt_coordinator,
            attempt_budget=FACTORY_ROLE_EVIDENCE_ATTEMPT_BUDGET,
            execution_authority_hash=execution_authority_hash,
        )

    def require_grant_capacity(self, role: str, count: int) -> None:
        """Preflight a complete stage-local fanout before any child is created."""

        try:
            normalized_role = role_final_request_policy(role).role
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_invalid") from exc
        if type(count) is not int or count < 0:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_capacity_count_invalid")
        expected_role, grant_cap = self._stage_role_and_cap()
        if normalized_role != expected_role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_mismatch")
        with self._grant_lock:
            if self._closed:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
            if len(self._grants) + count > grant_cap:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_grant_cardinality_exceeded")

    def revoke_authority_binding(self, binding: FactoryRoleEvidenceAuthorityBindingV1) -> None:
        """Revoke one minted grant whose role-task creation never completed."""

        if type(binding) is not FactoryRoleEvidenceAuthorityBindingV1:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_binding_type_invalid")
        FactoryRoleEvidenceAuthorityBindingV1.__post_init__(binding)
        if binding.cutoff_port is not self or binding.factory_run_id != self._authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_binding_owner_mismatch")
        with self._grant_lock:
            grant = self._grants.get(binding.execution_authority_hash)
            if grant is None:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_execution_authority_hash_mismatch")
            if binding.role != grant.role or binding.attempt_budget != grant.attempt_budget:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_binding_identity_mismatch")
            grant.revoked = True
            self._physical_attempt_coordinator.revoke_grant(binding.execution_authority_hash)

    def close_authority(self) -> None:
        """Publish closure, then wait until every registered acquisition drains."""

        with self._acquisition_condition:
            self._closed = True
            for grant in self._grants.values():
                grant.revoked = True
                self._physical_attempt_coordinator.close_grant(grant.execution_authority_hash)
            while self._active_acquisitions:
                self._acquisition_condition.wait()

    def _require_authorized_request_locked(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
        *,
        expected_role: str,
    ) -> None:
        if request.role != expected_role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_role_mismatch")
        if self._closed:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
        grant = self._grants.get(request.execution_authority_hash)
        if grant is None:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_execution_authority_hash_mismatch")
        expected_hash = self._grant_hash(role=grant.role, grant_nonce=grant.grant_nonce)
        if expected_hash != request.execution_authority_hash or grant.execution_authority_hash != expected_hash:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_execution_authority_hash_mismatch")
        if grant.revoked:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_revoked")
        if request.role != grant.role:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_role_mismatch")
        if request.attempt_budget != grant.attempt_budget:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_attempt_budget_mismatch")

    def _begin_acquisition(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Atomically validate authority and register a close-drained lease."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._acquisition_condition:
            self._require_authorized_request_locked(request, expected_role=expected_role)
            self._active_acquisitions += 1

    def _preflight_authorized_request(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Reject invalid authority before awaiting run access or producing effects."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._grant_lock:
            self._require_authorized_request_locked(request, expected_role=expected_role)

    def _end_acquisition(self) -> None:
        with self._acquisition_condition:
            self._end_acquisition_locked()

    def _end_acquisition_locked(self) -> None:
        if self._active_acquisitions <= 0:  # pragma: no cover - internal invariant guard
            raise RuntimeError("factory_role_evidence_acquisition_lease_underflow")
        self._active_acquisitions -= 1
        if self._active_acquisitions == 0:
            self._acquisition_condition.notify_all()

    def _require_acquisition_live(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Revalidate closure/revocation at every persistent-effect boundary."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._grant_lock:
            self._require_authorized_request_locked(request, expected_role=expected_role)

    def _append_authorized_commit(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        partial: _PartialCutoff,
        expected_sequence: int,
    ) -> SegmentedFactEventAppendedV1:
        """Linearize authority validation and the durable cutoff commit append."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._acquisition_condition:
            self._require_authorized_request_locked(request, expected_role=expected_role)
            return self._append_commit(partial=partial, expected_sequence=expected_sequence)

    def _publish_ack_and_end_acquisition(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        """Atomically authorize ACK publication and release its acquisition lease."""

        expected_role, _grant_cap = self._stage_role_and_cap()
        with self._acquisition_condition:
            self._require_authorized_request_locked(request, expected_role=expected_role)
            self._end_acquisition_locked()
            return ack

    def _bind_live_request_identity(self, request: FactoryRoleEvidenceCutoffRequestV1) -> None:
        """Bind child run/freeze only after the stage claim has been revalidated."""

        with self._grant_lock:
            grant = self._grants.get(request.execution_authority_hash)
            if self._closed:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_authority_closed")
            if grant is None or grant.revoked:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_grant_revoked")
            if grant.controlled_child_run_id and grant.controlled_child_run_id != request.run_id:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_controlled_child_run_mismatch")
            if (
                request.request_freeze_id not in grant.request_freeze_ids
                and len(grant.request_freeze_ids) >= _MAX_REQUEST_FREEZES_PER_GRANT
            ):
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_request_freeze_cardinality_exceeded")
            if not grant.controlled_child_run_id:
                grant.controlled_child_run_id = request.run_id
            grant.request_freeze_ids.add(request.request_freeze_id)

    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        owner_loop = self._authority_owner_loop()
        if asyncio.get_running_loop() is not owner_loop:
            return await self._run_on_authority_owner_loop(lambda: self.acquire_cutoff(request))
        if type(request) is not FactoryRoleEvidenceCutoffRequestV1:
            raise TypeError("factory_role_evidence_cutoff_request_exact_type_required")
        FactoryRoleEvidenceCutoffRequestV1.__post_init__(request)
        self._preflight_authorized_request(request)
        async with self._run_lock:
            run = await self._run_loader()
            self._begin_acquisition(request)
            lease_active = True
            try:
                self._require_current_run(run)
                authority = self._authority
                with self._admission.hold_active_stage_claim(
                    authority.factory_run_id,
                    fencing_token=authority.workspace_fencing_token,
                    stage=authority.stage,
                    attempt=authority.stage_claim_attempt,
                    nonce=authority.stage_claim_nonce,
                ) as revalidate_claim:
                    self._bind_live_request_identity(request)
                    self._require_acquisition_live(request)
                    ready = self._ensure_ledger()
                    scan = self._scan_authority_events()
                    if ready.head != scan.captured_head:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_head_mismatch")
                    replay = scan.stored.get(request.request_freeze_id)
                    if replay is not None:
                        if not self._same_request_and_authority(replay.body, request):
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                        revalidate_claim()
                        self._require_acquisition_live(request)
                        ack = self._publish_ack_and_end_acquisition(
                            request=request,
                            ack=self._ack(replay),
                        )
                        lease_active = False
                        return ack

                    request_hash = _request_authority_hash(request, authority)
                    partial = scan.partial.get(request.request_freeze_id)
                    if partial is not None:
                        if partial.request_authority_hash != request_hash:
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                        if partial.body is None or partial.fragment_vector_hash is None:
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_partial_incomplete")
                        if not self._same_request_and_authority(partial.body, request):
                            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                        self._require_unchanged_head(scan.captured_head)
                        revalidate_claim()
                        self._require_acquisition_live(request)
                        commit = self._append_authorized_commit(
                            request=request,
                            partial=partial,
                            expected_sequence=scan.captured_head.next_expected_global_seq,
                        )
                        ack = self._strict_reread_ack(
                            request=request,
                            expected_body=partial.body,
                            expected_body_hash=partial.body_hash,
                            expected_fragment_count=partial.fragment_count,
                            expected_fragment_vector_hash=partial.fragment_vector_hash,
                            commit=commit,
                        )
                        revalidate_claim()
                        self._require_acquisition_live(request)
                        ack = self._publish_ack_and_end_acquisition(request=request, ack=ack)
                        lease_active = False
                        return ack

                    self._require_acquisition_live(request)
                    # Exit stage-claim flock BEFORE any await.  Holding the OS
                    # flock across await lets heartbeat renew (or another
                    # cutoff) block the same event-loop thread that must resume
                    # to release the claim — process-wide self-deadlock
                    # (R142 locks_lock_inode_wait / GET 30s / keepalive 1011).
                    frozen_run = cast(FactoryRun, run)
                    frozen_authority = authority
                    frozen_request_hash = request_hash

                # Stage claim released: resolve off-loop without admission flock.
                try:
                    resolved = await asyncio.to_thread(
                        self._source_authority.resolve_source_cut,
                        request=request,
                        authority=frozen_authority,
                        factory_run=frozen_run,
                    )
                except FactoryRoleEvidenceAuthorityError:
                    raise
                except Exception as exc:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_resolver_failed") from exc
                if type(resolved) is not FactoryRoleEvidenceResolvedCutV1:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_cut_type_invalid")
                try:
                    FactoryRoleEvidenceResolvedCutV1.__post_init__(resolved)
                except (TypeError, ValueError) as exc:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_cut_invalid") from exc
                if resolved.role != request.role:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_source_cut_role_mismatch")

                body = FactoryRoleEvidenceCutoffBodyV1(
                    factory_run_id=frozen_authority.factory_run_id,
                    request=request,
                    authority=frozen_authority,
                    resolved_source_cut=resolved,
                )
                _raw, body_hash, fragment_payloads = _fragment_cutoff_body(body)

                # Multi-fragment fsync appends under stage claim must not run on
                # the asyncio event loop: each append holds the segmented fact
                # stream lock for durability, and concurrent heartbeat/settlement
                # queries time out at the default 2s budget (R143/R144
                # factory_role_evidence_cutoff_append_failed).  Execute the whole
                # claim+write critical section off-loop on one worker thread.
                try:
                    ack = await asyncio.to_thread(
                        self._finalize_cutoff_after_resolve,
                        request=request,
                        frozen_authority=frozen_authority,
                        frozen_request_hash=frozen_request_hash,
                        body=body,
                        body_hash=body_hash,
                        fragment_payloads=fragment_payloads,
                    )
                except FactoryRoleEvidenceAuthorityError:
                    raise
                except Exception as exc:
                    # Preserve lease/admission conflicts (tests + live fail-closed
                    # paths expect the original conflict type, not a wrap into
                    # append_failed).
                    from polaris.cells.factory.pipeline.public.contracts import (
                        FactoryWorkspaceRunLeaseConflictError,
                    )

                    if isinstance(exc, FactoryWorkspaceRunLeaseConflictError):
                        raise
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_failed") from exc
                lease_active = False
                return ack
            finally:
                if lease_active:
                    self._end_acquisition()

    def _finalize_cutoff_after_resolve(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        frozen_authority: FactoryRoleEvidenceStageAuthorityV1,
        frozen_request_hash: str,
        body: FactoryRoleEvidenceCutoffBodyV1,
        body_hash: str,
        fragment_payloads: tuple[Any, ...],
    ) -> FactoryRoleEvidenceCutoffAckV1:
        """Claim + durable fragment/commit path; must run fully sync on one thread."""

        with self._admission.hold_active_stage_claim(
            frozen_authority.factory_run_id,
            fencing_token=frozen_authority.workspace_fencing_token,
            stage=frozen_authority.stage,
            attempt=frozen_authority.stage_claim_attempt,
            nonce=frozen_authority.stage_claim_nonce,
        ) as revalidate_claim:
            revalidate_claim()
            self._require_acquisition_live(request)
            rescan = self._scan_authority_events()
            replay_after = rescan.stored.get(request.request_freeze_id)
            if replay_after is not None:
                if not self._same_request_and_authority(replay_after.body, request):
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                revalidate_claim()
                self._require_acquisition_live(request)
                return self._publish_ack_and_end_acquisition(
                    request=request,
                    ack=self._ack(replay_after),
                )
            partial_after = rescan.partial.get(request.request_freeze_id)
            if partial_after is not None:
                if partial_after.request_authority_hash != frozen_request_hash:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                if partial_after.body is None or partial_after.fragment_vector_hash is None:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_partial_incomplete")
                if not self._same_request_and_authority(partial_after.body, request):
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_replay_conflict")
                self._require_unchanged_head(rescan.captured_head)
                revalidate_claim()
                self._require_acquisition_live(request)
                commit = self._append_authorized_commit(
                    request=request,
                    partial=partial_after,
                    expected_sequence=rescan.captured_head.next_expected_global_seq,
                )
                ack = self._strict_reread_ack(
                    request=request,
                    expected_body=partial_after.body,
                    expected_body_hash=partial_after.body_hash,
                    expected_fragment_count=partial_after.fragment_count,
                    expected_fragment_vector_hash=partial_after.fragment_vector_hash,
                    commit=commit,
                )
                revalidate_claim()
                self._require_acquisition_live(request)
                return self._publish_ack_and_end_acquisition(request=request, ack=ack)

            self._require_unchanged_head(rescan.captured_head)
            expected_sequence = rescan.captured_head.next_expected_global_seq
            persisted_fragments: list[_StoredFragment] = []
            for fragment_payload in fragment_payloads:
                revalidate_claim()
                self._require_acquisition_live(request)
                appended = self._append_event(
                    event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
                    payload=fragment_payload.to_record(),
                    idempotency_key=(
                        f"role-evidence-cutoff:{request.request_freeze_id}:fragment:{fragment_payload.index}"
                    ),
                    expected_sequence=expected_sequence,
                )
                self._require_acquisition_live(request)
                persisted_fragments.append(
                    _StoredFragment(
                        event_id=appended.event_id,
                        sequence=appended.global_seq,
                        event_hash=appended.event_hash,
                        payload=fragment_payload,
                    )
                )
                expected_sequence += 1
            fragments = tuple(persisted_fragments)
            vector_hash = _fragment_vector_hash(fragments)
            partial = _PartialCutoff(
                request_authority_hash=frozen_request_hash,
                body_hash=body_hash,
                fragment_count=len(fragments),
                fragments=fragments,
                body=body,
                fragment_vector_hash=vector_hash,
            )
            revalidate_claim()
            self._require_acquisition_live(request)
            commit = self._append_authorized_commit(
                request=request,
                partial=partial,
                expected_sequence=expected_sequence,
            )
            ack = self._strict_reread_ack(
                request=request,
                expected_body=body,
                expected_body_hash=body_hash,
                expected_fragment_count=len(fragments),
                expected_fragment_vector_hash=vector_hash,
                commit=commit,
            )
            revalidate_claim()
            self._require_acquisition_live(request)
            return self._publish_ack_and_end_acquisition(request=request, ack=ack)

    async def resolve_cutoff_proof(
        self,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        """Strictly re-read one committed ACK locator into a detached proof."""

        owner_loop = self._authority_owner_loop()
        if asyncio.get_running_loop() is not owner_loop:
            return await self._run_on_authority_owner_loop(lambda: self.resolve_cutoff_proof(ack))
        if type(ack) is not FactoryRoleEvidenceCutoffAckV1:
            raise TypeError("factory_role_evidence_cutoff_ack_exact_type_required")
        try:
            FactoryRoleEvidenceCutoffAckV1.__post_init__(ack)
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_ack_invalid") from exc
        if ack.factory_run_id != self._authority.factory_run_id or ack.authority_stream != self._logical_stream:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_ack_namespace_mismatch")
        async with self._run_lock:
            run = await self._run_loader()
            self._require_current_run(run)
            authority = self._authority
            with self._admission.hold_active_stage_claim(
                authority.factory_run_id,
                fencing_token=authority.workspace_fencing_token,
                stage=authority.stage,
                attempt=authority.stage_claim_attempt,
                nonce=authority.stage_claim_nonce,
            ) as revalidate_claim:
                reread = self._scan_authority_events()
                stored = reread.stored.get(ack.request_freeze_id)
                if stored is None:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_not_found")
                derived_ack = self._ack(stored)
                if derived_ack != ack:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_proof_ack_mismatch")
                revalidate_claim()
                proof = self._proof_from_stored(stored, derived_ack)
                revalidate_claim()
                return proof

    @staticmethod
    def _proof_from_stored(
        stored: _StoredCutoff,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        source_heads: list[PublicFactoryRoleEvidenceCutoffSourceHeadV1] = []
        policy_slots: list[RoleFinalRequestEvidenceSlotV1] = []
        for source_slot in stored.body.resolved_source_cut.slots:
            source_head = source_slot.source_head
            source_heads.append(
                PublicFactoryRoleEvidenceCutoffSourceHeadV1(
                    canonical_source_ref=source_head.canonical_source_ref,
                    source_fact_schema=source_head.source_fact_schema,
                    source_fact_version=source_head.source_fact_version,
                    source_head_fact_id=source_head.source_head_fact_id,
                    source_head_sequence=source_head.source_head_sequence,
                    source_head_hash=source_head.source_head_hash,
                )
            )
            anchors = tuple(
                RoleFinalRequestEvidenceAnchorV1.create(
                    ref_kind=item.ref_kind,
                    canonical_source_ref=source_head.canonical_source_ref,
                    canonical_ref=item.canonical_ref,
                    canonical_hash=item.canonical_hash,
                    source_fact_schema=source_head.source_fact_schema,
                    source_fact_version=source_head.source_fact_version,
                    factory_run_id=ack.factory_run_id,
                    run_id=ack.run_id,
                    role=ack.role,
                    request_freeze_id=ack.request_freeze_id,
                    cutoff_fact_id=ack.cutoff_fact_id,
                    cutoff_fact_sequence=ack.cutoff_fact_sequence,
                    cutoff_fact_hash=ack.cutoff_fact_hash,
                    source_fact_id=item.source_fact_id,
                    source_fact_sequence=item.source_fact_sequence,
                    source_fact_hash=item.source_fact_hash,
                    source_head_sequence=source_head.source_head_sequence,
                    source_head_hash=source_head.source_head_hash,
                    execution_authority_hash=ack.execution_authority_hash,
                )
                for item in source_slot.items
            )
            policy_slots.append(
                RoleFinalRequestEvidenceSlotV1.create(
                    ref_kind=source_slot.ref_kind,
                    state=source_slot.state,
                    canonical_source_ref=source_head.canonical_source_ref,
                    source_fact_schema=source_head.source_fact_schema,
                    source_fact_version=source_head.source_fact_version,
                    factory_run_id=ack.factory_run_id,
                    run_id=ack.run_id,
                    role=ack.role,
                    request_freeze_id=ack.request_freeze_id,
                    cutoff_fact_id=ack.cutoff_fact_id,
                    cutoff_fact_sequence=ack.cutoff_fact_sequence,
                    cutoff_fact_hash=ack.cutoff_fact_hash,
                    source_head_sequence=source_head.source_head_sequence,
                    source_head_hash=source_head.source_head_hash,
                    execution_authority_hash=ack.execution_authority_hash,
                    items=anchors,
                )
            )
        facts = RoleFinalRequestPolicyFactsV1.create(role=ack.role, slots=policy_slots)
        return FactoryRoleEvidenceCutoffProofV1.create(
            ack=ack,
            source_head_vector=tuple(source_heads),
            policy_facts=facts,
        )

    def _require_current_run(self, run: FactoryRun | None) -> None:
        authority = self._authority
        if type(run) is not FactoryRun:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_type_invalid")
        current_run = cast(FactoryRun, run)
        if type(current_run.id) is not str or current_run.id != authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_missing_or_mismatched")
        if type(current_run.status) is not FactoryRunStatus:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_status_invalid")
        if current_run.status not in {FactoryRunStatus.RUNNING, FactoryRunStatus.RECOVERING}:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_status_invalid")
        metadata = current_run.metadata
        if type(metadata) is not dict or any(type(key) is not str for key in metadata):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_metadata_invalid")
        current_stage = metadata.get("current_stage")
        if type(current_stage) is not str or current_stage != authority.stage:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_run_stage_mismatch")
        if metadata.get("factory_stage_in_flight") is not True:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_stage_not_in_flight")

    def _ensure_ledger(self) -> SegmentedFactLedgerReadyV1:
        try:
            ready = self._facts.ensure(
                EnsureSegmentedFactLedgerCommandV1(
                    workspace=str(self._workspace),
                    logical_stream=self._logical_stream,
                    maintenance_reason="factory_role_evidence_cutoff_authority",
                    retention="pinned_audit_no_delete",
                )
            )
        except Exception as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_unavailable") from exc
        if type(ready) is SegmentedFactLedgerReadyV1:
            try:
                SegmentedFactLedgerReadyV1.__post_init__(ready)
            except (TypeError, ValueError) as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_corrupt") from exc
        if (
            type(ready) is not SegmentedFactLedgerReadyV1
            or ready.workspace != str(self._workspace)
            or ready.logical_stream != self._logical_stream
            or ready.retention != "pinned_audit_no_delete"
            or ready.storage_prefix != ready.head.storage_prefix
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_ledger_corrupt")
        self._validate_head(ready.head)
        return ready

    def _validate_head(self, head: object) -> SegmentedFactLedgerHeadV1:
        if type(head) is not SegmentedFactLedgerHeadV1:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_type_invalid")
        validated_head = cast(SegmentedFactLedgerHeadV1, head)
        try:
            SegmentedFactLedgerHeadV1.__post_init__(validated_head)  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_invalid") from exc
        if (
            validated_head.workspace != str(self._workspace)
            or validated_head.logical_stream != self._logical_stream
            or validated_head.retention != "pinned_audit_no_delete"
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_identity_mismatch")
        return validated_head

    def _scan_authority_events(self) -> _AuthorityScan:
        continuation: str | None = None
        seen_continuations: set[str] = set()
        events: list[dict[str, Any]] = []
        captured_head: SegmentedFactLedgerHeadV1 | None = None
        while True:
            try:
                result = self._facts.query_events(
                    QuerySegmentedFactEventsV1(
                        workspace=str(self._workspace),
                        logical_stream=self._logical_stream,
                        limit=511,
                        continuation=continuation,
                        strict_integrity=True,
                    )
                )
            except Exception as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_strict_scan_failed") from exc
            if type(result) is SegmentedFactQueryResultV1:
                try:
                    SegmentedFactQueryResultV1.__post_init__(result)
                except (TypeError, ValueError) as exc:
                    raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_strict_scan_corrupt") from exc
            if (
                type(result) is not SegmentedFactQueryResultV1
                or result.workspace != str(self._workspace)
                or result.logical_stream != self._logical_stream
            ):
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_strict_scan_corrupt")
            self._validate_head(result.captured_head)
            if captured_head is None:
                captured_head = result.captured_head
            elif result.captured_head != captured_head:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_scan_head_drift")
            events.extend(result.events)
            continuation = result.continuation
            if continuation is None:
                break
            if continuation in seen_continuations:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_scan_continuation_cycle")
            seen_continuations.add(continuation)
        assert captured_head is not None
        if len(events) != captured_head.total_count:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_scan_count_mismatch")
        fragment_metadata: dict[str, tuple[str, str, int]] = {}
        fragment_groups: dict[str, dict[int, _StoredFragment]] = {}
        stored: dict[str, _StoredCutoff] = {}
        for expected_sequence, event in enumerate(events, start=1):
            try:
                event_type = event.get("event_type") if type(event) is dict else None
                if event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE:
                    fragment = self._parse_fragment_event(event, expected_sequence=expected_sequence)
                    freeze_id = fragment.payload.request_freeze_id
                    if freeze_id in stored:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_fragment_after_commit")
                    fragment_metadata_value = (
                        fragment.payload.request_authority_hash,
                        fragment.payload.cutoff_body_hash,
                        fragment.payload.count,
                    )
                    existing_metadata = fragment_metadata.setdefault(
                        freeze_id,
                        fragment_metadata_value,
                    )
                    if existing_metadata != fragment_metadata_value:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_fragment_group_conflict")
                    fragment_group = fragment_groups.setdefault(freeze_id, {})
                    if fragment.payload.index in fragment_group:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_duplicate_fragment")
                    fragment_group[fragment.payload.index] = fragment
                    continue
                if event_type == FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE:
                    manifest, event_id, sequence, event_hash = self._parse_commit_event(
                        event,
                        expected_sequence=expected_sequence,
                    )
                    freeze_id = manifest.request_freeze_id
                    if freeze_id in stored:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_duplicate_freeze")
                    commit_metadata = fragment_metadata.get(freeze_id)
                    commit_group = fragment_groups.get(freeze_id)
                    if commit_metadata is None or commit_group is None:
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_commit_without_fragments")
                    commit_partial = self._build_partial(
                        metadata=commit_metadata,
                        indexed_fragments=commit_group,
                    )
                    if commit_partial.body is None or commit_partial.fragment_vector_hash is None:
                        raise FactoryRoleEvidenceAuthorityError(
                            "factory_role_evidence_cutoff_commit_fragments_incomplete"
                        )
                    if (
                        manifest.factory_run_id != self._authority.factory_run_id
                        or manifest.request_authority_hash != commit_partial.request_authority_hash
                        or manifest.cutoff_body_hash != commit_partial.body_hash
                        or manifest.fragment_count != commit_partial.fragment_count
                        or manifest.cutoff_fragment_vector_hash != commit_partial.fragment_vector_hash
                    ):
                        raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_commit_manifest_mismatch")
                    stored[freeze_id] = _StoredCutoff(
                        event_id=event_id,
                        sequence=sequence,
                        event_hash=event_hash,
                        body_hash=commit_partial.body_hash,
                        body=commit_partial.body,
                        fragment_count=commit_partial.fragment_count,
                        fragment_vector_hash=commit_partial.fragment_vector_hash,
                    )
                    continue
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_type_mismatch")
            except FactoryRoleEvidenceAuthorityError:
                raise
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_malformed") from exc
        partial_cutoffs = {
            freeze_id: self._build_partial(
                metadata=metadata,
                indexed_fragments=fragment_groups[freeze_id],
            )
            for freeze_id, metadata in fragment_metadata.items()
            if freeze_id not in stored
        }
        return _AuthorityScan(stored=stored, partial=partial_cutoffs, captured_head=captured_head)

    def _parse_event_locator(
        self,
        event: object,
        *,
        expected_sequence: int,
        expected_event_type: str,
    ) -> tuple[Mapping[str, Any], str, int, str]:
        if type(event) is not dict:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_type_invalid")
        if event.get("logical_stream") != self._logical_stream:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_stream_mismatch")
        if event.get("event_type") != expected_event_type:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_type_mismatch")
        if event.get("source") != _AUTHORITY_SOURCE:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_source_mismatch")
        event_id = _locator("cutoff_event_id", event.get("event_id"))
        sequence = _positive_int("cutoff_event_sequence", event.get("global_seq"))
        if sequence != expected_sequence:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_event_sequence_mismatch")
        event_hash = _hash64("cutoff_event_hash", event.get("event_hash"))
        return event, event_id, sequence, event_hash

    def _parse_fragment_event(self, event: object, *, expected_sequence: int) -> _StoredFragment:
        value, event_id, sequence, event_hash = self._parse_event_locator(
            event,
            expected_sequence=expected_sequence,
            expected_event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_FRAGMENT_EVENT_TYPE,
        )
        payload = _CutoffFragmentPayload.from_record(value.get("payload"))
        if payload.factory_run_id != self._authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_factory_run_mismatch")
        expected_idempotency = f"role-evidence-cutoff:{payload.request_freeze_id}:fragment:{payload.index}"
        if value.get("idempotency_key") != expected_idempotency:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_idempotency_mismatch")
        return _StoredFragment(
            event_id=event_id,
            sequence=sequence,
            event_hash=event_hash,
            payload=payload,
        )

    def _parse_commit_event(
        self,
        event: object,
        *,
        expected_sequence: int,
    ) -> tuple[_CutoffCommitManifest, str, int, str]:
        value, event_id, sequence, event_hash = self._parse_event_locator(
            event,
            expected_sequence=expected_sequence,
            expected_event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
        )
        manifest = _CutoffCommitManifest.from_record(value.get("payload"))
        if value.get("idempotency_key") != f"role-evidence-cutoff:{manifest.request_freeze_id}":
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_idempotency_mismatch")
        return manifest, event_id, sequence, event_hash

    def _build_partial(
        self,
        *,
        metadata: tuple[str, str, int],
        indexed_fragments: Mapping[int, _StoredFragment],
    ) -> _PartialCutoff:
        request_hash, body_hash, fragment_count = metadata
        fragments = tuple(indexed_fragments[index] for index in sorted(indexed_fragments))
        complete = len(fragments) == fragment_count and tuple(
            fragment.payload.index for fragment in fragments
        ) == tuple(range(fragment_count))
        if not complete:
            return _PartialCutoff(
                request_authority_hash=request_hash,
                body_hash=body_hash,
                fragment_count=fragment_count,
                fragments=fragments,
                body=None,
                fragment_vector_hash=None,
            )
        raw = b"".join(fragment.payload.raw for fragment in fragments)
        if len(raw) > _MAX_CUTOFF_BODY_BYTES:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_body_too_large")
        if hashlib.sha256(raw).hexdigest() != body_hash:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_body_hash_mismatch")
        decoded = json.loads(raw.decode("utf-8"))
        if type(decoded) is not dict:
            raise ValueError("cutoff_body_mapping_required")
        canonical = _canonical_cutoff_body_bytes(decoded)
        if canonical != raw:
            raise ValueError("cutoff_body_bytes_not_canonical")
        body = FactoryRoleEvidenceCutoffBodyV1.from_record(decoded)
        if _canonical_cutoff_body_bytes(body.to_record()) != raw:
            raise ValueError("cutoff_body_roundtrip_mismatch")
        if body.factory_run_id != self._authority.factory_run_id:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_factory_run_mismatch")
        if _request_authority_hash(body.request, body.authority) != request_hash:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_request_authority_hash_mismatch")
        return _PartialCutoff(
            request_authority_hash=request_hash,
            body_hash=body_hash,
            fragment_count=fragment_count,
            fragments=fragments,
            body=body,
            fragment_vector_hash=_fragment_vector_hash(fragments),
        )

    def _require_unchanged_head(self, captured_head: SegmentedFactLedgerHeadV1) -> None:
        try:
            current_head = self._facts.query_head(
                QuerySegmentedFactLedgerHeadV1(
                    workspace=str(self._workspace),
                    logical_stream=self._logical_stream,
                )
            )
        except Exception as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_query_failed") from exc
        self._validate_head(current_head)
        if current_head != captured_head:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_head_drift")

    def _append_event(
        self,
        *,
        event_type: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        expected_sequence: int,
    ) -> SegmentedFactEventAppendedV1:
        try:
            appended = self._facts.append(
                AppendSegmentedFactEventCommandV1(
                    workspace=str(self._workspace),
                    logical_stream=self._logical_stream,
                    event_type=event_type,
                    source=_AUTHORITY_SOURCE,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    expected_global_seq=expected_sequence,
                    require_idempotency_replay=False,
                    durability="fsync",
                )
            )
        except Exception as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_failed") from exc
        if type(appended) is SegmentedFactEventAppendedV1:
            try:
                SegmentedFactEventAppendedV1.__post_init__(appended)
            except (TypeError, ValueError) as exc:
                raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_corrupt") from exc
        if (
            type(appended) is not SegmentedFactEventAppendedV1
            or appended.workspace != str(self._workspace)
            or appended.logical_stream != self._logical_stream
            or appended.global_seq != expected_sequence
            or appended.segment_index < 0
            or appended.local_seq <= 0
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_corrupt")
        try:
            _locator("cutoff_event_id", appended.event_id)
            _hash64("cutoff_event_hash", appended.event_hash)
            _text("cutoff_event_appended_at", appended.appended_at)
        except (TypeError, ValueError) as exc:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_append_corrupt") from exc
        return appended

    def _append_commit(
        self,
        *,
        partial: _PartialCutoff,
        expected_sequence: int,
    ) -> SegmentedFactEventAppendedV1:
        if partial.body is None or partial.fragment_vector_hash is None:
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_partial_incomplete")
        manifest = _CutoffCommitManifest(
            factory_run_id=partial.body.factory_run_id,
            request_freeze_id=partial.body.request.request_freeze_id,
            request_authority_hash=partial.request_authority_hash,
            cutoff_body_hash=partial.body_hash,
            fragment_count=partial.fragment_count,
            cutoff_fragment_vector_hash=partial.fragment_vector_hash,
        )
        return self._append_event(
            event_type=FACTORY_ROLE_EVIDENCE_CUTOFF_EVENT_TYPE,
            payload=manifest.to_record(),
            idempotency_key=f"role-evidence-cutoff:{manifest.request_freeze_id}",
            expected_sequence=expected_sequence,
        )

    def _strict_reread_ack(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        expected_body: FactoryRoleEvidenceCutoffBodyV1,
        expected_body_hash: str,
        expected_fragment_count: int,
        expected_fragment_vector_hash: str,
        commit: SegmentedFactEventAppendedV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        reread = self._scan_authority_events()
        persisted = reread.stored.get(request.request_freeze_id)
        if (
            persisted is None
            or persisted.event_id != commit.event_id
            or persisted.sequence != commit.global_seq
            or persisted.event_hash != commit.event_hash
            or persisted.body_hash != expected_body_hash
            or persisted.fragment_count != expected_fragment_count
            or persisted.fragment_vector_hash != expected_fragment_vector_hash
            or persisted.body.to_record() != expected_body.to_record()
        ):
            raise FactoryRoleEvidenceAuthorityError("factory_role_evidence_cutoff_reread_corrupt")
        return self._ack(persisted)

    def _same_request_and_authority(
        self,
        body: FactoryRoleEvidenceCutoffBodyV1,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> bool:
        return (
            body.factory_run_id == self._authority.factory_run_id
            and body.authority == self._authority
            and body.request == request
        )

    def _ack(self, stored: _StoredCutoff) -> FactoryRoleEvidenceCutoffAckV1:
        request = stored.body.request
        return FactoryRoleEvidenceCutoffAckV1(
            schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
            factory_run_id=stored.body.factory_run_id,
            run_id=request.run_id,
            role=request.role,
            turn_id=request.turn_id,
            call_id=request.call_id,
            request_freeze_id=request.request_freeze_id,
            semantic_candidate_hash=request.semantic_candidate_hash,
            attempt_budget=request.attempt_budget,
            execution_authority_hash=request.execution_authority_hash,
            authority_stream=self._logical_stream,
            cutoff_fact_id=stored.event_id,
            cutoff_fact_sequence=stored.sequence,
            cutoff_fact_hash=stored.event_hash,
            cutoff_body_hash=stored.body_hash,
            cutoff_fragment_vector_hash=stored.fragment_vector_hash,
            cutoff_fragment_count=stored.fragment_count,
        )
