# Chief Engineer Director Toggle Status Evidence

Date: 2026-05-23

## Scope

- Frontend: `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
- Regression: `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
- Reused backend route: `/v2/director/status?source=auto`

## Root Cause

The Chief Engineer desktop could start or stop Director through `onToggleDirector`, but the control did not read back the canonical Director process status after the action. This left the CE page dependent on optimistic UI state and realtime worker heartbeats, which is weaker evidence than the existing backend status endpoint.

## Fix

- Added a post-toggle status query through the existing `getDirectorStatus()` service.
- Added `chief-engineer-director-status-evidence` to show the exact endpoint, running state, pid, mode, and source returned by the backend.
- Disabled the action button while the toggle/status readback is in flight.
- Kept the existing blueprint handoff guard unchanged: CE still cannot start Director without real blueprint evidence.

## Verification

- `npx eslint src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
- `npm test -- src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`

Both commands passed on 2026-05-23.
