# Chief Engineer PM Task Plan Handoff Gate - 2026-05-25

## Problem

Chief Engineer diagnostics could treat any loadable persisted blueprint as Director handoff-ready when the PM task plan could not be read. That made stale or orphaned blueprints look authoritative even though there was no auditable PM task list to prove coverage.

## Change

- `/v2/chief-engineer/diagnostics` now reports PM task plan evidence in the `blueprints` section:
  - `plan_status`
  - `plan_path`
  - `plan_error`
- Director handoff is blocked when `runtime/tasks/plan.json` is missing, unresolved, unreadable, invalid, or empty.
- A loadable blueprint is now handoff-ready only when an auditable PM task plan exists and every planned task has blueprint coverage.
- The Chief Engineer desktop maps the new blockers to actionable UI copy instead of allowing the Director start button to proceed.

## New Blocker Tokens

- `blueprint_task_plan_unavailable`
- `blueprint_task_plan_empty`

## Verification

- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py -q`
- `npm run test -- ChiefEngineerWorkspace chiefEngineerService`
- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `npm run typecheck`
- `npm run lint`
