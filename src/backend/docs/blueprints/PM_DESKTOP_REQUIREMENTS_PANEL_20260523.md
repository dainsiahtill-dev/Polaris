# PM Desktop Requirements Panel

Date: 2026-05-23

## Finding

The PM management backend exposes requirement list and detail routes through
`/v2/pm/requirements` and `/v2/pm/requirements/{req_id}`. The PM desktop
workspace, however, only surfaces tasks, activity, documents, history, and task
analytics. Requirement traceability is therefore available in the backend but
not reachable from the PM role desktop.

## Contract

PM desktop must expose requirements as read-only backend evidence:

- List source: `GET /v2/pm/requirements?limit=100&offset=0`.
- Detail source: `GET /v2/pm/requirements/{req_id}`.
- Failure mode: show backend errors as evidence without hiding other PM
  workspace surfaces.
- Scope: no requirement mutation and no target-project code generation.

## Verification

- `src/frontend/src/services/pmService.ts`
- `src/frontend/src/app/components/pm/PMWorkspace.tsx`
- `src/frontend/src/services/__tests__/pmService.test.ts`
- `src/frontend/src/app/components/pm/PMWorkspace.test.tsx`
- Existing PM management v2 backend tests for requirement list/detail routes.
