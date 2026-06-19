"""Built-in role composition specs for roles.runtime.

Data-only built-in role runtime composition specs for the business roles
(pm / chief_engineer / architect / qa / director) plus the shared
task-market lifecycle capability spec. These factories assemble
``RoleRuntimeObjectSpec`` instances from the object-composition contracts;
they do not copy or mutate foreign Cell state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.runtime.public.contracts._object_contracts import (
    RoleAssetMount,
    RoleAssetMountTable,
    RoleAssetRef,
    RoleCapabilityDescriptor,
    RoleCapabilityPorts,
    RoleRuntimeObjectSpec,
    RoleTaskMarketBinding,
)
from polaris.cells.roles.runtime.public.contracts._validation import _require_non_empty


def _asset_ref(
    *,
    asset_id: str,
    owner_cell: str,
    contract_name: str,
    ref: str,
    asset_kind: str,
    metadata: Mapping[str, Any] | None = None,
) -> RoleAssetRef:
    return RoleAssetRef(
        asset_id=asset_id,
        owner_cell=owner_cell,
        contract_name=contract_name,
        ref=ref,
        asset_kind=asset_kind,
        metadata=metadata or {},
    )


def _mount(
    mount_name: str,
    asset_ref: RoleAssetRef,
    *,
    access_mode: str = "read",
    metadata: Mapping[str, Any] | None = None,
) -> RoleAssetMount:
    return RoleAssetMount(
        mount_name=mount_name,
        asset_ref=asset_ref,
        access_mode=access_mode,
        metadata=metadata or {},
    )


def _capability(
    *,
    capability_id: str,
    owner_cell: str,
    contract_name: str,
    effect: str,
    allowed_roles: tuple[str, ...],
    endpoint_ref: str,
    metadata: Mapping[str, Any] | None = None,
) -> RoleCapabilityDescriptor:
    return RoleCapabilityDescriptor(
        capability_id=capability_id,
        owner_cell=owner_cell,
        contract_name=contract_name,
        effect=effect,
        allowed_roles=allowed_roles,
        endpoint_ref=endpoint_ref,
        metadata=metadata or {},
    )


def _task_market_lifecycle_capabilities(allowed_roles: tuple[str, ...]) -> tuple[RoleCapabilityDescriptor, ...]:
    return (
        _capability(
            capability_id="claim_task_market_work_item",
            owner_cell="runtime.task_market",
            contract_name="ClaimTaskWorkItemCommandV1",
            effect="task_market.claim",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.claim_work_item",
            metadata={"lifecycle_operation": "claim"},
        ),
        _capability(
            capability_id="renew_task_market_lease",
            owner_cell="runtime.task_market",
            contract_name="RenewTaskLeaseCommandV1",
            effect="task_market.lease",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.renew_task_lease",
            metadata={"lifecycle_operation": "lease"},
        ),
        _capability(
            capability_id="acknowledge_task_market_stage",
            owner_cell="runtime.task_market",
            contract_name="AcknowledgeTaskStageCommandV1",
            effect="task_market.ack",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.acknowledge_task_stage",
            metadata={"lifecycle_operation": "ack"},
        ),
        _capability(
            capability_id="fail_task_market_stage",
            owner_cell="runtime.task_market",
            contract_name="FailTaskStageCommandV1",
            effect="task_market.fail",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.fail_task_stage",
            metadata={"lifecycle_operation": "fail"},
        ),
        _capability(
            capability_id="requeue_task_market_work_item",
            owner_cell="runtime.task_market",
            contract_name="RequeueTaskCommandV1",
            effect="task_market.requeue",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.requeue_task",
            metadata={"lifecycle_operation": "requeue"},
        ),
        _capability(
            capability_id="move_task_to_dead_letter",
            owner_cell="runtime.task_market",
            contract_name="MoveTaskToDeadLetterCommandV1",
            effect="task_market.dead_letter",
            allowed_roles=allowed_roles,
            endpoint_ref="polaris.cells.runtime.task_market.public.service.move_task_to_dead_letter",
            metadata={"lifecycle_operation": "dead_letter"},
        ),
    )


def _build_pm_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="pm",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "ProjectFunctionIndex",
                    _asset_ref(
                        asset_id="project-function-index",
                        owner_cell="context.catalog",
                        contract_name="SearchCellsQueryV1",
                        ref="context.catalog:project-function-index",
                        asset_kind="project_function_index",
                        metadata={
                            "derived_from": (
                                "context.catalog",
                                "runtime.task_runtime",
                                "runtime.task_market",
                                "runtime.projection",
                            )
                        },
                    ),
                ),
                _mount(
                    "TaskGraph",
                    _asset_ref(
                        asset_id="task-graph",
                        owner_cell="runtime.task_market",
                        contract_name="QueryTaskMarketStatusV1",
                        ref="runtime.task_market:task-graph",
                        asset_kind="task_graph",
                        metadata={"task_runtime_owner_cell": "runtime.task_runtime"},
                    ),
                ),
                _mount(
                    "RuntimeProjectionState",
                    _asset_ref(
                        asset_id="runtime-projection-state",
                        owner_cell="runtime.projection",
                        contract_name="RuntimeProjectionQueryV1",
                        ref="runtime.projection:runtime-status",
                        asset_kind="runtime_projection_state",
                    ),
                ),
                _mount(
                    "OpenLoopRegistry",
                    _asset_ref(
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
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="dispatch_task_to_market",
                    owner_cell="runtime.task_market",
                    contract_name="PublishTaskWorkItemCommandV1",
                    effect="task_market.publish",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.runtime.task_market.public.service.TaskMarketService.publish_work_item",
                    metadata={"target_stage": "pending_design"},
                ),
                _capability(
                    capability_id="evaluate_critical_path",
                    owner_cell="runtime.task_market",
                    contract_name="QueryTaskMarketStatusV1",
                    effect="task_market.read",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.runtime.task_market.public.service.TaskMarketService.query_status",
                    metadata={"requires_asset_mounts": ("TaskGraph", "RuntimeProjectionState")},
                ),
                _capability(
                    capability_id="project_runtime_status",
                    owner_cell="runtime.projection",
                    contract_name="RuntimeProjectionQueryV1",
                    effect="runtime_projection.read",
                    allowed_roles=("pm",),
                    endpoint_ref="polaris.cells.runtime.projection.public.contracts.RuntimeProjectionQueryV1",
                    metadata={"requires_asset_mounts": ("TaskGraph", "RuntimeProjectionState")},
                ),
            )
        ),
        default_capability_id="dispatch_task_to_market",
        task_market_binding=RoleTaskMarketBinding(),
        metadata={"owner_cell": "roles.runtime", "business_role": "pm"},
    )


def _build_chief_engineer_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="chief_engineer",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "BlueprintDatabase",
                    _asset_ref(
                        asset_id="blueprint-database",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:runtime/blueprints",
                        asset_kind="blueprint_database",
                    ),
                ),
                _mount(
                    "ArchConstraintMemo",
                    _asset_ref(
                        asset_id="arch-constraint-memo",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:arch-constraint-memo",
                        asset_kind="arch_constraint_memo",
                        metadata={"governance_source_ref": "docs/graph/**"},
                    ),
                ),
                _mount(
                    "DiffMapArchive",
                    _asset_ref(
                        asset_id="diff-map-archive",
                        owner_cell="chief_engineer.blueprint",
                        contract_name="GetBlueprintStatusQueryV1",
                        ref="chief_engineer.blueprint:diff-map-archive",
                        asset_kind="diff_map_archive",
                        metadata={
                            "requires_blueprint_ref": True,
                            "blueprint_id": "chief-engineer-runtime-blueprint",
                            "path": "runtime/blueprints/diff-map-archive",
                            "ref": "chief_engineer.blueprint:diff-map-archive:chief-engineer-runtime-blueprint",
                        },
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="generate_diff_specification",
                    owner_cell="chief_engineer.blueprint",
                    contract_name="GenerateTaskBlueprintCommandV1",
                    effect="blueprint.generate",
                    allowed_roles=("chief_engineer",),
                    endpoint_ref="polaris.cells.chief_engineer.blueprint.public.service.generate_task_blueprint",
                    metadata={"output_contract": "TaskBlueprintResultV1"},
                ),
                _capability(
                    capability_id="verify_ast_dependency",
                    owner_cell="code_intelligence.engine",
                    contract_name="VerifyAstDependencyQueryV1",
                    effect="code_intelligence.read",
                    allowed_roles=("chief_engineer",),
                    endpoint_ref="polaris.cells.code_intelligence.engine.public.service.verify_ast_dependency",
                    metadata={
                        "output_contract": "AstDependencyVerificationResultV1",
                        "implementation_port": "TreeSitterSymbolHandler.find_symbol",
                    },
                ),
                _capability(
                    capability_id="record_arch_memo",
                    owner_cell="chief_engineer.blueprint",
                    contract_name="GenerateTaskBlueprintCommandV1",
                    effect="blueprint.memo.record",
                    allowed_roles=("chief_engineer",),
                    endpoint_ref="polaris.cells.chief_engineer.blueprint.public.service.generate_task_blueprint",
                    metadata={"asset_mount": "ArchConstraintMemo"},
                ),
                *_task_market_lifecycle_capabilities(("chief_engineer",)),
            )
        ),
        default_capability_id="generate_diff_specification",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_design"),
        metadata={"owner_cell": "roles.runtime", "business_role": "chief_engineer"},
    )


def _build_architect_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="architect",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "ConstraintTopology",
                    _asset_ref(
                        asset_id="constraint-topology",
                        owner_cell="context.catalog",
                        contract_name="SearchCellsQueryV1",
                        ref="docs.graph:cells",
                        asset_kind="constraint_topology",
                        metadata={"graph_source_ref": "docs/graph/**", "target_cell": "architect.design"},
                    ),
                ),
                _mount(
                    "ContextBudgetProfile",
                    _asset_ref(
                        asset_id="context-budget-profile",
                        owner_cell="finops.budget_guard",
                        contract_name="GetBudgetStatusQueryV1",
                        ref="finops.budget_guard:context-budget-profile",
                        asset_kind="context_budget_profile",
                        metadata={"context_owner_cell": "context.engine"},
                    ),
                ),
                _mount(
                    "MutationBoundaryMap",
                    _asset_ref(
                        asset_id="mutation-boundary-map",
                        owner_cell="policy.workspace_guard",
                        contract_name="WorkspaceWriteGuardQueryV1",
                        ref="policy.workspace_guard:mutation-boundary-map",
                        asset_kind="mutation_boundary_map",
                        metadata={
                            "derived_from": ("docs/graph/**", "policy.workspace_guard", "policy.permission"),
                            "permission_owner_cell": "policy.permission",
                        },
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="allocate_context_token_budget",
                    owner_cell="finops.budget_guard",
                    contract_name="ReserveBudgetCommandV1",
                    effect="budget.reserve:context",
                    allowed_roles=("architect",),
                    endpoint_ref="polaris.cells.finops.budget_guard.public.service.reserve_budget",
                    metadata={"asset_mount": "ContextBudgetProfile"},
                ),
                _capability(
                    capability_id="intercept_illegal_mutations",
                    owner_cell="policy.workspace_guard",
                    contract_name="WorkspaceWriteGuardQueryV1",
                    effect="mutation.guard:workspace",
                    allowed_roles=("architect",),
                    endpoint_ref="polaris.cells.policy.workspace_guard.public.service.check_workspace_write_guard",
                    metadata={"asset_mount": "MutationBoundaryMap"},
                ),
                _capability(
                    capability_id="validate_cell_boundary_change",
                    owner_cell="architect.design",
                    contract_name="GenerateArchitectureDesignCommandV1",
                    effect="architect.validate_cell_boundary",
                    allowed_roles=("architect",),
                    endpoint_ref="polaris.cells.architect.design.public.service.generate_architecture_design",
                    metadata={
                        "requires_asset_mounts": ("ConstraintTopology", "MutationBoundaryMap"),
                        "permission_contract": "EvaluatePermissionCommandV1",
                    },
                ),
            )
        ),
        default_capability_id="intercept_illegal_mutations",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_architecture"),
        metadata={"owner_cell": "roles.runtime", "business_role": "architect"},
    )


def _build_qa_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="qa",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "TruthLog",
                    _asset_ref(
                        asset_id="truth-log",
                        owner_cell="audit.evidence",
                        contract_name="QueryEvidenceEventsV1",
                        ref="audit.evidence:runtime/evidence",
                        asset_kind="truth_log",
                        metadata={
                            "runtime_receipt_owner_cell": "factory.cognitive_runtime",
                            "runtime_receipt_ref": "factory.cognitive_runtime:receipt:truth-log",
                        },
                    ),
                ),
                _mount(
                    "RegressionTestRegistry",
                    _asset_ref(
                        asset_id="regression-test-registry",
                        owner_cell="qa.audit_verdict",
                        contract_name="RunQaAuditCommandV1",
                        ref="qa.audit_verdict:regression-test-registry",
                        asset_kind="regression_test_registry",
                        metadata={"verification_owner_cell": "factory.verification_guard"},
                    ),
                ),
                _mount(
                    "FailureSignalIndex",
                    _asset_ref(
                        asset_id="failure-signal-index",
                        owner_cell="qa.audit_verdict",
                        contract_name="RunQaAuditCommandV1",
                        ref="qa.audit_verdict:failure-signal-index",
                        asset_kind="failure_signal_index",
                        metadata={"evidence_owner_cell": "audit.evidence"},
                    ),
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="invoke_container_pytest",
                    owner_cell="factory.verification_guard",
                    contract_name="VerifyCompletionCommandV1",
                    effect="process.spawn:qa/pytest",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.factory.verification_guard.public.service.verify_completion",
                    metadata={"output_contract": "VerifyCompletionResultV1"},
                ),
                _capability(
                    capability_id="parse_traceback_frames",
                    owner_cell="qa.audit_verdict",
                    contract_name="ParseTracebackFramesCommandV1",
                    effect="qa.failure_signal.parse",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.qa.audit_verdict.public.service.parse_traceback_frames",
                    metadata={"output_asset_mount": "FailureSignalIndex", "output_contract": "FailureSignalV1"},
                ),
                _capability(
                    capability_id="issue_audit_verdict",
                    owner_cell="qa.audit_verdict",
                    contract_name="RunQaAuditCommandV1",
                    effect="qa.verdict.issue",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.qa.audit_verdict.public.contracts.RunQaAuditCommandV1",
                    metadata={"output_contract": "QaAuditResultV1"},
                ),
                _capability(
                    capability_id="issue_visual_audit_verdict",
                    owner_cell="qa.audit_verdict",
                    contract_name="RunVisualQaAuditCommandV1",
                    effect="llm.invoke:vision",
                    allowed_roles=("qa",),
                    endpoint_ref="polaris.cells.qa.audit_verdict.public.service.run_visual_qa_audit",
                    metadata={
                        "input_asset_mount": "TruthLog",
                        "model_capability_query": "CheckLlmModelCapabilityQueryV1",
                        "required_model_capability": "image_input",
                        "output_contract": "VisualQaAuditResultV1",
                    },
                ),
                *_task_market_lifecycle_capabilities(("qa",)),
            )
        ),
        default_capability_id="invoke_container_pytest",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_qa"),
        metadata={"owner_cell": "roles.runtime", "business_role": "qa"},
    )


def _build_director_runtime_spec() -> RoleRuntimeObjectSpec:
    return RoleRuntimeObjectSpec(
        role_id="director",
        asset_mounts=RoleAssetMountTable(
            mounts=(
                _mount(
                    "ExecutionTask",
                    _asset_ref(
                        asset_id="director-execution-task",
                        owner_cell="runtime.task_market",
                        contract_name="ClaimTaskWorkItemCommandV1",
                        ref="runtime.task_market:director/execution-task",
                        asset_kind="task_market_work_item",
                        metadata={"status_contract": "QueryTaskMarketStatusV1"},
                    ),
                ),
                _mount(
                    "DirectorExecutionState",
                    _asset_ref(
                        asset_id="director-execution-state",
                        owner_cell="director.execution",
                        contract_name="GetDirectorTaskStatusQueryV1",
                        ref="director.execution:runtime/state",
                        asset_kind="director_execution_state",
                    ),
                ),
                _mount(
                    "DirectorEvidenceTrail",
                    _asset_ref(
                        asset_id="director-evidence-trail",
                        owner_cell="audit.evidence",
                        contract_name="AppendEvidenceEventCommandV1",
                        ref="audit.evidence:director-execution",
                        asset_kind="director_evidence_trail",
                        metadata={"query_contract": "QueryEvidenceEventsV1"},
                    ),
                    access_mode="write",
                ),
            )
        ),
        capability_ports=RoleCapabilityPorts(
            capabilities=(
                _capability(
                    capability_id="execute_director_task",
                    owner_cell="director.execution",
                    contract_name="ExecuteDirectorTaskCommandV1",
                    effect="process.spawn:director/*",
                    allowed_roles=("director",),
                    endpoint_ref="polaris.cells.director.execution.public.service.execute_director_task",
                    metadata={
                        "requires_asset_mounts": ("ExecutionTask", "DirectorExecutionState"),
                        "evidence_asset_mount": "DirectorEvidenceTrail",
                        "output_contract": "DirectorExecutionResultV1",
                    },
                ),
                *_task_market_lifecycle_capabilities(("director",)),
            )
        ),
        default_capability_id="execute_director_task",
        task_market_binding=RoleTaskMarketBinding(work_item_ref="runtime.task_market:pending_exec"),
        metadata={"owner_cell": "roles.runtime", "business_role": "director"},
    )


def get_builtin_role_runtime_spec(role_id: str) -> RoleRuntimeObjectSpec:
    """Return the built-in role runtime composition spec for a known role."""
    normalized = _require_non_empty("role_id", role_id).lower().replace("-", "_")
    aliases = {
        "project_manager": "pm",
        "ce": "chief_engineer",
        "chiefengineer": "chief_engineer",
        "architecture": "architect",
        "design_architect": "architect",
        "quality_assurance": "qa",
        "auditor": "qa",
        "director_execution": "director",
        "executor": "director",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == "pm":
        return _build_pm_runtime_spec()
    if normalized == "chief_engineer":
        return _build_chief_engineer_runtime_spec()
    if normalized == "architect":
        return _build_architect_runtime_spec()
    if normalized == "qa":
        return _build_qa_runtime_spec()
    if normalized == "director":
        return _build_director_runtime_spec()
    raise KeyError(normalized)
