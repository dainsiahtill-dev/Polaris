# Director Desktop Task Cancel Control

Date: 2026-05-23

## Scope

Expose the existing Director backend task cancellation contract in the Electron Director task detail view without changing the backend execution model.

## Backend Contract

- Route: `POST /v2/director/tasks/{task_id}/cancel`
- Router: `src/backend/polaris/delivery/http/v2/director.py`
- Response evidence: `{ "ok": true, "task_id": "<task id>" }` when the task service accepts cancellation.

## Desktop Behavior

- `pmService.cancelDirectorTask(taskId)` uses a no-body POST and URI-encodes the selected task id.
- The selected task detail panel shows a dedicated cancel control next to the existing execute control.
- The detail panel renders the exact cancel endpoint as operator evidence.
- The workspace records submitted, accepted, and failed cancel requests in the Director terminal output.
- After an accepted cancel request, the workspace refreshes fallback task rows through the existing Director fallback projection.

## Verification

- Service test covers encoded no-body cancellation calls.
- Panel test covers endpoint evidence and selected-task callback wiring.
- Workspace test covers end-to-end desktop submission through the mocked backend service.
