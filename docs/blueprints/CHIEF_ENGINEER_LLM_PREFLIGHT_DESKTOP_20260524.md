# Chief Engineer LLM Preflight Desktop 2026-05-24

## Problem

PM and Director desktop actions already fail closed when their runtime LLM
roles are not ready. Chief Engineer diagnostics and blueprint generation still
focus on workspace and blueprint-store evidence only, so the desktop can appear
ready to produce Director handoff artifacts even when the `chief_engineer` LLM
role is stale, untested, or unsupported.

## Scope

- Add Chief Engineer role-specific LLM readiness to `/v2/chief-engineer/diagnostics`.
- Expose generation blockers separately from Director handoff blockers.
- Guard `/v2/chief-engineer/blueprints` with the existing runtime-role readiness
  helper, forcing the `chief_engineer` role to be checked.
- Surface the LLM row and generation blocker in the Chief Engineer desktop
  workspace.

## Verification

- `ruff check src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py --fix`
- `ruff format src/backend/polaris/delivery/http/v2/chief_engineer.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
- `mypy src/backend/polaris/delivery/http/v2/chief_engineer.py`
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py -q`
- `npm run test -- src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkspace.test.tsx src/frontend/src/services/__tests__/chiefEngineerService.test.ts`
- `npm run typecheck`
