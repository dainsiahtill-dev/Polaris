"""Task5 lifecycle tests against real TaskRuntime public command services."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from polaris.cells.director.runtime.public import (
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    DirectorEffectAuthorizationBindingV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectClassificationEvidenceV1,
    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    DirectorEffectCurrentPolicyEvidenceCaptureResultV1,
    DirectorEffectCurrentPolicyEvidenceV1,
    DirectorEffectPolicyBaselineCaptureRequestV1,
    DirectorEffectPolicyBoundSnapshotV1,
    DirectorEffectPolicyMemberBindingRequestV1,
    DirectorEffectPolicyMemberBindingResultV1,
    DirectorEffectPolicyOperationSubjectV1,
    DirectorEffectPolicyRevalidationRequestV1,
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    DirectorEffectPreflightResultV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_arguments,
    hash_directed_effect_policy_member_binding,
    hash_director_effect_authorization_evidence,
    project_director_effect_public_policy_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    hash_directed_effect_policy_snapshot_evidence,
    hash_directed_effect_target_state_components,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import (
    DirectedEffectLifecycleCandidateV1,
    DirectedEffectLifecycleService,
    DirectedEffectTaskRuntimePortsV1,
    refresh_directed_effect_attempt,
)
from polaris.cells.roles.kernel.public.directed_effect_service import (
    create_directed_effect_fence_ports,
)
from polaris.cells.runtime.task_runtime.public import (
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectOperationResultV1,
    DirectedEffectParentRegistryIdentityV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectOperationQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    SealDirectedEffectInventoryCommandV1,
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    admit_directed_effect_operation,
    admit_directed_effect_parent_batch,
    claim_directed_effect,
    commit_directed_effect_receipt,
    create_task_runtime_execution_attempt_authority,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    finalize_directed_effect_inventory_admission,
    get_directed_effect_operation,
    seal_directed_effect_inventory,
)
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
from polaris.kernelone.process.command_executor import CommandExecutionService

_HASH = "a" * 64
_ARGUMENTS = (("content", "after\n"), ("path", "src/a.py"))
_ARGUMENTS_HASH = hash_directed_effect_arguments(_ARGUMENTS)
_T = TypeVar("_T")


def _job_restriction_evidence(
    *,
    allowed_commands: tuple[str, ...] = (),
    allowed_paths: tuple[str, ...] = ("src/",),
) -> DirectedEffectImmutableItemsV1:
    allowed_commands_hash = hash_directed_effect_arguments((("allowed_commands", allowed_commands),))
    allowed_paths_hash = hash_directed_effect_arguments((("allowed_paths", allowed_paths),))
    return (
        ("allowed_commands", allowed_commands),
        ("allowed_commands_hash", allowed_commands_hash),
        ("allowed_paths", allowed_paths),
        ("allowed_paths_hash", allowed_paths_hash),
        ("job_token_hash", _HASH),
        ("job_token_id", "job-1"),
    )


def _setup_attempt(workspace: str) -> TaskRuntimeExecutionAttemptIdentityV1:
    """Create one real attempt using public FactStream and TaskRuntime APIs only."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="deo-lifecycle-test",
        )
    )
    service = TaskRuntimeService(workspace)
    task_id = int(service.create_task_row(subject="deo lifecycle")["id"])
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(
        service.claim_execution(
            task_id,
            worker_id="worker",
            role_id="director",
            run_id="run",
            external_task_id="DEO-LIFECYCLE",
            selection_source="test",
        )["execution_attempt"]
    )


def _authority(
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    *,
    heartbeat_events: list[HeartbeatTaskRuntimeExecutionAttemptCommandV1] | None = None,
) -> TaskRuntimeExecutionAttemptAuthorityV1:
    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        if heartbeat_events is not None:
            heartbeat_events.append(command)
        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=command.identity,
        )

    return create_task_runtime_execution_attempt_authority(
        execution_attempt,
        heartbeat=heartbeat,
    )


def _forged(instance: _T, **changes: object) -> _T:
    forged = object.__new__(type(instance))
    for field in fields(cast(Any, instance)):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(instance, field.name)))
    return cast(_T, forged)


def _operation_hash(
    *,
    workspace: str,
    ordinal: int,
    tool_call_id: str,
    normalized_tool_name: str = "write_file",
    normalized_arguments: DirectedEffectImmutableItemsV1 = _ARGUMENTS,
    effect_type: str = "write",
    execution_mode: str = "write_serial",
    turn_id: str = "turn-1",
    batch_id: str = "batch-1",
) -> str:
    return hash_directed_effect_arguments(
        (
            ("batch_id", batch_id),
            ("effect_type", effect_type),
            ("execution_mode", execution_mode),
            ("inventory_ordinal", ordinal),
            ("normalized_arguments", DirectedEffectImmutableMapV1(items=normalized_arguments)),
            ("normalized_tool_name", normalized_tool_name),
            ("tool_call_id", tool_call_id),
            ("turn_id", turn_id),
            ("workspace", workspace),
        )
    )


