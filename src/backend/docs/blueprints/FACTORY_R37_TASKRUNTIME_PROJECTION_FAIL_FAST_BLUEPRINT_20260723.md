# Factory R37: TaskRuntime projection fail-fast

Status: closed  
Bench: not_schedulable after the consumed R38 acceptance exposed a new timeout-budget root  
Scope: Polaris platform only; no target-project edits.

## Root

Director dispatch already detected that the TaskRuntime observable projection
was file-backed, degraded, or otherwise non-authoritative, but converted that
condition to an empty row list. The dispatch loop then started/waited for a
Director child, consumed up to 595 seconds, and finally projected either
`director.dispatch_timeout` or a generic `director.run_status_non_success`.

## Contract

- Query the authoritative TaskRuntime execution-fact projection once before
  Director dispatch.
- If unavailable or not ready, append one typed error signal and do not invoke
  Director or wait on a child run.
- Preserve the exact projection source/readiness evidence and classify the
  failure as `LEDGER_PROJECTION_INCOMPLETE` owned by `task_runtime`.
- Tests whose subject is downstream Director behavior must provide an explicit
  authoritative projection fixture; file rows never authorize execution.

## Evidence

- Typed codes:
  `director.task_runtime_fact_projection_unavailable` and
  `director.task_runtime_fact_projection_not_ready`.
- Two former long-wait tests now terminate in about four seconds with exact
  attribution.
- Six drift/fail-fast regressions: `6 passed`.
- Full `test_factory_run_service.py`: `90 passed`.
- Cross-layer Factory authority matrix: `506 passed`.
- Requalified TaskRuntime + Factory fact-chain matrix: `2126 passed, 12 warnings`.
- Full architecture: `1411 passed, 8 skipped`.
- Ruff, Ruff format, Mypy on changed production modules, compileall, diff check,
  and post-edit CodeGraph review: pass.
