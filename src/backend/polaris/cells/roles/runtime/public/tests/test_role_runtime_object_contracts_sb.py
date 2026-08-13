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






def test_role_state_commit_receipt_rejects_duplicate_cognitive_runtime_refs(
    payload: _StateCommitReceiptKwargs,
    expected_error: str,
) -> None:
    receipt_payload: _StateCommitReceiptKwargs = {
        "request_id": "duplicate-cognitive-runtime-ref",
        "ok": True,
        "commit_receipt_ref": "roles.kernel:commit:turn-1",
        "runtime_receipt_refs": ("factory.cognitive_runtime:receipt-1",),
    }
    receipt_payload.update(payload)

    with pytest.raises(ValueError, match=expected_error):
        RoleStateCommitReceipt(**receipt_payload)


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {"turn_ledger_ref": "roles.runtime:turn-ledger:run-1"},
            r"turn_ledger_ref must point to roles\.kernel",
        ),
        (
            {
                "turn_ledger_ref": "roles.kernel:turn-ledger:run-1",
                "commit_receipt_ref": "factory.cognitive_runtime:commit:turn-1",
            },
            r"commit_receipt_ref must point to roles\.kernel",
        ),
        (
            {
                "turn_ledger_ref": "roles.kernel:turn-ledger:run-1",
                "receipt_refs": ("roles.kernel:receipt:turn-1",),
            },
            r"receipt_refs must point to factory\.cognitive_runtime",
        ),
    ),
)
def test_role_ledger_binding_rejects_refs_outside_source_of_truth(
    payload: _LedgerBindingKwargs,
    expected_error: str,
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        RoleLedgerBinding(**payload)


@pytest.mark.parametrize(
    "payload",
    (
        {"work_item_ref": "roles.runtime:task-1"},
        {"lease_token_ref": "roles.session:lease-1"},
    ),
)
def test_role_task_market_binding_rejects_refs_outside_task_market(payload: _TaskMarketBindingKwargs) -> None:
    with pytest.raises(ValueError, match=r"task-market binding refs must point to runtime\.task_market"):
        RoleTaskMarketBinding(**payload)


def test_role_task_market_binding_rejects_legacy_active_task_ref_shape() -> None:
    with pytest.raises(
        ValueError,
        match=r"work_item_ref active task refs must use runtime\.task_market:task:<task_id>",
    ):
        RoleTaskMarketBinding(work_item_ref="runtime.task_market:task-1")


def test_role_turn_envelope_rejects_profile_role_mismatch() -> None:
    with pytest.raises(ValueError, match=r"identity\.role_id must match profile_binding\.role_id"):
        RoleTurnEnvelope(
            identity=_identity("pm"),
            profile_binding=_profile_binding("qa"),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:task-1",
                context_snapshot_ref="context.engine:snapshot-1",
            ),
            capability_invocations=(),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(),
        )


def test_role_turn_envelope_rejects_task_market_ref_outside_turn_context() -> None:
    with pytest.raises(
        ValueError,
        match=r"task_market_binding\.work_item_ref must be listed in turn_context\.task_refs",
    ):
        RoleTurnEnvelope(
            identity=_identity("chief_engineer"),
            profile_binding=_profile_binding("chief_engineer"),
            turn_context=RoleTurnContext(
                typed_input_ref="roles.runtime:typed-input:task-1",
                context_snapshot_ref="context.engine:snapshot-1",
                task_refs=("runtime.task_market:task:task-1",),
            ),
            capability_invocations=(),
            ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
            task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-other"),
        )


