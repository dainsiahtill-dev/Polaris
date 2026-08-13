from __future__ import annotations

import ast
import dataclasses
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import pytest
import yaml
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    GenerateTaskBlueprintCommandV1,
    TaskBlueprintResultV1,
)
from polaris.cells.code_intelligence.engine.public.contracts import (
    AstDependencyVerificationResultV1,
    VerifyAstDependencyQueryV1,
)
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionResultV1,
    ExecuteDirectorTaskCommandV1,
)
from polaris.cells.factory.cognitive_runtime.public.contracts import (
    ExportHandoffPackCommandV1,
    HandoffPackResultV1,
    HandoffRehydrationResultV1,
    RecordRuntimeReceiptCommandV1,
    RehydrateHandoffPackCommandV1,
    RuntimeReceiptResultV1,
    ValidateChangeSetCommandV1,
    ValidateChangeSetResultV1,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    VerificationReport,
    VerificationStatus,
    VerifyCompletionCommandV1,
    VerifyCompletionResultV1,
)
from polaris.cells.finops.budget_guard.public.contracts import (
    BudgetDecisionResultV1,
    ReserveBudgetCommandV1,
)
from polaris.cells.llm.control_plane.public.contracts import (
    CheckLlmModelCapabilityQueryV1,
    LlmModelCapabilityResultV1,
)
from polaris.cells.policy.permission.public.contracts import (
    EvaluatePermissionCommandV1,
    PermissionDecisionResultV1,
)
from polaris.cells.policy.workspace_guard.public.contracts import (
    WorkspaceGuardBatchDecisionV1,
    WorkspaceGuardDecisionV1,
    WorkspaceGuardPathDecisionV1,
    WorkspaceWriteGuardBatchQueryV1,
    WorkspaceWriteGuardQueryV1,
)
from polaris.cells.qa.audit_verdict.public.contracts import (
    FailureSignalV1,
    ParseTracebackFramesCommandV1,
    ParseTracebackFramesResultV1,
    QaAuditResultV1,
    RunQaAuditCommandV1,
    RunVisualQaAuditCommandV1,
    TracebackFrameV1,
    VisualQaAuditResultV1,
)
from polaris.cells.roles.runtime.public import (
    capability_commands as runtime_capability_commands,
    contracts as runtime_contracts,
    service as runtime_service,
)
from polaris.cells.roles.runtime.public.contracts import (
    AssembleRoleRuntimeChainCommandV1,
    ExecuteRoleCapabilityInvocationCommandV1,
    ExecuteRoleTaskMarketLifecycleCommandV1,
    InstantiateRoleRuntimeObjectCommandV1,
    RoleAssetMount,
    RoleAssetMountTable,
    RoleAssetRef,
    RoleCapabilityDecision,
    RoleCapabilityDescriptor,
    RoleCapabilityFingerprint,
    RoleCapabilityInvocation,
    RoleCapabilityInvocationResultV1,
    RoleCapabilityPorts,
    RoleIdentity,
    RoleLedgerBinding,
    RoleProfileBinding,
    RoleRuntimeChainAssemblyResultV1,
    RoleRuntimeChainEnvelope,
    RoleRuntimeChainStepRef,
    RoleRuntimeObject,
    RoleRuntimeObjectResultV1,
    RoleStateCommitReceipt,
    RoleStateCommitRequest,
    RoleTaskMarketBinding,
    RoleTaskMarketLifecycleResultV1,
    RoleTurnContext,
    RoleTurnEnvelope,
)
from polaris.cells.roles.runtime.public.service import (
    assemble_role_runtime_chain,
    commit_role_state,
    execute_role_capability_invocation,
    execute_role_task_market_lifecycle,
    instantiate_role_runtime_object,
)
from polaris.cells.runtime.projection.public.contracts import RuntimeProjectionQueryV1, RuntimeProjectionResultV1
from polaris.cells.runtime.task_market.public import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    MoveTaskToDeadLetterCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
    RequeueTaskCommandV1,
    TaskLeaseRenewResultV1,
    TaskMarketStatusResultV1,
    TaskWorkItemResultV1,
)

if TYPE_CHECKING:
    from polaris.domain.cognitive_runtime import (
        ChangeSetValidationResult,
        ContextHandoffPack,
        HandoffRehydration,
        RuntimeReceipt,
    )


# ---------------------------------------------------------------------------
# Typed keyword-argument payloads
#
# Several parametrized rejection tests build a base kwargs mapping, ``.update()``
# it with a parametrized subset of overrides, then unpack it (``**payload``) into
# a frozen contract constructor to assert the constructor's validation. Typing the
# payload mappings as ``total=False`` TypedDicts (instead of ``dict[str, object]``)
# lets mypy verify each key against the matching constructor parameter at the
# unpack boundary while keeping every override field optional.
# ---------------------------------------------------------------------------


class _CapabilityInvocationKwargs(TypedDict, total=False):
    invocation_id: str
    capability_id: str
    role_id: str
    command_contract: str
    payload_ref: str
    fingerprint_ref: str


class _TurnContextKwargs(TypedDict, total=False):
    typed_input_ref: str
    context_snapshot_ref: str
    handoff_refs: tuple[str, ...]
    task_refs: tuple[str, ...]


class _CapabilityInvocationResultKwargs(TypedDict, total=False):
    ok: bool
    invocation_id: str
    role_id: str
    capability_id: str
    command_contract: str
    allowed: bool
    payload_ref: str
    owner_cell: str
    result_ref: str | None
    error_code: str | None
    error_message: str | None


class _StateCommitReceiptKwargs(TypedDict, total=False):
    request_id: str
    ok: bool
    commit_receipt_ref: str | None
    change_set_validation_ref: str | None
    runtime_receipt_refs: tuple[str, ...]
    handoff_pack_refs: tuple[str, ...]
    turn_outcome_ref: str | None


class _LedgerBindingKwargs(TypedDict, total=False):
    turn_ledger_ref: str
    commit_receipt_ref: str | None
    receipt_refs: tuple[str, ...]


class _TaskMarketBindingKwargs(TypedDict, total=False):
    work_item_ref: str | None
    lease_token_ref: str | None


class _ChainStepRefKwargs(TypedDict, total=False):
    role_id: str
    stage: str
    capability_id: str
    capability_fingerprint_ref: str
    owner_cell: str
    command_contract: str
    result_ref: str
    task_ref: str | None
    work_item_ref: str | None
    evidence_refs: tuple[str, ...]
    receipt_refs: tuple[str, ...]
    handoff_refs: tuple[str, ...]


class _ChainEnvelopeKwargs(TypedDict, total=False):
    chain_id: str
    workspace: str
    run_id: str
    task_id: str
    steps: tuple[RoleRuntimeChainStepRef, ...]
    turn_ledger_ref: str
    task_market_refs: tuple[str, ...]
    audit_evidence_refs: tuple[str, ...]
    runtime_projection_refs: tuple[str, ...]
    capability_fingerprint_refs: tuple[str, ...]
    handoff_refs: tuple[str, ...]
    runtime_receipt_refs: tuple[str, ...]


# Aggregate step-derived ref kwargs unpacked alongside the explicit envelope
# arguments; the parametrized ``missing_field`` selects which one to blank out.
_ChainEnvelopeAggregateField = Literal[
    "task_market_refs",
    "audit_evidence_refs",
    "capability_fingerprint_refs",
    "handoff_refs",
    "runtime_receipt_refs",
]


class _ChainEnvelopeAggregateRefs(TypedDict, total=False):
    task_market_refs: tuple[str, ...]
    audit_evidence_refs: tuple[str, ...]
    capability_fingerprint_refs: tuple[str, ...]
    handoff_refs: tuple[str, ...]
    runtime_receipt_refs: tuple[str, ...]


class FakeTaskMarketService:
    def __init__(self) -> None:
        self.published: list[PublishTaskWorkItemCommandV1] = []
        self.queried: list[QueryTaskMarketStatusV1] = []
        self.claimed: list[ClaimTaskWorkItemCommandV1] = []
        self.renewed: list[RenewTaskLeaseCommandV1] = []
        self.acked: list[AcknowledgeTaskStageCommandV1] = []
        self.failed: list[FailTaskStageCommandV1] = []
        self.requeued: list[RequeueTaskCommandV1] = []
        self.dead_lettered: list[MoveTaskToDeadLetterCommandV1] = []

    def publish_work_item(self, command: object) -> TaskWorkItemResultV1:
        assert isinstance(command, PublishTaskWorkItemCommandV1)
        self.published.append(command)
        return TaskWorkItemResultV1(
            ok=True,
            task_id=command.task_id,
            stage=command.stage,
            status="pending",
            version=1,
            trace_id=command.trace_id,
            run_id=command.run_id,
            payload=command.payload,
        )

    def query_status(self, query: object) -> TaskMarketStatusResultV1:
        assert isinstance(query, QueryTaskMarketStatusV1)
        self.queried.append(query)
        return TaskMarketStatusResultV1(
            workspace=query.workspace,
            total=3,
            counts={"pending_exec": 1, "running": 1, "dead_letter": 1},
            items=(
                {
                    "task_id": "task-a",
                    "stage": "pending_exec",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ("task-b",),
                    "projection_ref": "runtime.projection:task:task-a",
                },
                {"task_id": "task-b", "stage": "pending_qa", "status": "running", "priority": "medium"},
                {
                    "task_id": "task-c",
                    "stage": "pending_exec",
                    "status": "dead_letter",
                    "priority": "high",
                    "failed_stage": "pending_exec",
                    "failure_reason": "lease expired",
                    "depends_on": ("task-a", "task-b"),
                },
            ),
        )

    def claim_work_item(self, command: ClaimTaskWorkItemCommandV1) -> TaskWorkItemResultV1:
        self.claimed.append(command)
        return TaskWorkItemResultV1(
            ok=True,
            task_id=command.task_id or "task-claimed",
            stage=command.stage,
            status="leased",
            version=2,
            trace_id=command.trace_id or "",
            lease_token="lease-1",
            claimed_by=command.worker_id,
        )

    def renew_task_lease(self, command: RenewTaskLeaseCommandV1) -> TaskLeaseRenewResultV1:
        self.renewed.append(command)
        return TaskLeaseRenewResultV1(
            ok=True,
            task_id=command.task_id,
            lease_token=command.lease_token,
            lease_expires_at="2026-06-07T00:15:00Z",
            version=3,
        )

    def acknowledge_task_stage(self, command: AcknowledgeTaskStageCommandV1) -> TaskWorkItemResultV1:
        self.acked.append(command)
        return TaskWorkItemResultV1(
            ok=True,
            task_id=command.task_id,
            stage=command.next_stage or "terminal",
            status=command.terminal_status or "pending",
            version=4,
            lease_token=command.lease_token,
        )

    def fail_task_stage(self, command: FailTaskStageCommandV1) -> TaskWorkItemResultV1:
        self.failed.append(command)
        return TaskWorkItemResultV1(
            ok=True,
            task_id=command.task_id,
            stage=command.requeue_stage or "dead_letter",
            status="failed",
            version=5,
            lease_token=command.lease_token,
            reason=command.error_message,
        )

    def requeue_task(self, command: RequeueTaskCommandV1) -> TaskWorkItemResultV1:
        self.requeued.append(command)
        return TaskWorkItemResultV1(
            ok=True,
            task_id=command.task_id,
            stage=command.target_stage,
            status="pending",
            version=6,
            reason=command.reason,
        )

    def move_task_to_dead_letter(self, command: MoveTaskToDeadLetterCommandV1) -> TaskWorkItemResultV1:
        self.dead_lettered.append(command)
        return TaskWorkItemResultV1(
            ok=True,
            task_id=command.task_id,
            stage="dead_letter",
            status="dead_letter",
            version=7,
            reason=command.reason,
        )


