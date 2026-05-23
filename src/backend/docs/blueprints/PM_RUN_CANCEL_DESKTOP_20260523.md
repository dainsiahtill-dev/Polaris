# PM Run Cancel Desktop Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The PM workbench can start a unified PM orchestration run and display
`GET /v2/pm/runs/{run_id}` evidence, but it has no run-scoped cancel action.
The orchestration runtime already exposes `cancel_run`, and Director now has a
desktop/backend cancel path for its run evidence. PM needs the same control so
users can stop a specific PM orchestration run without falling back to broader
process toggles.

## Scope

This change is limited to PM orchestration run cancellation:

- Add `POST /v2/pm/runs/{run_id}/cancel` in the PM v2 router.
- Return terminal PM run snapshots unchanged to keep cancellation idempotent.
- Add a typed `cancelPmRun` frontend service wrapper and barrel export.
- Add a compact cancel action to the PM workbench run evidence strip.
- Show endpoint evidence and resulting PM run status after cancellation.

No PM planning contract, PM dispatch implementation, workflow runtime internals,
or target project code is changed.

## Architecture Sketch

```text
PMWorkbenchPanel
  -> runPm(payload)
      -> POST /v2/pm/run
  -> getPmRun(run_id)
      -> GET /v2/pm/runs/{run_id}
  -> cancelPmRun(run_id)
      -> POST /v2/pm/runs/{run_id}/cancel
      -> get_orchestration_service().cancel_run(run_id)
      -> PMOrchestrationResponse
```

The workflow runtime remains the source of truth for run state. The PM desktop
only exposes the existing runtime capability through the evidence strip that
already represents a specific PM run.

## Assumption Register

- `get_orchestration_service().query_run(run_id)` returns snapshots with the
  same fields used by the current PM run-detail endpoint.
- PM run stage can be derived from the same task-role/current-phase logic used
  by `GET /v2/pm/runs/{run_id}`.
- Terminal statuses should be treated as already settled and returned without
  calling `cancel_run`.
- The PM workbench run evidence strip is the correct host for run-scoped
  controls.

## Pre-Mortem

- Risk: A completed PM run could fail cancellation because the runtime lock was
  already removed.
  Mitigation: Return terminal snapshots unchanged.
- Risk: The cancel response could drift from the existing PM run detail response.
  Mitigation: Share stage derivation and response assembly with the get route.
- Risk: Encoded run IDs could break cancellation links.
  Mitigation: Reuse `encodeURIComponent` in the typed service wrapper.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/v2/pm.py`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -q`
- `npm run test -- PMWorkbenchPanel pmService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
