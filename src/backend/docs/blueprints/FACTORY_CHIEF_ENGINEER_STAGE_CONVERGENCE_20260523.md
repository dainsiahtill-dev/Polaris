# Factory Chief Engineer Stage Convergence

Date: 2026-05-23
Classification: pattern
Owner: Codex

## Problem

Factory desktop now exposes PM, Chief Engineer, and Director as distinct role
layers, but the backend factory run still executes only:

```text
docs_generation -> pm_planning -> director_dispatch -> quality_gate
```

That mismatch makes the UI show a Chief Engineer layer without a durable
factory stage behind it. It also makes PM look like the last blocking role when
PM planning succeeds but the next governed handoff step is absent.

## Assumptions

- `execution_governance_pipeline` declares the execution chain as PM planning
  and dispatch, Chief Engineer blueprint, Director execution, then QA verdict.
- `chief_engineer.blueprint` already owns `runtime/blueprints/*` and exposes
  `GenerateTaskBlueprintCommandV1`.
- Factory stage execution can call the Chief Engineer public contract without
  writing code or bypassing Director.
- Director can keep using the PM plan as its task filter while the factory run
  publishes blueprint evidence as a separate handoff artifact.

## Architecture

```text
Factory start
  -> docs_generation       role=architect
  -> pm_planning           role=pm
  -> chief_engineer_review role=chief_engineer
       reads tasks/plan.json
       emits runtime/blueprints/*.json via chief_engineer.blueprint contract
       emits runtime/state/blueprints/<factory_run>.review.json
  -> director_dispatch     role=director
  -> quality_gate          role=qa
```

## Scope

- Add a `chief_engineer_review` stage to factory.pipeline.
- Map that stage to `chief_engineer` in Factory HTTP status roles.
- Include Chief Engineer in Factory role status snapshots.
- Add the stage to PM and Architect factory starts before Director.
- Keep `start_from=director` as Director-to-QA only, because it is an explicit
  resume/implementation entry.
- Add regression tests for stage construction, role mapping, and blueprint
  artifact generation.

## Non-Goals

- No new target-project code.
- No direct Director-to-Chief-Engineer calls.
- No claim that the EDA task-market durable consumer is fully converged.
- No Electron full-chain PASS claim without rerunning the relevant E2E.

## Verification Plan

- `python -m ruff check` on changed backend files and tests.
- `python -m ruff format` on changed backend files and tests.
- `python -m mypy` on changed backend files and tests.
- `python -m pytest src/backend/polaris/tests/test_factory_run_service.py src/backend/polaris/tests/test_factory_router.py -q`
- `npm run test -- FactoryWorkspace`
- `npm run typecheck`
- `npm run lint`
