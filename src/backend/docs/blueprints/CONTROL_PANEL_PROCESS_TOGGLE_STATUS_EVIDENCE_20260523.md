# Control Panel Process Action Status Evidence

Date: 2026-05-23

## Scope

- Frontend: `src/frontend/src/app/components/ControlPanel.tsx`
- Regression: `src/frontend/src/app/components/ControlPanel.test.tsx`
- Reused backend routes:
  - `/v2/pm/status`
  - `/v2/director/status?source=auto`

## Root Cause

The top-level desktop header could start or stop PM and Director through callback props, but it did not read back canonical backend process status after those actions. PM single-iteration execution used the same optimistic-only pattern. Role workspaces now expose status evidence, so the header controls needed the same audit behavior to avoid becoming an optimistic-only process surface.

## Fix

- Added post-toggle `getPmStatus()` evidence for the PM header control.
- Added post-run-once `getPmStatus()` evidence for PM single-iteration execution.
- Added post-resume `getPmStatus()` evidence for the PM resume control.
- Wired the live App header `onResumePm` callback to `startPmLoop(true)`, which calls the existing `/v2/pm/start?resume=true` backend route instead of a no-op.
- Added post-toggle `getDirectorStatus()` evidence for the Director header control.
- Added compact evidence chips that include endpoint, running state, pid, mode, and source.
- Disabled each toggle while its callback/status readback is pending.
- Widened PM/Director page callback prop types to preserve async status-aware callbacks.

## Verification

- `npx eslint src/frontend/src/app/components/ControlPanel.tsx src/frontend/src/app/components/ControlPanel.test.tsx`
- `npm test -- src/app/components/ControlPanel.test.tsx`
- `npx eslint src/frontend/src/app/components/ControlPanel.tsx src/frontend/src/app/components/ControlPanel.test.tsx src/frontend/src/app/pages/PMPage.tsx src/frontend/src/app/pages/DirectorPage.tsx`
- `npx eslint src/frontend/src/app/App.tsx src/frontend/src/hooks/useProcessOperations.test.ts`
- `npm test -- src/hooks/useProcessOperations.test.ts src/app/components/ControlPanel.test.tsx`

These commands passed on 2026-05-23.