def test_role_task_market_lifecycle_uses_binding_contracts_and_public_service() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    identity = RoleIdentity(
        role_id="chief_engineer",
        run_id="run-1",
        task_id="task-1",
        session_id="session-1",
        workspace="/repo",
        host_kind="task_market_worker",
    )
    profile_binding = _profile_binding("chief_engineer")
    ledger_binding = RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run")
    active_task_market_binding = RoleTaskMarketBinding(
        work_item_ref="runtime.task_market:task:task-1",
        lease_token_ref="runtime.task_market:lease:lease-1",
    )

    def runtime_object_for(
        capability_id: str,
        task_market_binding: RoleTaskMarketBinding | None = None,
    ) -> RoleRuntimeObject:
        return spec.instantiate(
            identity=identity,
            profile_binding=profile_binding,
            ledger_binding=ledger_binding,
            policy_fingerprint="ce-policy",
            capability_id=capability_id,
            task_market_binding=task_market_binding,
        )

    claim_runtime_object = runtime_object_for("claim_task_market_work_item")
    task_market = FakeTaskMarketService()

    claim = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=claim_runtime_object,
            operation="claim",
            payload={"stage": "pending_design", "worker_id": "ce-worker-1", "task_id": "task-1"},
        ),
        task_market_service=task_market,
    )
    renew = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object_for("renew_task_market_lease", active_task_market_binding),
            operation="lease",
            payload={"task_id": "task-1", "lease_token": "lease-1", "visibility_timeout_seconds": 120},
        ),
        task_market_service=task_market,
    )
    ack = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object_for("acknowledge_task_market_stage", active_task_market_binding),
            operation="ack",
            payload={"task_id": "task-1", "lease_token": "lease-1", "next_stage": "pending_exec"},
        ),
        task_market_service=task_market,
    )
    fail = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object_for("fail_task_market_stage", active_task_market_binding),
            operation="fail",
            payload={
                "task_id": "task-1",
                "lease_token": "lease-1",
                "error_code": "blueprint_failed",
                "error_message": "blueprint validation failed",
                "requeue_stage": "pending_design",
            },
        ),
        task_market_service=task_market,
    )
    requeue = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object_for("requeue_task_market_work_item", active_task_market_binding),
            operation="requeue",
            payload={"task_id": "task-1", "target_stage": "pending_design", "reason": "retry blueprint"},
        ),
        task_market_service=task_market,
    )

    assert isinstance(claim, RoleTaskMarketLifecycleResultV1)
    assert claim.ok is True
    assert claim.command_contract == claim_runtime_object.task_market_binding.claim_contract
    assert claim.result_ref == "runtime.task_market:task:task-1"
    assert claim.lease_token_ref == "runtime.task_market:lease:lease-1"
    assert task_market.claimed[0].worker_role == "chief_engineer"
    assert task_market.claimed[0].workspace == "/repo"
    assert renew.command_contract == active_task_market_binding.lease_contract
    assert task_market.renewed[0].visibility_timeout_seconds == 120
    assert ack.command_contract == active_task_market_binding.ack_contract
    assert task_market.acked[0].next_stage == "pending_exec"
    assert fail.command_contract == active_task_market_binding.fail_contract
    assert task_market.failed[0].requeue_stage == "pending_design"
    assert requeue.command_contract == active_task_market_binding.requeue_contract
    assert task_market.requeued[0].target_stage == "pending_design"


