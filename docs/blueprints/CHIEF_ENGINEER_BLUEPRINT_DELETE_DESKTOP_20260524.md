# Chief Engineer Blueprint Delete Desktop Contract

Date: 2026-05-24

## Objective

Complete the Chief Engineer desktop blueprint lifecycle by adding an auditable cleanup action for persisted blueprint records. The current desktop surface can list, generate, inspect, and query blueprint status, but stale or failed records cannot be removed from the role workspace.

## Scope

- Backend Cell: `chief_engineer.blueprint`
- Delivery route: `polaris.delivery.http.v2.chief_engineer`
- Frontend surface: `ChiefEngineerWorkspace`
- State owner: `runtime/blueprints/*`

## Architecture

```text
Chief Engineer Desktop
  -> DELETE /v2/chief-engineer/blueprints/{blueprint_id}
    -> validate blueprint_id
    -> BlueprintPersistence.delete(blueprint_id)
    -> return deletion evidence
  -> refresh list + diagnostics
```

## Design Rules

- Reuse the existing `BlueprintPersistence` public boundary exported by `chief_engineer.blueprint`.
- Do not add a second blueprint store or alternate handoff truth.
- Treat deletion as an explicit `fs.delete:runtime/blueprints/*` effect in graph metadata.
- Keep reads side-effect-free; missing blueprint deletion must not create the blueprint directory.
- Surface the endpoint path in the desktop UI so the operator can audit the action.

## Verification

- Backend route tests cover success, invalid id, missing id, and no directory creation.
- Frontend service tests cover encoded DELETE.
- Chief Engineer workspace tests cover UI deletion, list update, detail reset, and diagnostics refresh.
