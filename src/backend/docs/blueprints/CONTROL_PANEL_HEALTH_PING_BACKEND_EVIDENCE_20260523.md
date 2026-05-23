# Control Panel Health Ping Backend Evidence

Date: 2026-05-23

## Scope

- Frontend App wiring: `src/frontend/src/app/App.tsx`
- Header control: `src/frontend/src/app/components/ControlPanel.tsx`
- Hook: `src/frontend/src/hooks/useBackendHealthPing.ts`
- Reused backend route: `/v2/health`

## Root Cause

The top-level desktop health button was rendered as an active connectivity control, but `App.tsx` passed an empty `onPingHealth` callback. Clicking it did not verify backend reachability, did not surface backend evidence, and left the visual health state disconnected from the canonical health route.

## Fix

- Added `useBackendHealthPing()` to call the existing `healthV2Service.check()` route.
- Wired the live App header health control to `backendHealth.ping()`.
- Passed health status and endpoint evidence into `ControlPanel`.
- Updated the health button tooltip and status tone to distinguish `checking`, `healthy`, and `unhealthy`.
- Extended `HealthV2Response` to include the current `/v2/health` payload fields returned by the backend.

## Verification

- `npx eslint src/frontend/src/app/App.tsx src/frontend/src/app/components/ControlPanel.tsx src/frontend/src/app/components/ControlPanel.test.tsx src/frontend/src/hooks/useBackendHealthPing.ts src/frontend/src/hooks/useBackendHealthPing.test.ts`
- `npm test -- src/hooks/useBackendHealthPing.test.ts src/app/components/ControlPanel.test.tsx`

Both commands passed on 2026-05-23.
