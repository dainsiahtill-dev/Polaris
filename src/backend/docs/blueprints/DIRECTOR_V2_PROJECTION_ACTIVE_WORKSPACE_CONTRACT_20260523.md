# Director V2 Projection Active Workspace Contract

Date: 2026-05-23

## Finding

Director v2 projection-backed desktop routes and the Director service dependency
rebinder selected workspace through local/stale expressions instead of the
shared delivery resolver. That left Director status/task projection paths and
service binding out of the same active workspace contract already used by PM,
Chief Engineer, role sessions, role chat, runtime diagnostics, and LLM/provider
routes.

## Contract

Director desktop projection routes must resolve workspace with this precedence:

1. `settings.workspace_path`
2. `settings.workspace`

Affected backend surfaces:

- `GET /v2/director/status`
- `GET /v2/director/status?source=auto`
- `GET /v2/director/tasks?source=auto|workflow`
- `GET /v2/director/tasks/{task_id}` when falling back to runtime projection
- request-scoped `get_director_service` binding for Director task routes

## Data Flow

Desktop selected workspace -> `settings.workspace_path` -> shared delivery
active workspace resolver -> `RuntimeProjectionService.build_async(...)` ->
Director desktop status/task/detail panels.

Legacy callers that only populate `settings.workspace` continue through the
fallback path.

## Graph Boundary

`polaris/delivery/http/v2/director.py` is owned by `director.execution`.
Projection lookups remain delegated to
`polaris.cells.runtime.projection.public.service.RuntimeProjectionService`.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `src/backend/polaris/tests/test_http_dependencies_workspace.py`
- `src/frontend/src/app/components/director/__tests__/DirectorTaskPanel.test.tsx`
- `src/frontend/src/app/components/director/__tests__/DirectorWorkbenchPanel.test.tsx`
- `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`
