# Factory QA Terminal TaskRuntime Owner Epoch

## Defect

L1-03 `factory_6cf1e490e0e4` reached a fully green physical workspace gate, then
failed with `verified_delivery_runtime_owner_not_unique`.

Dynamic evidence showed one PM task (`TASK-3`) had several historical
`runtime_reset_removed` TaskRuntime rows. These rows are append-only audit
tombstones, not concurrent owners. `_reconcile_verified_runtime_delivery`
scanned the full observable history and counted every matching tombstone as a
live owner.

This made a QA-only retry contradict the existing Factory authority contract:
the retry correctly preserved `factory_terminal_task_runtime_projection`, but
delivery reconciliation ignored that frozen epoch.

## Invariant

1. Live reconciliation considers only matching TaskRuntime rows whose status is
   not `removed`.
2. More than one live matching owner remains a fail-closed contract violation.
3. If terminal settlement drained all live rows, reconciliation must validate
   exactly one matching row in the run-bound frozen TaskRuntime projection.
4. After frozen validation, TaskRuntime rematerializes the exact canonical PM
   task contract. Factory must not resurrect an arbitrary historical row or
   infer authority from the largest numeric id.
5. PM, Chief Engineer, and Director are not rerun for this QA-local control-plane
   repair.

## Implementation

- Owner: `factory.pipeline`
- File:
  `internal/factory_stage_executor/_mixin_03.py`
- Regression:
  `test_reconcile_verified_runtime_delivery_restores_frozen_owner_after_terminal_drain`

## Verification

- Focused reconciliation tests: `3 passed`
- Ruff: clean
- Mypy: clean
- Live validation: QA-only retry on the same L1-03 run; no PM/CE/Director restart.

## Operational lesson

UI error totals aggregate immutable event history. Historical failed/removed
events must not be interpreted as current defects or current ownership. Current
health must come from epoch-scoped projections; history remains available for
audit only.