def test_role_task_market_lifecycle_supports_publish_public_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=RoleIdentity(
            role_id="pm",
            run_id="run-1",
            task_id="pm-task-1",
            session_id="session-1",
            workspace="/repo",
            host_kind="task_market_worker",
        ),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
        capability_id="dispatch_task_to_market",
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="publish",
            payload={
                "trace_id": "trace-1",
                "run_id": "run-1",
                "task_id": "pm-task-1",
                "stage": "pending_design",
                "priority": "high",
                "payload": {"objective": "publish typed work item through lifecycle"},
                "metadata": {"plan_id": "plan-1"},
                "depends_on": ("dep-1",),
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is True
    assert result.command_contract == runtime_object.task_market_binding.publish_contract
    assert result.result_ref == "runtime.task_market:task:pm-task-1"
    assert result.task_id == "pm-task-1"
    assert len(task_market.published) == 1

    publish_command = task_market.published[0]
    assert isinstance(publish_command, PublishTaskWorkItemCommandV1)
    assert publish_command.workspace == "/repo"
    assert publish_command.source_role == "pm"
    assert publish_command.priority == "high"
    assert publish_command.payload == {"objective": "publish typed work item through lifecycle"}
    assert publish_command.metadata["role_id"] == "pm"
    assert publish_command.metadata["plan_id"] == "plan-1"
    assert publish_command.depends_on == ("dep-1",)


def test_role_task_market_lifecycle_supports_dead_letter_public_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="move_task_to_dead_letter",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="dead_letter",
            payload={
                "task_id": "task-1",
                "reason": "unrecoverable blueprint failure",
                "error_code": "blueprint_unrecoverable",
                "metadata": {"failure_signal_ref": "audit.evidence:failure:task-1"},
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is True
    assert result.operation == "dead_letter"
    assert result.command_contract == "MoveTaskToDeadLetterCommandV1"
    assert result.result_ref == "runtime.task_market:task:task-1"
    assert result.status == "dead_letter"
    assert task_market.dead_lettered[0].workspace == "/repo"
    assert task_market.dead_lettered[0].task_id == "task-1"
    assert task_market.dead_lettered[0].reason == "unrecoverable blueprint failure"
    assert task_market.dead_lettered[0].error_code == "blueprint_unrecoverable"
    assert task_market.dead_lettered[0].metadata["role_id"] == "chief_engineer"
    assert task_market.dead_lettered[0].metadata["failure_signal_ref"] == "audit.evidence:failure:task-1"


def test_successful_role_task_market_lifecycle_result_requires_task_market_result_ref() -> None:
    with pytest.raises(
        ValueError,
        match=r"successful task-market lifecycle result must include a runtime\.task_market result_ref",
    ):
        RoleTaskMarketLifecycleResultV1(
            ok=True,
            role_id="chief_engineer",
            operation="claim",
            command_contract="ClaimTaskWorkItemCommandV1",
            task_id="",
            result_ref="",
        )


def test_successful_claim_or_lease_lifecycle_result_requires_task_market_lease_ref() -> None:
    with pytest.raises(
        ValueError,
        match=r"successful claim/lease task-market lifecycle result must include a runtime\.task_market lease_token_ref",
    ):
        RoleTaskMarketLifecycleResultV1(
            ok=True,
            role_id="chief_engineer",
            operation="lease",
            command_contract="RenewTaskLeaseCommandV1",
            task_id="task-1",
            result_ref="runtime.task_market:task:task-1",
            lease_token_ref="",
        )


def test_role_task_market_lifecycle_returns_structured_failure_for_missing_result_ref() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="claim_task_market_work_item",
    )

    class MalformedTaskMarketService:
        def __init__(self) -> None:
            self.claimed: list[ClaimTaskWorkItemCommandV1] = []

        def claim_work_item(self, command: ClaimTaskWorkItemCommandV1) -> object:
            self.claimed.append(command)
            return SimpleNamespace(ok=True, task_id="", status="leased", version=1, lease_token="lease-1")

    task_market = MalformedTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="claim",
            payload={"stage": "pending_design", "worker_id": "ce-worker-1"},
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_lifecycle_missing_result_ref"
    assert result.metadata["owner_cell"] == "runtime.task_market"
    assert task_market.claimed[0].stage == "pending_design"


def test_role_task_market_lifecycle_returns_structured_failure_for_missing_lease_ref() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="renew_task_market_lease",
        task_market_binding=RoleTaskMarketBinding(
            work_item_ref="runtime.task_market:task:task-1",
            lease_token_ref="runtime.task_market:lease:lease-1",
        ),
    )

    class MalformedTaskMarketService:
        def __init__(self) -> None:
            self.renewed: list[RenewTaskLeaseCommandV1] = []

        def renew_task_lease(self, command: RenewTaskLeaseCommandV1) -> object:
            self.renewed.append(command)
            return SimpleNamespace(ok=True, task_id=command.task_id, lease_token="", status="leased", version=1)

    task_market = MalformedTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="lease",
            payload={"task_id": "task-1", "lease_token": "lease-1"},
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_lifecycle_missing_lease_ref"
    assert result.metadata["owner_cell"] == "runtime.task_market"
    assert task_market.renewed[0].task_id == "task-1"