def _candidate(
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    *,
    ordinal: int,
    tool_call_id: str | None = None,
    normalized_tool_name: str = "write_file",
    normalized_arguments: DirectedEffectImmutableItemsV1 = _ARGUMENTS,
    effect_type: str = "write",
    execution_mode: str = "write_serial",
    target_path: str = "src/a.py",
    target_exists: bool = True,
    target_before_content_hash: str = _HASH,
    is_no_file_state: bool = False,
    turn_id: str = "turn-1",
    batch_id: str = "batch-1",
    allowed_commands: tuple[str, ...] = (),
    allowed_paths: tuple[str, ...] = ("src/",),
) -> DirectedEffectLifecycleCandidateV1:
    tool_call_id = tool_call_id or f"call-{ordinal}"
    arguments_hash = hash_directed_effect_arguments(normalized_arguments)
    operation_hash = _operation_hash(
        workspace=attempt.workspace,
        ordinal=ordinal,
        tool_call_id=tool_call_id,
        normalized_tool_name=normalized_tool_name,
        normalized_arguments=normalized_arguments,
        effect_type=effect_type,
        execution_mode=execution_mode,
        turn_id=turn_id,
        batch_id=batch_id,
    )
    intent = DirectedEffectInventoryIntentV1(
        ordinal=ordinal,
        tool_call_id=tool_call_id,
        normalized_tool_name=normalized_tool_name,
        effect_type=effect_type,
        execution_mode=execution_mode,
        intended_effect_fingerprint=operation_hash,
        policy_verdict_hash=operation_hash,
        expected_receipt_binding_hash=operation_hash,
    )
    subject = DirectorEffectPolicyOperationSubjectV1(
        workspace=attempt.workspace,
        turn_id=turn_id,
        batch_id=batch_id,
        tool_call_id=tool_call_id,
        inventory_ordinal=ordinal,
        normalized_tool_name=normalized_tool_name,
        normalized_arguments=normalized_arguments,
        effect_type=effect_type,
        execution_mode=execution_mode,
        prospective_operation_hash=operation_hash,
    )
    target_hash = hash_directed_effect_target_state_components(
        target_path=target_path,
        exists=target_exists,
        before_content_hash=target_before_content_hash,
        minimal_content_evidence=(),
        agents_policy_hash=_HASH,
        is_no_file_state=is_no_file_state,
    )
    target = DirectorEffectTargetStateEvidenceV1(
        target_path=target_path,
        exists=target_exists,
        before_content_hash=target_before_content_hash,
        minimal_content_evidence=(),
        agents_policy_hash=_HASH,
        target_state_hash=target_hash,
        is_no_file_state=is_no_file_state,
    )
    snapshot = DirectorEffectPolicySnapshotResultV1(
        status="allowed",
        allowed=True,
        error_code=None,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        target_state_hash=target_hash,
        normalized_operation_hash=operation_hash,
        evidence_hash=hash_directed_effect_policy_snapshot_evidence(
            status="allowed",
            allowed=True,
            error_code=None,
            policy_version="v1",
            policy_hash=_HASH,
            subject=subject,
            baseline_target_state_evidence=target,
            normalized_operation_hash=operation_hash,
        ),
    )
    restrictions = _job_restriction_evidence(
        allowed_commands=allowed_commands,
        allowed_paths=allowed_paths,
    )
    execution_attempt_id = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt).execution_attempt_id
    capability_scope_hash = str(dict(restrictions)["allowed_paths_hash"])
    job_token_evidence_hash = hash_directed_effect_arguments(restrictions)
    allowed_command_hash = str(dict(restrictions)["allowed_commands_hash"])
    authorization_hash = hash_director_effect_authorization_evidence(
        workspace=attempt.workspace,
        execution_attempt_id=execution_attempt_id,
        turn_id=turn_id,
        batch_id=batch_id,
        tool_call_id=tool_call_id,
        normalized_tool_name=normalized_tool_name,
        arguments_hash=arguments_hash,
        tool_spec_hash=_HASH,
        role_policy_id="director",
        role_policy_hash=_HASH,
        canonical_allow_list_hash=_HASH,
        capability_scope=allowed_paths,
        capability_scope_hash=capability_scope_hash,
        job_token_id="job-1",
        job_token_evidence_hash=job_token_evidence_hash,
        execution_envelope_hash=_HASH,
        allowed_command_hash=allowed_command_hash,
        mutation_guard_mode="strict",
        bound_policy_snapshot_hash=snapshot.evidence_hash,
        target_state_hash=snapshot.target_state_hash,
        normalized_operation_hash=snapshot.normalized_operation_hash,
        policy_version=snapshot.policy_version,
        policy_hash=snapshot.policy_hash,
    )
    authorization = DirectorEffectAuthorizationEvidenceV1(
        workspace=attempt.workspace,
        execution_attempt_id=execution_attempt_id,
        turn_id=turn_id,
        batch_id=batch_id,
        tool_call_id=tool_call_id,
        normalized_tool_name=normalized_tool_name,
        arguments_hash=arguments_hash,
        tool_spec_hash=_HASH,
        role_policy_id="director",
        role_policy_hash=_HASH,
        canonical_allow_list_hash=_HASH,
        capability_scope=allowed_paths,
        capability_scope_hash=capability_scope_hash,
        job_token_id="job-1",
        job_token_evidence_hash=job_token_evidence_hash,
        execution_envelope_hash=_HASH,
        allowed_command_hash=allowed_command_hash,
        mutation_guard_mode="strict",
        bound_policy_snapshot_hash=snapshot.evidence_hash,
        target_state_hash=snapshot.target_state_hash,
        normalized_operation_hash=snapshot.normalized_operation_hash,
        policy_version=snapshot.policy_version,
        policy_hash=snapshot.policy_hash,
        authorization_hash=authorization_hash,
    )
    classification = DirectorEffectClassificationEvidenceV1(
        raw_tool_name=normalized_tool_name,
        canonical_tool_name=normalized_tool_name,
        effect_type=effect_type,
        execution_mode=execution_mode,
        normalized_arguments=normalized_arguments,
        arguments_hash=arguments_hash,
        tool_spec_hash=_HASH,
        tool_spec_snapshot_hash=_HASH,
        alias_binding_hash=_HASH,
    )
    authorization_binding = DirectorEffectAuthorizationBindingV1(
        authorization_evidence=authorization,
        classification_evidence=classification,
        tool_spec_hash=_HASH,
        tool_spec_snapshot_hash=_HASH,
        alias_binding_hash=_HASH,
    )
    return DirectedEffectLifecycleCandidateV1(
        preflight=DirectorEffectPreflightResultV1(
            status="authorized",
            applicability="mutation_capable",
            intent=intent,
            evidence=authorization,
            error_code=None,
        ),
        snapshot=snapshot,
        authorization_binding=authorization_binding,
    )