class FakeRuntimeProjectionService:
    def __init__(self) -> None:
        self.queries: list[RuntimeProjectionQueryV1] = []

    def query_runtime_projection(self, query: object) -> RuntimeProjectionResultV1:
        assert isinstance(query, RuntimeProjectionQueryV1)
        self.queries.append(query)
        return RuntimeProjectionResultV1(
            payload={
                "scope": query.scope,
                "running": False,
                "completed_task_count": 4,
                "last_director_status": "passed",
            }
        )


class FakeRoleProfileService:
    def __init__(self) -> None:
        self.queries: list[object] = []

    def get_profile(self, query: object) -> object:
        from polaris.cells.roles.profile.public.contracts import RoleProfileResultV1

        self.queries.append(query)
        return RoleProfileResultV1(
            ok=True,
            role_id="pm",
            payload={
                "role_id": "pm",
                "prompt_policy": {"core_template_id": "pm"},
                "tool_policy": {"whitelist": ["task_market.publish"]},
                "data_policy": {"data_subdir": "pm"},
                "version": "1.0.0",
                "profile_fingerprint": "pm-profile-fp",
            },
        )


class FakeCognitiveRuntimeCommitService:
    def __init__(self, *, validation_ok: bool = True) -> None:
        self.validation_ok = validation_ok
        self.validations: list[ValidateChangeSetCommandV1] = []
        self.receipts: list[RecordRuntimeReceiptCommandV1] = []
        self.handoffs: list[ExportHandoffPackCommandV1] = []
        self.rehydrations: list[RehydrateHandoffPackCommandV1] = []
        self.rehydrate_refs_are_raw = False

    def validate_change_set(self, command: ValidateChangeSetCommandV1) -> ValidateChangeSetResultV1:
        self.validations.append(command)
        return ValidateChangeSetResultV1(
            ok=self.validation_ok,
            validation=cast(
                "ChangeSetValidationResult",
                SimpleNamespace(validation_id="validation-1", ok=self.validation_ok),
            ),
            error_code=None if self.validation_ok else "validate_change_set_failed",
            error_message=None if self.validation_ok else "change set is outside role scope",
        )

    def record_runtime_receipt(self, command: RecordRuntimeReceiptCommandV1) -> RuntimeReceiptResultV1:
        self.receipts.append(command)
        return RuntimeReceiptResultV1(
            ok=True,
            receipt=cast("RuntimeReceipt", SimpleNamespace(receipt_id="receipt-1")),
        )

    def export_handoff_pack(self, command: ExportHandoffPackCommandV1) -> HandoffPackResultV1:
        self.handoffs.append(command)
        return HandoffPackResultV1(
            ok=True,
            handoff=cast("ContextHandoffPack", SimpleNamespace(handoff_id="handoff-1")),
        )

    def rehydrate_handoff_pack(self, command: RehydrateHandoffPackCommandV1) -> HandoffRehydrationResultV1:
        self.rehydrations.append(command)
        receipt_ref = (
            "receipt-handoff-1" if self.rehydrate_refs_are_raw else "factory.cognitive_runtime:receipt:handoff-1"
        )
        artifact_ref = "artifact-handoff-1" if self.rehydrate_refs_are_raw else "roles.session:artifact:handoff-1"
        episode_ref = "episode-handoff-1" if self.rehydrate_refs_are_raw else "roles.session:episode:handoff-1"
        return HandoffRehydrationResultV1(
            ok=True,
            rehydration=cast(
                "HandoffRehydration",
                SimpleNamespace(
                    rehydration_id="rehydration-1",
                    handoff_id=command.handoff_id,
                    target_role=command.target_role,
                    target_session_id=command.target_session_id,
                    context_override={"state_first_context_os": {"mode": "state_first_context_os.handoff_rehydrate"}},
                    metadata_patch={"handoff_rehydrated": True},
                    receipt_refs=(receipt_ref,),
                    artifact_refs=(artifact_ref,),
                    episode_refs=(episode_ref,),
                    source_spans=("ep-1:t1:t4",),
                ),
            ),
        )


class FakeBlueprintService:
    def __init__(self) -> None:
        self.generated: list[GenerateTaskBlueprintCommandV1] = []

    def generate_task_blueprint(self, command: object) -> TaskBlueprintResultV1:
        assert isinstance(command, GenerateTaskBlueprintCommandV1)
        self.generated.append(command)
        return TaskBlueprintResultV1(
            ok=True,
            task_id=command.task_id,
            workspace=command.workspace,
            status="generated",
            blueprint_id="bp-1",
            blueprint_path="runtime/blueprints/bp-1.json",
            summary=f"Blueprint for {command.task_id}",
            recommendations=("keep public contracts typed",),
            risks=("contract adapter drift",),
        )


class FakeCodeIntelligenceService:
    def __init__(self) -> None:
        self.verified: list[VerifyAstDependencyQueryV1] = []

    def verify_ast_dependency(self, query: object) -> AstDependencyVerificationResultV1:
        assert isinstance(query, VerifyAstDependencyQueryV1)
        self.verified.append(query)
        return AstDependencyVerificationResultV1(
            ok=True,
            workspace=query.workspace,
            path=query.path,
            language=query.language,
            symbol=query.symbol,
            engine="regex",
            results=({"file": query.path, "line": 1, "name": query.symbol, "node_type": "function_definition"},),
        )


class FakeDirectorExecutionService:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.executed: list[ExecuteDirectorTaskCommandV1] = []

    def execute_director_task(self, command: object) -> DirectorExecutionResultV1:
        assert isinstance(command, ExecuteDirectorTaskCommandV1)
        self.executed.append(command)
        return DirectorExecutionResultV1(
            ok=self.ok,
            task_id=command.task_id,
            workspace=command.workspace,
            status="completed" if self.ok else "failed",
            run_id=command.run_id,
            evidence_paths=("runtime/evidence/director-task-1.jsonl",),
            output_summary="director applied approved diff" if self.ok else "",
            error_code=None if self.ok else "director_task_failed",
            error_message=None if self.ok else "patch failed",
        )


class FakeVerificationGuardService:
    def __init__(self) -> None:
        self.verified: list[VerifyCompletionCommandV1] = []

    def verify_completion(self, command: object) -> VerifyCompletionResultV1:
        assert isinstance(command, VerifyCompletionCommandV1)
        self.verified.append(command)
        return VerifyCompletionResultV1(
            ok=True,
            report=VerificationReport(
                claim_id=command.claim.claim_id,
                status=VerificationStatus.PASS,
                execution_summary="pytest passed",
            ),
        )


class FakeLlmControlPlaneService:
    def __init__(self, *, supported: bool) -> None:
        self.supported = supported
        self.queried: list[CheckLlmModelCapabilityQueryV1] = []

    def check_model_capability(self, query: object) -> LlmModelCapabilityResultV1:
        assert isinstance(query, CheckLlmModelCapabilityQueryV1)
        self.queried.append(query)
        return LlmModelCapabilityResultV1(
            ok=True,
            workspace=query.workspace,
            role=query.role,
            provider_id="vision-provider" if self.supported else "text-provider",
            model="vision-model" if self.supported else "text-model",
            capability=query.capability,
            supported=self.supported,
            capability_ref="llm.control_plane:model-capability:qa:image_input:abc" if self.supported else "",
            reason="" if self.supported else "model does not declare image_input support",
        )


class FakeBudgetGuardService:
    def __init__(self, *, allowed: bool = True, reason: str = "reserved") -> None:
        self.allowed = allowed
        self.reason = reason
        self.reserved: list[ReserveBudgetCommandV1] = []

    def reserve_budget(self, command: object) -> BudgetDecisionResultV1:
        assert isinstance(command, ReserveBudgetCommandV1)
        self.reserved.append(command)
        return BudgetDecisionResultV1(
            allowed=self.allowed,
            scope_id=command.scope_id,
            role=command.role,
            remaining_tokens=command.token_budget if self.allowed else 0,
            estimated_cost_usd=0.0,
            reason=self.reason,
        )


