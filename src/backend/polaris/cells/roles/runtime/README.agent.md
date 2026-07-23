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
- `task_market.claim`
- `task_market.lease`
- `task_market.ack`
- `task_market.fail`
- `task_market.requeue`
- `task_market.dead_letter`
- `runtime_projection.read`
- `blueprint.generate:*`
- `blueprint.memo.record`
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
- `handoff.rehydrate`

## Public Contracts
Defined in `public/contracts.py`:
- `InstantiateRoleRuntimeObjectCommandV1`
- `ExecuteRoleTaskMarketLifecycleCommandV1`
- `ExecuteRoleCapabilityInvocationCommandV1`
- `AssembleRoleRuntimeChainCommandV1`
- `RoleStateCommitRequest`
- `RehydrateRoleHandoffCommandV1`
- `ExecuteRoleTaskCommandV1`
- `ExecuteRoleSessionCommandV1`
- `AggregateChatCompletionsCommandV1`
- `GetRoleRuntimeStatusQueryV1`
- `BuildAggregateRolePlanQueryV1`
- `AuditAggregateRuntimeIntegrationsQueryV1`
- `RoleRuntimeObjectResultV1`
- `RoleTaskMarketLifecycleResultV1`
- `RoleCapabilityInvocationResultV1`
- `RoleRuntimeChainAssemblyResultV1`
- `RoleStateCommitReceipt`
- `RoleHandoffRehydrationResultV1`
- `RoleTaskStartedEventV1`
- `RoleTaskCompletedEventV1`
- `RoleExecutionResultV1`
- `AggregateRolePlanResultV1`
- `AggregateRoleLobeV1`
- `AggregateCognitiveLedgerEntryV1`
- `AggregateTakeoverDirectiveV1`
- `AggregateRuntimeIntegrationV1`
- `AggregateRuntimeEntrypointCheckV1`
- `AggregateRuntimeAuditResultV1`
- `AggregateChatCompletionsResultV1`
- `AggregateChatChoiceV1`
- `AggregateChatMessageV1`
- `RoleRuntimeErrorV1`

## Design Notes
- New code should depend on the public contracts only.
- Guarded Director sessions validate the exact public TaskRuntime execution
  attempt before request preparation or kernel/controller construction; stream,
  non-stream, and direct-stream paths share this fail-closed ordering.
- Direct cross-cell access to `internal/**` is not allowed.
- Latest-only runtime ports do not keep prompt-driven fallback behavior; retired
  adapter call paths are migration targets and must not gain new behavior.
- Role capability ports reject mounted capabilities without an explicit
  `allowed_roles` allow-list, so an empty role scope cannot be interpreted as
  an open runtime API.
- Role capability invocation validates mounted ports, role allow-lists, and
  declared command contracts before delegating to the target Cell public API.
  `RoleCapabilityInvocation` rejects payload refs outside `roles.runtime` or
  `runtime.task_market`, and its fingerprint unlock token must be a 64-character
  hex capability fingerprint value. `RoleCapabilityDecision` evidence refs must
  point to `audit.evidence`. `RoleCapabilityInvocationResultV1` rejects role
  runtime/kernel/profile/session owner cells, requires target result refs to
  point to the declared owner Cell, requires successful results to include both
  target `owner_cell` and `result_ref`, only permits non-result payload refs from
  runtime typed input or task-market refs, and requires unique evidence refs
  owned by `audit.evidence`. Failed capability invocation results must always
  report `allowed=false`; after fingerprint unlock and before any target Cell
  public call, `execute_role_capability_invocation` requires the invocation
  payload ref to match the runtime object's current `RoleTurnContext` typed input
  ref or task refs. The mounted capability port must also match the current
  `RoleCapabilityFingerprint` capability id, effect, and endpoint/tool before
  any target Cell public API is invoked. Capability discoverability is
  represented only by `metadata.capability_available`. The KernelOne release gate enforces this
  with an AST check against public runtime code, so static
  `RoleCapabilityInvocationResultV1(ok=False, allowed=True)` constructors are
  rejected before release.
- Architect boundary validation uses `WorkspaceWriteGuardBatchQueryV1` for
  changed path checks so the runtime does not issue N public guard calls for a
  single boundary request.
