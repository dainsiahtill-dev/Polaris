from __future__ import annotations

import dataclasses

import pytest
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