class _RecordingPolicyPort:
    def __init__(
        self,
        events: list[tuple[str, object]],
        *,
        fail: bool = False,
        raise_on_bind: bool = False,
        forge_case: str | None = None,
        foreign_candidate: DirectedEffectLifecycleCandidateV1 | None = None,
    ) -> None:
        self._events = events
        self._fail = fail
        self._raise_on_bind = raise_on_bind
        self._forge_case = forge_case
        self._foreign_candidate = foreign_candidate

    async def capture_baseline_snapshot(
        self,
        request: DirectorEffectPolicyBaselineCaptureRequestV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        raise AssertionError(f"Task5 must consume an existing policy snapshot: {request!r}")

    async def snapshot(
        self,
        request: DirectorEffectPolicySnapshotRequestV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        raise AssertionError(f"Task5 must not recapture policy snapshot: {request!r}")

    async def capture_current_policy_evidence(
        self,
        request: DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    ) -> DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
        self._events.append(("capture_current_policy", request))
        public_policy = project_director_effect_public_policy_evidence(request.baseline_authorization_binding)
        restrictions = dict(request.current_job_token_restriction_evidence)
        operation_hash = hashlib.sha256(
            json.dumps(
                request.claim_grant.operation.to_record(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        snapshot = request.bound_snapshot.snapshot
        evidence = DirectorEffectCurrentPolicyEvidenceV1(
            baseline_authorization_binding_hash=request.baseline_authorization_binding.authorization_binding_hash,
            baseline_public_policy_evidence_hash=public_policy.public_policy_evidence_hash,
            bound_member_hash=request.bound_snapshot.member_binding_hash,
            claim_grant_hash=request.claim_grant.grant_hash,
            policy_target_version=snapshot.policy_version,
            policy_target_hash=snapshot.policy_hash,
            operation_version=str(request.claim_grant.operation_version),
            operation_hash=operation_hash,
            capability_scope_version="director-capability-scope.v1",
            capability_scope_hash=str(restrictions["allowed_paths_hash"]),
            job_token_id=str(restrictions["job_token_id"]),
            job_token_version="job-token-restriction.v1",
            job_token_evidence_hash=hash_directed_effect_arguments(request.current_job_token_restriction_evidence),
            tool_spec_snapshot_hash=request.baseline_authorization_binding.tool_spec_snapshot_hash,
            alias_binding_hash=request.baseline_authorization_binding.alias_binding_hash,
            execution_envelope_version="director-execution-envelope.v1",
            execution_envelope_hash=public_policy.execution_envelope_hash,
            allowed_commands_version="director-allowed-commands.v1",
            allowed_commands_hash=str(restrictions["allowed_commands_hash"]),
        )
        return DirectorEffectCurrentPolicyEvidenceCaptureResultV1(
            status="captured",
            evidence=evidence,
            error_code=None,
        )

    def bind_member(
        self,
        request: DirectorEffectPolicyMemberBindingRequestV1,
    ) -> DirectorEffectPolicyMemberBindingResultV1:
        self._events.append(("bind_member", request))
        if self._raise_on_bind:
            raise RuntimeError("policy bind failure")
        if self._fail:
            return DirectorEffectPolicyMemberBindingResultV1(
                status="denied",
                error_code="deo_authorization_binding_drift",
                member=None,
                member_binding_hash=None,
                bound_snapshot=None,
            )
        member_binding_hash = hash_directed_effect_policy_member_binding(
            request.snapshot.evidence_hash,
            request.authorization_evidence.authorization_hash,
            request.authorization_binding.authorization_binding_hash,
            request.member,
        )
        bound_snapshot = DirectorEffectPolicyBoundSnapshotV1(
            snapshot=request.snapshot,
            authorization_evidence_hash=request.authorization_evidence.authorization_hash,
            authorization_binding=request.authorization_binding,
            authorization_binding_hash=request.authorization_binding.authorization_binding_hash,
            member=request.member,
            member_binding_hash=member_binding_hash,
        )
        result = DirectorEffectPolicyMemberBindingResultV1(
            status="allowed",
            error_code=None,
            member=request.member,
            member_binding_hash=member_binding_hash,
            bound_snapshot=bound_snapshot,
            authorization_binding_hash=request.authorization_binding.authorization_binding_hash,
        )
        if self._forge_case is None:
            return result
        assert result.bound_snapshot is not None
        assert self._foreign_candidate is not None
        bound = result.bound_snapshot
        if self._forge_case == "foreign_snapshot":
            bound = _forged(bound, snapshot=self._foreign_candidate.snapshot)
        elif self._forge_case == "foreign_binding":
            bound = _forged(
                bound,
                authorization_binding=self._foreign_candidate.authorization_binding,
            )
        elif self._forge_case == "foreign_hash":
            bound = _forged(bound, authorization_binding_hash="b" * 64)
            return _forged(
                result,
                bound_snapshot=bound,
                authorization_binding_hash="b" * 64,
            )
        else:
            raise AssertionError(f"unknown forge case: {self._forge_case}")
        return _forged(result, bound_snapshot=bound)

    async def revalidate(
        self,
        request: DirectorEffectPolicyRevalidationRequestV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        raise AssertionError(f"Task5 must not revalidate current policy: {request!r}")


@dataclass(slots=True)
class _RecordingRuntime:
    events: list[tuple[str, object]]
    fail_stage: str | None = None
    raise_stage: str | None = None
    malformed_stage: str | None = None
    malformed_kind: str = "none"

    def _record(self, stage: str, command: object) -> None:
        self.events.append((stage, command))
        if self.raise_stage == stage:
            raise RuntimeError(f"{stage} port failure")

    def _malformed(self, stage: str, expected_type: type[object]) -> object | None:
        if self.malformed_stage != stage:
            return None
        if self.malformed_kind == "none":
            return None
        if self.malformed_kind == "wrong_type":
            return object()
        if self.malformed_kind == "uninitialized":
            return object.__new__(expected_type)
        raise AssertionError(f"unknown malformed kind: {self.malformed_kind}")

    def enroll_parent(
        self,
        command: EnrollDirectedEffectParentRegistryStreamCommandV1,
    ) -> DirectedEffectStreamEnrollmentResultV1:
        self._record("enroll_parent", command)
        malformed = self._malformed("enroll_parent", DirectedEffectStreamEnrollmentResultV1)
        if self.malformed_stage == "enroll_parent":
            return cast(DirectedEffectStreamEnrollmentResultV1, malformed)
        if self.fail_stage == "enroll_parent":
            return DirectedEffectStreamEnrollmentResultV1(
                ok=False,
                code="stream_append_failed",
                execution_attempt=command.execution_attempt,
            )
        return enroll_directed_effect_parent_registry_stream(command)

    def admit_parent(
        self,
        command: AdmitDirectedEffectParentBatchCommandV1,
    ) -> DirectedEffectOperationResultV1:
        self._record("admit_parent", command)
        malformed = self._malformed("admit_parent", DirectedEffectOperationResultV1)
        if self.malformed_stage == "admit_parent":
            return cast(DirectedEffectOperationResultV1, malformed)
        if self.fail_stage == "admit_parent":
            return DirectedEffectOperationResultV1(ok=False, code="stream_append_failed")
        return admit_directed_effect_parent_batch(command)

    def enroll_operation(
        self,
        command: EnrollDirectedEffectOperationStreamCommandV1,
    ) -> DirectedEffectStreamEnrollmentResultV1:
        self._record("enroll_operation", command)
        malformed = self._malformed("enroll_operation", DirectedEffectStreamEnrollmentResultV1)
        if self.malformed_stage == "enroll_operation":
            return cast(DirectedEffectStreamEnrollmentResultV1, malformed)
        if self.fail_stage == "enroll_operation":
            return DirectedEffectStreamEnrollmentResultV1(
                ok=False,
                code="stream_append_failed",
                execution_attempt=command.execution_attempt,
            )
        return enroll_directed_effect_operation_stream(command)

    def seal(
        self,
        command: SealDirectedEffectInventoryCommandV1,
    ) -> DirectedEffectInventoryResultV1:
        self._record("seal_inventory", command)
        malformed = self._malformed("seal_inventory", DirectedEffectInventoryResultV1)
        if self.malformed_stage == "seal_inventory":
            return cast(DirectedEffectInventoryResultV1, malformed)
        if self.fail_stage == "seal_inventory":
            return DirectedEffectInventoryResultV1(ok=False, code="stream_append_failed")
        return seal_directed_effect_inventory(command)

    def admit_operation(
        self,
        command: AdmitDirectedEffectOperationCommandV1,
    ) -> DirectedEffectOperationResultV1:
        self._record("admit_operation", command)
        malformed = self._malformed("admit_operation", DirectedEffectOperationResultV1)
        if self.malformed_stage == "admit_operation":
            return cast(DirectedEffectOperationResultV1, malformed)
        if self.fail_stage == "admit_operation":
            return DirectedEffectOperationResultV1(ok=False, code="stream_append_failed")
        return admit_directed_effect_operation(command)

    def finalize(
        self,
        command: FinalizeDirectedEffectInventoryAdmissionCommandV1,
    ) -> DirectedEffectInventoryResultV1:
        self._record("finalize_inventory", command)
        malformed = self._malformed("finalize_inventory", DirectedEffectInventoryResultV1)
        if self.malformed_stage == "finalize_inventory":
            return cast(DirectedEffectInventoryResultV1, malformed)
        if self.fail_stage == "finalize_inventory":
            return DirectedEffectInventoryResultV1(ok=False, code="stream_append_failed")
        return finalize_directed_effect_inventory_admission(command)

    def claim(
        self,
        command: ClaimDirectedEffectCommandV1,
    ) -> DirectedEffectOperationResultV1:
        self._record("claim_operation", command)
        malformed = self._malformed("claim_operation", DirectedEffectOperationResultV1)
        if self.malformed_stage == "claim_operation":
            return cast(DirectedEffectOperationResultV1, malformed)
        if self.fail_stage == "claim_operation":
            return DirectedEffectOperationResultV1(ok=False, code="stream_append_failed")
        return claim_directed_effect(command)

    def get_operation(
        self,
        query: GetDirectedEffectOperationQueryV1,
    ) -> DirectedEffectOperationResultV1:
        self._record("get_operation", query)
        malformed = self._malformed("get_operation", DirectedEffectOperationResultV1)
        if self.malformed_stage == "get_operation":
            return cast(DirectedEffectOperationResultV1, malformed)
        if self.fail_stage == "get_operation":
            return DirectedEffectOperationResultV1(ok=False, code="stream_read_failed")
        return get_directed_effect_operation(query)

    def ports(self) -> DirectedEffectTaskRuntimePortsV1:
        return DirectedEffectTaskRuntimePortsV1(
            enroll_parent_stream=self.enroll_parent,
            admit_parent=self.admit_parent,
            enroll_operation_stream=self.enroll_operation,
            seal_inventory=self.seal,
            admit_operation=self.admit_operation,
            finalize_inventory=self.finalize,
            claim_operation=self.claim,
            get_operation=self.get_operation,
        )


def _service(
    runtime: _RecordingRuntime,
    *,
    policy_failure: bool = False,
    policy_exception: bool = False,
    forge_case: str | None = None,
    foreign_candidate: DirectedEffectLifecycleCandidateV1 | None = None,
) -> DirectedEffectLifecycleService:
    return DirectedEffectLifecycleService(
        policy_snapshot_port=_RecordingPolicyPort(
            runtime.events,
            fail=policy_failure,
            raise_on_bind=policy_exception,
            forge_case=forge_case,
            foreign_candidate=foreign_candidate,
        ),
        task_runtime_ports=runtime.ports(),
    )


def _install_physical_effect_spies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbid(name: str) -> Callable[..., object]:
        def _forbid(*args: object, **kwargs: object) -> object:
            calls.append(name)
            raise AssertionError(f"Task5 attempted physical effect: {name}")

        return _forbid

    for method_name in (
        "write_text",
        "write_text_atomic",
        "append_text",
        "write_bytes",
        "write_json",
        "write_json_atomic",
        "workspace_write_text",
        "workspace_write_text_atomic",
        "workspace_append_text",
        "workspace_write_bytes",
        "workspace_remove",
        "remove",
    ):
        monkeypatch.setattr(KernelFileSystem, method_name, forbid(f"KernelFileSystem.{method_name}"))
    monkeypatch.setattr(AgentAccelToolExecutor, "execute", forbid("AgentAccelToolExecutor.execute"))
    monkeypatch.setattr(CommandExecutionService, "run", forbid("CommandExecutionService.run"))
    for function_name in ("system", "popen"):
        monkeypatch.setattr(os, function_name, forbid(f"os.{function_name}"))
    for function_name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, function_name, forbid(f"subprocess.{function_name}"))
    return calls


def test_exact_lifecycle_order_fields_cas_and_sealed_member_binding(tmp_path: Path) -> None:
    attempt = _setup_attempt(str(tmp_path / "workspace"))
    candidates = tuple(_candidate(attempt, ordinal=index) for index in range(2))
    runtime = _RecordingRuntime(events=[])

    result = _service(runtime).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )

    assert result.status == "ready"
    assert result.prepared_batch is not None
    names = tuple(name for name, _ in runtime.events)
    assert names == (
        "enroll_parent",
        "admit_parent",
        "enroll_operation",
        "seal_inventory",
        "bind_member",
        "bind_member",
        "admit_operation",
        "admit_operation",
        "finalize_inventory",
    )
    commands = {name: command for name, command in runtime.events if name not in {"bind_member", "admit_operation"}}
    parent = commands["admit_parent"]
    assert isinstance(parent, AdmitDirectedEffectParentBatchCommandV1)
    assert parent.actor == "roles.kernel"
    seal = commands["seal_inventory"]
    assert isinstance(seal, SealDirectedEffectInventoryCommandV1)
    assert (seal.expected_registry_version, seal.expected_registry_seq, seal.expected_operation_head_seq) == (1, 2, 0)
    assert seal.intents == tuple(candidate.preflight.intent for candidate in candidates)
    bind_requests = tuple(command for name, command in runtime.events if name == "bind_member")
    admitted_commands = tuple(command for name, command in runtime.events if name == "admit_operation")
    for ordinal, (candidate, request, command, prepared) in enumerate(
        zip(candidates, bind_requests, admitted_commands, result.prepared_batch.prepared_members, strict=True)
    ):
        assert isinstance(request, DirectorEffectPolicyMemberBindingRequestV1)
        assert isinstance(command, AdmitDirectedEffectOperationCommandV1)
        intent = candidate.preflight.intent
        assert intent is not None
        assert request.snapshot is candidate.snapshot
        assert request.authorization_binding is candidate.authorization_binding
        assert request.member.ordinal == ordinal
        assert request.member.tool_call_id == intent.tool_call_id
        assert request.member.intended_effect_fingerprint == candidate.snapshot.normalized_operation_hash
        assert command.tool_call_id == request.member.tool_call_id
        assert command.effect_id == request.member.effect_id
        assert command.expected_version == 0
        assert command.expected_seq == ordinal + 1
        assert prepared.policy_binding.bound_snapshot is not None
        assert prepared.policy_binding.bound_snapshot.member == request.member
        assert prepared.policy_binding.bound_snapshot.authorization_binding == candidate.authorization_binding
    finalize = commands["finalize_inventory"]
    assert isinstance(finalize, FinalizeDirectedEffectInventoryAdmissionCommandV1)
    assert (
        finalize.expected_registry_version,
        finalize.expected_registry_seq,
        finalize.expected_operation_head_seq,
    ) == (2, 3, 2)
    assert all(name != "claim_operation" for name, _ in runtime.events)


def test_lifecycle_admits_second_turn_after_first_batch_receipts_close(tmp_path: Path) -> None:
    attempt = _setup_attempt(str(tmp_path / "workspace"))
    runtime = _RecordingRuntime(events=[])
    service = _service(runtime)
    first = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(_candidate(attempt, ordinal=0),),
    )
    assert first.status == "ready"
    assert first.prepared_batch is not None
    first_batch = first.prepared_batch
    first_member = first_batch.prepared_members[0].member
    claimed = claim_directed_effect(
        ClaimDirectedEffectCommandV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=first_batch.parent_binding,
            tool_call_id=first_member.tool_call_id,
            effect_id=first_member.effect_id,
            expected_version=1,
            expected_seq=2,
            actor="roles.kernel.test",
            intended_effect_fingerprint=first_member.intended_effect_fingerprint,
            policy_verdict_hash=first_member.policy_verdict_hash,
            expected_receipt_binding_hash=first_member.expected_receipt_binding_hash,
        )
    )
    assert claimed.code == "effect_claimed"
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=first_batch.parent_binding,
            tool_call_id=first_member.tool_call_id,
            effect_id=first_member.effect_id,
            expected_version=2,
            expected_seq=3,
            actor="roles.kernel.test",
            intended_effect_fingerprint=first_member.intended_effect_fingerprint,
            policy_verdict_hash=first_member.policy_verdict_hash,
            expected_receipt_binding_hash=first_member.expected_receipt_binding_hash,
            receipt_ref="receipt://roles-kernel/first-batch",
            receipt_hash="b" * 64,
            receipt_binding_hash=first_member.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.code == "receipt_committed"

    second = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-2",
        batch_id="batch-2",
        candidates=(
            _candidate(
                attempt,
                ordinal=0,
                tool_call_id="call-second",
                target_path="src/b.py",
                turn_id="turn-2",
                batch_id="batch-2",
            ),
        ),
    )

    assert second.status == "ready", second
    assert second.prepared_batch is not None
    assert second.prepared_batch.parent_binding.parent_sequence == 2
    assert second.prepared_batch.parent_binding.registry_version == 5
    assert second.prepared_batch.latest_parent_registry_head == 7
    assert second.prepared_batch.prepared_members[0].member.tool_call_id == "call-second"


@pytest.mark.parametrize(
    "failure_stage",
    (
        "enroll_parent",
        "admit_parent",
        "enroll_operation",
        "seal_inventory",
        "member_binding",
        "admit_operation",
        "finalize_inventory",
    ),
)
def test_preparation_failure_has_whole_batch_zero_effect(
    tmp_path: Path,
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / failure_stage
    attempt = _setup_attempt(str(workspace))
    runtime = _RecordingRuntime(
        events=[],
        fail_stage=None if failure_stage == "member_binding" else failure_stage,
    )
    candidates = (_candidate(attempt, ordinal=0), _candidate(attempt, ordinal=1))
    physical_calls = _install_physical_effect_spies(monkeypatch)

    result = _service(
        runtime,
        policy_failure=failure_stage == "member_binding",
    ).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )

    assert result.status == "denied"
    assert result.prepared_batch is None
    assert all(name != "claim_operation" for name, _ in runtime.events)
    assert physical_calls == []


@pytest.mark.parametrize(
    ("raise_stage", "expected_stage"),
    (
        ("enroll_parent", "parent_stream_enrollment"),
        ("admit_parent", "parent_admission"),
        ("enroll_operation", "operation_stream_enrollment"),
        ("seal_inventory", "inventory_seal"),
        ("bind_member", "member_binding"),
        ("admit_operation", "member_admission"),
        ("finalize_inventory", "inventory_ready"),
    ),
)
def test_each_port_exception_is_typed_denial_without_physical_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raise_stage: str,
    expected_stage: str,
) -> None:
    attempt = _setup_attempt(str(tmp_path / raise_stage))
    runtime = _RecordingRuntime(
        events=[],
        raise_stage=None if raise_stage == "bind_member" else raise_stage,
    )
    candidates = (_candidate(attempt, ordinal=0), _candidate(attempt, ordinal=1))
    physical_calls = _install_physical_effect_spies(monkeypatch)

    result = _service(
        runtime,
        policy_exception=raise_stage == "bind_member",
    ).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )

    assert result.status == "denied"
    assert result.prepared_batch is None
    assert ("stage", expected_stage) in result.upstream_evidence
    assert any(
        key == "upstream_code" and str(value).startswith("port_exception:") for key, value in result.upstream_evidence
    )
    assert physical_calls == []


