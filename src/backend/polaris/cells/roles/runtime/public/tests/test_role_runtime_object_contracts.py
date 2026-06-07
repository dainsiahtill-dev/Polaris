from __future__ import annotations

import dataclasses
import time

import pytest
from polaris.cells.architect.design.public.contracts import (
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    GenerateTaskBlueprintCommandV1,
    TaskBlueprintResultV1,
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
    TracebackFrameV1,
)
from polaris.cells.roles.runtime.public import contracts as runtime_contracts
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleCapabilityInvocationCommandV1,
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
    RoleStateCommitReceipt,
    RoleStateCommitRequest,
    RoleTaskMarketBinding,
    RoleTurnContext,
    RoleTurnEnvelope,
)
from polaris.cells.roles.runtime.public.service import execute_role_capability_invocation
from polaris.cells.runtime.task_market.public import PublishTaskWorkItemCommandV1, TaskWorkItemResultV1


class FakeTaskMarketService:
    def __init__(self) -> None:
        self.published: list[PublishTaskWorkItemCommandV1] = []

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


class FakeBudgetGuardService:
    def __init__(self) -> None:
        self.reserved: list[ReserveBudgetCommandV1] = []

    def reserve_budget(self, command: ReserveBudgetCommandV1) -> BudgetDecisionResultV1:
        self.reserved.append(command)
        return BudgetDecisionResultV1(
            allowed=True,
            scope_id=command.scope_id,
            role=command.role,
            remaining_tokens=command.token_budget,
            estimated_cost_usd=0.0,
            reason="reserved",
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
    assert "public_contract_gap" in verify_ast.metadata
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
    assert parse_command.traceback_text == traceback_text


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
