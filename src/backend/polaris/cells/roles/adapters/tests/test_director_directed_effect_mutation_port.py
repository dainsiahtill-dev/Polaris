"""Task6 tests for the public consume-only Director mutation port."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.director.runtime.public import (
    DirectedEffectImmutableMapV1,
    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    DirectorEffectCurrentPolicyEvidenceCaptureResultV1,
    DirectorEffectPolicyBaselineCaptureRequestV1,
    DirectorEffectPolicyMemberBindingRequestV1,
    DirectorEffectPolicyMemberBindingResultV1,
    DirectorEffectPolicyRevalidationRequestV1,
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    DirectorEffectTargetStateEvidenceV1,
    DirectorRepairEffectV1,
    hash_directed_effect_arguments,
    hash_directed_effect_policy_revalidation_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    hash_directed_effect_target_state_components,
)
from polaris.cells.roles.adapters.public import (
    create_director_directed_effect_mutation_port,
)
from polaris.cells.roles.kernel.public import (
    DeferredDirectorRepairEffectBindingV1,
    DirectedEffectExecutionContextV1,
    DirectedEffectFenceConsumePortV1,
    DirectedEffectFenceConsumeResultV1,
    DirectedEffectFencePortsV1,
    DirectedEffectMutationPortResultV1,
    DirectedEffectMutationPortV1,
)
from polaris.cells.roles.kernel.public.directed_effect_service import (
    create_directed_effect_fence_ports,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_contracts import (
    _ARGUMENTS,
    _attempt,
    _claim_grant,
    _current_policy_evidence,
    _inventory,
    _member,
    _prepared_batch,
    _prepared_member,
)
from polaris.cells.runtime.task_runtime.public import DirectedEffectOperationResultV1


@pytest.fixture(autouse=True)
def _stub_task_runtime_receipt_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep adapter tests focused while requiring an explicit receipt-commit seam."""

    def commit(
        context: DirectedEffectExecutionContextV1,
        receipt_value: object,
    ) -> DirectedEffectOperationResultV1:
        grant = context.claim_grant
        assert isinstance(receipt_value, Mapping)
        receipt = dict(receipt_value)
        return DirectedEffectOperationResultV1(
            ok=True,
            code="receipt_committed",
            operation=grant.operation,
            state="RECEIPT_COMMITTED",
            version=grant.operation_version + 1,
            evidence={
                "event_id": "task-runtime-receipt-event",
                "receipt_ref": receipt["receipt_id"],
                "receipt_hash": receipt["receipt_hash"],
                "receipt_binding_hash": receipt["receipt_binding_hash"],
                "receipt_outcome": receipt["receipt_outcome"],
            },
        )

    def recovery(
        context: DirectedEffectExecutionContextV1,
        **_kwargs: object,
    ) -> DirectedEffectOperationResultV1:
        grant = context.claim_grant
        return DirectedEffectOperationResultV1(
            ok=True,
            code="recovery_pending",
            operation=grant.operation,
            state="RECOVERY_PENDING",
            version=grant.operation_version + 1,
            evidence={
                "event_id": "task-runtime-recovery-event",
                "recovery_evidence_ref": f"recovery://director/{context.context_id}",
                "recovery_evidence_hash": "9" * 64,
            },
        )

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._commit_physical_effect_receipt",
        commit,
    )
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._mark_physical_effect_recovery",
        recovery,
    )


def _allowed_revalidation(context: DirectedEffectExecutionContextV1) -> DirectorEffectPolicyRevalidationResultV1:
    snapshot = context.bound_snapshot.snapshot
    target = snapshot.baseline_target_state_evidence
    return DirectorEffectPolicyRevalidationResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        current_policy_version=snapshot.policy_version,
        current_policy_hash=snapshot.policy_hash,
        current_target_state_evidence=target,
        current_target_state_hash=target.target_state_hash,
        current_normalized_operation_hash=snapshot.normalized_operation_hash,
        target_observation_performed=True,
        current_evidence_hash=hash_directed_effect_policy_revalidation_evidence(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version=snapshot.policy_version,
            current_policy_hash=snapshot.policy_hash,
            current_target_state_evidence=target,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
        ),
    )


