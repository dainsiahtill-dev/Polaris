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
            risks=("legacy adapter drift",),
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


def _architect_validation_runtime_object() -> RoleRuntimeObject:
    spec = runtime_contracts.get_builtin_role_runtime_spec("architect")
    return spec.instantiate(
        identity=_identity("architect"),
        profile_binding=_profile_binding("architect"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:architect-run"),
        policy_fingerprint="architect-policy",
        capability_id="validate_cell_boundary_change",
    )


def _pm_mount_table() -> RoleAssetMountTable:
    return RoleAssetMountTable(
        mounts=(
            RoleAssetMount(
                mount_name="ProjectFunctionIndex",
                asset_ref=RoleAssetRef(
                    asset_id="project-function-index",
                    owner_cell="context.catalog",
                    contract_name="SearchCellsQueryV1",
                    ref="context.catalog:project-function-index",
                ),
                access_mode="read",
            ),
            RoleAssetMount(
                mount_name="TaskGraph",
                asset_ref=RoleAssetRef(
                    asset_id="task-graph",
                    owner_cell="runtime.task_market",
                    contract_name="QueryTaskMarketStatusV1",
                    ref="runtime.task_market:task-graph",
                ),
                access_mode="read",
            ),
            RoleAssetMount(
                mount_name="RuntimeProjectionState",
                asset_ref=RoleAssetRef(
                    asset_id="runtime-projection-state",
                    owner_cell="runtime.projection",
                    contract_name="RuntimeProjectionQueryV1",
                    ref="runtime.projection:runtime",
                ),
                access_mode="read",
            ),
            RoleAssetMount(
                mount_name="OpenLoopRegistry",
                asset_ref=RoleAssetRef(
                    asset_id="open-loop-registry",
                    owner_cell="runtime.task_market",
                    contract_name="QueryTaskMarketStatusV1",
                    ref="runtime.task_market:open-loops",
                    asset_kind="open_loop_registry",
                    metadata={
                        "evidence_owner_cell": "audit.evidence",
                        "evidence_ref": "audit.evidence:open-loop-registry",
                    },
                ),
                access_mode="read",
            ),
        )
    )


def _capability_ports() -> RoleCapabilityPorts:
    return RoleCapabilityPorts(
        capabilities=(
            RoleCapabilityDescriptor(
                capability_id="dispatch_task_to_market",
                owner_cell="runtime.task_market",
                contract_name="PublishTaskWorkItemCommandV1",
                effect="task_market.publish",
                allowed_roles=("pm",),
                endpoint_ref="polaris.cells.runtime.task_market.public.service.publish_work_item",
            ),
            RoleCapabilityDescriptor(
                capability_id="record_runtime_receipt",
                owner_cell="factory.cognitive_runtime",
                contract_name="RecordRuntimeReceiptCommandV1",
                effect="runtime_receipt.record",
                allowed_roles=("pm", "chief_engineer", "architect", "qa", "director"),
                endpoint_ref="polaris.cells.factory.cognitive_runtime.public.service.record_runtime_receipt",
            ),
        )
    )


def _mixed_role_capability_ports() -> RoleCapabilityPorts:
    return RoleCapabilityPorts(
        capabilities=(
            RoleCapabilityDescriptor(
                capability_id="dispatch_task_to_market",
                owner_cell="runtime.task_market",
                contract_name="PublishTaskWorkItemCommandV1",
                effect="task_market.publish",
                allowed_roles=("pm",),
            ),
            RoleCapabilityDescriptor(
                capability_id="invoke_container_pytest",
                owner_cell="factory.verification_guard",
                contract_name="VerifyCompletionCommandV1",
                effect="process.spawn:qa/pytest",
                allowed_roles=("qa",),
            ),
        )
    )


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


def _role_runtime_effect_is_allowed(effect: str, allowed_effects: tuple[str, ...]) -> bool:
    for allowed_effect in allowed_effects:
        if allowed_effect == effect:
            return True
        if allowed_effect.endswith(":*"):
            base_effect = allowed_effect[:-2]
            if effect == base_effect or effect.startswith(f"{base_effect}:"):
                return True
        elif allowed_effect.endswith("*") and effect.startswith(allowed_effect[:-1]):
            return True
    return False


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


def _commit_envelope() -> RoleTurnEnvelope:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-commit-test",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref="runtime.task_market:task:task-1",
        fingerprint_ref="a" * 64,
    )
    return RoleTurnEnvelope(
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


def test_role_runtime_chain_assembly_rejects_required_roles_out_of_order() -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-out-of-order",
            workspace="/repo",
            run_id="run-1",
            task_id="task-1",
            steps=(
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
                    role_id="director",
                    stage="execution",
                    capability_id="execute_director_task",
                    capability_fingerprint_ref="roles.runtime:capability-fingerprint:director-execution",
                    owner_cell="director.execution",
                    command_contract="ExecuteDirectorTaskCommandV1",
                    result_ref="director.execution:task:task-1",
                    task_ref="runtime.task_market:task:task-1",
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
                ),
            ),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "chain_required_roles_out_of_order"
    assert result.metadata["expected_order"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["actual_order"] == ("chief_engineer", "pm", "director", "qa")


def test_role_runtime_chain_assembly_rejects_full_phase5_without_runtime_projection_ref() -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-missing-projection",
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
                RoleRuntimeChainStepRef(
                    role_id="qa",
                    stage="audit",
                    capability_id="issue_audit_verdict",
                    capability_fingerprint_ref="roles.runtime:capability-fingerprint:qa-audit",
                    owner_cell="qa.audit_verdict",
                    command_contract="RunQaAuditCommandV1",
                    result_ref="qa.audit_verdict:verdict:task-1",
                    task_ref="runtime.task_market:task:task-1",
                ),
            ),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "missing_runtime_projection_ref"
    assert result.metadata["required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["required_owner_cell"] == "runtime.projection"


def test_role_runtime_chain_assembly_rejects_full_phase5_without_audit_evidence_ref() -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-missing-audit-evidence",
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
                RoleRuntimeChainStepRef(
                    role_id="qa",
                    stage="audit",
                    capability_id="issue_audit_verdict",
                    capability_fingerprint_ref="roles.runtime:capability-fingerprint:qa-audit",
                    owner_cell="qa.audit_verdict",
                    command_contract="RunQaAuditCommandV1",
                    result_ref="qa.audit_verdict:verdict:task-1",
                    task_ref="runtime.task_market:task:task-1",
                ),
            ),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            runtime_projection_refs=("runtime.projection:runtime:run-1",),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "missing_audit_evidence_ref"
    assert result.metadata["required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["required_owner_cell"] == "audit.evidence"


def _phase5_chain_steps(
    *,
    include_handoff: bool = True,
    include_director_handoff: bool = True,
    include_director_evidence: bool = True,
    include_qa_evidence: bool = True,
    include_chief_engineer_receipt: bool = True,
    include_director_receipt: bool = True,
    include_receipt: bool = True,
) -> tuple[RoleRuntimeChainStepRef, ...]:
    return (
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
            handoff_refs=("factory.cognitive_runtime:handoff:ce-to-director",) if include_handoff else (),
            receipt_refs=("factory.cognitive_runtime:receipt:ce-1",) if include_chief_engineer_receipt else (),
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
            evidence_refs=("audit.evidence:director:task-1",) if include_director_evidence else (),
            handoff_refs=("factory.cognitive_runtime:handoff:director-to-qa",) if include_director_handoff else (),
            receipt_refs=("factory.cognitive_runtime:receipt:director-1",) if include_director_receipt else (),
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
            evidence_refs=("audit.evidence:qa:task-1",) if include_qa_evidence else (),
            receipt_refs=("factory.cognitive_runtime:receipt:qa-1",) if include_receipt else (),
        ),
    )


