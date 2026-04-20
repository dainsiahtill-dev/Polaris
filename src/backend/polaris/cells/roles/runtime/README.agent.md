# Roles Runtime Cell

## Objective
Provide a shared role kernel for role lifecycle execution and role-session
management. This cell owns generic runtime mechanics, while business role
behavior stays in dedicated cells (`orchestration.pm_planning`,
`director.execution`, `qa.audit_verdict`, `chief_engineer.blueprint`).

## Boundaries
- Owns role-kernel internals under `polaris/cells/roles/runtime/internal/**`.
- Owns role session lifecycle services:
  - `polaris/application/services/role_session_service.py`
  - `polaris/application/services/role_session_artifact_service.py`
  - `polaris/application/services/role_session_audit_service.py`
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

## Public Contracts
Defined in `public/contracts.py`:
- `ExecuteRoleTaskCommandV1`
- `ExecuteRoleSessionCommandV1`
- `GetRoleRuntimeStatusQueryV1`
- `RoleTaskStartedEventV1`
- `RoleTaskCompletedEventV1`
- `RoleExecutionResultV1`
- `RoleRuntimeErrorV1`

## Design Notes
- New code should depend on the public contracts only.
- Direct cross-cell access to `internal/**` is not allowed.
- Compatibility call paths should be gradually migrated to command/query
  contracts instead of implicit service access.
- Product host direction is `polaris-cli` under `polaris/delivery/cli/`:
  one host, multi-role, multi-mode.
- Role-specific Textual / standalone hosts under `internal/tui_console.py`
  and `internal/standalone_entry.py` are frozen legacy test windows only.
