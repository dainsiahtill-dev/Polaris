# PM Director Handoff Preflight Desktop 2026-05-24

## Problem

PM desktop orchestration can set `run_director=true`, which asks the PM workflow
to continue into Director execution. The PM route already validates PM startup
readiness, but it did not force Director runtime LLM readiness before creating
the PM workflow run. The desktop checkbox also had no Director readiness
evidence, so users could see PM as ready while the handoff would later fail in
Director.

## Scope

- Guard `/v2/pm/run` with Director LLM readiness when `run_director=true`.
- Keep PM-only runs governed by PM startup diagnostics only.
- Surface Director LLM readiness beside the PM workbench Director checkbox.
- Disable PM orchestration launch only when Director auto-dispatch is selected
  and Director LLM readiness is blocked or unavailable.

## Verification

- `ruff check src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/pm.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `mypy src/backend/polaris/delivery/http/v2/pm.py`
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -q`
- `npm run test -- src/frontend/src/app/components/pm/PMWorkbenchPanel.test.tsx src/frontend/src/services/__tests__/pmService.test.ts`
- `npm run typecheck`
- `npm run lint`
