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
- errors: FinOpsBudgetErrorV1

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
