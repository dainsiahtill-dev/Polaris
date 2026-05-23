# PM Desktop Toggle Status Evidence - 2026-05-23

## Scope

- Desktop surface: `src/frontend/src/app/components/pm/PMWorkspace.tsx`
- Backend contracts reused:
  - existing PM start/stop callback ownership from the process operation layer
  - `GET /v2/pm/status` after the callback completes
- Test coverage: `src/frontend/src/app/components/pm/PMWorkspace.test.tsx`

## Root Cause

The PM workspace start/stop button delegated to the existing process operation callback, but the desktop did not show the backend status snapshot after the action. Operators could click the control without seeing the canonical `/v2/pm/status` evidence that proves whether the PM process entered running or idle state.

## Fix

- Kept `onTogglePm` ownership in the parent process operation layer.
- Added PM toggle status evidence state to `PMWorkspace`.
- After the callback resolves, the workspace reads `GET /v2/pm/status` through the typed PM service.
- Rendered a compact evidence strip with the exact endpoint, running state, pid, mode, and source.
- Added regression coverage for the toggle path.

## Verification

Run from repository root:

```bash
npx eslint src/frontend/src/app/components/pm/PMWorkspace.tsx src/frontend/src/app/components/pm/PMWorkspace.test.tsx
npm test -- src/app/components/pm/PMWorkspace.test.tsx
npm run typecheck
npm run build
git -c i18n.logOutputEncoding=UTF-8 diff --check
```