def _denied_revalidation(
    context: DirectedEffectExecutionContextV1,
    error_code: str = "deo_target_state_drift",
) -> DirectorEffectPolicyRevalidationResultV1:
    snapshot = context.bound_snapshot.snapshot
    target = snapshot.baseline_target_state_evidence
    return DirectorEffectPolicyRevalidationResultV1(
        status="denied",
        allowed=False,
        error_code=error_code,  # type: ignore[arg-type]
        current_policy_version=snapshot.policy_version,
        current_policy_hash=snapshot.policy_hash,
        current_target_state_evidence=target,
        current_target_state_hash=target.target_state_hash,
        current_normalized_operation_hash=snapshot.normalized_operation_hash,
        target_observation_performed=True,
        current_evidence_hash=hash_directed_effect_policy_revalidation_evidence(
            status="denied",
            allowed=False,
            error_code=error_code,  # type: ignore[arg-type]
            current_policy_version=snapshot.policy_version,
            current_policy_hash=snapshot.policy_hash,
            current_target_state_evidence=target,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
        ),
    )


class _PolicySpy:
    def __init__(
        self,
        events: list[str],
        context: DirectedEffectExecutionContextV1,
        *,
        denied: bool = False,
        denial_code: str = "deo_target_state_drift",
        malformed: bool = False,
        responses: list[DirectorEffectPolicyRevalidationResultV1] | None = None,
    ) -> None:
        self.events = events
        self.context = context
        self.denied = denied
        self.denial_code = denial_code
        self.malformed = malformed
        self.responses = list(responses or ())

    async def capture_baseline_snapshot(
        self,
        request: DirectorEffectPolicyBaselineCaptureRequestV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        raise AssertionError(f"mutation port must not capture baseline snapshot: {request!r}")

    async def snapshot(self, request: DirectorEffectPolicySnapshotRequestV1) -> DirectorEffectPolicySnapshotResultV1:
        raise AssertionError(f"mutation port must not recapture baseline snapshot: {request!r}")

    async def capture_current_policy_evidence(
        self,
        request: DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    ) -> DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
        raise AssertionError(f"mutation port must consume claimed current evidence: {request!r}")

    def bind_member(
        self,
        request: DirectorEffectPolicyMemberBindingRequestV1,
    ) -> DirectorEffectPolicyMemberBindingResultV1:
        raise AssertionError(f"mutation port must not rebind member: {request!r}")

    async def revalidate(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        self.events.append("revalidate")
        assert request.claim_grant is self.context.claim_grant
        assert request.bound_snapshot is self.context.bound_snapshot
        if self.malformed:
            return cast(DirectorEffectPolicyRevalidationResultV1, object())
        if self.responses:
            return self.responses.pop(0)
        return (
            _denied_revalidation(self.context, self.denial_code) if self.denied else _allowed_revalidation(self.context)
        )


class _ConsumeSpy:
    def __init__(self, events: list[str], delegate: DirectedEffectFenceConsumePortV1) -> None:
        self.events = events
        self.delegate = delegate

    def consume(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceConsumeResultV1:
        self.events.append("consume")
        return self.delegate.consume(context)


class _MalformedConsume:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def consume(self, context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceConsumeResultV1:
        self.events.append("consume")
        return cast(DirectedEffectFenceConsumeResultV1, object())


class _PhysicalSpy:
    def __init__(
        self,
        events: list[str],
        *,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
        expected_tool: str = "write_file",
        expected_args: dict[str, Any] | None = None,
    ) -> None:
        self.events = events
        self.result = result or {"ok": True, "file": "src/a.py", "changed": True}
        self.error = error
        self.expected_tool = expected_tool
        self.expected_args = expected_args or {"path": "src/a.py"}
        self.calls = 0

    def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
        repair_effect: DirectorRepairEffectV1 | None = None,
    ) -> dict[str, Any]:
        self.events.append("physical")
        self.calls += 1
        assert tool_name == self.expected_tool
        assert args == self.expected_args
        assert task_id
        if repair_effect is not None:
            assert repair_effect.tool_name == tool_name
            assert dict(repair_effect.arguments) == args
        if self.error is not None:
            raise self.error
        return self.result


def _context() -> tuple[DirectedEffectExecutionContextV1, DirectedEffectFencePortsV1, str]:
    batch = _prepared_batch()
    prepared = batch.prepared_members[0]
    member = prepared.member
    evidence = batch.authorization_evidence_by_call_id[0][1]
    bound_snapshot = prepared.policy_binding.bound_snapshot
    assert bound_snapshot is not None
    claim_grant = _claim_grant(batch.execution_attempt, batch.parent_binding, member)
    context = DirectedEffectExecutionContextV1(
        context_id="context-1",
        batch_id=batch.parent_binding.correlation.batch_id,
        creator_pid=os.getpid(),
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=evidence.arguments_hash,
        authorization_evidence=evidence,
        claim_grant=claim_grant,
        bound_snapshot=bound_snapshot,
        current_policy_evidence=_current_policy_evidence(batch, member, claim_grant),
        current_job_token_restriction_evidence=(),
    )
    fence = create_directed_effect_fence_ports()
    assert fence.admin.register(context).ok
    return context, fence, batch.execution_attempt.workspace


def _target_evidence(
    context: DirectedEffectExecutionContextV1,
    *,
    exists: bool,
    content_hash: str,
) -> DirectorEffectTargetStateEvidenceV1:
    baseline = context.bound_snapshot.snapshot.baseline_target_state_evidence
    target_state_hash = hash_directed_effect_target_state_components(
        target_path=baseline.target_path,
        exists=exists,
        before_content_hash=content_hash,
        minimal_content_evidence=baseline.minimal_content_evidence,
        agents_policy_hash=baseline.agents_policy_hash,
        is_no_file_state=False,
    )
    return DirectorEffectTargetStateEvidenceV1(
        target_path=baseline.target_path,
        exists=exists,
        before_content_hash=content_hash,
        minimal_content_evidence=baseline.minimal_content_evidence,
        agents_policy_hash=baseline.agents_policy_hash,
        target_state_hash=target_state_hash,
        is_no_file_state=False,
    )


def _revalidation_for_target(
    context: DirectedEffectExecutionContextV1,
    target: DirectorEffectTargetStateEvidenceV1,
    *,
    allowed: bool,
) -> DirectorEffectPolicyRevalidationResultV1:
    snapshot = context.bound_snapshot.snapshot
    error_code = None if allowed else "deo_target_state_drift"
    status = "allowed" if allowed else "denied"
    evidence_hash = hash_directed_effect_policy_revalidation_evidence(
        status=status,
        allowed=allowed,
        error_code=error_code,
        current_policy_version=snapshot.policy_version,
        current_policy_hash=snapshot.policy_hash,
        current_target_state_evidence=target,
        current_normalized_operation_hash=snapshot.normalized_operation_hash,
        target_observation_performed=True,
    )
    return DirectorEffectPolicyRevalidationResultV1(
        status=status,
        allowed=allowed,
        error_code=error_code,
        current_policy_version=snapshot.policy_version,
        current_policy_hash=snapshot.policy_hash,
        current_target_state_evidence=target,
        current_target_state_hash=target.target_state_hash,
        current_normalized_operation_hash=snapshot.normalized_operation_hash,
        target_observation_performed=True,
        current_evidence_hash=evidence_hash,
    )


def _repair_binding(*, tool_name: str) -> DeferredDirectorRepairEffectBindingV1:
    after_content = "after"
    arguments = (
        (("file", "src/a.py"),) if tool_name == "delete_file" else (("content", after_content), ("file", "src/a.py"))
    )
    effect = DirectorRepairEffectV1(
        call_id=f"source-{tool_name}",
        operation_id=f"operation-{tool_name}",
        tool_name=tool_name,  # type: ignore[arg-type]
        arguments=arguments,
        contingency_kind="forward",
        target_path="src/a.py",
        expected_before_hash="d" * 64,
        expected_after_hash=(
            "e" * 64 if tool_name == "delete_file" else hashlib.sha256(after_content.encode("utf-8")).hexdigest()
        ),
        exists_before=True,
        exists_after=tool_name != "delete_file",
    )
    return DeferredDirectorRepairEffectBindingV1(
        request_id="repair-request",
        request_hash="a" * 64,
        plan_hash="b" * 64,
        effect=effect,
    )


def _repair_context(
    binding: DeferredDirectorRepairEffectBindingV1,
    *,
    workspace: str | None = None,
) -> tuple[
    DirectedEffectExecutionContextV1,
    DirectedEffectFencePortsV1,
    str,
    tuple[tuple[str, object], ...],
]:
    attempt = _attempt()
    if workspace is not None:
        attempt = replace(attempt, workspace=workspace)
    member = _member(
        0,
        tool_call_id=binding.tool_call_id,
        normalized_tool_name=binding.effect.tool_name,
    )
    inventory = _inventory(attempt, (member,))
    prepared_member = _prepared_member(
        member,
        stream_head=3,
        execution_attempt=attempt,
        normalized_arguments=binding.effect.arguments,
    )
    batch = _prepared_batch(
        execution_attempt=attempt,
        inventory=inventory,
        prepared_members=(prepared_member,),
        call_id_index=((binding.tool_call_id, 0),),
        normalized_arguments=binding.effect.arguments,
    )
    evidence = batch.authorization_evidence_by_call_id[0][1]
    bound_snapshot = prepared_member.policy_binding.bound_snapshot
    assert bound_snapshot is not None
    claim_grant = _claim_grant(attempt, batch.parent_binding, member)
    context = DirectedEffectExecutionContextV1(
        context_id="repair-context",
        batch_id=batch.parent_binding.correlation.batch_id,
        creator_pid=os.getpid(),
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=evidence.arguments_hash,
        authorization_evidence=evidence,
        claim_grant=claim_grant,
        bound_snapshot=bound_snapshot,
        current_policy_evidence=_current_policy_evidence(batch, member, claim_grant),
        current_job_token_restriction_evidence=(),
    )
    fence = create_directed_effect_fence_ports()
    assert fence.admin.register(context).ok
    return context, fence, attempt.workspace, binding.effect.arguments


def _forged_context(
    context: DirectedEffectExecutionContextV1,
    **changes: object,
) -> DirectedEffectExecutionContextV1:
    forged = object.__new__(DirectedEffectExecutionContextV1)
    for field in fields(context):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(context, field.name)))
    return forged


def _forged_bound_snapshot(context: DirectedEffectExecutionContextV1) -> object:
    bound_snapshot = context.bound_snapshot
    forged = object.__new__(type(bound_snapshot))
    for field in fields(bound_snapshot):
        value = "b" * 64 if field.name == "member_binding_hash" else getattr(bound_snapshot, field.name)
        object.__setattr__(forged, field.name, value)
    return forged


def _port(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    context: DirectedEffectExecutionContextV1,
    fence: DirectedEffectFencePortsV1,
    workspace: str,
    denied: bool = False,
    denial_code: str = "deo_target_state_drift",
    malformed: bool = False,
    physical: _PhysicalSpy | None = None,
    policy_responses: list[DirectorEffectPolicyRevalidationResultV1] | None = None,
) -> tuple[DirectedEffectMutationPortV1, _PhysicalSpy]:
    executor = physical or _PhysicalSpy(events)
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.execution_tools._create_director_tool_executor",
        lambda _workspace: executor,
    )
    port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=_PolicySpy(
            events,
            context,
            denied=denied,
            denial_code=denial_code,
            malformed=malformed,
            responses=policy_responses,
        ),
        fence_consume_port=_ConsumeSpy(events, fence.consume),
    )
    return port, executor