class FakeWorkspaceGuardService:
    def __init__(self, *, allowed: bool, single_checks_allowed: bool = True) -> None:
        self.allowed = allowed
        self.single_checks_allowed = single_checks_allowed
        self.checked: list[WorkspaceWriteGuardQueryV1] = []
        self.batch_checked: list[WorkspaceWriteGuardBatchQueryV1] = []

    def check_workspace_write_guard(self, query: object) -> WorkspaceGuardDecisionV1:
        assert isinstance(query, WorkspaceWriteGuardQueryV1)
        if not self.single_checks_allowed:
            raise AssertionError("single-path workspace guard calls are not allowed for this test")
        self.checked.append(query)
        return WorkspaceGuardDecisionV1(
            allowed=self.allowed,
            reason="allowed" if self.allowed else "outside declared mutation boundary",
        )

    def check_workspace_write_guard_batch(
        self,
        query: object,
    ) -> WorkspaceGuardBatchDecisionV1:
        assert isinstance(query, WorkspaceWriteGuardBatchQueryV1)
        self.batch_checked.append(query)
        checked_paths = tuple(dict.fromkeys(query.paths))
        denied_path = "" if self.allowed or not checked_paths else checked_paths[0]
        path_decisions = tuple(
            WorkspaceGuardPathDecisionV1(
                path=path,
                operation=query.operation,
                allowed=self.allowed,
                reason="allowed" if self.allowed else "outside declared mutation boundary",
            )
            for path in checked_paths
        )
        return WorkspaceGuardBatchDecisionV1(
            allowed=self.allowed,
            reason="allowed" if self.allowed else "outside declared mutation boundary",
            checked_paths=checked_paths,
            denied_path=denied_path,
            path_decisions=path_decisions,
        )


class FakePermissionService:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.evaluated: list[EvaluatePermissionCommandV1] = []

    def evaluate_permission(self, command: object) -> PermissionDecisionResultV1:
        assert isinstance(command, EvaluatePermissionCommandV1)
        self.evaluated.append(command)
        return PermissionDecisionResultV1(
            allowed=self.allowed,
            role=command.role,
            action=command.action,
            resource=command.resource,
            reason="allowed by fake policy" if self.allowed else "denied by fake policy",
            matched_policy="fake.allow" if self.allowed else "fake.deny",
            context={"decision_source": "fake"},
        )


@dataclasses.dataclass(frozen=True)
class _FakeArchitectDesignCommand:
    """Local structural stand-in for ``GenerateArchitectureDesignCommandV1``.

    Keeps this ``roles.runtime`` test free of an ``architect.design`` import (the
    ``declared_cell_dependencies_match_imports`` gate counts test imports too, so a
    real cross-cell import would re-declare the roles.runtime -> architect.design
    edge that CYCLE-15 removed), while still recording exactly what the boundary
    handler projected onto the typed ``ArchitectDesignPort`` seam.
    """

    workspace: str
    objective: str
    constraints: dict[str, object]
    context: dict[str, object]


@dataclasses.dataclass(frozen=True)
class _FakeArchitectDesignResult:
    """Local structural stand-in for ``ArchitectureDesignResultV1``.

    The boundary handler reads the design result structurally (via its private
    ``_ArchitectureDesignResultLike`` protocol over the opaque port return), so a
    local double with the same members suffices and avoids the cross-cell import.
    """

    ok: bool
    workspace: str
    design_id: str
    status: str
    summary: str = ""
    recommendation_paths: tuple[str, ...] = ()


class FakeArchitectDesignService:
    """Boundary-design invoker fake.

    Mirrors the real ``architect.design`` provider supplied through the typed
    ``ArchitectDesignPort`` seam: it owns the boundary-design command construction
    (the boundary handler now passes only validated primitives) and records each
    built command so callers can assert the handler's projection.
    """

    def __init__(self) -> None:
        self.generated: list[_FakeArchitectDesignCommand] = []

    def run_boundary_design(
        self,
        *,
        workspace: str,
        objective: str,
        constraints: Mapping[str, object],
        context: Mapping[str, object],
    ) -> _FakeArchitectDesignResult:
        command = _FakeArchitectDesignCommand(
            workspace=workspace,
            objective=objective,
            constraints=dict(constraints),
            context=dict(context),
        )
        self.generated.append(command)
        return _FakeArchitectDesignResult(
            ok=True,
            workspace=command.workspace,
            design_id="design-boundary-1",
            status="completed",
            summary=f"Boundary validation for {command.context.get('target_cell', '')}",
            recommendation_paths=("runtime/state/architect/design-boundary-1.json",),
        )


class FakeQaAuditVerdictService:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.audit_commands: list[RunQaAuditCommandV1] = []
        self.visual_audit_commands: list[RunVisualQaAuditCommandV1] = []
        self.traceback_commands: list[ParseTracebackFramesCommandV1] = []

    def run_qa_audit(self, command: object) -> QaAuditResultV1:
        assert isinstance(command, RunQaAuditCommandV1)
        self.audit_commands.append(command)
        return QaAuditResultV1(
            ok=self.ok,
            task_id=command.task_id,
            workspace=command.workspace,
            verdict="PASS" if self.ok else "FAIL",
            score=1.0 if self.ok else 0.0,
            findings=() if self.ok else ("syntax error",),
            suggestions=("rerun failing test",) if not self.ok else (),
        )

    def run_visual_qa_audit(self, command: object) -> VisualQaAuditResultV1:
        assert isinstance(command, RunVisualQaAuditCommandV1)
        self.visual_audit_commands.append(command)
        return VisualQaAuditResultV1(
            ok=self.ok,
            task_id=command.task_id,
            workspace=command.workspace,
            verdict="VISUAL_AUDIT_RECORDED" if self.ok else "FAIL",
            image_refs=command.image_refs,
            model_capability_ref=command.model_capability_ref,
            score=1.0 if self.ok else 0.0,
            evidence_refs=command.evidence_paths,
        )

    def parse_traceback_frames(self, command: object) -> ParseTracebackFramesResultV1:
        assert isinstance(command, ParseTracebackFramesCommandV1)
        self.traceback_commands.append(command)
        signal = FailureSignalV1(
            signal_id="signal-1",
            task_id=command.task_id,
            workspace=command.workspace,
            signal_type="ValueError",
            summary="ValueError: boom",
            frames=(
                TracebackFrameV1(
                    path="/repo/app.py",
                    line=10,
                    function="handle",
                    code="return explode()",
                ),
            ),
        )
        return ParseTracebackFramesResultV1(
            ok=True,
            task_id=command.task_id,
            workspace=command.workspace,
            signal=signal,
        )


class SlowArchitectDesignService:
    def run_boundary_design(
        self,
        *,
        workspace: str,
        objective: str,
        constraints: Mapping[str, object],
        context: Mapping[str, object],
    ) -> _FakeArchitectDesignResult:
        del objective, constraints, context
        time.sleep(0.25)
        return _FakeArchitectDesignResult(
            ok=True,
            workspace=workspace,
            design_id="slow-design",
            status="completed",
        )


def _identity(role_id: str = "pm") -> RoleIdentity:
    return RoleIdentity(
        role_id=role_id,
        run_id="run-1",
        task_id="task-1",
        session_id="session-1",
        workspace="/repo",
        host_kind="headless",
    )


def _profile_binding(role_id: str = "pm") -> RoleProfileBinding:
    return RoleProfileBinding(
        role_id=role_id,
        profile_ref=f"roles.profile:{role_id}",
        tool_policy_ref=f"roles.profile:{role_id}:tool_policy",
        prompt_policy_ref=f"roles.profile:{role_id}:prompt_policy",
        data_policy_ref=f"roles.profile:{role_id}:data_policy",
        profile_fingerprint="profile-fp",
    )






def test_profile_binding_rejects_non_profile_owner_cell() -> None:
    with pytest.raises(ValueError, match=r"profile binding owner_cell must be roles\.profile"):
        RoleProfileBinding(
            role_id="pm",
            profile_ref="roles.profile:pm",
            tool_policy_ref="roles.profile:pm:tool_policy",
            prompt_policy_ref="roles.profile:pm:prompt_policy",
            data_policy_ref="roles.profile:pm:data_policy",
            profile_fingerprint="profile-fp",
            owner_cell="roles.runtime",
        )


@pytest.mark.parametrize(
    "ref_overrides",
    (
        {"profile_ref": "roles.runtime:pm"},
        {"tool_policy_ref": "qa.audit_verdict:pm:tool_policy"},
        {"prompt_policy_ref": "roles.session:pm:prompt_policy"},
        {"data_policy_ref": "kernelone.roles:pm:data_policy"},
    ),
)
def test_profile_binding_rejects_refs_outside_roles_profile(ref_overrides: dict[str, str]) -> None:
    payload = {
        "role_id": "pm",
        "profile_ref": "roles.profile:pm",
        "tool_policy_ref": "roles.profile:pm:tool_policy",
        "prompt_policy_ref": "roles.profile:pm:prompt_policy",
        "data_policy_ref": "roles.profile:pm:data_policy",
        "profile_fingerprint": "profile-fp",
    }
    payload.update(ref_overrides)

    with pytest.raises(ValueError, match=r"profile binding refs must point to roles\.profile"):
        RoleProfileBinding(**payload)


