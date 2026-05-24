# Director Run Preflight Gate Desktop Blueprint

Date: 2026-05-24

## Problem

The Director desktop workspace already reads `/v2/director/diagnostics` and disables execution when task handoff or worker readiness is blocked. The backend `POST /v2/director/run` endpoint still called the orchestration command service directly, so API callers could bypass the same readiness evidence.

## Scope

- Cell boundary: `director.execution`
- Entry point: `polaris/delivery/http/v2/director.py`
- Reused evidence: existing Director diagnostics sections and execution blockers
- No new state owner and no new runtime write path

## Data Flow

```text
Director desktop/API caller
  -> POST /v2/director/run
  -> build Director diagnostics snapshot
  -> if can_execute=false or execution_blockers non-empty: 409 DIRECTOR_EXECUTION_BLOCKED
  -> else OrchestrationCommandService.execute_director_run(...)
```

## Design

1. Keep `/v2/director/diagnostics` side-effect-free.
2. Extract diagnostics assembly into a shared helper used by both diagnostics and run.
3. Gate `/v2/director/run` before the orchestration write path.
4. Return structured evidence with blockers, issues, task counts, worker counts, and projection status.

## Verification Plan

- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -q`
- `ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `mypy src/backend/polaris/delivery/http/v2/director.py`
