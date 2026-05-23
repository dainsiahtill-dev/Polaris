# Chief Engineer Desktop Blueprint Service Contract Blueprint

Date: 2026-05-23
Status: Implemented
Scope: Chief Engineer desktop frontend service boundary, reusing existing Chief Engineer v2 HTTP routes.

## Current Fact

The Chief Engineer desktop workspace already uses the backend blueprint routes:

- `GET /v2/chief-engineer/blueprints`
- `GET /v2/chief-engineer/blueprints/{blueprint_id}`

The component called those routes directly through `apiFetch`, which made the desktop/backend contract less explicit than the PM and Director services.

## Target Data Flow

```text
ChiefEngineerWorkspace
  -> listChiefEngineerBlueprints()
  -> GET /v2/chief-engineer/blueprints
  -> render blueprint summaries

User opens detail
  -> getChiefEngineerBlueprint(blueprintId)
  -> GET /v2/chief-engineer/blueprints/{blueprint_id}
  -> render persisted blueprint payload
```

## Module Responsibilities

- `src/frontend/src/services/chiefEngineerService.ts`
  - Owns typed wrappers for Chief Engineer blueprint routes.
  - Uses shared `apiGet` error handling and keeps URL encoding centralized.

- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
  - Consumes the service layer rather than hand-rolling route calls.
  - Keeps current no-fake-blueprints behavior unchanged.

## Verification Plan

- Frontend service tests verify exact route paths and blueprint id encoding.
- Chief Engineer workspace tests verify the UI still loads summaries and details.
- Backend Chief Engineer router tests verify the v2 route contract remains intact.