@pytest.mark.asyncio
async def test_public_port_revalidates_then_consumes_then_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    from polaris.cells.director.runtime.public import validate_directed_effect_execution as real_validate

    def record_validate(*args: object, **kwargs: object) -> object:
        events.append("validate")
        return real_validate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port.validate_directed_effect_execution",
        record_validate,
    )
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert type(result) is DirectedEffectMutationPortResultV1
    assert result.status == "executed"
    assert result.tool_result is not None
    assert events == ["validate", "revalidate", "consume", "physical"]
    assert physical.calls == 1


@pytest.mark.asyncio
async def test_receipt_commit_failure_marks_recovery_without_reexecuting_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    recoveries: list[tuple[str, object]] = []

    def reject_commit(
        _context: DirectedEffectExecutionContextV1,
        receipt: object,
    ) -> DirectedEffectOperationResultV1:
        events.append("receipt_commit")
        return DirectedEffectOperationResultV1(
            ok=False,
            code="stream_append_failed",
            evidence={"receipt": receipt},
        )

    def record_recovery(
        _context: DirectedEffectExecutionContextV1,
        **kwargs: object,
    ) -> DirectedEffectOperationResultV1:
        events.append("recovery_pending")
        recoveries.append((cast(str, kwargs["reason"]), kwargs["evidence"]))
        grant = context.claim_grant
        return DirectedEffectOperationResultV1(
            ok=True,
            code="recovery_pending",
            operation=grant.operation,
            state="RECOVERY_PENDING",
            version=grant.operation_version + 1,
            evidence={
                "event_id": "task-runtime-recovery-event",
                "recovery_evidence_ref": f"recovery://director/{context.context_id}",
                "recovery_evidence_hash": "9" * 64,
            },
        )

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._commit_physical_effect_receipt",
        reject_commit,
    )
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._mark_physical_effect_recovery",
        record_recovery,
    )
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]
    replay = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert result.tool_result is not None
    assert replay.status == "denied"
    assert replay.error_code == "deo_context_replayed"
    assert physical.calls == 1
    assert events == [
        "revalidate",
        "consume",
        "physical",
        "receipt_commit",
        "recovery_pending",
        "revalidate",
        "consume",
    ]
    assert recoveries and "could not be committed" in recoveries[0][0]


