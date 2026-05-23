# Director Workbench Run Cancel Desktop Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The Director workbench can export a RoleSession to workflow and display
`GET /v2/director/runs/{run_id}` evidence, but it cannot cancel that visible
run from the same workbench surface. The backend and typed frontend service
already expose `POST /v2/director/runs/{run_id}/cancel`, and DirectorWorkspace
already has a run-scoped cancel action. The workbench needs the same control so
RoleSession-originated Director runs can be managed without switching surfaces.

## Scope

This change is limited to Director workbench run cancellation:

- Reuse the existing typed `cancelDirectorRun` frontend service wrapper.
- Add run-cancel state to `DirectorWorkbenchPanel`.
- Add a compact cancel action to the Director workbench run evidence strip.
- Show `/v2/director/runs/{run_id}/cancel` evidence and resulting status.

No backend route, orchestration runtime, role session contract, or target
project code is changed.

## Architecture Sketch

```text
DirectorWorkbenchPanel
  -> exportRoleSessionToWorkflow(session_id)
      -> returns run_id
  -> getDirectorRun(run_id)
      -> GET /v2/director/runs/{run_id}
  -> cancelDirectorRun(run_id)
      -> POST /v2/director/runs/{run_id}/cancel
      -> DirectorOrchestrationRunResponse
```

The backend remains the source of truth for Director run state. The workbench
only adds parity for the existing run cancel capability.

## Assumption Register

- `cancelDirectorRun` is already exported from the typed service layer.
- Workbench run evidence represents a single Director orchestration run and can
  safely host a run-scoped cancel action.
- Terminal statuses should disable the cancel action in the desktop UI.
- Cancellation responses use the same response shape as run detail responses.

## Pre-Mortem

- Risk: Users could confuse session export with run cancellation.
  Mitigation: Keep cancel evidence in the run evidence strip and show the exact
  `/v2/director/runs/{run_id}/cancel` endpoint.
- Risk: A terminal run could still show an enabled cancel action.
  Mitigation: Disable cancel for completed, failed, cancelled, blocked, and
  timeout statuses.
- Risk: The workbench test could miss service-level integration.
  Mitigation: Add a test that exports, loads run evidence, clicks cancel, and
  verifies the typed service call plus updated evidence.

## Verification Plan

- `npm run test -- DirectorWorkbenchPanel pmService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
