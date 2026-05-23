# Director Desktop Task Detail Backend Panel

Date: 2026-05-23

## Scope

Expose the existing single-task Director backend snapshot in the Electron Director task detail view.

## Backend Contract

- Route: `GET /v2/director/tasks/{task_id}`
- Router: `src/backend/polaris/delivery/http/v2/director.py`
- Payload: `TaskResponse` with task identity, status, priority, worker, PM linkage, blueprint linkage, goal, acceptance, target files, dependencies, current file, error, result, and metadata.

## Desktop Behavior

- `pmService.getDirectorTask(taskId)` URI-encodes the selected task id and reads the backend detail endpoint.
- The Director workspace refreshes this backend snapshot whenever the selected task changes.
- The selected task detail panel renders endpoint provenance, status, priority, worker, PM/blueprint linkage, goal/current file, and acceptance count.
- If the backend cannot resolve the task, the panel keeps the local runtime projection visible and shows the backend error as evidence.

## Verification

- Service test covers encoded task-detail calls.
- Panel test covers selected-task backend detail rendering.
- Workspace test covers selected-task fetch wiring through the mocked backend service.
