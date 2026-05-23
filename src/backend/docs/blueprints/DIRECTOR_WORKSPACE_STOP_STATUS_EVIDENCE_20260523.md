# Director Workspace Stop Status Evidence

Date: 2026-05-23

## Scope

- Frontend: `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- Regression: `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`
- Reused backend route: `/v2/director/status?source=auto`

## Root Cause

The Director workspace created auditable run evidence for queue/task execution through `/v2/director/runs/{run_id}`, but stopping an already running Director still only invoked the process toggle callback. The desktop had no immediate canonical backend readback proving whether the stop action left Director running or idle.

## Fix

- Added a shared stop/toggle readback path that calls the existing `getDirectorStatus()` service after `onToggleDirector`.
- Added `director-toggle-status-evidence` with the exact endpoint, running state, pid, mode, and source.
- Applied the same readback to both the main execute/stop button and the pause/stop icon.
- Disabled the stop controls while the toggle/readback is in flight.

## Verification

- `npx eslint src/frontend/src/app/components/director/DirectorWorkspace.tsx src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`
- `npm test -- src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`

Both commands passed on 2026-05-23.
