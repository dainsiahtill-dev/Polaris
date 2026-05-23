# Role Desktop Workbench Run Evidence

Date: 2026-05-23

## Scope

Expose authoritative PM and Director orchestration run snapshots after workbench session export creates a workflow run.

## Backend Contracts

- PM: `GET /v2/pm/runs/{run_id}`
- Director: `GET /v2/director/runs/{run_id}`
- Routers:
  - `src/backend/polaris/delivery/http/v2/pm.py`
  - `src/backend/polaris/delivery/http/v2/director.py`

## Desktop Behavior

- `pmService.getPmRun(runId)` reads the PM run snapshot with an encoded run id.
- `pmService.getDirectorRun(runId)` reads the Director run snapshot with an encoded run id.
- PM Workbench shows the PM run endpoint, status, and stage after exporting a RoleSession to workflow.
- Director Workbench shows the Director run endpoint, status, and queued task count after exporting a RoleSession to workflow.
- If the detail snapshot is unavailable, the workbench keeps the exported run id visible and renders the backend error as evidence.

## Verification

- Service tests cover encoded PM and Director run-detail calls.
- PM Workbench test covers post-export PM run evidence.
- Director Workbench test covers post-export Director run evidence.
