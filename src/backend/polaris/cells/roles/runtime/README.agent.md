# Roles Runtime Cell

## Objective
Provide a shared role kernel for role lifecycle execution and role-session
management. This cell owns generic runtime mechanics, while business role
behavior stays in dedicated cells (`orchestration.pm_planning`,
`director.execution`, `qa.audit_verdict`, `chief_engineer.blueprint`).

## Boundaries
- Owns role-kernel internals under `polaris/cells/roles/runtime/internal/**`.
- Owns role session lifecycle services:
  - `polaris/cells/roles/session/internal/role_session_service.py`
  - `polaris/cells/roles/session/internal/artifact_service.py`
  - `polaris/cells/audit/evidence/internal/role_session_audit_service.py`
- Owns delivery endpoints for role session and role status:
  - `polaris/delivery/http/routers/role_chat.py`
  - `polaris/delivery/http/routers/role_session.py`

## State Ownership
- `runtime/roles/*`
- `runtime/role_sessions/*`

## Allowed Effects
- `fs.read:runtime/**`
- `fs.write:runtime/tasks/*`
- `fs.write:runtime/state/*`
- `fs.write:runtime/events/*`
- `ws.outbound:runtime/*`
- `process.spawn:roles/*`
- `task_market.publish:*`
- `task_market.read`
- `runtime_projection.read`
- `blueprint.generate:*`
- `process.spawn:qa/pytest`
- `qa.failure_signal.parse`
- `qa.verdict.issue`
- `budget.reserve:context`
- `mutation.guard:workspace`
- `architect.validate_cell_boundary`

## Public Contracts
Defined in `public/contracts.py`:
- `InstantiateRoleRuntimeObjectCommandV1`
- `ExecuteRoleTaskMarketLifecycleCommandV1`
- `ExecuteRoleCapabilityInvocationCommandV1`
- `RoleStateCommitRequest`
- `ExecuteRoleTaskCommandV1`
- `ExecuteRoleSessionCommandV1`
- `GetRoleRuntimeStatusQueryV1`
- `RoleRuntimeObjectResultV1`
- `RoleTaskMarketLifecycleResultV1`
- `RoleCapabilityInvocationResultV1`
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
- Role object instantiation uses `instantiate_role_runtime_object(InstantiateRoleRuntimeObjectCommandV1)`
  to bind `roles.profile` via `GetRoleProfileQueryV1`; runtime stores only
  profile/tool/prompt/data policy refs and a profile fingerprint.
- Role task-market lifecycle operations use
  `execute_role_task_market_lifecycle(ExecuteRoleTaskMarketLifecycleCommandV1)`
  to translate role-bound claim/lease/ack/fail/requeue requests into
  `runtime.task_market` public contracts.
- Role state commits use `commit_role_state(RoleStateCommitRequest)` to bind an
  existing `roles.kernel` commit receipt to `factory.cognitive_runtime`
  `RecordRuntimeReceiptCommandV1` and `ExportHandoffPackCommandV1`; runtime does
  not create a second Turn Ledger or receipt store.
- PM dispatch delegates to `runtime.task_market`; Chief Engineer diff-spec
  generation delegates to `chief_engineer.blueprint`.
- PM critical-path evaluation reads `runtime.task_market` through
  `QueryTaskMarketStatusV1`; PM runtime status projection delegates to an
  injected `runtime.projection` public service using `RuntimeProjectionQueryV1`.
- QA pytest verification delegates to `factory.verification_guard` and requires
  both the `qa` role runtime object and QA capability fingerprint before any
  verification command is built, even if a capability port is misconfigured.
- QA traceback parsing delegates to `qa.audit_verdict.public.service.parse_traceback_frames`
  with `ParseTracebackFramesCommandV1`; runtime objects keep only typed signal refs
  and metadata.
- QA verdict issuance delegates to `qa.audit_verdict.public.service.run_qa_audit`
  with `RunQaAuditCommandV1`; runtime objects keep only result refs and metadata.
- Architect context-budget allocation delegates to `finops.budget_guard`; illegal
  mutation interception delegates to `policy.workspace_guard`.
- Architect Cell boundary validation delegates lightweight authorization to
  `policy.permission`, checks unique changed paths through `policy.workspace_guard`,
  and invokes `architect.design` through `GenerateArchitectureDesignCommandV1`.
  Denied sandbox checks return `allowed=false`; mounted capability discoverability
  is represented only by `metadata.capability_available`.
- Product host direction is `polaris-cli` under `polaris/delivery/cli/`:
  one host, multi-role, multi-mode.