@pytest.mark.parametrize(
    ("malformed_stage", "expected_stage"),
    (
        ("enroll_parent", "parent_stream_enrollment"),
        ("admit_parent", "parent_admission"),
        ("enroll_operation", "operation_stream_enrollment"),
        ("seal_inventory", "inventory_seal"),
        ("admit_operation", "member_admission"),
        ("finalize_inventory", "inventory_ready"),
    ),
)
@pytest.mark.parametrize("malformed_kind", ("none", "wrong_type", "uninitialized"))
def test_each_task_runtime_port_malformed_result_is_typed_denial_without_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_stage: str,
    expected_stage: str,
    malformed_kind: str,
) -> None:
    attempt = _setup_attempt(str(tmp_path / f"{malformed_stage}-{malformed_kind}"))
    runtime = _RecordingRuntime(
        events=[],
        malformed_stage=malformed_stage,
        malformed_kind=malformed_kind,
    )
    candidates = (_candidate(attempt, ordinal=0), _candidate(attempt, ordinal=1))
    physical_calls = _install_physical_effect_spies(monkeypatch)

    result = _service(runtime).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )

    assert result.status == "denied"
    assert result.prepared_batch is None
    assert ("stage", expected_stage) in result.upstream_evidence
    assert any(
        key == "upstream_code" and str(value).startswith("port_exception:") for key, value in result.upstream_evidence
    )
    assert physical_calls == []


