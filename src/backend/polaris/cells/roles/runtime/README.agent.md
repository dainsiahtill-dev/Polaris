# Roles Runtime Cell

## Objective
Provide a shared role kernel for role lifecycle execution and role-session
management. This cell owns generic runtime mechanics, while business role
behavior stays in dedicated cells (`orchestration.pm_planning`,
`director.execution`, `qa.audit_verdict`, `chief_engineer.blueprint`).

## Boundaries
- Owns role-kernel internals under `polaris/cells/roles/runtime/internal/**`.
- Composes role session lifecycle through `roles.session` public contracts; it
  must not claim or write `roles.session/internal/**` state.
- Composes audit evidence through `audit.evidence` public contracts; it must not
  claim or write `audit.evidence/internal/**` state.
- Owns delivery endpoints for role session and role status:
  - `polaris/delivery/http/routers/role_chat.py`
  - `polaris/delivery/http/routers/role_session.py`

## State Ownership
- `runtime/roles/*`

## Allowed Effects
- `fs.read:runtime/**`
- `fs.write:runtime/roles/*`
- `fs.write:runtime/tasks/*`
- `fs.write:runtime/state/*`
- `fs.write:runtime/events/*`
- `fs.write:runtime/strategy_runs/*`
- `fs.write:runtime/cognitive_runtime/*`
- `ws.outbound:runtime/*`
- `process.spawn:roles/*`
- `process.spawn:director/*`
- `bus.publish:agent_messages/*`
- `bus.consume:agent_messages/*`
- `task_market.publish:*`
- `task_market.read`
- `runtime_projection.read`
- `blueprint.generate:*`
- `process.spawn:qa/pytest`
- `qa.failure_signal.parse`
- `qa.verdict.issue`
- `llm.invoke:vision`
- `budget.reserve:context`
- `mutation.guard:workspace`
- `architect.validate_cell_boundary`
- `code_intelligence.read`
- `change_set.validate`
- `runtime_receipt.record`
- `handoff.export`

## Public Contracts
Defined in `public/contracts.py`:
- `InstantiateRoleRuntimeObjectCommandV1`
- `ExecuteRoleTaskMarketLifecycleCommandV1`
- `ExecuteRoleCapabilityInvocationCommandV1`
- `AssembleRoleRuntimeChainCommandV1`
- `RoleStateCommitRequest`
- `ExecuteRoleTaskCommandV1`
- `ExecuteRoleSessionCommandV1`
- `GetRoleRuntimeStatusQueryV1`
- `RoleRuntimeObjectResultV1`
- `RoleTaskMarketLifecycleResultV1`
- `RoleCapabilityInvocationResultV1`
- `RoleRuntimeChainAssemblyResultV1`
- `RoleStateCommitReceipt`
- `RoleTaskStartedEventV1`
- `RoleTaskCompletedEventV1`
- `RoleExecutionResultV1`
- `RoleRuntimeErrorV1`

## Design Notes
- New code should depend on the public contracts only.
- Direct cross-cell access to `internal/**` is not allowed.
- Latest-only runtime ports do not keep prompt-driven fallback behavior; legacy
  adapter call paths are migration targets and must not gain new behavior.
- Role capability invocation validates mounted ports, role allow-lists, and
  declared command contracts before delegating to the target Cell public API.
- Architect boundary validation uses `WorkspaceWriteGuardBatchQueryV1` for
  changed path checks so the runtime does not issue N public guard calls for a
  single boundary request.
- Role object instantiation uses `instantiate_role_runtime_object(InstantiateRoleRuntimeObjectCommandV1)`
  to bind `roles.profile` via `GetRoleProfileQueryV1`; runtime stores only
  profile/tool/prompt/data policy refs and a profile fingerprint.
- Role asset mount tables and capability ports reject `roles.runtime`, role
  adapter/kernel/profile/session cells, and `kernelone.roles` paths as owners.
  Business assets and capabilities must be owned by their real business or
  platform state Cell and mounted only by public contract/ref.
- Role task-market lifecycle operations use
  `execute_role_task_market_lifecycle(ExecuteRoleTaskMarketLifecycleCommandV1)`
  to translate role-bound claim/lease/ack/fail/requeue requests into
  `runtime.task_market` public contracts.
- Phase 5 chain assembly uses
  `assemble_role_runtime_chain(AssembleRoleRuntimeChainCommandV1)` to assemble
  PM, Chief Engineer, Director, QA, audit evidence, Turn Ledger, receipt,
  handoff, Task Market, and Runtime Projection refs into a typed
  `RoleRuntimeChainEnvelope`; it is pure refs-only assembly and does not write a
  second Task Market, ledger, handoff pack, receipt store, or projection.
- Role state commits use `commit_role_state(RoleStateCommitRequest)` to bind an
  existing `roles.kernel` commit receipt to `factory.cognitive_runtime`
  `ValidateChangeSetCommandV1`, `RecordRuntimeReceiptCommandV1`, and
  `ExportHandoffPackCommandV1`; runtime does not create a second Turn Ledger,
  change-set validator, handoff system, or receipt store.
- PM dispatch delegates to `runtime.task_market`; Chief Engineer diff-spec and
  architecture memo generation delegate to `chief_engineer.blueprint` with
  mounted `BlueprintDatabase`, `ArchConstraintMemo`, and `DiffMapArchive` refs
  in the command context/result metadata.
- PM critical-path evaluation reads `runtime.task_market` through
  `QueryTaskMarketStatusV1` and derives task DAG dependency edges, failed
  stages, projection refs, and mounted asset refs from that public result; PM
  runtime status projection delegates to an injected `runtime.projection` public
  service using `RuntimeProjectionQueryV1`.
- Director task execution mounts `ExecutionTask`, `DirectorExecutionState`, and
  `DirectorEvidenceTrail` refs, then delegates to
  `director.execution.public.service.execute_director_task` with
  `ExecuteDirectorTaskCommandV1`; runtime objects keep only result/evidence refs
  and capability metadata.
- QA pytest verification delegates to `factory.verification_guard` and requires
  both the `qa` role runtime object and QA capability fingerprint before any
  verification command is built, even if a capability port is misconfigured.
- QA traceback parsing delegates to `qa.audit_verdict.public.service.parse_traceback_frames`
  with `ParseTracebackFramesCommandV1`; runtime objects keep only typed signal refs
  and metadata.
- QA verdict issuance delegates to `qa.audit_verdict.public.service.run_qa_audit`
  with `RunQaAuditCommandV1`; runtime objects keep only result refs and metadata.
- QA visual audit delegates model feature checks to `llm.control_plane`
  `CheckLlmModelCapabilityQueryV1` and only calls
  `qa.audit_verdict.public.service.run_visual_qa_audit` with
  `RunVisualQaAuditCommandV1` after an explicit `image_input` capability ref is
  returned. Text-only models receive `allowed=false` before QA is invoked.
- Architect context-budget allocation delegates to `finops.budget_guard`; illegal
  mutation interception delegates to `policy.workspace_guard`.
- Architect Cell boundary validation delegates lightweight authorization to
  `policy.permission`, checks unique changed paths through `policy.workspace_guard`,
  and invokes `architect.design` through `GenerateArchitectureDesignCommandV1`.
  Denied sandbox checks return `allowed=false`; mounted capability discoverability
  is represented only by `metadata.capability_available`.
- Product host direction is `polaris-cli` under `polaris/delivery/cli/`:
  one host, multi-role, multi-mode.