def test_role_task_market_lifecycle_rejects_task_outside_current_turn_context() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="acknowledge_task_market_stage",
        task_market_binding=RoleTaskMarketBinding(
            work_item_ref="runtime.task_market:task:task-1",
            lease_token_ref="runtime.task_market:lease:lease-1",
        ),
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="ack",
            payload={
                "task_id": "task-other",
                "lease_token": "lease-other",
                "next_stage": "pending_exec",
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_task_ref_outside_turn_context"
    assert result.metadata["turn_task_refs"] == runtime_object.turn_context.task_refs
    assert task_market.acked == []


def test_role_task_market_lifecycle_rejects_lease_outside_current_binding() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="acknowledge_task_market_stage",
        task_market_binding=RoleTaskMarketBinding(
            work_item_ref="runtime.task_market:task:task-1",
            lease_token_ref="runtime.task_market:lease:lease-1",
        ),
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="ack",
            payload={
                "task_id": "task-1",
                "lease_token": "lease-other",
                "next_stage": "pending_exec",
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_lease_ref_outside_binding"
    assert result.metadata["lease_token_ref"] == "runtime.task_market:lease:lease-other"
    assert result.metadata["binding_lease_token_ref"] == "runtime.task_market:lease:lease-1"
    assert task_market.acked == []


def test_role_task_market_lifecycle_rejects_lease_operation_without_binding() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="acknowledge_task_market_stage",
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="ack",
            payload={
                "task_id": "task-1",
                "lease_token": "lease-1",
                "next_stage": "pending_exec",
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_lease_ref_missing_from_binding"
    assert result.metadata["lease_token_ref"] == "runtime.task_market:lease:lease-1"
    assert result.metadata["binding_lease_token_ref"] == ""
    assert task_market.acked == []


def test_role_task_market_lifecycle_rejects_missing_capability_port_without_service_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        task_market_binding=RoleTaskMarketBinding(
            work_item_ref="runtime.task_market:task:task-1",
            lease_token_ref="runtime.task_market:lease:lease-1",
        ),
    )
    active_capability = runtime_object.capability_ports.get(runtime_object.capability_fingerprint.capability_id)
    runtime_object = dataclasses.replace(
        runtime_object,
        capability_ports=RoleCapabilityPorts(capabilities=(active_capability,)),
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="ack",
            payload={"task_id": "task-1", "lease_token": "lease-1", "next_stage": "pending_exec"},
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_capability_not_mounted"
    assert result.metadata["command_contract"] == "AcknowledgeTaskStageCommandV1"
    assert task_market.acked == []


def test_role_task_market_lifecycle_rejects_fingerprint_mismatch_without_service_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
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
        capability_id="generate_diff_specification",
        task_market_binding=RoleTaskMarketBinding(
            work_item_ref="runtime.task_market:task:task-1",
            lease_token_ref="runtime.task_market:lease:lease-1",
        ),
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="ack",
            payload={"task_id": "task-1", "lease_token": "lease-1", "next_stage": "pending_exec"},
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "task_market_capability_fingerprint_mismatch"
    assert result.metadata["expected_capability_id"] == "acknowledge_task_market_stage"
    assert result.metadata["actual_capability_id"] == "generate_diff_specification"
    assert task_market.acked == []


def test_role_task_market_lifecycle_rejects_unknown_operation_without_service_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=RoleIdentity(
            role_id="qa",
            run_id="run-1",
            task_id="task-qa-1",
            session_id="session-qa-1",
            workspace="/repo",
            host_kind="task_market_worker",
        ),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
    )
    task_market = FakeTaskMarketService()

    result = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="delete",
            payload={"task_id": "task-qa-1"},
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.error_code == "unsupported_task_market_operation"
    assert task_market.claimed == []
    assert task_market.acked == []
    assert task_market.failed == []
    assert task_market.requeued == []


def test_commit_role_state_records_runtime_receipt_and_handoff_via_cognitive_runtime() -> None:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-commit-1",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref="runtime.task_market:task:task-1",
        fingerprint_ref="a" * 64,
    )
    envelope = RoleTurnEnvelope(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
            handoff_refs=("factory.cognitive_runtime:handoff:previous",),
            task_refs=("runtime.task_market:task:task-1",),
        ),
        capability_invocations=(invocation,),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_receipt_ref="roles.kernel:commit:turn-1",
            receipt_refs=("factory.cognitive_runtime:receipt:previous",),
        ),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
        metadata={"turn_id": "turn-1"},
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
    cognitive_runtime = FakeCognitiveRuntimeCommitService()

    receipt = commit_role_state(request, cognitive_runtime_service=cognitive_runtime)

    assert receipt.ok is True
    assert receipt.commit_receipt_ref == "roles.kernel:commit:turn-1"
    assert receipt.runtime_receipt_refs == (
        "factory.cognitive_runtime:receipt:previous",
        "factory.cognitive_runtime:receipt:receipt-1",
    )
    assert receipt.handoff_pack_refs == ("factory.cognitive_runtime:handoff:handoff-1",)
    assert receipt.change_set_validation_ref == "factory.cognitive_runtime:change-set-validation:validation-1"
    assert len(cognitive_runtime.validations) == 1
    assert len(cognitive_runtime.receipts) == 1
    assert len(cognitive_runtime.handoffs) == 1

    validation_command = cognitive_runtime.validations[0]
    assert validation_command.workspace == "/repo"
    assert validation_command.changed_files == ("runtime/tasks/task-1.json",)
    assert validation_command.allowed_scope_paths == ("runtime/tasks",)
    assert validation_command.evidence_refs == ("audit.evidence:evt-1",)

    receipt_command = cognitive_runtime.receipts[0]
    assert receipt_command.workspace == "/repo"
    assert receipt_command.receipt_type == "role_state_commit"
    assert receipt_command.session_id == "session-1"
    assert receipt_command.run_id == "run-1"
    assert receipt_command.payload["request_id"] == "commit-request-1"
    assert receipt_command.payload["role_id"] == "pm"
    assert receipt_command.payload["changed_asset_refs"] == ("runtime.task_market:task:task-1",)
    assert receipt_command.payload["change_set_validation_ref"] == (
        "factory.cognitive_runtime:change-set-validation:validation-1"
    )
    assert receipt_command.turn_envelope["identity"]["role_id"] == "pm"
    assert receipt_command.turn_envelope["ledger_binding"]["commit_receipt_ref"] == "roles.kernel:commit:turn-1"
    assert "roles.kernel:commit:turn-1" in receipt_command.trace_refs
    assert "audit.evidence:evt-1" in receipt_command.trace_refs
    assert "factory.cognitive_runtime:change-set-validation:validation-1" in receipt_command.trace_refs

    handoff_command = cognitive_runtime.handoffs[0]
    assert handoff_command.workspace == "/repo"
    assert handoff_command.session_id == "session-1"
    assert handoff_command.reason == "task-market dispatch committed"
    assert handoff_command.turn_envelope["runtime_receipt_refs"] == (
        "factory.cognitive_runtime:receipt:previous",
        "factory.cognitive_runtime:receipt:receipt-1",
    )