def test_role_runtime_object_mounts_assets_and_ports_by_public_contract_refs() -> None:
    fingerprint = RoleCapabilityFingerprint(
        role_id="pm",
        capability_id="dispatch_task_to_market",
        effect="task_market.publish",
        tool="polaris.cells.runtime.task_market.public.service.publish_work_item",
        policy_fingerprint="policy-fp",
        profile_fingerprint="profile-fp",
    )
    turn_context = RoleTurnContext(
        typed_input_ref="roles.runtime:typed-input:pm-run-1",
        context_snapshot_ref="roles.session:context-snapshot:session-1",
        handoff_refs=("factory.cognitive_runtime:handoff:run-1",),
        task_refs=("runtime.task_market:task:task-1",),
    )

    runtime_object = RoleRuntimeObject(
        identity=_identity(),
        profile_binding=_profile_binding(),
        turn_context=turn_context,
        asset_mounts=_pm_mount_table(),
        capability_ports=_capability_ports(),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_contract="CommitReceipt",
            runtime_receipt_contract="RecordRuntimeReceiptCommandV1",
            receipt_refs=("factory.cognitive_runtime:receipt-1",),
        ),
        task_market_binding=RoleTaskMarketBinding(),
        capability_fingerprint=fingerprint,
    )

    assert runtime_object.identity.role_id == "pm"
    assert runtime_object.turn_context is turn_context
    assert runtime_object.turn_context.task_refs == ("runtime.task_market:task:task-1",)
    assert runtime_object.asset_mounts.get("TaskGraph").asset_ref.owner_cell == "runtime.task_market"
    assert (
        runtime_object.capability_ports.get("dispatch_task_to_market").contract_name == "PublishTaskWorkItemCommandV1"
    )
    assert runtime_object.task_market_binding.publish_contract == "PublishTaskWorkItemCommandV1"
    assert runtime_object.ledger_binding.runtime_receipt_contract == "RecordRuntimeReceiptCommandV1"
    assert runtime_object.capability_fingerprint.fingerprint == fingerprint.fingerprint
    assert dataclasses.is_dataclass(runtime_object)


@pytest.mark.parametrize(
    ("fingerprint", "match"),
    (
        (
            RoleCapabilityFingerprint(
                role_id="pm",
                capability_id="missing_capability",
                effect="task_market.publish",
                tool="polaris.cells.runtime.task_market.public.service.publish_work_item",
                policy_fingerprint="policy-fp",
                profile_fingerprint="profile-fp",
            ),
            "capability_fingerprint.capability_id must be mounted",
        ),
        (
            RoleCapabilityFingerprint(
                role_id="pm",
                capability_id="dispatch_task_to_market",
                effect="task_market.requeue",
                tool="polaris.cells.runtime.task_market.public.service.publish_work_item",
                policy_fingerprint="policy-fp",
                profile_fingerprint="profile-fp",
            ),
            "capability_fingerprint.effect must match mounted capability effect",
        ),
        (
            RoleCapabilityFingerprint(
                role_id="pm",
                capability_id="dispatch_task_to_market",
                effect="task_market.publish",
                tool="runtime.task_market:wrong-tool",
                policy_fingerprint="policy-fp",
                profile_fingerprint="profile-fp",
            ),
            "capability_fingerprint.tool must match mounted capability tool",
        ),
    ),
)
def test_role_runtime_object_rejects_fingerprint_that_does_not_match_mounted_port(
    fingerprint: RoleCapabilityFingerprint,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        RoleRuntimeObject(
            identity=_identity(),
            profile_binding=_profile_binding(),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:pm-run-1",
                context_snapshot_ref="roles.session:context-snapshot:session-1",
                task_refs=("runtime.task_market:task:task-1",),
            ),
            asset_mounts=_pm_mount_table(),
            capability_ports=_capability_ports(),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(),
            capability_fingerprint=fingerprint,
        )


def test_role_runtime_object_rejects_mounted_capability_ports_that_exclude_identity_role() -> None:
    with pytest.raises(
        ValueError,
        match=r"capability 'invoke_container_pytest' is not allowed for role 'pm'",
    ):
        RoleRuntimeObject(
            identity=_identity("pm"),
            profile_binding=_profile_binding("pm"),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:pm-run-1",
                context_snapshot_ref="roles.session:context-snapshot:session-1",
                task_refs=("runtime.task_market:task:task-1",),
            ),
            asset_mounts=RoleAssetMountTable(),
            capability_ports=_mixed_role_capability_ports(),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(),
            capability_fingerprint=RoleCapabilityFingerprint(
                role_id="pm",
                capability_id="dispatch_task_to_market",
                effect="task_market.publish",
                tool="runtime.task_market:PublishTaskWorkItemCommandV1",
                policy_fingerprint="policy-fp",
                profile_fingerprint="profile-fp",
            ),
        )


def test_runtime_spec_instantiation_derives_refs_only_turn_context() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")

    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )

    assert isinstance(runtime_object.turn_context, RoleTurnContext)
    assert runtime_object.turn_context.typed_input_ref == "roles.runtime:typed-input:pm:run-1:task-1"
    assert runtime_object.turn_context.context_snapshot_ref == "roles.session:context-snapshot:session-1"
    assert runtime_object.turn_context.task_refs == ("runtime.task_market:task:task-1",)
    assert runtime_object.turn_context.metadata["source"] == "roles.runtime.spec.instantiate"


def test_runtime_spec_instantiation_accepts_active_task_market_binding() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    task_market_binding = RoleTaskMarketBinding(
        work_item_ref="runtime.task_market:task:task-1",
        lease_token_ref="runtime.task_market:lease:lease-1",
    )

    runtime_object = spec.instantiate(
        identity=RoleIdentity(
            role_id="chief_engineer",
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            workspace="/repo",
            host_kind="task_market_worker",
        ),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
        task_market_binding=task_market_binding,
    )

    assert runtime_object.task_market_binding is task_market_binding
    assert runtime_object.task_market_binding.work_item_ref == "runtime.task_market:task:task-1"
    assert runtime_object.task_market_binding.lease_token_ref == "runtime.task_market:lease:lease-1"


def test_runtime_object_rejects_active_task_market_binding_outside_turn_context() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    task_market_binding = RoleTaskMarketBinding(
        work_item_ref="runtime.task_market:task:task-other",
        lease_token_ref="runtime.task_market:lease:lease-1",
    )

    with pytest.raises(
        ValueError,
        match=r"task_market_binding\.work_item_ref must be listed in turn_context\.task_refs",
    ):
        spec.instantiate(
            identity=RoleIdentity(
                role_id="chief_engineer",
                run_id="run-1",
                task_id="task-1",
                session_id="session-1",
                workspace="/repo",
                host_kind="task_market_worker",
            ),
            profile_binding=_profile_binding("chief_engineer"),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
            policy_fingerprint="ce-policy",
            task_market_binding=task_market_binding,
        )


def test_builtin_pm_runtime_spec_mounts_pm_assets_and_task_market_capabilities() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")

    assert isinstance(spec, runtime_contracts.RoleRuntimeObjectSpec)
    assert spec.role_id == "pm"
    assert spec.asset_mounts.get("ProjectFunctionIndex").asset_ref.owner_cell == "context.catalog"
    assert spec.asset_mounts.get("TaskGraph").asset_ref.owner_cell == "runtime.task_market"
    assert spec.asset_mounts.get("RuntimeProjectionState").asset_ref.owner_cell == "runtime.projection"
    assert spec.asset_mounts.get("OpenLoopRegistry").asset_ref.metadata["evidence_owner_cell"] == "audit.evidence"

    dispatch = spec.capability_ports.get("dispatch_task_to_market")
    assert dispatch.owner_cell == "runtime.task_market"
    assert dispatch.contract_name == "PublishTaskWorkItemCommandV1"
    assert dispatch.allowed_roles == ("pm",)
    assert spec.capability_ports.get("evaluate_critical_path").contract_name == "QueryTaskMarketStatusV1"
    assert spec.capability_ports.get("project_runtime_status").owner_cell == "runtime.projection"

    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )

    assert runtime_object.identity.role_id == "pm"
    assert runtime_object.capability_fingerprint.capability_id == "dispatch_task_to_market"
    assert runtime_object.task_market_binding.publish_contract == "PublishTaskWorkItemCommandV1"


def test_runtime_instantiation_binds_profile_via_roles_profile_public_contract() -> None:
    profile_service = FakeRoleProfileService()

    result = instantiate_role_runtime_object(
        InstantiateRoleRuntimeObjectCommandV1(
            role_id="pm",
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            workspace="/workspace",
            host_kind="task_market_worker",
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            policy_fingerprint="pm-policy-fp",
            capability_id="evaluate_critical_path",
        ),
        profile_service=profile_service,
    )

    assert isinstance(result, RoleRuntimeObjectResultV1)
    assert result.ok is True
    assert result.runtime_object is not None
    assert result.runtime_object.identity.role_id == "pm"
    assert result.runtime_object.identity.run_id == "run-1"
    assert result.runtime_object.profile_binding.owner_cell == "roles.profile"
    assert result.runtime_object.profile_binding.profile_ref == "roles.profile:pm:profile:pm-profile-fp"
    assert result.runtime_object.profile_binding.tool_policy_ref == "roles.profile:pm:tool_policy:pm-profile-fp"
    assert result.runtime_object.profile_binding.prompt_policy_ref == "roles.profile:pm:prompt_policy:pm-profile-fp"
    assert result.runtime_object.profile_binding.data_policy_ref == "roles.profile:pm:data_policy:pm-profile-fp"
    assert result.runtime_object.capability_fingerprint.capability_id == "evaluate_critical_path"
    assert result.runtime_object.capability_fingerprint.profile_fingerprint == "pm-profile-fp"
    assert result.runtime_object.metadata["profile_ref"] == "roles.profile:pm:profile:pm-profile-fp"
    assert len(profile_service.queries) == 1


def test_runtime_instantiation_result_exposes_refs_only_audit_index() -> None:
    profile_service = FakeRoleProfileService()

    result = instantiate_role_runtime_object(
        InstantiateRoleRuntimeObjectCommandV1(
            role_id="chief_engineer",
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            workspace="/workspace",
            host_kind="task_market_worker",
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            policy_fingerprint="ce-policy-fp",
            capability_id="generate_diff_specification",
        ),
        profile_service=profile_service,
    )

    assert result.ok is True
    assert result.runtime_object is not None
    metadata = dict(result.metadata)

    assert metadata["turn_ledger_ref"] == "roles.kernel:turn-ledger:run-1"
    assert metadata["capability_fingerprint_ref"] == (
        f"roles.runtime:capability-fingerprint:{result.runtime_object.capability_fingerprint.fingerprint}"
    )
    assert metadata["asset_refs"] == (
        "chief_engineer.blueprint:runtime/blueprints",
        "chief_engineer.blueprint:arch-constraint-memo",
        "chief_engineer.blueprint:diff-map-archive",
    )
    assert metadata["asset_owner_cells"] == ("chief_engineer.blueprint",)
    assert "chief_engineer.blueprint:GenerateTaskBlueprintCommandV1" in metadata["capability_refs"]
    assert "runtime.task_market:ClaimTaskWorkItemCommandV1" in metadata["capability_refs"]
    assert metadata["capability_owner_cells"] == (
        "chief_engineer.blueprint",
        "code_intelligence.engine",
        "runtime.task_market",
    )
    assert metadata["task_market_binding_refs"] == ("runtime.task_market:pending_design",)


