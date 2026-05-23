# Role Workspace Route Contracts

Date: 2026-05-23

## Finding

The desktop role workspace flow surfaced two backend route-contract failures:

- `GET /v2/role/chief_engineer/llm-events?limit=5` returned 500.
- `GET /v2/director/capabilities` returned 404 and produced an actionable renderer console error.

## Root Cause

- `role_chat.py` imported LLM events and cache services through a stale delivery-local path instead of the existing `roles.kernel` public cell boundary.
- Director capabilities were implemented under the Arsenal router, but that router is mounted at `/arsenal`, so the canonical v2 Director URL was not reachable.

## Fix

- Updated role chat LLM event and cache endpoints to use `polaris.cells.roles.kernel.public.service`.
- Replaced skipped import-path tests with live role LLM event and cache endpoint coverage, including `chief_engineer`.
- Added `/v2/director/capabilities` to the Director v2 router using the existing domain capability matrix.
- Added a Director router regression test for the exact desktop capability endpoint.

## 2026-05-23 Follow-up: Role Chat Active Workspace

PM and Chief Engineer desktop dialogue panels use the generic role chat routes:

- `GET /v2/role/{role}/chat/status`
- `POST /v2/role/{role}/chat`
- `POST /v2/role/{role}/chat/stream`

Those routes must resolve the same active workspace as the rest of the desktop
role workspaces. In Electron sessions, `settings.workspace_path` is the selected
target workspace and `settings.workspace` may be stale. The role chat router now
uses this precedence for LLM config lookup and generation calls:

1. `settings.workspace_path`
2. `settings.workspace`

This keeps legacy fallback while preventing PM/Chief Engineer dialogue from
loading configuration or generating role context against the wrong workspace.

## 2026-05-23 Follow-up: Role Readiness Active Workspace

The shared role readiness gate used by RoleChat must resolve the same active
workspace as the chat route itself. `_ensure_llm_ready` and
`required_ready_roles` now use this precedence for cache roots and LLM config
loading:

1. `settings.workspace_path`
2. `settings.workspace`

This prevents PM, Chief Engineer, and Director desktop dialogue from passing
message generation against one workspace while validating LLM readiness against
another.

## Verification

Targeted gates:

- `ruff check src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/tests/integration/delivery/routers/test_role_chat_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/tests/integration/delivery/routers/test_role_chat_router.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/tests/integration/delivery/routers/test_role_chat_router.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/integration/delivery/routers/test_role_chat_router.py -v`
- `ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -v`
- `npm run test:e2e -- src/backend/polaris/tests/electron/role-workspaces-visual.spec.ts`
