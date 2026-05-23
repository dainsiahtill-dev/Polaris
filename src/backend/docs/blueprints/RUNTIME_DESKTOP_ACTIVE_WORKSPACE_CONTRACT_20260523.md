# Runtime Desktop Active Workspace Contract

Date: 2026-05-23

## Finding

Runtime support routes used by desktop preflight, diagnostics, and reset flows
resolved `settings.workspace` before `settings.workspace_path`.

In Electron sessions, `settings.workspace_path` is the selected target project
workspace, while `settings.workspace` can still point at the Polaris repo or a
legacy value. PM and Director desktop flows depend on these runtime support
routes for storage layout inspection, runtime clearing, migration status, task
reset, and diagnostics.

## Contract

Runtime delivery routes must resolve workspace with this precedence:

1. `settings.workspace_path`
2. `settings.workspace`

Affected backend surfaces:

- `GET /runtime/storage-layout`
- `GET /v2/runtime/storage/layout`
- `POST /runtime/clear`
- `POST /v2/runtime/clear`
- `GET /runtime/migration-status`
- `GET /v2/runtime/migration/status`
- `POST /runtime/reset/tasks`
- `POST /v2/runtime/reset/tasks`
- `GET /v2/runtime/diagnostics`

## Data Flow

Desktop selected workspace -> `settings.workspace_path` -> runtime route
workspace resolver -> KernelOne storage/cache/runtime helpers.

Legacy callers that only populate `settings.workspace` still work through the
fallback path.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_runtime_v2.py`
- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_runtime_diagnostics_router.py`
- `src/frontend/src/app/components/RuntimeDiagnosticsWorkspace.test.tsx`
- `src/frontend/src/services/__tests__/runtimeService.test.ts`