@pytest.mark.asyncio
async def test_receipt_commit_hash_mismatch_marks_recovery_without_claiming_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    recoveries: list[str] = []

    def forged_commit(
        _context: DirectedEffectExecutionContextV1,
        receipt_value: object,
    ) -> DirectedEffectOperationResultV1:
        assert isinstance(receipt_value, Mapping)
        receipt = dict(receipt_value)
        return DirectedEffectOperationResultV1(
            ok=True,
            code="receipt_committed",
            operation=context.claim_grant.operation,
            state="RECEIPT_COMMITTED",
            version=context.claim_grant.operation_version + 1,
            evidence={
                "event_id": "task-runtime-receipt-event",
                "receipt_ref": receipt["receipt_id"],
                "receipt_hash": "0" * 64,
                "receipt_binding_hash": receipt["receipt_binding_hash"],
                "receipt_outcome": receipt["receipt_outcome"],
            },
        )

    def record_recovery(
        _context: DirectedEffectExecutionContextV1,
        **kwargs: object,
    ) -> DirectedEffectOperationResultV1:
        recoveries.append(cast(str, kwargs["reason"]))
        grant = context.claim_grant
        return DirectedEffectOperationResultV1(
            ok=True,
            code="recovery_pending",
            operation=grant.operation,
            state="RECOVERY_PENDING",
            version=grant.operation_version + 1,
            evidence={
                "event_id": "task-runtime-recovery-event",
                "recovery_evidence_ref": f"recovery://director/{context.context_id}",
                "recovery_evidence_hash": "9" * 64,
            },
        )

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._commit_physical_effect_receipt",
        forged_commit,
    )
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._mark_physical_effect_recovery",
        record_recovery,
    )
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert result.tool_result is not None
    assert physical.calls == 1
    assert recoveries == ["physical receipt could not be committed to TaskRuntime"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ("write_file", "delete_file"))