def _valid_phase5_chain_command(chain_id: str = "phase5-chain-valid") -> AssembleRoleRuntimeChainCommandV1:
    return AssembleRoleRuntimeChainCommandV1(
        chain_id=chain_id,
        workspace="/repo",
        run_id="run-1",
        task_id="task-1",
        steps=_phase5_chain_steps(),
        turn_ledger_ref="roles.kernel:turn-ledger:run-1",
        runtime_projection_refs=("runtime.projection:runtime:run-1",),
        audit_evidence_refs=("audit.evidence:truth-log:task-1",),
    )


@pytest.mark.parametrize(
    ("missing_ref", "expected_error_code", "expected_owner_cell"),
    (
        ("handoff", "missing_handoff_ref", "factory.cognitive_runtime"),
        ("runtime_receipt", "missing_runtime_receipt_ref", "factory.cognitive_runtime"),
    ),
)
def test_role_runtime_chain_assembly_rejects_full_phase5_without_cognitive_runtime_ref(
    missing_ref: str,
    expected_error_code: str,
    expected_owner_cell: str,
) -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id=f"phase5-chain-missing-{missing_ref}",
            workspace="/repo",
            run_id="run-1",
            task_id="task-1",
            steps=_phase5_chain_steps(
                include_handoff=missing_ref != "handoff",
                include_director_handoff=missing_ref != "handoff",
                include_chief_engineer_receipt=missing_ref != "runtime_receipt",
                include_director_receipt=missing_ref != "runtime_receipt",
                include_receipt=missing_ref != "runtime_receipt",
            ),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            runtime_projection_refs=("runtime.projection:runtime:run-1",),
            audit_evidence_refs=("audit.evidence:truth-log:task-1",),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == expected_error_code
    assert result.metadata["required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["required_owner_cell"] == expected_owner_cell
    assert result.metadata["missing_ref"] == missing_ref


def test_role_runtime_chain_assembly_rejects_full_phase5_without_director_qa_handoff_ref() -> None:
    result = assemble_role_runtime_chain(
        dataclasses.replace(
            _valid_phase5_chain_command("phase5-chain-missing-director-qa-handoff"),
            steps=_phase5_chain_steps(include_director_handoff=False),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "missing_phase5_role_handoff_ref"
    assert result.metadata["required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["required_owner_cell"] == "factory.cognitive_runtime"
    assert result.metadata["missing_role"] == "director"
    assert result.metadata["required_handoff_roles"] == ("chief_engineer", "director")


@pytest.mark.parametrize(
    ("missing_role", "step_kwargs"),
    (
        ("chief_engineer", {"include_chief_engineer_receipt": False}),
        ("director", {"include_director_receipt": False}),
        ("qa", {"include_receipt": False}),
    ),
)
def test_role_runtime_chain_assembly_rejects_full_phase5_without_role_runtime_receipt_ref(
    missing_role: str,
    step_kwargs: dict[str, bool],
) -> None:
    result = assemble_role_runtime_chain(
        dataclasses.replace(
            _valid_phase5_chain_command(f"phase5-chain-missing-{missing_role}-receipt"),
            steps=_phase5_chain_steps(**step_kwargs),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "missing_phase5_role_runtime_receipt_ref"
    assert result.metadata["required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["required_owner_cell"] == "factory.cognitive_runtime"
    assert result.metadata["missing_role"] == missing_role
    assert result.metadata["required_receipt_roles"] == ("chief_engineer", "director", "qa")


@pytest.mark.parametrize(
    ("missing_role", "step_kwargs"),
    (
        ("director", {"include_director_evidence": False}),
        ("qa", {"include_qa_evidence": False}),
    ),
)
def test_role_runtime_chain_assembly_rejects_full_phase5_without_role_audit_evidence_ref(
    missing_role: str,
    step_kwargs: dict[str, bool],
) -> None:
    result = assemble_role_runtime_chain(
        dataclasses.replace(
            _valid_phase5_chain_command(f"phase5-chain-missing-{missing_role}-evidence"),
            steps=_phase5_chain_steps(**step_kwargs),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "missing_phase5_role_audit_evidence_ref"
    assert result.metadata["required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["required_owner_cell"] == "audit.evidence"
    assert result.metadata["missing_role"] == missing_role
    assert result.metadata["required_evidence_roles"] == ("director", "qa")


def test_role_runtime_chain_assembly_rejects_full_phase5_required_role_downgrade() -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-downgraded-required-roles",
            workspace="/repo",
            run_id="run-1",
            task_id="task-1",
            steps=_phase5_chain_steps(),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            runtime_projection_refs=("runtime.projection:runtime:run-1",),
            audit_evidence_refs=("audit.evidence:truth-log:task-1",),
            required_roles=("pm",),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "required_roles_cannot_downgrade_full_phase5_chain"
    assert result.metadata["expected_required_roles"] == ("pm", "chief_engineer", "director", "qa")
    assert result.metadata["actual_required_roles"] == ("pm",)


def test_role_runtime_chain_assembly_rejects_invalid_turn_ledger_ref_as_typed_result() -> None:
    result = assemble_role_runtime_chain(
        dataclasses.replace(
            _valid_phase5_chain_command("phase5-chain-invalid-turn-ledger"),
            turn_ledger_ref="roles.runtime:turn-ledger:run-1",
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "invalid_turn_ledger_ref"
    assert result.metadata["required_owner_cell"] == "roles.kernel"
    assert result.metadata["invalid_ref"] == "roles.runtime:turn-ledger:run-1"


def test_role_runtime_chain_assembly_rejects_invalid_runtime_projection_ref_as_typed_result() -> None:
    result = assemble_role_runtime_chain(
        dataclasses.replace(
            _valid_phase5_chain_command("phase5-chain-invalid-runtime-projection"),
            runtime_projection_refs=("roles.runtime:projection:run-1",),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "invalid_runtime_projection_ref"
    assert result.metadata["required_owner_cell"] == "runtime.projection"
    assert result.metadata["invalid_ref"] == "roles.runtime:projection:run-1"


def test_role_runtime_chain_assembly_rejects_invalid_audit_evidence_ref_as_typed_result() -> None:
    result = assemble_role_runtime_chain(
        dataclasses.replace(
            _valid_phase5_chain_command("phase5-chain-invalid-audit-evidence"),
            audit_evidence_refs=("roles.runtime:evidence:task-1",),
        )
    )

    assert result.ok is False
    assert result.chain is None
    assert result.error_code == "invalid_audit_evidence_ref"
    assert result.metadata["required_owner_cell"] == "audit.evidence"
    assert result.metadata["invalid_ref"] == "roles.runtime:evidence:task-1"


def test_role_runtime_chain_assembly_aggregates_capability_fingerprint_refs() -> None:
    result = assemble_role_runtime_chain(
        AssembleRoleRuntimeChainCommandV1(
            chain_id="phase5-chain-fingerprints",
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
            ),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            required_roles=("pm",),
        )
    )

    assert result.ok is True
    assert result.chain is not None
    assert result.chain.capability_fingerprint_refs == ("roles.runtime:capability-fingerprint:pm-dispatch",)


def test_role_runtime_chain_step_requires_task_market_anchor_ref() -> None:
    with pytest.raises(ValueError, match="chain step must include task_ref or work_item_ref"):
        RoleRuntimeChainStepRef(
            role_id="pm",
            stage="task_market_dispatch",
            capability_id="dispatch_task_to_market",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:pm-dispatch",
            owner_cell="runtime.task_market",
            command_contract="PublishTaskWorkItemCommandV1",
            result_ref="runtime.task_market:work-item:task-1",
        )


def test_role_runtime_chain_step_rejects_legacy_task_ref_shape() -> None:
    with pytest.raises(ValueError, match=r"task_ref must use runtime\.task_market:task:<task_id>"):
        RoleRuntimeChainStepRef(
            role_id="qa",
            stage="audit",
            capability_id="issue_audit_verdict",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:qa-audit",
            owner_cell="qa.audit_verdict",
            command_contract="RunQaAuditCommandV1",
            result_ref="qa.audit_verdict:verdict:task-1",
            task_ref="runtime.task_market:task-1",
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
def test_role_runtime_chain_step_rejects_role_runtime_or_kernelone_role_owners(owner_cell: str) -> None:
    with pytest.raises(ValueError, match="chain step owner_cell must be a target public Cell"):
        RoleRuntimeChainStepRef(
            role_id="pm",
            stage="task_market_dispatch",
            capability_id="dispatch_task_to_market",
            capability_fingerprint_ref="roles.runtime:capability-fingerprint:pm-dispatch",
            owner_cell=owner_cell,
            command_contract="PublishTaskWorkItemCommandV1",
            result_ref="runtime.task_market:work-item:task-1",
        )


@pytest.mark.parametrize(
    "payload, expected_error",
    (
        (
            {"capability_fingerprint_ref": "factory.cognitive_runtime:capability-fingerprint:qa-audit"},
            r"capability_fingerprint_ref must point to roles\.runtime",
        ),
        (
            {"result_ref": "runtime.task_market:verdict:task-1"},
            r"result_ref must point to owner_cell",
        ),
        (
            {"task_ref": "roles.runtime:task:task-1"},
            r"task_ref must use runtime\.task_market:task:<task_id>",
        ),
        (
            {"work_item_ref": "roles.profile:work-item:task-1"},
            r"chain step task/work item refs must point to runtime\.task_market",
        ),
        (
            {"evidence_refs": ("roles.kernel:evidence:task-1",)},
            r"evidence_refs must point to audit\.evidence",
        ),
        (
            {"receipt_refs": ("roles.kernel:receipt:task-1",)},
            r"receipt_refs must point to factory\.cognitive_runtime",
        ),
        (
            {"handoff_refs": ("roles.session:handoff:task-1",)},
            r"handoff_refs must point to factory\.cognitive_runtime",
        ),
    ),
)
def test_role_runtime_chain_step_rejects_refs_outside_source_of_truth(
    payload: _ChainStepRefKwargs,
    expected_error: str,
) -> None:
    base_payload: _ChainStepRefKwargs = {
        "role_id": "qa",
        "stage": "audit",
        "capability_id": "issue_audit_verdict",
        "capability_fingerprint_ref": "roles.runtime:capability-fingerprint:qa-audit",
        "owner_cell": "qa.audit_verdict",
        "command_contract": "RunQaAuditCommandV1",
        "result_ref": "qa.audit_verdict:verdict:task-1",
        "task_ref": "runtime.task_market:task:task-1",
    }
    base_payload.update(payload)

    with pytest.raises(ValueError, match=expected_error):
        RoleRuntimeChainStepRef(**base_payload)


@pytest.mark.parametrize(
    ("missing_field", "error_message"),
    (
        ("task_market_refs", "task_market_refs must include step task/work item refs"),
        ("audit_evidence_refs", "audit_evidence_refs must include step evidence refs"),
        ("capability_fingerprint_refs", "capability_fingerprint_refs must include step capability fingerprint refs"),
        ("handoff_refs", "handoff_refs must include step handoff refs"),
        ("runtime_receipt_refs", "runtime_receipt_refs must include step receipt refs"),
    ),
)
def test_role_runtime_chain_envelope_rejects_missing_step_aggregate_refs(
    missing_field: _ChainEnvelopeAggregateField,
    error_message: str,
) -> None:
    step = RoleRuntimeChainStepRef(
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
        handoff_refs=("factory.cognitive_runtime:handoff:qa-to-audit",),
    )
    aggregate_refs: _ChainEnvelopeAggregateRefs = {
        "task_market_refs": ("runtime.task_market:task:task-1",),
        "audit_evidence_refs": ("audit.evidence:qa:task-1",),
        "capability_fingerprint_refs": ("roles.runtime:capability-fingerprint:qa-audit",),
        "handoff_refs": ("factory.cognitive_runtime:handoff:qa-to-audit",),
        "runtime_receipt_refs": ("factory.cognitive_runtime:receipt:qa-1",),
    }
    aggregate_refs[missing_field] = ()

    with pytest.raises(ValueError, match=error_message):
        RoleRuntimeChainEnvelope(
            chain_id="phase5-chain-inconsistent",
            workspace="/repo",
            run_id="run-1",
            task_id="task-1",
            steps=(step,),
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            **aggregate_refs,
        )


@pytest.mark.parametrize(
    "envelope_overrides, expected_error",
    (
        (
            {"turn_ledger_ref": "roles.runtime:turn-ledger:run-1"},
            r"turn_ledger_ref must point to roles\.kernel",
        ),
        (
            {"task_market_refs": ("runtime.task_market:task:task-1", "roles.kernel:task:task-1")},
            r"task_market_refs must point to runtime\.task_market",
        ),
        (
            {"audit_evidence_refs": ("audit.evidence:qa:task-1", "roles.kernel:evidence:qa")},
            r"audit_evidence_refs must point to audit\.evidence",
        ),
        (
            {"runtime_projection_refs": ("runtime.projection:runtime:run-1", "context.engine:snapshot-1")},
            r"runtime_projection_refs must point to runtime\.projection",
        ),
        (
            {
                "capability_fingerprint_refs": (
                    "roles.runtime:capability-fingerprint:qa-audit",
                    "factory.cognitive_runtime:capability-fingerprint:qa-audit",
                )
            },
            r"capability_fingerprint_refs must point to roles\.runtime",
        ),
        (
            {
                "handoff_refs": (
                    "factory.cognitive_runtime:handoff:qa-to-audit",
                    "roles.session:handoff:qa-to-audit",
                )
            },
            r"handoff_refs must point to factory\.cognitive_runtime",
        ),
        (
            {
                "runtime_receipt_refs": (
                    "factory.cognitive_runtime:receipt:qa-1",
                    "roles.kernel:receipt:qa-1",
                )
            },
            r"runtime_receipt_refs must point to factory\.cognitive_runtime",
        ),
    ),
)
def test_role_runtime_chain_envelope_rejects_aggregate_refs_outside_source_of_truth(
    envelope_overrides: _ChainEnvelopeKwargs,
    expected_error: str,
) -> None:
    step = RoleRuntimeChainStepRef(
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
        handoff_refs=("factory.cognitive_runtime:handoff:qa-to-audit",),
    )
    envelope_payload: _ChainEnvelopeKwargs = {
        "chain_id": "phase5-chain-bad-owner",
        "workspace": "/repo",
        "run_id": "run-1",
        "task_id": "task-1",
        "steps": (step,),
        "turn_ledger_ref": "roles.kernel:turn-ledger:run-1",
        "task_market_refs": ("runtime.task_market:task:task-1",),
        "audit_evidence_refs": ("audit.evidence:qa:task-1",),
        "runtime_projection_refs": ("runtime.projection:runtime:run-1",),
        "capability_fingerprint_refs": ("roles.runtime:capability-fingerprint:qa-audit",),
        "handoff_refs": ("factory.cognitive_runtime:handoff:qa-to-audit",),
        "runtime_receipt_refs": ("factory.cognitive_runtime:receipt:qa-1",),
    }
    envelope_payload.update(envelope_overrides)

    with pytest.raises(ValueError, match=expected_error):
        RoleRuntimeChainEnvelope(**envelope_payload)


def test_pm_dispatch_capability_invokes_task_market_publish_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-dispatch-1",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    task_market = FakeTaskMarketService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "trace_id": "trace-1",
                "run_id": "run-1",
                "task_id": "pm-task-1",
                "stage": "pending_design",
                "priority": "high",
                "payload": {"objective": "publish a typed work item"},
                "metadata": {"plan_id": "plan-1"},
                "depends_on": ("dep-1",),
            },
        ),
        task_market_service=task_market,
    )

    assert isinstance(result, RoleCapabilityInvocationResultV1)
    assert result.ok is True
    assert result.allowed is True
    assert result.command_contract == "PublishTaskWorkItemCommandV1"
    assert result.task_id == "pm-task-1"
    assert result.status == "pending"
    assert result.payload_ref == "runtime.task_market:work-item:pm-task-1"
    assert len(task_market.published) == 1

    publish_command = task_market.published[0]
    assert isinstance(publish_command, PublishTaskWorkItemCommandV1)
    assert publish_command.workspace == "/repo"
    assert publish_command.source_role == "pm"
    assert publish_command.priority == "high"
    assert publish_command.payload == {"objective": "publish a typed work item"}
    assert publish_command.metadata["role_invocation_id"] == "invoke-dispatch-1"
    assert publish_command.metadata["role_fingerprint_ref"] == runtime_object.capability_fingerprint.fingerprint
    assert publish_command.metadata["turn_ledger_ref"] == "roles.kernel:turn-ledger:pm-run"
    assert publish_command.metadata["typed_input_ref"] == runtime_object.turn_context.typed_input_ref
    assert publish_command.metadata["context_snapshot_ref"] == runtime_object.turn_context.context_snapshot_ref
    assert publish_command.metadata["turn_task_refs"] == runtime_object.turn_context.task_refs
    assert publish_command.metadata["profile_ref"] == runtime_object.profile_binding.profile_ref
    assert publish_command.metadata["asset_refs"] == {
        "project_function_index": "context.catalog:project-function-index",
        "task_graph": "runtime.task_market:task-graph",
        "runtime_projection_state": "runtime.projection:runtime-status",
        "open_loop_registry": "runtime.task_market:open-loops",
    }
    assert publish_command.depends_on == ("dep-1",)


def test_capability_invocation_rejects_payload_ref_outside_current_turn_context() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )
    task_market = FakeTaskMarketService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-dispatch-outside-turn-1",
                capability_id="dispatch_task_to_market",
                role_id="pm",
                command_contract="PublishTaskWorkItemCommandV1",
                payload_ref="roles.runtime:typed-input:foreign-turn",
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "trace_id": "trace-foreign",
                "run_id": "run-1",
                "task_id": "pm-task-foreign",
                "stage": "pending_design",
                "payload": {"objective": "must not publish"},
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "payload_ref_outside_turn_context"
    assert result.metadata["turn_typed_input_ref"] == runtime_object.turn_context.typed_input_ref
    assert task_market.published == []


def test_non_pm_dispatch_capability_is_structurally_denied_without_task_market_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
    )
    task_market = FakeTaskMarketService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-denied-1",
                capability_id="dispatch_task_to_market",
                role_id="chief_engineer",
                command_contract="PublishTaskWorkItemCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "trace_id": "trace-1",
                "run_id": "run-1",
                "task_id": "ce-task-1",
                "stage": "pending_design",
                "payload": {"objective": "illegal dispatch"},
            },
        ),
        task_market_service=task_market,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "capability_not_mounted"
    assert "dispatch_task_to_market" in (result.error_message or "")
    assert task_market.published == []


def test_pm_evaluate_critical_path_reads_task_market_status_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
        capability_id="evaluate_critical_path",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-critical-path-1",
        capability_id="evaluate_critical_path",
        role_id="pm",
        command_contract="QueryTaskMarketStatusV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    task_market = FakeTaskMarketService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={"stage": "pending_exec", "limit": 50, "include_payload": True},
        ),
        task_market_service=task_market,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "runtime.task_market"
    assert result.command_contract == "QueryTaskMarketStatusV1"
    assert result.status == "EVALUATED"
    assert result.result_ref == "runtime.task_market:critical-path:invoke-critical-path-1"
    assert result.metadata["total_tasks"] == 3
    assert result.metadata["blocked_task_ids"] == ("task-c",)
    assert result.metadata["open_task_ids"] == ("task-a", "task-b", "task-c")
    assert result.metadata["dependency_edges"] == (
        {"task_id": "task-a", "depends_on": ("task-b",)},
        {"task_id": "task-c", "depends_on": ("task-a", "task-b")},
    )
    assert result.metadata["failed_stages"] == (
        {"task_id": "task-c", "stage": "pending_exec", "reason": "lease expired"},
    )
    assert result.metadata["projection_refs"] == ("runtime.projection:task:task-a",)
    assert result.metadata["asset_refs"]["task_graph"] == "runtime.task_market:task-graph"
    assert result.metadata["asset_refs"]["runtime_projection_state"] == "runtime.projection:runtime-status"
    assert result.metadata["asset_refs"]["open_loop_registry"] == "runtime.task_market:open-loops"
    assert len(task_market.queried) == 1
    assert task_market.queried[0].workspace == "/repo"
    assert task_market.queried[0].stage == "pending_exec"
    assert task_market.queried[0].include_payload is True


def test_pm_project_runtime_status_invokes_runtime_projection_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
        capability_id="project_runtime_status",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-runtime-projection-1",
        capability_id="project_runtime_status",
        role_id="pm",
        command_contract="RuntimeProjectionQueryV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    projection = FakeRuntimeProjectionService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={"scope": "runtime"},
        ),
        runtime_projection_service=projection,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "runtime.projection"
    assert result.command_contract == "RuntimeProjectionQueryV1"
    assert result.status == "PROJECTED"
    assert result.result_ref == "runtime.projection:runtime:invoke-runtime-projection-1"
    assert result.metadata["projection"]["completed_task_count"] == 4
    assert len(projection.queries) == 1
    assert projection.queries[0].scope == "runtime"


def test_director_execute_capability_invokes_director_execution_public_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("director")
    runtime_object = spec.instantiate(
        identity=_identity("director"),
        profile_binding=_profile_binding("director"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:director-run"),
        policy_fingerprint="director-policy",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-director-exec-1",
        capability_id="execute_director_task",
        role_id="director",
        command_contract="ExecuteDirectorTaskCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    director_execution = FakeDirectorExecutionService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "task_id": "director-task-1",
                "run_id": "run-1",
                "instruction": "Apply approved CE diff specification",
                "attempt": 2,
                "metadata": {"command": "python -m pytest -q"},
            },
        ),
        director_execution_service=director_execution,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "director.execution"
    assert result.command_contract == "ExecuteDirectorTaskCommandV1"
    assert result.task_id == "director-task-1"
    assert result.status == "completed"
    assert result.result_ref == "director.execution:task:director-task-1"
    assert result.evidence_refs == ("audit.evidence:path:runtime/evidence/director-task-1.jsonl",)
    assert result.metadata["director_status"] == "completed"
    assert result.metadata["evidence_paths"] == ("runtime/evidence/director-task-1.jsonl",)
    assert result.metadata["audit_evidence_refs"] == ("audit.evidence:path:runtime/evidence/director-task-1.jsonl",)
    assert result.metadata["asset_refs"] == {
        "execution_task": "runtime.task_market:director/execution-task",
        "director_execution_state": "director.execution:runtime/state",
        "director_evidence_trail": "audit.evidence:director-execution",
    }
    assert len(director_execution.executed) == 1

    director_command = director_execution.executed[0]
    assert isinstance(director_command, ExecuteDirectorTaskCommandV1)
    assert director_command.workspace == "/repo"
    assert director_command.task_id == "director-task-1"
    assert director_command.run_id == "run-1"
    assert director_command.instruction == "Apply approved CE diff specification"
    assert director_command.attempt == 2
    assert director_command.metadata["command"] == "python -m pytest -q"
    assert director_command.metadata["role_invocation_id"] == "invoke-director-exec-1"
    assert director_command.metadata["role_fingerprint_ref"] == runtime_object.capability_fingerprint.fingerprint
    assert director_command.metadata["role_capability_id"] == "execute_director_task"
    assert director_command.metadata["asset_refs"]["director_execution_state"] == "director.execution:runtime/state"


def test_chief_engineer_generate_diff_capability_invokes_blueprint_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-blueprint-1",
        capability_id="generate_diff_specification",
        role_id="chief_engineer",
        command_contract="GenerateTaskBlueprintCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    blueprint_service = FakeBlueprintService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "task_id": "ce-task-1",
                "run_id": "run-1",
                "objective": "Generate a typed diff specification",
                "constraints": {"cell": "roles.runtime", "effect": "blueprint.generate"},
                "context": {
                    "target_files": ["src/backend/polaris/cells/roles/runtime/public/service.py"],
                    "acceptance_criteria": ["Role runtime calls blueprint public contract"],
                    "execution_checklist": ["Build GenerateTaskBlueprintCommandV1"],
                },
            },
        ),
        blueprint_service=blueprint_service,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "chief_engineer.blueprint"
    assert result.command_contract == "GenerateTaskBlueprintCommandV1"
    assert result.task_id == "ce-task-1"
    assert result.status == "generated"
    assert result.result_ref == "chief_engineer.blueprint:blueprint:bp-1"
    assert result.metadata["blueprint_path"] == "runtime/blueprints/bp-1.json"
    assert result.metadata["asset_refs"] == {
        "blueprint_database": "chief_engineer.blueprint:runtime/blueprints",
        "arch_constraint_memo": "chief_engineer.blueprint:arch-constraint-memo",
        "diff_map_archive": "chief_engineer.blueprint:diff-map-archive",
    }
    assert result.metadata["diff_map_archive_ref"] == "chief_engineer.blueprint:diff-map-archive:bp-1"
    assert result.metadata["arch_memo_ref"] == "chief_engineer.blueprint:arch-constraint-memo:bp-1"
    assert len(blueprint_service.generated) == 1

    blueprint_command = blueprint_service.generated[0]
    assert isinstance(blueprint_command, GenerateTaskBlueprintCommandV1)
    assert blueprint_command.workspace == "/repo"
    assert blueprint_command.task_id == "ce-task-1"
    assert blueprint_command.run_id == "run-1"
    assert blueprint_command.objective == "Generate a typed diff specification"
    assert blueprint_command.constraints == {"cell": "roles.runtime", "effect": "blueprint.generate"}
    assert blueprint_command.context["role_invocation_id"] == "invoke-blueprint-1"
    assert blueprint_command.context["role_capability_id"] == "generate_diff_specification"
    assert blueprint_command.context["asset_refs"] == {
        "blueprint_database": "chief_engineer.blueprint:runtime/blueprints",
        "arch_constraint_memo": "chief_engineer.blueprint:arch-constraint-memo",
        "diff_map_archive": "chief_engineer.blueprint:diff-map-archive",
    }
    assert blueprint_command.context["diff_map_archive_requires_blueprint_ref"] is True


def test_chief_engineer_record_arch_memo_targets_arch_constraint_memo_ref() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
        capability_id="record_arch_memo",
    )
    blueprint_service = FakeBlueprintService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-arch-memo-1",
                capability_id="record_arch_memo",
                role_id="chief_engineer",
                command_contract="GenerateTaskBlueprintCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "ce-memo-1",
                "run_id": "run-1",
                "objective": "Record a governance-backed architecture constraint memo",
                "constraints": {"source_ref": "docs/graph/catalog/cells.yaml"},
                "context": {"memo_subject": "roles.runtime boundary"},
            },
        ),
        blueprint_service=blueprint_service,
    )

    assert result.ok is True
    assert result.owner_cell == "chief_engineer.blueprint"
    assert result.command_contract == "GenerateTaskBlueprintCommandV1"
    assert result.metadata["target_asset_mount"] == "ArchConstraintMemo"
    assert result.metadata["target_asset_ref"] == "chief_engineer.blueprint:arch-constraint-memo"
    assert result.metadata["arch_memo_ref"] == "chief_engineer.blueprint:arch-constraint-memo:bp-1"
    assert "memo_subject" not in result.metadata

    assert len(blueprint_service.generated) == 1
    blueprint_command = blueprint_service.generated[0]
    assert blueprint_command.context["role_capability_id"] == "record_arch_memo"
    assert blueprint_command.context["target_asset_mount"] == "ArchConstraintMemo"
    assert blueprint_command.context["target_asset_ref"] == "chief_engineer.blueprint:arch-constraint-memo"
    assert blueprint_command.context["asset_refs"]["arch_constraint_memo"] == (
        "chief_engineer.blueprint:arch-constraint-memo"
    )


