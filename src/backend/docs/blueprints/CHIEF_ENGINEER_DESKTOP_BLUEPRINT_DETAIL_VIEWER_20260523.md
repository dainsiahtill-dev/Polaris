# Chief Engineer Desktop Blueprint Detail Viewer Blueprint (2026-05-23)

## Scope

This blueprint covers the Chief Engineer desktop view for inspecting persisted blueprint payloads.

The workspace already listed Chief Engineer blueprint summaries through `GET /v2/chief-engineer/blueprints`, but it did not use the existing detail route. This increment adds a read-only detail viewer so operators can verify the exact backend-persisted blueprint payload before handing work to Director.

## Current Evidence

- `polaris.delivery.http.v2.chief_engineer` exposes:
  - `GET /v2/chief-engineer/blueprints`
  - `GET /v2/chief-engineer/blueprints/{blueprint_id}`
- `ChiefEngineerWorkspace` already renders blueprint summary evidence and blocks direct Director startup when no blueprint evidence exists.
- Existing tests cover the backend detail route and the desktop summary state, but the frontend did not expose detail payloads.

## Boundary

- Frontend implementation:
  - `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.tsx`
  - `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
- Backend implementation: no new endpoint. This increment reuses the existing Chief Engineer detail route.
- Backend cells involved:
  - `chief_engineer.blueprint`: blueprint persistence owner.
  - `runtime.projection`: typed role-contract projection used by the delivery schema.

## Design

```text
ChiefEngineerWorkspace
  -> blueprint summary card
  -> operator clicks detail
  -> GET /v2/chief-engineer/blueprints/{blueprint_id}
  -> right-side read-only payload viewer
```

The viewer deliberately displays the persisted payload as evidence. It does not edit blueprint records, synthesize missing fields, or start Director automatically.

## UX Rules

- Detail loading must be explicit per blueprint.
- Missing or unavailable detail must show a clear inline error.
- Raw payload is displayed in a constrained scroll region.
- Use Lucide icons and existing compact workspace styling.

## Verification Plan

- Chief Engineer workspace tests prove the detail button calls the backend detail route and renders persisted payload fields.
- Existing frontend RoleSession tests continue to pass because the AI panel remains independent.
- Frontend lint, targeted Vitest, typecheck, and build cover the changed TypeScript surface.
- Existing backend Chief Engineer route tests remain unchanged because no backend contract changed.
