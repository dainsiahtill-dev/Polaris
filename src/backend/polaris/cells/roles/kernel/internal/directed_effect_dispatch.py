"""Process-local, PID-bound, one-shot directed-effect dispatch fence."""

from __future__ import annotations

import os
import threading
import weakref
from dataclasses import dataclass

from polaris.cells.director.runtime.public.directed_effect_contracts import DirectedEffectErrorCodeV1
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DirectedEffectExecutionContextV1,
    DirectedEffectFenceConsumeResultV1,
    DirectedEffectFencePortsV1,
    DirectedEffectFenceRegistrationResultV1,
    DirectedEffectFenceReleaseResultV1,
    validate_directed_effect_execution_context,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

_FENCE_CAPACITY = 64


@dataclass(slots=True)
class _FenceEntry:
    context: DirectedEffectExecutionContextV1
    identity: tuple[object, ...]
    batch_id: str
    attempt_identity: tuple[object, ...]
    consumed: bool = False


def _attempt_stable_identity(
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
) -> tuple[object, ...]:
    return (
        attempt.schema_version,
        attempt.workspace,
        attempt.task_id,
        attempt.external_task_id,
        attempt.session_id,
        attempt.attempt,
        attempt.role_id,
        attempt.worker_id,
        attempt.run_id,
    )


def _context_identity(context: DirectedEffectExecutionContextV1) -> tuple[object, ...]:
    grant = context.claim_grant
    return (
        context.context_id,
        context.batch_id,
        context.creator_pid,
        context.tool_call_id,
        context.normalized_tool_name,
        context.arguments_hash,
        context.authorization_evidence.authorization_hash,
        context.bound_snapshot.member_binding_hash,
        context.bound_snapshot.authorization_binding_hash,
        context.current_policy_evidence.evidence_hash,
        context.current_job_token_restriction_evidence,
        _attempt_stable_identity(grant.execution_attempt),
        grant.parent_binding.to_record(),
        grant.operation.to_record(),
        grant.member.to_record(),
        grant.inventory_hash,
        grant.operation_version,
        grant.claim_event_id,
        grant.claim_event_seq,
        grant.operation_source_head_seq,
        grant.parent_registry_source_head_seq,
        grant.grant_hash,
    )