def test_capability_fingerprint_mismatch_is_denied_before_blueprint_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
    )
    blueprint_service = FakeBlueprintService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-blueprint-denied-1",
                capability_id="generate_diff_specification",
                role_id="chief_engineer",
                command_contract="GenerateTaskBlueprintCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref="b" * 64,
            ),
            payload={
                "task_id": "ce-task-1",
                "objective": "Generate a typed diff specification",
                "context": {"target_files": ["src/backend/example.py"]},
            },
        ),
        blueprint_service=blueprint_service,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "capability_fingerprint_mismatch"
    assert blueprint_service.generated == []


def test_capability_fingerprint_effect_mismatch_is_rejected_by_runtime_object() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
    )
    original_fingerprint = runtime_object.capability_fingerprint
    mismatched_fingerprint = RoleCapabilityFingerprint(
        role_id=original_fingerprint.role_id,
        capability_id=original_fingerprint.capability_id,
        effect="process.spawn:unexpected",
        tool=original_fingerprint.tool,
        policy_fingerprint=original_fingerprint.policy_fingerprint,
        profile_fingerprint=original_fingerprint.profile_fingerprint,
    )
    with pytest.raises(ValueError, match=r"capability_fingerprint\.effect must match mounted capability effect"):
        dataclasses.replace(runtime_object, capability_fingerprint=mismatched_fingerprint)