@pytest.mark.parametrize("forge_case", ("foreign_snapshot", "foreign_binding", "foreign_hash"))
def test_foreign_policy_binding_is_denied_before_any_operation_admission(
    tmp_path: Path,
    forge_case: str,
) -> None:
    attempt = _setup_attempt(str(tmp_path / forge_case))
    runtime = _RecordingRuntime(events=[])
    candidates = (_candidate(attempt, ordinal=0), _candidate(attempt, ordinal=1))
    foreign_candidate = _candidate(attempt, ordinal=99)

    result = _service(
        runtime,
        forge_case=forge_case,
        foreign_candidate=foreign_candidate,
    ).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )

    assert result.status == "denied"
    assert result.error_code == "deo_authorization_binding_drift"
    names = tuple(name for name, _ in runtime.events)
    assert "admit_operation" not in names
    assert "finalize_inventory" not in names


def test_empty_batch_is_not_applicable_without_any_runtime_or_policy_call(tmp_path: Path) -> None:
    attempt = _setup_attempt(str(tmp_path / "workspace"))
    runtime = _RecordingRuntime(events=[])

    result = _service(runtime).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=_authority(attempt),
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(),
    )

    assert result.status == "not_applicable"
    assert runtime.events == []


@pytest.mark.parametrize("failure_mode", ("callback_exception", "invalid_verdict", "stable_identity_drift"))
def test_refresh_directed_effect_attempt_fails_closed_on_heartbeat_defects(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    attempt = _setup_attempt(str(tmp_path / failure_mode))

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        if failure_mode == "callback_exception":
            raise RuntimeError("heartbeat boom")
        if failure_mode == "invalid_verdict":
            return cast(TaskRuntimeExecutionAttemptHeartbeatVerdictV1, object())
        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=replace(command.identity, role_id="chief_engineer"),
        )

    authority = create_task_runtime_execution_attempt_authority(attempt, heartbeat=heartbeat)
    result = refresh_directed_effect_attempt(
        authority=authority,
        expected_execution_attempt=attempt,
        context_summary="directed_effect_batch_prepare",
    )

    assert result.status == "denied"
    assert result.execution_attempt is None
    assert result.error_code == "deo_execution_attempt_heartbeat_failed"


