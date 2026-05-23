# Role Chat Kernel Events Cache Route Tests

Date: 2026-05-23

## Finding

The generic role chat router already exposes working shared role-kernel
diagnostics:

- `GET /v2/role/{role}/llm-events`
- `GET /v2/role/llm-events`
- `GET /v2/role/cache-stats`

However, `test_v2_role_router.py` still documented these routes as production
bugs with `xfail` markers for missing imports that no longer exist. The same
test file also asserted older verbose status errors after the route moved to a
sanitized top-level error contract with structured details. The stale baseline
causes passing production endpoints to appear as failed tests.

## Contract

The router tests must assert the current contract:

- role-scoped LLM events call the shared emitter with role, run, task, and limit
  filters;
- all-role LLM events call the shared emitter with optional filters and return a
  count;
- cache stats call the shared role-kernel cache and return its stats payload.
- role chat status tests assert sanitized top-level error fields and keep
  detailed evidence under `debug` or `details`.

## Boundary

- Target cell: `llm.dialogue`
- Shared capability reused: `roles.kernel`
- Backend scope: route tests for `polaris.delivery.http.routers.role_chat`
- No production code changes are required.
- No target-project code is generated or modified.

## Verification

- Focused role-router LLM/cache route tests.
- Full role-router test file.
- Python formatting, linting, and type checks for the changed test module.
