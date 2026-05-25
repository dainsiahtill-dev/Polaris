# PM Failure Root Cause Desktop Detail

Date: 2026-05-25

## Problem

When PM planning fails, the desktop runtime dialog can show downstream cascade text such as Director skipped or QA blocked, while the PM role detail remains generic. Users then cannot tell whether the root cause was empty PM tasks, provider invocation failure, or task contract validation.

## Decision

Reuse PM engine evidence that already exists in the planning pipeline:

```text
PM planning normalized payload / PM state
  -> terminal_error_code / terminal_error
  -> last_pm_error_code / last_pm_error_detail
  -> schema_warnings / notes
  -> Engine role PM.detail
  -> desktop runtime issue dialog
```

No new state owner or parallel diagnostics endpoint is introduced. The engine still keeps the existing phase errors (`PM_PLANNING_FAILED`, `PM_ITERATION_FAILED`) for compatibility, but PM role detail now carries the root-cause evidence.

## Verification

- PM zero-task fail-fast annotates the normalized payload with `PM_EMPTY_TASKS_WITH_REQUIREMENTS`.
- PM failure detail prefers terminal error evidence, falls back to PM state error evidence, then schema warnings.
- Desktop runtime issue rendering now includes PM role detail in addition to downstream Director/QA details.