async def test_repair_binding_stale_before_state_denies_with_zero_physical_execution(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    binding = _repair_binding(tool_name=tool_name)
    context, fence, workspace, arguments = _repair_context(binding)
    stale_target = _target_evidence(context, exists=True, content_hash="f" * 64)
    events: list[str] = []
    physical = _PhysicalSpy(
        events,
        expected_tool=tool_name,
        expected_args=dict(arguments),
    )
    port, _ = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        physical=physical,
        policy_responses=[_revalidation_for_target(context, stale_target, allowed=True)],
    )

    result = await port.execute_mutation(
        context,
        tool_name,
        arguments,
        binding,
    )

    assert result.status == "denied"
    assert result.error_code == "deo_target_state_drift"
    assert events == ["revalidate"]
    assert physical.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ("write_file", "delete_file"))
async def test_repair_binding_commit_time_cas_preserves_post_revalidation_external_change(
    tmp_path: Path,
    tool_name: str,
) -> None:
    workspace_path = tmp_path / "workspace"
    target = workspace_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")
    before_hash = hashlib.sha256(b"before").hexdigest()
    after_content = "after"
    arguments = (
        (("file", "src/a.py"),) if tool_name == "delete_file" else (("content", after_content), ("file", "src/a.py"))
    )
    effect = DirectorRepairEffectV1(
        call_id=f"source-{tool_name}",
        operation_id=f"operation-{tool_name}",
        tool_name=tool_name,  # type: ignore[arg-type]
        arguments=arguments,
        contingency_kind="forward",
        target_path="src/a.py",
        expected_before_hash=before_hash,
        expected_after_hash=(
            "e" * 64 if tool_name == "delete_file" else hashlib.sha256(after_content.encode("utf-8")).hexdigest()
        ),
        exists_before=True,
        exists_after=tool_name != "delete_file",
    )
    binding = DeferredDirectorRepairEffectBindingV1(
        request_id="repair-request-race",
        request_hash="a" * 64,
        plan_hash="b" * 64,
        effect=effect,
    )
    context, fence, workspace, normalized_arguments = _repair_context(
        binding,
        workspace=str(workspace_path),
    )
    expected_target = _target_evidence(
        context,
        exists=True,
        content_hash=before_hash,
    )
    events: list[str] = []

    class _RacePolicy(_PolicySpy):
        async def revalidate(
            self,
            request: DirectorEffectPolicyRevalidationRequestV1,
        ) -> DirectorEffectPolicyRevalidationResultV1:
            result = await super().revalidate(request)
            if target.read_text(encoding="utf-8") == "before":
                target.write_text("external", encoding="utf-8")
            return result

    port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=_RacePolicy(
            events,
            context,
            responses=[_revalidation_for_target(context, expected_target, allowed=True)],
        ),
        fence_consume_port=_ConsumeSpy(events, fence.consume),
    )

    result = await port.execute_mutation(
        context,
        tool_name,
        normalized_arguments,
        binding,
    )

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert result.tool_result is not None
    assert target.read_text(encoding="utf-8") == "external"
    assert events == ["revalidate", "consume"]