def test_rehydrate_role_handoff_delegates_to_cognitive_runtime_public_contract() -> None:
    command_cls = getattr(runtime_contracts, "RehydrateRoleHandoffCommandV1", None)
    result_cls = getattr(runtime_contracts, "RoleHandoffRehydrationResultV1", None)
    rehydrate_role_handoff = getattr(runtime_service, "rehydrate_role_handoff", None)
    assert command_cls is not None
    assert result_cls is not None
    assert rehydrate_role_handoff is not None

    cognitive_runtime = FakeCognitiveRuntimeCommitService()
    command = command_cls(
        identity=_identity("director"),
        handoff_ref="factory.cognitive_runtime:handoff:handoff-1",
        target_role="director",
        target_session_id="session-director-1",
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:director-run-1",
            context_snapshot_ref="roles.session:context-snapshot:session-director-1",
            handoff_refs=("factory.cognitive_runtime:handoff:handoff-1",),
            task_refs=("runtime.task_market:task:task-1",),
        ),
    )

    result = rehydrate_role_handoff(command, cognitive_runtime_service=cognitive_runtime)

    assert isinstance(result, result_cls)
    assert result.ok is True
    assert result.handoff_ref == "factory.cognitive_runtime:handoff:handoff-1"
    assert result.rehydration_ref == "factory.cognitive_runtime:rehydration:rehydration-1"
    assert result.target_role == "director"
    assert result.target_session_id == "session-director-1"
    assert result.context_override["state_first_context_os"]["mode"] == "state_first_context_os.handoff_rehydrate"
    assert result.metadata_patch["handoff_rehydrated"] is True
    assert result.runtime_receipt_refs == ("factory.cognitive_runtime:receipt:handoff-1",)
    assert result.artifact_refs == ("roles.session:artifact:handoff-1",)
    assert result.episode_refs == ("roles.session:episode:handoff-1",)
    assert result.source_spans == ("ep-1:t1:t4",)
    assert len(cognitive_runtime.rehydrations) == 1

    rehydrate_command = cognitive_runtime.rehydrations[0]
    assert rehydrate_command.workspace == "/repo"
    assert rehydrate_command.handoff_id == "handoff-1"
    assert rehydrate_command.target_role == "director"
    assert rehydrate_command.target_session_id == "session-director-1"
    assert rehydrate_command.turn_envelope["identity"]["role_id"] == "director"
    assert rehydrate_command.turn_envelope["turn_context"]["handoff_refs"] == (
        "factory.cognitive_runtime:handoff:handoff-1",
    )
    assert rehydrate_command.metadata["handoff_ref"] == "factory.cognitive_runtime:handoff:handoff-1"
    assert rehydrate_command.metadata["role_payload_ref"] == "roles.runtime:typed-input:director-run-1"


