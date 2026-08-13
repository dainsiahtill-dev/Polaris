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

## Second-order closure defects

Dynamic L1-03 recovery exposed four defects hidden behind the original owner
epoch mismatch:

1. Project evidence closure observed the same large TaskRuntime owner stream
   once per obligation. A batch API now observes once and materializes the
   ordered obligation set from that frozen owner snapshot.
2. The async process stream decremented producer liveness before the consumer
   drained queued stdout/stderr. A successful unittest process could therefore
   lose its final `OK` line and produce a false failed proof. EOF ownership now
   moves to the consumer side.
3. A completed verifier receipt with `proof_satisfied=false` was permanently
   reused. Explicit verifier execution may now create a new fenced attempt;
   passive queries remain side-effect free and the old receipt remains in the
   append-only HMAC history.
4. ProjectOutcome joined all Factory TaskRuntime rows, including CE portfolio,
   schema repair, and materialization-settle helpers. It also ignored newer
   owner-sealed ExecutionBroker receipts and trusted stale
   `downstream_pending_artifacts`. The adapter now filters exact CE-covered task
   owners, reconciles pending paths against the workspace, and only lets a fully
   passed, authority-bound completion evidence bundle supersede failed/pending
   intermediate rows.

The final rule is deliberately narrow: physical settlement may override an
intermediate task status only when every active CE obligation is evaluated and
passed under the exact workspace/project/run/contract identity. Partial,
missing, failed, lookalike, or unbound evidence remains fail-closed.

## Live closure evidence

L1-03 run `factory_6cf1e490e0e4` was repaired in place without restarting PM,
Chief Engineer, or Director:

- all 16 active obligations passed;
- ProjectOutcome axes: delivery verified, chain completed, QA passed,
  TaskBoundary passed, TaskRuntime converged, Run Ledger closed;
- exact business task count: `3/3` (auxiliary Factory rows excluded);
- missing/failed modalities, reasons, and blocking axes: empty;
- durable completion cursor:
  `project-completion-72b63b47ab38f13cc7382efc35a3a8f18c7e344d999b4e3bab30ea061100e078`;
- terminal replay: `completed_verified`, `event_seq=2`, reproduced from a new
  process.

Targeted regression evidence: ProjectOutcome/diagnostics/convergence `64/64`,
physical evidence/receipt authority `36/36`, async process/verifier policy
`86/86`, Ruff clean, Mypy clean.

## ContextOS historical-error projection

The ContextOS Receipt card previously derived its `blocked` state directly
from `telemetry.errorCount`. That counter is an immutable observation-window
total: a successful same-stage repair does not erase prior `Director task
failed` events. L1-03 was therefore `COMPLETED_VERIFIED` while the UI still
rendered `18 errors / blocked`.

The frontend now keeps those events visible as **historical error events** but
does not infer current blockage from their count. It reports a telemetry block
only when the latest observation is still an unrecovered error (or the runtime
gate is blocked); a newer successful/state event clears that transient status
without deleting history. Authoritative project completion remains owned by
the runtime gate / ProjectOutcome.

Frontend verification: ContextOS model tests `111/111`, targeted ESLint clean,
TypeScript typecheck clean, and production renderer build clean.
