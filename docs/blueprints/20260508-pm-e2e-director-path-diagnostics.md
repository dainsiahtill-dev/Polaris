# PM E2E Director Path And Diagnostics Blueprint

## Current Understanding

Polaris Desktop launches the backend, the backend `PMService` launches the PM CLI through `runtime.execution_broker`, and the PM CLI optionally dispatches Director work through workflow runtime when `pm_runs_director=true`.

The visible user failure is that the Electron app enters the PM workspace and then appears idle: no PM tasks are shown, no Director result is produced, and the runtime overlay only reports an execution broker terminal line with `exit_code=1 error=`.

## Evidence

- `PMService._spawn_process()` launches through `runtime.execution_broker` with `workspace` as subprocess cwd.
- `PMService._build_command()` appends `--run-director` but does not pass `--director-path`.
- PM CLI default `--director-path` is relative: `src/backend/polaris/delivery/cli/loop-director.py`.
- Workflow Director activity previously calculated `project_root` from `parents[5]`, which resolves to `src/backend/polaris/cells`, not the repository root.
- `loop-director.py` imported `polaris.*` modules before bootstrapping `src/backend` into `sys.path`, so direct script execution from a target workspace can fail before its bootstrap runs.
- `runtime.execution_broker` previously retained no subprocess stderr in terminal snapshots, causing E2E diagnostics to hide the real Python traceback.

## Defect Analysis

### Defect 1: Director script path is not canonical across process boundaries

Trigger: PM is launched by Electron from a target workspace while `pm_runs_director=true`.

Root cause: a relative Director script path crosses PMService, PM CLI, workflow runtime, and Director adapter boundaries. Each layer has a different cwd/root assumption.

Impact: Director dispatch may fail or be marked unavailable even though the script exists in the Polaris repository.

### Defect 2: CLI bootstrap runs after package imports

Trigger: `python <absolute loop-director.py>` from a non-repository cwd without `PYTHONPATH=src/backend`.

Root cause: `loop-director.py` imports `polaris.*` before inserting the backend root into `sys.path`.

Impact: Director can fail with `ModuleNotFoundError` before it can emit structured runtime evidence.

### Defect 3: Failed subprocesses lose stderr in PM diagnostics

Trigger: PM CLI exits non-zero through execution broker.

Root cause: `PopenAsyncHandle.result()` returned empty stdout/stderr lines, and runtime snapshots left `error` empty for normal non-zero exits.

Impact: E2E and UI show `exit_code=1 error=` instead of the traceback, slowing root-cause analysis.

## Repair Plan

- Pass the canonical absolute Director script path from `PMService._build_command()`.
- Make PM CLI's default `--director-path` absolute.
- Normalize Director script paths before workflow metadata is handed off.
- Resolve workflow Director activity project root from an actual repository marker instead of a fixed parent index.
- Move `loop-director.py` path bootstrap before any `polaris.*` import.
- Preserve subprocess stderr/stdout tails in execution runtime snapshots and broker logs.

No public HTTP API or user-facing command contract changes are required. Existing explicit `--director-path` arguments remain supported.

## Test Plan

- Happy path: PMService command includes `--run-director` and absolute `--director-path`.
- Edge case: PM CLI parser default Director path is absolute and exists.
- Exception path: a failing subprocess preserves stderr in broker wait result and log.
- Regression: `loop-director.py --help` works from a temporary workspace without relying on cwd.
- E2E: rerun `npm run test:e2e:real-flow` with seeded real settings and verify PM produces Director result artifacts.

## Rollback Plan

Changed files are limited to PM launch, CLI path normalization, workflow Director activity path resolution, process diagnostics, tests, and this blueprint. Rollback is a normal git revert of those files. After rollback, specifically recheck PMService launch command, broker failure logs, and PM/Director E2E artifact production.
