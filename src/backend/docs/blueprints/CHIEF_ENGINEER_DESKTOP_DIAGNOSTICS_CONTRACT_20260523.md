# Chief Engineer Desktop Diagnostics Contract

Date: 2026-05-23

## Problem

The Chief Engineer desktop workspace can list persisted blueprints, but it does not have a single backend readiness contract for workspace state and blueprint handoff health. The UI has to infer readiness from separate projections, which makes Director handoff troubleshooting less explicit.

## Contract

Add `GET /v2/chief-engineer/diagnostics` as a side-effect-free Chief Engineer readiness snapshot:

- `workspace`: active workspace path and existence check.
- `blueprints`: persisted blueprint store health, total records, loadable records, invalid payload count, and latest update token.
- `issues`: deterministic issue tokens for desktop rendering and tests.

The route must inspect blueprint state without creating directories or writing files. `BlueprintPersistence` therefore gets a backward-compatible `ensure_directory` constructor option, defaulting to the existing write-ready behavior.

## Data Flow

```text
ChiefEngineerWorkspace
  -> chiefEngineerService.getChiefEngineerDiagnostics()
  -> GET /v2/chief-engineer/diagnostics
  -> chief_engineer.blueprint.public.BlueprintPersistence(..., ensure_directory=false)
  -> runtime/blueprints read-only listing and payload checks
```

## Boundaries

This is a delivery-layer aggregation endpoint backed by the existing `chief_engineer.blueprint` public persistence boundary. It does not start Chief Engineer, start Director, mutate workspace files, or create runtime directories.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `src/backend/polaris/cells/chief_engineer/blueprint/tests/test_blueprint_persistence.py`
- `src/frontend/src/services/__tests__/chiefEngineerService.test.ts`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
