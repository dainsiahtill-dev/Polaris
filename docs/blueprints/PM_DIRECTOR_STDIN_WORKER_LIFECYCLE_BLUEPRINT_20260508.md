# PM Director Stdin Worker Lifecycle Blueprint 2026-05-08

## Current Understanding

Polaris is the meta application under test. Target workspaces must stay outside the repository, with global persistence under `~/.polaris` and project persistence under `<workspace>/.polaris`.

The failing Electron real-flow run reaches PM planning successfully. PM produces a valid `pm_tasks.contract.json` and marks the engine as dispatching Director tasks, but the UI remains in PM RUN/Planning for more than 20 minutes.

## Evidence

- Electron E2E workspace: `C:\Temp\Polaris_ETMS_Stress_E2E_mowmj923`
- Runtime root: `C:\Users\dains\AppData\Local\Temp\Polaris\runtime\e2e-real-flow-8396`
- PM contract exists with 3 valid tasks and quality score 100.
- `runtime/status/engine.status.json` remains `running=true`, `phase=dispatching`.
- Director transcripts show repeated `Director loop error: 'worker-...'` about 60 seconds after task submission.
- `WorkerService` launches `task_execution_runner` with `stdin_input` and then calls `broker.wait_process(...)`.
- `PopenAsyncHandle` writes prepared stdin only from `stream()`, while `wait()` never writes stdin.
- `task_execution_runner` starts by reading JSON from `sys.stdin`, so a wait-only caller can leave the child blocked forever.

## Defect Analysis

### Defect 1: Wait-only subprocess stdin deadlock

Trigger: a process is launched with `stdin_input`, and the caller waits without consuming `stream()`.

Root cause: KernelOne's Popen handle treats stdin delivery as a streaming-side effect. The process contract allows `wait()` without `stream()`, so stdin delivery must be independent of output streaming.

Impact: Director task subprocesses wait indefinitely for JSON input, PM dispatch never completes, and Electron E2E appears stuck in the PM workspace.

### Defect 2: Busy worker heartbeat false failure and cleanup race

Trigger: a worker is BUSY on a long or blocked subprocess for longer than `heartbeat_timeout_seconds`.

Root cause: health checks apply idle-loop heartbeat rules to busy workers even though the task timeout is the correct guard for active subprocess execution. `destroy_worker()` also indexes `_worker_tasks[worker_id]` after a membership check while `_cleanup_worker_task()` can concurrently remove the same entry.

Impact: active workers are incorrectly restarted and can surface as `KeyError 'worker-...'`, hiding the original subprocess deadlock.

## Fix Plan

- Move stdin delivery into a single guarded helper in `PopenAsyncHandle`.
- Call the helper from both `stream()` and `wait()` before waiting for process exit.
- Treat BUSY workers as healthy for heartbeat purposes; subprocess timeout remains the active execution guard.
- Make `destroy_worker()` idempotent around missing worker loop tasks.

No public API changes are planned.

## Test Plan

- Regression: launch a process with `stdin_input` and call `wait_process()` without log streaming; it must complete successfully.
- Edge case: stale BUSY worker remains healthy and is not restarted by failed-worker handling.
- Regression: destroying a worker after its task registry entry was already cleaned up must not raise `KeyError`.
- Integration: rerun Electron real-flow after unit regressions pass.

## Rollback Plan

Revert changes in:

- `src/backend/polaris/kernelone/process/async_contracts.py`
- `src/backend/polaris/cells/director/tasking/internal/worker_pool_service.py`
- Related tests under `src/backend/polaris/cells/runtime/execution_broker/tests/` and `src/backend/polaris/cells/director/tasking/tests/`

Post-rollback risk to recheck: PM dispatch may return to wait-only stdin deadlock.