def test_prepare_heartbeat_denial_precedes_all_lifecycle_ports(tmp_path: Path) -> None:
    attempt = _setup_attempt(str(tmp_path / "prepare-heartbeat-denied"))
    runtime = _RecordingRuntime(events=[])

    def heartbeat(
        _command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        raise RuntimeError("heartbeat unavailable")

    authority = create_task_runtime_execution_attempt_authority(attempt, heartbeat=heartbeat)
    result = _service(runtime).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(_candidate(attempt, ordinal=0),),
    )

    assert result.status == "denied"
    assert result.error_code == "deo_execution_attempt_heartbeat_failed"
    assert runtime.events == []


async def test_claim_heartbeat_denial_precedes_taskruntime_claim(tmp_path: Path) -> None:
    attempt = _setup_attempt(str(tmp_path / "claim-heartbeat-denied"))
    runtime = _RecordingRuntime(events=[])
    heartbeat_commands: list[HeartbeatTaskRuntimeExecutionAttemptCommandV1] = []

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        heartbeat_commands.append(command)
        if len(heartbeat_commands) > 1:
            raise RuntimeError("pre-claim heartbeat unavailable")
        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=command.identity,
        )

    authority = create_task_runtime_execution_attempt_authority(attempt, heartbeat=heartbeat)
    service = _service(runtime)
    prepared = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(_candidate(attempt, ordinal=0),),
    ).prepared_batch
    assert prepared is not None

    result = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )

    assert result.status == "denied"
    assert result.error_code == "deo_execution_attempt_heartbeat_failed"
    assert [command.context_summary for command in heartbeat_commands] == [
        "directed_effect_batch_prepare",
        "directed_effect_pre_claim:call-0",
    ]
    assert all(name != "claim_operation" for name, _command in runtime.events)