class _PidBoundDirectedEffectFence:
    """Private shared state; capability views expose disjoint method sets."""

    def __init__(self) -> None:
        self._creator_pid = os.getpid()
        self._entries: dict[str, _FenceEntry] = {}
        self._lock = threading.RLock()
        self_ref = weakref.ref(self)

        def _reset_in_child() -> None:
            fence = self_ref()
            if fence is not None:
                fence._lock = threading.RLock()

        os.register_at_fork(after_in_child=_reset_in_child)

    def _pid_ok(self) -> bool:
        return os.getpid() == self._creator_pid

    @staticmethod
    def _denial_context_id(context: DirectedEffectExecutionContextV1) -> str:
        value = getattr(context, "context_id", "")
        return value if isinstance(value, str) and value.strip() else "invalid-context"

    @staticmethod
    def _canonical_context(
        context: DirectedEffectExecutionContextV1,
    ) -> DirectedEffectExecutionContextV1 | None:
        try:
            return validate_directed_effect_execution_context(context)
        except (AttributeError, TypeError, ValueError):
            return None

    def register(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceRegistrationResultV1:
        if not self._pid_ok():
            return DirectedEffectFenceRegistrationResultV1(
                ok=False,
                status="denied",
                context_id=self._denial_context_id(context),
                error_code="deo_fence_pid_mismatch",
            )
        if not isinstance(context, DirectedEffectExecutionContextV1):
            raise TypeError("context must be DirectedEffectExecutionContextV1")
        if context.creator_pid != self._creator_pid:
            return DirectedEffectFenceRegistrationResultV1(
                ok=False,
                status="denied",
                context_id=context.context_id,
                error_code="deo_fence_pid_mismatch",
            )
        with self._lock:
            canonical = self._canonical_context(context)
            if canonical is None:
                return DirectedEffectFenceRegistrationResultV1(
                    ok=False,
                    status="denied",
                    context_id=self._denial_context_id(context),
                    error_code="deo_context_identity_mismatch",
                )
            existing = self._entries.get(context.context_id)
            if existing is not None:
                if existing.context is context:
                    code: DirectedEffectErrorCodeV1 = "deo_context_replayed"
                elif existing.identity == _context_identity(context):
                    code = "deo_context_reconstructed"
                else:
                    code = "deo_context_identity_mismatch"
                return DirectedEffectFenceRegistrationResultV1(
                    ok=False,
                    status="denied",
                    context_id=context.context_id,
                    error_code=code,
                )
            if len(self._entries) >= _FENCE_CAPACITY:
                return DirectedEffectFenceRegistrationResultV1(
                    ok=False,
                    status="denied",
                    context_id=context.context_id,
                    error_code="deo_fence_capacity_exceeded",
                )
            self._entries[context.context_id] = _FenceEntry(
                context=context,
                identity=_context_identity(context),
                batch_id=context.batch_id,
                attempt_identity=_attempt_stable_identity(context.claim_grant.execution_attempt),
            )
            return DirectedEffectFenceRegistrationResultV1(
                ok=True,
                status="registered",
                context_id=context.context_id,
                error_code=None,
            )

    def consume(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceConsumeResultV1:
        if not self._pid_ok():
            return DirectedEffectFenceConsumeResultV1(
                ok=False,
                status="denied",
                context_id=self._denial_context_id(context),
                error_code="deo_fence_pid_mismatch",
            )
        if not isinstance(context, DirectedEffectExecutionContextV1):
            raise TypeError("context must be DirectedEffectExecutionContextV1")
        if context.creator_pid != self._creator_pid:
            return DirectedEffectFenceConsumeResultV1(
                ok=False,
                status="denied",
                context_id=context.context_id,
                error_code="deo_fence_pid_mismatch",
            )
        with self._lock:
            canonical = self._canonical_context(context)
            if canonical is None:
                return DirectedEffectFenceConsumeResultV1(
                    ok=False,
                    status="denied",
                    context_id=self._denial_context_id(context),
                    error_code="deo_context_identity_mismatch",
                )
            entry = self._entries.get(context.context_id)
            if entry is None:
                return DirectedEffectFenceConsumeResultV1(
                    ok=False,
                    status="denied",
                    context_id=context.context_id,
                    error_code="deo_context_not_registered",
                )
            if entry.context is not context:
                code: DirectedEffectErrorCodeV1 = (
                    "deo_context_reconstructed"
                    if entry.identity == _context_identity(context)
                    else "deo_context_identity_mismatch"
                )
                return DirectedEffectFenceConsumeResultV1(
                    ok=False,
                    status="denied",
                    context_id=context.context_id,
                    error_code=code,
                )
            if entry.identity != _context_identity(context):
                return DirectedEffectFenceConsumeResultV1(
                    ok=False,
                    status="denied",
                    context_id=context.context_id,
                    error_code="deo_context_identity_mismatch",
                )
            if entry.consumed:
                return DirectedEffectFenceConsumeResultV1(
                    ok=False,
                    status="denied",
                    context_id=context.context_id,
                    error_code="deo_context_replayed",
                )
            entry.consumed = True
            return DirectedEffectFenceConsumeResultV1(
                ok=True,
                status="consumed",
                context_id=context.context_id,
                error_code=None,
            )

    def release_batch(
        self,
        batch_id: str,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectFenceReleaseResultV1:
        batch_token = str(batch_id or "").strip()
        if not batch_token:
            raise ValueError("batch_id must be a non-empty string")
        if not self._pid_ok():
            return DirectedEffectFenceReleaseResultV1(
                ok=False,
                status="denied",
                batch_id=batch_token,
                released_count=0,
                error_code="deo_fence_pid_mismatch",
            )
        if not isinstance(execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        with self._lock:
            try:
                canonical_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(execution_attempt.to_record())
            except (AttributeError, TypeError, ValueError):
                canonical_attempt = None
            if canonical_attempt != execution_attempt:
                return DirectedEffectFenceReleaseResultV1(
                    ok=False,
                    status="denied",
                    batch_id=batch_token,
                    released_count=0,
                    error_code="deo_context_release_failed",
                )
            requested_attempt = _attempt_stable_identity(canonical_attempt)
            same_batch = [
                (context_id, entry) for context_id, entry in self._entries.items() if entry.batch_id == batch_token
            ]
            matching = [context_id for context_id, entry in same_batch if entry.attempt_identity == requested_attempt]
            if not same_batch:
                return DirectedEffectFenceReleaseResultV1(
                    ok=True,
                    status="absent",
                    batch_id=batch_token,
                    released_count=0,
                    error_code=None,
                )
            if not matching:
                return DirectedEffectFenceReleaseResultV1(
                    ok=False,
                    status="denied",
                    batch_id=batch_token,
                    released_count=0,
                    error_code="deo_context_release_failed",
                )
            for context_id in matching:
                del self._entries[context_id]
            return DirectedEffectFenceReleaseResultV1(
                ok=True,
                status="released",
                batch_id=batch_token,
                released_count=len(matching),
                error_code=None,
            )


class _DirectedEffectFenceAdminView:
    """Kernel-only administrative capability over private fence state."""

    __slots__ = ("_state",)

    def __init__(self, state: _PidBoundDirectedEffectFence) -> None:
        self._state = state

    def register(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceRegistrationResultV1:
        return self._state.register(context)

    def release_batch(
        self,
        batch_id: str,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectFenceReleaseResultV1:
        return self._state.release_batch(batch_id, execution_attempt)


class _DirectedEffectFenceConsumeView:
    """Adapter-visible one-shot spending capability without administration."""

    __slots__ = ("_state",)

    def __init__(self, state: _PidBoundDirectedEffectFence) -> None:
        self._state = state

    def consume(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceConsumeResultV1:
        return self._state.consume(context)


def create_directed_effect_fence_ports() -> DirectedEffectFencePortsV1:
    """Internal composition root for one fixed-capacity process-local fence."""

    fence = _PidBoundDirectedEffectFence()
    return DirectedEffectFencePortsV1(
        admin=_DirectedEffectFenceAdminView(fence),
        consume=_DirectedEffectFenceConsumeView(fence),
    )
