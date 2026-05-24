# PM Start Preflight Gate Desktop 2026-05-24

## Problem

The desktop PM workspace already consumes `/v2/pm/diagnostics` and disables start
controls when LanceDB, LLM runtime, or workspace docs are not ready. The backend
execution endpoints still trust the caller and can start PM through
`/v2/pm/run_once`, `/v2/pm/start`, `/v2/pm/start_loop`, or `/v2/pm/run` even when
the same diagnostics report hard blockers.

## Root Cause

PM startup readiness was implemented as a display-only diagnostic endpoint. The
actual execution endpoints did not reuse the diagnostic result as an enforcement
gate.

## Scope

- Add a shared PM diagnostics builder for read-only diagnostics and guarded
  execution.
- Fail closed with a structured 409 when `can_start=false` or
  `startup_blockers` is non-empty.
- Cover single-run, loop-start, deprecated loop-start, and unified PM run entry
  points.

## Verification

- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -q`
- `ruff check src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `mypy src/backend/polaris/delivery/http/v2/pm.py`
