"""Pure fixtures recovered losslessly from the pre-split contract test module."""
from __future__ import annotations

from polaris.cells.roles.runtime.public import contracts as runtime_contracts
from polaris.cells.roles.runtime.public.contracts import (
    AssembleRoleRuntimeChainCommandV1,
    RoleAssetMount,
    RoleAssetMountTable,
    RoleAssetRef,
    RoleCapabilityDescriptor,
    RoleCapabilityInvocation,
    RoleCapabilityPorts,
    RoleIdentity,
    RoleLedgerBinding,
    RoleProfileBinding,
    RoleRuntimeChainStepRef,
    RoleRuntimeObject,
    RoleTaskMarketBinding,
    RoleTurnContext,
    RoleTurnEnvelope,
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