def test_chief_engineer_verify_ast_dependency_invokes_code_intelligence_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("chief_engineer")
    runtime_object = spec.instantiate(
        identity=_identity("chief_engineer"),
        profile_binding=_profile_binding("chief_engineer"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:ce-run"),
        policy_fingerprint="ce-policy",
        capability_id="verify_ast_dependency",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-code-intel-1",
        capability_id="verify_ast_dependency",
        role_id="chief_engineer",
        command_contract="VerifyAstDependencyQueryV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    code_intelligence = FakeCodeIntelligenceService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "path": "src/backend/app.py",
                "language": "python",
                "symbol": "handle",
                "kind": "function",
                "max_results": 5,
            },
        ),
        code_intelligence_service=code_intelligence,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "code_intelligence.engine"
    assert result.command_contract == "VerifyAstDependencyQueryV1"
    assert result.status == "VERIFIED"
    assert result.result_ref == "code_intelligence.engine:ast-dependency:invoke-code-intel-1"
    assert result.metadata["result_count"] == 1
    assert len(code_intelligence.verified) == 1
    query = code_intelligence.verified[0]
    assert query.workspace == "/repo"
    assert query.path == "src/backend/app.py"
    assert query.symbol == "handle"


def test_qa_pytest_capability_invokes_verification_guard_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-pytest-1",
        capability_id="invoke_container_pytest",
        role_id="qa",
        command_contract="VerifyCompletionCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    verification_guard = FakeVerificationGuardService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "claim_id": "qa-pytest-1",
                "claimed_outcome": "pytest passes",
                "verification_commands": ("python -m pytest tests -q",),
                "evidence_paths": ("pytest-report.xml",),
                "timeout_seconds": 120,
                "allowed_commands": ("python", "pytest"),
            },
        ),
        verification_guard_service=verification_guard,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "factory.verification_guard"
    assert result.command_contract == "VerifyCompletionCommandV1"
    assert result.status == "PASS"
    assert result.result_ref == "factory.verification_guard:report:qa-pytest-1"
    assert result.metadata["execution_summary"] == "pytest passed"
    assert len(verification_guard.verified) == 1

    verify_command = verification_guard.verified[0]
    assert isinstance(verify_command, VerifyCompletionCommandV1)
    assert verify_command.workspace == "/repo"
    assert verify_command.strict_mode is True
    assert verify_command.allowed_commands == ("python", "pytest")
    assert verify_command.claim.claim_id == "qa-pytest-1"
    assert verify_command.claim.verification_commands == ("python -m pytest tests -q",)
    assert verify_command.claim.metadata["role_invocation_id"] == "invoke-pytest-1"
    assert verify_command.claim.metadata["role_capability_id"] == "invoke_container_pytest"


