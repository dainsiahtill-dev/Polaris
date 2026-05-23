# PM Management Desktop V2 Route Mount

Date: 2026-05-23

## Finding

The PM desktop document, task, and requirement panels call true v2 routes such as:

- `GET /v2/pm/documents`
- `GET /v2/pm/search/tasks`
- `GET /v2/pm/tasks/history`
- `GET /v2/pm/requirements`

The PM management router defined these aliases on a router with prefix `/pm`, so the app registered them as `/pm/v2/pm/...` instead of `/v2/pm/...`.

## Root Cause

The router mixed legacy `/pm/*` routes and v2 absolute-looking aliases in the same prefixed `APIRouter`. FastAPI still applies the router prefix to every decorator path.

## Fix Plan

- Keep the legacy prefixed router unchanged.
- Add an unprefixed PM management v2 router that reuses the same handler functions for document, search, task, and requirement endpoints.
- Mount the unprefixed v2 router in `app_factory`.
- Add a route-registration regression test proving the desktop `/v2/pm/...` endpoints are present.

## Verification Plan

- `ruff check src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/delivery/http/app_factory.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py --fix`
- `ruff format src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/delivery/http/app_factory.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/delivery/http/app_factory.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py -v`
