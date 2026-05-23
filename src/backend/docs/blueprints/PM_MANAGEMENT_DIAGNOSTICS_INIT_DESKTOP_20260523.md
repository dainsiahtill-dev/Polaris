# PM Management Diagnostics Init Desktop Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The backend PM management router exposes workspace-level PM management
contracts for status, project health, and initialization:

- `GET /pm/v2/pm/status`
- `GET /pm/v2/pm/health`
- `POST /pm/v2/pm/init`

The PM desktop diagnostics modal already checks startup dependencies and
Kernel metrics, but it does not expose the PM management status or provide a
desktop path to initialize PM when the management store is not initialized. This
leaves an operator unable to close the loop from the diagnostics surface.

## Scope

This change is limited to PM desktop diagnostics:

- Add typed frontend wrappers for PM management status, health, and init.
- Load PM management status when the diagnostics modal opens.
- Load PM project health only when PM management reports initialized.
- Render endpoint evidence for the management status and health calls.
- Provide a guarded init form that calls `POST /pm/v2/pm/init` when the PM
  management store is uninitialized.
- Refresh PM management diagnostics after a successful init.

No backend route, graph edge, Cell contract, or PM management behavior changes
are required.

## Architecture Sketch

```text
PMDiagnosticsPanel
  -> getPmManagementStatus()
      -> GET /pm/v2/pm/status
  -> initialized ? getPmManagementHealth() : skip health
      -> GET /pm/v2/pm/health
  -> initializePmManagement(projectName, description)
      -> POST /pm/v2/pm/init
      -> reload management diagnostics
```

The backend remains the source of truth for PM initialization and project health.
The desktop panel only exposes the existing contracts with explicit endpoint
evidence.

## Assumption Register

- `/pm/v2/pm/status` is the PM management status route and can return
  `initialized=false` without failing.
- `/pm/v2/pm/health` requires initialized PM management state and returns
  health only after initialization.
- `/pm/v2/pm/init` accepts project name and description as query parameters.
- PMDiagnosticsPanel is the correct desktop surface for startup and management
  readiness remediation.

## Verification Plan

- `npm run test -- PMDiagnosticsPanel pmService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