def test_public_runtime_instantiation_accepts_active_task_market_binding() -> None:
    profile_service = FakeRoleProfileService()
    task_market_binding = RoleTaskMarketBinding(
        work_item_ref="runtime.task_market:task:task-1",
        lease_token_ref="runtime.task_market:lease:lease-1",
    )

    result = instantiate_role_runtime_object(
        InstantiateRoleRuntimeObjectCommandV1(
            role_id="chief_engineer",
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            workspace="/workspace",
            host_kind="task_market_worker",
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            policy_fingerprint="ce-policy-fp",
            task_market_binding=task_market_binding,
        ),
        profile_service=profile_service,
    )

    assert result.ok is True
    assert result.runtime_object is not None
    assert result.runtime_object.task_market_binding is task_market_binding
    assert result.runtime_object.task_market_binding.work_item_ref == "runtime.task_market:task:task-1"
    assert result.runtime_object.task_market_binding.lease_token_ref == "runtime.task_market:lease:lease-1"


def test_builtin_chief_engineer_runtime_spec_mounts_blueprint_assets_and_code_intel_capabilities() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")

    assert isinstance(spec, runtime_contracts.RoleRuntimeObjectSpec)
    assert spec.role_id == "chief_engineer"
    assert spec.asset_mounts.get("BlueprintDatabase").asset_ref.owner_cell == "chief_engineer.blueprint"
    assert spec.asset_mounts.get("ArchConstraintMemo").asset_ref.owner_cell == "chief_engineer.blueprint"
    assert spec.asset_mounts.get("DiffMapArchive").asset_ref.metadata["requires_blueprint_ref"] is True

    generate_diff = spec.capability_ports.get("generate_diff_specification")
    assert generate_diff.owner_cell == "chief_engineer.blueprint"
    assert generate_diff.contract_name == "GenerateTaskBlueprintCommandV1"
    assert generate_diff.allowed_roles == ("chief_engineer",)
    verify_ast = spec.capability_ports.get("verify_ast_dependency")
    assert verify_ast.owner_cell == "code_intelligence.engine"
    assert verify_ast.contract_name == "VerifyAstDependencyQueryV1"
    assert verify_ast.metadata["output_contract"] == "AstDependencyVerificationResultV1"
    assert spec.capability_ports.get("record_arch_memo").owner_cell == "chief_engineer.blueprint"

    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
    )

    assert runtime_object.identity.role_id == "chief_engineer"
    assert runtime_object.capability_fingerprint.capability_id == "generate_diff_specification"
    assert runtime_object.asset_mounts.get("BlueprintDatabase").asset_ref.ref.startswith("chief_engineer.blueprint:")


def test_builtin_qa_runtime_spec_mounts_truth_assets_and_pytest_capability() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")

    assert isinstance(spec, runtime_contracts.RoleRuntimeObjectSpec)
    assert spec.role_id == "qa"
    assert spec.asset_mounts.get("TruthLog").asset_ref.owner_cell == "audit.evidence"
    assert (
        spec.asset_mounts.get("TruthLog").asset_ref.metadata["runtime_receipt_ref"]
        == "factory.cognitive_runtime:receipt:truth-log"
    )
    assert spec.asset_mounts.get("RegressionTestRegistry").asset_ref.owner_cell == "qa.audit_verdict"
    assert spec.asset_mounts.get("FailureSignalIndex").asset_ref.owner_cell == "qa.audit_verdict"

    pytest_capability = spec.capability_ports.get("invoke_container_pytest")
    assert pytest_capability.owner_cell == "factory.verification_guard"
    assert pytest_capability.contract_name == "VerifyCompletionCommandV1"
    assert pytest_capability.effect == "process.spawn:qa/pytest"
    assert pytest_capability.allowed_roles == ("qa",)
    traceback_capability = spec.capability_ports.get("parse_traceback_frames")
    assert traceback_capability.owner_cell == "qa.audit_verdict"
    assert traceback_capability.contract_name == "ParseTracebackFramesCommandV1"
    assert traceback_capability.effect == "qa.failure_signal.parse"
    assert spec.capability_ports.get("issue_audit_verdict").contract_name == "RunQaAuditCommandV1"
    visual_capability = spec.capability_ports.get("issue_visual_audit_verdict")
    assert visual_capability.owner_cell == "qa.audit_verdict"
    assert visual_capability.contract_name == "RunVisualQaAuditCommandV1"
    assert visual_capability.effect == "llm.invoke:vision"
    assert visual_capability.metadata["required_model_capability"] == "image_input"

    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
    )

    assert runtime_object.identity.role_id == "qa"
    assert runtime_object.capability_fingerprint.capability_id == "invoke_container_pytest"
    assert runtime_object.asset_mounts.get("TruthLog").asset_ref.contract_name == "QueryEvidenceEventsV1"


