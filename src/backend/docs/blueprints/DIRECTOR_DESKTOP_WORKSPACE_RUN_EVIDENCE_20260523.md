# Director Desktop Workspace Run Evidence - 2026-05-23

## Scope

- Desktop surface: `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- Backend contract surfaced: `POST /v2/director/run` followed by `GET /v2/director/runs/{run_id}`
- Test coverage: `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`

## Root Cause

The Director workspace execute button already created backend orchestration runs through the shared `runDirector` service, but the desktop only wrote the queued run id into the terminal. Operators could not see whether the corresponding backend run snapshot was readable from the canonical run evidence route.

## Fix

- Added `DirectorRunEvidenceState` to the Director workspace.
- Added a `getDirectorRun(run_id)` follow-up after successful `runDirector` creation.
- Rendered a compact evidence strip with the exact `/v2/director/runs/{run_id}` route, run status, queued task count, and workspace.
- Extended the Director capability integration test to assert the service call and visible run evidence.

## Verification

Run from repository root:

```bash
npx eslint src/frontend/src/app/components/director/DirectorWorkspace.tsx src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx
npm test -- src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx
npm run typecheck
npm run build
git -c i18n.logOutputEncoding=UTF-8 diff --check
```