def test_non_qa_pytest_capability_is_denied_without_verification_guard_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )
    verification_guard = FakeVerificationGuardService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-pytest-denied-1",
                capability_id="invoke_container_pytest",
                role_id="pm",
                command_contract="VerifyCompletionCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "claim_id": "pm-pytest-1",
                "claimed_outcome": "pytest passes",
                "verification_commands": ("python -m pytest tests -q",),
            },
        ),
        verification_guard_service=verification_guard,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "capability_not_mounted"
    assert "invoke_container_pytest" in (result.error_message or "")
    assert verification_guard.verified == []


def test_non_qa_pytest_capability_is_denied_even_if_misconfigured_as_mounted() -> None:
    runtime_object = RoleRuntimeObject(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        turn_context=RoleTurnContext(
            typed_input_ref="roles.runtime:typed-input:pm-pytest-misconfigured-1",
            context_snapshot_ref="roles.session:context-snapshot:pm-run",
            task_refs=("runtime.task_market:task:pm-pytest-misconfigured-1",),
        ),
        asset_mounts=RoleAssetMountTable(),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                RoleCapabilityDescriptor(
                    capability_id="invoke_container_pytest",
                    owner_cell="factory.verification_guard",
                    contract_name="VerifyCompletionCommandV1",
                    effect="process.spawn:qa/pytest",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.factory.verification_guard.public.service.verify_completion",
                ),
            )
        ),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_pm"),
        capability_fingerprint=RoleCapabilityFingerprint(
            role_id="pm",
            capability_id="invoke_container_pytest",
            effect="process.spawn:qa/pytest",
            tool="polaris.cells.factory.verification_guard.public.service.verify_completion",
            policy_fingerprint="pm-policy",
            profile_fingerprint="profile-fp",
        ),
    )
    verification_guard = FakeVerificationGuardService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-pytest-misconfigured-denied-1",
                capability_id="invoke_container_pytest",
                role_id="pm",
                command_contract="VerifyCompletionCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "claim_id": "pm-pytest-misconfigured-1",
                "claimed_outcome": "pytest passes",
                "verification_commands": ("python -m pytest tests -q",),
            },
        ),
        verification_guard_service=verification_guard,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "qa_capability_role_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["required_role"] == "qa"
    assert verification_guard.verified == []