- Role object instantiation uses `instantiate_role_runtime_object(InstantiateRoleRuntimeObjectCommandV1)`
  to bind `roles.profile` via `GetRoleProfileQueryV1`; runtime stores only
  profile/tool/prompt/data policy refs and a profile fingerprint.
  `RoleProfileBinding` rejects any owner or profile/policy ref namespace outside
  `roles.profile`. `RoleRuntimeObject` also carries a refs-only
  `RoleTurnContext`; built-in specs derive typed input, context snapshot, and
  task refs from `RoleIdentity` when an explicit turn context is not supplied.
  `RoleRuntimeObject` requires its current `RoleCapabilityFingerprint` to point
  to a mounted capability port and to match that port's effect and endpoint/tool,
  so stale or fabricated fingerprints cannot enter a stateful role instance.
  `RoleCapabilityFingerprint` rejects caller-supplied fingerprint overrides that
  are not the 64-character sha256 value derived from role, capability, effect,
  tool, policy fingerprint, and profile fingerprint fields.
  `InstantiateRoleRuntimeObjectCommandV1` and built-in specs may receive an
  explicit `RoleTaskMarketBinding` during instantiation to bind the runtime
  object to the current Task Market work item or lease refs; the object still
  stores refs only and does not own Task Market state. If that binding names an
  active `runtime.task_market:task:*` work item, `RoleRuntimeObject` requires the
  ref to be present in the current `RoleTurnContext.task_refs`; queue/stage refs
  such as `runtime.task_market:pending_design` remain refs-only routing bindings.
  `RoleRuntimeObjectSpec` and `RoleRuntimeObject` reject any mounted capability
  port whose `allowed_roles` does not include the instantiated role, so a role
  object cannot carry latent RPC/API ports that would be denied only at invocation
  time.
- `RoleTurnEnvelope` rejects identity/profile role mismatches, capability
  invocations whose `role_id` does not match the envelope identity, invocation
  payload refs outside the current typed input or task refs, duplicate
  invocation ids, and task-market active `runtime.task_market:task:*` work refs
  that are not listed in the current turn context task refs, so inconsistent
  typed envelopes cannot enter ledger/commit boundaries.
  `RoleTurnContext` rejects typed input refs outside `roles.runtime` or
  `runtime.task_market`, context snapshot refs outside `context.engine` or
  `roles.session`, handoff refs outside `factory.cognitive_runtime`, and task
  refs outside the latest `runtime.task_market:task:<task_id>` shape.
- Role asset mount tables and capability ports reject `roles.runtime`, role
  adapter/kernel/profile/session cells, and `kernelone.roles` paths as owners.
  Business assets and capabilities must be owned by their real business or
  platform state Cell and mounted only by public contract/ref. Asset mount refs
  also reject role-runtime and KernelOne role-template namespaces, so a business
  owner cannot point at a `roles.runtime:*` or template-backed asset ref. Asset
  mount refs must also use a namespace that matches `owner_cell`; the only
  built-in exception is Architect `ConstraintTopology`, where `context.catalog`
  may expose a graph-derived `docs.graph:*` ref when `graph_source_ref` anchors
  the asset to `docs/graph/**`.
  Capability descriptors reject the same role-runtime and KernelOne role-template
  owner cells before they can be mounted into a port table. Capability
  `endpoint_ref` values must point to the same owner Cell public contract, either
  through `owner_cell:*` logical refs or `polaris.cells.<owner_cell>.public.*`
  module refs. `RoleRuntimeObjectSpec`
  also validates capability metadata such as `requires_asset_mounts`,
  `asset_mount`, `input_asset_mount`, `output_asset_mount`, and
  `evidence_asset_mount` against its `RoleAssetMountTable`, so a capability port
  cannot claim a role asset dependency that is not actually mounted. The KernelOne
  release gate scans all `roles.runtime` owned Python paths from `cell.yaml`
  (`public/**`, `internal/**`, `role_chat.py`, and `role_session.py`) and rejects
  cross-Cell `polaris.cells.*.internal` imports before release. It also scans
  production Python entrypoints under `polaris/application`, `polaris/cells`, and
  `polaris/delivery` and rejects direct `RoleExecutionKernel` imports or
  construction outside the `roles.kernel` owner Cell and
  `roles.runtime.public.service` composition boundary, so new production code
  cannot bypass `RoleRuntimeService` and its runtime-object/session/ledger
  preflight. It also rejects production call sites that import or call the
  `llm.dialogue` role compatibility functions `generate_role_response` and
  `generate_role_response_streaming` outside the `llm.dialogue` owner Cell, so
  prompt-driven role dialogue cannot re-enter as a production route. It also scans
  `polaris/kernelone/roles/**` and rejects business-role filenames or Python
  definitions for PM, Chief Engineer, Architect, QA, and Director so that
  KernelOne stays limited to shared types and low-level role templates.
