# PM Kernel LLM Events Global Query

Date: 2026-05-23

## Finding

`PMDiagnosticsPanel` reads PM Kernel telemetry with `GET /v2/pm/llm-events?limit=5`, but the backend route required `run_id`. Opening the PM diagnostics modal could therefore turn a valid "latest PM LLM events" query into a 422 response.

## Root Cause

PM and Director exposed similar Kernel telemetry surfaces with different query contracts. Director already allowed a global latest-events query, while PM required `run_id` even though the desktop diagnostics view does not have one.

## Fix Plan

- Make `run_id` optional on `GET /v2/pm/llm-events`.
- Preserve existing filtering when `run_id` and `task_id` are supplied.
- Return lightweight stats compatible with the Director and role LLM event surfaces.
- Add a router regression test for `/v2/pm/llm-events?limit=5`.

## Verification Plan

- `ruff check src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -v`
- Focused frontend test covering PM diagnostics if available.