def test_qa_issue_audit_verdict_invokes_qa_audit_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="issue_audit_verdict",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-qa-verdict-1",
        capability_id="issue_audit_verdict",
        role_id="qa",
        command_contract="RunQaAuditCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    qa_audit = FakeQaAuditVerdictService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "task_id": "qa-task-1",
                "run_id": "run-1",
                "criteria": {
                    "task_subject": "Audit Director output",
                    "changed_files": ("src/backend/example.py",),
                    "require_changed_files": True,
                },
                "evidence_paths": ("pytest-report.xml", "runtime/evidence/qa.jsonl"),
            },
        ),
        qa_audit_service=qa_audit,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "qa.audit_verdict"
    assert result.command_contract == "RunQaAuditCommandV1"
    assert result.status == "PASS"
    assert result.result_ref == "qa.audit_verdict:verdict:qa-task-1"
    assert result.evidence_refs == ("audit.evidence:path:runtime/evidence/qa.jsonl",)
    assert result.metadata["verdict"] == "PASS"
    assert result.metadata["score"] == 1.0
    assert result.metadata["evidence_paths"] == ("pytest-report.xml", "runtime/evidence/qa.jsonl")
    assert result.metadata["audit_evidence_refs"] == ("audit.evidence:path:runtime/evidence/qa.jsonl",)
    assert len(qa_audit.audit_commands) == 1

    audit_command = qa_audit.audit_commands[0]
    assert audit_command.workspace == "/repo"
    assert audit_command.task_id == "qa-task-1"
    assert audit_command.run_id == "run-1"
    assert audit_command.evidence_paths == ("pytest-report.xml", "runtime/evidence/qa.jsonl")
    assert audit_command.criteria["role_invocation_id"] == "invoke-qa-verdict-1"
    assert audit_command.criteria["role_capability_id"] == "issue_audit_verdict"
    assert audit_command.criteria["changed_files"] == ("src/backend/example.py",)


def test_qa_visual_audit_rejects_without_image_capable_model_before_qa_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="issue_visual_audit_verdict",
    )
    qa_audit = FakeQaAuditVerdictService()
    llm_control_plane = FakeLlmControlPlaneService(supported=False)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-visual-denied-1",
                capability_id="issue_visual_audit_verdict",
                role_id="qa",
                command_contract="RunVisualQaAuditCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "qa-visual-1",
                "image_refs": ("audit.evidence:image:screenshot-1",),
                "criteria": {"assertions": ("no visual overlap",)},
            },
        ),
        qa_audit_service=qa_audit,
        llm_control_plane_service=llm_control_plane,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "visual_model_capability_missing"
    assert result.metadata["model_capability_supported"] is False
    assert result.metadata["required_capability"] == "image_input"
    assert len(llm_control_plane.queried) == 1
    assert llm_control_plane.queried[0].capability == "image_input"
    assert qa_audit.visual_audit_commands == []


def test_qa_visual_audit_rejects_payload_model_capability_downgrade_before_preflight() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="issue_visual_audit_verdict",
    )
    qa_audit = FakeQaAuditVerdictService()
    llm_control_plane = FakeLlmControlPlaneService(supported=True)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-visual-downgrade-1",
                capability_id="issue_visual_audit_verdict",
                role_id="qa",
                command_contract="RunVisualQaAuditCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "qa-visual-1",
                "image_refs": ("audit.evidence:image:screenshot-1",),
                "criteria": {"assertions": ("no visual overlap",)},
                "required_model_capability": "text_output",
            },
        ),
        qa_audit_service=qa_audit,
        llm_control_plane_service=llm_control_plane,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "visual_model_capability_override_denied"
    assert result.metadata["required_capability"] == "image_input"
    assert result.metadata["requested_capability"] == "text_output"
    assert llm_control_plane.queried == []
    assert qa_audit.visual_audit_commands == []