async def test_claim_execution_context_captures_current_policy_before_registration(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "claim-success"
    attempt = _setup_attempt(str(workspace))
    runtime = _RecordingRuntime(events=[])
    authority = _authority(attempt)
    service = _service(runtime)
    prepared = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(_candidate(attempt, ordinal=0),),
    ).prepared_batch
    assert prepared is not None
    fence = create_directed_effect_fence_ports()

    claimed = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )

    assert claimed.status == "claimed"
    assert claimed.context is not None
    context = claimed.context
    assert context.creator_pid == os.getpid()
    assert context.bound_snapshot == prepared.prepared_members[0].policy_binding.bound_snapshot
    assert context.current_job_token_restriction_evidence == _job_restriction_evidence()
    claim_commands = [command for name, command in runtime.events if name == "claim_operation"]
    assert len(claim_commands) == 1
    command = claim_commands[0]
    assert isinstance(command, ClaimDirectedEffectCommandV1)
    assert command.expected_version == 1
    assert command.expected_seq == prepared.latest_operation_stream_head + 1
    assert [name for name, _payload in runtime.events][-2:] == [
        "claim_operation",
        "capture_current_policy",
    ]
    assert fence.consume.consume(context).status == "denied"
    assert fence.admin.register(context).status == "registered"
    assert fence.consume.consume(context).status == "consumed"

    replay = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )
    assert replay.status == "denied"
    assert replay.error_code == "deo_claim_failed"