- Role task-market lifecycle operations use
  `execute_role_task_market_lifecycle(ExecuteRoleTaskMarketLifecycleCommandV1)`
  to translate role-bound publish/claim/lease/ack/fail/requeue/dead-letter
  requests into `runtime.task_market` public contracts. `RoleTaskMarketBinding`
  rejects active work item and lease token refs outside `runtime.task_market`,
  and rejects old active work refs such as `runtime.task_market:task-1`;
  lease/ack/fail/requeue/dead-letter operations additionally require
  `payload.task_id` to resolve to a task ref listed in the runtime object's
  current `RoleTurnContext.task_refs` before the Task Market public service is
  called. They also require a mounted `runtime.task_market` capability port
  whose public contract matches the lifecycle command, whose `allowed_roles`
  contains the current role, and whose capability/effect/tool match the runtime
  object's current `RoleCapabilityFingerprint`. Lease, ack, and fail also
  require `payload.lease_token` to match the runtime object's active
  `RoleTaskMarketBinding.lease_token_ref`; unbound or mismatched leases are
  rejected in `roles.runtime`. Dead-letter operations delegate to
  `MoveTaskToDeadLetterCommandV1` and do not create a second DLQ. Successful
  `RoleTaskMarketLifecycleResultV1`
  values must include a `runtime.task_market` `result_ref`; malformed upstream
  `ok=true` task-market responses without a task id/ref return the structured
  `task_market_lifecycle_missing_result_ref` failure instead of becoming an
  unauditable success. Successful claim/lease lifecycle results must also
  expose a `runtime.task_market` `lease_token_ref`; malformed upstream
  claim/lease successes without a lease token return
  `task_market_lifecycle_missing_lease_ref`.
- Phase 5 chain assembly uses
  `assemble_role_runtime_chain(AssembleRoleRuntimeChainCommandV1)` to assemble
  PM, Chief Engineer, Director, QA, audit evidence, Turn Ledger, receipt,
  handoff, Task Market, Runtime Projection, and per-step capability fingerprint
  refs into a typed `RoleRuntimeChainEnvelope`; it is pure refs-only assembly
  and does not write a second Task Market, ledger, handoff pack, receipt store,
  or projection. Each `RoleRuntimeChainStepRef` must be anchored to
  `runtime.task_market` through a task or work-item ref, and `task_ref` must use
  the latest `runtime.task_market:task:<task_id>` shape. Required role steps must
  preserve the declared Phase 5 order, so PM cannot appear after Chief Engineer,
  Director, or QA in an assembled chain. If all default PM -> Chief Engineer ->
  Director -> QA steps are present, callers cannot downgrade `required_roles` to
  a subset to bypass complete-chain gates. A complete default Phase 5 chain must
  include at least one `runtime.projection` ref so the runtime status endpoint is
  represented by its real owner Cell, at least one `audit.evidence` ref so the
  audit trail has a real owner-backed anchor, per-step `audit.evidence` refs on
  the Director and QA execution/audit steps, typed handoff refs on both
  Chief Engineer -> Director and Director -> QA role transitions, and runtime
  receipt refs from `factory.cognitive_runtime` for Chief Engineer, Director,
  and QA execution steps so typed handoff and receipt systems remain the only
  continuity anchors. `RoleRuntimeChainEnvelope`
  rejects aggregate refs that omit step task/work-item, evidence, capability
  fingerprint, handoff, or runtime receipt refs, and chain steps cannot name role
  runtime/kernel/profile/session or KernelOne role templates as capability owner
  cells. Chain step result refs must point to the declared owner Cell, and
  aggregate refs must point to their real owner namespaces (`roles.kernel`,
  `runtime.task_market`, `audit.evidence`, `runtime.projection`, `roles.runtime`,
  and `factory.cognitive_runtime`). Public chain assembly returns typed
  `RoleRuntimeChainAssemblyResultV1` failures for invalid aggregate owner refs
  such as Turn Ledger, Runtime Projection, or Audit Evidence refs instead of
  leaking `RoleRuntimeChainEnvelope` constructor exceptions to callers.
- Role state commits use `commit_role_state(RoleStateCommitRequest)` to bind an
  existing `roles.kernel` commit receipt to `factory.cognitive_runtime`
  `ValidateChangeSetCommandV1`, `RecordRuntimeReceiptCommandV1`, and
  `ExportHandoffPackCommandV1`; handoff rehydration uses
  `rehydrate_role_handoff(RehydrateRoleHandoffCommandV1)` to delegate to
  `factory.cognitive_runtime` `RehydrateHandoffPackCommandV1`. Runtime does not
  create a second Turn Ledger, change-set validator, handoff system, or receipt
  store. Successful
  ledger/commit refs must point to `roles.kernel`, and runtime receipt,
  handoff, and change-set refs must point to `factory.cognitive_runtime`.
  Rehydrated receipt refs are normalized to `factory.cognitive_runtime`, and
  rehydrated artifact/episode refs are normalized to `roles.session` even when
  Cognitive Runtime returns raw owner ids.
  `RoleStateCommitReceipt` values must include both a kernel commit receipt ref
  and at least one Cognitive Runtime receipt ref.
  `RoleStateCommitRequest` rejects changed asset refs outside the current
  `RoleTurnContext.task_refs`, rejects evidence refs outside `audit.evidence`,
  and requires changed files to be relative paths under explicit
  `allowed_scope_paths` before Cognitive Runtime validation or receipt recording
  can run. It also rejects empty commits that omit changed asset refs, changed
  files, and audit evidence refs, so runtime receipts and handoff packs are not
  created for unanchored no-op state commits. Successful commit receipts reject
  duplicate Cognitive Runtime receipt and handoff refs instead of silently
  collapsing audit anchors.