def test_builtin_architect_runtime_spec_mounts_graph_budget_and_boundary_capabilities() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("architect")

    assert isinstance(spec, runtime_contracts.RoleRuntimeObjectSpec)
    assert spec.role_id == "architect"
    assert spec.asset_mounts.get("ConstraintTopology").asset_ref.owner_cell == "context.catalog"
    assert spec.asset_mounts.get("ConstraintTopology").asset_ref.metadata["graph_source_ref"] == "docs/graph/**"
    assert spec.asset_mounts.get("ContextBudgetProfile").asset_ref.owner_cell == "finops.budget_guard"
    assert spec.asset_mounts.get("MutationBoundaryMap").asset_ref.owner_cell == "policy.workspace_guard"
    assert (
        spec.asset_mounts.get("MutationBoundaryMap").asset_ref.metadata["permission_owner_cell"] == "policy.permission"
    )

    budget_capability = spec.capability_ports.get("allocate_context_token_budget")
    assert budget_capability.owner_cell == "finops.budget_guard"
    assert budget_capability.contract_name == "ReserveBudgetCommandV1"
    assert budget_capability.allowed_roles == ("architect",)
    mutation_guard = spec.capability_ports.get("intercept_illegal_mutations")
    assert mutation_guard.owner_cell == "policy.workspace_guard"
    assert mutation_guard.contract_name == "WorkspaceWriteGuardQueryV1"
    assert mutation_guard.effect == "mutation.guard:workspace"
    assert spec.capability_ports.get("validate_cell_boundary_change").owner_cell == "architect.design"

    runtime_object = spec.instantiate(
        identity=_identity("architect"),
        profile_binding=_profile_binding("architect"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:architect-run"),
        policy_fingerprint="architect-policy",
    )

    assert runtime_object.identity.role_id == "architect"
    assert runtime_object.capability_fingerprint.capability_id == "intercept_illegal_mutations"
    assert runtime_object.asset_mounts.get("ConstraintTopology").asset_ref.ref == "docs.graph:cells"


def test_builtin_director_runtime_spec_mounts_execution_assets_and_execute_capability() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("director")

    assert isinstance(spec, runtime_contracts.RoleRuntimeObjectSpec)
    assert spec.role_id == "director"
    assert spec.asset_mounts.get("ExecutionTask").asset_ref.owner_cell == "runtime.task_market"
    assert spec.asset_mounts.get("ExecutionTask").asset_ref.contract_name == "ClaimTaskWorkItemCommandV1"
    assert spec.asset_mounts.get("DirectorExecutionState").asset_ref.owner_cell == "director.execution"
    assert spec.asset_mounts.get("DirectorExecutionState").asset_ref.contract_name == "GetDirectorTaskStatusQueryV1"
    assert spec.asset_mounts.get("DirectorEvidenceTrail").asset_ref.owner_cell == "audit.evidence"
    assert spec.asset_mounts.get("DirectorEvidenceTrail").asset_ref.contract_name == "AppendEvidenceEventCommandV1"
    assert spec.asset_mounts.get("DirectorEvidenceTrail").access_mode == "write"

    execute_capability = spec.capability_ports.get("execute_director_task")
    assert execute_capability.owner_cell == "director.execution"
    assert execute_capability.contract_name == "ExecuteDirectorTaskCommandV1"
    assert execute_capability.effect == "process.spawn:director/*"
    assert execute_capability.allowed_roles == ("director",)
    assert execute_capability.metadata["output_contract"] == "DirectorExecutionResultV1"

    runtime_object = spec.instantiate(
        identity=_identity("director"),
        profile_binding=_profile_binding("director"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:director-run"),
        policy_fingerprint="director-policy",
    )

    assert runtime_object.identity.role_id == "director"
    assert runtime_object.capability_fingerprint.capability_id == "execute_director_task"
    assert runtime_object.task_market_binding.work_item_ref == "runtime.task_market:pending_exec"


def test_builtin_role_capability_effects_are_declared_in_roles_runtime_cell_yaml() -> None:
    cell_yaml = Path(__file__).resolve().parents[2] / "cell.yaml"
    cell_payload = yaml.safe_load(cell_yaml.read_text(encoding="utf-8"))
    allowed_effects = tuple(str(effect) for effect in cell_payload["effects_allowed"])

    undeclared_effects: list[str] = []
    for role_id in ("pm", "chief_engineer", "architect", "qa", "director"):
        spec = runtime_contracts.get_builtin_role_runtime_spec(role_id)
        for capability in spec.capability_ports.capabilities:
            if not _role_runtime_effect_is_allowed(capability.effect, allowed_effects):
                undeclared_effects.append(f"{role_id}.{capability.capability_id}:{capability.effect}")

    assert undeclared_effects == []


def test_runtime_spec_rejects_capability_asset_mount_dependency_that_is_not_mounted() -> None:
    with pytest.raises(
        ValueError,
        match=r"capability 'evaluate_critical_path' requires missing asset mount 'MissingTaskGraph'",
    ):
        runtime_contracts.RoleRuntimeObjectSpec(
            role_id="pm",
            asset_mounts=RoleAssetMountTable(
                mounts=(
                    RoleAssetMount(
                        "TaskGraph",
                        RoleAssetRef(
                            asset_id="task-graph",
                            owner_cell="runtime.task_market",
                            contract_name="QueryTaskMarketStatusV1",
                            ref="runtime.task_market:task-graph",
                            asset_kind="task_graph",
                        ),
                    ),
                )
            ),
            capability_ports=RoleCapabilityPorts(
                capabilities=(
                    RoleCapabilityDescriptor(
                        capability_id="evaluate_critical_path",
                        owner_cell="runtime.task_market",
                        contract_name="QueryTaskMarketStatusV1",
                        effect="task_market.read",
                        allowed_roles=("pm",),
                        endpoint_ref="runtime.task_market:QueryTaskMarketStatusV1",
                        metadata={"requires_asset_mounts": ("TaskGraph", "MissingTaskGraph")},
                    ),
                )
            ),
            default_capability_id="evaluate_critical_path",
        )


def test_runtime_spec_rejects_role_excluded_capability_ports() -> None:
    with pytest.raises(
        ValueError,
        match=r"capability 'invoke_container_pytest' is not allowed for role 'pm'",
    ):
        runtime_contracts.RoleRuntimeObjectSpec(
            role_id="pm",
            asset_mounts=RoleAssetMountTable(),
            capability_ports=_mixed_role_capability_ports(),
            default_capability_id="dispatch_task_to_market",
        )


def test_asset_mount_table_rejects_duplicate_mount_names() -> None:
    asset_ref = RoleAssetRef(
        asset_id="task-graph",
        owner_cell="runtime.task_market",
        contract_name="QueryTaskMarketStatusV1",
        ref="runtime.task_market:task-graph",
    )

    with pytest.raises(ValueError, match="duplicate asset mount"):
        RoleAssetMountTable(
            mounts=(
                RoleAssetMount(mount_name="TaskGraph", asset_ref=asset_ref),
                RoleAssetMount(mount_name="TaskGraph", asset_ref=asset_ref),
            )
        )


def test_asset_mount_table_rejects_role_runtime_or_kernelone_role_asset_owners() -> None:
    forbidden_owner_cells = (
        "roles.runtime",
        "roles.adapters",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "kernelone.roles",
        "polaris.kernelone.roles",
    )

    for owner_cell in forbidden_owner_cells:
        with pytest.raises(ValueError, match="must be owned by a business or platform state Cell"):
            RoleAssetMountTable(
                mounts=(
                    RoleAssetMount(
                        mount_name=f"invalid-{owner_cell}",
                        asset_ref=RoleAssetRef(
                            asset_id="invalid-business-asset",
                            owner_cell=owner_cell,
                            contract_name="InvalidBusinessAssetV1",
                            ref=f"{owner_cell}:invalid-business-asset",
                        ),
                    ),
                )
            )


@pytest.mark.parametrize(
    "asset_ref",
    (
        "roles.runtime:fake-business-asset",
        "roles.kernel:turn-ledger-as-asset",
        "roles.profile:profile-as-business-asset",
        "roles.session:session-as-business-asset",
        "kernelone.roles:template-as-business-asset",
        "polaris.kernelone.roles.business:template-as-business-asset",
    ),
)
def test_asset_mount_table_rejects_role_runtime_or_kernelone_role_asset_refs(asset_ref: str) -> None:
    with pytest.raises(ValueError, match="asset ref must point to the real owner Cell"):
        RoleAssetMountTable(
            mounts=(
                RoleAssetMount(
                    mount_name="invalid-ref-owner",
                    asset_ref=RoleAssetRef(
                        asset_id="invalid-ref-owner",
                        owner_cell="qa.audit_verdict",
                        contract_name="RunQaAuditCommandV1",
                        ref=asset_ref,
                    ),
                ),
            )
        )


def test_asset_mount_table_rejects_asset_ref_namespace_that_does_not_match_owner_cell() -> None:
    with pytest.raises(ValueError, match="asset ref namespace must match owner_cell"):
        RoleAssetMountTable(
            mounts=(
                RoleAssetMount(
                    mount_name="misowned-blueprint",
                    asset_ref=RoleAssetRef(
                        asset_id="misowned-blueprint",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="runtime.task_market:task-graph",
                    ),
                ),
            )
        )


def test_asset_mount_table_rejects_unanchored_diff_map_archive() -> None:
    with pytest.raises(
        ValueError,
        match="DiffMapArchive asset mount must include blueprint_id, path, and ref metadata",
    ):
        RoleAssetMountTable(
            mounts=(
                RoleAssetMount(
                    mount_name="DiffMapArchive",
                    asset_ref=RoleAssetRef(
                        asset_id="diff-map-archive",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:diff-map-archive",
                        asset_kind="diff_map_archive",
                        metadata={"requires_blueprint_ref": True},
                    ),
                ),
            )
        )


def test_asset_mount_table_rejects_open_loop_registry_without_evidence_ref() -> None:
    with pytest.raises(
        ValueError,
        match=r"OpenLoopRegistry asset mount must include audit\.evidence evidence_ref metadata",
    ):
        RoleAssetMountTable(
            mounts=(
                RoleAssetMount(
                    mount_name="OpenLoopRegistry",
                    asset_ref=RoleAssetRef(
                        asset_id="open-loop-registry",
                        owner_cell="runtime.task_market",
                        contract_name="QueryTaskMarketStatusV1",
                        ref="runtime.task_market:open-loops",
                        asset_kind="open_loop_registry",
                        metadata={"evidence_owner_cell": "audit.evidence"},
                    ),
                ),
            )
        )


def test_asset_mount_table_rejects_truth_log_without_runtime_receipt_ref() -> None:
    with pytest.raises(
        ValueError,
        match=r"TruthLog asset mount must include factory\.cognitive_runtime runtime_receipt_ref metadata",
    ):
        RoleAssetMountTable(
            mounts=(
                RoleAssetMount(
                    mount_name="TruthLog",
                    asset_ref=RoleAssetRef(
                        asset_id="truth-log",
                        owner_cell="audit.evidence",
                        contract_name="QueryEvidenceEventsV1",
                        ref="audit.evidence:runtime/evidence",
                        asset_kind="truth_log",
                        metadata={"runtime_receipt_owner_cell": "factory.cognitive_runtime"},
                    ),
                ),
            )
        )


def test_capability_ports_reject_duplicate_capability_ids() -> None:
    capability = RoleCapabilityDescriptor(
        capability_id="dispatch_task_to_market",
        owner_cell="runtime.task_market",
        contract_name="PublishTaskWorkItemCommandV1",
        effect="task_market.publish",
        allowed_roles=("pm",),
    )

    with pytest.raises(ValueError, match="duplicate capability"):
        RoleCapabilityPorts(capabilities=(capability, capability))


def test_capability_ports_reject_unscoped_role_capabilities() -> None:
    with pytest.raises(ValueError, match="capability 'dispatch_task_to_market' must declare allowed_roles"):
        RoleCapabilityPorts(
            capabilities=(
                RoleCapabilityDescriptor(
                    capability_id="dispatch_task_to_market",
                    owner_cell="runtime.task_market",
                    contract_name="PublishTaskWorkItemCommandV1",
                    effect="task_market.publish",
                ),
            )
        )


def test_runtime_role_scope_checks_do_not_keep_empty_allowed_roles_compatibility() -> None:
    # ``capability_commands`` holds the role-scope checks after the lossless
    # split of ``service.py``; scan it too so the invariant keeps its coverage.
    sources = (
        Path(runtime_contracts.__file__).read_text(encoding="utf-8"),
        Path(runtime_service.__file__).read_text(encoding="utf-8"),
        Path(runtime_capability_commands.__file__).read_text(encoding="utf-8"),
    )
    forbidden_patterns = (
        "if capability.allowed_roles and",
        "if lifecycle_capability.allowed_roles and",
    )

    violations = [pattern for source in sources for pattern in forbidden_patterns if pattern in source]

    assert violations == []


def test_capability_descriptor_rejects_public_endpoint_outside_owner_cell() -> None:
    with pytest.raises(ValueError, match="endpoint_ref must point to owner_cell public contract"):
        RoleCapabilityDescriptor(
            capability_id="misowned-blueprint-generation",
            owner_cell="chief_engineer.blueprint",
            contract_name="GenerateTaskBlueprintCommandV1",
            effect="blueprint.generate",
            allowed_roles=("chief_engineer",),
            endpoint_ref="polaris.cells.runtime.task_market.public.service.publish_work_item",
        )


@pytest.mark.parametrize(
    "owner_cell",
    (
        "roles.runtime",
        "roles.adapters",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "kernelone.roles",
        "polaris.kernelone.roles",
        "polaris.kernelone.roles.business",
    ),
)
def test_capability_descriptor_rejects_role_runtime_or_kernelone_role_owners(owner_cell: str) -> None:
    with pytest.raises(ValueError, match="capability owner_cell must be a target public Cell"):
        RoleCapabilityDescriptor(
            capability_id=f"invalid-{owner_cell}",
            owner_cell=owner_cell,
            contract_name="InvalidRoleCapabilityCommandV1",
            effect="role.capability.invalid",
            allowed_roles=("pm",),
        )


def test_capability_ports_reject_role_runtime_or_kernelone_role_capability_owners() -> None:
    forbidden_owner_cells = (
        "roles.runtime",
        "roles.adapters",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "kernelone.roles",
        "polaris.kernelone.roles",
    )

    for owner_cell in forbidden_owner_cells:
        with pytest.raises(ValueError, match="must be a target public Cell"):
            RoleCapabilityPorts(
                capabilities=(
                    RoleCapabilityDescriptor(
                        capability_id=f"invalid-{owner_cell}",
                        owner_cell=owner_cell,
                        contract_name="InvalidRoleCapabilityCommandV1",
                        effect="role.capability.invalid",
                        allowed_roles=("pm",),
                    ),
                )
            )


def test_capability_fingerprint_is_deterministic_and_policy_sensitive() -> None:
    base = RoleCapabilityFingerprint(
        role_id="qa",
        capability_id="invoke_container_pytest",
        effect="process.spawn:qa/pytest",
        tool="pytest",
        policy_fingerprint="qa-policy",
        profile_fingerprint="qa-profile",
    )
    same = RoleCapabilityFingerprint(
        role_id="qa",
        capability_id="invoke_container_pytest",
        effect="process.spawn:qa/pytest",
        tool="pytest",
        policy_fingerprint="qa-policy",
        profile_fingerprint="qa-profile",
    )
    changed_policy = RoleCapabilityFingerprint(
        role_id="qa",
        capability_id="invoke_container_pytest",
        effect="process.spawn:qa/pytest",
        tool="pytest",
        policy_fingerprint="different-policy",
        profile_fingerprint="qa-profile",
    )

    assert base.fingerprint == same.fingerprint
    assert base.fingerprint != changed_policy.fingerprint
    assert len(base.fingerprint) == 64


@pytest.mark.parametrize(
    ("fingerprint", "match"),
    (
        ("capability-fp", r"fingerprint must be a 64-character hex capability fingerprint"),
        ("0" * 64, r"fingerprint must match role, capability, effect, tool, policy, and profile fields"),
    ),
)
def test_capability_fingerprint_rejects_fabricated_override(fingerprint: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        RoleCapabilityFingerprint(
            role_id="qa",
            capability_id="invoke_container_pytest",
            effect="process.spawn:qa/pytest",
            tool="pytest",
            policy_fingerprint="qa-policy",
            profile_fingerprint="qa-profile",
            fingerprint=fingerprint,
        )


def test_role_turn_envelope_and_commit_request_carry_refs_not_foreign_state() -> None:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-1",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref="runtime.task_market:task:task-1",
        fingerprint_ref="a" * 64,
    )
    envelope = RoleTurnEnvelope(
        identity=_identity(),
        profile_binding=_profile_binding(),
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
            handoff_refs=("factory.cognitive_runtime:handoff:handoff-1",),
            task_refs=("runtime.task_market:task:task-1",),
        ),
        capability_invocations=(invocation,),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_contract="CommitReceipt",
            runtime_receipt_contract="RecordRuntimeReceiptCommandV1",
        ),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
    )
    request = RoleStateCommitRequest(
        request_id="commit-request-1",
        envelope=envelope,
        changed_asset_refs=("runtime.task_market:task:task-1",),
        changed_files=("runtime/tasks/task-1.json",),
        allowed_scope_paths=("runtime/tasks",),
        evidence_refs=("audit.evidence:evt-1",),
        reason="task-market dispatch committed",
    )
    receipt = RoleStateCommitReceipt(
        request_id=request.request_id,
        ok=True,
        commit_receipt_ref="roles.kernel:commit:turn-1",
        runtime_receipt_refs=("factory.cognitive_runtime:receipt-1",),
        turn_outcome_ref="roles.kernel:turn-outcome:turn-1",
    )

    assert request.envelope.turn_context.handoff_refs == ("factory.cognitive_runtime:handoff:handoff-1",)
    assert request.envelope.capability_invocations[0].payload_ref == "runtime.task_market:task:task-1"
    assert receipt.commit_contract == "CommitReceipt"
    assert receipt.runtime_receipt_contract == "RecordRuntimeReceiptCommandV1"


