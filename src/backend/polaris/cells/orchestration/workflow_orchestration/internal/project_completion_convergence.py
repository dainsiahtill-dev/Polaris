"""Durable, owner-bound project-completion orchestration.

The workflow cursor never owns delivery truth.  It records frozen budgets,
action reservations, dispatch leases, owner receipts, and terminal binding
hashes.  Every completion replay re-queries the sealed ``runtime.projection``
owner binding before repairing the execution projection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import cast

from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectCompletionDiagnosticsV1,
    ProjectCompletionDiagnosticV1,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    _PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN,
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionActionCommandV1,
    ProjectCompletionActionPortV1,
    ProjectCompletionActionReceiptV1,
    ProjectCompletionAdvanceResultV1,
    ProjectCompletionDiagnosticsPortV1,
    ProjectCompletionDispatchClaimV1,
    ProjectCompletionIdentityV1,
    ProjectCompletionModelCeilingPortV1,
    ProjectCompletionOutcomePortV1,
    project_completion_action_receipt_hash,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingOwnerObservationError,
    ModelCeilingQualificationV1,
    ModelCeilingTerminalResultV1,
    revalidate_model_ceiling_result,
)
from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    ProjectCompletionCursorConflictError,
    ProjectCompletionCursorEventV1,
    ProjectCompletionCursorIdentityV1,
    ProjectCompletionCursorLimitsV1,
    ProjectCompletionCursorPortV1,
    ProjectCompletionCursorTransitionV1,
)
from polaris.cells.runtime.projection.public.contracts import ProjectOutcomeAuthorityBindingV1

_EVENT_RESERVED = "project_completion.action_reserved.v1"
_EVENT_DISPATCH_CLAIMED = "project_completion.dispatch_claimed.v1"
_EVENT_COMMITTED = "project_completion.action_committed.v1"
_EVENT_DISPATCH_FAILED = "project_completion.action_dispatch_failed.v1"
_EVENT_ABANDONED = "project_completion.action_abandoned.v1"
_EVENT_WAITING = "project_completion.observation_waiting.v1"
_EVENT_TERMINAL = "project_completion.terminal.v1"
_KNOWN_EVENTS = frozenset(
    {
        "workflow_started",
        _EVENT_RESERVED,
        _EVENT_DISPATCH_CLAIMED,
        _EVENT_COMMITTED,
        _EVENT_DISPATCH_FAILED,
        _EVENT_ABANDONED,
        _EVENT_WAITING,
        _EVENT_TERMINAL,
    }
)
_TERMINAL_STATUSES = frozenset({"completed_verified", "model_ceiling"})


@dataclass(frozen=True, slots=True)
class _FrozenLimits:
    max_actions: int
    max_dispatch_attempts: int
    max_no_progress_observations: int
    dispatch_lease_seconds: int


@dataclass(frozen=True, slots=True)
class _ClaimState:
    claim: ProjectCompletionDispatchClaimV1
    failed: bool


@dataclass(frozen=True, slots=True)
class _CommittedActionState:
    action: ProjectCompletionActionCommandV1
    claim: ProjectCompletionDispatchClaimV1
    receipt_hash: str
    effect_hash: str


@dataclass(frozen=True, slots=True)
class _EventState:
    last_seq: int
    limits: _FrozenLimits
    terminal_event: ProjectCompletionCursorEventV1 | None
    pending_action: ProjectCompletionActionCommandV1 | None
    pending_claims: tuple[_ClaimState, ...]
    pending_claim: _ClaimState | None
    committed_actions: tuple[_CommittedActionState, ...]
    committed_action_count: int
    settled_action_count: int
    committed_snapshot_hashes: frozenset[str]
    abandoned_action_ids: frozenset[str]
    waiting_hashes: tuple[str, ...]


def _workflow_id(identity: ProjectCompletionIdentityV1) -> str:
    payload = json.dumps(identity.as_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"project-completion-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _limits(command: AdvanceProjectCompletionCommandV1) -> _FrozenLimits:
    return _FrozenLimits(
        max_actions=command.max_actions,
        max_dispatch_attempts=command.max_dispatch_attempts,
        max_no_progress_observations=command.max_no_progress_observations,
        dispatch_lease_seconds=command.dispatch_lease_seconds,
    )


def _limits_payload(limits: _FrozenLimits) -> dict[str, int]:
    return {
        "max_actions": limits.max_actions,
        "max_dispatch_attempts": limits.max_dispatch_attempts,
        "max_no_progress_observations": limits.max_no_progress_observations,
        "dispatch_lease_seconds": limits.dispatch_lease_seconds,
    }


def _payload_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if type(value) is not str:
        raise TypeError(f"event payload {key} must be an exact string")
    return value


def _payload_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise TypeError(f"event payload {key} must be an exact integer")
    return value


def _identity_from_payload(payload: object) -> ProjectCompletionIdentityV1:
    if not isinstance(payload, Mapping):
        raise TypeError("identity event payload must be an exact mapping")
    return ProjectCompletionIdentityV1(
        workspace=_payload_str(payload, "workspace"),
        project_id=_payload_str(payload, "project_id"),
        run_id=_payload_str(payload, "run_id"),
        completion_contract_hash=_payload_str(payload, "completion_contract_hash"),
    )


def _require_event_identity(payload: Mapping[str, object], identity: ProjectCompletionIdentityV1) -> None:
    if _identity_from_payload(payload.get("identity")) != identity:
        raise ValueError("workflow event identity drift")


def _optional_payload_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if type(value) is str else None


def _limits_from_payload(payload: object) -> _FrozenLimits:
    if not isinstance(payload, Mapping):
        raise TypeError("workflow limits must be an exact mapping")
    return _FrozenLimits(
        max_actions=_payload_int(payload, "max_actions"),
        max_dispatch_attempts=_payload_int(payload, "max_dispatch_attempts"),
        max_no_progress_observations=_payload_int(payload, "max_no_progress_observations"),
        dispatch_lease_seconds=_payload_int(payload, "dispatch_lease_seconds"),
    )


def _action_payload(command: ProjectCompletionActionCommandV1) -> dict[str, object]:
    return {
        "identity": command.identity.as_payload(),
        "action_id": command.action_id,
        "handoff_id": command.handoff_id,
        "diagnostic_id": command.diagnostic_id,
        "obligation_id": command.obligation_id,
        "owner_task_id": command.owner_task_id,
        "action_kind": command.action_kind,
        "owner_snapshot_hash": command.owner_snapshot_hash,
        "owner_bundle_hash": command.owner_bundle_hash,
    }


def _action_from_payload(payload: object) -> ProjectCompletionActionCommandV1:
    if not isinstance(payload, Mapping):
        raise TypeError("action event payload must be an exact mapping")
    action = ProjectCompletionActionCommandV1(
        identity=_identity_from_payload(payload.get("identity")),
        action_id=_payload_str(payload, "action_id"),
        diagnostic_id=_payload_str(payload, "diagnostic_id"),
        obligation_id=_payload_str(payload, "obligation_id"),
        owner_task_id=_payload_str(payload, "owner_task_id"),
        action_kind=_payload_str(payload, "action_kind"),
        owner_snapshot_hash=_payload_str(payload, "owner_snapshot_hash"),
        owner_bundle_hash=_payload_str(payload, "owner_bundle_hash"),
    )
    if _payload_str(payload, "handoff_id") != action.action_id:
        raise ValueError("reserved handoff_id must equal action_id")
    return action


def _claim_payload(claim: ProjectCompletionDispatchClaimV1) -> dict[str, object]:
    return {
        "identity": claim.identity.as_payload(),
        "action_id": claim.action_id,
        "claim_id": claim.claim_id,
        "attempt_ordinal": claim.attempt_ordinal,
        "lease_expires_at": claim.lease_expires_at,
    }


def _claim_from_payload(payload: object) -> ProjectCompletionDispatchClaimV1:
    if not isinstance(payload, Mapping):
        raise TypeError("dispatch claim event payload must be an exact mapping")
    return ProjectCompletionDispatchClaimV1(
        identity=_identity_from_payload(payload.get("identity")),
        action_id=_payload_str(payload, "action_id"),
        claim_id=_payload_str(payload, "claim_id"),
        attempt_ordinal=_payload_int(payload, "attempt_ordinal"),
        lease_expires_at=_payload_str(payload, "lease_expires_at"),
    )


def _receipt_content_hash(receipt: ProjectCompletionActionReceiptV1) -> str:
    """Canonical owner receipt digest; never trust a caller-supplied digest alone."""

    return project_completion_action_receipt_hash(
        identity=receipt.identity,
        action_id=receipt.action_id,
        handoff_id=receipt.handoff_id,
        diagnostic_id=receipt.diagnostic_id,
        owner_task_id=receipt.owner_task_id,
        status=receipt.status,
        lease_id=receipt.lease_id,
        settlement_id=receipt.settlement_id,
        effect_hash=receipt.effect_hash,
    )


def _event_state(
    events: tuple[ProjectCompletionCursorEventV1, ...],
    identity: ProjectCompletionIdentityV1,
) -> _EventState:
    if not events or events[0].event_type != "workflow_started" or events[0].seq != 1:
        raise ValueError("workflow_started event missing")
    started_payload = events[0].payload
    if not isinstance(started_payload, Mapping):
        raise TypeError("workflow_started payload must be an exact mapping")
    _require_event_identity(started_payload, identity)
    limits = _limits_from_payload(started_payload.get("limits"))

    reservations: dict[str, ProjectCompletionActionCommandV1] = {}
    claims: dict[str, list[_ClaimState]] = {}
    committed_ids: set[str] = set()
    committed_actions: list[_CommittedActionState] = []
    committed_snapshots: set[str] = set()
    abandoned_ids: set[str] = set()
    waiting_hashes: list[str] = []
    terminal: ProjectCompletionCursorEventV1 | None = None
    previous_seq = 0
    for event in events:
        if event.seq != previous_seq + 1:
            raise ValueError("workflow event sequence is not contiguous")
        previous_seq = event.seq
        if event.event_type not in _KNOWN_EVENTS:
            raise ValueError(f"unsupported workflow event: {event.event_type}")
        if terminal is not None:
            raise ValueError("events after terminal are not allowed")
        if event.event_type == "workflow_started":
            if event.seq != 1:
                raise ValueError("duplicate workflow_started event")
            continue
        if not isinstance(event.payload, Mapping):
            raise TypeError("project completion event payload must be an exact mapping")
        _require_event_identity(event.payload, identity)
        if event.event_type == _EVENT_RESERVED:
            action = _action_from_payload(event.payload)
            if action.action_id in reservations:
                raise ValueError("duplicate action reservation")
            if any(action_id not in committed_ids | abandoned_ids for action_id in reservations):
                raise ValueError("multiple pending actions are not allowed")
            reservations[action.action_id] = action
        elif event.event_type == _EVENT_DISPATCH_CLAIMED:
            claim = _claim_from_payload(event.payload)
            if claim.action_id not in reservations or claim.action_id in committed_ids | abandoned_ids:
                raise ValueError("dispatch claim lacks a pending reservation")
            prior = claims.setdefault(claim.action_id, [])
            if claim.attempt_ordinal != len(prior) + 1:
                raise ValueError("dispatch claim ordinal is not contiguous")
            prior.append(_ClaimState(claim=claim, failed=False))
        elif event.event_type == _EVENT_DISPATCH_FAILED:
            action_id = _payload_str(event.payload, "action_id")
            claim_id = _payload_str(event.payload, "claim_id")
            if action_id not in claims or not claims[action_id]:
                raise ValueError("dispatch failure lacks a claim")
            current = claims[action_id][-1]
            if current.claim.claim_id != claim_id or current.failed:
                raise ValueError("dispatch failure claim mismatch")
            claims[action_id][-1] = _ClaimState(claim=current.claim, failed=True)
        elif event.event_type == _EVENT_COMMITTED:
            action_id = _payload_str(event.payload, "action_id")
            if action_id not in reservations or action_id in committed_ids | abandoned_ids:
                raise ValueError("committed action lacks a pending reservation")
            if action_id not in claims or not claims[action_id]:
                raise ValueError("committed action lacks a durable dispatch claim")
            if _payload_str(event.payload, "handoff_id") != action_id:
                raise ValueError("committed handoff_id must equal action_id")
            if _payload_str(event.payload, "owner_snapshot_hash") != reservations[action_id].owner_snapshot_hash:
                raise ValueError("committed owner snapshot mismatch")
            receipt_lease_id = _payload_str(event.payload, "lease_id")
            matching_claims = [
                claim_state.claim for claim_state in claims[action_id] if claim_state.claim.claim_id == receipt_lease_id
            ]
            if len(matching_claims) != 1:
                raise ValueError("committed receipt lease does not match durable claim")
            receipt_claim = matching_claims[0]
            committed_actions.append(
                _CommittedActionState(
                    action=reservations[action_id],
                    claim=receipt_claim,
                    receipt_hash=_payload_str(event.payload, "receipt_hash"),
                    effect_hash=_payload_str(event.payload, "effect_hash"),
                )
            )
            committed_ids.add(action_id)
            committed_snapshots.add(reservations[action_id].owner_snapshot_hash)
        elif event.event_type == _EVENT_ABANDONED:
            action_id = _payload_str(event.payload, "action_id")
            if action_id not in reservations or action_id in committed_ids | abandoned_ids:
                raise ValueError("abandoned action lacks a pending reservation")
            abandoned_ids.add(action_id)
        elif event.event_type == _EVENT_WAITING:
            waiting_hashes.append(_payload_str(event.payload, "owner_snapshot_hash"))
        elif event.event_type == _EVENT_TERMINAL:
            status = _payload_str(event.payload, "status")
            if status not in _TERMINAL_STATUSES:
                raise ValueError("unsupported terminal status")
            if any(action_id not in committed_ids | abandoned_ids for action_id in reservations):
                raise ValueError("terminal event cannot bypass a pending reservation")
            terminal = event

    pending_ids = [action_id for action_id in reservations if action_id not in committed_ids | abandoned_ids]
    if len(pending_ids) > 1:
        raise ValueError("multiple pending actions are not allowed")
    pending_action = reservations[pending_ids[0]] if pending_ids else None
    pending_claims: tuple[_ClaimState, ...] = ()
    pending_claim = None
    if pending_action is not None and claims.get(pending_action.action_id):
        pending_claims = tuple(claims[pending_action.action_id])
        pending_claim = pending_claims[-1]
    return _EventState(
        last_seq=events[-1].seq,
        limits=limits,
        terminal_event=terminal,
        pending_action=pending_action,
        pending_claims=pending_claims,
        pending_claim=pending_claim,
        committed_actions=tuple(committed_actions),
        committed_action_count=len(committed_ids),
        settled_action_count=len(committed_ids | abandoned_ids),
        committed_snapshot_hashes=frozenset(committed_snapshots),
        abandoned_action_ids=frozenset(abandoned_ids),
        waiting_hashes=tuple(waiting_hashes),
    )


def _binding_hash(binding: ProjectOutcomeAuthorityBindingV1) -> str:
    outcome = binding.outcome
    return _hash(
        {
            "identity": {
                "workspace": binding.workspace,
                "project_id": binding.project_id,
                "run_id": binding.run_id,
                "completion_contract_hash": binding.completion_contract_hash,
            },
            "outcome": {
                "delivery": outcome.delivery.value,
                "chain": outcome.chain.value,
                "qa": outcome.qa.value,
                "task_boundary": outcome.task_boundary.value,
                "task_runtime": outcome.task_runtime.value,
                "run_ledger": outcome.run_ledger.value,
                "missing_required_modalities": list(outcome.missing_required_modalities),
                "failed_required_modalities": list(outcome.failed_required_modalities),
                "completion_candidate": outcome.completion_candidate,
                "authority_bound": outcome.authority_bound,
                "completed_verified": outcome.completed_verified,
                "recommended_disposition": outcome.recommended_disposition.value,
                "reasons": list(outcome.reasons),
                "blocking_axes": list(outcome.blocking_axes),
                "task_count": outcome.task_count,
                "completed_task_count": outcome.completed_task_count,
            },
            "factory_chain_projection_hash": binding.factory_chain_projection_hash,
            "factory_chain_evidence_refs": list(binding.factory_chain_evidence_refs),
            "non_factory_projection_hashes": {
                axis: getattr(binding.non_factory_projection_hashes, axis)
                for axis in ("delivery", "qa", "task_boundary", "task_runtime", "run_ledger")
            },
            "non_factory_evidence_refs": {
                axis: list(getattr(binding.non_factory_evidence_refs, axis))
                for axis in ("delivery", "qa", "task_boundary", "task_runtime", "run_ledger")
            },
        }
    )


def _model_ceiling_binding_hash(result: ModelCeilingTerminalResultV1) -> str:
    qualification = result.qualification
    if type(qualification) is not ModelCeilingQualificationV1:
        raise TypeError("qualified model ceiling must contain exact qualification")
    return _hash(
        {
            "workspace": result.workspace,
            "project_id": result.project_id,
            "run_id": result.run_id,
            "factory_run_id": result.factory_run_id,
            "completion_contract_hash": result.completion_contract_hash,
            "diagnostic_id": result.diagnostic_id,
            "status": result.status,
            "routing_disposition": result.routing_disposition,
            "reason_codes": list(result.reason_codes),
            "qualification": {
                "workspace": qualification.workspace,
                "project_id": qualification.project_id,
                "run_id": qualification.run_id,
                "factory_run_id": qualification.factory_run_id,
                "completion_contract_hash": qualification.completion_contract_hash,
                "diagnostic_id": qualification.diagnostic_id,
                "semantic_class": qualification.semantic_class,
                "role_id": qualification.role_id,
                "provider_id": qualification.provider_id,
                "model": qualification.model,
                "provider_call_id": qualification.provider_call_id,
                "request_hash": qualification.request_hash,
                "final_request_snapshot_ref": qualification.final_request_snapshot_ref,
                "round_count": qualification.round_count,
                "max_rounds": qualification.max_rounds,
                "round_request_binding_hashes": list(qualification.round_request_binding_hashes),
                "evidence_refs": list(qualification.evidence_refs),
            },
        }
    )


def _is_exact_qualified_model_ceiling(
    result: object,
    identity: ProjectCompletionIdentityV1,
    diagnostic_id: str,
) -> bool:
    if type(result) is not ModelCeilingTerminalResultV1:
        return False
    sealed_result = cast(ModelCeilingTerminalResultV1, result)
    qualification = sealed_result.qualification
    if type(qualification) is not ModelCeilingQualificationV1:
        return False
    expected_identity = (
        identity.workspace,
        identity.project_id,
        identity.run_id,
        identity.completion_contract_hash,
        diagnostic_id,
    )
    result_identity = (
        sealed_result.workspace,
        sealed_result.project_id,
        sealed_result.run_id,
        sealed_result.completion_contract_hash,
        sealed_result.diagnostic_id,
    )
    qualification_identity = (
        qualification.workspace,
        qualification.project_id,
        qualification.run_id,
        qualification.completion_contract_hash,
        qualification.diagnostic_id,
    )
    return (
        result_identity == expected_identity
        and qualification_identity == expected_identity
        and sealed_result.factory_run_id == qualification.factory_run_id
        and sealed_result.status == "MODEL_CEILING_QUALIFIED"
        and sealed_result.routing_disposition == "stop"
        and sealed_result.terminal is True
        and sealed_result.is_model_ceiling is True
        and sealed_result.parked is False
    )


def _diagnostic_payload(diagnostic: ProjectCompletionDiagnosticV1) -> dict[str, object]:
    return {
        "diagnostic_id": diagnostic.diagnostic_id,
        "archetype": diagnostic.archetype,
        "evidence_state": diagnostic.evidence_state,
        "primary_module_id": diagnostic.primary_module_id,
        "obligation_id": diagnostic.obligation_id,
        "owner_task_id": diagnostic.owner_task_id,
        "affected_target": diagnostic.affected_target,
        "owner_evidence_refs": list(diagnostic.owner_evidence_refs),
        "retry_class": diagnostic.retry_class,
        "allowed_next_action": diagnostic.allowed_next_action,
        "dependency_ids": list(diagnostic.dependency_ids),
        "repair_coverage": diagnostic.repair_coverage,
        "repair_source_tool": diagnostic.repair_source_tool,
        "repair_coverage_evidence_ref": diagnostic.repair_coverage_evidence_ref,
        "repair_coverage_evidence_hash": diagnostic.repair_coverage_evidence_hash,
        "required_verifier_ids": list(diagnostic.required_verifier_ids),
    }


def _owner_snapshot_hash(
    binding: ProjectOutcomeAuthorityBindingV1,
    diagnostics: ProjectCompletionDiagnosticsV1,
) -> str:
    return _hash(
        {
            "owner_binding_hash": _binding_hash(binding),
            "owner_bundle_hash": diagnostics.owner_bundle_hash,
            "diagnostics": [_diagnostic_payload(item) for item in diagnostics.diagnostics],
            "passed_obligation_ids": list(diagnostics.passed_obligation_ids),
            "missing_obligation_ids": list(diagnostics.missing_obligation_ids),
            "failed_obligation_ids": list(diagnostics.failed_obligation_ids),
            "non_blocking_obligation_ids": list(diagnostics.non_blocking_obligation_ids),
        }
    )


def _action_id(
    identity: ProjectCompletionIdentityV1,
    diagnostic: ProjectCompletionDiagnosticV1,
    owner_snapshot_hash: str,
) -> str:
    return _hash(
        {
            "identity": identity.as_payload(),
            "diagnostic": _diagnostic_payload(diagnostic),
            "owner_snapshot_hash": owner_snapshot_hash,
            "action_kind": diagnostic.allowed_next_action,
        }
    )


class ProjectCompletionConvergenceEngineV1:
    """One-tick durable convergence engine with claim-before-effect CAS."""

    def __init__(
        self,
        *,
        cursor: ProjectCompletionCursorPortV1,
        outcome_port: ProjectCompletionOutcomePortV1,
        diagnostics_port: ProjectCompletionDiagnosticsPortV1,
        action_port: ProjectCompletionActionPortV1,
        model_ceiling_port: ProjectCompletionModelCeilingPortV1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(cursor, ProjectCompletionCursorPortV1):
            raise TypeError("cursor must implement ProjectCompletionCursorPortV1")
        if not isinstance(outcome_port, ProjectCompletionOutcomePortV1):
            raise TypeError("outcome_port must implement ProjectCompletionOutcomePortV1")
        if not isinstance(diagnostics_port, ProjectCompletionDiagnosticsPortV1):
            raise TypeError("diagnostics_port must implement ProjectCompletionDiagnosticsPortV1")
        if not isinstance(action_port, ProjectCompletionActionPortV1):
            raise TypeError("action_port must implement ProjectCompletionActionPortV1")
        if not isinstance(model_ceiling_port, ProjectCompletionModelCeilingPortV1):
            raise TypeError("model_ceiling_port must implement ProjectCompletionModelCeilingPortV1")
        self._cursor = cursor
        self._outcome_port = outcome_port
        self._diagnostics_port = diagnostics_port
        self._action_port = action_port
        self._model_ceiling_port = model_ceiling_port
        self._clock = clock or (lambda: datetime.now(UTC))

    async def advance(self, command: AdvanceProjectCompletionCommandV1) -> ProjectCompletionAdvanceResultV1:
        """Advance one durable transition and perform at most one owner effect."""

        if type(command) is not AdvanceProjectCompletionCommandV1:
            raise TypeError("command must be an exact AdvanceProjectCompletionCommandV1")
        identity = command.identity
        workflow_id = _workflow_id(identity)
        await self._ensure_execution(workflow_id, command)
        cursor_identity = self._cursor_identity(identity)
        events = await self._cursor.load_cursor(workflow_id, cursor_identity)
        try:
            state = _event_state(events, identity)
        except (TypeError, ValueError):
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("workflow_identity_or_event_drift",),
                event_seq=events[-1].seq if events else 0,
            )
        if state.limits != _limits(command):
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("frozen_convergence_budget_drift",),
                event_seq=state.last_seq,
            )
        committed_replay = await self._revalidate_committed_receipts(workflow_id, state)
        if committed_replay is not None:
            return committed_replay
        if state.terminal_event is not None:
            return await self._replay_terminal(identity, workflow_id, state)

        # A reservation is a durable obligation.  It MUST be resolved against
        # the owner receipt before querying any terminal outcome.
        if state.pending_action is not None:
            return await self._resume_pending_action(command, workflow_id, state)

        binding_result = await self._query_outcome(identity, workflow_id, state.last_seq)
        if isinstance(binding_result, ProjectCompletionAdvanceResultV1):
            return binding_result
        binding = binding_result
        binding_hash = _binding_hash(binding)
        if binding.outcome.completed_verified:
            return await self._append_terminal(
                identity,
                workflow_id,
                state.last_seq,
                status="completed_verified",
                reason_codes=(),
                owner_binding_hash=binding_hash,
            )

        diagnostics_result = await self._query_diagnostics(identity, workflow_id, state.last_seq)
        if isinstance(diagnostics_result, ProjectCompletionAdvanceResultV1):
            return diagnostics_result
        diagnostics = diagnostics_result
        snapshot_hash = _owner_snapshot_hash(binding, diagnostics)
        ready = self._ready_diagnostic(diagnostics.diagnostics)
        if ready is None:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("incomplete_outcome_without_ready_owner_diagnostic",),
                event_seq=state.last_seq,
                owner_snapshot_hash=snapshot_hash,
            )
        diagnostic = ready
        action_id = _action_id(identity, diagnostic, snapshot_hash)
        if action_id in state.abandoned_action_ids:
            return await self._resolve_model_ceiling_or_park(
                identity,
                workflow_id,
                state.last_seq,
                diagnostic=diagnostic,
                exhaustion_reason="dispatch_attempt_budget_exhausted",
                action_id=action_id,
                owner_snapshot_hash=snapshot_hash,
            )
        if state.settled_action_count >= state.limits.max_actions:
            return await self._resolve_model_ceiling_or_park(
                identity,
                workflow_id,
                state.last_seq,
                diagnostic=diagnostic,
                exhaustion_reason="action_budget_exhausted",
                owner_snapshot_hash=snapshot_hash,
            )
        if snapshot_hash in state.committed_snapshot_hashes:
            return await self._record_no_progress(
                command,
                workflow_id,
                state,
                diagnostic,
                snapshot_hash,
            )
        return await self._reserve_action(
            command,
            workflow_id,
            state,
            diagnostic,
            snapshot_hash,
            diagnostics.owner_bundle_hash,
        )

    async def _ensure_execution(
        self,
        workflow_id: str,
        command: AdvanceProjectCompletionCommandV1,
    ) -> None:
        limits = _limits(command)
        await self._cursor.ensure_cursor(
            workflow_id,
            self._cursor_identity(command.identity),
            ProjectCompletionCursorLimitsV1(**_limits_payload(limits)),
        )

    @staticmethod
    def _cursor_identity(identity: ProjectCompletionIdentityV1) -> ProjectCompletionCursorIdentityV1:
        return ProjectCompletionCursorIdentityV1(**identity.as_payload())

    async def _load_state(
        self,
        workflow_id: str,
        identity: ProjectCompletionIdentityV1,
    ) -> _EventState:
        events = await self._cursor.load_cursor(workflow_id, self._cursor_identity(identity))
        return _event_state(events, identity)

    async def _append_transition(
        self,
        workflow_id: str,
        identity: ProjectCompletionIdentityV1,
        event_type: str,
        payload: dict[str, object],
        *,
        expected_previous_seq: int,
    ) -> ProjectCompletionCursorEventV1:
        payload_identity = payload.pop("identity", None)
        if payload_identity != identity.as_payload():
            raise ValueError("coordinator transition identity mismatch")
        return await self._cursor.append_transition(
            workflow_id,
            self._cursor_identity(identity),
            ProjectCompletionCursorTransitionV1(event_type=event_type, payload=payload),
            expected_previous_seq=expected_previous_seq,
        )

    async def _query_outcome(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        event_seq: int,
    ) -> ProjectOutcomeAuthorityBindingV1 | ProjectCompletionAdvanceResultV1:
        try:
            binding = await self._outcome_port.query_project_completion_outcome(identity)
        except Exception:  # noqa: BLE001 -- owner boundary failures must fail closed
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("project_outcome_owner_query_failed",),
                event_seq=event_seq,
            )
        if type(binding) is not ProjectOutcomeAuthorityBindingV1:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("project_outcome_owner_wrong_type",),
                event_seq=event_seq,
            )
        if (
            binding.workspace,
            binding.project_id,
            binding.run_id,
            binding.completion_contract_hash,
        ) != (
            identity.workspace,
            identity.project_id,
            identity.run_id,
            identity.completion_contract_hash,
        ):
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("project_outcome_owner_identity_mismatch",),
                event_seq=event_seq,
            )
        return binding

    async def _query_diagnostics(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        event_seq: int,
    ) -> ProjectCompletionDiagnosticsV1 | ProjectCompletionAdvanceResultV1:
        try:
            diagnostics = await self._diagnostics_port.query_project_completion_diagnostics(identity)
        except Exception:  # noqa: BLE001 -- owner boundary failures must fail closed
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("project_completion_diagnostics_owner_query_failed",),
                event_seq=event_seq,
            )
        if type(diagnostics) is not ProjectCompletionDiagnosticsV1:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("project_completion_diagnostics_wrong_type",),
                event_seq=event_seq,
            )
        if (
            diagnostics.workspace,
            diagnostics.project_id,
            diagnostics.run_id,
            diagnostics.completion_contract_hash,
        ) != (
            identity.workspace,
            identity.project_id,
            identity.run_id,
            identity.completion_contract_hash,
        ):
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("project_completion_diagnostics_identity_mismatch",),
                event_seq=event_seq,
            )
        return diagnostics

    async def _query_model_ceiling(
        self,
        identity: ProjectCompletionIdentityV1,
        diagnostic_id: str,
        workflow_id: str,
        event_seq: int,
    ) -> ModelCeilingTerminalResultV1 | ProjectCompletionAdvanceResultV1 | None:
        try:
            result = await self._model_ceiling_port.query_project_completion_model_ceiling(
                identity,
                diagnostic_id,
            )
        except Exception:  # noqa: BLE001 -- owner boundary failures must fail closed
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("model_ceiling_owner_query_failed",),
                event_seq=event_seq,
                diagnostic_id=diagnostic_id,
            )
        if result is None:
            return None
        if type(result) is not ModelCeilingTerminalResultV1:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("model_ceiling_owner_wrong_type",),
                event_seq=event_seq,
                diagnostic_id=diagnostic_id,
            )
        if (
            result.workspace,
            result.project_id,
            result.run_id,
            result.completion_contract_hash,
            result.diagnostic_id,
        ) != (
            identity.workspace,
            identity.project_id,
            identity.run_id,
            identity.completion_contract_hash,
            diagnostic_id,
        ):
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("model_ceiling_owner_identity_mismatch",),
                event_seq=event_seq,
                diagnostic_id=diagnostic_id,
            )
        try:
            revalidated = await asyncio.to_thread(revalidate_model_ceiling_result, result)
        except (ModelCeilingOwnerObservationError, TypeError, ValueError):
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("model_ceiling_owner_revalidation_failed",),
                event_seq=event_seq,
                diagnostic_id=diagnostic_id,
            )
        if type(revalidated) is not ModelCeilingTerminalResultV1:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("model_ceiling_owner_revalidation_wrong_type",),
                event_seq=event_seq,
                diagnostic_id=diagnostic_id,
            )
        if revalidated.status != "MODEL_CEILING_QUALIFIED":
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=revalidated.reason_codes or ("model_ceiling_owner_revalidation_non_terminal",),
                event_seq=event_seq,
                diagnostic_id=diagnostic_id,
            )
        return revalidated

    async def _resolve_model_ceiling_or_park(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        event_seq: int,
        *,
        diagnostic: ProjectCompletionDiagnosticV1,
        exhaustion_reason: str,
        action_id: str | None = None,
        owner_snapshot_hash: str | None = None,
    ) -> ProjectCompletionAdvanceResultV1:
        decision = await self._query_model_ceiling(
            identity,
            diagnostic.diagnostic_id,
            workflow_id,
            event_seq,
        )
        if isinstance(decision, ProjectCompletionAdvanceResultV1):
            return self._result(
                identity,
                workflow_id,
                status=decision.status,
                reason_codes=(exhaustion_reason, *decision.reason_codes),
                event_seq=decision.event_seq,
                diagnostic_id=diagnostic.diagnostic_id,
                action_id=action_id,
                owner_snapshot_hash=owner_snapshot_hash,
                next_action=diagnostic.allowed_next_action,
            )
        if decision is not None and _is_exact_qualified_model_ceiling(
            decision,
            identity,
            diagnostic.diagnostic_id,
        ):
            return await self._append_terminal(
                identity,
                workflow_id,
                event_seq,
                status="model_ceiling",
                reason_codes=decision.reason_codes,
                diagnostic_id=diagnostic.diagnostic_id,
                action_id=action_id,
                owner_snapshot_hash=owner_snapshot_hash,
                model_ceiling_result=decision,
            )
        reason_codes = (
            (exhaustion_reason, "model_ceiling_decision_unavailable")
            if decision is None
            else (exhaustion_reason, *decision.reason_codes)
        )
        return self._result(
            identity,
            workflow_id,
            status="control_plane_blocked",
            reason_codes=reason_codes,
            event_seq=event_seq,
            diagnostic_id=diagnostic.diagnostic_id,
            action_id=action_id,
            owner_snapshot_hash=owner_snapshot_hash,
            next_action=diagnostic.allowed_next_action,
        )

    @staticmethod
    def _ready_diagnostic(
        diagnostics: tuple[ProjectCompletionDiagnosticV1, ...],
    ) -> ProjectCompletionDiagnosticV1 | None:
        ready = [item for item in diagnostics if not item.dependency_ids]
        return (
            min(ready, key=lambda item: (item.owner_task_id, item.obligation_id, item.diagnostic_id)) if ready else None
        )

    async def _reserve_action(
        self,
        command: AdvanceProjectCompletionCommandV1,
        workflow_id: str,
        state: _EventState,
        diagnostic: ProjectCompletionDiagnosticV1,
        owner_snapshot_hash: str,
        owner_bundle_hash: str,
    ) -> ProjectCompletionAdvanceResultV1:
        action = ProjectCompletionActionCommandV1(
            identity=command.identity,
            action_id=_action_id(command.identity, diagnostic, owner_snapshot_hash),
            diagnostic_id=diagnostic.diagnostic_id,
            obligation_id=diagnostic.obligation_id,
            owner_task_id=diagnostic.owner_task_id,
            action_kind=diagnostic.allowed_next_action,
            owner_snapshot_hash=owner_snapshot_hash,
            owner_bundle_hash=owner_bundle_hash,
        )
        try:
            await self._append_transition(
                workflow_id,
                command.identity,
                _EVENT_RESERVED,
                _action_payload(action),
                expected_previous_seq=state.last_seq,
            )
        except ProjectCompletionCursorConflictError:
            return self._result(
                command.identity,
                workflow_id,
                status="waiting",
                reason_codes=("concurrent_cursor_advanced",),
                event_seq=state.last_seq,
                diagnostic_id=diagnostic.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=owner_snapshot_hash,
            )
        refreshed = await self._load_state(workflow_id, command.identity)
        return await self._resume_pending_action(command, workflow_id, refreshed)

    async def _resume_pending_action(
        self,
        command: AdvanceProjectCompletionCommandV1,
        workflow_id: str,
        state: _EventState,
    ) -> ProjectCompletionAdvanceResultV1:
        action = state.pending_action
        if action is None:
            raise RuntimeError("pending action expected")
        receipt_result = await self._query_action_receipt(
            action,
            tuple(item.claim for item in state.pending_claims),
            workflow_id,
            state.last_seq,
        )
        if isinstance(receipt_result, ProjectCompletionAdvanceResultV1):
            return receipt_result
        if receipt_result is not None:
            if not state.pending_claims:
                return self._result(
                    command.identity,
                    workflow_id,
                    status="control_plane_blocked",
                    reason_codes=("owner_receipt_without_durable_claim",),
                    event_seq=state.last_seq,
                    diagnostic_id=action.diagnostic_id,
                    action_id=action.action_id,
                    owner_snapshot_hash=action.owner_snapshot_hash,
                )
            return await self._commit_receipt(workflow_id, state, action, receipt_result)

        current_claim = state.pending_claim
        now = self._aware_now()
        if current_claim is not None and not current_claim.failed:
            lease_end = datetime.fromisoformat(current_claim.claim.lease_expires_at.replace("Z", "+00:00"))
            if lease_end > now:
                return self._result(
                    command.identity,
                    workflow_id,
                    status="waiting",
                    reason_codes=("dispatch_claim_active",),
                    event_seq=state.last_seq,
                    diagnostic_id=action.diagnostic_id,
                    action_id=action.action_id,
                    owner_snapshot_hash=action.owner_snapshot_hash,
                )
        attempt_ordinal = current_claim.claim.attempt_ordinal + 1 if current_claim is not None else 1
        if attempt_ordinal > state.limits.max_dispatch_attempts:
            try:
                event = await self._append_transition(
                    workflow_id,
                    command.identity,
                    _EVENT_ABANDONED,
                    {
                        "identity": command.identity.as_payload(),
                        "action_id": action.action_id,
                        "reason_code": "dispatch_attempt_budget_exhausted",
                    },
                    expected_previous_seq=state.last_seq,
                )
            except ProjectCompletionCursorConflictError:
                return self._result(
                    command.identity,
                    workflow_id,
                    status="waiting",
                    reason_codes=("concurrent_cursor_advanced",),
                    event_seq=state.last_seq,
                    diagnostic_id=action.diagnostic_id,
                    action_id=action.action_id,
                    owner_snapshot_hash=action.owner_snapshot_hash,
                )
            return self._result(
                command.identity,
                workflow_id,
                status="waiting",
                reason_codes=("pending_action_abandoned_before_terminal",),
                event_seq=event.seq,
                diagnostic_id=action.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=action.owner_snapshot_hash,
            )

        claim = self._new_claim(action, attempt_ordinal, state.limits.dispatch_lease_seconds)
        try:
            claim_event = await self._append_transition(
                workflow_id,
                command.identity,
                _EVENT_DISPATCH_CLAIMED,
                _claim_payload(claim),
                expected_previous_seq=state.last_seq,
            )
        except ProjectCompletionCursorConflictError:
            return self._result(
                command.identity,
                workflow_id,
                status="waiting",
                reason_codes=("concurrent_dispatch_claim_lost",),
                event_seq=state.last_seq,
                diagnostic_id=action.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=action.owner_snapshot_hash,
            )
        try:
            receipt = await self._action_port.dispatch_project_completion_action(action, claim)
            self._validate_receipt(action, (claim,), receipt)
        except Exception as exc:  # noqa: BLE001 -- persist failed claim; BaseException models process death
            try:
                failed = await self._append_transition(
                    workflow_id,
                    command.identity,
                    _EVENT_DISPATCH_FAILED,
                    {
                        "identity": command.identity.as_payload(),
                        "action_id": action.action_id,
                        "claim_id": claim.claim_id,
                        "error_type": type(exc).__name__,
                    },
                    expected_previous_seq=claim_event.seq,
                )
                event_seq = failed.seq
            except ProjectCompletionCursorConflictError:
                event_seq = claim_event.seq
            return self._result(
                command.identity,
                workflow_id,
                status="waiting",
                reason_codes=("owner_action_dispatch_failed",),
                event_seq=event_seq,
                diagnostic_id=action.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=action.owner_snapshot_hash,
            )
        refreshed = await self._load_state(workflow_id, command.identity)
        return await self._commit_receipt(workflow_id, refreshed, action, receipt)

    async def _query_action_receipt(
        self,
        action: ProjectCompletionActionCommandV1,
        claims: tuple[ProjectCompletionDispatchClaimV1, ...],
        workflow_id: str,
        event_seq: int,
    ) -> ProjectCompletionActionReceiptV1 | ProjectCompletionAdvanceResultV1 | None:
        try:
            receipt = await self._action_port.query_project_completion_action_receipt(action)
        except Exception:  # noqa: BLE001 -- receipt authority outage must fail closed
            return self._result(
                action.identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("owner_action_receipt_query_failed",),
                event_seq=event_seq,
                diagnostic_id=action.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=action.owner_snapshot_hash,
            )
        if receipt is None:
            return None
        if not claims:
            return receipt
        try:
            self._validate_receipt(action, claims, receipt)
        except (TypeError, ValueError):
            return self._result(
                action.identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("owner_action_receipt_invalid",),
                event_seq=event_seq,
                diagnostic_id=action.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=action.owner_snapshot_hash,
            )
        return receipt

    async def _revalidate_committed_receipts(
        self,
        workflow_id: str,
        state: _EventState,
    ) -> ProjectCompletionAdvanceResultV1 | None:
        """Re-query every owner receipt before trusting committed cursor history."""

        for committed in state.committed_actions:
            receipt_result = await self._query_action_receipt(
                committed.action,
                (committed.claim,),
                workflow_id,
                state.last_seq,
            )
            if isinstance(receipt_result, ProjectCompletionAdvanceResultV1):
                return receipt_result
            if receipt_result is None:
                return self._result(
                    committed.action.identity,
                    workflow_id,
                    status="control_plane_blocked",
                    reason_codes=("committed_owner_receipt_missing",),
                    event_seq=state.last_seq,
                    diagnostic_id=committed.action.diagnostic_id,
                    action_id=committed.action.action_id,
                    owner_snapshot_hash=committed.action.owner_snapshot_hash,
                )
            if (
                receipt_result.receipt_hash != committed.receipt_hash
                or receipt_result.effect_hash != committed.effect_hash
            ):
                return self._result(
                    committed.action.identity,
                    workflow_id,
                    status="control_plane_blocked",
                    reason_codes=("committed_owner_receipt_hash_drift",),
                    event_seq=state.last_seq,
                    diagnostic_id=committed.action.diagnostic_id,
                    action_id=committed.action.action_id,
                    owner_snapshot_hash=committed.action.owner_snapshot_hash,
                )
        return None

    @staticmethod
    def _validate_receipt(
        action: ProjectCompletionActionCommandV1,
        claims: tuple[ProjectCompletionDispatchClaimV1, ...],
        receipt: object,
    ) -> None:
        if type(receipt) is not ProjectCompletionActionReceiptV1:
            raise TypeError("owner action receipt must use the exact contract")
        typed_receipt = cast(ProjectCompletionActionReceiptV1, receipt)
        if (
            typed_receipt.identity != action.identity
            or typed_receipt.action_id != action.action_id
            or typed_receipt.handoff_id != action.action_id
            or typed_receipt.diagnostic_id != action.diagnostic_id
            or typed_receipt.owner_task_id != action.owner_task_id
            or not any(typed_receipt.lease_id == claim.claim_id for claim in claims)
            or typed_receipt.receipt_hash != _receipt_content_hash(typed_receipt)
        ):
            raise ValueError("owner action receipt identity mismatch")

    async def _commit_receipt(
        self,
        workflow_id: str,
        state: _EventState,
        action: ProjectCompletionActionCommandV1,
        receipt: ProjectCompletionActionReceiptV1,
    ) -> ProjectCompletionAdvanceResultV1:
        try:
            event = await self._append_transition(
                workflow_id,
                action.identity,
                _EVENT_COMMITTED,
                {
                    "identity": action.identity.as_payload(),
                    "action_id": action.action_id,
                    "handoff_id": receipt.handoff_id,
                    "diagnostic_id": action.diagnostic_id,
                    "owner_task_id": action.owner_task_id,
                    "owner_snapshot_hash": action.owner_snapshot_hash,
                    "owner_bundle_hash": action.owner_bundle_hash,
                    "receipt_hash": receipt.receipt_hash,
                    "lease_id": receipt.lease_id,
                    "settlement_id": receipt.settlement_id,
                    "effect_hash": receipt.effect_hash,
                },
                expected_previous_seq=state.last_seq,
            )
        except ProjectCompletionCursorConflictError:
            return self._result(
                action.identity,
                workflow_id,
                status="waiting",
                reason_codes=("concurrent_cursor_advanced",),
                event_seq=state.last_seq,
                diagnostic_id=action.diagnostic_id,
                action_id=action.action_id,
                owner_snapshot_hash=action.owner_snapshot_hash,
            )
        return self._result(
            action.identity,
            workflow_id,
            status="waiting",
            reason_codes=("owner_action_receipt_committed",),
            event_seq=event.seq,
            diagnostic_id=action.diagnostic_id,
            action_id=action.action_id,
            owner_snapshot_hash=action.owner_snapshot_hash,
        )

    async def _record_no_progress(
        self,
        command: AdvanceProjectCompletionCommandV1,
        workflow_id: str,
        state: _EventState,
        diagnostic: ProjectCompletionDiagnosticV1,
        owner_snapshot_hash: str,
    ) -> ProjectCompletionAdvanceResultV1:
        consecutive = 1
        for observed_hash in reversed(state.waiting_hashes):
            if observed_hash != owner_snapshot_hash:
                break
            consecutive += 1
        if consecutive >= state.limits.max_no_progress_observations:
            return await self._resolve_model_ceiling_or_park(
                command.identity,
                workflow_id,
                state.last_seq,
                diagnostic=diagnostic,
                exhaustion_reason="no_progress_budget_exhausted",
                owner_snapshot_hash=owner_snapshot_hash,
            )
        try:
            event = await self._append_transition(
                workflow_id,
                command.identity,
                _EVENT_WAITING,
                {
                    "identity": command.identity.as_payload(),
                    "diagnostic_id": diagnostic.diagnostic_id,
                    "owner_snapshot_hash": owner_snapshot_hash,
                },
                expected_previous_seq=state.last_seq,
            )
        except ProjectCompletionCursorConflictError:
            return self._result(
                command.identity,
                workflow_id,
                status="waiting",
                reason_codes=("concurrent_cursor_advanced",),
                event_seq=state.last_seq,
                diagnostic_id=diagnostic.diagnostic_id,
                owner_snapshot_hash=owner_snapshot_hash,
            )
        return self._result(
            command.identity,
            workflow_id,
            status="waiting",
            reason_codes=("owner_observation_unchanged",),
            event_seq=event.seq,
            diagnostic_id=diagnostic.diagnostic_id,
            owner_snapshot_hash=owner_snapshot_hash,
        )

    async def _append_terminal(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        expected_seq: int,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        diagnostic_id: str | None = None,
        action_id: str | None = None,
        owner_binding_hash: str | None = None,
        owner_snapshot_hash: str | None = None,
        model_ceiling_result: ModelCeilingTerminalResultV1 | None = None,
    ) -> ProjectCompletionAdvanceResultV1:
        if status not in _TERMINAL_STATUSES:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("unsupported_project_completion_terminal_status",),
                event_seq=expected_seq,
                diagnostic_id=diagnostic_id,
                action_id=action_id,
                owner_snapshot_hash=owner_snapshot_hash,
            )
        terminal_owner_binding_hash = owner_binding_hash
        if status == "model_ceiling":
            if (
                diagnostic_id is None
                or model_ceiling_result is None
                or not _is_exact_qualified_model_ceiling(model_ceiling_result, identity, diagnostic_id)
            ):
                return self._result(
                    identity,
                    workflow_id,
                    status="control_plane_blocked",
                    reason_codes=("model_ceiling_sealed_result_required",),
                    event_seq=expected_seq,
                    diagnostic_id=diagnostic_id,
                    action_id=action_id,
                    owner_snapshot_hash=owner_snapshot_hash,
                )
            terminal_owner_binding_hash = _model_ceiling_binding_hash(model_ceiling_result)
        elif owner_binding_hash is None:
            return self._result(
                identity,
                workflow_id,
                status="control_plane_blocked",
                reason_codes=("completed_verified_owner_binding_required",),
                event_seq=expected_seq,
            )
        payload: dict[str, object] = {
            "identity": identity.as_payload(),
            "status": status,
            "reason_codes": list(reason_codes),
        }
        if diagnostic_id is not None:
            payload["diagnostic_id"] = diagnostic_id
        if action_id is not None:
            payload["action_id"] = action_id
        if terminal_owner_binding_hash is not None:
            payload["owner_binding_hash"] = terminal_owner_binding_hash
        if owner_snapshot_hash is not None:
            payload["owner_snapshot_hash"] = owner_snapshot_hash
        try:
            event = await self._append_transition(
                workflow_id,
                identity,
                _EVENT_TERMINAL,
                payload,
                expected_previous_seq=expected_seq,
            )
        except ProjectCompletionCursorConflictError:
            state = await self._load_state(workflow_id, identity)
            if state.terminal_event is not None:
                return await self._replay_terminal(identity, workflow_id, state)
            return self._result(
                identity,
                workflow_id,
                status="waiting",
                reason_codes=("concurrent_cursor_advanced",),
                event_seq=state.last_seq,
                diagnostic_id=diagnostic_id,
                action_id=action_id,
                owner_snapshot_hash=owner_snapshot_hash,
            )
        result = self._terminal_result_from_payload(identity, workflow_id, event)
        await self._repair_execution_projection(workflow_id, result)
        return result

    async def _replay_terminal(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        state: _EventState,
    ) -> ProjectCompletionAdvanceResultV1:
        event = state.terminal_event
        if event is None:
            raise RuntimeError("terminal event expected")
        if not isinstance(event.payload, Mapping):
            return self._invalid_terminal(identity, workflow_id, event.seq)
        try:
            _require_event_identity(event.payload, identity)
            status = _payload_str(event.payload, "status")
        except (TypeError, ValueError):
            return self._invalid_terminal(identity, workflow_id, event.seq)
        if status == "completed_verified":
            owner_binding_hash = event.payload.get("owner_binding_hash")
            if type(owner_binding_hash) is not str:
                return await self._invalidate_terminal_projection(identity, workflow_id, event.seq)
            binding_result = await self._query_outcome(identity, workflow_id, event.seq)
            if (
                isinstance(binding_result, ProjectCompletionAdvanceResultV1)
                or not binding_result.outcome.completed_verified
                or _binding_hash(binding_result) != owner_binding_hash
            ):
                return await self._invalidate_terminal_projection(identity, workflow_id, event.seq)
        elif status == "model_ceiling":
            diagnostic_id = event.payload.get("diagnostic_id")
            model_ceiling_binding_hash = event.payload.get("owner_binding_hash")
            if type(diagnostic_id) is not str or type(model_ceiling_binding_hash) is not str:
                return await self._invalidate_terminal_projection(identity, workflow_id, event.seq)
            decision = await self._query_model_ceiling(identity, diagnostic_id, workflow_id, event.seq)
            if (
                not _is_exact_qualified_model_ceiling(decision, identity, diagnostic_id)
                or _model_ceiling_binding_hash(cast(ModelCeilingTerminalResultV1, decision))
                != model_ceiling_binding_hash
            ):
                return await self._invalidate_terminal_projection(identity, workflow_id, event.seq)
        else:
            return await self._invalidate_terminal_projection(identity, workflow_id, event.seq)
        result = self._terminal_result_from_payload(identity, workflow_id, event)
        await self._repair_execution_projection(workflow_id, result)
        return result

    async def _invalidate_terminal_projection(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        event_seq: int,
    ) -> ProjectCompletionAdvanceResultV1:
        result = self._invalid_terminal(identity, workflow_id, event_seq)
        await self._repair_execution_projection(workflow_id, result)
        return result

    def _invalid_terminal(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        event_seq: int,
    ) -> ProjectCompletionAdvanceResultV1:
        return self._result(
            identity,
            workflow_id,
            status="control_plane_blocked",
            reason_codes=("terminal_owner_binding_revalidation_failed",),
            event_seq=event_seq,
        )

    def _terminal_result_from_payload(
        self,
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        event: ProjectCompletionCursorEventV1,
    ) -> ProjectCompletionAdvanceResultV1:
        payload = event.payload
        if not isinstance(payload, Mapping):
            return self._invalid_terminal(identity, workflow_id, event.seq)
        reason_codes_value = payload.get("reason_codes", [])
        if type(reason_codes_value) is not list or any(type(item) is not str for item in reason_codes_value):
            return self._invalid_terminal(identity, workflow_id, event.seq)
        try:
            return self._result(
                identity,
                workflow_id,
                status=_payload_str(payload, "status"),
                reason_codes=tuple(reason_codes_value),
                event_seq=event.seq,
                diagnostic_id=_optional_payload_str(payload, "diagnostic_id"),
                action_id=_optional_payload_str(payload, "action_id"),
                owner_snapshot_hash=(
                    _optional_payload_str(payload, "owner_snapshot_hash")
                    or _optional_payload_str(payload, "owner_binding_hash")
                ),
            )
        except (TypeError, ValueError):
            return self._invalid_terminal(identity, workflow_id, event.seq)

    async def _repair_execution_projection(
        self,
        workflow_id: str,
        result: ProjectCompletionAdvanceResultV1,
    ) -> None:
        execution_status = (
            "completed" if result.status == "completed_verified" else "failed" if result.terminal else "running"
        )
        await self._cursor.repair_execution_projection(
            workflow_id,
            status=execution_status,
            result={
                "status": result.status,
                "reason_codes": list(result.reason_codes),
                "event_seq": result.event_seq,
                "diagnostic_id": result.diagnostic_id,
                "action_id": result.action_id,
                "owner_snapshot_hash": result.owner_snapshot_hash,
                "next_action": result.next_action,
            },
            close_time=self._aware_now().isoformat() if result.terminal else None,
        )

    def _new_claim(
        self,
        action: ProjectCompletionActionCommandV1,
        attempt_ordinal: int,
        lease_seconds: int,
    ) -> ProjectCompletionDispatchClaimV1:
        now = self._aware_now()
        claim_id = _hash({"action_id": action.action_id, "attempt_ordinal": attempt_ordinal})
        return ProjectCompletionDispatchClaimV1(
            identity=action.identity,
            action_id=action.action_id,
            claim_id=claim_id,
            attempt_ordinal=attempt_ordinal,
            lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _result(
        identity: ProjectCompletionIdentityV1,
        workflow_id: str,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        event_seq: int,
        diagnostic_id: str | None = None,
        action_id: str | None = None,
        owner_snapshot_hash: str | None = None,
        next_action: str | None = None,
    ) -> ProjectCompletionAdvanceResultV1:
        return ProjectCompletionAdvanceResultV1(
            identity=identity,
            workflow_id=workflow_id,
            status=status,
            reason_codes=reason_codes,
            event_seq=event_seq,
            diagnostic_id=diagnostic_id,
            action_id=action_id,
            owner_snapshot_hash=owner_snapshot_hash,
            next_action=next_action,
            _authority_token=_PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN,
        )


_bound_engine: ProjectCompletionConvergenceEngineV1 | None = None
_bound_engine_lock = Lock()


def bind_project_completion_convergence_runtime(
    *,
    cursor: ProjectCompletionCursorPortV1,
    outcome_port: ProjectCompletionOutcomePortV1,
    diagnostics_port: ProjectCompletionDiagnosticsPortV1,
    action_port: ProjectCompletionActionPortV1,
    model_ceiling_port: ProjectCompletionModelCeilingPortV1,
) -> None:
    """Bind one composition-root runtime without owning any upstream facts."""

    engine = ProjectCompletionConvergenceEngineV1(
        cursor=cursor,
        outcome_port=outcome_port,
        diagnostics_port=diagnostics_port,
        action_port=action_port,
        model_ceiling_port=model_ceiling_port,
    )
    global _bound_engine
    with _bound_engine_lock:
        if _bound_engine is None:
            _bound_engine = engine
            return
        if _bound_engine is engine:
            return
        raise RuntimeError("project_completion_convergence_runtime_conflicting_rebind")


def clear_project_completion_convergence_runtime() -> None:
    """Clear process-local bootstrap state; never mutates durable cursor data."""

    global _bound_engine
    with _bound_engine_lock:
        _bound_engine = None


async def advance_project_completion_authoritatively(
    command: AdvanceProjectCompletionCommandV1,
) -> ProjectCompletionAdvanceResultV1:
    """Advance through the bootstrap-bound convergence engine."""

    with _bound_engine_lock:
        engine = _bound_engine
    if engine is None:
        raise RuntimeError("project_completion_convergence_runtime_unbound")
    return await engine.advance(command)


__all__ = [
    "ProjectCompletionConvergenceEngineV1",
    "advance_project_completion_authoritatively",
    "bind_project_completion_convergence_runtime",
    "clear_project_completion_convergence_runtime",
]