def test_rehydrate_role_handoff_normalizes_owner_refs_from_raw_cognitive_runtime_ids() -> None:
    command_cls = getattr(runtime_contracts, "RehydrateRoleHandoffCommandV1", None)
    result_cls = getattr(runtime_contracts, "RoleHandoffRehydrationResultV1", None)
    rehydrate_role_handoff = getattr(runtime_service, "rehydrate_role_handoff", None)
    assert command_cls is not None
    assert result_cls is not None
    assert rehydrate_role_handoff is not None

    cognitive_runtime = FakeCognitiveRuntimeCommitService()
    cognitive_runtime.rehydrate_refs_are_raw = True
    command = command_cls(
        identity=_identity("director"),
        handoff_ref="factory.cognitive_runtime:handoff:handoff-1",
        target_role="director",
        target_session_id="session-director-1",
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:director-run-1",
            context_snapshot_ref="roles.session:context-snapshot:session-director-1",
            handoff_refs=("factory.cognitive_runtime:handoff:handoff-1",),
            task_refs=("runtime.task_market:task:task-1",),
        ),
    )

    result = rehydrate_role_handoff(command, cognitive_runtime_service=cognitive_runtime)

    assert isinstance(result, result_cls)
    assert result.ok is True
    assert result.runtime_receipt_refs == ("factory.cognitive_runtime:receipt:receipt-handoff-1",)
    assert result.artifact_refs == ("roles.session:artifact:artifact-handoff-1",)
    assert result.episode_refs == ("roles.session:episode:episode-handoff-1",)


