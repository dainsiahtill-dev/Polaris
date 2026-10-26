# FinOps Budget Guard

## Purpose

Enforce token and execution budget controls with explicit reserve and consume accounting and threshold signals.

## Kind

`policy`

## Public Contracts

- commands: ReserveBudgetCommandV1, RecordUsageCommandV1
- queries: GetBudgetStatusQueryV1
- events: BudgetThresholdExceededEventV1
- results: BudgetDecisionResultV1
- errors: FinOpsBudgetError

## Public Service

- `reserve_budget(command: ReserveBudgetCommandV1)`

## Depends On

- `policy.permission`
- `audit.evidence`

## State Ownership

- `runtime/state/budget/*`

## Effects Allowed

- `fs.read:runtime/**`
- `fs.write:runtime/state/budget/*`
- `fs.write:runtime/events/runtime.events.jsonl`

## Verification

- `tests/test_llm_token_budget.py`
- `tests/test_token_estimator.py`

## Deterministic core and optional advisor

`BudgetService` owns deterministic budget operations over the unchanged `BudgetKFSStore`; public `reserve_budget` uses that service. It imports no RoleAgent runtime. The optional `CFOAgent` moved to `polaris.cells.roles.runtime.public.cfo_advisor`; the former finops root/public/service Agent aliases are retired. Seven advisor tools, TASK messages and RESULT correlation remain in the higher role owner.

Production dependencies/effects and exact test-only dependencies/effects remain separate in `cell.yaml`. Scoped operation, KFS-byte and cold-import checks do not qualify host execution or project delivery.