- PM dispatch delegates to `runtime.task_market`; Chief Engineer diff-spec and
  architecture memo generation delegate to `chief_engineer.blueprint` with
  mounted `BlueprintDatabase`, `ArchConstraintMemo`, and `DiffMapArchive` refs
  in the command context/result metadata. `DiffMapArchive` mounts that declare
  `requires_blueprint_ref` must include `blueprint_id`, `path`, and `ref`
  metadata so diff/spec assets remain line-anchorable to a real blueprint asset
  owned by `chief_engineer.blueprint`.
- PM critical-path evaluation reads `runtime.task_market` through
  `QueryTaskMarketStatusV1` and derives task DAG dependency edges, failed
  stages, projection refs, and mounted asset refs from that public result; PM
  `OpenLoopRegistry` mounts must include an `audit.evidence` `evidence_ref` so
  open-loop task state is anchored to both Task Market and Audit Evidence. PM
  runtime status projection delegates to an injected `runtime.projection` public
  service using `RuntimeProjectionQueryV1`.
- Director task execution mounts `ExecutionTask`, `DirectorExecutionState`, and
  `DirectorEvidenceTrail` refs, then delegates to
  `director.execution.public.service.execute_director_task` with
  `ExecuteDirectorTaskCommandV1`; runtime objects keep only result/evidence refs
  and capability metadata. Owner-service `runtime/evidence/**` paths returned by
  Director are normalized to `audit.evidence:path:*` refs before being exposed in
  `RoleCapabilityInvocationResultV1.evidence_refs`.
- QA pytest verification delegates to `factory.verification_guard` and requires
  both the `qa` role runtime object and QA capability fingerprint before any
  verification command is built, even if a capability port is misconfigured.
- QA traceback parsing delegates to `qa.audit_verdict.public.service.parse_traceback_frames`
  with `ParseTracebackFramesCommandV1`; runtime objects keep only typed signal refs
  and metadata.
- QA verdict issuance delegates to `qa.audit_verdict.public.service.run_qa_audit`
  with `RunQaAuditCommandV1`; runtime objects keep only result refs and metadata.
  Runtime evidence paths from the verdict payload are normalized to
  `audit.evidence:path:*` refs so QA audit results remain anchored to the Audit
  Evidence Cell.
- QA `TruthLog` mounts must include both the `audit.evidence` owner ref and a
  `factory.cognitive_runtime` `runtime_receipt_ref`; runtime objects never treat
  transcript or natural-language handoff text as the receipt truth.
- QA visual audit delegates model feature checks to `llm.control_plane`
  `CheckLlmModelCapabilityQueryV1` and only calls
  `qa.audit_verdict.public.service.run_visual_qa_audit` with
  `RunVisualQaAuditCommandV1` after an explicit `image_input` capability ref is
  returned. The required model capability comes from the mounted capability
  descriptor; payload attempts to downgrade it are denied before model preflight.
  The model capability query is bound to the current runtime `role_id`; payload
  `llm_role` values are retained only as audit metadata and cannot switch the
  preflight role. Text-only models receive `allowed=false` before QA is invoked.
  Successful visual audit results must include an `audit.evidence` evidence ref;
  runtime maps owner-service `runtime/evidence/**` receipt paths to
  `audit.evidence:path:*` refs and rejects unaudited success results.
- Architect context-budget allocation delegates to `finops.budget_guard`; illegal
  mutation interception delegates to `policy.workspace_guard`.
- Architect Cell boundary validation delegates lightweight authorization to
  `policy.permission`, checks unique changed paths through `policy.workspace_guard`,
  and invokes `architect.design` through `GenerateArchitectureDesignCommandV1`.
  Empty `changed_paths` requests are denied before permission, guard, or design
  calls so mutation validation cannot bypass workspace guard. Denied sandbox
  checks return `allowed=false`; mounted capability discoverability is
  represented only by `metadata.capability_available`.
- Product host direction is `polaris-cli` under `polaris/delivery/cli/`:
  one host, multi-role, multi-mode.