async def test_second_claim_uses_current_operation_stream_head_after_first_receipt(
    tmp_path: Path,
) -> None:
    """A committed receipt advances the shared stream before the next member claim."""

    workspace = tmp_path / "claim-after-receipt"
    attempt = _setup_attempt(str(workspace))
    runtime = _RecordingRuntime(events=[])
    authority = _authority(attempt)
    service = _service(runtime)
    prepared = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(
            _candidate(attempt, ordinal=0),
            _candidate(attempt, ordinal=1),
        ),
    ).prepared_batch
    assert prepared is not None

    first = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )
    assert first.status == "claimed"
    assert first.context is not None
    grant = first.context.claim_grant
    member = prepared.prepared_members[0].member
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=prepared.parent_binding,
            tool_call_id=member.tool_call_id,
            effect_id=member.effect_id,
            expected_version=grant.operation_version,
            expected_seq=grant.operation_source_head_seq + 1,
            actor="roles.kernel.test",
            intended_effect_fingerprint=member.intended_effect_fingerprint,
            policy_verdict_hash=member.policy_verdict_hash,
            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
            receipt_ref="receipt://first",
            receipt_hash="b" * 64,
            receipt_binding_hash=member.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.ok
    assert committed.snapshot is not None

    second = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-1",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )

    assert second.status == "claimed"
    claim_commands = [command for name, command in runtime.events if name == "claim_operation"]
    assert len(claim_commands) == 2
    assert claim_commands[1].expected_seq == committed.snapshot.source_head_seq + 1


async def test_claim_fails_closed_when_current_operation_head_is_unavailable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "claim-head-unavailable"
    attempt = _setup_attempt(str(workspace))
    runtime = _RecordingRuntime(events=[], fail_stage="get_operation")
    authority = _authority(attempt)
    service = _service(runtime)
    prepared = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(_candidate(attempt, ordinal=0),),
    ).prepared_batch
    assert prepared is not None

    result = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )

    assert result.status == "denied"
    assert result.error_code == "deo_claim_failed"
    assert result.operation_claim_status == "not_claimed"
    assert all(name != "claim_operation" for name, _command in runtime.events)


async def test_abort_uses_current_operation_stream_head_after_prior_receipt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "abort-after-receipt"
    attempt = _setup_attempt(str(workspace))
    runtime = _RecordingRuntime(events=[])
    authority = _authority(attempt)
    service = _service(runtime)
    prepared = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(
            _candidate(attempt, ordinal=0),
            _candidate(attempt, ordinal=1),
        ),
    ).prepared_batch
    assert prepared is not None
    first = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )
    assert first.context is not None
    grant = first.context.claim_grant
    first_member = prepared.prepared_members[0].member
    committed = commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=prepared.parent_binding,
            tool_call_id=first_member.tool_call_id,
            effect_id=first_member.effect_id,
            expected_version=grant.operation_version,
            expected_seq=grant.operation_source_head_seq + 1,
            actor="roles.kernel.test",
            intended_effect_fingerprint=first_member.intended_effect_fingerprint,
            policy_verdict_hash=first_member.policy_verdict_hash,
            expected_receipt_binding_hash=first_member.expected_receipt_binding_hash,
            receipt_ref="receipt://first",
            receipt_hash="b" * 64,
            receipt_binding_hash=first_member.expected_receipt_binding_hash,
            receipt_outcome="succeeded",
        )
    )
    assert committed.snapshot is not None

    aborted = service.abort_unclaimed_members(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_ids=("call-1",),
        reason="not_activated",
    )

    assert len(aborted) == 1
    assert aborted[0].state == "ABORTED"
    assert aborted[0].snapshot is not None
    assert aborted[0].snapshot.source_head_seq == committed.snapshot.source_head_seq + 1


@pytest.mark.parametrize("malformed_kind", ("none", "wrong_type", "uninitialized"))
async def test_claim_execution_context_rejects_malformed_task_runtime_result(
    tmp_path: Path,
    malformed_kind: str,
) -> None:
    workspace = tmp_path / f"claim-malformed-{malformed_kind}"
    attempt = _setup_attempt(str(workspace))
    runtime = _RecordingRuntime(events=[])
    authority = _authority(attempt)
    service = _service(runtime)
    prepared = service.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(_candidate(attempt, ordinal=0),),
    ).prepared_batch
    assert prepared is not None
    runtime.malformed_stage = "claim_operation"
    runtime.malformed_kind = malformed_kind
    result = await service.claim_execution_context(
        prepared_batch=prepared,
        execution_attempt_authority=authority,
        tool_call_id="call-0",
        current_job_token_restriction_evidence=_job_restriction_evidence(),
    )

    assert result.status == "denied"
    assert result.error_code == "deo_claim_failed"


def test_lifecycle_module_has_no_physical_execution_surface() -> None:
    module_path = Path(__file__).parents[1] / "internal" / "directed_effect_lifecycle.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden = (
        "DirectorToolExecutor",
        "AgentAccelToolExecutor",
        "execute_mutation",
        "execute_command",
        "subprocess",
        "os.system",
        "write_text",
        "write_bytes",
    )
    assert all(token not in source for token in forbidden)
