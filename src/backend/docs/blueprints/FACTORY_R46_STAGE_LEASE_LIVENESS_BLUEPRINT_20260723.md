# Factory R46 — Stage Lease Liveness

Status: `prebench_verified`

Bench state: `schedulable_once_under_R48`

## Problem

R45 fresh isolated L1-04 reached Director and materialized nine files, but the
Factory run never settled. During the long `director_dispatch` stage, one
Factory Run Store/Event projection lock failure terminated the single stage
heartbeat coroutine. The durable workspace lease then stopped renewing,
expired after 180 seconds, and the timeout cancellation request was rejected
as an expired-owner conflict.

The current implementation incorrectly couples two different duties:

1. security-critical workspace lease liveness;
2. observable Run Store/Event heartbeat projection.

A transient projection failure must not silently terminate lease renewal.

## Invariants

1. The exact active `run_id` and `fencing_token` renew the durable workspace
   lease independently of Run Store/Event projection.
2. Projection failure is observable and retried on the next interval; it does
   not stop lease renewal.
3. A real lease authority failure remains fail-closed; no token inference,
   owner replacement, or silent reacquisition is allowed.
4. Stage completion/cancellation still owns the existing commit arbitration,
   stage claim, settlement, and release path.
5. No target-project code, Bench success rule, or production UI semantics are
   changed.

## Minimal implementation

- Pass the captured workspace fencing token into the stage heartbeat task.
- Renew the admission lease before attempting Run Store/Event projection.
- Separate projection from renewal so a projection exception cannot terminate
  the lease keeper.
- Add focused regression tests proving:
  - repeated projection failures do not stop durable lease renewal;
  - the exact captured fencing token is used;
  - foreign/fenced renewal still fails closed.

## Proof ladder

1. Focused Factory workspace-admission/run-service tests.
2. Ruff and mypy for changed files.
3. Factory Pipeline suite.
4. TaskRuntime + Workflow Runtime suites.
5. KernelOne release and architecture gates.
6. Stable source fingerprint.
7. New one-shot pre-bench authorization before any Provider/Bench request.

## Exit criteria

R46 closes only when the proof ladder is green and an independent fresh
isolated run can keep the workspace lease alive throughout a long Director
stage without `FactoryWorkspaceRunLeaseConflictError`.

## Pre-bench evidence

- Focused Factory Run Service: `91 passed`.
- Focused workspace admission: `44 passed`.
- Factory Pipeline: `1240 passed`.
- TaskRuntime: `418 passed`.
- Workflow Runtime: `215 passed`.
- KernelOne release: `415 passed, 1 skipped`, `ok=true`.
- Architecture: `1411 passed, 8 skipped`.
- Ruff, source mypy, compileall, and `git diff --check`: pass.
- Stable source fingerprint: `aab54ea3611e77cd` (two consecutive reads).

Fresh isolated runtime evidence remains the only item required to close R46.