@pytest.mark.asyncio
async def test_repair_binding_matching_post_state_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = _repair_binding(tool_name="write_file")
    context, fence, workspace, arguments = _repair_context(binding)
    after_target = _target_evidence(
        context,
        exists=True,
        content_hash=binding.effect.expected_after_hash,
    )
    events: list[str] = []
    physical = _PhysicalSpy(
        events,
        expected_args=dict(arguments),
    )
    port, _ = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        physical=physical,
        policy_responses=[
            _allowed_revalidation(context),
            _revalidation_for_target(context, after_target, allowed=False),
        ],
    )

    result = await port.execute_mutation(context, "write_file", arguments, binding)

    assert result.status == "executed"
    assert events == ["revalidate", "consume", "physical", "revalidate"]
    assert physical.calls == 1


@pytest.mark.asyncio
async def test_real_repair_cas_returns_hash_bound_physical_effect_receipt(tmp_path: Path) -> None:
    """A real guarded repair write returns the adapter receipt consumed by ToolBatchRuntime."""

    workspace_path = tmp_path / "workspace"
    target = workspace_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")
    before_hash = hashlib.sha256(b"before").hexdigest()
    after_content = "after"
    after_hash = hashlib.sha256(after_content.encode("utf-8")).hexdigest()
    effect = DirectorRepairEffectV1(
        call_id="source-real-repair",
        operation_id="operation-real-repair",
        tool_name="write_file",
        arguments=(("content", after_content), ("file", "src/a.py")),
        contingency_kind="forward",
        target_path="src/a.py",
        expected_before_hash=before_hash,
        expected_after_hash=after_hash,
        exists_before=True,
        exists_after=True,
    )
    binding = DeferredDirectorRepairEffectBindingV1(
        request_id="repair-request-real",
        request_hash="a" * 64,
        plan_hash="b" * 64,
        effect=effect,
    )
    context, fence, workspace, arguments = _repair_context(
        binding,
        workspace=str(workspace_path),
    )
    events: list[str] = []

    class _FilesystemPolicy(_PolicySpy):
        async def revalidate(
            self,
            request: DirectorEffectPolicyRevalidationRequestV1,
        ) -> DirectorEffectPolicyRevalidationResultV1:
            self.events.append("revalidate")
            assert request.claim_grant is context.claim_grant
            content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            evidence = _target_evidence(context, exists=True, content_hash=content_hash)
            return _revalidation_for_target(context, evidence, allowed=True)

    port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=_FilesystemPolicy(events, context),
        fence_consume_port=_ConsumeSpy(events, fence.consume),
    )

    result = await port.execute_mutation(context, "write_file", arguments, binding)

    assert result.status == "executed"
    assert result.tool_result is not None
    assert target.read_text(encoding="utf-8") == after_content
    payload = dict(result.tool_result.payload)
    receipt_value = payload["effect_receipt"]
    assert isinstance(receipt_value, DirectedEffectImmutableMapV1)
    receipt = dict(receipt_value.items)
    assert receipt["authoritative"] is True
    assert receipt["durable"] is True
    assert receipt["parent_close_eligible"] is True
    assert receipt["schema_version"] == "roles.adapters.director_physical_effect_receipt.v2"
    assert receipt["receipt_binding_hash"] == context.claim_grant.member.expected_receipt_binding_hash
    assert receipt["receipt_outcome"] == "succeeded"
    assert receipt["context_id"] == context.context_id
    assert receipt["claim_grant_hash"] == context.claim_grant.grant_hash
    assert receipt["tool_call_id"] == binding.tool_call_id
    assert receipt["repair_binding_hash"] == binding.binding_hash
    assert receipt["plan_hash"] == binding.plan_hash
    assert receipt["target_state_hash"]
    receipt_hash_payload = tuple(
        (key, value) for key, value in receipt_value.items if key not in {"receipt_hash", "receipt_id"}
    )
    assert hash_directed_effect_arguments(receipt_hash_payload) == receipt["receipt_hash"]
    tampered_hash_payload = tuple(
        (key, False if key == "authoritative" else value) for key, value in receipt_hash_payload
    )
    assert hash_directed_effect_arguments(tampered_hash_payload) != receipt["receipt_hash"]
    commit_value = payload["effect_receipt_commit"]
    assert isinstance(commit_value, DirectedEffectImmutableMapV1)
    commit = dict(commit_value.items)
    assert commit["code"] == "receipt_committed"
    assert commit["state"] == "RECEIPT_COMMITTED"
    assert commit["operation_id"] == context.claim_grant.operation.operation_id
    assert commit["receipt_ref"] == receipt["receipt_id"]
    assert commit["receipt_hash"] == receipt["receipt_hash"]
    assert commit["receipt_binding_hash"] == receipt["receipt_binding_hash"]
    assert commit["receipt_outcome"] == receipt["receipt_outcome"]
    assert events == ["revalidate", "consume", "revalidate"]


