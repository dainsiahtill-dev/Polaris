from __future__ import annotations

import dataclasses
import time
from types import SimpleNamespace

import pytest
from polaris.cells.architect.design.public.contracts import (
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    GenerateTaskBlueprintCommandV1,
    TaskBlueprintResultV1,
)
from polaris.cells.code_intelligence.engine.public.contracts import (
    AstDependencyVerificationResultV1,
    VerifyAstDependencyQueryV1,
)
from polaris.cells.factory.cognitive_runtime.public.contracts import (
    ExportHandoffPackCommandV1,
    HandoffPackResultV1,
    RecordRuntimeReceiptCommandV1,
    RuntimeReceiptResultV1,
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
    WorkspaceGuardDecisionV1,
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
from polaris.cells.roles.runtime.public import contracts as runtime_contracts
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleCapabilityInvocationCommandV1,
    ExecuteRoleTaskMarketLifecycleCommandV1,
    InstantiateRoleRuntimeObjectCommandV1,
    RoleAssetMount,
    RoleAssetMountTable,
    RoleAssetRef,
    RoleCapabilityDescriptor,
    RoleCapabilityFingerprint,
    RoleCapabilityInvocation,
    RoleCapabilityInvocationResultV1,
    RoleCapabilityPorts,
    RoleIdentity,
    RoleLedgerBinding,
    RoleProfileBinding,
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
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
    RequeueTaskCommandV1,
    TaskLeaseRenewResultV1,
    TaskMarketStatusResultV1,
    TaskWorkItemResultV1,
)