def test_qa_visual_audit_model_preflight_uses_current_qa_role() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="issue_visual_audit_verdict",
    )
    qa_audit = FakeQaAuditVerdictService()
    llm_control_plane = FakeLlmControlPlaneService(supported=True)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-visual-role-bound-1",
                capability_id="issue_visual_audit_verdict",
                role_id="qa",
                command_contract="RunVisualQaAuditCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "qa-visual-1",
                "image_refs": ("audit.evidence:image:screenshot-1",),
                "criteria": {"assertions": ("no visual overlap",)},
                "evidence_paths": ("runtime/evidence/qa-visual-role-bound.jsonl",),
                "llm_role": "architect",
            },
        ),
        qa_audit_service=qa_audit,
        llm_control_plane_service=llm_control_plane,
    )

    assert result.ok is True
    assert len(llm_control_plane.queried) == 1
    assert llm_control_plane.queried[0].role == "qa"
    assert qa_audit.visual_audit_commands[0].model_capability_ref.startswith(
        "llm.control_plane:model-capability:qa:image_input:"
    )


def test_qa_visual_audit_invokes_qa_contract_after_image_capability_preflight() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="issue_visual_audit_verdict",
    )
    qa_audit = FakeQaAuditVerdictService()
    llm_control_plane = FakeLlmControlPlaneService(supported=True)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-visual-1",
                capability_id="issue_visual_audit_verdict",
                role_id="qa",
                command_contract="RunVisualQaAuditCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "qa-visual-1",
                "run_id": "run-1",
                "image_refs": ("audit.evidence:image:screenshot-1",),
                "criteria": {"assertions": ("no visual overlap",)},
                "evidence_paths": ("runtime/evidence/screenshot-1.png",),
            },
        ),
        qa_audit_service=qa_audit,
        llm_control_plane_service=llm_control_plane,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "qa.audit_verdict"
    assert result.command_contract == "RunVisualQaAuditCommandV1"
    assert result.status == "VISUAL_AUDIT_RECORDED"
    assert result.result_ref == "qa.audit_verdict:visual-verdict:qa-visual-1"
    assert result.evidence_refs == ("audit.evidence:path:runtime/evidence/screenshot-1.png",)
    assert result.metadata["evidence_refs"] == ("runtime/evidence/screenshot-1.png",)
    assert result.metadata["audit_evidence_refs"] == ("audit.evidence:path:runtime/evidence/screenshot-1.png",)
    assert len(llm_control_plane.queried) == 1
    assert len(qa_audit.visual_audit_commands) == 1

    visual_command = qa_audit.visual_audit_commands[0]
    assert visual_command.workspace == "/repo"
    assert visual_command.task_id == "qa-visual-1"
    assert visual_command.image_refs == ("audit.evidence:image:screenshot-1",)
    assert visual_command.model_capability_ref == "llm.control_plane:model-capability:qa:image_input:abc"
    assert visual_command.criteria["role_invocation_id"] == "invoke-qa-visual-1"
    assert visual_command.criteria["role_capability_id"] == "issue_visual_audit_verdict"
    assert visual_command.evidence_paths == ("runtime/evidence/screenshot-1.png",)


def test_qa_visual_audit_requires_audit_evidence_ref_after_owner_success() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="issue_visual_audit_verdict",
    )
    qa_audit = FakeQaAuditVerdictService()
    llm_control_plane = FakeLlmControlPlaneService(supported=True)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-visual-without-evidence-1",
                capability_id="issue_visual_audit_verdict",
                role_id="qa",
                command_contract="RunVisualQaAuditCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "qa-visual-1",
                "image_refs": ("audit.evidence:image:screenshot-1",),
                "criteria": {"assertions": ("no visual overlap",)},
            },
        ),
        qa_audit_service=qa_audit,
        llm_control_plane_service=llm_control_plane,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "visual_qa_audit_missing_evidence_ref"
    assert result.metadata["owner_cell"] == "qa.audit_verdict"
    assert result.metadata["evidence_owner_cell"] == "audit.evidence"
    assert qa_audit.visual_audit_commands[0].task_id == "qa-visual-1"


def test_non_qa_issue_audit_verdict_is_denied_without_qa_audit_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )
    qa_audit = FakeQaAuditVerdictService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-verdict-denied-1",
                capability_id="issue_audit_verdict",
                role_id="pm",
                command_contract="RunQaAuditCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "task_id": "qa-task-1",
                "criteria": {"task_subject": "illegal verdict"},
            },
        ),
        qa_audit_service=qa_audit,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "capability_not_mounted"
    assert qa_audit.audit_commands == []


def test_qa_parse_traceback_frames_invokes_failure_signal_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("qa")
    runtime_object = spec.instantiate(
        identity=_identity("qa"),
        profile_binding=_profile_binding("qa"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:qa-run"),
        policy_fingerprint="qa-policy",
        capability_id="parse_traceback_frames",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-qa-traceback-1",
        capability_id="parse_traceback_frames",
        role_id="qa",
        command_contract="ParseTracebackFramesCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    qa_audit = FakeQaAuditVerdictService()
    traceback_text = """Traceback (most recent call last):
  File "/repo/app.py", line 10, in handle
    return explode()
ValueError: boom
"""

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "task_id": "qa-task-2",
                "run_id": "run-1",
                "traceback_text": traceback_text,
                "metadata": {"source": "pytest"},
            },
        ),
        qa_audit_service=qa_audit,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "qa.audit_verdict"
    assert result.command_contract == "ParseTracebackFramesCommandV1"
    assert result.status == "PARSED"
    assert result.result_ref == "qa.audit_verdict:failure-signal:signal-1"
    assert result.metadata["signal_type"] == "ValueError"
    assert result.metadata["frame_count"] == 1
    assert len(qa_audit.traceback_commands) == 1

    parse_command = qa_audit.traceback_commands[0]
    assert parse_command.workspace == "/repo"
    assert parse_command.task_id == "qa-task-2"
    assert parse_command.run_id == "run-1"
    assert parse_command.metadata["source"] == "pytest"
    assert parse_command.metadata["role_invocation_id"] == "invoke-qa-traceback-1"
    assert parse_command.traceback_text == traceback_text.strip()


def test_non_qa_parse_traceback_frames_is_denied_without_qa_audit_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )
    qa_audit = FakeQaAuditVerdictService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-qa-traceback-denied-1",
                capability_id="parse_traceback_frames",
                role_id="pm",
                command_contract="ParseTracebackFramesCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={"task_id": "qa-task-2", "traceback_text": "ValueError: boom"},
        ),
        qa_audit_service=qa_audit,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "capability_not_mounted"
    assert qa_audit.traceback_commands == []


def test_architect_budget_capability_invokes_finops_budget_contract() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("architect")
    runtime_object = spec.instantiate(
        identity=_identity("architect"),
        profile_binding=_profile_binding("architect"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:architect-run"),
        policy_fingerprint="architect-policy",
        capability_id="allocate_context_token_budget",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-budget-1",
        capability_id="allocate_context_token_budget",
        role_id="architect",
        command_contract="ReserveBudgetCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    budget_guard = FakeBudgetGuardService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "scope_id": "architect-context-task-1",
                "token_budget": 4096,
                "metadata": {"context_profile_ref": "context.engine:profile:task-1"},
            },
        ),
        budget_guard_service=budget_guard,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "finops.budget_guard"
    assert result.command_contract == "ReserveBudgetCommandV1"
    assert result.result_ref == "finops.budget_guard:budget:architect-context-task-1"
    assert result.metadata["remaining_tokens"] == 4096
    assert len(budget_guard.reserved) == 1

    reserve_command = budget_guard.reserved[0]
    assert isinstance(reserve_command, ReserveBudgetCommandV1)
    assert reserve_command.workspace == "/repo"
    assert reserve_command.role == "architect"
    assert reserve_command.token_budget == 4096
    assert reserve_command.metadata["role_invocation_id"] == "invoke-budget-1"


