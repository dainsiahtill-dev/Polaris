# PM And Director Run Active Workspace Contract

Date: 2026-05-23

## Finding

The desktop PM and Director workbenches generally pass the selected workspace
explicitly, but the backend orchestration request models still default
`workspace` to `"."`. If a desktop caller omits that field or forwards the
schema default, `/v2/pm/run` and `/v2/director/run` can start orchestration
against the Polaris repository process working directory instead of the active
Electron target workspace.

## Contract

PM and Director orchestration run routes must resolve workspace with this
precedence:

1. Explicit non-dot request `workspace`
2. `settings.workspace_path`
3. `settings.workspace`
4. Request default `"."` as the final legacy fallback

Affected backend surfaces:

- `POST /v2/pm/run`
- `POST /v2/director/run`

Explicit non-dot workspace values remain supported for API callers that need to
target a workspace other than the active desktop selection.

## Data Flow

Desktop selected workspace -> `settings.workspace_path` -> shared delivery run
workspace resolver -> `OrchestrationCommandService.execute_pm_run(...)` or
`execute_director_run(...)` -> orchestration state and desktop run response.

Legacy callers that only populate `settings.workspace` continue through the
fallback path. Legacy callers that intentionally send a concrete workspace path
keep that value.

## Graph Boundary

`polaris/delivery/http/v2/pm.py` is owned by `orchestration.pm_planning`.
`polaris/delivery/http/v2/director.py` is owned by `director.execution`.
Both routes keep execution delegated through the existing
`orchestration.pm_dispatch` public service boundary.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `src/frontend/src/app/components/pm/PMWorkbenchPanel.test.tsx`
- `src/frontend/src/app/components/director/__tests__/DirectorWorkbenchPanel.test.tsx`
- `src/backend/polaris/tests/electron/role-workspaces-visual.spec.ts`