def test_commit_role_state_rejects_failed_change_set_validation_without_receipt_or_handoff() -> None:
    envelope = RoleTurnEnvelope(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
            task_refs=("runtime.task_market:task:task-1",),
        ),
        capability_invocations=(),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_receipt_ref="roles.kernel:commit:turn-1",
        ),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
    )
    request = RoleStateCommitRequest(
        request_id="commit-request-invalid-change-set",
        envelope=envelope,
        changed_asset_refs=("runtime.task_market:task:task-1",),
        changed_files=("runtime/tasks/task-1.json",),
        allowed_scope_paths=("runtime/tasks",),
        evidence_refs=("audit.evidence:evt-1",),
    )
    cognitive_runtime = FakeCognitiveRuntimeCommitService(validation_ok=False)

    receipt = commit_role_state(request, cognitive_runtime_service=cognitive_runtime)

    assert receipt.ok is False
    assert receipt.status == "change_set_validation_failed"
    assert receipt.error_code == "validate_change_set_failed"
    assert receipt.error_message == "change set is outside role scope"
    assert len(cognitive_runtime.validations) == 1
    assert cognitive_runtime.receipts == []
    assert cognitive_runtime.handoffs == []


def test_commit_role_state_rejects_missing_kernel_commit_receipt_without_runtime_call() -> None:
    envelope = RoleTurnEnvelope(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
            task_refs=("runtime.task_market:task:task-1",),
        ),
        capability_invocations=(),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task:task-1"),
    )
    request = RoleStateCommitRequest(
        request_id="commit-request-missing",
        envelope=envelope,
        changed_asset_refs=("runtime.task_market:task:task-1",),
    )
    cognitive_runtime = FakeCognitiveRuntimeCommitService()

    receipt = commit_role_state(request, cognitive_runtime_service=cognitive_runtime)

    assert receipt.ok is False
    assert receipt.status == "rejected"
    assert receipt.error_code == "missing_commit_receipt_ref"
    assert cognitive_runtime.receipts == []
    assert cognitive_runtime.handoffs == []


