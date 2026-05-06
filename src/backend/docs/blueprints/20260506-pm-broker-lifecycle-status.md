# PM Broker Lifecycle Status Blueprint

Date: 2026-05-06

## Current Understanding

Electron acceptance launches PM through `/v2/pm/run_once` and then polls
`/v2/pm/status` until it observes `running=true` and later `running=false`.
PMService starts the PM CLI through the `runtime.execution_broker` cell, but
the status endpoint still primarily uses the resolved runtime process object's
`poll()` state.

The failing acceptance run proved that `pm_tasks.contract.json` was written
after Playwright had already observed PM as not running and moved into contract
polling. That makes `runtime.execution_broker` the authoritative lifecycle
owner, while the raw process handle is only a degraded implementation detail.

Follow-up evidence from the next Electron run showed a second root cause:
`get_container()` assembled the global DI container once with environment-derived
settings before the FastAPI app rebound it to `app.state.settings`. Because
`assemble_core_services()` skipped existing Settings/Storage registrations, a
long-lived PMService could keep a stale workspace and write PM artifacts to the
previous runtime root.

## Evidence List

Read and traced:

- `polaris/cells/orchestration/pm_planning/service.py`
- `polaris/cells/runtime/execution_broker/public/contracts.py`
- `polaris/cells/runtime/execution_broker/public/service.py`
- `polaris/cells/runtime/execution_broker/internal/service.py`
- `polaris/delivery/http/v2/pm.py`
- `polaris/bootstrap/assembly.py`
- `polaris/delivery/http/routers/system.py`
- `polaris/tests/electron/full-chain-audit.spec.ts`
- `polaris/tests/electron/pm-director-real-flow.spec.ts`

Runtime evidence:

- `.polaris/logs/full_chain_audit_2026-05-06T14-03-41-896Z.json`
- `test-results/electron/full-chain-audit-unattende-eaf39-trong-JSON-evidence-package/round-01.snapshot.json`
- `C:/Users/dains/AppData/Local/Polaris/cache/.polaris/projects/polaris-etms-stress-e2e-mou4o9gx-e312e0307329/runtime/contracts/pm_tasks.contract.json`
- `C:/Users/dains/AppData/Local/Polaris/cache/.polaris/projects/polaris-etms-stress-e2e-mou57m6h-de5c49db3aa7/runtime/contracts/pm_tasks.contract.json`

Confirmed:

- PMService launches PM through `get_execution_broker_service().launch_process()`.
- `ProcessHandle.execution_id` stores the broker execution id.
- `PMService.get_status()` ignores that execution id and reports `running`
  from `process.poll()`.
- The acceptance snapshot reported `pm.running=false` before PM contract output
  appeared in the runtime root.
- In the next run, current workspace `mou57m6h` had no PM contract during the
  full-chain poll, while previous workspace `mou4o9gx` received a fresh PM
  contract at the PM run timestamp.
- `assemble_core_services()` did not replace Settings/Storage registrations
  when explicit app settings were supplied after the initial container
  bootstrap.

## Defect Analysis

Issue: `/v2/pm/status` can report PM idle before the broker execution is
terminal.

Trigger: PM is launched via the execution broker and the resolved runtime
process handle no longer represents the whole execution lifecycle, especially
while the broker is draining logs or completing post-process state publication.

Root cause: lifecycle ownership migrated to `runtime.execution_broker`, but
PMService status, duplicate-run protection, and stale-handle cleanup still
prefer the raw process handle.

Impact: Electron/Playwright and the frontend can observe false idle states,
start premature verification, miss generated contracts, or allow duplicate PM
starts.

Issue: PM artifacts can be written to a stale workspace after `/settings`
changes the active workspace.

Trigger: the global DI container is created before FastAPI lifespan passes the
app settings, or a long-lived PMService survives a workspace update.

Root cause: explicit app settings were treated as optional during assembly, and
PMService had no public rebind operation to align its settings object with the
application state owner.

Impact: the UI, storage-layout endpoint, and Playwright verify one runtime root
while PM writes contracts, state, and reports into another runtime root.

## Fix Plan

Minimal changes:

1. Import the execution broker status query and status enum through the public
   contract module.
2. Add a PMService helper that resolves broker status from
   `ProcessHandle.execution_id`.
3. Treat broker `queued` and `running` as active.
4. Use the broker-derived active state for run guards, stop guards, stale-handle
   cleanup, and `/v2/pm/status`.
5. Keep `process.poll()` as a fallback only when no execution id exists or the
   broker cannot resolve status.
6. When app settings are explicitly passed to assembly, re-register Settings and
   StorageLayout as the DI truth.
7. Add `PMService.rebind_settings()` and use it after `/settings` workspace
   updates.

Non-goals:

- No changes to the execution broker internals.
- No change to PM CLI behavior or contract shape.
- No target workspace modifications.
- No broad lifecycle rewrite.
- No change to settings API request or response shape.

Compatibility:

- `/v2/pm/status.running` remains a boolean.
- Additive fields such as `execution_id` and broker status are diagnostic only.
- Existing non-broker tests continue through the process-handle fallback.
- Settings and storage registrations are only force-rebound when assembly is
  called with an explicit settings object.

## Test Plan

Happy path:

- Existing PMService launch tests still pass.

Edge cases:

- Broker `running` wins over a process handle whose `poll()` is already
  terminal.
- Broker `queued` is also active.
- Broker lookup failure falls back to the process handle.
- Explicit app settings override a pre-existing environment-derived DI Settings
  registration.
- PMService rebind refreshes storage to the new workspace.

Exception cases:

- Broker status query exceptions are logged at debug and do not break
  `/v2/pm/status`.

Regression cases:

- `run_once` duplicate-run protection uses broker active state.
- `/v2/pm/status` does not clear the handle while the broker is active.
- Electron acceptance waits for the true PM lifecycle before verifying
  contracts.
- PM contracts are written under the runtime root returned for the current
  workspace.

## Rollback Plan

Files expected to change:

- `src/backend/polaris/cells/orchestration/pm_planning/service.py`
- `src/backend/polaris/tests/test_pm_service_lifecycle_lock.py`
- `src/backend/polaris/bootstrap/assembly.py`
- `src/backend/polaris/delivery/http/routers/system.py`
- `src/backend/polaris/tests/test_provider_bootstrap.py`

Rollback:

- Revert the listed files plus this blueprint and verification card.
- Re-run focused PMService tests and Electron acceptance.