@pytest.mark.asyncio
async def test_repair_binding_post_state_mismatch_fails_closed_after_physical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _repair_binding(tool_name="write_file")
    context, fence, workspace, arguments = _repair_context(binding)
    wrong_after = _target_evidence(context, exists=True, content_hash="f" * 64)
    events: list[str] = []
    physical = _PhysicalSpy(
        events,
        expected_args=dict(arguments),
    )
    port, _ = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        physical=physical,
        policy_responses=[
            _allowed_revalidation(context),
            _revalidation_for_target(context, wrong_after, allowed=False),
        ],
    )

    result = await port.execute_mutation(context, "write_file", arguments, binding)

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert events == ["revalidate", "consume", "physical", "revalidate"]
    assert physical.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    (
        "missing",
        "forged",
        "forged_bound_snapshot",
        "tool_mismatch",
        "stale_policy",
        "target_drift",
        "reconstructed",
        "pid",
    ),
)
async def test_denials_have_zero_physical_execution(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    supplied = context
    tool_name = "write_file"
    denied = case in {"stale_policy", "target_drift"}
    denial_code = "deo_policy_version_drift" if case == "stale_policy" else "deo_target_state_drift"
    if case == "missing":
        supplied = cast(DirectedEffectExecutionContextV1, None)
    elif case == "forged":
        supplied = _forged_context(context, arguments_hash="b" * 64)
    elif case == "forged_bound_snapshot":
        supplied = _forged_context(context, bound_snapshot=_forged_bound_snapshot(context))
    elif case == "tool_mismatch":
        tool_name = "execute_command"
    elif case == "reconstructed":
        supplied = replace(context)
    elif case == "pid":
        supplied = _forged_context(context, creator_pid=os.getpid() + 1)
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        denied=denied,
        denial_code=denial_code,
    )

    result = await port.execute_mutation(supplied, tool_name, _ARGUMENTS)  # type: ignore[union-attr]

    assert type(result) is DirectedEffectMutationPortResultV1
    assert result.status == "denied"
    assert physical.calls == 0
    assert "physical" not in events