def test_role_turn_envelope_rejects_invocation_role_outside_identity() -> None:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-wrong-role",
        capability_id="dispatch_task_to_market",
        role_id="qa",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref="runtime.task_market:task:task-1",
        fingerprint_ref="a" * 64,
    )

    with pytest.raises(ValueError, match=r"capability_invocations role_id must match identity\.role_id"):
        RoleTurnEnvelope(
            identity=_identity("pm"),
            profile_binding=_profile_binding("pm"),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:task-1",
                context_snapshot_ref="context.engine:snapshot-1",
                task_refs=("runtime.task_market:task:task-1",),
            ),
            capability_invocations=(invocation,),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
        )


@pytest.mark.parametrize(
    "payload_ref",
    (
        "roles.runtime:typed-input:other-turn",
        "runtime.task_market:task:other-task",
        "runtime.task_market:work-item:task-1",
    ),
)
def test_role_turn_envelope_rejects_invocation_payload_outside_turn_context(payload_ref: str) -> None:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-outside-turn",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref=payload_ref,
        fingerprint_ref="a" * 64,
    )

    with pytest.raises(ValueError, match=r"capability_invocations payload_ref must match turn_context"):
        RoleTurnEnvelope(
            identity=_identity("pm"),
            profile_binding=_profile_binding("pm"),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:task-1",
                context_snapshot_ref="context.engine:snapshot-1",
                task_refs=("runtime.task_market:task:task-1",),
            ),
            capability_invocations=(invocation,),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
        )


def test_role_turn_envelope_rejects_duplicate_capability_invocation_ids() -> None:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-duplicate",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref="runtime.task_market:task:task-1",
        fingerprint_ref="a" * 64,
    )

    with pytest.raises(ValueError, match="duplicate capability invocation: invoke-duplicate"):
        RoleTurnEnvelope(
            identity=_identity("pm"),
            profile_binding=_profile_binding("pm"),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:task-1",
                context_snapshot_ref="context.engine:snapshot-1",
                task_refs=("runtime.task_market:task:task-1",),
            ),
            capability_invocations=(invocation, invocation),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
        )


def test_role_turn_envelope_rejects_active_task_market_binding_without_turn_task_ref() -> None:
    with pytest.raises(
        ValueError,
        match=r"task_market_binding\.work_item_ref must be listed in turn_context\.task_refs",
    ):
        RoleTurnEnvelope(
            identity=_identity(),
            profile_binding=_profile_binding(),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:task-1",
                context_snapshot_ref="context.engine:snapshot-1",
                handoff_refs=("factory.cognitive_runtime:handoff:handoff-1",),
            ),
            capability_invocations=(),
            ledger_binding=RoleLedgerBinding(
                turn_ledger_ref="roles.kernel:turn-ledger:run-1",
                commit_contract="CommitReceipt",
                runtime_receipt_contract="RecordRuntimeReceiptCommandV1",
            ),
            task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
        )


def test_role_state_commit_request_rejects_changed_asset_ref_outside_turn_context() -> None:
    with pytest.raises(ValueError, match=r"changed_asset_refs must be listed in turn_context task refs"):
        RoleStateCommitRequest(
            request_id="commit-request-outside-asset",
            envelope=_commit_envelope(),
            changed_asset_refs=("runtime.task_market:task:other-task",),
            changed_files=("runtime/tasks/task-1.json",),
            allowed_scope_paths=("runtime/tasks",),
            evidence_refs=("audit.evidence:evt-1",),
            reason="reject outside-turn asset",
        )


def test_role_state_commit_request_rejects_empty_commit_without_audit_anchor() -> None:
    with pytest.raises(
        ValueError,
        match="role state commit must include changed_asset_refs, changed_files, or evidence_refs",
    ):
        RoleStateCommitRequest(
            request_id="commit-request-empty",
            envelope=_commit_envelope(),
            changed_asset_refs=(),
            changed_files=(),
            allowed_scope_paths=(),
            evidence_refs=(),
            reason="empty commit must not produce a runtime receipt",
        )


def test_role_state_commit_request_rejects_evidence_refs_outside_audit_evidence() -> None:
    with pytest.raises(ValueError, match=r"evidence_refs must point to audit\.evidence"):
        RoleStateCommitRequest(
            request_id="commit-request-invalid-evidence",
            envelope=_commit_envelope(),
            changed_asset_refs=("runtime.task_market:task:task-1",),
            changed_files=("runtime/tasks/task-1.json",),
            allowed_scope_paths=("runtime/tasks",),
            evidence_refs=("roles.runtime:evidence:evt-1",),
            reason="reject invalid evidence owner",
        )


@pytest.mark.parametrize(
    "changed_files",
    (
        ("/runtime/tasks/task-1.json",),
        ("outside/task-1.json",),
        ("runtime/tasks-ok/../events/task-1.json",),
    ),
)
def test_role_state_commit_request_rejects_changed_files_outside_allowed_scope(
    changed_files: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match=r"changed_files must be within allowed_scope_paths"):
        RoleStateCommitRequest(
            request_id="commit-request-outside-file",
            envelope=_commit_envelope(),
            changed_asset_refs=("runtime.task_market:task:task-1",),
            changed_files=changed_files,
            allowed_scope_paths=("runtime/tasks",),
            evidence_refs=("audit.evidence:evt-1",),
            reason="reject file outside allowed scope",
        )


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {"payload_ref": "roles.profile:prompt:pm"},
            r"payload_ref must point to roles\.runtime or runtime\.task_market",
        ),
        (
            {"fingerprint_ref": "capability-fp"},
            r"fingerprint_ref must be a 64-character hex capability fingerprint",
        ),
        (
            {"fingerprint_ref": "z" * 64},
            r"fingerprint_ref must be a 64-character hex capability fingerprint",
        ),
    ),
)
def test_role_capability_invocation_rejects_unowned_payload_or_invalid_fingerprint(
    payload: _CapabilityInvocationKwargs,
    expected_error: str,
) -> None:
    invocation_payload: _CapabilityInvocationKwargs = {
        "invocation_id": "invoke-invalid-ref",
        "capability_id": "dispatch_task_to_market",
        "role_id": "pm",
        "command_contract": "PublishTaskWorkItemCommandV1",
        "payload_ref": "roles.runtime:typed-input:pm-task-1",
        "fingerprint_ref": "a" * 64,
    }
    invocation_payload.update(payload)

    with pytest.raises(ValueError, match=expected_error):
        RoleCapabilityInvocation(**invocation_payload)


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {"typed_input_ref": "roles.profile:prompt:pm"},
            r"typed_input_ref must point to roles\.runtime or runtime\.task_market",
        ),
        (
            {"context_snapshot_ref": "roles.runtime:snapshot:1"},
            r"context_snapshot_ref must point to context\.engine or roles\.session",
        ),
        (
            {"handoff_refs": ("roles.session:handoff:1",)},
            r"handoff_refs must point to factory\.cognitive_runtime",
        ),
        (
            {"task_refs": ("roles.kernel:task:1",)},
            r"task_refs must point to runtime\.task_market",
        ),
        (
            {"task_refs": ("runtime.task_market:task-1",)},
            r"task_refs must use runtime\.task_market:task:<task_id>",
        ),
    ),
)
def test_role_turn_context_rejects_refs_outside_source_of_truth(
    payload: _TurnContextKwargs,
    expected_error: str,
) -> None:
    context_payload: _TurnContextKwargs = {
        "typed_input_ref": "roles.runtime:typed-input:pm-task-1",
        "context_snapshot_ref": "context.engine:snapshot-1",
        "handoff_refs": ("factory.cognitive_runtime:handoff:1",),
        "task_refs": ("runtime.task_market:task:1",),
    }
    context_payload.update(payload)

    with pytest.raises(ValueError, match=expected_error):
        RoleTurnContext(**context_payload)


