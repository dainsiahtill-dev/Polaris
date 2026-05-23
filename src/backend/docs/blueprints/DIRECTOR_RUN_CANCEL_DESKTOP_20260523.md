# Director Run Cancel Desktop Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

The Director desktop can start a unified orchestration run and display
`GET /v2/director/runs/{run_id}` evidence, while the orchestration runtime
already exposes `cancel_run`. There is no delivery route or desktop control for
canceling the active run from that evidence surface, so users must fall back to
older process-level stop controls that do not target a specific orchestration
run.

## Scope

This change is limited to Director orchestration run cancellation:

- Add `POST /v2/director/runs/{run_id}/cancel` in the Director v2 router.
- Keep cancellation idempotent for terminal run snapshots.
- Add a typed `cancelDirectorRun` frontend service wrapper.
- Add a compact cancel action to the Director run evidence strip.
- Show endpoint evidence and the resulting run status after cancellation.

No Director task contract, PM dispatch behavior, workflow runtime internals, or
target project code is changed.

## Architecture Sketch

```text
DirectorWorkspace
  -> runDirector(payload)
      -> POST /v2/director/run
  -> getDirectorRun(run_id)
      -> GET /v2/director/runs/{run_id}
  -> cancelDirectorRun(run_id)
      -> POST /v2/director/runs/{run_id}/cancel
      -> get_orchestration_service().cancel_run(run_id)
      -> DirectorOrchestrationResponse
```

The runtime remains the source of truth for run state. The desktop only exposes
the existing runtime capability through the same evidence strip that already
represents the run.

## Assumption Register

- `get_orchestration_service().query_run(run_id)` returns snapshots with the
  same fields used by the existing run-detail endpoint.
- Terminal statuses should be treated as already settled and returned without
  calling `cancel_run`.
- Active non-terminal statuses are cancellable through the existing runtime
  service.
- The Director run evidence strip is the correct host for run-scoped controls.

## Pre-Mortem

- Risk: A completed run could fail cancellation because the runtime lock was
  already removed.
  Mitigation: Return terminal snapshots unchanged.
- Risk: A UI user could confuse task cancellation and run cancellation.
  Mitigation: Keep the control in the run evidence strip and show the
  `/v2/director/runs/{run_id}/cancel` endpoint.
- Risk: Encoded run IDs could break run cancellation for slash or space
  characters.
  Mitigation: Reuse `encodeURIComponent` in the typed service wrapper.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/v2/director.py`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -q`
- `npm run test -- DirectorWorkspace pmService`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel PMDocumentPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
