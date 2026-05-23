# PM Desktop Orchestration Run Control - 2026-05-23

## Scope

- Expose the existing backend `POST /v2/pm/run` orchestration contract in the PM desktop workbench.
- Keep the backend write path unchanged: the desktop calls the typed service and then verifies the returned `run_id` through `GET /v2/pm/runs/{run_id}`.

## Implementation

- `src/frontend/src/services/pmService.ts`
  - Added `RunPmPayload`.
  - Added `runPm(payload)` mapped to `POST /v2/pm/run`.
- `src/frontend/src/services/index.ts`
  - Re-exported `runPm` and `RunPmPayload`.
- `src/frontend/src/app/components/pm/PMWorkbenchPanel.tsx`
  - Added a compact orchestration strip for directive, stage, Director handoff, and launch.
  - Reused the run evidence reader so direct PM runs and RoleSession workflow exports both render backend run detail from `/v2/pm/runs/{run_id}`.

## Verification Targets

- Service contract test covers the exact `/v2/pm/run` path and payload.
- PM workbench test covers the desktop control, typed service payload, toast evidence, and run detail fetch.
- Existing backend router tests already cover `/v2/pm/run` and `/v2/pm/runs/{run_id}`.