def test_architect_budget_denial_has_allowed_false_and_capability_available_metadata() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("architect")
    runtime_object = spec.instantiate(
        identity=_identity("architect"),
        profile_binding=_profile_binding("architect"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:architect-run"),
        policy_fingerprint="architect-policy",
        capability_id="allocate_context_token_budget",
    )
    budget_guard = FakeBudgetGuardService(allowed=False, reason="context budget exhausted")

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-budget-denied-1",
                capability_id="allocate_context_token_budget",
                role_id="architect",
                command_contract="ReserveBudgetCommandV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={
                "scope_id": "architect-context-task-denied-1",
                "token_budget": 4096,
                "metadata": {"context_profile_ref": "context.engine:profile:task-denied-1"},
            },
        ),
        budget_guard_service=budget_guard,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.owner_cell == "finops.budget_guard"
    assert result.error_code == "budget_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["budget_allowed"] is False
    assert result.metadata["reason"] == "context budget exhausted"
    assert len(budget_guard.reserved) == 1


def test_architect_intercept_illegal_mutation_uses_workspace_guard_refusal() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("architect")
    runtime_object = spec.instantiate(
        identity=_identity("architect"),
        profile_binding=_profile_binding("architect"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:architect-run"),
        policy_fingerprint="architect-policy",
    )
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-mutation-guard-1",
        capability_id="intercept_illegal_mutations",
        role_id="architect",
        command_contract="WorkspaceWriteGuardQueryV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    workspace_guard = FakeWorkspaceGuardService(allowed=False)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "path": "../outside-project/secret.py",
                "operation": "write",
            },
        ),
        workspace_guard_service=workspace_guard,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.owner_cell == "policy.workspace_guard"
    assert result.error_code == "workspace_guard_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["mutation_allowed"] is False
    assert result.metadata["guard_reason"] == "outside declared mutation boundary"
    assert len(workspace_guard.checked) == 1
    assert workspace_guard.checked[0].path == "../outside-project/secret.py"
    assert workspace_guard.checked[0].operation == "write"


def test_non_architect_mutation_guard_is_denied_without_workspace_guard_call() -> None:
    spec = runtime_contracts.get_builtin_role_runtime_spec("pm")
    runtime_object = spec.instantiate(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        policy_fingerprint="pm-policy",
    )
    workspace_guard = FakeWorkspaceGuardService(allowed=True, single_checks_allowed=False)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-mutation-guard-denied-1",
                capability_id="intercept_illegal_mutations",
                role_id="pm",
                command_contract="WorkspaceWriteGuardQueryV1",
                payload_ref=runtime_object.turn_context.typed_input_ref,
                fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
            ),
            payload={"path": "src/backend/example.py", "operation": "write"},
        ),
        workspace_guard_service=workspace_guard,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "capability_not_mounted"
    assert workspace_guard.checked == []


def test_architect_validate_cell_boundary_change_invokes_permission_guard_and_design() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=True)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate roles.runtime boundary change",
                "target_cell": "roles.runtime",
                "changed_paths": (
                    "src/backend/polaris/cells/roles/runtime/public/service.py",
                    "src/backend/polaris/cells/roles/runtime/public/service.py",
                    "src/backend/polaris/cells/roles/runtime/public/contracts.py",
                ),
                "constraints": {
                    "depends_on_delta": ("architect.design",),
                    "state_owner_delta": (),
                    "effects_delta": ("architect.validate_cell_boundary",),
                },
                "context": {"graph_ref": "docs/graph/catalog/cells.yaml"},
                "timeout_seconds": 1.0,
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "architect.design"
    assert result.command_contract == "GenerateArchitectureDesignCommandV1"
    assert result.result_ref == "architect.design:boundary-validation:design-boundary-1"
    assert result.metadata["permission_allowed"] is True
    assert result.metadata["workspace_guard_allowed"] is True
    assert result.metadata["checked_paths"] == (
        "src/backend/polaris/cells/roles/runtime/public/service.py",
        "src/backend/polaris/cells/roles/runtime/public/contracts.py",
    )
    assert len(permission.evaluated) == 1
    assert permission.evaluated[0].context["capability_id"] == "validate_cell_boundary_change"
    assert "depends_on_delta" not in permission.evaluated[0].context
    assert workspace_guard.checked == []
    assert len(workspace_guard.batch_checked) == 1
    assert workspace_guard.batch_checked[0].paths == (
        "src/backend/polaris/cells/roles/runtime/public/service.py",
        "src/backend/polaris/cells/roles/runtime/public/contracts.py",
    )
    assert len(architect_design.generated) == 1
    design_command = architect_design.generated[0]
    assert design_command.workspace == "/repo"
    assert design_command.objective == "Validate roles.runtime boundary change"
    assert design_command.constraints["depends_on_delta"] == ("architect.design",)
    assert design_command.context["target_cell"] == "roles.runtime"
    assert design_command.context["role_invocation_id"] == "invoke-boundary-1"


def test_architect_validate_cell_boundary_permission_denial_has_allowed_false() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-denied-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=False)
    workspace_guard = FakeWorkspaceGuardService(allowed=True)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate denied change",
                "target_cell": "roles.runtime",
                "changed_paths": ("src/backend/polaris/cells/roles/runtime/public/service.py",),
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "permission_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["permission_allowed"] is False
    assert workspace_guard.checked == []
    assert architect_design.generated == []


def test_architect_validate_cell_boundary_workspace_guard_denial_has_allowed_false() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-guard-denied-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=False, single_checks_allowed=False)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate denied path",
                "target_cell": "roles.runtime",
                "changed_paths": ("../outside-project/secret.py",),
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "workspace_guard_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["workspace_guard_allowed"] is False
    assert result.metadata["denied_path"] == "../outside-project/secret.py"
    assert workspace_guard.checked == []
    assert len(workspace_guard.batch_checked) == 1
    assert workspace_guard.batch_checked[0].paths == ("../outside-project/secret.py",)
    assert architect_design.generated == []


def test_architect_validate_cell_boundary_rejects_empty_changed_paths_before_guards() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-empty-paths-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=True, single_checks_allowed=False)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate pathless change",
                "target_cell": "roles.runtime",
                "changed_paths": (),
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "invalid_architect_boundary_changed_paths"
    assert permission.evaluated == []
    assert workspace_guard.checked == []
    assert workspace_guard.batch_checked == []
    assert architect_design.generated == []


def test_architect_validate_cell_boundary_invalid_context_has_allowed_false() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-invalid-context-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=True, single_checks_allowed=False)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate malformed change",
                "target_cell": "roles.runtime",
                "changed_paths": ("src/backend/polaris/cells/roles/runtime/public/service.py",),
                "context": "not-a-mapping",
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "invalid_architect_boundary_context"
    assert result.metadata["capability_available"] is True
    assert result.metadata["capability_id"] == "validate_cell_boundary_change"
    assert permission.evaluated == []
    assert workspace_guard.checked == []
    assert workspace_guard.batch_checked == []
    assert architect_design.generated == []


def test_architect_validate_cell_boundary_design_timeout_has_structured_failure() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-timeout-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref=runtime_object.turn_context.typed_input_ref,
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate slow change",
                "target_cell": "roles.runtime",
                "changed_paths": ("src/backend/polaris/cells/roles/runtime/public/service.py",),
                "timeout_seconds": 0.01,
            },
        ),
        permission_service=FakePermissionService(allowed=True),
        workspace_guard_service=FakeWorkspaceGuardService(allowed=True),
        architect_design_service=SlowArchitectDesignService(),
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "architect_design_timeout"
    assert result.metadata["capability_available"] is True
