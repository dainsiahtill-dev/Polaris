# Chief Engineer Desktop Blueprint Command Status Contract

Date: 2026-05-23

## Problem

The `chief_engineer.blueprint` cell already declares `GenerateTaskBlueprintCommandV1` and `GetBlueprintStatusQueryV1`, but the desktop v2 API only exposes persisted blueprint list/detail reads. The Chief Engineer workspace can inspect existing blueprints but cannot create a task blueprint or ask for task-level blueprint status through the declared public contract.

## Contract

Add a contract-backed desktop bridge:

- `POST /v2/chief-engineer/blueprints` accepts a task id, objective, optional run id, constraints, and context.
- `GET /v2/chief-engineer/blueprints/status?task_id=...&run_id=...` returns the latest persisted blueprint result for that task.
- The backend calls only `polaris.cells.chief_engineer.blueprint.public.service` command/query functions.
- The frontend consumes the routes through `chiefEngineerService.ts`, not raw component fetches.

## Data Flow

```text
ChiefEngineerWorkspace task action
  -> generateChiefEngineerBlueprint()
  -> POST /v2/chief-engineer/blueprints
  -> GenerateTaskBlueprintCommandV1
  -> chief_engineer.blueprint.public.generate_task_blueprint()
  -> BlueprintPersistence.save()
  -> runtime/blueprints/{blueprint_id}.json

ChiefEngineerWorkspace status check
  -> getChiefEngineerBlueprintStatus()
  -> GET /v2/chief-engineer/blueprints/status
  -> GetBlueprintStatusQueryV1
  -> chief_engineer.blueprint.public.get_blueprint_status()
```

## Boundaries

This fills the existing Chief Engineer cell contract. It does not add target-project code, does not bypass Director execution, and does not invoke LLMs from the desktop route. The generated payload is an auditable task-level blueprint scaffold derived from the PM task contract and CE command fields.

## Verification

- `src/backend/polaris/cells/chief_engineer/blueprint/public/tests/test_public_contracts.py`
- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `src/frontend/src/services/__tests__/chiefEngineerService.test.ts`
- `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx`
