# Factory Chief Engineer Evidence Projection

Date: 2026-05-23
Classification: pattern
Owner: Codex

## Problem

The Factory backend can now execute `chief_engineer_review` and persist Chief
Engineer blueprints under `runtime/blueprints/*`, but the Factory desktop still
derives the Chief Engineer layer mostly from PM task fields such as
`blueprint_id` and `blueprint_path`.

That leaves a gap: a valid Factory run may generate CE blueprint artifacts while
the desktop CE layer still shows "no blueprint evidence".

## Architecture

```text
Factory stage_completed event
  -> result.artifacts[]
  -> /v2/factory/runs/{run_id}/artifacts
  -> FactoryWorkspace deliveryArtifacts
  -> Chief Engineer layer evidence list
```

## Scope

- Include existing stage result artifacts in Factory artifact responses when
  those artifact paths resolve to real files.
- Preserve run-local artifact directory listing behavior.
- Teach the Factory Chief Engineer desktop layer to render blueprint artifacts
  from `runtime/blueprints/*` and `runtime/state/blueprints/*`.
- Keep PM task `blueprint_*` fields as an additional evidence source.

## Non-Goals

- No task contract mutation from the artifact endpoint.
- No new file download endpoint.
- No claim that artifact content preview is implemented.

## Verification Plan

- `python -m pytest src/backend/polaris/tests/test_factory_router.py -q`
- `python -m pytest src/backend/polaris/tests/test_factory_run_service.py -q`
- `npm run test -- FactoryWorkspace useFactory`
- `npm run typecheck`
- `npm run lint`
