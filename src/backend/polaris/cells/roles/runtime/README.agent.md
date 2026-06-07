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
- `blueprint.generate:*`
- `process.spawn:qa/pytest`
- `qa.verdict.issue`
- `budget.reserve:context`
- `mutation.guard:workspace`
- `architect.validate_cell_boundary`

## Public Contracts
Defined in `public/contracts.py`:
- `ExecuteRoleCapabilityInvocationCommandV1`
- `ExecuteRoleTaskCommandV1`
- `ExecuteRoleSessionCommandV1`
- `GetRoleRuntimeStatusQueryV1`
- `RoleCapabilityInvocationResultV1`
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
- PM dispatch delegates to `runtime.task_market`; Chief Engineer diff-spec
  generation delegates to `chief_engineer.blueprint`.
- QA pytest verification delegates to `factory.verification_guard` and requires
  the QA capability fingerprint before any verification command is built.
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