def test_role_capability_decision_rejects_evidence_refs_outside_audit_evidence() -> None:
    with pytest.raises(ValueError, match=r"evidence_refs must point to audit\.evidence"):
        RoleCapabilityDecision(
            invocation_id="decision-invalid-evidence",
            capability_id="dispatch_task_to_market",
            role_id="pm",
            allowed=False,
            denial_code="policy_denied",
            evidence_refs=("roles.kernel:evidence:decision-1",),
        )


def test_role_capability_invocation_result_rejects_failed_allowed_true() -> None:
    with pytest.raises(ValueError, match=r"failed capability invocation result must set allowed=False"):
        RoleCapabilityInvocationResultV1(
            ok=False,
            invocation_id="invoke-failed-allowed-true",
            role_id="architect",
            capability_id="validate_cell_boundary_change",
            command_contract="GenerateArchitectureDesignCommandV1",
            allowed=True,
            owner_cell="architect.design",
            payload_ref="roles.runtime:typed-input:architect-boundary-1",
            metadata={"capability_available": True},
            error_code="invalid_architect_boundary_context",
            error_message="payload.context must be a mapping when provided",
        )


def test_capability_invocation_failure_helper_does_not_use_allowed_for_capability_availability() -> None:
    # The stateless capability-command handlers (including this helper) live in
    # ``capability_commands`` after the lossless split of ``service.py``.
    service_path = Path(runtime_capability_commands.__file__)
    service_tree = ast.parse(service_path.read_text(encoding="utf-8"))

    helper_defs = [
        node
        for node in ast.walk(service_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_capability_invocation_failure"
    ]
    assert len(helper_defs) == 1
    keyword_only_args = {arg.arg for arg in helper_defs[0].args.kwonlyargs}
    assert "allowed" not in keyword_only_args
    assert "capability_available" in keyword_only_args

    calls_with_allowed_keyword = [
        node.lineno
        for node in ast.walk(service_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_capability_invocation_failure"
        and any(keyword.arg == "allowed" for keyword in node.keywords)
    ]
    assert calls_with_allowed_keyword == []


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {
                "ok": True,
                "allowed": True,
                "owner_cell": "roles.runtime",
                "result_ref": "roles.runtime:result:1",
                "payload_ref": "roles.runtime:result:1",
            },
            r"capability invocation result owner_cell must be a target public Cell",
        ),
        (
            {
                "ok": True,
                "allowed": True,
                "owner_cell": "qa.audit_verdict",
                "result_ref": "runtime.task_market:result:1",
                "payload_ref": "runtime.task_market:result:1",
            },
            r"result_ref must point to owner_cell",
        ),
        (
            {
                "ok": False,
                "allowed": False,
                "owner_cell": "",
                "result_ref": None,
                "payload_ref": "roles.profile:prompt:pm",
                "error_code": "capability_not_mounted",
                "error_message": "not mounted",
            },
            r"payload_ref must point to roles\.runtime or runtime\.task_market",
        ),
    ),
)
def test_role_capability_invocation_result_rejects_unowned_result_refs(
    payload: _CapabilityInvocationResultKwargs,
    expected_error: str,
) -> None:
    result_payload: _CapabilityInvocationResultKwargs = {
        "ok": True,
        "invocation_id": "invoke-result-invalid",
        "role_id": "qa",
        "capability_id": "issue_audit_verdict",
        "command_contract": "RunQaAuditCommandV1",
        "allowed": True,
        "payload_ref": "qa.audit_verdict:verdict:task-1",
        "owner_cell": "qa.audit_verdict",
        "result_ref": "qa.audit_verdict:verdict:task-1",
    }
    result_payload.update(payload)

    with pytest.raises(ValueError, match=expected_error):
        RoleCapabilityInvocationResultV1(**result_payload)


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {"owner_cell": "", "result_ref": None},
            "successful capability invocation result must include owner_cell",
        ),
        (
            {"owner_cell": "qa.audit_verdict", "result_ref": None},
            "successful capability invocation result must include result_ref",
        ),
    ),
)
def test_successful_role_capability_invocation_result_requires_target_result_anchor(
    payload: _CapabilityInvocationResultKwargs,
    expected_error: str,
) -> None:
    result_payload: _CapabilityInvocationResultKwargs = {
        "ok": True,
        "invocation_id": "invoke-success-without-result-anchor",
        "role_id": "qa",
        "capability_id": "issue_audit_verdict",
        "command_contract": "RunQaAuditCommandV1",
        "allowed": True,
        "payload_ref": "roles.runtime:typed-input:qa-task-1",
        "owner_cell": "qa.audit_verdict",
        "result_ref": "qa.audit_verdict:verdict:qa-task-1",
    }
    result_payload.update(payload)

    with pytest.raises(ValueError, match=expected_error):
        RoleCapabilityInvocationResultV1(**result_payload)


@pytest.mark.parametrize(
    "evidence_refs, expected_error",
    (
        (
            ("roles.kernel:evidence:qa-1",),
            r"evidence_refs must point to audit\.evidence",
        ),
        (
            ("audit.evidence:qa-1", "audit.evidence:qa-1"),
            "evidence_refs must not contain duplicate refs",
        ),
    ),
)
def test_role_capability_invocation_result_rejects_invalid_evidence_refs(
    evidence_refs: tuple[str, ...],
    expected_error: str,
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id="invoke-invalid-evidence-ref",
            role_id="qa",
            capability_id="issue_audit_verdict",
            command_contract="RunQaAuditCommandV1",
            allowed=True,
            payload_ref="qa.audit_verdict:verdict:task-1",
            owner_cell="qa.audit_verdict",
            result_ref="qa.audit_verdict:verdict:task-1",
            evidence_refs=evidence_refs,
        )


def test_successful_role_state_commit_receipt_requires_runtime_receipt_ref() -> None:
    with pytest.raises(ValueError, match="successful commit receipt must include runtime_receipt_refs"):
        RoleStateCommitReceipt(
            request_id="commit-request-without-runtime-receipt",
            ok=True,
            commit_receipt_ref="roles.kernel:commit:turn-1",
        )


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {
                "request_id": "bad-commit-ref",
                "ok": True,
                "commit_receipt_ref": "runtime.task_market:commit:turn-1",
                "runtime_receipt_refs": ("factory.cognitive_runtime:receipt-1",),
            },
            r"commit_receipt_ref must point to roles\.kernel",
        ),
        (
            {
                "request_id": "bad-runtime-receipt-ref",
                "ok": True,
                "commit_receipt_ref": "roles.kernel:commit:turn-1",
                "runtime_receipt_refs": ("roles.kernel:receipt-1",),
            },
            r"runtime_receipt_refs must point to factory\.cognitive_runtime",
        ),
        (
            {
                "request_id": "bad-change-set-ref",
                "ok": True,
                "commit_receipt_ref": "roles.kernel:commit:turn-1",
                "runtime_receipt_refs": ("factory.cognitive_runtime:receipt-1",),
                "change_set_validation_ref": "roles.kernel:change-set-validation-1",
            },
            r"change_set_validation_ref must point to factory\.cognitive_runtime",
        ),
        (
            {
                "request_id": "bad-handoff-ref",
                "ok": True,
                "commit_receipt_ref": "roles.kernel:commit:turn-1",
                "runtime_receipt_refs": ("factory.cognitive_runtime:receipt-1",),
                "handoff_pack_refs": ("roles.session:handoff-1",),
            },
            r"handoff_pack_refs must point to factory\.cognitive_runtime",
        ),
        (
            {
                "request_id": "bad-turn-outcome-ref",
                "ok": True,
                "commit_receipt_ref": "roles.kernel:commit:turn-1",
                "runtime_receipt_refs": ("factory.cognitive_runtime:receipt-1",),
                "turn_outcome_ref": "roles.runtime:turn-outcome-1",
            },
            r"turn_outcome_ref must point to roles\.kernel",
        ),
    ),
)
def test_role_state_commit_receipt_rejects_refs_outside_source_of_truth(
    payload: _StateCommitReceiptKwargs,
    expected_error: str,
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        RoleStateCommitReceipt(**payload)


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {
                "runtime_receipt_refs": (
                    "factory.cognitive_runtime:receipt-1",
                    "factory.cognitive_runtime:receipt-1",
                ),
            },
            "runtime_receipt_refs must not contain duplicate refs",
        ),
        (
            {
                "handoff_pack_refs": (
                    "factory.cognitive_runtime:handoff:handoff-1",
                    "factory.cognitive_runtime:handoff:handoff-1",
                ),
            },
            "handoff_pack_refs must not contain duplicate refs",
        ),
    ),
)
