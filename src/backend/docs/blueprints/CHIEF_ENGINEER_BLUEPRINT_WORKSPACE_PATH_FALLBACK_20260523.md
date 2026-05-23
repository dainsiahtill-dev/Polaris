# Chief Engineer Blueprint Workspace Path Fallback

Date: 2026-05-23

## Finding

Chief Engineer diagnostics resolved the active workspace from either `settings.workspace` or `settings.workspace_path`, but the blueprint list/detail persistence helper read only `settings.workspace`.

## Root Cause

The delivery route duplicated workspace resolution instead of reusing the local `_workspace_value()` helper.

## Fix

- Updated `_persistence_for_request()` to use `_workspace_value()`.
- Added an explicit `WORKSPACE_NOT_CONFIGURED` error if no workspace can be resolved.
- Added a router regression test proving `workspace_path` is honored by the blueprint list endpoint.

## 2026-05-23 Follow-up: Active Workspace Precedence

Further desktop contract audit found that `_workspace_value()` still preferred
`settings.workspace` before `settings.workspace_path`. In Electron sessions this
can point Chief Engineer diagnostics/list/generate/status/detail at the repo
default workspace even when the active target workspace has been switched and
stored on `workspace_path`.

The route helper now resolves workspace values in this order:

1. `settings.workspace_path`
2. `settings.workspace`

The follow-up regression covers the both-present case so a stale default
workspace cannot mask the active desktop workspace.

## Verification

Targeted Python gates:

- `ruff check src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/delivery/http/v2/chief_engineer.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py -v`
