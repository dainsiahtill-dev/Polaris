# TaskRuntime durable local-rework receipt replay

Status: implementation active  
Date: 2026-08-25  
Scope: `runtime.task_runtime` public query contract and bootstrap project-completion action owner.

## Exact-run defect

Fresh isolated L3-22 run `factory_cbc114bc122b` committed same-task local
rework action `8d1ec0e871ebad6e1695b3ff04d0049ab966027058c4edfdf95bab53158bc694`.
The append-only `task_runtime.execution` stream contains its
`same_task_local_rework_prepared` fact, dispatch claim and effect hash. A later
Factory terminal drain removed the mutable TaskRuntime row. Project-completion
convergence then revalidated the already committed action through the current
row only, returned no receipt and stopped with
`committed_owner_receipt_missing` before the next owner-scoped repair could be
dispatched.

## Root cause

The bootstrap action owner treats mutable row metadata as the only receipt
store. That metadata is a projection and may disappear during an authorized
runtime reset. The committed action fact is durable, but no typed TaskRuntime
public query exposes it for replay.

## Fix contract

1. Add a read-only typed `runtime.task_runtime` query for one exact same-task
   local-rework authorization.
2. Resolve the query from append-only `task_runtime.execution` facts, matching
   exact workspace, Factory run, external task and action id.
3. Fail closed on malformed or ambiguous matching facts; never widen task,
   workspace, action or effect authority.
4. Keep the current observable-row lookup as the fast path. Use the durable
   fact query only when that projection no longer contains the committed
   action.
5. Reconstruct the existing project-completion receipt only after the current
   effect-hash and 64-character dispatch-claim checks pass.
6. Do not modify the generated Bench project and do not replay PM or Chief
   Engineer.

## Verification

- RED: dispatch a same-task action, reset/remove the TaskRuntime row, then
  query the receipt; the pre-fix implementation returns `None`.
- GREEN: the same test returns the original receipt from the append-only fact.
- Existing cross-run, identity, idempotency and reset tests remain green.
- Ruff and Mypy pass on touched Python paths.
- Same-run L3-22 recovery no longer emits
  `committed_owner_receipt_missing`; the next result must be an owner-scoped
  Director repair or a newly evidenced downstream blocker.

## Rollback

Revert the new query, bootstrap fallback and tests together. Do not replace the
durable query with a bootstrap import of TaskRuntime internals or with a broad
row-retention exception.
