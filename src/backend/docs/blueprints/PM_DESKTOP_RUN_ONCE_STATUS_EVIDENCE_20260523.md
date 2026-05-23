# PM Desktop Run Once Status Evidence - 2026-05-23

## Scope

- Desktop surface: `src/frontend/src/app/components/pm/PMWorkspace.tsx`
- Backend contracts reused:
  - `POST /v2/pm/run_once` through the existing PM workspace callback
  - `GET /v2/pm/status` after the callback completes
- Test coverage: `src/frontend/src/app/components/pm/PMWorkspace.test.tsx`

## Root Cause

The PM workspace single-run button delegated to the existing `run_once` flow, but the workspace did not show the status snapshot that operators and E2E acceptance use to confirm the run entered backend PM state. The visible desktop evidence stopped at the button action.

## Fix

- Kept the existing `onRunPmOnce` behavior and callback ownership.
- Added a PM run-once status evidence state to `PMWorkspace`.
- After the callback resolves, the workspace reads `GET /v2/pm/status` through the typed service.
- Rendered a compact evidence strip with the exact endpoint, running state, pid, mode, and source.
- Added regression coverage that clicks `pm-workspace-run-once`, verifies the callback, verifies `getPmStatus`, and checks the visible evidence.

## Verification

Run from repository root:

```bash
npx eslint src/frontend/src/app/components/pm/PMWorkspace.tsx src/frontend/src/app/components/pm/PMWorkspace.test.tsx
npm test -- src/app/components/pm/PMWorkspace.test.tsx
npm run typecheck
npm run build
git -c i18n.logOutputEncoding=UTF-8 diff --check
```

