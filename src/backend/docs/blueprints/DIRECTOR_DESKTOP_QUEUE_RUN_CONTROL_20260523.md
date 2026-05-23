# Director Desktop Queue Run Control - 2026-05-23

## Scope

- Desktop surface: `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- Backend contract reused: `POST /v2/director/run`
- Evidence route reused: `GET /v2/director/runs/{run_id}`
- Test coverage: `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`

## Root Cause

The Director workspace execute button used two different start paths:

- selected task: create a v2 Director orchestration run and read run evidence
- no selected task: fall back to the legacy Director toggle callback

That meant the common "run the current Director queue" workflow bypassed the canonical orchestration command route and produced no `/v2/director/runs/{run_id}` evidence in the desktop.

## Fix

- Kept the existing stop behavior when Director is already running.
- Changed the start behavior so the desktop always calls `runDirector`.
- When no task is selected, the payload only includes `workspace` and `execution_mode`, matching the backend queue-level run contract.
- When a task is selected, the payload still includes `task_id` and `task_filter`.
- Added regression coverage that asserts queue-level execute does not call the legacy toggle and does show run evidence.

## Verification

Run from repository root:

```bash
npx eslint src/frontend/src/app/components/director/DirectorWorkspace.tsx src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx
npm test -- src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx
npm run typecheck
npm run build
git -c i18n.logOutputEncoding=UTF-8 diff --check
```

