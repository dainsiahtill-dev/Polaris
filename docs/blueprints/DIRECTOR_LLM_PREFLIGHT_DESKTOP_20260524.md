# Director LLM Preflight Desktop 2026-05-24

## Problem

PM and Chief Engineer desktop diagnostics expose role-specific LLM readiness,
but Director diagnostics only reported status, task queue, and worker pool
evidence. The `/v2/director/run` path used those diagnostics as its execution
gate, so a Director run could be created without a visible Director LLM
readiness verdict.

## Scope

- Add Director role-specific LLM readiness to `/v2/director/diagnostics`.
- Force the `director` role into the readiness evidence even when workspace
  policy omits it.
- Include `director_llm_not_ready` in diagnostics issues and execution blockers.
- Surface the LLM status row in the Director desktop readiness strip and disable
  execution when it is blocked.

## Verification

- `ruff check src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/director.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py`
- `mypy src/backend/polaris/delivery/http/v2/director.py`
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -q`
- `npm run test -- src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx src/frontend/src/services/__tests__/pmService.test.ts`
- `npm run typecheck`
- `npm run lint`