class FakeTaskMarketService:
    def __init__(self) -> None:
        self.published: list[PublishTaskWorkItemCommandV1] = []
        self.queried: list[QueryTaskMarketStatusV1] = []
        self.claimed: list[ClaimTaskWorkItemCommandV1] = []
        self.renewed: list[RenewTaskLeaseCommandV1] = []
        self.acked: list[AcknowledgeTaskStageCommandV1] = []
        self.failed: list[FailTaskStageCommandV1] = []
        self.requeued: list[RequeueTaskCommandV1] = []

    def publish_work_item(self, command: PublishTaskWorkItemCommandV1) -> TaskWorkItemResultV1:
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

    def query_status(self, query: QueryTaskMarketStatusV1) -> TaskMarketStatusResultV1:
        self.queried.append(query)
        return TaskMarketStatusResultV1(
            workspace=query.workspace,
            total=3,
            counts={"pending_exec": 1, "running": 1, "dead_letter": 1},
            items=(
                {"task_id": "task-a", "stage": "pending_exec", "status": "pending", "priority": "high"},
                {"task_id": "task-b", "stage": "pending_qa", "status": "running", "priority": "medium"},
                {"task_id": "task-c", "stage": "pending_exec", "status": "dead_letter", "priority": "high"},
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


class FakeRuntimeProjectionService:
    def __init__(self) -> None:
        self.queries: list[RuntimeProjectionQueryV1] = []

    def query_runtime_projection(self, query: RuntimeProjectionQueryV1) -> RuntimeProjectionResultV1:
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
    def __init__(self) -> None:
        self.receipts: list[RecordRuntimeReceiptCommandV1] = []
        self.handoffs: list[ExportHandoffPackCommandV1] = []

    def record_runtime_receipt(self, command: RecordRuntimeReceiptCommandV1) -> RuntimeReceiptResultV1:
        self.receipts.append(command)
        return RuntimeReceiptResultV1(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

    def export_handoff_pack(self, command: ExportHandoffPackCommandV1) -> HandoffPackResultV1:
        self.handoffs.append(command)
        return HandoffPackResultV1(ok=True, handoff=SimpleNamespace(handoff_id="handoff-1"))


class FakeBlueprintService:
    def __init__(self) -> None:
        self.generated: list[GenerateTaskBlueprintCommandV1] = []

    def generate_task_blueprint(self, command: GenerateTaskBlueprintCommandV1) -> TaskBlueprintResultV1:
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

    def verify_ast_dependency(self, query: VerifyAstDependencyQueryV1) -> AstDependencyVerificationResultV1:
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


class FakeVerificationGuardService:
    def __init__(self) -> None:
        self.verified: list[VerifyCompletionCommandV1] = []

    def verify_completion(self, command: VerifyCompletionCommandV1) -> VerifyCompletionResultV1:
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

    def check_model_capability(self, query: CheckLlmModelCapabilityQueryV1) -> LlmModelCapabilityResultV1:
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

    def reserve_budget(self, command: ReserveBudgetCommandV1) -> BudgetDecisionResultV1:
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
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.checked: list[WorkspaceWriteGuardQueryV1] = []

    def check_workspace_write_guard(self, query: WorkspaceWriteGuardQueryV1) -> WorkspaceGuardDecisionV1:
        self.checked.append(query)
        return WorkspaceGuardDecisionV1(
            allowed=self.allowed,
            reason="allowed" if self.allowed else "outside declared mutation boundary",
        )


class FakePermissionService:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.evaluated: list[EvaluatePermissionCommandV1] = []

    def evaluate_permission(self, command: EvaluatePermissionCommandV1) -> PermissionDecisionResultV1:
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


class FakeArchitectDesignService:
    def __init__(self) -> None:
        self.generated: list[GenerateArchitectureDesignCommandV1] = []

    def generate_architecture_design(
        self,
        command: GenerateArchitectureDesignCommandV1,
    ) -> ArchitectureDesignResultV1:
        self.generated.append(command)
        return ArchitectureDesignResultV1(
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

    def run_qa_audit(self, command: RunQaAuditCommandV1) -> QaAuditResultV1:
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

    def run_visual_qa_audit(self, command: RunVisualQaAuditCommandV1) -> VisualQaAuditResultV1:
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

    def parse_traceback_frames(self, command: ParseTracebackFramesCommandV1) -> ParseTracebackFramesResultV1:
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
    def generate_architecture_design(
        self,
        command: GenerateArchitectureDesignCommandV1,
    ) -> ArchitectureDesignResultV1:
        time.sleep(0.25)
        return ArchitectureDesignResultV1(
            ok=True,
            workspace=command.workspace,
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
            ),
            RoleCapabilityDescriptor(
                capability_id="record_runtime_receipt",
                owner_cell="factory.cognitive_runtime",
                contract_name="RecordRuntimeReceiptCommandV1",
                effect="runtime_receipt.record",
                allowed_roles=("pm", "chief_engineer", "architect", "qa", "director"),
            ),
        )
    )


def test_role_runtime_object_mounts_assets_and_ports_by_public_contract_refs() -> None:
    fingerprint = RoleCapabilityFingerprint(
        role_id="pm",
        capability_id="dispatch_task_to_market",
        effect="task_market.publish",
        tool="runtime.task_market.public.service.publish_work_item",
        policy_fingerprint="policy-fp",
        profile_fingerprint="profile-fp",
    )

    runtime_object = RoleRuntimeObject(
        identity=_identity(),
        profile_binding=_profile_binding(),
        asset_mounts=_pm_mount_table(),
        capability_ports=_capability_ports(),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_contract="CommitReceipt",
            runtime_receipt_contract="RecordRuntimeReceiptCommandV1",
            receipt_refs=("receipt-1",),
        ),
        task_market_binding=RoleTaskMarketBinding(),
        capability_fingerprint=fingerprint,
    )

    assert runtime_object.identity.role_id == "pm"
    assert runtime_object.asset_mounts.get("TaskGraph").asset_ref.owner_cell == "runtime.task_market"
    assert (
        runtime_object.capability_ports.get("dispatch_task_to_market").contract_name == "PublishTaskWorkItemCommandV1"
    )
    assert runtime_object.task_market_binding.publish_contract == "PublishTaskWorkItemCommandV1"
    assert runtime_object.ledger_binding.runtime_receipt_contract == "RecordRuntimeReceiptCommandV1"
    assert runtime_object.capability_fingerprint.fingerprint == fingerprint.fingerprint
    assert dataclasses.is_dataclass(runtime_object)


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
        with pytest.raises(ValueError, match="must be owned by a target public Cell"):
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


def test_role_turn_envelope_and_commit_request_carry_refs_not_foreign_state() -> None:
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-1",
        capability_id="dispatch_task_to_market",
        role_id="pm",
        command_contract="PublishTaskWorkItemCommandV1",
        payload_ref="runtime.task_market:work-item:task-1",
        fingerprint_ref="capability-fp",
    )
    envelope = RoleTurnEnvelope(
        identity=_identity(),
        profile_binding=_profile_binding(),
        turn_context=RoleTurnContext(
            typed_input_ref="pm.task_contract:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
            handoff_refs=("handoff-1",),
            task_refs=("runtime.task_market:task-1",),
        ),
        capability_invocations=(invocation,),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_contract="CommitReceipt",
            runtime_receipt_contract="RecordRuntimeReceiptCommandV1",
        ),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task-1"),
    )
    request = RoleStateCommitRequest(
        request_id="commit-request-1",
        envelope=envelope,
        changed_asset_refs=("runtime.task_market:task-1",),
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

    assert request.envelope.turn_context.handoff_refs == ("handoff-1",)
    assert request.envelope.capability_invocations[0].payload_ref == "runtime.task_market:work-item:task-1"
    assert receipt.commit_contract == "CommitReceipt"
    assert receipt.runtime_receipt_contract == "RecordRuntimeReceiptCommandV1"


def test_role_task_market_lifecycle_uses_binding_contracts_and_public_service() -> None:
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
    )
    task_market = FakeTaskMarketService()

    claim = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="claim",
            payload={"stage": "pending_design", "worker_id": "ce-worker-1", "task_id": "task-1"},
        ),
        task_market_service=task_market,
    )
    renew = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="lease",
            payload={"task_id": "task-1", "lease_token": "lease-1", "visibility_timeout_seconds": 120},
        ),
        task_market_service=task_market,
    )
    ack = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
            operation="ack",
            payload={"task_id": "task-1", "lease_token": "lease-1", "next_stage": "pending_exec"},
        ),
        task_market_service=task_market,
    )
    fail = execute_role_task_market_lifecycle(
        ExecuteRoleTaskMarketLifecycleCommandV1(
            runtime_object=runtime_object,
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
            runtime_object=runtime_object,
            operation="requeue",
            payload={"task_id": "task-1", "target_stage": "pending_design", "reason": "retry blueprint"},
        ),
        task_market_service=task_market,
    )

    assert isinstance(claim, RoleTaskMarketLifecycleResultV1)
    assert claim.ok is True
    assert claim.command_contract == runtime_object.task_market_binding.claim_contract
    assert claim.result_ref == "runtime.task_market:task:task-1"
    assert claim.lease_token_ref == "runtime.task_market:lease:lease-1"
    assert task_market.claimed[0].worker_role == "chief_engineer"
    assert task_market.claimed[0].workspace == "/repo"
    assert renew.command_contract == runtime_object.task_market_binding.lease_contract
    assert task_market.renewed[0].visibility_timeout_seconds == 120
    assert ack.command_contract == runtime_object.task_market_binding.ack_contract
    assert task_market.acked[0].next_stage == "pending_exec"
    assert fail.command_contract == runtime_object.task_market_binding.fail_contract
    assert task_market.failed[0].requeue_stage == "pending_design"
    assert requeue.command_contract == runtime_object.task_market_binding.requeue_contract
    assert task_market.requeued[0].target_stage == "pending_design"


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
        payload_ref="runtime.task_market:work-item:task-1",
        fingerprint_ref="capability-fp",
    )
    envelope = RoleTurnEnvelope(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        turn_context=RoleTurnContext(
            typed_input_ref="pm.task_contract:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
            handoff_refs=("factory.cognitive_runtime:handoff:previous",),
            task_refs=("runtime.task_market:task-1",),
        ),
        capability_invocations=(invocation,),
        ledger_binding=RoleLedgerBinding(
            turn_ledger_ref="roles.kernel:turn-ledger:run-1",
            commit_receipt_ref="roles.kernel:commit:turn-1",
            receipt_refs=("factory.cognitive_runtime:receipt:previous",),
        ),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task-1"),
        metadata={"turn_id": "turn-1"},
    )
    request = RoleStateCommitRequest(
        request_id="commit-request-1",
        envelope=envelope,
        changed_asset_refs=("runtime.task_market:task-1",),
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
    assert len(cognitive_runtime.receipts) == 1
    assert len(cognitive_runtime.handoffs) == 1

    receipt_command = cognitive_runtime.receipts[0]
    assert receipt_command.workspace == "/repo"
    assert receipt_command.receipt_type == "role_state_commit"
    assert receipt_command.session_id == "session-1"
    assert receipt_command.run_id == "run-1"
    assert receipt_command.payload["request_id"] == "commit-request-1"
    assert receipt_command.payload["role_id"] == "pm"
    assert receipt_command.payload["changed_asset_refs"] == ("runtime.task_market:task-1",)
    assert receipt_command.turn_envelope["identity"]["role_id"] == "pm"
    assert receipt_command.turn_envelope["ledger_binding"]["commit_receipt_ref"] == "roles.kernel:commit:turn-1"
    assert "roles.kernel:commit:turn-1" in receipt_command.trace_refs
    assert "audit.evidence:evt-1" in receipt_command.trace_refs

    handoff_command = cognitive_runtime.handoffs[0]
    assert handoff_command.workspace == "/repo"
    assert handoff_command.session_id == "session-1"
    assert handoff_command.reason == "task-market dispatch committed"
    assert handoff_command.turn_envelope["runtime_receipt_refs"] == (
        "factory.cognitive_runtime:receipt:previous",
        "factory.cognitive_runtime:receipt:receipt-1",
    )


def test_commit_role_state_rejects_missing_kernel_commit_receipt_without_runtime_call() -> None:
    envelope = RoleTurnEnvelope(
        identity=_identity("pm"),
        profile_binding=_profile_binding("pm"),
        turn_context=RoleTurnContext(
            typed_input_ref="pm.task_contract:task-1",
            context_snapshot_ref="context.engine:snapshot-1",
        ),
        capability_invocations=(),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:run-1"),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:task-1"),
    )
    request = RoleStateCommitRequest(
        request_id="commit-request-missing",
        envelope=envelope,
        changed_asset_refs=("runtime.task_market:task-1",),
    )
    cognitive_runtime = FakeCognitiveRuntimeCommitService()

    receipt = commit_role_state(request, cognitive_runtime_service=cognitive_runtime)

    assert receipt.ok is False
    assert receipt.status == "rejected"
    assert receipt.error_code == "missing_commit_receipt_ref"
    assert cognitive_runtime.receipts == []
    assert cognitive_runtime.handoffs == []


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
        payload_ref="roles.runtime:typed-input:pm-task-1",
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
    assert publish_command.depends_on == ("dep-1",)


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
                payload_ref="roles.runtime:typed-input:ce-task-1",
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
        payload_ref="roles.runtime:typed-input:pm-critical-path-1",
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
        payload_ref="roles.runtime:typed-input:pm-runtime-projection-1",
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
        payload_ref="roles.runtime:typed-input:ce-task-1",
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
                payload_ref="roles.runtime:typed-input:ce-task-1",
                fingerprint_ref="wrong-fingerprint",
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
        payload_ref="roles.runtime:typed-input:ce-code-intel-1",
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
        payload_ref="roles.runtime:typed-input:qa-pytest-1",
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
                payload_ref="roles.runtime:typed-input:pm-pytest-1",
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
        asset_mounts=RoleAssetMountTable(),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                RoleCapabilityDescriptor(
                    capability_id="invoke_container_pytest",
                    owner_cell="factory.verification_guard",
                    contract_name="VerifyCompletionCommandV1",
                    effect="process.spawn:qa/pytest",
                    allowed_roles=("pm",),
                ),
            )
        ),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:pm-run"),
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_pm"),
        capability_fingerprint=RoleCapabilityFingerprint(
            role_id="pm",
            capability_id="invoke_container_pytest",
            effect="process.spawn:qa/pytest",
            tool="pytest",
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
                payload_ref="roles.runtime:typed-input:pm-pytest-misconfigured-1",
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
        payload_ref="roles.runtime:typed-input:qa-verdict-1",
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
    assert result.metadata["verdict"] == "PASS"
    assert result.metadata["score"] == 1.0
    assert result.metadata["evidence_paths"] == ("pytest-report.xml", "runtime/evidence/qa.jsonl")
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
                payload_ref="roles.runtime:typed-input:qa-visual-denied-1",
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
                payload_ref="roles.runtime:typed-input:qa-visual-1",
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
                payload_ref="roles.runtime:typed-input:pm-verdict-1",
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
        payload_ref="roles.runtime:typed-input:qa-traceback-1",
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
                payload_ref="roles.runtime:typed-input:pm-traceback-1",
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
        payload_ref="roles.runtime:typed-input:architect-budget-1",
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
                payload_ref="roles.runtime:typed-input:architect-budget-denied-1",
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
        payload_ref="roles.runtime:typed-input:architect-mutation-1",
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
    workspace_guard = FakeWorkspaceGuardService(allowed=True)

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=RoleCapabilityInvocation(
                invocation_id="invoke-mutation-guard-denied-1",
                capability_id="intercept_illegal_mutations",
                role_id="pm",
                command_contract="WorkspaceWriteGuardQueryV1",
                payload_ref="roles.runtime:typed-input:pm-mutation-1",
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
        payload_ref="roles.runtime:typed-input:architect-boundary-1",
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
    assert len(workspace_guard.checked) == 2
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
        payload_ref="roles.runtime:typed-input:architect-boundary-denied-1",
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
        payload_ref="roles.runtime:typed-input:architect-boundary-guard-denied-1",
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=False)
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
    assert len(workspace_guard.checked) == 1
    assert architect_design.generated == []


def test_architect_validate_cell_boundary_design_timeout_has_structured_failure() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-timeout-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref="roles.runtime:typed-input:architect-boundary-timeout-1",
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