@pytest.mark.asyncio
async def test_public_port_rejects_reconstructed_context_before_physical_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
    )

    result = await port.execute_mutation(replace(context), "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "denied"
    assert result.error_code == "deo_context_reconstructed"
    assert events == ["revalidate", "consume"]
    assert physical.calls == 0


@pytest.mark.asyncio
async def test_replay_is_denied_after_one_physical_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
    )

    first = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]
    replay = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert first.status == "executed"
    assert replay.status == "denied"
    assert replay.error_code == "deo_context_replayed"
    assert physical.calls == 1


@pytest.mark.asyncio
async def test_declared_physical_failure_is_typed_and_spends_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    physical = _PhysicalSpy(events, result={"ok": False, "error": "write failed"})
    port, _ = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        physical=physical,
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]
    replay = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert result.tool_result is not None
    payload = dict(result.tool_result.payload)
    assert payload.get("physical_error") == "write failed"
    assert payload.get("failure_kind") == "physical_result_failed"
    assert replay.error_code == "deo_context_replayed"
    assert physical.calls == 1


@pytest.mark.asyncio
async def test_unexpected_physical_exception_returns_durable_recovery_and_spends_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    physical = _PhysicalSpy(events, error=RuntimeError("physical boom"))
    port, _ = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        physical=physical,
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]
    replay = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert result.tool_result is not None
    assert replay.error_code == "deo_context_replayed"
    assert physical.calls == 1


@pytest.mark.asyncio
async def test_physical_executor_factory_exception_returns_durable_recovery_and_spends_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    recovery_evidence: list[tuple[str, tuple[tuple[str, object], ...]]] = []

    def fail_factory(_workspace: str) -> object:
        events.append("factory")
        raise LookupError("factory boom")

    def record_recovery(
        recovery_context: DirectedEffectExecutionContextV1,
        *,
        reason: str,
        evidence: tuple[tuple[str, object], ...],
    ) -> DirectedEffectOperationResultV1:
        events.append("recovery_pending")
        recovery_evidence.append((reason, evidence))
        grant = recovery_context.claim_grant
        return DirectedEffectOperationResultV1(
            ok=True,
            code="recovery_pending",
            operation=grant.operation,
            state="RECOVERY_PENDING",
            version=grant.operation_version + 1,
            evidence={
                "event_id": "task-runtime-recovery-event",
                "recovery_evidence_ref": f"recovery://director/{recovery_context.context_id}",
                "recovery_evidence_hash": "9" * 64,
            },
        )

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.execution_tools._create_director_tool_executor",
        fail_factory,
    )
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port._mark_physical_effect_recovery",
        record_recovery,
    )
    port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=_PolicySpy(events, context),
        fence_consume_port=_ConsumeSpy(events, fence.consume),
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]
    replay = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "failed"
    assert result.error_code == "deo_physical_execution_failed"
    assert result.tool_result is not None
    assert replay.error_code == "deo_context_replayed"
    assert events == ["revalidate", "consume", "factory", "recovery_pending", "revalidate", "consume"]
    assert recovery_evidence
    assert ("failure_kind", "physical_executor_construction_exception") in recovery_evidence[0][1]


def test_mutation_port_exposes_no_injectable_physical_executor_slot() -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=_PolicySpy(events, context),
        fence_consume_port=_ConsumeSpy(events, fence.consume),
    )

    with pytest.raises(AttributeError):
        cast(Any, port)._executor = _PhysicalSpy(events)


@pytest.mark.asyncio
async def test_malformed_policy_result_is_denied_before_fence_or_physical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, fence, workspace = _context()
    events: list[str] = []
    port, physical = _port(
        monkeypatch,
        events=events,
        context=context,
        fence=fence,
        workspace=workspace,
        malformed=True,
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)  # type: ignore[union-attr]

    assert result.status == "denied"
    assert events == ["revalidate"]
    assert physical.calls == 0


@pytest.mark.asyncio
async def test_malformed_consume_result_is_typed_denial_before_physical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _fence, workspace = _context()
    events: list[str] = []
    physical = _PhysicalSpy(events)
    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.execution_tools._create_director_tool_executor",
        lambda _workspace: physical,
    )
    port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=_PolicySpy(events, context),
        fence_consume_port=_MalformedConsume(events),
    )

    result = await port.execute_mutation(context, "write_file", _ARGUMENTS)

    assert result.status == "denied"
    assert result.error_code == "deo_context_identity_mismatch"
    assert events == ["revalidate", "consume"]
    assert physical.calls == 0