def test_role_runtime_chain_assembly_keeps_phase5_refs_typed_and_ordered() -> None:
    steps = (
        RoleRuntimeChainStepRef(
            role_id="pm",
            stage="task_market_dispatch",
            capability_id="dispatch_task_to_market",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:pm-dispatch",
            owner_cell="runtime.task_market",
            command_contract="PublishTaskWorkItemCommandV1",
            result_ref="runtime.task_market:work-item:task-1",
            task_ref="runtime.task_market:task:task-1",
            status="pending_design",
        ),
        RoleRuntimeChainStepRef(
            role_id="chief_engineer",
            stage="blueprint",
            capability_id="generate_diff_specification",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:ce-blueprint",
            owner_cell="chief_engineer.blueprint",
            command_contract="GenerateTaskBlueprintCommandV1",
            result_ref="chief_engineer.blueprint:blueprint:bp-1",
            task_ref="runtime.task_market:task:task-1",
            handoff_refs=("factory.cognitive_runtime:handoff:ce-to-director",),
            receipt_refs=("factory.cognitive_runtime:receipt:ce-1",),
            status="generated",
        ),
        RoleRuntimeChainStepRef(
            role_id="director",
            stage="execution",
            capability_id="execute_director_task",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:director-execution",
            owner_cell="director.execution",
            command_contract="ExecuteDirectorTaskCommandV1",
            result_ref="director.execution:task:task-1",
            task_ref="runtime.task_market:task:task-1",
            evidence_refs=("audit.evidence:director:task-1",),
            handoff_refs=("factory.cognitive_runtime:handoff:director-to-qa",),
            receipt_refs=("factory.cognitive_runtime:receipt:director-1",),
            status="completed",
        ),
        RoleRuntimeChainStepRef(
            role_id="qa",
            stage="audit",
            capability_id="issue_audit_verdict",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:qa-audit",
            owner_cell="qa.audit_verdict",
            command_contract="RunQaAuditCommandV1",
            result_ref="qa.audit_verdict:verdict:task-1",
            task_ref="runtime.task_market:task:task-1",
            evidence_refs=("audit.evidence:qa:task-1",),
            receipt_refs=("factory.cognitive_runtime:receipt:qa-1",),
            status="PASS",
        ),
    )

    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-1",
            workspace="/repo",
            run_id="run-1",
            task_id="task-1",
            steps=steps,
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            runtime_projection_refs=("runtime.projection:runtime:run-1",),
            audit_evidence_refs=("audit.evidence:truth-log:task-1",),
            metadata={"phase": "phase5"},
        )
    )

    assert isinstance(result, RoleRuntimeChainAssemblyResultV1)
    assert result.ok is True
    assert result.chain is not None
    assert result.chain_ref == "roles.runtime:chain:phase5-chain-1"
    assert tuple(step.role_id for step in result.chain.steps) == ("pm", "chief_engineer", "director", "qa")
    assert result.chain.task_market_refs == ("runtime.task_market:task:task-1",)
    assert result.chain.audit_evidence_refs == (
        "audit.evidence:truth-log:task-1",
        "audit.evidence:director:task-1",
        "audit.evidence:qa:task-1",
    )
    assert result.chain.turn_ledger_ref == "roles.kernel:turn-ledger:run-1"
    assert result.chain.runtime_projection_refs == ("runtime.projection:runtime:run-1",)
    assert result.chain.handoff_refs == (
        "factory.cognitive_runtime:handoff:ce-to-director",
        "factory.cognitive_runtime:handoff:director-to-qa",
    )
    assert result.chain.runtime_receipt_refs == (
        "factory.cognitive_runtime:receipt:ce-1",
        "factory.cognitive_runtime:receipt:director-1",
        "factory.cognitive_runtime:receipt:qa-1",
    )
    assert result.chain.metadata["phase"] == "phase5"


def test_role_runtime_chain_assembly_rejects_missing_required_role() -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-missing-qa",
            workspace="/repo",
            run_id="run-1",
            task_id="task-1",
            steps=(
                RoleRuntimeChainStepRef(
                    role_id="pm",
                    stage="task_market_dispatch",
                    capability_id="dispatch_task_to_market",
                    capability_fingerprint_ref="roles.runtime:capability-fingerprint:pm-dispatch",
                    owner_cell="runtime.task_market",
                    command_contract="PublishTaskWorkItemCommandV1",
                    result_ref="runtime.task_market:work-item:task-1",
                    task_ref="runtime.task_market:task:task-1",
                ),
                RoleRuntimeChainStepRef(
                    role_id="chief_engineer",
                    stage="blueprint",
                    capability_id="generate_diff_specification",
                    capability_fingerprint_ref="roles.runtime:capability-fingerprint:ce-blueprint",
                    owner_cell="chief_engineer.blueprint",
                    command_contract="GenerateTaskBlueprintCommandV1",
                    result_ref="chief_engineer.blueprint:blueprint:bp-1",
                    task_ref="runtime.task_market:task:task-1",
                ),
                RoleRuntimeChainStepRef(
                    role_id="director",
                    stage="execution",
                    capability_id="execute_director_task",
                    capability_fingerprint_ref="roles.runtime:capability-fingerprint:director-execution",
                    owner_cell="director.execution",
                    command_contract="ExecuteDirectorTaskCommandV1",
                    result_ref="director.execution:task:task-1",
                    task_ref="runtime.task_market:task:task-1",
                ),
            ),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.missing_roles == ("qa",)
    assert result.error_code == "missing_required_chain_roles"


